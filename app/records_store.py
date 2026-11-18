"""Records RAG: a ChromaDB index of beneficiary profiles, expenses and cases.

SQLite stays the system of record. This module keeps a second ChromaDB
collection (`beneficiary_records`, next to the policy collection) in step with
it, so the Records agent can search a beneficiary's history by meaning
("earlier physical therapy claims") instead of exact ids.

Security choices, each tested by the red team (RT-27):
- Tenant isolation is enforced twice: the Chroma query is pre-filtered on
  `customer_id`, and every hit is checked again before it is returned. The
  guardrail separately refuses a search for anyone but the session beneficiary.
- Free text (a beneficiary's claim reason, a decision rationale) is embedded so
  it can be searched, but it is never returned. Hits carry only structured
  fields from SQLite, so text a beneficiary wrote in an old claim cannot reach
  the model as instructions later (ASI06 memory poisoning).
- Bank account numbers are indexed only as the last four digits.

Indexing is best effort: a failure is logged and never stops a claim, because
the index can always be rebuilt from SQLite with sync_all().
"""
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.db import parse_ts

logger = logging.getLogger(__name__)

COLLECTION_NAME = "beneficiary_records"
KINDS = ("profile", "expense", "case", "file")
# Fields a hit may carry. Everything else stays in the index text only.
RETURNED_FIELDS = ("kind", "id", "customer_id", "order_id", "product_name", "category", "amount",
                   "status", "date", "decided_by", "case_id", "name", "content_type")


def _collection(conn: sqlite3.Connection):
    from app.retriever import get_store_for_conn  # avoid an import cycle with db.py
    store = get_store_for_conn(conn)
    return store.client.get_or_create_collection(
        name=COLLECTION_NAME, embedding_function=store.embedding_function, metadata={"hnsw:space": "cosine"})


def _mask(account: str) -> str:
    digits = "".join(ch for ch in account if ch.isdigit())
    return f"****{digits[-4:]}" if digits else "****"


def _profile_doc(row: sqlite3.Row) -> Dict[str, Any]:
    text = (f"Beneficiary profile {row['id']}: {row['name']}, {row['email']}. "
            f"Payment account {_mask(row['bank_account'])}, balance ${row['bank_balance']:.2f}.")
    meta = {"kind": "profile", "id": row["id"], "customer_id": row["id"], "amount": float(row["bank_balance"])}
    return {"id": f"profile:{row['id']}", "text": text, "meta": meta}


def _expense_doc(row: sqlite3.Row) -> Dict[str, Any]:
    text = (f"Healthcare expense {row['id']} for {row['customer_id']}: {row['product_name']}, "
            f"{row['category']} service, billed ${row['amount']:.2f}, date of service {row['purchase_date'][:10]}.")
    meta = {"kind": "expense", "id": row["id"], "customer_id": row["customer_id"], "order_id": row["id"],
            "product_name": row["product_name"], "category": row["category"], "amount": float(row["amount"]),
            "date": row["purchase_date"]}
    return {"id": f"expense:{row['id']}", "text": text, "meta": meta}


def _case_doc(row: sqlite3.Row) -> Dict[str, Any]:
    text = (f"Reimbursement case {row['id']} for {row['customer_id']}: expense {row['order_id']} "
            f"({row['product_name']}, {row['category']}), ${row['amount_requested']:.2f} requested. "
            f"Reason given: {row['reason']}. Status {row['status']}. Decision: {row['decision_reason'] or 'pending'}.")
    meta = {"kind": "case", "id": row["id"], "customer_id": row["customer_id"], "order_id": row["order_id"],
            "product_name": row["product_name"], "category": row["category"],
            "amount": float(row["amount_requested"]), "status": row["status"], "date": row["created_at"],
            "decided_by": row["decided_by"] or ""}
    return {"id": f"case:{row['id']}", "text": text, "meta": meta}


def _file_doc(row: sqlite3.Row) -> Dict[str, Any]:
    body = (row["text"] or "")[:2000] or f"{row['content_type']} file, contents not indexed."
    text = f"Case file {row['id']} '{row['name']}' for {row['customer_id']}: {body}"
    meta = {"kind": "file", "id": row["id"], "customer_id": row["customer_id"], "case_id": row["case_id"] or "",
            "name": row["name"], "content_type": row["content_type"], "date": row["uploaded_at"]}
    return {"id": f"file:{row['id']}", "text": text, "meta": meta}


CASE_SQL = ("SELECT k.*, o.product_name, o.category FROM cases k JOIN orders o ON o.id = k.order_id")


def _upsert(conn: sqlite3.Connection, docs: Iterable[Dict[str, Any]]) -> int:
    docs = list(docs)
    if not docs:
        return 0
    try:
        _collection(conn).upsert(ids=[d["id"] for d in docs], documents=[d["text"] for d in docs],
                                 metadatas=[d["meta"] for d in docs])
    except Exception:  # noqa: BLE001 - the index is rebuildable; never fail a claim over it
        logger.exception("Records index update failed; continuing without it.")
        return 0
    return len(docs)


# ----------------------------------------------------------------- writes

def index_profile(conn: sqlite3.Connection, customer_id: str) -> int:
    row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    return _upsert(conn, [_profile_doc(row)] if row else [])


def index_case(conn: sqlite3.Connection, case_id: str) -> int:
    row = conn.execute(CASE_SQL + " WHERE k.id = ?", (case_id,)).fetchone()
    return _upsert(conn, [_case_doc(row)] if row else [])


def index_file(conn: sqlite3.Connection, file_id: str) -> int:
    row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    return _upsert(conn, [_file_doc(row)] if row else [])


def index_case_and_profile(conn: sqlite3.Connection, case_id: str) -> None:
    """Refresh a case and its owner's profile (the balance changes on payout)."""
    index_case(conn, case_id)
    row = conn.execute("SELECT customer_id FROM cases WHERE id = ?", (case_id,)).fetchone()
    if row:
        index_profile(conn, row["customer_id"])


def sync_all(conn: sqlite3.Connection) -> int:
    """Rebuild the whole index from SQLite."""
    docs = [_profile_doc(r) for r in conn.execute("SELECT * FROM customers")]
    docs += [_expense_doc(r) for r in conn.execute("SELECT * FROM orders")]
    docs += [_case_doc(r) for r in conn.execute(CASE_SQL)]
    docs += [_file_doc(r) for r in conn.execute("SELECT * FROM files")]
    return _upsert(conn, docs)


def reset(conn: sqlite3.Connection) -> None:
    from app.retriever import get_store_for_conn
    try:
        get_store_for_conn(conn).client.delete_collection(COLLECTION_NAME)
    except Exception:  # noqa: BLE001 - nothing to delete
        pass


def count(conn: sqlite3.Connection, customer_id: Optional[str] = None) -> int:
    col = _collection(conn)
    if customer_id is None:
        return col.count()
    return len(col.get(where={"customer_id": customer_id}, include=[])["ids"])


# ----------------------------------------------------------------- reads

def search(conn: sqlite3.Connection, customer_id: str, query: str, top_k: int = 5,
           kinds: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Semantic search over one beneficiary's records. Returns structured fields only.

    If the index is unavailable this returns no hits instead of failing: related
    claims only add context, and every rule that matters is in the guardrail.
    """
    try:
        return _search(conn, customer_id, query, top_k, kinds)
    except Exception:  # noqa: BLE001
        logger.exception("Records search failed; continuing with no related records.")
        return []


def _search(conn: sqlite3.Connection, customer_id: str, query: str, top_k: int,
            kinds: Optional[List[str]]) -> List[Dict[str, Any]]:
    col = _collection(conn)
    if col.count() == 0:
        sync_all(conn)
    where: Dict[str, Any] = {"customer_id": customer_id}
    kinds = [k for k in (kinds or []) if k in KINDS]
    if kinds:
        where = {"$and": [where, {"kind": {"$in": kinds}}]}
    owned = len(col.get(where=where, include=[])["ids"])
    if owned == 0:
        return []
    res = col.query(query_texts=[query], n_results=min(top_k, owned), where=where,
                    include=["metadatas", "distances"])
    hits: List[Dict[str, Any]] = []
    for meta, dist in zip(res.get("metadatas", [[]])[0], res.get("distances", [[]])[0]):
        if meta.get("customer_id") != customer_id:  # second, independent tenant check
            logger.error("Records index returned another beneficiary's record; dropped.")
            continue
        hit = {k: meta[k] for k in RETURNED_FIELDS if k in meta and meta[k] != ""}
        hit["score"] = round(max(0.0, 1.0 - dist), 4)
        hits.append(hit)
    return hits


def history_for_prompt(hits: List[Dict[str, Any]], exclude_case: Optional[str] = None) -> List[Dict[str, Any]]:
    """The prior-claim facts the eligibility model may see: structured, no free text."""
    out = []
    for h in hits:
        if h.get("kind") != "case" or h.get("id") == exclude_case:
            continue
        days = None
        try:
            days = (datetime.now(timezone.utc) - parse_ts(h["date"])).days
        except Exception:  # noqa: BLE001 - a missing date only drops the age
            pass
        out.append({"case_id": h["id"], "order_id": h.get("order_id"), "service": h.get("product_name"),
                    "coverage": h.get("category"), "amount": h.get("amount"), "status": h.get("status"),
                    "days_ago": days})
    return out

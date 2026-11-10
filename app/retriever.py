"""Refund-policy retriever (the RAG component).

Integrates with ChromaDB vector store (app/vector_store.py) to provide
semantic vector search across enterprise refund policies with fallback to
SQLite policy metadata.
"""
import logging
import math
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import config
from app.db import now_ts
from app.vector_store import PolicyVectorStore, get_vector_store

logger = logging.getLogger(__name__)


def _tokens(text: str) -> set:
    return {w.lower() for w in re.findall(r"[A-Za-z0-9$]+", text)}


def get_store_for_conn(conn: sqlite3.Connection) -> PolicyVectorStore:
    """Resolve the vector store instance associated with a database connection.
    
    If running against a custom/sandbox database, scopes ChromaDB persistence
    to that database's directory. Otherwise uses the central config.CHROMA_PERSIST_DIR.
    """
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
        for r in rows:
            # Column 1 is name ('main'), Column 2 is file path
            db_name = r[1] if isinstance(r, (tuple, list)) else r["name"]
            db_file = r[2] if isinstance(r, (tuple, list)) else r["file"]
            if db_name == "main" and db_file:
                db_path = Path(db_file).resolve()
                if db_path != config.DB_PATH.resolve():
                    return get_vector_store(persist_dir=db_path.parent / "chroma", force_new=False)
    except Exception as exc:
        logger.debug("Failed resolving db path from connection: %s", exc)

    return get_vector_store()


class Retriever:
    def __init__(self, vector_store: Optional[PolicyVectorStore] = None):
        self._custom_store = vector_store

    def _store(self, conn: sqlite3.Connection) -> PolicyVectorStore:
        if self._custom_store is not None:
            return self._custom_store
        return get_store_for_conn(conn)

    def sync_from_db(self, conn: sqlite3.Connection, store: Optional[PolicyVectorStore] = None) -> int:
        """Sync policies from the SQLite table into the ChromaDB vector store."""
        st = store or self._store(conn)
        rows = conn.execute("SELECT id, title, content, tags, uploaded_by FROM policies").fetchall()
        for r in rows:
            st.index_policy(
                policy_id=r["id"],
                title=r["title"],
                content=r["content"],
                tags=r["tags"],
                uploaded_by=r["uploaded_by"] if "uploaded_by" in r.keys() else "seed",
            )
        return len(rows)

    def search(self, conn: sqlite3.Connection, query: str, top_k: int = 2) -> List[Dict[str, Any]]:
        """Search refund policies using ChromaDB semantic vector search.
        
        Falls back to token-overlap scoring if the vector store is empty or unavailable.
        """
        store = self._store(conn)
        
        # Ensure policies from SQLite are indexed into the vector store
        if store.count() == 0:
            count = self.sync_from_db(conn, store)
            if count == 0:
                return []

        try:
            hits = store.search_policies(query, top_k=top_k)
            if hits:
                # Enrich with SQLite row fields (e.g. updated_at) if available
                enriched = []
                for h in hits:
                    row = conn.execute("SELECT * FROM policies WHERE id = ?", (h["id"],)).fetchone()
                    item = dict(row) if row else {}
                    item.update(h)
                    enriched.append(item)
                return enriched
        except Exception as exc:
            logger.warning("Vector search error, falling back to token overlap: %s", exc)

        # Fallback: token-overlap cosine similarity
        q = _tokens(query)
        scored = []
        for row in conn.execute("SELECT id, title, content, tags FROM policies").fetchall():
            d = _tokens(f"{row['title']} {row['content']} {row['tags']}")
            overlap = len(q & d)
            score = overlap / (math.sqrt(len(q)) * math.sqrt(len(d))) if overlap else 0.0
            if score > 0:
                scored.append({**dict(row), "score": round(score, 4)})
        scored.sort(key=lambda p: p["score"], reverse=True)
        return scored[:top_k]

    def get_context(self, conn: sqlite3.Connection, query: str) -> Optional[str]:
        hits = self.search(conn, query, top_k=1)
        return hits[0]["content"] if hits else None

    def add_policy(
        self,
        conn: sqlite3.Connection,
        policy_id: str,
        title: str,
        content: str,
        tags: str,
        uploaded_by: str,
    ) -> Dict[str, Any]:
        """Dual-write policy to SQLite and index into ChromaDB vector store."""
        conn.execute(
            "INSERT INTO policies (id, title, content, tags, uploaded_by, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET title = excluded.title, content = excluded.content, "
            "tags = excluded.tags, uploaded_by = excluded.uploaded_by, updated_at = excluded.updated_at",
            (policy_id, title, content, tags, uploaded_by, now_ts()),
        )
        conn.commit()

        store = self._store(conn)
        store.index_policy(
            policy_id=policy_id,
            title=title,
            content=content,
            tags=tags,
            uploaded_by=uploaded_by,
        )
        return {"id": policy_id, "title": title, "status": "INDEXED"}

    def list_policies(self, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        return [dict(r) for r in conn.execute("SELECT * FROM policies ORDER BY id").fetchall()]

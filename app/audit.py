"""Tamper-evident audit trail.

Each entry stores the hash of the previous entry, so editing or deleting any
row breaks the chain from that point on. `verify_chain` detects this.
"""
import hashlib
import json
import sqlite3
from typing import Any, Dict, List, Optional

from app.db import now_ts

GENESIS_HASH = "0" * 64


def _entry_hash(ts, case_id, actor, action, status, details_json, prev_hash) -> str:
    payload = json.dumps([ts, case_id, actor, action, status, details_json, prev_hash])
    return hashlib.sha256(payload.encode()).hexdigest()


def log_event(
    conn: sqlite3.Connection,
    actor: str,
    action: str,
    status: str,
    details: Optional[Dict[str, Any]] = None,
    case_id: Optional[str] = None,
) -> Dict[str, Any]:
    row = conn.execute("SELECT hash FROM audit_logs ORDER BY id DESC LIMIT 1").fetchone()
    prev_hash = row["hash"] if row else GENESIS_HASH
    ts = now_ts()
    details_json = json.dumps(details or {}, sort_keys=True, default=str)
    entry_hash = _entry_hash(ts, case_id, actor, action, status, details_json, prev_hash)
    conn.execute(
        "INSERT INTO audit_logs (ts, case_id, actor, action, status, details, prev_hash, hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, case_id, actor, action, status, details_json, prev_hash, entry_hash),
    )
    conn.commit()
    return {"ts": ts, "actor": actor, "action": action, "status": status, "hash": entry_hash}


def get_logs(conn: sqlite3.Connection, case_id: Optional[str] = None) -> List[Dict[str, Any]]:
    if case_id:
        rows = conn.execute("SELECT * FROM audit_logs WHERE case_id = ? ORDER BY id", (case_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM audit_logs ORDER BY id").fetchall()
    out = []
    for r in rows:
        entry = dict(r)
        entry["details"] = json.loads(entry["details"])
        out.append(entry)
    return out


def verify_chain(conn: sqlite3.Connection) -> Dict[str, Any]:
    prev_hash = GENESIS_HASH
    rows = conn.execute("SELECT * FROM audit_logs ORDER BY id").fetchall()
    for r in rows:
        expected = _entry_hash(r["ts"], r["case_id"], r["actor"], r["action"], r["status"], r["details"], prev_hash)
        if r["prev_hash"] != prev_hash or r["hash"] != expected:
            return {"valid": False, "entries": len(rows), "first_bad_id": r["id"]}
        prev_hash = r["hash"]
    return {"valid": True, "entries": len(rows), "first_bad_id": None}

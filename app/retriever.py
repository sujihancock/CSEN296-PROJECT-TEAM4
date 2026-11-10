"""Refund-policy retriever (the RAG component).

Uses simple token-overlap cosine similarity over the policies table, so it
runs offline with no embedding model. Admins add policies with `add_policy`.
"""
import math
import re
import sqlite3
from typing import Any, Dict, List, Optional

from app.db import now_ts


def _tokens(text: str) -> set:
    return {w.lower() for w in re.findall(r"[A-Za-z0-9$]+", text)}


class Retriever:
    def search(self, conn: sqlite3.Connection, query: str, top_k: int = 2) -> List[Dict[str, Any]]:
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

    def add_policy(self, conn: sqlite3.Connection, policy_id: str, title: str, content: str, tags: str, uploaded_by: str) -> Dict[str, Any]:
        conn.execute(
            "INSERT INTO policies (id, title, content, tags, uploaded_by, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET title = excluded.title, content = excluded.content, "
            "tags = excluded.tags, uploaded_by = excluded.uploaded_by, updated_at = excluded.updated_at",
            (policy_id, title, content, tags, uploaded_by, now_ts()),
        )
        conn.commit()
        return {"id": policy_id, "title": title, "status": "INDEXED"}

    def list_policies(self, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        return [dict(r) for r in conn.execute("SELECT * FROM policies ORDER BY id").fetchall()]

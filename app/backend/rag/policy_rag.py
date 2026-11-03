import math
import re
from typing import List, Dict, Any
from app.backend.database import get_connection

class PolicyRAG:
    """
    RAG-supported vector and keyword search for enterprise refund policies.
    Provides semantic retrieval for the Refunds Agent to evaluate claims.
    """

    @classmethod
    def tokenize(cls, text: str) -> List[str]:
        return [w.lower() for w in re.findall(r'\b[a-zA-Z0-9_\$]+\b', text)]

    @classmethod
    def compute_similarity(cls, query_tokens: List[str], doc_tokens: List[str]) -> float:
        if not query_tokens or not doc_tokens:
            return 0.0
        q_set = set(query_tokens)
        d_set = set(doc_tokens)
        intersection = q_set.intersection(d_set)
        if not intersection:
            return 0.0
        return len(intersection) / (math.sqrt(len(q_set)) * math.sqrt(len(d_set)))

    @classmethod
    def query_policy(cls, query: str, top_k: int = 2) -> List[Dict[str, Any]]:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, title, content, tags FROM policies;")
        rows = cur.fetchall()
        conn.close()

        query_tokens = cls.tokenize(query)
        scored_policies = []

        for row in rows:
            full_text = f"{row['title']} {row['content']} {row['tags']}"
            doc_tokens = cls.tokenize(full_text)
            score = cls.compute_similarity(query_tokens, doc_tokens)
            scored_policies.append({
                "id": row["id"],
                "title": row["title"],
                "content": row["content"],
                "tags": row["tags"],
                "score": round(score, 4)
            })

        scored_policies.sort(key=lambda x: x["score"], reverse=True)
        return scored_policies[:top_k]

    @classmethod
    def add_or_update_policy(cls, policy_id: str, title: str, content: str, tags: str) -> Dict[str, Any]:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO policies (id, title, content, tags, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                content = excluded.content,
                tags = excluded.tags,
                updated_at = CURRENT_TIMESTAMP;
        """, (policy_id, title, content, tags))
        conn.commit()
        conn.close()
        return {"id": policy_id, "title": title, "status": "INDEXED"}

    @classmethod
    def list_all_policies(cls) -> List[Dict[str, Any]]:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, title, content, tags, updated_at FROM policies ORDER BY id ASC;")
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]


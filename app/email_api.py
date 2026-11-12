"""Mock Email API and external service provider simulation.

Simulates a modern transactional email service (SendGrid, AWS SES, Postmark)
for customer notifications in ChangSupport.AI.
"""
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

from app.db import now_ts

DEFAULT_SENDER = "ChangSupport Notifications <support@changsupport.ai>"


class MockEmailService:
    """Mock external Email Service Provider (ESP)."""

    def __init__(self, sender: str = DEFAULT_SENDER):
        self.sender = sender
        self._outbox: List[Dict[str, Any]] = []

    def send_message(
        self,
        conn: sqlite3.Connection,
        case_id: str,
        to_email: str,
        subject: str,
        body: str,
        email_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Dispatch an email via the mock service and record in the emails table."""
        sent_at = now_ts()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO emails (case_id, to_email, subject, body, email_type, sent_at) VALUES (?, ?, ?, ?, ?, ?)",
            (case_id, to_email, subject, body, email_type, sent_at),
        )
        row_id = cur.lastrowid
        conn.commit()

        message_id = f"msg_{row_id:04d}_{uuid.uuid4().hex[:8]}"
        record = {
            "sent": True,
            "message_id": message_id,
            "id": row_id,
            "case_id": case_id,
            "from": self.sender,
            "to": to_email,
            "subject": subject,
            "body": body,
            "email_type": email_type,
            "status": "DELIVERED",
            "sent_at": sent_at,
            "metadata": metadata or {},
        }
        self._outbox.append(record)
        return record

    def get_outbox(
        self,
        conn: Optional[sqlite3.Connection] = None,
        limit: int = 50,
        to_email: Optional[str] = None,
        case_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve sent emails from persistent storage or outbox buffer."""
        if conn is not None:
            query = "SELECT id, case_id, to_email, subject, body, email_type, sent_at FROM emails WHERE 1=1"
            params: List[Any] = []
            if to_email:
                query += " AND to_email = ?"
                params.append(to_email)
            if case_id:
                query += " AND case_id = ?"
                params.append(case_id)
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            results = []
            for r in rows:
                row_dict = dict(r)
                row_id = row_dict["id"]
                matching = next((m for m in reversed(self._outbox) if m["id"] == row_id), None)
                if matching:
                    results.append(matching)
                else:
                    results.append({
                        "sent": True,
                        "message_id": f"msg_{row_id:04d}",
                        "id": row_id,
                        "case_id": row_dict["case_id"],
                        "from": self.sender,
                        "to": row_dict["to_email"],
                        "subject": row_dict["subject"],
                        "body": row_dict["body"],
                        "email_type": row_dict["email_type"],
                        "status": "DELIVERED",
                        "sent_at": row_dict["sent_at"],
                        "metadata": {},
                    })
            return results

        items = self._outbox
        if to_email:
            items = [m for m in items if m["to"] == to_email]
        if case_id:
            items = [m for m in items if m["case_id"] == case_id]
        return list(reversed(items[-limit:]))

    def get_message(self, conn: sqlite3.Connection, message_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a specific dispatched message by its message_id."""
        for m in self._outbox:
            if m["message_id"] == message_id:
                return m

        try:
            parts = message_id.split("_")
            if len(parts) >= 2 and parts[1].isdigit():
                row_id = int(parts[1])
                row = conn.execute("SELECT * FROM emails WHERE id = ?", (row_id,)).fetchone()
                if row:
                    d = dict(row)
                    return {
                        "sent": True,
                        "message_id": message_id,
                        "id": d["id"],
                        "case_id": d["case_id"],
                        "from": self.sender,
                        "to": d["to_email"],
                        "subject": d["subject"],
                        "body": d["body"],
                        "email_type": d["email_type"],
                        "status": "DELIVERED",
                        "sent_at": d["sent_at"],
                        "metadata": {},
                    }
        except Exception:
            pass
        return None

    def clear_outbox(self) -> None:
        """Clear memory cache (useful for test resets)."""
        self._outbox.clear()


# Global singleton service
email_service = MockEmailService()

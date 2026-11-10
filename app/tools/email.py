import sqlite3
from typing import Any, Dict

from app.db import now_ts
from app.tools.base import ToolContext, tool


def insert_email(conn: sqlite3.Connection, case_id: str, to_email: str, subject: str, body: str, email_type: str) -> Dict[str, Any]:
    conn.execute(
        "INSERT INTO emails (case_id, to_email, subject, body, email_type, sent_at) VALUES (?, ?, ?, ?, ?, ?)",
        (case_id, to_email, subject, body, email_type, now_ts()),
    )
    conn.commit()
    return {"sent": True, "to": to_email, "subject": subject}


@tool("email.send", "Send an email to a customer (mock inbox).",
      {"case_id": "string", "to_email": "string", "subject": "string", "body": "string", "email_type": "string"})
def email_send(ctx: ToolContext, case_id: str, to_email: str, subject: str, body: str, email_type: str) -> Dict[str, Any]:
    return insert_email(ctx.conn, case_id, to_email, subject, body, email_type)

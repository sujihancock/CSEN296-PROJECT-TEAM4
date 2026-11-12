"""MCP tool for customer notification email dispatch."""
import sqlite3
from typing import Any, Dict

from app.email_api import email_service
from app.tools.base import ToolContext, tool


def insert_email(conn: sqlite3.Connection, case_id: str, to_email: str, subject: str, body: str, email_type: str) -> Dict[str, Any]:
    """Dispatch an email via the mock email service and store in SQLite."""
    return email_service.send_message(conn, case_id, to_email, subject, body, email_type)


@tool(
    "email.send",
    "Send an email to a customer via the mock email service provider.",
    {
        "case_id": "string",
        "to_email": "string",
        "subject": "string",
        "body": "string",
        "email_type": "string",
    },
)
def email_send(ctx: ToolContext, case_id: str, to_email: str, subject: str, body: str, email_type: str) -> Dict[str, Any]:
    return insert_email(ctx.conn, case_id, to_email, subject, body, email_type)

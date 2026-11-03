import sqlite3
from typing import Dict, Any, List, Optional
from app.backend.database import get_connection

class ReportingSpecialist:
    """
    Reporting Specialist Agent:
    - Maintains an immutable audit log of decisions (APPROVED, DENIED, ESCALATED, OVERRIDDEN)
    - Records justifications, safety guardrail triggers, and timestamps
    """

    @classmethod
    def log_decision(
        cls,
        case_id: Optional[str],
        action: str,
        agent: str,
        details: str,
        status: str,
        conn: sqlite3.Connection
    ) -> Dict[str, Any]:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO audit_logs (case_id, action, agent, details, status)
            VALUES (?, ?, ?, ?, ?);
        """, (case_id, action, agent, details, status))
        conn.commit()
        return {
            "case_id": case_id,
            "action": action,
            "agent": agent,
            "status": status,
            "details": details
        }

    @classmethod
    def get_audit_trail(cls, limit: int = 50, conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
        close_after = False
        if conn is None:
            conn = get_connection()
            close_after = True

        cur = conn.cursor()
        cur.execute("""
            SELECT id, case_id, action, agent, details, status, created_at
            FROM audit_logs
            ORDER BY id DESC
            LIMIT ?;
        """, (limit,))
        rows = cur.fetchall()

        if close_after:
            conn.close()

        return [dict(r) for r in rows]


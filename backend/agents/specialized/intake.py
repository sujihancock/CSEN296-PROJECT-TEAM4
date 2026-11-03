import sqlite3
import uuid
from typing import Dict, Any
from backend.database import get_connection

class IntakeSpecialist:
    """
    Intake Specialist Agent:
    - Receives support cases from customers
    - Generates ticket ID and assigns case
    """

    @classmethod
    def create_case(
        cls,
        customer_id: str,
        order_id: str,
        amount_requested: float,
        reason: str,
        conn: sqlite3.Connection
    ) -> Dict[str, Any]:
        cur = conn.cursor()
        case_id = f"CASE-{uuid.uuid4().hex[:6].upper()}"
        assigned_to = "Agent-ChangSupport-Core"

        cur.execute("""
            INSERT INTO cases (id, customer_id, order_id, amount_requested, reason, status, assigned_to)
            VALUES (?, ?, ?, ?, ?, 'RECEIVED', ?);
        """, (case_id, customer_id, order_id, amount_requested, reason, assigned_to))
        conn.commit()

        return {
            "case_id": case_id,
            "customer_id": customer_id,
            "order_id": order_id,
            "amount_requested": amount_requested,
            "status": "RECEIVED",
            "assigned_to": assigned_to,
            "message": f"Case {case_id} successfully created and assigned to {assigned_to}."
        }


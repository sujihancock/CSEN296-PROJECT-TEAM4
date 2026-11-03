import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple
from app.backend.config import MAX_AUTONOMOUS_REFUND_PER_TX, MAX_WEEKLY_CUMULATIVE_REFUNDS
from app.backend.database import get_connection

class PolicyGuardrailEngine:
    """
    Independent Policy Enforcement Layer.
    Enforces governance and security boundaries completely outside the LLM.
    Any call to move funds or export records passes through this layer.
    """

    @staticmethod
    def is_kill_switch_active(conn: sqlite3.Connection) -> bool:
        cur = conn.cursor()
        cur.execute("SELECT value FROM system_settings WHERE key = 'kill_switch_active';")
        row = cur.fetchone()
        return row and row["value"].lower() == "true"

    @staticmethod
    def set_kill_switch(active: bool, supervisor_id: str, reason: str = "") -> Dict[str, Any]:
        conn = get_connection()
        cur = conn.cursor()
        val = "true" if active else "false"
        cur.execute(
            "INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES ('kill_switch_active', ?, CURRENT_TIMESTAMP);",
            (val,)
        )
        cur.execute("""
            INSERT INTO audit_logs (case_id, action, agent, details, status)
            VALUES (NULL, 'SET_KILL_SWITCH', ?, ?, ?);
        """, (supervisor_id, f"Kill switch state changed to {val}. Reason: {reason}", "ALLOWED"))
        conn.commit()
        conn.close()
        return {"kill_switch_active": active, "updated_by": supervisor_id, "reason": reason}

    @staticmethod
    def get_weekly_autonomous_refunds_total(conn: sqlite3.Connection) -> float:
        """
        Calculates the cumulative sum of refunds autonomously approved in the past 7 days.
        """
        cur = conn.cursor()
        seven_days_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
        cur.execute("""
            SELECT COALESCE(SUM(amount_requested), 0.0) as total
            FROM cases
            WHERE status = 'APPROVED'
              AND autonomous = 1
              AND updated_at >= ?;
        """, (seven_days_ago,))
        row = cur.fetchone()
        return float(row["total"]) if row else 0.0

    @classmethod
    def evaluate_refund_request(
        cls,
        customer_id: str,
        order_id: str,
        amount_requested: float,
        conn: sqlite3.Connection
    ) -> Tuple[str, str, Dict[str, Any]]:
        """
        Evaluates a proposed refund against hard governance boundaries.
        Returns:
            (verdict, reason, metadata)
            verdict: 'ALLOWED' | 'ESCALATED' | 'BLOCKED'
        """
        cur = conn.cursor()

        # 1. Kill-Switch Verification (Emergency Revocation)
        if cls.is_kill_switch_active(conn):
            return (
                "BLOCKED",
                "SECURITY GUARDRAIL TRIP: Agent authority has been revoked via Supervisor Kill-Switch. Fail-closed enforced.",
                {"code": "KILL_SWITCH_ACTIVE", "limit": 0.0, "amount": amount_requested}
            )

        # 2. Scope & Ownership Check (Prevent unauthorized horizontal access)
        cur.execute("SELECT customer_id, amount, status, purchase_date FROM orders WHERE id = ?;", (order_id,))
        order = cur.fetchone()
        if not order:
            return (
                "BLOCKED",
                f"SECURITY GUARDRAIL TRIP: Order {order_id} does not exist.",
                {"code": "INVALID_ORDER", "order_id": order_id}
            )

        if order["customer_id"] != customer_id:
            return (
                "BLOCKED",
                f"SECURITY VIOLATION: Customer {customer_id} is not the owner of Order {order_id}. Unauthorized cross-tenant access blocked.",
                {"code": "IDENTITY_PRIVILEGE_MISMATCH", "owner": order["customer_id"], "requester": customer_id}
            )

        # Ensure requested amount does not exceed original order value
        if amount_requested > float(order["amount"]):
            return (
                "BLOCKED",
                f"VALIDATION ERROR: Requested refund (${amount_requested:.2f}) exceeds the original purchase amount (${order['amount']:.2f}).",
                {"code": "AMOUNT_EXCEEDS_ORDER", "order_amount": float(order["amount"]), "requested": amount_requested}
            )

        # 3. Single-Transaction Autonomous Limit ($100 cap)
        if amount_requested > MAX_AUTONOMOUS_REFUND_PER_TX:
            return (
                "ESCALATED",
                f"POLICY THRESHOLD: Requested amount (${amount_requested:.2f}) exceeds the single-transaction autonomous limit of ${MAX_AUTONOMOUS_REFUND_PER_TX:.2f}. Escalated to Supervisor.",
                {"code": "TX_LIMIT_EXCEEDED", "limit": MAX_AUTONOMOUS_REFUND_PER_TX, "amount": amount_requested}
            )

        # 4. Weekly Cumulative Autonomous Limit ($500 cap)
        weekly_current = cls.get_weekly_autonomous_refunds_total(conn)
        if (weekly_current + amount_requested) > MAX_WEEKLY_CUMULATIVE_REFUNDS:
            return (
                "ESCALATED",
                f"POLICY THRESHOLD: Weekly cumulative autonomous refunds (${weekly_current:.2f} + ${amount_requested:.2f} = ${weekly_current + amount_requested:.2f}) would exceed the $500 weekly safety cap. Escalated to Supervisor.",
                {"code": "WEEKLY_CAP_EXCEEDED", "current_total": weekly_current, "cap": MAX_WEEKLY_CUMULATIVE_REFUNDS, "amount": amount_requested}
            )

        return (
            "ALLOWED",
            f"Compliant with autonomous policy (<= ${MAX_AUTONOMOUS_REFUND_PER_TX:.2f}/tx and weekly cap <= ${MAX_WEEKLY_CUMULATIVE_REFUNDS:.2f}).",
            {"code": "POLICY_PASSED", "weekly_total_before": weekly_current, "amount": amount_requested}
        )

    @classmethod
    def process_bank_disbursement(
        cls,
        customer_id: str,
        amount: float,
        conn: sqlite3.Connection
    ) -> Tuple[bool, str, float]:
        """
        Executes financial transfer (mock bank balance update).
        Guarded strictly by policy layer.
        """
        if cls.is_kill_switch_active(conn):
            return False, "Disbursement blocked: Kill switch is active.", 0.0

        cur = conn.cursor()
        cur.execute("SELECT bank_balance FROM customers WHERE id = ?;", (customer_id,))
        row = cur.fetchone()
        if not row:
            return False, f"Customer {customer_id} not found.", 0.0

        new_balance = float(row["bank_balance"]) + amount
        cur.execute("UPDATE customers SET bank_balance = ? WHERE id = ?;", (new_balance, customer_id))
        conn.commit()
        return True, f"Successfully refunded ${amount:.2f} to customer account.", new_balance


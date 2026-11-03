import sqlite3
from typing import Dict, Any, Tuple
from datetime import datetime
from app.backend.rag.policy_rag import PolicyRAG
from app.backend.guardrails.policy_engine import PolicyGuardrailEngine

class RefundsSpecialist:
    """
    Refunds Specialist Agent:
    - Evaluates customer refund claims against enterprise policies retrieved via RAG
    - Routes financial operations through the independent Policy Guardrail layer
    - Updates case status and disburses funds if authorized
    """

    @classmethod
    def process_refund(
        cls,
        case_id: str,
        customer_id: str,
        order: Dict[str, Any],
        amount_requested: float,
        reason: str,
        conn: sqlite3.Connection
    ) -> Dict[str, Any]:
        cur = conn.cursor()

        # Step 1: Retrieve relevant enterprise policies using RAG
        rag_results = PolicyRAG.query_policy(reason, top_k=2)
        top_policy = rag_results[0] if rag_results else None

        # Step 2: Policy Compliance Check (e.g. check digital goods or 30-day window)
        order_date_str = order.get("purchase_date")
        days_since_purchase = 0
        if order_date_str:
            order_date = datetime.fromisoformat(order_date_str.replace("Z", ""))
            days_since_purchase = (datetime.utcnow() - order_date).days

        # Business Rule 1: Outside 30-day return window unless defective
        is_damaged = any(k in reason.lower() for k in ["damaged", "broken", "defective", "shattered"])
        if days_since_purchase > 30 and not is_damaged:
            decision = "DENIED"
            decision_reason = f"Return window expired ({days_since_purchase} days since purchase; policy requires <= 30 days)."
            cur.execute("""
                UPDATE cases
                SET status = ?, decision_reason = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
            """, (decision, decision_reason, case_id))
            conn.commit()
            return {
                "verdict": decision,
                "reason": decision_reason,
                "autonomous": True,
                "refunded_amount": 0.0,
                "matched_policy": top_policy["title"] if top_policy else "Default Policy"
            }

        # Business Rule 2: Digital goods non-refundable
        is_digital = "ebook" in order.get("product_name", "").lower() or "digital" in reason.lower()
        if is_digital and not is_damaged:
            decision = "DENIED"
            decision_reason = "Digital items are non-refundable per Policy POL-003."
            cur.execute("""
                UPDATE cases
                SET status = ?, decision_reason = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
            """, (decision, decision_reason, case_id))
            conn.commit()
            return {
                "verdict": decision,
                "reason": decision_reason,
                "autonomous": True,
                "refunded_amount": 0.0,
                "matched_policy": top_policy["title"] if top_policy else "POL-003"
            }

        # Step 3: Independent Policy Guardrail Layer Check
        guardrail_verdict, guardrail_reason, meta = PolicyGuardrailEngine.evaluate_refund_request(
            customer_id=customer_id,
            order_id=order["id"],
            amount_requested=amount_requested,
            conn=conn
        )

        if guardrail_verdict == "BLOCKED":
            decision = "REVOKED" if "KILL_SWITCH" in meta.get("code", "") else "DENIED"
            cur.execute("""
                UPDATE cases
                SET status = ?, decision_reason = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
            """, (decision, guardrail_reason, case_id))
            conn.commit()
            return {
                "verdict": decision,
                "reason": guardrail_reason,
                "autonomous": True,
                "refunded_amount": 0.0,
                "guardrail_meta": meta
            }

        elif guardrail_verdict == "ESCALATED":
            cur.execute("""
                UPDATE cases
                SET status = 'ESCALATED', decision_reason = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
            """, (guardrail_reason, case_id))
            conn.commit()
            return {
                "verdict": "ESCALATED",
                "reason": guardrail_reason,
                "autonomous": False,
                "refunded_amount": 0.0,
                "guardrail_meta": meta
            }

        # Step 4: Approved - execute disbursement
        success, disburse_msg, new_bal = PolicyGuardrailEngine.process_bank_disbursement(
            customer_id=customer_id,
            amount=amount_requested,
            conn=conn
        )

        if not success:
            cur.execute("""
                UPDATE cases
                SET status = 'REVOKED', decision_reason = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
            """, (disburse_msg, case_id))
            conn.commit()
            return {
                "verdict": "REVOKED",
                "reason": disburse_msg,
                "autonomous": True,
                "refunded_amount": 0.0
            }

        decision_reason = f"Approved per policy ({top_policy['title'] if top_policy else 'Standard Refund Policy'}). {disburse_msg}"
        cur.execute("""
            UPDATE cases
            SET status = 'APPROVED', decision_reason = ?, autonomous = 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
        """, (decision_reason, case_id))
        conn.commit()

        return {
            "verdict": "APPROVED",
            "reason": decision_reason,
            "autonomous": True,
            "refunded_amount": amount_requested,
            "new_bank_balance": new_bal,
            "matched_policy": top_policy["title"] if top_policy else "Standard Policy"
        }


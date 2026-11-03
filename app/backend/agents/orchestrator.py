import sqlite3
from typing import Dict, Any, List
from app.backend.database import get_connection
from app.backend.agents.specialized.intake import IntakeSpecialist
from app.backend.agents.specialized.email import EmailSpecialist
from app.backend.agents.specialized.records import RecordsSpecialist
from app.backend.agents.specialized.refunds import RefundsSpecialist
from app.backend.agents.specialized.reporting import ReportingSpecialist
from app.backend.guardrails.policy_engine import PolicyGuardrailEngine

class CoreOrchestrator:
    """
    ChangSupport.AI Core Orchestrator Agent.
    - Task Decomposition: Analyzes user requests into sub-tasks.
    - Agent Routing: Coordinates Intake, Email, Records, Refunds, and Reporting agents.
    - Response Aggregation: Synthesizes multi-agent outputs into a coherent result.
    - Trace Logging: Keeps a transparent execution trace for real-time observability.
    """

    @classmethod
    def process_refund_request(
        cls,
        customer_id: str,
        order_id: str,
        amount_requested: float,
        reason: str
    ) -> Dict[str, Any]:
        trace: List[Dict[str, str]] = []
        conn = get_connection()

        try:
            # Step 1: Decomposition & Planning
            trace.append({
                "agent": "Orchestrator",
                "phase": "Task Decomposition",
                "message": f"Decomposing refund request for customer {customer_id}, order {order_id}, amount ${amount_requested:.2f}."
            })

            # Check Kill Switch before delegating
            if PolicyGuardrailEngine.is_kill_switch_active(conn):
                ReportingSpecialist.log_decision(
                    case_id=None,
                    action="AGENT_EXECUTION_BLOCKED",
                    agent="Orchestrator",
                    details="Execution blocked: Agent authority revoked by Supervisor Kill Switch.",
                    status="BLOCKED",
                    conn=conn
                )
                trace.append({
                    "agent": "PolicyGuardrail",
                    "phase": "Fail-Closed Check",
                    "message": "Supervisor Kill Switch is active. Consequential agent actions revoked."
                })
                return {
                    "status": "REVOKED",
                    "message": "Agent operations are currently suspended by supervisor emergency kill-switch.",
                    "trace": trace
                }

            # Step 2: Route to Intake Specialist
            trace.append({
                "agent": "Intake Specialist",
                "phase": "Case Submission & Assignment",
                "message": f"Registering ticket in customer support registry..."
            })
            intake_res = IntakeSpecialist.create_case(customer_id, order_id, amount_requested, reason, conn)
            case_id = intake_res["case_id"]
            trace.append({
                "agent": "Intake Specialist",
                "phase": "Case Created",
                "message": f"Generated Case ID #{case_id} assigned to Core Agent."
            })

            # Step 3: Route to Records Specialist to fetch customer details
            trace.append({
                "agent": "Records Specialist",
                "phase": "Customer Profile Verification",
                "message": f"Validating account information and boundaries for {customer_id}..."
            })
            customer_profile = RecordsSpecialist.get_customer_profile(customer_id, requesting_customer_id=customer_id, conn=conn)
            if not customer_profile:
                raise ValueError(f"Customer record {customer_id} not found.")

            # Step 4: Route to Email Specialist for Acknowledgement
            trace.append({
                "agent": "Email Specialist",
                "phase": "Customer Acknowledgement",
                "message": f"Dispatching receipt acknowledgement to {customer_profile['email']}..."
            })
            EmailSpecialist.send_acknowledgement(
                case_id=case_id,
                to_email=customer_profile["email"],
                customer_name=customer_profile["name"],
                amount=amount_requested,
                conn=conn
            )

            # Step 5: Route to Records Specialist to verify Order Details
            trace.append({
                "agent": "Records Specialist",
                "phase": "Order Data Retrieval",
                "message": f"Retrieving order specifications for Order #{order_id}..."
            })
            order = RecordsSpecialist.verify_order(customer_id, order_id, conn=conn)
            if not order:
                # Order does not exist
                reason_denied = f"Order #{order_id} does not exist in the merchant system."
                cur = conn.cursor()
                cur.execute("UPDATE cases SET status = 'DENIED', decision_reason = ? WHERE id = ?;", (reason_denied, case_id))
                conn.commit()
                EmailSpecialist.send_decision_notification(case_id, customer_profile["email"], customer_profile["name"], "DENIED", reason_denied, amount_requested, conn)
                ReportingSpecialist.log_decision(case_id, "ORDER_NOT_FOUND", "RecordsSpecialist", reason_denied, "BLOCKED", conn)
                return {"status": "DENIED", "case_id": case_id, "message": reason_denied, "trace": trace}

            # Step 6: Route to Refunds Specialist (Policy RAG + Policy Guardrails)
            trace.append({
                "agent": "Refunds Specialist",
                "phase": "Policy Evaluation & Guardrail Verification",
                "message": f"Evaluating refund reason: '{reason}' against enterprise knowledge base..."
            })
            refund_res = RefundsSpecialist.process_refund(
                case_id=case_id,
                customer_id=customer_id,
                order=order,
                amount_requested=amount_requested,
                reason=reason,
                conn=conn
            )
            verdict = refund_res["verdict"]
            decision_reason = refund_res["reason"]

            trace.append({
                "agent": "Refunds Specialist",
                "phase": "Verdict Determined",
                "message": f"Outcome: {verdict} - {decision_reason}"
            })

            # Step 7: Route to Email Specialist for Decision Notification
            trace.append({
                "agent": "Email Specialist",
                "phase": "Decision Notification",
                "message": f"Sending decision notification ({verdict}) to customer inbox..."
            })
            EmailSpecialist.send_decision_notification(
                case_id=case_id,
                to_email=customer_profile["email"],
                customer_name=customer_profile["name"],
                status=verdict,
                reason=decision_reason,
                amount=amount_requested,
                conn=conn
            )

            # Step 8: Route to Reporting Specialist for Audit Trail
            log_status = "ALLOWED" if verdict == "APPROVED" else ("ESCALATED" if verdict == "ESCALATED" else "BLOCKED")
            trace.append({
                "agent": "Reporting Specialist",
                "phase": "Immutable Audit Logging",
                "message": f"Persisting decision trail to tamper-evident audit table (Status: {log_status})."
            })
            ReportingSpecialist.log_decision(
                case_id=case_id,
                action=f"REFUND_EVALUATION_{verdict}",
                agent="RefundsSpecialist",
                details=f"Amount: ${amount_requested:.2f} | Reason: {reason} | Decision: {decision_reason}",
                status=log_status,
                conn=conn
            )

            # Step 9: Aggregate final response
            trace.append({
                "agent": "Orchestrator",
                "phase": "Response Aggregation",
                "message": f"Workflow completed successfully. Final state: {verdict}."
            })

            return {
                "case_id": case_id,
                "status": verdict,
                "amount": amount_requested,
                "reason": decision_reason,
                "refunded_amount": refund_res.get("refunded_amount", 0.0),
                "new_bank_balance": refund_res.get("new_bank_balance"),
                "trace": trace
            }

        finally:
            conn.close()

    @classmethod
    def supervisor_override(cls, case_id: str, supervisor_id: str, approved: bool, supervisor_comment: str) -> Dict[str, Any]:
        """
        Allows human supervisor to review and authorize/deny an escalated case.
        """
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM cases WHERE id = ?;", (case_id,))
            case = cur.fetchone()
            if not case:
                raise ValueError(f"Case {case_id} not found.")

            customer_id = case["customer_id"]
            amount = float(case["amount_requested"])
            cur.execute("SELECT * FROM customers WHERE id = ?;", (customer_id,))
            customer = cur.fetchone()

            if approved:
                new_status = "APPROVED"
                full_reason = f"Authorized by Supervisor {supervisor_id}. Note: {supervisor_comment}"
                # Process bank disbursement through supervisor authority
                cur.execute("UPDATE customers SET bank_balance = bank_balance + ? WHERE id = ?;", (amount, customer_id))
            else:
                new_status = "DENIED"
                full_reason = f"Denied by Supervisor {supervisor_id}. Note: {supervisor_comment}"

            cur.execute("""
                UPDATE cases
                SET status = ?, decision_reason = ?, autonomous = 0, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
            """, (new_status, full_reason, case_id))

            # Send Email notification
            if customer:
                EmailSpecialist.send_decision_notification(
                    case_id=case_id,
                    to_email=customer["email"],
                    customer_name=customer["name"],
                    status=new_status,
                    reason=full_reason,
                    amount=amount,
                    conn=conn
                )

            # Audit log
            ReportingSpecialist.log_decision(
                case_id=case_id,
                action=f"SUPERVISOR_OVERRIDE_{new_status}",
                agent=supervisor_id,
                details=full_reason,
                status="OVERRIDDEN",
                conn=conn
            )
            conn.commit()

            return {
                "case_id": case_id,
                "status": new_status,
                "supervisor_id": supervisor_id,
                "reason": full_reason
            }
        finally:
            conn.close()


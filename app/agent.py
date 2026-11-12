"""ChangSupport.AI agents.

The Orchestrator (the template's `Agent`) plans a request, routes sub-tasks to
five specialist agents, and aggregates their results. Specialists only act
through `tools.call_tool`, so every action passes the guardrail layer.
"""
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app import config, guardrails, tools
from app.audit import log_event
from app.db import parse_ts
from app.guardrails import GuardrailViolation
from app.llm import BaseLLM, MockLLM, get_llm


class Specialist:
    name = "Specialist"

    def __init__(self, conn: sqlite3.Connection, session_customer_id: str):
        self.conn = conn
        self.session = session_customer_id

    def call(self, tool: str, **params) -> Any:
        return tools.call_tool(self.conn, self.name, tool, params, self.session)


class IntakeAgent(Specialist):
    name = "IntakeAgent"

    def open_case(self, order_id: str, amount: float, reason: str) -> Dict[str, Any]:
        return self.call("cases.create", customer_id=self.session, order_id=order_id,
                         amount_requested=amount, reason=reason)


class RecordsAgent(Specialist):
    name = "RecordsAgent"

    def customer(self) -> Optional[Dict[str, Any]]:
        return self.call("records.get_customer", customer_id=self.session)

    def order(self, order_id: str) -> Optional[Dict[str, Any]]:
        return self.call("records.get_order", order_id=order_id)

    def history(self) -> List[Dict[str, Any]]:
        return self.call("records.get_case_history", customer_id=self.session)


class EmailAgent(Specialist):
    name = "EmailAgent"

    def acknowledge(self, case_id: str, customer: Dict[str, Any], amount: float) -> Dict[str, Any]:
        return self.call(
            "email.send", case_id=case_id, to_email=customer["email"],
            subject=f"We received your refund request ({case_id})",
            body=f"Hi {customer['name']}, we received your request for a ${amount:.2f} refund and are reviewing it.",
            email_type="ACKNOWLEDGEMENT",
        )

    def notify_decision(self, case_id: str, customer: Dict[str, Any], status: str, reason: str) -> Dict[str, Any]:
        return self.call(
            "email.send", case_id=case_id, to_email=customer["email"],
            subject=f"Update on your refund request ({case_id}): {status}",
            body=f"Hi {customer['name']}, your refund request is {status}. {reason}",
            email_type="DECISION",
        )


class RefundsAgent(Specialist):
    name = "RefundsAgent"

    def __init__(self, conn, session_customer_id, llm: Optional[BaseLLM] = None):
        super().__init__(conn, session_customer_id)
        self.llm = llm or get_llm()

    def evaluate(self, case: Dict[str, Any], order: Dict[str, Any], reason: str) -> Dict[str, Any]:
        case_id = case["case_id"]
        self.call("cases.update_status", case_id=case_id, status="UNDER_REVIEW", decision_reason="")
        policies = self.call("policy.search", query=f"{reason} {order['product_name']} {order['category']}")
        days = (datetime.now(timezone.utc) - parse_ts(order["purchase_date"])).days
        proposal = self.llm.propose_refund_decision(
            reason, order, case["amount_requested"], policies, days, config.RETURN_WINDOW_DAYS)
        result = {
            "llm_proposal": proposal,
            "refunded_amount": 0.0,
            "new_bank_balance": None,
            "retrieved_policies": policies,
        }

        if proposal["action"] != "APPROVE":
            self.call("cases.update_status", case_id=case_id, status="DENIED", decision_reason=proposal["rationale"])
            return {**result, "status": "DENIED", "reason": proposal["rationale"]}

        try:
            paid = self.call("bank.disburse", case_id=case_id, amount=proposal["amount"])
        except GuardrailViolation as v:
            d = v.decision
            if d.code == "KILL_SWITCH_ACTIVE":
                raise
            status = "ESCALATED" if d.verdict == guardrails.ESCALATED else "DENIED"
            self.call("cases.update_status", case_id=case_id, status=status, decision_reason=d.reason)
            return {**result, "status": status, "reason": d.reason, "guardrail": d.as_dict()}

        reason_text = f"Approved: {proposal['rationale']}"
        self.call("cases.update_status", case_id=case_id, status="APPROVED", decision_reason=reason_text)
        return {**result, "status": "APPROVED", "reason": reason_text,
                "refunded_amount": paid["paid"], "new_bank_balance": paid["new_bank_balance"]}


class ReportingAgent(Specialist):
    name = "ReportingAgent"

    def record(self, case_id: str, status: str, reason: str) -> Dict[str, Any]:
        return self.call("reporting.record_decision", case_id=case_id, decision=status, reason=reason)


class Orchestrator:
    """Core agent: plan -> route to specialists -> aggregate."""

    REFUND_PLAN = [
        "intake:create_case",
        "email:send_acknowledgement",
        "records:validate_customer_and_order",
        "refunds:evaluate_and_process",
        "email:send_decision",
        "reporting:record_decision",
    ]

    def __init__(self, llm: Optional[BaseLLM] = None):
        self.llm = llm or get_llm()

    def plan(self, task: str, data: Dict[str, Any]) -> List[str]:
        if task == "refund_request":
            return list(self.REFUND_PLAN)
        raise ValueError(f"Unknown task {task!r}")

    def handle_task(self, conn: sqlite3.Connection, task: str, data: Dict[str, Any], session_customer_id: str) -> Dict[str, Any]:
        self.plan(task, data)  # validates the task type
        return self.process_refund_request(
            conn, session_customer_id, data["order_id"], float(data["amount"]), str(data.get("reason", "")))

    def process_refund_request(self, conn: sqlite3.Connection, customer_id: str, order_id: str,
                               amount: float, reason: str) -> Dict[str, Any]:
        trace: List[Dict[str, str]] = []

        def step(agent: str, message: str) -> None:
            trace.append({"agent": agent, "message": message})

        step("Orchestrator", f"Plan: {', '.join(self.REFUND_PLAN)}")
        if guardrails.is_kill_switch_active(conn):
            log_event(conn, "Orchestrator", "AGENT_EXECUTION_BLOCKED", guardrails.BLOCKED,
                      {"reason": "kill switch active", "customer_id": customer_id})
            return self._revoked(trace)

        intake = IntakeAgent(conn, customer_id)
        email = EmailAgent(conn, customer_id)
        records = RecordsAgent(conn, customer_id)
        refunds = RefundsAgent(conn, customer_id, self.llm)
        reporting = ReportingAgent(conn, customer_id)

        case_id = None
        try:
            case = intake.open_case(order_id, amount, reason)
            case_id = case["case_id"]
            case["amount_requested"] = amount
            step("IntakeAgent", f"Created case {case_id}.")

            customer = records.customer()
            ack = email.acknowledge(case_id, customer, amount)
            step("EmailAgent", f"Sent acknowledgement to {customer['email']}.")

            order = records.order(order_id)
            history = records.history()
            step("RecordsAgent", f"Verified order {order_id}; customer has {len(history)} case(s) on file.")

            outcome = refunds.evaluate(case, order, reason)
            step("RefundsAgent", f"LLM proposed {outcome['llm_proposal']['action']} "
                                 f"${outcome['llm_proposal']['amount']:.2f}; final status {outcome['status']}.")

            dec_email = email.notify_decision(case_id, customer, outcome["status"], outcome["reason"])
            step("EmailAgent", "Sent decision notification.")

            reporting.record(case_id, outcome["status"], outcome["reason"])
            step("ReportingAgent", "Recorded decision in the audit trail.")

            return {
                "case_id": case_id,
                "trace": trace,
                "customer": customer,
                "order": order,
                "acknowledgement_email": ack,
                "decision_email": dec_email,
                **outcome,
            }

        except GuardrailViolation as v:
            d = v.decision
            if d.code == "KILL_SWITCH_ACTIVE":
                if case_id:
                    guardrails.mark_case(conn, case_id, "REVOKED", d.reason)
                return self._revoked(trace, case_id)
            step("PolicyGuardrail", f"Blocked: {d.reason}")
            return {"case_id": case_id, "status": "BLOCKED", "reason": d.reason, "guardrail": d.as_dict(),
                    "refunded_amount": 0.0, "trace": trace}

    @staticmethod
    def _revoked(trace: List[Dict[str, str]], case_id: Optional[str] = None) -> Dict[str, Any]:
        msg = "Agent operations are suspended by the supervisor kill-switch."
        trace.append({"agent": "PolicyGuardrail", "message": msg})
        return {"case_id": case_id, "status": "REVOKED", "message": msg, "reason": msg,
                "refunded_amount": 0.0, "trace": trace}

    # Human path: not an agent action, so it does not use agent tools.
    def supervisor_review(self, conn: sqlite3.Connection, case_id: str, supervisor_id: str,
                          approve: bool, comment: str) -> Dict[str, Any]:
        result = guardrails.supervisor_decide(conn, case_id, supervisor_id, approve, comment)
        row = conn.execute(
            "SELECT c.email, c.name FROM cases k JOIN customers c ON c.id = k.customer_id WHERE k.id = ?", (case_id,)
        ).fetchone()
        email_res = tools.insert_email(
            conn, case_id, row["email"],
            f"Update on your refund request ({case_id}): {result['status']}",
            f"Hi {row['name']}, a supervisor reviewed your request. {result['reason']}",
            "DECISION",
        )
        result["email"] = email_res
        return result

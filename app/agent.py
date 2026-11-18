"""ChangSupport.AI agents.

The Orchestrator (the template's `Agent`) plans a request, routes sub-tasks to
five specialist agents, and aggregates their results. Specialists only act
through `tools.call_tool`, so every action passes the guardrail layer.
"""
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app import config, guardrails, tools
from app.audit import log_event
from app.db import parse_ts
from app.guardrails import GuardrailViolation
from app.llm import BaseLLM, MockLLM, get_llm

logger = logging.getLogger(__name__)

EventListener = Callable[[Dict[str, Any]], None]


class EventSink:
    """Live progress events for one claim (used by the sandbox's streaming trace).

    Each event names one of the sandbox pipeline steps: intake, records, rag,
    llm, guard, pay. A listener that fails is logged and ignored, so it can
    never change how a claim is decided.
    """

    def __init__(self, listener: Optional[EventListener] = None):
        self.listener = listener
        self.started = time.perf_counter()

    def emit(self, step: str, state: str, agent: str, message: str, **data: Any) -> None:
        if not self.listener:
            return
        event = {"type": "step", "step": step, "state": state, "agent": agent, "message": message,
                 "t": round(time.perf_counter() - self.started, 3), "data": data}
        try:
            self.listener(event)
        except Exception:  # noqa: BLE001 - a broken listener must not break the claim
            logger.exception("Claim event listener failed; continuing without it.")


def model_label(llm: BaseLLM) -> str:
    return getattr(llm, "model", None) or ("mock model" if isinstance(llm, MockLLM) else type(llm).__name__)


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
            subject=f"We received your reimbursement request ({case_id})",
            body=f"Hi {customer['name']}, we received your request for ${amount:.2f} in healthcare expense reimbursement and are reviewing your eligibility.",
            email_type="ACKNOWLEDGEMENT",
        )

    def notify_decision(self, case_id: str, customer: Dict[str, Any], status: str, reason: str) -> Dict[str, Any]:
        return self.call(
            "email.send", case_id=case_id, to_email=customer["email"],
            subject=f"Update on your reimbursement request ({case_id}): {status}",
            body=f"Hi {customer['name']}, your reimbursement request is {status}. {reason}",
            email_type="DECISION",
        )


class RefundsAgent(Specialist):
    name = "RefundsAgent"

    def __init__(self, conn, session_customer_id, llm: Optional[BaseLLM] = None, events: Optional[EventSink] = None):
        super().__init__(conn, session_customer_id)
        self.llm = llm or get_llm()
        self.events = events or EventSink()

    def evaluate(self, case: Dict[str, Any], order: Dict[str, Any], reason: str) -> Dict[str, Any]:
        case_id = case["case_id"]
        self.call("cases.update_status", case_id=case_id, status="UNDER_REVIEW", decision_reason="")
        ev = self.events
        ev.emit("rag", "running", self.name, "Searching benefits program policies (ChromaDB).")
        policies = self.call("policy.search", query=f"{reason} {order['product_name']} {order['category']}")
        ev.emit("rag", "ok" if policies else "warn", self.name,
                f"Retrieved {len(policies)} polic{'y' if len(policies) == 1 else 'ies'}: "
                f"{', '.join(p['id'] for p in policies) or 'none'}.",
                policies=[{"id": p.get("id"), "title": p.get("title"), "score": p.get("score")} for p in policies])
        days = (datetime.now(timezone.utc) - parse_ts(order["purchase_date"])).days
        model = model_label(self.llm)
        ev.emit("llm", "running", self.name, f"Asking {model} to decide entitlement and the eligible amount.",
                model=model)
        t0 = time.perf_counter()
        proposal = self.llm.propose_refund_decision(
            reason, order, case["amount_requested"], policies, days, config.RETURN_WINDOW_DAYS)
        secs = round(time.perf_counter() - t0, 2)
        approve = proposal["action"] == "APPROVE"
        ev.emit("llm", "bad" if proposal.get("followed_embedded_instructions") else "ok" if approve else "warn",
                self.name, f"{proposal.get('model_used') or model} proposed {proposal['action']} "
                f"${proposal['amount']:.2f} after {secs:.1f}s.",
                proposal=proposal, secs=secs, model=proposal.get("model_used") or model)
        result = {
            "llm_proposal": proposal,
            "refunded_amount": 0.0,
            "new_bank_balance": None,
            "retrieved_policies": policies,
        }

        if proposal["action"] != "APPROVE":
            self.call("cases.update_status", case_id=case_id, status="DENIED", decision_reason=proposal["rationale"])
            ev.emit("guard", "bad", "PolicyGuardrail", "Nothing to pay: the model denied the claim.", status="DENIED")
            return {**result, "status": "DENIED", "reason": proposal["rationale"]}

        ev.emit("guard", "running", "PolicyGuardrail",
                f"Checking caps, ownership and the kill switch before paying ${proposal['amount']:.2f}.")
        try:
            paid = self.call("bank.disburse", case_id=case_id, amount=proposal["amount"])
        except GuardrailViolation as v:
            d = v.decision
            if d.code == "KILL_SWITCH_ACTIVE":
                raise
            status = "ESCALATED" if d.verdict == guardrails.ESCALATED else "DENIED"
            ev.emit("guard", "warn" if status == "ESCALATED" else "bad", "PolicyGuardrail",
                    f"{d.code}: {d.reason}", status=status, guardrail=d.as_dict())
            self.call("cases.update_status", case_id=case_id, status=status, decision_reason=d.reason)
            return {**result, "status": status, "reason": d.reason, "guardrail": d.as_dict()}
        ev.emit("guard", "ok", "PolicyGuardrail", "Every check passed.", status="APPROVED")

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

    def handle_task(self, conn: sqlite3.Connection, task: str, data: Dict[str, Any], session_customer_id: str,
                    on_event: Optional[EventListener] = None) -> Dict[str, Any]:
        self.plan(task, data)  # validates the task type
        return self.process_refund_request(
            conn, session_customer_id, data["order_id"], float(data["amount"]), str(data.get("reason", "")),
            on_event=on_event)

    def process_refund_request(self, conn: sqlite3.Connection, customer_id: str, order_id: str,
                               amount: float, reason: str,
                               on_event: Optional[EventListener] = None) -> Dict[str, Any]:
        """Run one claim. `on_event`, if given, receives live step events (see EventSink)."""
        trace: List[Dict[str, str]] = []
        ev = EventSink(on_event)
        later_steps = ("intake", "records", "rag", "llm", "pay")

        def revoked(case_id: Optional[str] = None) -> Dict[str, Any]:
            ev.emit("guard", "bad", "PolicyGuardrail", "Kill switch is on: agent authority revoked, failing closed.",
                    status="REVOKED")
            for s in later_steps:
                if s not in ev_done:
                    ev.emit(s, "skip", "PolicyGuardrail", "Not reached.")
            return self._revoked(trace, case_id)
        ev_done: set = set()

        def step(agent: str, message: str) -> None:
            trace.append({"agent": agent, "message": message})

        step("Orchestrator", f"Plan: {', '.join(self.REFUND_PLAN)}")
        if guardrails.is_kill_switch_active(conn):
            log_event(conn, "Orchestrator", "AGENT_EXECUTION_BLOCKED", guardrails.BLOCKED,
                      {"reason": "kill switch active", "customer_id": customer_id})
            return revoked()

        intake = IntakeAgent(conn, customer_id)
        email = EmailAgent(conn, customer_id)
        records = RecordsAgent(conn, customer_id)
        refunds = RefundsAgent(conn, customer_id, self.llm, ev)
        reporting = ReportingAgent(conn, customer_id)

        case_id = None
        try:
            ev.emit("intake", "running", "IntakeAgent", f"Opening a case for {order_id}.")
            case = intake.open_case(order_id, amount, reason)
            case_id = case["case_id"]
            case["amount_requested"] = amount
            step("IntakeAgent", f"Created case {case_id}.")

            customer = records.customer()
            ack = email.acknowledge(case_id, customer, amount)
            step("EmailAgent", f"Sent acknowledgement to {customer['email']}.")
            ev.emit("intake", "ok", "IntakeAgent", f"Created case {case_id} and emailed a receipt to {customer['email']}.",
                    case_id=case_id)
            ev_done.add("intake")

            ev.emit("records", "running", "RecordsAgent", f"Verifying {order_id} and building the eligibility profile.")
            order = records.order(order_id)
            history = records.history()
            prior = [h for h in history if h.get("id") != case_id]
            step("RecordsAgent", f"Verified expense {order_id} and built the eligibility profile for {customer_id}: "
                                 f"{len(prior)} prior claim(s) on file.")
            ev.emit("records", "ok", "RecordsAgent",
                    f"Verified {order_id} ({order['product_name']}, ${order['amount']:.2f}); "
                    f"{customer_id} has {len(prior)} prior claim(s) on file.",
                    order=order, prior_claims=len(prior))
            ev_done.add("records")

            outcome = refunds.evaluate(case, order, reason)
            step("RefundsAgent", f"LLM proposed {outcome['llm_proposal']['action']} "
                                 f"${outcome['llm_proposal']['amount']:.2f}; final status {outcome['status']}.")

            ev.emit("pay", "running", "EmailAgent", "Sending the decision email.")
            dec_email = email.notify_decision(case_id, customer, outcome["status"], outcome["reason"])
            step("EmailAgent", "Sent decision notification.")

            reporting.record(case_id, outcome["status"], outcome["reason"])
            step("ReportingAgent", "Recorded decision in the audit trail.")
            st = outcome["status"]
            ev.emit("pay", "ok" if st == "APPROVED" else "warn" if st == "ESCALATED" else "bad", "EmailAgent",
                    f"Paid ${outcome['refunded_amount']:.2f}; decision emailed and recorded." if st == "APPROVED"
                    else "Nothing paid until a supervisor decides; beneficiary told it is under review."
                    if st == "ESCALATED" else "Nothing paid; decision emailed and recorded.",
                    status=st, paid=outcome["refunded_amount"], new_balance=outcome.get("new_bank_balance"))

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
                return revoked(case_id)
            step("PolicyGuardrail", f"Blocked: {d.reason}")
            ev.emit("guard", "bad", "PolicyGuardrail", f"{d.code}: {d.reason}", status="BLOCKED", guardrail=d.as_dict())
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
            f"Update on your reimbursement request ({case_id}): {result['status']}",
            f"Hi {row['name']}, a supervisor reviewed your request. {result['reason']}",
            "DECISION",
        )
        result["email"] = email_res
        return result

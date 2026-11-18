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

from app import config, guardrails, records_store, tools
from app.audit import log_event
from app.db import parse_ts
from app.guardrails import GuardrailViolation
from app.llm import BaseLLM, MockLLM, get_llm
from app.orchestration import CANONICAL_PLAN, validate_plan, validate_routes, validate_summary

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

    def attach(self, file_id: str, case_id: str) -> Dict[str, Any]:
        return self.call("drive.attach", file_id=file_id, case_id=case_id)

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

    def search(self, query: str) -> List[Dict[str, Any]]:
        """Records RAG: the beneficiary's own profile, expenses and cases, closest to `query` first."""
        return self.call("records.search_records", customer_id=self.session, query=query)


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

    def evaluate(self, case: Dict[str, Any], order: Dict[str, Any], reason: str,
                 history: Optional[List[Dict[str, Any]]] = None,
                 file_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        case_id = case["case_id"]
        self.call("cases.update_status", case_id=case_id, status="UNDER_REVIEW", decision_reason="")
        ev = self.events
        ev.emit("rag", "running", self.name, "Searching benefits program policies (ChromaDB)"
                + (" and reading the attached case files." if file_ids else "."))
        policies = self.call("policy.search", query=f"{reason} {order['product_name']} {order['category']}")
        # Drive: read each attached file. drive.get_file withholds instruction-like text.
        files = [self.call("drive.get_file", file_id=f) for f in (file_ids or [])]
        documents = [{"name": f["name"], "text": f["text"]} for f in files if f.get("text")]
        withheld = [f["id"] for f in files if f.get("withheld")]
        file_note = (f" Read {len(files)} case file(s)" + (f", {len(withheld)} withheld by the content screen" if withheld else "")
                     + ".") if files else ""
        ev.emit("rag", "warn" if withheld or not policies else "ok", self.name,
                f"Retrieved {len(policies)} polic{'y' if len(policies) == 1 else 'ies'}: "
                f"{', '.join(p['id'] for p in policies) or 'none'}.{file_note}",
                policies=[{"id": p.get("id"), "title": p.get("title"), "score": p.get("score")} for p in policies],
                files=[{k: f.get(k) for k in ("id", "name", "withheld", "flags", "note")} for f in files])
        days = (datetime.now(timezone.utc) - parse_ts(order["purchase_date"])).days
        model = model_label(self.llm)
        ev.emit("llm", "running", self.name, f"Asking {model} to decide entitlement and the eligible amount.",
                model=model)
        t0 = time.perf_counter()
        proposal = self.llm.propose_refund_decision(
            reason, order, case["amount_requested"], policies, days, config.RETURN_WINDOW_DAYS, history,
            documents)
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
            "case_files": [{k: f.get(k) for k in ("id", "name", "content_type", "withheld", "flags", "note")}
                           for f in files],
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
    """Core agent: plan -> route to specialists -> aggregate.

    The LLM proposes the plan, the routes and the final reply (spec 3.2:
    task decomposition, agent routing, response aggregation). The checks in
    app/orchestration.py decide what actually runs, so a hijacked plan or route
    is logged and corrected instead of executed.
    """

    REFUND_PLAN = CANONICAL_PLAN

    def __init__(self, llm: Optional[BaseLLM] = None):
        self.llm = llm or get_llm()

    def plan(self, task: str, data: Dict[str, Any]) -> List[str]:
        """Validate the task type and return the steps it can use (no LLM call)."""
        if task == "refund_request":
            return list(CANONICAL_PLAN)
        raise ValueError(f"Unknown task {task!r}")

    def handle_task(self, conn: sqlite3.Connection, task: str, data: Dict[str, Any], session_customer_id: str,
                    on_event: Optional[EventListener] = None) -> Dict[str, Any]:
        self.plan(task, data)  # validates the task type
        return self.process_refund_request(
            conn, session_customer_id, data["order_id"], float(data["amount"]), str(data.get("reason", "")),
            on_event=on_event, attachments=data.get("attachments") or [])

    # ------------------------------------------------------------- LLM tools

    def decompose_and_route(self, conn: sqlite3.Connection, customer_id: str,
                            request: Dict[str, Any]) -> Dict[str, Any]:
        """Ask the LLM for sub-tasks and routes, then keep only what the catalog allows."""
        t0 = time.perf_counter()
        proposed = self.llm.decompose_task("refund_request", request)
        t1 = time.perf_counter()
        plan_check = validate_plan(proposed.get("steps"))
        steps = proposed.get("steps") if isinstance(proposed.get("steps"), list) else []
        routed = self.llm.route_subtasks(steps)
        secs = {"decompose": round(t1 - t0, 2), "route": round(time.perf_counter() - t1, 2)}
        fallback = bool(proposed.get("fallback_triggered") or routed.get("fallback_triggered"))
        route_check = validate_routes(plan_check.accepted, routed.get("routes"))
        rejections = plan_check.rejected + route_check.rejected
        followed = bool(proposed.get("followed_embedded_instructions") or routed.get("followed_embedded_instructions"))

        log_event(conn, "Orchestrator", "ORCHESTRATOR_PLAN", guardrails.ALLOWED, {
            "customer_id": customer_id, "order_id": request["order_id"],
            "proposed_steps": [s.get("step") if isinstance(s, dict) else s for s in steps],
            "proposed_routes": routed.get("routes"),
            "plan": plan_check.accepted, "routes": route_check.accepted,
            "model": proposed.get("model_used"), "secs": secs, "fallback_triggered": fallback,
        })
        for r in rejections:
            log_event(conn, "Orchestrator", r["code"], guardrails.BLOCKED,
                      {**r, "customer_id": customer_id, "order_id": request["order_id"]})
        return {
            "plan": plan_check.accepted,
            "routes": route_check.accepted,
            "proposed_steps": steps,
            "proposed_routes": routed.get("routes"),
            "rejections": rejections,
            "followed_embedded_instructions": followed,
            "model_used": proposed.get("model_used"),
            "fallback_triggered": fallback,
            "secs": secs,
        }

    def aggregate(self, conn: sqlite3.Connection, request: Dict[str, Any],
                  outcome: Dict[str, Any]) -> Dict[str, Any]:
        """Ask the LLM to write the reply, falling back to a template if it misstates the outcome."""
        facts = {"case_id": outcome.get("case_id"), "status": outcome.get("status"),
                 "reason": outcome.get("reason", ""), "amount_requested": request["amount"],
                 "refunded_amount": outcome.get("refunded_amount", 0.0)}
        t0 = time.perf_counter()
        proposed = self.llm.aggregate_results(request, facts)
        secs = round(time.perf_counter() - t0, 2)
        check = validate_summary(proposed.get("summary"), facts)
        for r in check.rejected:
            log_event(conn, "Orchestrator", r["code"], guardrails.BLOCKED,
                      {**r, "proposed_summary": str(proposed.get("summary"))[:300]}, outcome.get("case_id"))
        return {"summary": check.accepted, "proposed_summary": proposed.get("summary"),
                "rejections": check.rejected, "model_used": proposed.get("model_used"), "secs": secs,
                "fallback_triggered": bool(proposed.get("fallback_triggered")),
                "followed_embedded_instructions": bool(proposed.get("followed_embedded_instructions"))}

    # ------------------------------------------------------------- workflow

    def process_refund_request(self, conn: sqlite3.Connection, customer_id: str, order_id: str,
                               amount: float, reason: str,
                               on_event: Optional[EventListener] = None,
                               attachments: Optional[List[str]] = None) -> Dict[str, Any]:
        """Run one claim. `on_event`, if given, receives live step events (see EventSink).

        `attachments` are drive file ids the beneficiary uploaded for this claim.
        """
        trace: List[Dict[str, str]] = []
        ev = EventSink(on_event)
        later_steps = ("plan", "intake", "records", "rag", "llm", "pay", "summary")
        ev_done: set = set()
        request = {"order_id": order_id, "amount": amount, "reason": reason}
        model = getattr(self.llm, "orchestrator_model", None) or model_label(self.llm)

        def revoked(case_id: Optional[str] = None) -> Dict[str, Any]:
            ev.emit("guard", "bad", "PolicyGuardrail", "Kill switch is on: agent authority revoked, failing closed.",
                    status="REVOKED")
            for s in later_steps:
                if s not in ev_done:
                    ev.emit(s, "skip", "PolicyGuardrail", "Not reached.")
            return self._revoked(trace, case_id)

        def step(agent: str, message: str) -> None:
            trace.append({"agent": agent, "message": message})

        if guardrails.is_kill_switch_active(conn):
            step("Orchestrator", "Kill switch is on; not planning.")
            log_event(conn, "Orchestrator", "AGENT_EXECUTION_BLOCKED", guardrails.BLOCKED,
                      {"reason": "kill switch active", "customer_id": customer_id})
            return revoked()

        # 1. Task decomposition and agent routing (LLM, then validated).
        ev.emit("plan", "running", "Orchestrator", f"Asking {model} to break the request into sub-tasks and route them.",
                model=model)
        orch = self.decompose_and_route(conn, customer_id, request)
        plan, routes = orch["plan"], orch["routes"]
        step("Orchestrator", f"Plan: {', '.join(plan)}")
        step("Orchestrator", "Routes: " + ", ".join(f"{s} -> {a}" for s, a in routes.items()))
        for r in orch["rejections"]:
            step("Orchestrator", f"Rejected from the model's plan: {r['code']} {r.get('step') or r.get('detail', '')}".rstrip())
        n_rej = len(orch["rejections"])
        ev.emit("plan", "bad" if orch["followed_embedded_instructions"] else "warn" if n_rej else "ok", "Orchestrator",
                f"{len(plan)} sub-tasks routed to {len(set(routes.values()))} specialists"
                + (f"; {n_rej} change(s) from the model's proposal rejected." if n_rej else ".")
                + f" Decomposed in {orch['secs']['decompose']:.1f}s, routed in {orch['secs']['route']:.1f}s."
                + (" The model did not answer in time, so the standard plan was used." if orch["fallback_triggered"] else ""),
                plan=plan, routes=routes, rejections=orch["rejections"], secs=orch["secs"],
                fallback_triggered=orch["fallback_triggered"],
                followed_embedded_instructions=orch["followed_embedded_instructions"])
        ev_done.add("plan")

        specialists: Dict[str, Specialist] = {
            "IntakeAgent": IntakeAgent(conn, customer_id),
            "EmailAgent": EmailAgent(conn, customer_id),
            "RecordsAgent": RecordsAgent(conn, customer_id),
            "RefundsAgent": RefundsAgent(conn, customer_id, self.llm, ev),
            "ReportingAgent": ReportingAgent(conn, customer_id),
        }
        ctx: Dict[str, Any] = {}

        def create_case(agent: IntakeAgent) -> None:
            ev.emit("intake", "running", agent.name, f"Opening a case for {order_id}.")
            case = agent.open_case(order_id, amount, reason)
            case["amount_requested"] = amount
            ctx["case"] = case
            step(agent.name, f"Created case {case['case_id']} (assigned to {case.get('assigned_to')}).")
            ctx["file_ids"] = []
            for fid in dict.fromkeys(attachments or []):  # de-duplicated, order kept
                attached = agent.attach(fid, case["case_id"])
                ctx["file_ids"].append(attached["id"])
                step(agent.name, f"Attached {attached['id']} ({attached['name']}) to {case['case_id']}.")

        def send_ack(agent: EmailAgent) -> None:
            case_id = ctx["case"]["case_id"]
            ctx["customer"] = specialists["RecordsAgent"].customer()
            ctx["ack"] = agent.acknowledge(case_id, ctx["customer"], amount)
            step(agent.name, f"Sent acknowledgement to {ctx['customer']['email']}.")
            ev.emit("intake", "ok", "IntakeAgent",
                    f"Created case {case_id} and emailed a receipt to {ctx['customer']['email']}.", case_id=case_id)
            ev_done.add("intake")

        def validate_records(agent: RecordsAgent) -> None:
            ev.emit("records", "running", agent.name, f"Verifying {order_id} and building the eligibility profile.")
            order = agent.order(order_id)
            prior = [h for h in agent.history() if h.get("id") != ctx["case"]["case_id"]]
            # Records RAG: the closest of this beneficiary's past claims, as structured facts only.
            hits = agent.search(f"{order['product_name']} {order['category']} {reason}"[:500])
            related = records_store.history_for_prompt(hits, exclude_case=ctx["case"]["case_id"])
            ctx["order"], ctx["related_claims"] = order, related
            step(agent.name, f"Verified expense {order_id} and built the eligibility profile for {customer_id}: "
                             f"{len(prior)} prior claim(s) on file, {len(related)} related by records search.")
            ev.emit("records", "ok", agent.name,
                    f"Verified {order_id} ({order['product_name']}, ${order['amount']:.2f}); "
                    f"{customer_id} has {len(prior)} prior claim(s) on file, {len(related)} related.",
                    order=order, prior_claims=len(prior), related_claims=related)
            ev_done.add("records")

        def evaluate(agent: RefundsAgent) -> None:
            outcome = agent.evaluate(ctx["case"], ctx["order"], reason, ctx.get("related_claims"), ctx.get("file_ids"))
            outcome["related_claims"] = ctx.get("related_claims", [])
            ctx["outcome"] = outcome
            step(agent.name, f"LLM proposed {outcome['llm_proposal']['action']} "
                             f"${outcome['llm_proposal']['amount']:.2f}; final status {outcome['status']}.")

        def send_decision(agent: EmailAgent) -> None:
            ev.emit("pay", "running", agent.name, "Sending the decision email.")
            o = ctx["outcome"]
            ctx["decision_email"] = agent.notify_decision(ctx["case"]["case_id"], ctx["customer"], o["status"], o["reason"])
            step(agent.name, "Sent decision notification.")

        def record(agent: ReportingAgent) -> None:
            o = ctx["outcome"]
            agent.record(ctx["case"]["case_id"], o["status"], o["reason"])
            step(agent.name, "Recorded decision in the audit trail.")
            st = o["status"]
            ev.emit("pay", "ok" if st == "APPROVED" else "warn" if st == "ESCALATED" else "bad", "EmailAgent",
                    f"Paid ${o['refunded_amount']:.2f}; decision emailed and recorded." if st == "APPROVED"
                    else "Nothing paid until a supervisor decides; beneficiary told it is under review."
                    if st == "ESCALATED" else "Nothing paid; decision emailed and recorded.",
                    status=st, paid=o["refunded_amount"], new_balance=o.get("new_bank_balance"))
            ev_done.add("pay")

        handlers: Dict[str, Callable[[Any], None]] = {
            "intake:create_case": create_case,
            "email:send_acknowledgement": send_ack,
            "records:validate_customer_and_order": validate_records,
            "refunds:evaluate_and_process": evaluate,
            "email:send_decision": send_decision,
            "reporting:record_decision": record,
        }

        try:
            for sid in plan:
                handlers[sid](specialists[routes[sid]])
            case_id = ctx["case"]["case_id"]
            result = {
                "case_id": case_id,
                "trace": trace,
                "customer": ctx["customer"],
                "order": ctx["order"],
                "acknowledgement_email": ctx["ack"],
                "decision_email": ctx["decision_email"],
                **ctx["outcome"],
            }

        except GuardrailViolation as v:
            d = v.decision
            case_id = (ctx.get("case") or {}).get("case_id")
            if d.code == "KILL_SWITCH_ACTIVE":
                if case_id:
                    guardrails.mark_case(conn, case_id, "REVOKED", d.reason)
                return revoked(case_id)
            step("PolicyGuardrail", f"Blocked: {d.reason}")
            ev.emit("guard", "bad", "PolicyGuardrail", f"{d.code}: {d.reason}", status="BLOCKED", guardrail=d.as_dict())
            result = {"case_id": case_id, "status": "BLOCKED", "reason": d.reason, "guardrail": d.as_dict(),
                      "refunded_amount": 0.0, "trace": trace}

        # 3. Response aggregation (LLM, then checked against the real outcome).
        ev.emit("summary", "running", "Orchestrator", f"Asking {model} to write the reply to the beneficiary.")
        agg = self.aggregate(conn, request, result)
        if agg["rejections"]:
            step("Orchestrator", "Rejected the model's reply ("
                 + ", ".join(r["code"] for r in agg["rejections"]) + "); used the template reply.")
        else:
            step("Orchestrator", "Wrote the reply to the beneficiary.")
        ev.emit("summary", "bad" if agg["followed_embedded_instructions"] else "warn" if agg["rejections"] else "ok",
                "Orchestrator",
                "The model's reply misstated the outcome, so the template reply was used." if agg["rejections"]
                else "Reply written.", summary=agg["summary"], rejections=agg["rejections"])
        result["summary"] = agg["summary"]
        result["orchestration"] = {**orch, "aggregation": agg}
        return result

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

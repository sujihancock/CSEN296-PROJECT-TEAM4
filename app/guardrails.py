"""Independent policy guardrail layer (no LLM involved).

Every tool call an agent makes passes through `authorize_tool_call`, which
logs it and can block it. Money only moves through `disburse_refund` (agent
path) or `supervisor_decide` (human path), both of which re-check every limit
against the guardrail's own disbursement ledger.
"""
import math
import sqlite3
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Optional

from app import config
from app.audit import log_event
from app.db import now_ts

ALLOWED = "ALLOWED"
ESCALATED = "ESCALATED"
BLOCKED = "BLOCKED"

PENDING_STATUSES = ("RECEIVED", "UNDER_REVIEW")
AGENT_SETTABLE_STATUSES = ("UNDER_REVIEW", "APPROVED", "DENIED", "ESCALATED")

# Which tools each specialist may call. Read-only at runtime; there is no tool
# that changes it, so an agent cannot grant itself new permissions.
AGENT_PERMISSIONS = MappingProxyType({
    "IntakeAgent": frozenset({"cases.create"}),
    "EmailAgent": frozenset({"email.send"}),
    "RecordsAgent": frozenset({"records.get_customer", "records.get_order", "records.get_case_history"}),
    "RefundsAgent": frozenset({"policy.search", "cases.update_status", "bank.disburse"}),
    "ReportingAgent": frozenset({"reporting.record_decision"}),
})


@dataclass
class Decision:
    verdict: str
    code: str
    reason: str
    meta: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"verdict": self.verdict, "code": self.code, "reason": self.reason, "meta": self.meta}


class GuardrailViolation(Exception):
    def __init__(self, decision: Decision):
        super().__init__(decision.reason)
        self.decision = decision


# --------------------------------------------------------------------------
# Kill switch
# --------------------------------------------------------------------------

def is_kill_switch_active(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM system_settings WHERE key = 'kill_switch_active'").fetchone()
    return bool(row) and row["value"] == "true"


def set_kill_switch(conn: sqlite3.Connection, active: bool, actor: str, reason: str = "") -> Dict[str, Any]:
    conn.execute(
        "INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES ('kill_switch_active', ?, ?)",
        ("true" if active else "false", now_ts()),
    )
    conn.commit()
    log_event(conn, actor, "SET_KILL_SWITCH", "INFO", {"active": active, "reason": reason})
    return {"kill_switch_active": active, "updated_by": actor, "reason": reason}


def _kill_switch_decision() -> Decision:
    return Decision(
        BLOCKED, "KILL_SWITCH_ACTIVE",
        "Agent authority has been revoked by the supervisor kill-switch. Fail-closed enforced.",
    )


# --------------------------------------------------------------------------
# Ledger queries
# --------------------------------------------------------------------------

def weekly_autonomous_total(conn: sqlite3.Connection) -> float:
    since = now_ts(-config.WEEKLY_WINDOW_DAYS)
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM disbursements WHERE autonomous = 1 AND created_at >= ?",
        (since,),
    ).fetchone()
    return float(row["total"])


def refunded_total_for_order(conn: sqlite3.Connection, order_id: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM disbursements WHERE order_id = ?", (order_id,)
    ).fetchone()
    return float(row["total"])


def _get(conn: sqlite3.Connection, table: str, row_id: Optional[str]) -> Optional[sqlite3.Row]:
    if not row_id:
        return None
    return conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()


# --------------------------------------------------------------------------
# Refund evaluation (hard limits)
# --------------------------------------------------------------------------

def evaluate_refund(conn: sqlite3.Connection, case_id: str, amount: float, session_customer_id: str) -> Decision:
    if is_kill_switch_active(conn):
        return _kill_switch_decision()

    if not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount <= 0:
        return Decision(BLOCKED, "INVALID_AMOUNT", f"Refund amount {amount!r} is not a positive number.")

    case = _get(conn, "cases", case_id)
    if not case:
        return Decision(BLOCKED, "CASE_NOT_FOUND", f"Every refund must match an existing case; {case_id} does not exist.")

    if case["customer_id"] != session_customer_id:
        return Decision(
            BLOCKED, "IDENTITY_PRIVILEGE_MISMATCH",
            f"Customer {session_customer_id} does not own case {case_id}. Cross-customer access blocked.",
            {"owner": case["customer_id"], "requester": session_customer_id},
        )

    if case["status"] not in PENDING_STATUSES:
        return Decision(BLOCKED, "CASE_NOT_PENDING", f"Case {case_id} is {case['status']} and cannot be paid by the agent.")

    if abs(amount - float(case["amount_requested"])) > 0.005:
        return Decision(
            BLOCKED, "AMOUNT_MISMATCH",
            f"Payout ${amount:.2f} does not match the amount on case {case_id} (${case['amount_requested']:.2f}).",
            {"case_amount": case["amount_requested"], "payout": amount},
        )

    order = _get(conn, "orders", case["order_id"])
    if not order or order["customer_id"] != case["customer_id"]:
        return Decision(BLOCKED, "IDENTITY_PRIVILEGE_MISMATCH", f"Order {case['order_id']} does not belong to the case owner.")

    already = refunded_total_for_order(conn, order["id"])
    if already + amount > float(order["amount"]) + 0.005:
        return Decision(
            BLOCKED, "AMOUNT_EXCEEDS_ORDER",
            f"Refund ${amount:.2f} plus ${already:.2f} already refunded exceeds the order total ${order['amount']:.2f}.",
            {"order_amount": order["amount"], "already_refunded": already, "requested": amount},
        )

    if amount > config.MAX_AUTONOMOUS_REFUND_PER_TX:
        return Decision(
            ESCALATED, "TX_LIMIT_EXCEEDED",
            f"${amount:.2f} exceeds the single-transaction autonomous limit of "
            f"${config.MAX_AUTONOMOUS_REFUND_PER_TX:.2f}. Escalated to a supervisor.",
            {"limit": config.MAX_AUTONOMOUS_REFUND_PER_TX, "amount": amount},
        )

    weekly = weekly_autonomous_total(conn)
    if weekly + amount > config.MAX_WEEKLY_AUTONOMOUS_REFUNDS:
        return Decision(
            ESCALATED, "WEEKLY_CAP_EXCEEDED",
            f"Weekly autonomous refunds (${weekly:.2f} + ${amount:.2f}) would exceed the "
            f"${config.MAX_WEEKLY_AUTONOMOUS_REFUNDS:.2f} weekly safety cap. Escalated to a supervisor.",
            {"current_total": weekly, "cap": config.MAX_WEEKLY_AUTONOMOUS_REFUNDS, "amount": amount},
        )

    return Decision(ALLOWED, "POLICY_PASSED", "Within autonomous refund limits.", {"weekly_total_before": weekly})


def disburse_refund(conn: sqlite3.Connection, case_id: str, amount: float, session_customer_id: str, agent: str) -> Dict[str, Any]:
    """Agent-initiated payout. Check and payment happen in one write transaction."""
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        decision = evaluate_refund(conn, case_id, amount, session_customer_id)
        if decision.verdict != ALLOWED:
            conn.rollback()
            log_event(conn, "PolicyGuardrail", "REFUND_PAYOUT", decision.verdict,
                      {"agent": agent, **decision.as_dict()}, case_id)
            raise GuardrailViolation(decision)
        case = _get(conn, "cases", case_id)
        new_balance = _pay(conn, case, amount, autonomous=True, authorized_by=agent)
        conn.commit()
    except GuardrailViolation:
        raise
    except Exception:
        conn.rollback()
        raise
    log_event(conn, "PolicyGuardrail", "REFUND_PAYOUT", ALLOWED, {"agent": agent, "amount": amount}, case_id)
    return {"paid": amount, "new_bank_balance": new_balance}


def _pay(conn: sqlite3.Connection, case: sqlite3.Row, amount: float, autonomous: bool, authorized_by: str) -> float:
    conn.execute(
        "INSERT INTO disbursements (case_id, customer_id, order_id, amount, autonomous, authorized_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (case["id"], case["customer_id"], case["order_id"], amount, 1 if autonomous else 0, authorized_by, now_ts()),
    )
    conn.execute("UPDATE customers SET bank_balance = bank_balance + ? WHERE id = ?", (amount, case["customer_id"]))
    row = conn.execute("SELECT bank_balance FROM customers WHERE id = ?", (case["customer_id"],)).fetchone()
    return float(row["bank_balance"])


# --------------------------------------------------------------------------
# Human paths (not agent tools)
# --------------------------------------------------------------------------

def supervisor_decide(conn: sqlite3.Connection, case_id: str, supervisor_id: str, approve: bool, comment: str) -> Dict[str, Any]:
    case = _get(conn, "cases", case_id)
    if not case:
        raise GuardrailViolation(Decision(BLOCKED, "CASE_NOT_FOUND", f"Case {case_id} does not exist."))
    if case["status"] != "ESCALATED":
        raise GuardrailViolation(Decision(
            BLOCKED, "CASE_NOT_ESCALATED", f"Case {case_id} is {case['status']}; only escalated cases need a supervisor decision."))

    amount = float(case["amount_requested"])
    new_balance = None
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if approve:
            order = _get(conn, "orders", case["order_id"])
            already = refunded_total_for_order(conn, case["order_id"])
            if amount <= 0 or already + amount > float(order["amount"]) + 0.005:
                conn.rollback()
                raise GuardrailViolation(Decision(
                    BLOCKED, "AMOUNT_EXCEEDS_ORDER", "Approval would refund more than the order total."))
            new_balance = _pay(conn, case, amount, autonomous=False, authorized_by=supervisor_id)
        status = "APPROVED" if approve else "DENIED"
        reason = f"{'Approved' if approve else 'Denied'} by supervisor {supervisor_id}. Note: {comment}"
        conn.execute(
            "UPDATE cases SET status = ?, decision_reason = ?, decided_by = ?, autonomous = 0, updated_at = ? WHERE id = ?",
            (status, reason, supervisor_id, now_ts(), case_id),
        )
        conn.commit()
    except GuardrailViolation:
        raise
    except Exception:
        conn.rollback()
        raise
    log_event(conn, supervisor_id, f"SUPERVISOR_OVERRIDE_{status}", "OVERRIDDEN",
              {"comment": comment, "amount": amount}, case_id)
    return {"case_id": case_id, "status": status, "reason": reason, "new_bank_balance": new_balance}


def mark_case(conn: sqlite3.Connection, case_id: str, status: str, reason: str) -> None:
    """System-level status change, used when a workflow is cut off (e.g. kill switch)."""
    conn.execute(
        "UPDATE cases SET status = ?, decision_reason = ?, updated_at = ? WHERE id = ?",
        (status, reason, now_ts(), case_id),
    )
    conn.commit()
    log_event(conn, "PolicyGuardrail", f"CASE_{status}", BLOCKED, {"reason": reason}, case_id)


# --------------------------------------------------------------------------
# Tool-call authorization
# --------------------------------------------------------------------------

def _mismatch(what: str, session: str) -> Decision:
    return Decision(BLOCKED, "IDENTITY_PRIVILEGE_MISMATCH",
                    f"Customer {session} is not allowed to access {what}. Cross-customer access blocked.")


def _owns_customer(conn, params, session) -> Optional[Decision]:
    target = params.get("customer_id")
    return None if target == session else _mismatch(f"customer {target}", session)


def _owns_order(conn, params, session) -> Optional[Decision]:
    order = _get(conn, "orders", params.get("order_id"))
    if order and order["customer_id"] != session:
        return _mismatch(f"order {order['id']}", session)
    return None


def _order_exists(conn, params, session) -> Optional[Decision]:
    if not _get(conn, "orders", params.get("order_id")):
        return Decision(BLOCKED, "INVALID_ORDER", f"Order {params.get('order_id')} does not exist.")
    return None


def _owns_case(conn, params, session) -> Optional[Decision]:
    case = _get(conn, "cases", params.get("case_id"))
    if not case:
        return Decision(BLOCKED, "CASE_NOT_FOUND", f"Case {params.get('case_id')} does not exist.")
    if case["customer_id"] != session:
        return _mismatch(f"case {case['id']}", session)
    return None


def _valid_status_change(conn, params, session) -> Optional[Decision]:
    case = _get(conn, "cases", params.get("case_id"))
    target = params.get("status")
    if case["status"] == "ESCALATED":
        return Decision(BLOCKED, "SUPERVISOR_DECISION_REQUIRED",
                        f"Case {case['id']} is waiting for a supervisor; the agent cannot change it.")
    if case["status"] not in PENDING_STATUSES:
        return Decision(BLOCKED, "CASE_FINALIZED", f"Case {case['id']} is already {case['status']}.")
    if target not in AGENT_SETTABLE_STATUSES:
        return Decision(BLOCKED, "INVALID_STATUS", f"Agents cannot set status {target!r}.")
    if target == "APPROVED":
        paid = conn.execute("SELECT 1 FROM disbursements WHERE case_id = ?", (case["id"],)).fetchone()
        if not paid:
            return Decision(BLOCKED, "APPROVAL_WITHOUT_PAYOUT",
                            "A case can only be marked APPROVED after the guardrail has authorized the payout.")
    return None


def _recipient_is_customer(conn, params, session) -> Optional[Decision]:
    customer = _get(conn, "customers", session)
    if not customer or params.get("to_email") != customer["email"]:
        return Decision(BLOCKED, "EMAIL_RECIPIENT_NOT_ALLOWED",
                        "Emails may only be sent to the customer who owns the case.")
    return None


Check = Callable[[sqlite3.Connection, Dict[str, Any], str], Optional[Decision]]

TOOL_CHECKS: Dict[str, List[Check]] = {
    "cases.create": [_owns_customer, _order_exists, _owns_order],
    "cases.update_status": [_owns_case, _valid_status_change],
    "records.get_customer": [_owns_customer],
    "records.get_order": [_owns_order],
    "records.get_case_history": [_owns_customer],
    "policy.search": [],
    "email.send": [_owns_case, _recipient_is_customer],
    "bank.disburse": [_owns_case],  # limits are checked atomically in disburse_refund
    "reporting.record_decision": [_owns_case],
}


def _summarize(params: Dict[str, Any]) -> Dict[str, Any]:
    return {k: (v[:120] + "..." if isinstance(v, str) and len(v) > 120 else v) for k, v in params.items()}


def authorize_tool_call(conn: sqlite3.Connection, agent: str, tool: str, params: Dict[str, Any], session_customer_id: str) -> None:
    """Log the call and raise GuardrailViolation if it is not allowed."""
    decision = _authorize(conn, agent, tool, params, session_customer_id)
    log_event(
        conn, agent, f"TOOL_CALL:{tool}", decision.verdict,
        {"params": _summarize(params), "code": decision.code, "reason": decision.reason},
        params.get("case_id"),
    )
    if decision.verdict != ALLOWED:
        raise GuardrailViolation(decision)


def _authorize(conn, agent, tool, params, session) -> Decision:
    if is_kill_switch_active(conn):
        return _kill_switch_decision()
    if tool not in TOOL_CHECKS:
        return Decision(BLOCKED, "UNKNOWN_TOOL", f"Tool {tool!r} does not exist.")
    if tool not in AGENT_PERMISSIONS.get(agent, frozenset()):
        return Decision(BLOCKED, "PERMISSION_DENIED", f"{agent} is not permitted to call {tool}.")
    for check in TOOL_CHECKS[tool]:
        result = check(conn, params, session)
        if result:
            return result
    return Decision(ALLOWED, "OK", "Tool call permitted.")

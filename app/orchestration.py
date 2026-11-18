"""Deterministic checks on the orchestrator's LLM output (plan, routes, summary).

The Orchestrator asks the LLM to decompose a request into sub-tasks, route each
sub-task to a specialist, and aggregate the specialists' results into a reply.
The LLM is untrusted, the same as for refund decisions: everything it proposes
here is checked against a fixed step catalog before anything runs.

- validate_plan: unknown steps are dropped, steps that are required but missing
  are added back, and the order is forced to the dependency order.
- validate_routes: each step runs on the specialist that owns it, whatever the
  model said. The tool allowlist in guardrails.py would block a wrong agent
  anyway; this check catches it earlier and logs it.
- validate_summary: the reply may not name a status other than the real one,
  or dollar amounts the case does not contain. If it does, a template reply is
  used instead.

Each check returns what was rejected, so the Orchestrator can write it to the
audit log.
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app import config

# Step id -> (owning specialist, what the step does). Insertion order is the
# dependency order: a case must exist before it can be emailed about, and so on.
STEP_CATALOG: Dict[str, Dict[str, str]] = {
    "intake:create_case": {
        "agent": "IntakeAgent",
        "does": "Open a reimbursement case for the beneficiary's expense and assign it.",
    },
    "email:send_acknowledgement": {
        "agent": "EmailAgent",
        "does": "Email the beneficiary that the request was received.",
    },
    "records:validate_customer_and_order": {
        "agent": "RecordsAgent",
        "does": "Check the expense belongs to the beneficiary and build their eligibility profile.",
    },
    "refunds:evaluate_and_process": {
        "agent": "RefundsAgent",
        "does": "Retrieve policies, decide eligibility and amount, and pay if the guardrail allows.",
    },
    "email:send_decision": {
        "agent": "EmailAgent",
        "does": "Email the beneficiary the decision.",
    },
    "reporting:record_decision": {
        "agent": "ReportingAgent",
        "does": "Record the decision and its reason for reporting.",
    },
}

CANONICAL_PLAN: List[str] = list(STEP_CATALOG)
REQUIRED_STEPS = frozenset(CANONICAL_PLAN)
SPECIALISTS = ("IntakeAgent", "EmailAgent", "RecordsAgent", "RefundsAgent", "ReportingAgent")

# Words a reply may use for each final status. A reply that uses a word from
# another status's list contradicts the real outcome.
STATUS_WORDS: Dict[str, tuple] = {
    "APPROVED": ("approved", "paid"),
    "DENIED": ("denied", "not eligible", "rejected"),
    "ESCALATED": ("escalated", "under review", "awaiting a supervisor"),
    "BLOCKED": ("blocked", "could not be processed"),
}
MAX_SUMMARY_CHARS = 600


@dataclass
class Check:
    """Result of validating one piece of LLM output."""
    accepted: Any
    rejected: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.rejected


def _step_id(item: Any) -> Optional[str]:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("step", "")).strip() or None
    return None


def validate_plan(proposed: Any) -> Check:
    """Turn the model's proposed sub-tasks into the plan that will actually run."""
    rejected: List[Dict[str, Any]] = []
    items = proposed if isinstance(proposed, list) else []
    if not isinstance(proposed, list):
        rejected.append({"code": "PLAN_MALFORMED", "detail": "The model did not return a list of steps."})

    seen: List[str] = []
    for item in items:
        sid = _step_id(item)
        if sid not in STEP_CATALOG:
            rejected.append({"code": "PLAN_STEP_UNKNOWN", "step": sid if sid else repr(item)[:120]})
        elif sid in seen:
            rejected.append({"code": "PLAN_STEP_DUPLICATE", "step": sid})
        else:
            seen.append(sid)

    for sid in CANONICAL_PLAN:
        if sid not in seen:
            rejected.append({"code": "PLAN_STEP_MISSING", "step": sid})
    if [s for s in CANONICAL_PLAN if s in seen] != seen:
        rejected.append({"code": "PLAN_REORDERED", "detail": "Steps were put back in dependency order.",
                         "proposed_order": seen})
    return Check(accepted=list(CANONICAL_PLAN), rejected=rejected)


def validate_routes(plan: List[str], proposed: Any) -> Check:
    """Map each step to its owning specialist, recording any route the model got wrong."""
    routes = proposed if isinstance(proposed, dict) else {}
    rejected: List[Dict[str, Any]] = []
    if not isinstance(proposed, dict):
        rejected.append({"code": "ROUTE_MALFORMED", "detail": "The model did not return a step-to-agent map."})
    accepted: Dict[str, str] = {}
    for sid in plan:
        owner = STEP_CATALOG[sid]["agent"]
        chosen = routes.get(sid)
        if chosen != owner:
            rejected.append({"code": "ROUTE_REJECTED", "step": sid, "proposed_agent": chosen, "agent": owner})
        accepted[sid] = owner
    return Check(accepted=accepted, rejected=rejected)


def template_summary(outcome: Dict[str, Any]) -> str:
    """The reply used when the model's summary is rejected or unavailable."""
    case = outcome.get("case_id") or "your request"
    status = outcome.get("status", "")
    if status == "APPROVED":
        return (f"Your reimbursement request {case} was approved and ${outcome.get('refunded_amount', 0.0):.2f} "
                f"was paid to your account on file. A confirmation email has been sent.")
    if status == "ESCALATED":
        return (f"Your reimbursement request {case} is under review by a supervisor. Nothing has been paid yet; "
                f"we will email you when a decision is made.")
    if status == "DENIED":
        return f"Your reimbursement request {case} was denied. {outcome.get('reason', '')}".strip()
    return f"Your reimbursement request {case} could not be processed. {outcome.get('reason', '')}".strip()


def _amounts(text: str) -> List[float]:
    return [float(m.replace(",", "")) for m in re.findall(r"\$\s*([\d,]+(?:\.\d+)?)", text)]


def validate_summary(proposed: Any, outcome: Dict[str, Any]) -> Check:
    """Accept the model's reply only if it agrees with the real outcome."""
    text = proposed.strip() if isinstance(proposed, str) else ""
    rejected: List[Dict[str, Any]] = []
    if not text:
        rejected.append({"code": "SUMMARY_EMPTY"})
    if len(text) > MAX_SUMMARY_CHARS:
        rejected.append({"code": "SUMMARY_TOO_LONG", "length": len(text)})
    if re.search(r"<\s*[a-zA-Z/!]|javascript:|\]\(", text):  # HTML, script URLs, Markdown links (LLM10)
        rejected.append({"code": "SUMMARY_MARKUP"})

    status = outcome.get("status", "")
    # Negated phrases ("nothing has been paid", "$0.00 was paid") don't claim that status.
    lower = re.sub(r"(?:\b(?:not|nothing|never)(?:\s+\w+){0,2}|\$\s*0(?:\.00)?(?:\s+\w+){0,2})\s+(?:paid|approved)\b",
                   " ", text.lower())
    for other, words in STATUS_WORDS.items():
        if other == status:
            continue
        hits = [w for w in words if w in lower]
        if hits:
            rejected.append({"code": "SUMMARY_CONTRADICTS_STATUS", "status": status, "words": hits})

    # Amounts the reply may mention: what was asked, what was paid, the policy caps,
    # and any amount the decision reason itself gives.
    allowed = {round(float(outcome.get("refunded_amount") or 0.0), 2),
               round(float(outcome.get("amount_requested") or 0.0), 2),
               config.MAX_AUTONOMOUS_REFUND_PER_TX, config.MAX_WEEKLY_AUTONOMOUS_REFUNDS,
               *(round(a, 2) for a in _amounts(str(outcome.get("reason") or "")))}
    bad = [a for a in _amounts(text) if round(a, 2) not in allowed]
    if bad:
        rejected.append({"code": "SUMMARY_UNKNOWN_AMOUNT", "amounts": bad})

    if rejected:
        return Check(accepted=template_summary(outcome), rejected=rejected)
    return Check(accepted=text)

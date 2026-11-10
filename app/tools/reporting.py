from typing import Any, Dict

from app.audit import log_event
from app.tools.base import ToolContext, tool

_REPORT_STATUS = {"APPROVED": "ALLOWED", "ESCALATED": "ESCALATED"}


@tool("reporting.record_decision", "Record a refund decision and its reasoning.",
      {"case_id": "string", "decision": "string", "reason": "string"})
def record_decision(ctx: ToolContext, case_id: str, decision: str, reason: str) -> Dict[str, Any]:
    return log_event(
        ctx.conn, ctx.agent, f"REFUND_EVALUATION_{decision}",
        _REPORT_STATUS.get(decision, "BLOCKED"), {"reason": reason}, case_id,
    )

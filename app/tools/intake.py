import uuid
from typing import Any, Dict

from app import config, records_store
from app.db import now_ts
from app.tools.base import ToolContext, tool


@tool("cases.create", "Create and assign a reimbursement case.",
      {"customer_id": "string", "order_id": "string", "amount_requested": "number", "reason": "string"})
def cases_create(ctx: ToolContext, customer_id: str, order_id: str, amount_requested: float, reason: str) -> Dict[str, Any]:
    case_id = f"CASE-{uuid.uuid4().hex[:8].upper()}"
    ts = now_ts()
    ctx.conn.execute(
        "INSERT INTO cases (id, customer_id, order_id, amount_requested, reason, status, assigned_to, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'RECEIVED', ?, ?, ?)",
        (case_id, customer_id, order_id, amount_requested, reason, config.CORE_AGENT_ID, ts, ts),
    )
    ctx.conn.commit()
    records_store.index_case(ctx.conn, case_id)
    return {"case_id": case_id, "status": "RECEIVED", "assigned_to": config.CORE_AGENT_ID}

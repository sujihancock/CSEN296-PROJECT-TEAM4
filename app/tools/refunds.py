from typing import Any, Dict, List

from app import guardrails
from app.db import now_ts
from app.retriever import Retriever
from app.tools.base import ToolContext, tool

_retriever = Retriever()


@tool("policy.search", "Search refund policies (RAG).", {"query": "string"})
def policy_search(ctx: ToolContext, query: str) -> List[Dict[str, Any]]:
    return _retriever.search(ctx.conn, query)


@tool("cases.update_status", "Update a case's status and decision reason.",
      {"case_id": "string", "status": "string", "decision_reason": "string"})
def update_status(ctx: ToolContext, case_id: str, status: str, decision_reason: str) -> Dict[str, Any]:
    ctx.conn.execute(
        "UPDATE cases SET status = ?, decision_reason = ?, decided_by = ?, updated_at = ? WHERE id = ?",
        (status, decision_reason, ctx.agent, now_ts(), case_id),
    )
    ctx.conn.commit()
    return {"case_id": case_id, "status": status}


@tool("bank.disburse", "Pay a refund into the customer's account (mock bank).",
      {"case_id": "string", "amount": "number"})
def bank_disburse(ctx: ToolContext, case_id: str, amount: float) -> Dict[str, Any]:
    return guardrails.disburse_refund(ctx.conn, case_id, amount, ctx.session_customer_id, ctx.agent)

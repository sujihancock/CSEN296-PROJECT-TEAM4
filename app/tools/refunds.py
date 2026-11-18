from typing import Any, Dict, List

from app import guardrails, records_store
from app.db import now_ts
from app.retriever import Retriever
from app.tools.base import ToolContext, tool

_retriever = Retriever()


@tool("policy.search", "Search benefits program policies (RAG).", {"query": "string"})
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
    if status != "UNDER_REVIEW":  # transient within one claim; skip the ~70ms re-embed
        records_store.index_case(ctx.conn, case_id)
    return {"case_id": case_id, "status": status}


@tool("bank.disburse", "Pay a reimbursement into the beneficiary's account (mock payment).",
      {"case_id": "string", "amount": "number"})
def bank_disburse(ctx: ToolContext, case_id: str, amount: float) -> Dict[str, Any]:
    paid = guardrails.disburse_refund(ctx.conn, case_id, amount, ctx.session_customer_id, ctx.agent)
    records_store.index_profile(ctx.conn, ctx.session_customer_id)  # balance changed
    return paid

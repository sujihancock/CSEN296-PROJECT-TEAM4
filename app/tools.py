"""Mock tools the specialist agents use, defined MCP-style (name, description,
input schema, handler). Agents never call handlers directly: `call_tool`
routes every call through the guardrail layer first.
"""
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from app import config, guardrails
from app.audit import log_event
from app.db import now_ts
from app.retriever import Retriever


@dataclass
class ToolContext:
    conn: sqlite3.Connection
    agent: str
    session_customer_id: str


@dataclass
class Tool:
    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: Callable[..., Any]


TOOLS: Dict[str, Tool] = {}
_retriever = Retriever()


def tool(name: str, description: str, properties: Dict[str, str]):
    def register(fn):
        TOOLS[name] = Tool(
            name=name,
            description=description,
            input_schema={
                "type": "object",
                "properties": {k: {"type": t} for k, t in properties.items()},
                "required": list(properties),
            },
            handler=fn,
        )
        return fn
    return register


def list_tools() -> List[Dict[str, Any]]:
    """MCP-style tool listing (what an MCP server's tools/list would return)."""
    return [{"name": t.name, "description": t.description, "inputSchema": t.input_schema} for t in TOOLS.values()]


def call_tool(conn: sqlite3.Connection, agent: str, name: str, params: Dict[str, Any], session_customer_id: str) -> Any:
    guardrails.authorize_tool_call(conn, agent, name, params, session_customer_id)
    return TOOLS[name].handler(ToolContext(conn, agent, session_customer_id), **params)


def insert_email(conn: sqlite3.Connection, case_id: str, to_email: str, subject: str, body: str, email_type: str) -> Dict[str, Any]:
    conn.execute(
        "INSERT INTO emails (case_id, to_email, subject, body, email_type, sent_at) VALUES (?, ?, ?, ?, ?, ?)",
        (case_id, to_email, subject, body, email_type, now_ts()),
    )
    conn.commit()
    return {"sent": True, "to": to_email, "subject": subject}


# --------------------------------------------------------------------------
# Intake
# --------------------------------------------------------------------------

@tool("cases.create", "Create and assign a refund case.",
      {"customer_id": "string", "order_id": "string", "amount_requested": "number", "reason": "string"})
def _cases_create(ctx: ToolContext, customer_id: str, order_id: str, amount_requested: float, reason: str) -> Dict[str, Any]:
    case_id = f"CASE-{uuid.uuid4().hex[:8].upper()}"
    ts = now_ts()
    ctx.conn.execute(
        "INSERT INTO cases (id, customer_id, order_id, amount_requested, reason, status, assigned_to, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'RECEIVED', ?, ?, ?)",
        (case_id, customer_id, order_id, amount_requested, reason, config.CORE_AGENT_ID, ts, ts),
    )
    ctx.conn.commit()
    return {"case_id": case_id, "status": "RECEIVED", "assigned_to": config.CORE_AGENT_ID}


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------

@tool("records.get_customer", "Get a customer's profile.", {"customer_id": "string"})
def _get_customer(ctx: ToolContext, customer_id: str) -> Optional[Dict[str, Any]]:
    row = ctx.conn.execute(
        "SELECT id, name, email, bank_balance FROM customers WHERE id = ?", (customer_id,)
    ).fetchone()
    return dict(row) if row else None


@tool("records.get_order", "Get an order.", {"order_id": "string"})
def _get_order(ctx: ToolContext, order_id: str) -> Optional[Dict[str, Any]]:
    row = ctx.conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return dict(row) if row else None


@tool("records.get_case_history", "List a customer's previous cases.", {"customer_id": "string"})
def _get_case_history(ctx: ToolContext, customer_id: str) -> List[Dict[str, Any]]:
    rows = ctx.conn.execute(
        "SELECT id, order_id, amount_requested, status, created_at FROM cases WHERE customer_id = ? ORDER BY created_at",
        (customer_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Refunds
# --------------------------------------------------------------------------

@tool("policy.search", "Search refund policies (RAG).", {"query": "string"})
def _policy_search(ctx: ToolContext, query: str) -> List[Dict[str, Any]]:
    return _retriever.search(ctx.conn, query)


@tool("cases.update_status", "Update a case's status and decision reason.",
      {"case_id": "string", "status": "string", "decision_reason": "string"})
def _update_status(ctx: ToolContext, case_id: str, status: str, decision_reason: str) -> Dict[str, Any]:
    ctx.conn.execute(
        "UPDATE cases SET status = ?, decision_reason = ?, decided_by = ?, updated_at = ? WHERE id = ?",
        (status, decision_reason, ctx.agent, now_ts(), case_id),
    )
    ctx.conn.commit()
    return {"case_id": case_id, "status": status}


@tool("bank.disburse", "Pay a refund into the customer's account (mock bank).",
      {"case_id": "string", "amount": "number"})
def _bank_disburse(ctx: ToolContext, case_id: str, amount: float) -> Dict[str, Any]:
    return guardrails.disburse_refund(ctx.conn, case_id, amount, ctx.session_customer_id, ctx.agent)


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------

@tool("email.send", "Send an email to a customer (mock inbox).",
      {"case_id": "string", "to_email": "string", "subject": "string", "body": "string", "email_type": "string"})
def _email_send(ctx: ToolContext, case_id: str, to_email: str, subject: str, body: str, email_type: str) -> Dict[str, Any]:
    return insert_email(ctx.conn, case_id, to_email, subject, body, email_type)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

_REPORT_STATUS = {"APPROVED": "ALLOWED", "ESCALATED": "ESCALATED"}


@tool("reporting.record_decision", "Record a refund decision and its reasoning.",
      {"case_id": "string", "decision": "string", "reason": "string"})
def _record_decision(ctx: ToolContext, case_id: str, decision: str, reason: str) -> Dict[str, Any]:
    return log_event(
        ctx.conn, ctx.agent, f"REFUND_EVALUATION_{decision}",
        _REPORT_STATUS.get(decision, "BLOCKED"), {"reason": reason}, case_id,
    )

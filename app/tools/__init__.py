"""MCP-style tools used by the specialist agents.

Tools are organized into specialist modules:
- intake: Case creation and assignment (`cases.create`)
- records: Customer, order, and case history lookups (`records.get_customer`, `records.get_order`, `records.get_case_history`)
- refunds: Policy RAG retrieval, status updates, bank disbursement (`policy.search`, `cases.update_status`, `bank.disburse`)
- email: Customer notification inbox (`email.send`)
- reporting: Tamper-evident audit trail logging (`reporting.record_decision`)

Agents never call handlers directly: `call_tool` routes every call through the guardrail layer first.
"""
from app.tools.base import Tool, ToolContext, TOOLS, tool, list_tools, call_tool
from app.tools.email import insert_email

# Import modules to register their tools in the global TOOLS dict
from app.tools import intake, email, records, refunds, reporting

__all__ = [
    "Tool",
    "ToolContext",
    "TOOLS",
    "tool",
    "list_tools",
    "call_tool",
    "insert_email",
    "intake",
    "email",
    "records",
    "refunds",
    "reporting",
]

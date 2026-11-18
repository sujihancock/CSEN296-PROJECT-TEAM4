from typing import Any, Dict, List, Optional

from app import records_store
from app.tools.base import ToolContext, tool


@tool("records.get_customer", "Get a beneficiary's eligibility profile.", {"customer_id": "string"})
def get_customer(ctx: ToolContext, customer_id: str) -> Optional[Dict[str, Any]]:
    row = ctx.conn.execute(
        "SELECT id, name, email, bank_balance FROM customers WHERE id = ?", (customer_id,)
    ).fetchone()
    return dict(row) if row else None


@tool("records.get_order", "Get a healthcare expense record.", {"order_id": "string"})
def get_order(ctx: ToolContext, order_id: str) -> Optional[Dict[str, Any]]:
    row = ctx.conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return dict(row) if row else None


@tool("records.get_case_history", "List a beneficiary's previous claims.", {"customer_id": "string"})
def get_case_history(ctx: ToolContext, customer_id: str) -> List[Dict[str, Any]]:
    rows = ctx.conn.execute(
        "SELECT id, order_id, amount_requested, status, created_at FROM cases WHERE customer_id = ? ORDER BY created_at",
        (customer_id,),
    ).fetchall()
    return [dict(r) for r in rows]


@tool("records.search_records",
      "Semantic search (RAG) over one beneficiary's profile, expenses and past cases. Returns structured fields only.",
      {"customer_id": "string", "query": "string"})
def search_records(ctx: ToolContext, customer_id: str, query: str) -> List[Dict[str, Any]]:
    return records_store.search(ctx.conn, customer_id, query)

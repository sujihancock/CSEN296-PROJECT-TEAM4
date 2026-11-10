from typing import Any, Dict, List, Optional

from app.tools.base import ToolContext, tool


@tool("records.get_customer", "Get a customer's profile.", {"customer_id": "string"})
def get_customer(ctx: ToolContext, customer_id: str) -> Optional[Dict[str, Any]]:
    row = ctx.conn.execute(
        "SELECT id, name, email, bank_balance FROM customers WHERE id = ?", (customer_id,)
    ).fetchone()
    return dict(row) if row else None


@tool("records.get_order", "Get an order.", {"order_id": "string"})
def get_order(ctx: ToolContext, order_id: str) -> Optional[Dict[str, Any]]:
    row = ctx.conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return dict(row) if row else None


@tool("records.get_case_history", "List a customer's previous cases.", {"customer_id": "string"})
def get_case_history(ctx: ToolContext, customer_id: str) -> List[Dict[str, Any]]:
    rows = ctx.conn.execute(
        "SELECT id, order_id, amount_requested, status, created_at FROM cases WHERE customer_id = ? ORDER BY created_at",
        (customer_id,),
    ).fetchall()
    return [dict(r) for r in rows]

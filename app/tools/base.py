import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


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
    from app import guardrails
    guardrails.authorize_tool_call(conn, agent, name, params, session_customer_id)
    return TOOLS[name].handler(ToolContext(conn, agent, session_customer_id), **params)

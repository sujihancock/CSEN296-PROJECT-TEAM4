"""ChangSupport.AI API (built on the course starter template).

Run: uvicorn app.main:app --reload --port 8000

Mock authentication: send `X-API-Key` (see app/config.py). Identity always
comes from the key, never from the request body.
"""
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional

from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import audit, config, guardrails, redteam, tools
from app.agent import Orchestrator
from app.db import get_connection, init_db
from app.guardrails import GuardrailViolation
from app.retriever import Retriever


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db(seed=True)
    yield


app = FastAPI(title="ChangSupport.AI", lifespan=lifespan)
agent = Orchestrator()
retriever = Retriever()


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------

@dataclass
class Identity:
    role: str
    user_id: str


def db() -> Iterator[Any]:
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def identity(x_api_key: Optional[str] = Header(default=None)) -> Identity:
    if not x_api_key or x_api_key not in config.API_KEYS:
        raise HTTPException(401, "Missing or invalid X-API-Key")
    role, user_id = config.API_KEYS[x_api_key]
    return Identity(role, user_id)


def require(*roles: str):
    def check(who: Identity = Depends(identity)) -> Identity:
        if who.role not in roles:
            raise HTTPException(403, f"Role '{who.role}' cannot access this endpoint")
        return who
    return check


# --------------------------------------------------------------------------
# Template endpoints
# --------------------------------------------------------------------------

class TaskRequest(BaseModel):
    task: str
    data: Dict[str, Any] = Field(default_factory=dict)


STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health(conn=Depends(db)):
    return {"status": "ok", "kill_switch_active": guardrails.is_kill_switch_active(conn)}


@app.post("/tasks")
def submit_task(req: TaskRequest, who: Identity = Depends(require("customer")), conn=Depends(db)):
    """Submit a task to the agent. Supported: refund_request {order_id, amount, reason}."""
    claimed = req.data.get("customer_id")
    if claimed is not None and claimed != who.user_id:
        audit.log_event(conn, who.user_id, "API_IDENTITY_MISMATCH", "BLOCKED", {"claimed_customer_id": claimed})
        raise HTTPException(403, "customer_id does not match the authenticated customer")
    try:
        agent.plan(req.task, req.data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    missing = [k for k in ("order_id", "amount") if k not in req.data]
    if missing:
        raise HTTPException(422, f"Missing fields in data: {missing}")
    try:
        amount = float(req.data["amount"])
    except (TypeError, ValueError):
        raise HTTPException(422, "amount must be a number")
    result = agent.process_refund_request(conn, who.user_id, str(req.data["order_id"]), amount,
                                          str(req.data.get("reason", "")))
    return {"status": "ok", "result": result}


@app.get("/logs")
def get_logs(case_id: Optional[str] = None, _who=Depends(require("supervisor", "admin")), conn=Depends(db)):
    return audit.get_logs(conn, case_id)


@app.get("/logs/verify")
def verify_logs(_who=Depends(require("supervisor", "admin")), conn=Depends(db)):
    return audit.verify_chain(conn)


# --------------------------------------------------------------------------
# Red-team tests (template pattern: /tests/rt-XX). Each attack runs in a
# throwaway sandbox DB; evidence is written to redteam/results/RT-XX/.
# --------------------------------------------------------------------------

@app.get("/tests")
def list_red_team_tests(_who=Depends(require("supervisor", "admin"))):
    return redteam.available_tests()


@app.post("/tests/{rt}")
def run_red_team_test(rt: str, _who=Depends(require("supervisor", "admin")), conn=Depends(db)):
    if rt not in redteam.available_tests():
        raise HTTPException(404, f"Unknown red-team test {rt!r}; available: {redteam.available_tests()}")
    return {"status": "ok", **redteam.run_test(rt, live_conn=conn)}


@app.get("/demo/customers")
def demo_customers(_who: Identity = Depends(identity), conn=Depends(db)):
    """Return customer profiles for the demo dashboard."""
    return _rows(conn, "SELECT id, name, email, bank_account, bank_balance FROM customers ORDER BY id")


# --------------------------------------------------------------------------
# Customer
# --------------------------------------------------------------------------

def _rows(conn, sql: str, *args):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


@app.get("/me/orders")
def my_orders(who: Identity = Depends(require("customer")), conn=Depends(db)):
    return _rows(conn, "SELECT * FROM orders WHERE customer_id = ? ORDER BY id", who.user_id)


@app.get("/me/cases")
def my_cases(who: Identity = Depends(require("customer")), conn=Depends(db)):
    return _rows(conn, "SELECT * FROM cases WHERE customer_id = ? ORDER BY created_at DESC", who.user_id)


@app.get("/me/emails")
def my_emails(who: Identity = Depends(require("customer")), conn=Depends(db)):
    return _rows(conn, "SELECT e.* FROM emails e JOIN customers c ON c.email = e.to_email "
                       "WHERE c.id = ? ORDER BY e.id DESC", who.user_id)


# --------------------------------------------------------------------------
# Supervisor
# --------------------------------------------------------------------------

class DecisionRequest(BaseModel):
    approve: bool
    comment: str = ""


class KillSwitchRequest(BaseModel):
    active: bool
    reason: str = ""


@app.get("/supervisor/cases")
def supervisor_cases(status: str = "ESCALATED", _who=Depends(require("supervisor")), conn=Depends(db)):
    return _rows(conn, "SELECT * FROM cases WHERE status = ? ORDER BY created_at", status)


@app.post("/supervisor/cases/{case_id}/decision")
def supervisor_decision(case_id: str, req: DecisionRequest, who: Identity = Depends(require("supervisor")), conn=Depends(db)):
    try:
        return agent.supervisor_review(conn, case_id, who.user_id, req.approve, req.comment)
    except GuardrailViolation as v:
        raise HTTPException(409, v.decision.as_dict())


@app.post("/supervisor/kill-switch")
def kill_switch(req: KillSwitchRequest, who: Identity = Depends(require("supervisor")), conn=Depends(db)):
    return guardrails.set_kill_switch(conn, req.active, who.user_id, req.reason)


@app.get("/guardrails/status")
def guardrail_status(_who=Depends(require("supervisor", "admin")), conn=Depends(db)):
    return {
        "kill_switch_active": guardrails.is_kill_switch_active(conn),
        "max_autonomous_refund_per_tx": config.MAX_AUTONOMOUS_REFUND_PER_TX,
        "max_weekly_autonomous_refunds": config.MAX_WEEKLY_AUTONOMOUS_REFUNDS,
        "weekly_autonomous_total": guardrails.weekly_autonomous_total(conn),
        "agent_permissions": {k: sorted(v) for k, v in guardrails.AGENT_PERMISSIONS.items()},
    }


@app.get("/tools")
def list_tools(_who=Depends(require("supervisor", "admin"))):
    return tools.list_tools()


# --------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------

class PolicyRequest(BaseModel):
    id: str
    title: str
    content: str
    tags: str = ""


@app.get("/policies")
def list_policies(_who=Depends(require("admin", "supervisor")), conn=Depends(db)):
    return retriever.list_policies(conn)


@app.get("/policies/vector-stats")
def vector_stats(_who=Depends(require("admin", "supervisor")), conn=Depends(db)):
    from app.retriever import get_store_for_conn
    store = get_store_for_conn(conn)
    return {
        "engine": "ChromaDB",
        "collection": store.collection_name,
        "indexed_vectors": store.count(),
    }


@app.post("/policies")
def upload_policy(req: PolicyRequest, who: Identity = Depends(require("admin")), conn=Depends(db)):
    result = retriever.add_policy(conn, req.id, req.title, req.content, req.tags, who.user_id)
    audit.log_event(conn, who.user_id, "POLICY_UPLOADED", "INFO", {"policy_id": req.id, "title": req.title})
    return result


@app.post("/admin/reset")
def reset(_who=Depends(require("admin"))):
    """Reset the demo database to its seed state."""
    init_db(seed=True, drop_existing=True)
    return {"status": "reset"}

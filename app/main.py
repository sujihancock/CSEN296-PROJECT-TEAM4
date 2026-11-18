"""ChangSupport.AI API (built on the course starter template).

Run: uvicorn app.main:app --reload --port 8000

Mock authentication: send `X-API-Key` (see app/config.py). Identity always
comes from the key, never from the request body.
"""
import json
import logging
import queue
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional

from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
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
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
if (STATIC_DIR / "css").exists():
    app.mount("/css", StaticFiles(directory=STATIC_DIR / "css"), name="css")
if (STATIC_DIR / "js").exists():
    app.mount("/js", StaticFiles(directory=STATIC_DIR / "js"), name="js")


# --------------------------------------------------------------------------
# Dashboard pages. Each workspace is its own page at its own URL; the shared
# header (partials/header.html) is stitched in here and the current page's
# nav link is marked. Persona-based page locking would hook in at page().
# --------------------------------------------------------------------------

PAGES = ("overview", "sandbox", "customer", "supervisor", "vector-policies", "audit")
NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}


def _render_page(name: str) -> HTMLResponse:
    header = (STATIC_DIR / "partials" / "header.html").read_text()
    link = f'class="nav-item" href="/{name}"'
    header = header.replace(link, f'class="nav-item active" aria-current="page" href="/{name}"')
    html = (STATIC_DIR / "pages" / f"{name}.html").read_text().replace("<!-- @header -->", header)
    return HTMLResponse(html, headers=NO_CACHE)


@app.get("/", include_in_schema=False)
def ui():
    return RedirectResponse("/overview")


def _page_route(name: str):
    def page():
        return _render_page(name)
    page.__name__ = f"page_{name.replace('-', '_')}"
    return page


for _name in PAGES:
    app.get(f"/{_name}", include_in_schema=False, response_class=HTMLResponse)(_page_route(_name))


@app.get("/health")
def health(conn=Depends(db)):
    return {"status": "ok", "kill_switch_active": guardrails.is_kill_switch_active(conn)}


def _validate_task(req: "TaskRequest", who: Identity, conn) -> float:
    """Shared checks for /tasks and /tasks/stream. Returns the parsed amount."""
    claimed = req.data.get("customer_id")
    if claimed is not None and claimed != who.user_id:
        audit.log_event(conn, who.user_id, "API_IDENTITY_MISMATCH", "BLOCKED", {"claimed_customer_id": claimed})
        raise HTTPException(403, "customer_id does not match the authenticated beneficiary")
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
    return amount


@app.post("/tasks")
def submit_task(req: TaskRequest, who: Identity = Depends(require("customer")), conn=Depends(db)):
    """Submit a task to the agent. Supported: refund_request {order_id, amount, reason}."""
    amount = _validate_task(req, who, conn)
    result = agent.process_refund_request(conn, who.user_id, str(req.data["order_id"]), amount,
                                          str(req.data.get("reason", "")))
    return {"status": "ok", "result": result}


STREAM_KEEPALIVE_SECS = 5.0


@app.post("/tasks/stream")
def submit_task_stream(req: TaskRequest, who: Identity = Depends(require("customer")), conn=Depends(db)):
    """Same as /tasks, but streams live step events as NDJSON (used by the Sandbox).

    Each line is a JSON object: {"type": "step", ...} as each pipeline step runs,
    {"type": "ping"} while waiting on the model, then one {"type": "result"}
    (same payload as /tasks) or {"type": "error"}.
    """
    amount = _validate_task(req, who, conn)  # 403/422 before any streaming starts
    order_id, reason, user_id = str(req.data["order_id"]), str(req.data.get("reason", "")), who.user_id
    events: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
    started = time.perf_counter()

    def run() -> None:
        # The request's connection closes before a streamed body is sent, so the claim gets its own.
        own = get_connection()
        try:
            result = agent.process_refund_request(own, user_id, order_id, amount, reason, on_event=events.put)
            events.put({"type": "result", "result": result})
        except Exception as e:  # noqa: BLE001 - report to the client instead of dropping the stream
            logging.getLogger(__name__).exception("Streamed claim failed")
            events.put({"type": "error", "message": str(e) or type(e).__name__})
        finally:
            own.close()
            events.put(None)

    threading.Thread(target=run, name=f"claim-stream-{user_id}", daemon=True).start()

    def lines() -> Iterator[str]:
        while True:
            try:
                item = events.get(timeout=STREAM_KEEPALIVE_SECS)
            except queue.Empty:
                item = {"type": "ping", "t": round(time.perf_counter() - started, 3)}
            if item is None:
                return
            yield json.dumps(item, default=str) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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
# Mock Email API (External Service Simulation)
# --------------------------------------------------------------------------

class EmailSendRequest(BaseModel):
    case_id: str
    to_email: str
    subject: str
    body: str
    email_type: str = "GENERAL"


@app.post("/api/email/send")
def api_send_email(req: EmailSendRequest, who: Identity = Depends(identity), conn=Depends(db)):
    """Mock Email API: send an email through the mock email service provider."""
    # Tenant check: customers may only send to their own email on file
    if who.role == "customer":
        cust = conn.execute("SELECT email FROM customers WHERE id = ?", (who.user_id,)).fetchone()
        if not cust or req.to_email != cust["email"]:
            raise HTTPException(403, "Beneficiaries can only send emails to their own address on file")

    from app.email_api import email_service
    result = email_service.send_message(conn, req.case_id, req.to_email, req.subject, req.body, req.email_type)
    audit.log_event(conn, who.user_id, "EMAIL_SENT", "INFO", {
        "message_id": result.get("message_id"),
        "to_email": req.to_email,
        "case_id": req.case_id,
        "email_type": req.email_type,
    })
    return result


@app.get("/api/email/outbox")
def api_email_outbox(
    case_id: Optional[str] = None,
    limit: int = 50,
    who: Identity = Depends(identity),
    conn=Depends(db),
):
    """Retrieve emails from the mock email provider's outbox."""
    from app.email_api import email_service
    to_filter = None
    if who.role == "customer":
        cust = conn.execute("SELECT email FROM customers WHERE id = ?", (who.user_id,)).fetchone()
        to_filter = cust["email"] if cust else "none"
    return email_service.get_outbox(conn, limit=limit, to_email=to_filter, case_id=case_id)


@app.get("/api/email/messages/{message_id}")
def api_get_email_message(message_id: str, who: Identity = Depends(identity), conn=Depends(db)):
    """Retrieve a specific email message delivery status and payload."""
    from app.email_api import email_service
    msg = email_service.get_message(conn, message_id)
    if not msg:
        raise HTTPException(404, f"Message '{message_id}' not found")
    if who.role == "customer":
        cust = conn.execute("SELECT email FROM customers WHERE id = ?", (who.user_id,)).fetchone()
        if not cust or msg["to"] != cust["email"]:
            raise HTTPException(403, "Access denied to message")
    return msg


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


@app.get("/llm/status")
def llm_status():
    from app.llm import GeminiLLM, OllamaLLM, get_llm
    active_llm = get_llm()
    is_ollama = isinstance(active_llm, OllamaLLM)
    is_gemini = isinstance(active_llm, GeminiLLM)
    return {
        "configured_provider": config.LLM_PROVIDER,
        "active_class": active_llm.__class__.__name__,
        "ollama_base_url": config.OLLAMA_BASE_URL,
        "ollama_model": config.OLLAMA_MODEL,
        "ollama_reachable": active_llm.is_available() if is_ollama else False,
        "ollama_models": active_llm.list_models() if is_ollama else [],
        "fallback_to_mock_enabled": (
            config.GEMINI_FALLBACK_TO_MOCK if config.LLM_PROVIDER == "gemini" else config.OLLAMA_FALLBACK_TO_MOCK
        ),
        "gemini_model": config.GEMINI_MODEL,
        "gemini_key_configured": bool(config.GEMINI_API_KEY),
        "active_model": (
            active_llm.model if (is_ollama or is_gemini) else "mock"
        ),
    }


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
    from app.email_api import email_service
    email_service.clear_outbox()
    return {"status": "reset"}

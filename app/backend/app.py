import os
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from app.backend.database import init_db, get_connection
from app.backend.config import MAX_AUTONOMOUS_REFUND_PER_TX, MAX_WEEKLY_CUMULATIVE_REFUNDS, BASE_DIR
from app.backend.guardrails.policy_engine import PolicyGuardrailEngine
from app.backend.rag.policy_rag import PolicyRAG
from app.backend.agents.orchestrator import CoreOrchestrator
from app.backend.agents.specialized.email import EmailSpecialist
from app.backend.agents.specialized.reporting import ReportingSpecialist

app = FastAPI(title="ChangSupport.AI API", version="1.0.0")

# Enable CORS for independent frontend running on port 3000
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Startup event: initialize DB if needed
@app.on_event("startup")
def on_startup():
    init_db(seed=True)

# Pydantic Request Models
class RefundSubmissionRequest(BaseModel):
    customer_id: str
    order_id: str
    amount_requested: float
    reason: str

class SupervisorOverrideRequest(BaseModel):
    case_id: str
    supervisor_id: str
    approved: bool
    comment: str

class KillSwitchRequest(BaseModel):
    active: bool
    supervisor_id: str
    reason: Optional[str] = "Supervisor manual action"

class PolicyUploadRequest(BaseModel):
    id: str
    title: str
    content: str
    tags: str

# API Endpoints
@app.get("/api/health")
def health_check():
    return {"status": "online", "application": "ChangSupport.AI"}

@app.post("/api/system/reset")
def reset_db():
    init_db(seed=True)
    return {"status": "SUCCESS", "message": "Database reset to clean initial state."}

@app.get("/api/customers")
def get_customers():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, email, bank_account, bank_balance FROM customers;")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/orders")
def get_orders(customer_id: Optional[str] = None):
    conn = get_connection()
    cur = conn.cursor()
    if customer_id:
        cur.execute("SELECT * FROM orders WHERE customer_id = ? ORDER BY purchase_date DESC;", (customer_id,))
    else:
        cur.execute("SELECT * FROM orders ORDER BY purchase_date DESC;")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/cases")
def get_cases(customer_id: Optional[str] = None):
    conn = get_connection()
    cur = conn.cursor()
    if customer_id:
        cur.execute("SELECT * FROM cases WHERE customer_id = ? ORDER BY created_at DESC;", (customer_id,))
    else:
        cur.execute("SELECT * FROM cases ORDER BY created_at DESC;")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/cases/{case_id}")
def get_case(case_id: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM cases WHERE id = ?;", (case_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Case not found")
    return dict(row)

@app.post("/api/refunds/submit")
def submit_refund(req: RefundSubmissionRequest):
    result = CoreOrchestrator.process_refund_request(
        customer_id=req.customer_id,
        order_id=req.order_id,
        amount_requested=req.amount_requested,
        reason=req.reason
    )
    return result

@app.get("/api/emails")
def get_emails(email: Optional[str] = None):
    conn = get_connection()
    cur = conn.cursor()
    if email:
        cur.execute("SELECT * FROM emails WHERE to_email = ? ORDER BY sent_at DESC;", (email,))
    else:
        cur.execute("SELECT * FROM emails ORDER BY sent_at DESC;")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/policies")
def get_policies():
    return PolicyRAG.list_all_policies()

@app.post("/api/policies")
def upload_policy(req: PolicyUploadRequest):
    res = PolicyRAG.add_or_update_policy(req.id, req.title, req.content, req.tags)
    return res

@app.get("/api/policies/query")
def query_policy(q: str = Query(..., description="Query phrase")):
    return PolicyRAG.query_policy(q, top_k=3)

@app.get("/api/guardrails/status")
def get_guardrail_status():
    conn = get_connection()
    kill_switch = PolicyGuardrailEngine.is_kill_switch_active(conn)
    weekly_total = PolicyGuardrailEngine.get_weekly_autonomous_refunds_total(conn)
    conn.close()
    return {
        "kill_switch_active": kill_switch,
        "max_per_transaction": MAX_AUTONOMOUS_REFUND_PER_TX,
        "max_weekly_cumulative": MAX_WEEKLY_CUMULATIVE_REFUNDS,
        "weekly_cumulative_spent": weekly_total,
        "weekly_remaining_quota": max(0.0, MAX_WEEKLY_CUMULATIVE_REFUNDS - weekly_total)
    }

@app.post("/api/guardrails/kill-switch")
def toggle_kill_switch(req: KillSwitchRequest):
    return PolicyGuardrailEngine.set_kill_switch(
        active=req.active,
        supervisor_id=req.supervisor_id,
        reason=req.reason or "Supervisor action"
    )

@app.get("/api/supervisor/escalated")
def get_escalated_cases():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT c.*, cust.name as customer_name, cust.email as customer_email, o.product_name
        FROM cases c
        JOIN customers cust ON c.customer_id = cust.id
        JOIN orders o ON c.order_id = o.id
        WHERE c.status = 'ESCALATED'
        ORDER BY c.created_at ASC;
    """)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/supervisor/override")
def supervisor_override(req: SupervisorOverrideRequest):
    return CoreOrchestrator.supervisor_override(
        case_id=req.case_id,
        supervisor_id=req.supervisor_id,
        approved=req.approved,
        supervisor_comment=req.comment
    )

@app.get("/api/audit-logs")
def get_audit_logs(limit: int = 50):
    return ReportingSpecialist.get_audit_trail(limit=limit)

# Also mount frontend directory if static hosting is desired
frontend_path = BASE_DIR.parent / "frontend"
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="frontend_static")


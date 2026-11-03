import pytest
from app.backend.database import init_db, get_connection
from app.backend.agents.orchestrator import CoreOrchestrator

@pytest.fixture(autouse=True)
def setup_database():
    init_db(seed=True, drop_existing=True)

def test_full_autonomous_approval_workflow():
    # Customer 1 submits refund of $45 for Order ORD-101 (Damaged in shipping)
    res = CoreOrchestrator.process_refund_request(
        customer_id="CUST-001",
        order_id="ORD-101",
        amount_requested=45.0,
        reason="Item arrived damaged and headphones do not turn on."
    )

    assert res["status"] == "APPROVED"
    assert res["refunded_amount"] == 45.0
    case_id = res["case_id"]

    # Verify DB state
    conn = get_connection()
    cur = conn.cursor()

    # 1. Verify case
    cur.execute("SELECT * FROM cases WHERE id = ?;", (case_id,))
    case = cur.fetchone()
    assert case["status"] == "APPROVED"
    assert case["autonomous"] == 1

    # 2. Verify emails sent (acknowledgement + decision)
    cur.execute("SELECT * FROM emails WHERE case_id = ?;", (case_id,))
    emails = cur.fetchall()
    assert len(emails) == 2
    types = [e["email_type"] for e in emails]
    assert "ACKNOWLEDGEMENT" in types
    assert "DECISION" in types

    # 3. Verify bank balance increased by $45 (1250.0 + 45.0 = 1295.0)
    cur.execute("SELECT bank_balance FROM customers WHERE id = 'CUST-001';")
    customer = cur.fetchone()
    assert customer["bank_balance"] == 1295.0

    # 4. Verify audit trail logged
    cur.execute("SELECT * FROM audit_logs WHERE case_id = ?;", (case_id,))
    logs = cur.fetchall()
    assert len(logs) >= 1
    assert any("REFUND_EVALUATION_APPROVED" in l["action"] for l in logs)

    conn.close()

def test_workflow_escalation_and_supervisor_override():
    # Submit request of $120 (> $100 cap)
    res = CoreOrchestrator.process_refund_request(
        customer_id="CUST-001",
        order_id="ORD-102",
        amount_requested=120.0,
        reason="Defective keyboard switches"
    )

    assert res["status"] == "ESCALATED"
    case_id = res["case_id"]

    # Bank balance should not be changed yet
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT bank_balance FROM customers WHERE id = 'CUST-001';")
    assert cur.fetchone()["bank_balance"] == 1250.0
    conn.close()

    # Now Supervisor reviews and approves the override
    override_res = CoreOrchestrator.supervisor_override(
        case_id=case_id,
        supervisor_id="Supervisor-Bhatti",
        approved=True,
        supervisor_comment="Verified proof of defect with supplier warranty. Approved."
    )

    assert override_res["status"] == "APPROVED"

    # Now bank balance must reflect the override
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT bank_balance FROM customers WHERE id = 'CUST-001';")
    assert cur.fetchone()["bank_balance"] == 1370.0 # 1250 + 120

    cur.execute("SELECT * FROM audit_logs WHERE case_id = ? AND status = 'OVERRIDDEN';", (case_id,))
    assert cur.fetchone() is not None
    conn.close()

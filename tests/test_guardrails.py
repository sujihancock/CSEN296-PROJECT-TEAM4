import pytest
from backend.database import init_db, get_connection
from backend.guardrails.policy_engine import PolicyGuardrailEngine
from backend.agents.orchestrator import CoreOrchestrator

@pytest.fixture(autouse=True)
def setup_database():
    init_db(seed=True, drop_existing=True)

def test_single_transaction_limit_pass():
    conn = get_connection()
    # ORD-101 is $45 owned by CUST-001
    verdict, reason, meta = PolicyGuardrailEngine.evaluate_refund_request("CUST-001", "ORD-101", 45.0, conn)
    conn.close()
    assert verdict == "ALLOWED"
    assert meta["code"] == "POLICY_PASSED"

def test_single_transaction_limit_escalate():
    conn = get_connection()
    # ORD-102 is $120 owned by CUST-001 (> $100 autonomous cap)
    verdict, reason, meta = PolicyGuardrailEngine.evaluate_refund_request("CUST-001", "ORD-102", 120.0, conn)
    conn.close()
    assert verdict == "ESCALATED"
    assert meta["code"] == "TX_LIMIT_EXCEEDED"
    assert "exceeds the single-transaction autonomous limit of $100.00" in reason

def test_cross_tenant_ownership_violation():
    conn = get_connection()
    # ORD-201 belongs to CUST-002. CUST-001 attempts to request refund.
    verdict, reason, meta = PolicyGuardrailEngine.evaluate_refund_request("CUST-001", "ORD-201", 35.0, conn)
    conn.close()
    assert verdict == "BLOCKED"
    assert meta["code"] == "IDENTITY_PRIVILEGE_MISMATCH"

def test_kill_switch_revocation():
    # Activate kill switch
    PolicyGuardrailEngine.set_kill_switch(True, "Supervisor-Admin", "Security drill")

    conn = get_connection()
    verdict, reason, meta = PolicyGuardrailEngine.evaluate_refund_request("CUST-001", "ORD-101", 45.0, conn)
    conn.close()

    assert verdict == "BLOCKED"
    assert meta["code"] == "KILL_SWITCH_ACTIVE"
    assert "Fail-closed enforced" in reason

    # Reset kill switch
    PolicyGuardrailEngine.set_kill_switch(False, "Supervisor-Admin", "Resume operations")

def test_weekly_cumulative_cap_escalation():
    # Submit multiple approved refunds up to $500 cap
    # We will insert cases directly into database to simulate accumulated spend
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO cases (id, customer_id, order_id, amount_requested, reason, status, assigned_to, autonomous)
        VALUES 
            ('C-SIM-1', 'CUST-001', 'ORD-101', 95.0, 'Damaged', 'APPROVED', 'Core', 1),
            ('C-SIM-2', 'CUST-001', 'ORD-101', 95.0, 'Damaged', 'APPROVED', 'Core', 1),
            ('C-SIM-3', 'CUST-001', 'ORD-101', 95.0, 'Damaged', 'APPROVED', 'Core', 1),
            ('C-SIM-4', 'CUST-001', 'ORD-101', 95.0, 'Damaged', 'APPROVED', 'Core', 1),
            ('C-SIM-5', 'CUST-001', 'ORD-101', 95.0, 'Damaged', 'APPROVED', 'Core', 1);
    """) # Total = $475
    conn.commit()

    # Now evaluate a new request of $35. $475 + $35 = $510 > $500!
    verdict, reason, meta = PolicyGuardrailEngine.evaluate_refund_request("CUST-001", "ORD-101", 35.0, conn)
    conn.close()

    assert verdict == "ESCALATED"
    assert meta["code"] == "WEEKLY_CAP_EXCEEDED"
    assert "safety cap" in reason

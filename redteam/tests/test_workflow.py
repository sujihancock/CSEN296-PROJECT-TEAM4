"""End-to-end workflow tests (proposal workflow steps 2-8)."""
from fastapi.testclient import TestClient

from app.audit import get_logs, verify_chain
from app.main import app

from conftest import balance


def test_autonomous_approval(conn, agent):
    res = agent.process_refund_request(conn, "CUST-001", "ORD-101", 45.0,
                                       "Item arrived damaged and the headphones do not turn on.")
    assert res["status"] == "APPROVED"
    assert res["refunded_amount"] == 45.0
    case_id = res["case_id"]

    case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    assert (case["status"], case["autonomous"]) == ("APPROVED", 1)

    types = sorted(r["email_type"] for r in conn.execute("SELECT email_type FROM emails WHERE case_id = ?", (case_id,)))
    assert types == ["ACKNOWLEDGEMENT", "DECISION"]

    assert balance(conn) == 1295.0
    assert any(e["action"] == "REFUND_EVALUATION_APPROVED" for e in get_logs(conn, case_id))
    assert verify_chain(conn)["valid"]


def test_escalation_then_supervisor_approval(conn, agent):
    res = agent.process_refund_request(conn, "CUST-001", "ORD-102", 120.0, "Defective keyboard switches")
    assert res["status"] == "ESCALATED"
    assert balance(conn) == 1250.0

    override = agent.supervisor_review(conn, res["case_id"], "SUP-001", True, "Verified defect.")
    assert override["status"] == "APPROVED"
    assert balance(conn) == 1370.0
    assert any(e["status"] == "OVERRIDDEN" for e in get_logs(conn, res["case_id"]))


def test_supervisor_cannot_approve_non_escalated_case(conn, agent):
    res = agent.process_refund_request(conn, "CUST-001", "ORD-101", 45.0, "Arrived damaged")
    try:
        agent.supervisor_review(conn, res["case_id"], "SUP-001", True, "pay again")
        assert False, "expected GuardrailViolation"
    except Exception as e:
        assert e.decision.code == "CASE_NOT_ESCALATED"
    assert balance(conn) == 1295.0


def test_business_policy_denials(conn, agent):
    expired = agent.process_refund_request(conn, "CUST-001", "ORD-105", 60.0, "Changed my mind")
    assert expired["status"] == "DENIED"
    digital = agent.process_refund_request(conn, "CUST-001", "ORD-104", 30.0, "Did not like the book")
    assert digital["status"] == "DENIED"
    assert balance(conn) == 1250.0


def test_api_end_to_end():
    with TestClient(app) as client:
        cust = {"X-API-Key": "customer-001-key"}
        sup = {"X-API-Key": "supervisor-key"}
        assert client.get("/health").json()["status"] == "ok"

        r = client.post("/tasks", headers=cust, json={"task": "refund_request",
                        "data": {"order_id": "ORD-102", "amount": 120, "reason": "Defective"}})
        assert r.status_code == 200
        case_id = r.json()["result"]["case_id"]
        assert r.json()["result"]["status"] == "ESCALATED"

        escalated = client.get("/supervisor/cases", headers=sup).json()
        assert [c["id"] for c in escalated] == [case_id]

        r = client.post(f"/supervisor/cases/{case_id}/decision", headers=sup, json={"approve": False, "comment": "No proof"})
        assert r.json()["status"] == "DENIED"
        assert client.get("/logs/verify", headers=sup).json()["valid"]


def test_api_role_checks():
    with TestClient(app) as client:
        assert client.post("/tasks", json={"task": "refund_request", "data": {}}).status_code == 401
        cust = {"X-API-Key": "customer-001-key"}
        assert client.post("/supervisor/kill-switch", headers=cust, json={"active": True}).status_code == 403
        assert client.post("/policies", headers=cust, json={"id": "X", "title": "x", "content": "x"}).status_code == 403
        assert client.get("/logs", headers=cust).status_code == 403

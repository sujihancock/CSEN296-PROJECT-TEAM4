"""End-to-end workflow tests (proposal workflow steps 2-8)."""
from fastapi.testclient import TestClient

from app.audit import get_logs, verify_chain
from app.main import app

from conftest import balance


def test_autonomous_approval(conn, agent):
    res = agent.process_refund_request(conn, "BEN-001", "EXP-101", 45.0,
                                       "Urgent care visit for a sprained ankle. Itemized receipt attached.")
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
    res = agent.process_refund_request(conn, "BEN-001", "EXP-102", 120.0, "Physical therapy session prescribed after knee surgery")
    assert res["status"] == "ESCALATED"
    assert balance(conn) == 1250.0

    override = agent.supervisor_review(conn, res["case_id"], "SUP-001", True, "Verified itemized bill and referral.")
    assert override["status"] == "APPROVED"
    assert balance(conn) == 1370.0
    assert any(e["status"] == "OVERRIDDEN" for e in get_logs(conn, res["case_id"]))


def test_supervisor_cannot_approve_non_escalated_case(conn, agent):
    res = agent.process_refund_request(conn, "BEN-001", "EXP-101", 45.0, "Urgent care visit copay")
    try:
        agent.supervisor_review(conn, res["case_id"], "SUP-001", True, "pay again")
        assert False, "expected GuardrailViolation"
    except Exception as e:
        assert e.decision.code == "CASE_NOT_ESCALATED"
    assert balance(conn) == 1295.0


def test_business_policy_denials(conn, agent):
    expired = agent.process_refund_request(conn, "BEN-001", "EXP-105", 60.0, "Forgot to submit my eye exam receipt")
    assert expired["status"] == "DENIED"
    excluded = agent.process_refund_request(conn, "BEN-001", "EXP-104", 30.0, "Please reimburse my teeth whitening")
    assert excluded["status"] == "DENIED"
    assert balance(conn) == 1250.0


def test_emergency_exception_allows_late_submission(conn, agent):
    # POL-002: emergency care stays reimbursable after the 30-day window.
    res = agent.process_refund_request(conn, "BEN-001", "EXP-105", 60.0, "Emergency eye exam after a workplace injury")
    assert res["status"] == "APPROVED"
    assert balance(conn) == 1310.0


def test_beneficiaries_are_profiled_before_decision(conn, agent):
    res = agent.process_refund_request(conn, "BEN-001", "EXP-101", 45.0, "Urgent care visit copay")
    assert any(s["agent"] == "RecordsAgent" and "eligibility profile" in s["message"] for s in res["trace"])


def test_api_end_to_end():
    with TestClient(app) as client:
        cust = {"X-API-Key": "customer-001-key"}
        sup = {"X-API-Key": "supervisor-key"}
        assert client.get("/health").json()["status"] == "ok"

        r = client.post("/tasks", headers=cust, json={"task": "refund_request",
                        "data": {"order_id": "EXP-102", "amount": 120, "reason": "Physical therapy session"}})
        assert r.status_code == 200
        case_id = r.json()["result"]["case_id"]
        assert r.json()["result"]["status"] == "ESCALATED"

        escalated = client.get("/supervisor/cases", headers=sup).json()
        assert [c["id"] for c in escalated] == [case_id]

        r = client.post(f"/supervisor/cases/{case_id}/decision", headers=sup, json={"approve": False, "comment": "No proof"})
        assert r.json()["status"] == "DENIED"
        assert client.get("/logs/verify", headers=sup).json()["valid"]


def test_ui_is_served():
    with TestClient(app) as client:
        r = client.get("/", follow_redirects=False)
        assert r.status_code in (302, 307)
        assert r.headers["location"] == "/overview"

        pages = {
            "overview": "overview.js", "sandbox": "sandbox.js", "customer": "customer.js",
            "supervisor": "supervisor.js", "vector-policies": "policies.js", "audit": "audit.js",
        }
        for page, script in pages.items():
            r = client.get(f"/{page}")
            assert r.status_code == 200, page
            assert "ChangSupport.AI" in r.text
            assert '<nav class="nav-segmented"' in r.text   # shared header was stitched in
            assert f'aria-current="page" href="/{page}"' in r.text
            assert f"/static/js/pages/{script}" in r.text
            assert client.get(f"/static/js/pages/{script}").status_code == 200

        css = client.get("/static/css/styles.css")
        assert css.status_code == 200
        assert "--accent" in css.text

        core = client.get("/static/js/core.js")
        assert core.status_code == 200
        assert "applyPreset" in client.get("/static/js/pages/sandbox.js").text


def test_demo_customers_endpoint():
    with TestClient(app) as client:
        r = client.get("/demo/customers", headers={"X-API-Key": "customer-001-key"})
        assert r.status_code == 200
        customers = r.json()
        assert len(customers) >= 3
        assert any(c["id"] == "BEN-001" for c in customers)


def test_api_role_checks():
    with TestClient(app) as client:
        assert client.post("/tasks", json={"task": "refund_request", "data": {}}).status_code == 401
        cust = {"X-API-Key": "customer-001-key"}
        assert client.post("/supervisor/kill-switch", headers=cust, json={"active": True}).status_code == 403
        assert client.post("/policies", headers=cust, json={"id": "X", "title": "x", "content": "x"}).status_code == 403
        assert client.get("/logs", headers=cust).status_code == 403

"""Unit tests for the independent policy guardrail layer."""
import pytest

from app import guardrails, tools
from app.audit import verify_chain
from app.guardrails import GuardrailViolation

from conftest import add_disbursement


def open_case(conn, order_id, amount, customer="CUST-001"):
    return tools.call_tool(conn, "IntakeAgent", "cases.create",
                           {"customer_id": customer, "order_id": order_id,
                            "amount_requested": amount, "reason": "test"}, customer)["case_id"]


def test_refund_within_limits_is_allowed(conn):
    case_id = open_case(conn, "ORD-101", 45.0)
    d = guardrails.evaluate_refund(conn, case_id, 45.0, "CUST-001")
    assert (d.verdict, d.code) == ("ALLOWED", "POLICY_PASSED")


def test_over_100_is_escalated(conn):
    case_id = open_case(conn, "ORD-102", 120.0)
    d = guardrails.evaluate_refund(conn, case_id, 120.0, "CUST-001")
    assert (d.verdict, d.code) == ("ESCALATED", "TX_LIMIT_EXCEEDED")


def test_weekly_cap_is_escalated(conn):
    add_disbursement(conn, 475.0)
    case_id = open_case(conn, "ORD-101", 35.0)
    d = guardrails.evaluate_refund(conn, case_id, 35.0, "CUST-001")
    assert (d.verdict, d.code) == ("ESCALATED", "WEEKLY_CAP_EXCEEDED")


def test_supervisor_payouts_do_not_count_toward_weekly_cap(conn):
    add_disbursement(conn, 475.0, autonomous=0)
    case_id = open_case(conn, "ORD-101", 35.0)
    assert guardrails.evaluate_refund(conn, case_id, 35.0, "CUST-001").verdict == "ALLOWED"


def test_kill_switch_blocks_everything(conn):
    case_id = open_case(conn, "ORD-101", 45.0)
    guardrails.set_kill_switch(conn, True, "SUP-001", "drill")
    d = guardrails.evaluate_refund(conn, case_id, 45.0, "CUST-001")
    assert (d.verdict, d.code) == ("BLOCKED", "KILL_SWITCH_ACTIVE")
    with pytest.raises(GuardrailViolation) as e:
        tools.call_tool(conn, "RecordsAgent", "records.get_customer", {"customer_id": "CUST-001"}, "CUST-001")
    assert e.value.decision.code == "KILL_SWITCH_ACTIVE"


@pytest.mark.parametrize("amount, code", [
    (-50.0, "INVALID_AMOUNT"),
    (0.0, "INVALID_AMOUNT"),
    (float("nan"), "INVALID_AMOUNT"),
    (40.0, "AMOUNT_MISMATCH"),
])
def test_bad_amounts_blocked(conn, amount, code):
    case_id = open_case(conn, "ORD-101", 45.0)
    assert guardrails.evaluate_refund(conn, case_id, amount, "CUST-001").code == code


def test_refund_needs_existing_case(conn):
    assert guardrails.evaluate_refund(conn, "CASE-NOPE", 10.0, "CUST-001").code == "CASE_NOT_FOUND"


def test_cannot_refund_same_order_twice(conn):
    first = open_case(conn, "ORD-101", 45.0)
    guardrails.disburse_refund(conn, first, 45.0, "CUST-001", "RefundsAgent")
    second = open_case(conn, "ORD-101", 45.0)
    assert guardrails.evaluate_refund(conn, second, 45.0, "CUST-001").code == "AMOUNT_EXCEEDS_ORDER"


def test_agent_cannot_use_tools_outside_its_role(conn):
    case_id = open_case(conn, "ORD-101", 45.0)
    with pytest.raises(GuardrailViolation) as e:
        tools.call_tool(conn, "EmailAgent", "bank.disburse", {"case_id": case_id, "amount": 45.0}, "CUST-001")
    assert e.value.decision.code == "PERMISSION_DENIED"


def test_unknown_tool_blocked(conn):
    with pytest.raises(GuardrailViolation) as e:
        tools.call_tool(conn, "RefundsAgent", "guardrails.set_permissions", {}, "CUST-001")
    assert e.value.decision.code == "UNKNOWN_TOOL"


def test_agent_cannot_approve_without_payout(conn):
    case_id = open_case(conn, "ORD-101", 45.0)
    with pytest.raises(GuardrailViolation) as e:
        tools.call_tool(conn, "RefundsAgent", "cases.update_status",
                        {"case_id": case_id, "status": "APPROVED", "decision_reason": "trust me"}, "CUST-001")
    assert e.value.decision.code == "APPROVAL_WITHOUT_PAYOUT"


def test_agent_cannot_override_escalated_case(conn):
    case_id = open_case(conn, "ORD-102", 120.0)
    tools.call_tool(conn, "RefundsAgent", "cases.update_status",
                    {"case_id": case_id, "status": "ESCALATED", "decision_reason": "over cap"}, "CUST-001")
    with pytest.raises(GuardrailViolation) as e:
        tools.call_tool(conn, "RefundsAgent", "cases.update_status",
                        {"case_id": case_id, "status": "DENIED", "decision_reason": "skip supervisor"}, "CUST-001")
    assert e.value.decision.code == "SUPERVISOR_DECISION_REQUIRED"


def test_email_only_to_case_owner(conn):
    case_id = open_case(conn, "ORD-101", 45.0)
    with pytest.raises(GuardrailViolation) as e:
        tools.call_tool(conn, "EmailAgent", "email.send",
                        {"case_id": case_id, "to_email": "attacker@evil.example", "subject": "x",
                         "body": "customer data", "email_type": "DECISION"}, "CUST-001")
    assert e.value.decision.code == "EMAIL_RECIPIENT_NOT_ALLOWED"


def test_every_tool_call_is_audited_and_chain_is_valid(conn):
    open_case(conn, "ORD-101", 45.0)
    with pytest.raises(GuardrailViolation):
        tools.call_tool(conn, "RecordsAgent", "records.get_customer", {"customer_id": "CUST-002"}, "CUST-001")
    actions = [r["action"] for r in conn.execute("SELECT action FROM audit_logs")]
    assert actions.count("TOOL_CALL:cases.create") == 1
    assert actions.count("TOOL_CALL:records.get_customer") == 1
    assert verify_chain(conn)["valid"]


def test_tampering_with_audit_log_is_detected(conn):
    open_case(conn, "ORD-101", 45.0)
    open_case(conn, "ORD-102", 120.0)
    conn.execute("UPDATE audit_logs SET status = 'ALLOWED', details = '{}' WHERE id = 1")
    conn.commit()
    result = verify_chain(conn)
    assert result == {"valid": False, "entries": 2, "first_bad_id": 1}

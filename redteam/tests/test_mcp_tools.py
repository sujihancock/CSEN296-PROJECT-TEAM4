"""Unit tests for modular MCP tool definitions across the 5 specialists:
Intake, Email, Records, Refunds, Reporting.
"""
import pytest
from app import tools
from app.tools import intake, email, records, refunds, reporting


EXPECTED_TOOLS = {
    "cases.create": intake,
    "email.send": email,
    "records.get_customer": records,
    "records.get_order": records,
    "records.get_case_history": records,
    "policy.search": refunds,
    "cases.update_status": refunds,
    "bank.disburse": refunds,
    "reporting.record_decision": reporting,
}


def test_all_specialist_modules_imported():
    """Verify each specialist tool module is properly structured and loaded."""
    assert intake is not None
    assert email is not None
    assert records is not None
    assert refunds is not None
    assert reporting is not None


def test_mcp_tool_registry_contains_all_tools():
    """Verify all 8 expected tools are registered in the global TOOLS dict."""
    for tool_name in EXPECTED_TOOLS:
        assert tool_name in tools.TOOLS, f"Missing tool {tool_name} in registry"


def test_mcp_tool_schemas_conform_to_mcp_spec():
    """Verify each tool matches the Model Context Protocol tools/list schema format."""
    tool_list = tools.list_tools()
    assert len(tool_list) == len(EXPECTED_TOOLS)

    for t in tool_list:
        assert "name" in t
        assert "description" in t
        assert "inputSchema" in t
        schema = t["inputSchema"]
        assert schema.get("type") == "object"
        assert "properties" in schema
        assert isinstance(schema["properties"], dict)
        assert "required" in schema
        assert isinstance(schema["required"], list)


def test_intake_tool_execution(conn):
    """Test intake specialist cases.create tool."""
    res = tools.call_tool(
        conn, "IntakeAgent", "cases.create",
        {"customer_id": "CUST-001", "order_id": "ORD-101", "amount_requested": 45.0, "reason": "Damaged"},
        "CUST-001",
    )
    assert res["status"] == "RECEIVED"
    assert res["case_id"].startswith("CASE-")

    # Verify persisted in database
    row = conn.execute("SELECT * FROM cases WHERE id = ?", (res["case_id"],)).fetchone()
    assert row is not None
    assert row["amount_requested"] == 45.0


def test_records_tools_execution(conn):
    """Test records specialist tools."""
    cust = tools.call_tool(conn, "RecordsAgent", "records.get_customer", {"customer_id": "CUST-001"}, "CUST-001")
    assert cust is not None
    assert cust["id"] == "CUST-001"
    assert cust["email"] == "alex.smith@example.com"

    order = tools.call_tool(conn, "RecordsAgent", "records.get_order", {"order_id": "ORD-101"}, "CUST-001")
    assert order is not None
    assert order["id"] == "ORD-101"

    history = tools.call_tool(conn, "RecordsAgent", "records.get_case_history", {"customer_id": "CUST-001"}, "CUST-001")
    assert isinstance(history, list)


def test_email_tool_execution(conn):
    """Test email specialist email.send tool."""
    # First create a case to associate with CUST-001
    intake_res = tools.call_tool(
        conn, "IntakeAgent", "cases.create",
        {"customer_id": "CUST-001", "order_id": "ORD-101", "amount_requested": 45.0, "reason": "Test"},
        "CUST-001",
    )
    case_id = intake_res["case_id"]

    email_res = tools.call_tool(
        conn, "EmailAgent", "email.send",
        {"case_id": case_id, "to_email": "alex.smith@example.com", "subject": "Test", "body": "Body", "email_type": "TEST"},
        "CUST-001",
    )
    assert email_res["sent"] is True

    # Verify persisted in emails table
    rows = conn.execute("SELECT * FROM emails WHERE case_id = ?", (case_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["to_email"] == "alex.smith@example.com"


def test_reporting_tool_execution(conn):
    """Test reporting specialist reporting.record_decision tool."""
    intake_res = tools.call_tool(
        conn, "IntakeAgent", "cases.create",
        {"customer_id": "CUST-001", "order_id": "ORD-101", "amount_requested": 45.0, "reason": "Test"},
        "CUST-001",
    )
    case_id = intake_res["case_id"]

    rep_res = tools.call_tool(
        conn, "ReportingAgent", "reporting.record_decision",
        {"case_id": case_id, "decision": "APPROVED", "reason": "Policy met"},
        "CUST-001",
    )
    assert rep_res["action"] == "REFUND_EVALUATION_APPROVED"
    assert rep_res["status"] == "ALLOWED"

    # Verify tamper-evident audit record was logged
    audit_row = conn.execute("SELECT * FROM audit_logs WHERE case_id = ?", (case_id,)).fetchone()
    assert audit_row is not None
    assert audit_row["prev_hash"] is not None

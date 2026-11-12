"""Tests for Mock Email API service, MCP tool, and FastAPI endpoints."""
import pytest
from fastapi.testclient import TestClient

from app.email_api import MockEmailService, email_service
from app.main import app
from app import tools


def test_mock_email_service_send_and_retrieve(conn):
    svc = MockEmailService()
    svc.clear_outbox()

    # Send an email
    msg = svc.send_message(
        conn,
        case_id="CASE-TEST-001",
        to_email="alexandre.lopes@example.com",
        subject="Your claim update",
        body="Claim is being processed.",
        email_type="ACKNOWLEDGEMENT",
    )

    assert msg["sent"] is True
    assert msg["message_id"].startswith("msg_")
    assert msg["status"] == "DELIVERED"
    assert msg["to"] == "alexandre.lopes@example.com"
    assert msg["case_id"] == "CASE-TEST-001"

    # Verify SQLite row created
    row = conn.execute("SELECT * FROM emails WHERE case_id = ?", ("CASE-TEST-001",)).fetchone()
    assert row is not None
    assert row["to_email"] == "alexandre.lopes@example.com"
    assert row["email_type"] == "ACKNOWLEDGEMENT"

    # Verify retrieval via get_outbox
    outbox = svc.get_outbox(conn, case_id="CASE-TEST-001")
    assert len(outbox) == 1
    assert outbox[0]["message_id"] == msg["message_id"]

    # Verify retrieval via get_message
    fetched = svc.get_message(conn, msg["message_id"])
    assert fetched is not None
    assert fetched["subject"] == "Your claim update"


def test_mcp_email_tool_returns_delivery_metadata(conn):
    # Ensure a case exists
    res = tools.call_tool(
        conn,
        "IntakeAgent",
        "cases.create",
        {"customer_id": "CUST-001", "order_id": "ORD-101", "amount_requested": 45.0, "reason": "Damaged"},
        "CUST-001",
    )
    case_id = res["case_id"]

    # Call email.send MCP tool
    email_res = tools.call_tool(
        conn,
        "EmailAgent",
        "email.send",
        {
            "case_id": case_id,
            "to_email": "alexandre.lopes@example.com",
            "subject": "Case Received",
            "body": "We have received your claim.",
            "email_type": "ACKNOWLEDGEMENT",
        },
        "CUST-001",
    )

    assert email_res["sent"] is True
    assert "message_id" in email_res
    assert email_res["status"] == "DELIVERED"
    assert email_res["to"] == "alexandre.lopes@example.com"


def test_api_send_email_endpoints():
    with TestClient(app) as client:
        cust_headers = {"X-API-Key": "customer-001-key"}
        sup_headers = {"X-API-Key": "supervisor-key"}

        # 1. Customer sending to own email succeeds
        payload = {
            "case_id": "CASE-999",
            "to_email": "alexandre.lopes@example.com",
            "subject": "Direct API Test",
            "body": "Testing /api/email/send",
            "email_type": "TEST",
        }
        res = client.post("/api/email/send", headers=cust_headers, json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["sent"] is True
        assert "message_id" in data
        msg_id = data["message_id"]

        # 2. Customer sending to unauthorized email is blocked (403)
        spoofed = {
            "case_id": "CASE-999",
            "to_email": "attacker@evil.example",
            "subject": "Exfiltration",
            "body": "Secret data",
            "email_type": "TEST",
        }
        res_blocked = client.post("/api/email/send", headers=cust_headers, json=spoofed)
        assert res_blocked.status_code == 403

        # 3. Retrieve outbox as customer
        res_outbox = client.get("/api/email/outbox", headers=cust_headers)
        assert res_outbox.status_code == 200
        outbox_items = res_outbox.json()
        assert any(item["to"] == "alexandre.lopes@example.com" for item in outbox_items)
        assert all(item["to"] == "alexandre.lopes@example.com" for item in outbox_items)

        # 4. Fetch message by ID
        res_msg = client.get(f"/api/email/messages/{msg_id}", headers=cust_headers)
        assert res_msg.status_code == 200
        assert res_msg.json()["subject"] == "Direct API Test"

        # 5. Fetch non-existent message returns 404
        res_404 = client.get("/api/email/messages/msg_99999_nonexistent", headers=cust_headers)
        assert res_404.status_code == 404

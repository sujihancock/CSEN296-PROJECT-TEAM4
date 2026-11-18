"""Live step events (Orchestrator on_event) and the sandbox's /tasks/stream endpoint."""
import json

from fastapi.testclient import TestClient

from app import guardrails
from app.main import app


def run(conn, agent, order_id, amount, reason, customer="BEN-001"):
    events = []
    res = agent.process_refund_request(conn, customer, order_id, amount, reason, on_event=events.append)
    return res, events


def final_states(events):
    """Last state each step reached, in the order steps first appeared."""
    out = {}
    for e in events:
        out[e["step"]] = e["state"]
    return out


def test_approved_claim_emits_every_step_in_order(conn, agent):
    res, events = run(conn, agent, "EXP-101", 45.0, "Urgent care visit copay")
    assert res["status"] == "APPROVED"
    assert list(final_states(events)) == ["plan", "intake", "records", "rag", "llm", "guard", "pay", "summary"]
    assert all(s == "ok" for s in final_states(events).values())
    # Every step announces itself before it finishes, and time only moves forward.
    for step in ("plan", "intake", "records", "rag", "llm", "guard", "pay", "summary"):
        states = [e["state"] for e in events if e["step"] == step]
        assert states[0] == "running" and states[-1] == "ok"
    ts = [e["t"] for e in events]
    assert ts == sorted(ts)
    llm_done = [e for e in events if e["step"] == "llm" and e["state"] != "running"][0]
    assert llm_done["data"]["proposal"]["action"] == "APPROVE"
    assert "secs" in llm_done["data"]


def test_escalated_claim_marks_guard_and_pay_as_held(conn, agent):
    res, events = run(conn, agent, "EXP-102", 120.0, "Physical therapy session")
    assert res["status"] == "ESCALATED"
    states = final_states(events)
    assert states["guard"] == "warn" and states["pay"] == "warn"
    guard = [e for e in events if e["step"] == "guard"][-1]
    assert guard["data"]["guardrail"]["verdict"] == "ESCALATED"


def test_kill_switch_fails_closed_without_calling_the_model(conn, agent):
    guardrails.set_kill_switch(conn, True, "SUP-001", "test")
    res, events = run(conn, agent, "EXP-101", 45.0, "Urgent care visit copay")
    assert res["status"] == "REVOKED"
    states = final_states(events)
    assert states["guard"] == "bad"
    assert all(states[s] == "skip" for s in ("plan", "intake", "records", "rag", "llm", "pay", "summary"))
    assert not any(e["step"] == "llm" and e["state"] == "running" for e in events)


def test_broken_listener_does_not_change_the_outcome(conn, agent):
    def boom(_event):
        raise RuntimeError("listener exploded")
    res = agent.process_refund_request(conn, "BEN-001", "EXP-101", 45.0, "Urgent care visit copay", on_event=boom)
    assert res["status"] == "APPROVED"


def _stream(client, key, data):
    return client.post("/tasks/stream", headers={"X-API-Key": key},
                       json={"task": "refund_request", "data": data})


def test_stream_endpoint_returns_events_then_the_tasks_result():
    with TestClient(app) as client:
        r = _stream(client, "customer-001-key", {"order_id": "EXP-101", "amount": 45, "reason": "Urgent care visit copay"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/x-ndjson")
        lines = [json.loads(line) for line in r.text.splitlines() if line.strip()]
        assert lines[-1]["type"] == "result"
        assert lines[-1]["result"]["status"] == "APPROVED"
        steps = [m for m in lines if m["type"] == "step"]
        assert {m["step"] for m in steps} == {"plan", "intake", "records", "rag", "llm", "guard", "pay", "summary"}


def test_stream_endpoint_keeps_the_same_access_checks():
    with TestClient(app) as client:
        spoof = _stream(client, "customer-001-key",
                        {"order_id": "EXP-201", "amount": 35, "reason": "x", "customer_id": "BEN-002"})
        assert spoof.status_code == 403
        assert _stream(client, "supervisor-key", {"order_id": "EXP-101", "amount": 45, "reason": "x"}).status_code == 403
        assert _stream(client, "customer-001-key", {"order_id": "EXP-101"}).status_code == 422

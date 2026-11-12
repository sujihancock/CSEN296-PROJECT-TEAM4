"""Tests for the /tests/rt-XX red-team runner.

Guardrail-category attacks must always be defended. llm_policy attacks are
known weak spots (findings); they are reported, not asserted, so the Blue
Team's fixes show up as a change in the evidence rather than a test failure.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app import config, redteam
from app.main import app

ADMIN = {"X-API-Key": "admin-key"}


@pytest.fixture(autouse=True)
def results_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(redteam, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(config, "ROOT_DIR", tmp_path)


@pytest.mark.parametrize("rt", redteam.available_tests())
def test_all_guardrail_attacks_defended(rt):
    result = redteam.run_test(rt)
    assert result["summary"]["guardrail_failures"] == [], result["summary"]


def test_all_twenty_five_attack_files_present():
    assert redteam.available_tests() == [f"rt-{i:02d}" for i in range(1, 26)]


def test_endpoint_writes_evidence_and_logs_run(tmp_path):
    with TestClient(app) as client:
        r = client.post("/tests/rt-21", headers=ADMIN)
        assert r.status_code == 200
        body = r.json()
        assert body["rt"] == "RT-21"
        evidence = json.loads((tmp_path / body["evidence_path"]).read_text())
        assert evidence["summary"]["attacks"] == 3
        assert all(a["audit_chain_valid"] for a in evidence["results"])

        logs = client.get("/logs", headers=ADMIN).json()
        assert any(e["action"] == "RT-21_RUN" for e in logs)


def test_sandbox_does_not_touch_live_data():
    with TestClient(app) as client:
        before = client.get("/me/orders", headers={"X-API-Key": "customer-001-key"}).json()
        client.post("/tests/rt-04", headers=ADMIN)
        assert client.get("/supervisor/cases", headers={"X-API-Key": "supervisor-key"}).json() == []
        assert client.get("/me/orders", headers={"X-API-Key": "customer-001-key"}).json() == before


def test_endpoint_requires_role_and_known_test():
    with TestClient(app) as client:
        assert client.post("/tests/rt-01").status_code == 401
        assert client.post("/tests/rt-01", headers={"X-API-Key": "customer-001-key"}).status_code == 403
        assert client.post("/tests/rt-99", headers=ADMIN).status_code == 404
        assert client.post("/tests/..%2Fsecrets", headers=ADMIN).status_code == 404

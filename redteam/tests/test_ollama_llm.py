"""Unit tests for the Ollama Qwen integration and LLM factory."""
import json
import pytest
from unittest.mock import MagicMock, patch

from app import config
from app.llm import BaseLLM, MockLLM, OllamaLLM, get_llm


@pytest.fixture
def sample_order():
    return {
        "id": "ORD-101",
        "customer_id": "CUST-001",
        "product_name": "Premium Wireless Headphones",
        "category": "electronics",
        "amount": 45.0,
        "purchase_date": "2026-10-25 12:00:00",
    }


@pytest.fixture
def sample_policies():
    return [
        {
            "id": "POL-001",
            "title": "Standard 30-Day Return Policy",
            "content": "Physical electronics may be returned within 30 days of delivery.",
        }
    ]


def test_get_llm_factory():
    """Verify get_llm selects the right provider class."""
    default_llm = get_llm()
    assert isinstance(default_llm, MockLLM)

    ollama_llm = get_llm("ollama")
    assert isinstance(ollama_llm, OllamaLLM)
    assert ollama_llm.model == config.OLLAMA_MODEL


def test_ollama_build_prompt(sample_order, sample_policies):
    """Verify prompt formatting for Qwen."""
    client = OllamaLLM(model="qwen3.5:2b")
    messages = client.build_prompt(
        reason="Item arrived defective",
        order=sample_order,
        amount_requested=45.0,
        policies=sample_policies,
        days_since_purchase=15,
        return_window_days=30,
    )

    assert len(messages) == 2
    system_msg = messages[0]["content"]
    user_msg = messages[1]["content"]

    assert "ChangSupport.AI" in system_msg
    assert "JSON object" in system_msg
    assert "ORD-101" in user_msg
    assert "Premium Wireless Headphones" in user_msg
    assert "POL-001" in user_msg
    assert "$45.00" in user_msg


def test_ollama_propose_refund_decision_success(sample_order, sample_policies):
    """Verify successful parsing of Ollama's Qwen response."""
    client = OllamaLLM(model="qwen3.5:2b")

    mock_ollama_response = {
        "message": {
            "content": json.dumps({
                "action": "APPROVE",
                "amount": 45.0,
                "rationale": "Defective electronics covered under POL-001 within 30 days.",
                "policies_considered": ["POL-001"],
            })
        }
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_ollama_response

    with patch("httpx.post", return_value=mock_resp):
        decision = client.propose_refund_decision(
            reason="Item arrived defective",
            order=sample_order,
            amount_requested=45.0,
            policies=sample_policies,
            days_since_purchase=15,
            return_window_days=30,
        )

    assert decision["action"] == "APPROVE"
    assert decision["amount"] == 45.0
    assert "POL-001" in decision["rationale"]
    assert decision["model_used"] == "qwen3.5:2b"


def test_ollama_handles_markdown_wrapped_json(sample_order, sample_policies):
    """Verify stripping of markdown fences (```json ... ```)."""
    client = OllamaLLM(model="qwen3.5:2b")

    json_text = json.dumps({
        "action": "DENY",
        "amount": 0.0,
        "rationale": "Outside return window.",
        "policies_considered": ["POL-001"],
    })

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "message": {"content": f"```json\n{json_text}\n```"}
    }

    with patch("httpx.post", return_value=mock_resp):
        decision = client.propose_refund_decision(
            reason="Changed mind",
            order=sample_order,
            amount_requested=45.0,
            policies=sample_policies,
            days_since_purchase=45,
            return_window_days=30,
        )

    assert decision["action"] == "DENY"
    assert decision["amount"] == 0.0
    assert "Outside return window" in decision["rationale"]


def test_ollama_fallback_to_mock_on_connection_error(sample_order, sample_policies, monkeypatch):
    """Verify graceful fallback to MockLLM when Ollama is unreachable."""
    monkeypatch.setattr(config, "OLLAMA_FALLBACK_TO_MOCK", True)
    client = OllamaLLM(model="qwen3.5:2b")

    with patch("httpx.post", side_effect=Exception("Connection refused")):
        decision = client.propose_refund_decision(
            reason="damaged item",
            order=sample_order,
            amount_requested=45.0,
            policies=sample_policies,
            days_since_purchase=15,
            return_window_days=30,
        )

    assert decision["fallback_triggered"] is True
    assert "Connection refused" in decision["fallback_reason"]
    # MockLLM approves damaged items within return window
    assert decision["action"] == "APPROVE"
    assert decision["amount"] == 45.0


def test_llm_status_endpoint():
    """Verify /llm/status endpoint returns valid diagnostic data."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.get("/llm/status")
    assert resp.status_code == 200
    data = resp.json()

    assert "configured_provider" in data
    assert "active_class" in data
    assert "ollama_model" in data
    assert "fallback_to_mock_enabled" in data
    assert data["ollama_model"] == "qwen3.5:2b"

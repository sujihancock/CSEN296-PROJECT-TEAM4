"""Unit tests for the Google AI Studio (Gemma/Gemini) integration. No network calls are made."""
import json
from unittest.mock import MagicMock, patch

import pytest

from app import config
from app.llm import GeminiLLM, MockLLM, get_llm


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
    return [{"id": "POL-001", "title": "Standard 30-Day Return Policy",
             "content": "Physical electronics may be returned within 30 days of delivery."}]


def _gemini_response(text, thought=None):
    parts = ([{"text": thought, "thought": True}] if thought else []) + [{"text": text}]
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}]}
    return resp


def _decide(client, order, policies, **kw):
    args = dict(reason="Item arrived defective", order=order, amount_requested=45.0,
                policies=policies, days_since_purchase=15, return_window_days=30)
    args.update(kw)
    return client.propose_refund_decision(**args)


def test_factory_uses_mock_without_key(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    assert isinstance(get_llm("gemini"), MockLLM)


def test_factory_returns_gemini_with_key(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    llm = get_llm("gemini")
    assert isinstance(llm, GeminiLLM)
    assert llm.model == config.GEMINI_MODEL


def test_request_shape_and_key_header(sample_order, sample_policies):
    client = GeminiLLM(api_key="test-key", model="gemini-test")
    answer = json.dumps({"action": "APPROVE", "amount": 45.0,
                         "rationale": "Defective, within window (POL-001).", "policies_considered": ["POL-001"]})
    with patch("httpx.post", return_value=_gemini_response(answer, thought="thinking...")) as post:
        decision = _decide(client, sample_order, sample_policies)

    url = post.call_args.args[0]
    kwargs = post.call_args.kwargs
    assert url.endswith("/models/gemini-test:generateContent")
    assert "test-key" not in url  # key goes in a header, not the URL
    assert kwargs["headers"]["x-goog-api-key"] == "test-key"
    body = kwargs["json"]
    assert "ChangSupport.AI" in body["systemInstruction"]["parts"][0]["text"]
    assert "ORD-101" in body["contents"][0]["parts"][0]["text"]
    assert body["generationConfig"]["responseMimeType"] == "application/json"  # Gemini model: JSON mode on

    assert decision["action"] == "APPROVE"
    assert decision["amount"] == 45.0
    assert decision["model_used"] == "gemini-test"


def test_falls_back_to_mock_on_error(sample_order, sample_policies, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_FALLBACK_TO_MOCK", True)
    client = GeminiLLM(api_key="test-key")
    with patch("httpx.post", side_effect=Exception("403 API key not valid")):
        decision = _decide(client, sample_order, sample_policies, reason="damaged item")
    assert decision["fallback_triggered"] is True
    assert "403" in decision["fallback_reason"]
    assert decision["action"] == "APPROVE"


def test_raises_when_fallback_disabled(sample_order, sample_policies, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_FALLBACK_TO_MOCK", False)
    client = GeminiLLM(api_key="")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        _decide(client, sample_order, sample_policies)


def test_blocked_prompt_is_an_error(sample_order, sample_policies, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_FALLBACK_TO_MOCK", True)
    client = GeminiLLM(api_key="test-key")
    resp = MagicMock()
    resp.json.return_value = {"promptFeedback": {"blockReason": "SAFETY"}}
    with patch("httpx.post", return_value=resp):
        decision = _decide(client, sample_order, sample_policies)
    assert decision["fallback_triggered"] is True
    assert "SAFETY" in decision["fallback_reason"]


def test_default_model_is_gemma():
    assert config.GEMINI_MODEL.startswith("gemma-4")


def test_gemma_skips_json_mode_and_parses_prose_wrapped_json(sample_order, sample_policies):
    client = GeminiLLM(api_key="test-key", model="gemma-4-31b-it")
    answer = 'Here is the decision:\n{"action": "DENY", "amount": 0, "rationale": "Outside window (POL-001).", "policies_considered": ["POL-001"]}'
    with patch("httpx.post", return_value=_gemini_response(answer)) as post:
        decision = _decide(client, sample_order, sample_policies, days_since_purchase=45)

    body = post.call_args.kwargs["json"]
    assert "responseMimeType" not in body["generationConfig"]
    assert post.call_args.args[0].endswith("/models/gemma-4-31b-it:generateContent")
    assert decision["action"] == "DENY"
    assert decision["amount"] == 0.0
    assert decision["model_used"] == "gemma-4-31b-it"


def test_retries_when_model_is_busy(sample_order, sample_policies, monkeypatch):
    monkeypatch.setattr("app.llm.time.sleep", lambda s: None)
    client = GeminiLLM(api_key="test-key", model="gemma-4-31b-it")
    client.max_retries = 3
    busy = MagicMock(status_code=503)
    answer = json.dumps({"action": "APPROVE", "amount": 45.0, "rationale": "ok", "policies_considered": ["POL-001"]})
    with patch("httpx.post", side_effect=[busy, busy, _gemini_response(answer)]) as post:
        decision = _decide(client, sample_order, sample_policies)
    assert post.call_count == 3
    assert decision["action"] == "APPROVE"
    assert "fallback_triggered" not in decision


def test_thinking_level_is_sent_when_set(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_THINKING_LEVEL", "minimal")
    client = GeminiLLM(api_key="test-key", model="gemma-4-31b-it")
    body = client.build_request([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "minimal"}
    monkeypatch.setattr(config, "GEMINI_THINKING_LEVEL", "")
    assert "thinkingConfig" not in GeminiLLM(api_key="k").build_request([{"role": "user", "content": "u"}])["generationConfig"]

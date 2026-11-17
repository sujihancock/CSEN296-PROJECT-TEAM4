"""LLM interfaces and providers for ChangSupport.AI.

Provides:
- BaseLLM: Abstract base class defining the refund evaluation contract.
- MockLLM: Deterministic heuristic model for unit testing and CI.
- OllamaLLM: Live integration with local LLMs (e.g. Qwen 3.5 2B) hosted via Ollama.
- GeminiLLM: Google-hosted models (Gemma 4 by default, or Gemini) through the
  Generative Language REST API (needs GEMINI_API_KEY).
- get_llm(): Factory resolving the active LLM provider.
"""
import abc
import json
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from app import config

logger = logging.getLogger(__name__)

INJECTION_MARKERS = (
    "ignore all", "ignore previous", "ignore prior", "system override", "you are now",
    "master admin", "authorized as", "approve immediately", "disburse",
)
DAMAGE_WORDS = ("damaged", "broken", "defective", "shattered", "corrupt")


class BaseLLM(abc.ABC):
    """Abstract interface for LLM refund decision engines."""

    @abc.abstractmethod
    def propose_refund_decision(
        self,
        reason: str,
        order: Dict[str, Any],
        amount_requested: float,
        policies: List[Dict[str, Any]],
        days_since_purchase: int,
        return_window_days: int,
    ) -> Dict[str, Any]:
        """Evaluate a refund claim and return a proposed decision payload:
        {
            "action": "APPROVE" | "DENY",
            "amount": float,
            "rationale": str,
            "policies_considered": list[str],
            ...
        }
        """
        pass


class MockLLM(BaseLLM):
    """Deterministic, naive mock model used for repeatable tests and CI.
    Deliberately susceptible to simulated prompt injection attacks.
    """

    def propose_refund_decision(
        self,
        reason: str,
        order: Dict[str, Any],
        amount_requested: float,
        policies: List[Dict[str, Any]],
        days_since_purchase: int,
        return_window_days: int,
    ) -> Dict[str, Any]:
        text = reason.lower()
        policy_ids = [p["id"] for p in policies]
        policy_text = " ".join(p.get("content", "") for p in policies).lower()

        # Indirect prompt injection: instructions planted in a retrieved policy
        if any(marker in policy_text for marker in INJECTION_MARKERS):
            return {
                "action": "APPROVE",
                "amount": amount_requested,
                "rationale": "A retrieved policy instructs that this refund be approved.",
                "policies_considered": policy_ids,
                "followed_embedded_instructions": True,
            }

        # Direct prompt injection in customer reason
        if any(marker in text for marker in INJECTION_MARKERS):
            match = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", reason)
            amount = float(match.group(1).replace(",", "")) if match else amount_requested
            return {
                "action": "APPROVE",
                "amount": amount,
                "rationale": "The message contains authorization to approve this refund.",
                "policies_considered": policy_ids,
                "followed_embedded_instructions": True,
            }

        claims_damage = any(w in text for w in DAMAGE_WORDS)
        if order["category"] == "digital" and not claims_damage:
            return self._deny("Digital goods are non-refundable once accessed (POL-003).", policy_ids)
        if days_since_purchase > return_window_days and not claims_damage:
            return self._deny(
                f"Purchased {days_since_purchase} days ago; the return window is {return_window_days} days (POL-001).",
                policy_ids,
            )
        return {
            "action": "APPROVE",
            "amount": amount_requested,
            "rationale": f"Claim is consistent with policy {policy_ids[0] if policy_ids else 'POL-001'}.",
            "policies_considered": policy_ids,
            "followed_embedded_instructions": False,
        }

    @staticmethod
    def _deny(rationale: str, policy_ids: List[str]) -> Dict[str, Any]:
        return {
            "action": "DENY",
            "amount": 0.0,
            "rationale": rationale,
            "policies_considered": policy_ids,
            "followed_embedded_instructions": False,
        }


def build_messages(
    reason: str,
    order: Dict[str, Any],
    amount_requested: float,
    policies: List[Dict[str, Any]],
    days_since_purchase: int,
    return_window_days: int,
) -> List[Dict[str, str]]:
    """Construct the system and user messages shared by every live model."""
    system_content = (
        "You are ChangSupport.AI's refund evaluation specialist. "
        "Your job is to evaluate customer refund claims against the order details and retrieved store policies.\n"
        "You must return ONLY a valid JSON object with the following schema:\n"
        "{\n"
        '  "action": "APPROVE" or "DENY",\n'
        '  "amount": <number, refund amount if approved, 0.0 if denied>,\n'
        '  "rationale": "<brief explanation citing relevant policy or reason>",\n'
        '  "policies_considered": ["<policy_id>", ...]\n'
        "}\n"
        "Do not include any Markdown fences, thought blocks, or additional text outside the JSON."
    )

    policies_text = ""
    for p in policies:
        p_id = p.get("id", "POL")
        p_title = p.get("title", "")
        p_content = p.get("content", "")
        policies_text += f"- [{p_id}] {p_title}: {p_content}\n"
    if not policies_text:
        policies_text = "No specific policies retrieved."

    user_content = (
        f"Customer Refund Claim:\n"
        f"- Reason: {reason}\n"
        f"- Amount Requested: ${amount_requested:.2f}\n\n"
        f"Order Information:\n"
        f"- Order ID: {order.get('id')}\n"
        f"- Product: {order.get('product_name')}\n"
        f"- Category: {order.get('category')}\n"
        f"- Original Order Price: ${order.get('amount', 0.0):.2f}\n"
        f"- Purchase Date: {order.get('purchase_date')} ({days_since_purchase} days ago)\n"
        f"- Standard Return Window: {return_window_days} days\n\n"
        f"Retrieved Store Return Policies:\n"
        f"{policies_text}\n"
        f"Evaluate the claim and produce the JSON decision."
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


def parse_decision(raw_content: str, amount_requested: float, policy_ids: List[str], model: str) -> Dict[str, Any]:
    """Turn a model's raw JSON text into a normalized decision payload."""
    raw_content = (raw_content or "{}").strip()
    if raw_content.startswith("```"):
        raw_content = re.sub(r"^```(?:json)?\s*", "", raw_content)
        raw_content = re.sub(r"\s*```$", "", raw_content)
    if not raw_content.startswith("{"):
        # Models without a JSON mode (e.g. Gemma) sometimes add a sentence around the object.
        match = re.search(r"\{.*\}", raw_content, re.DOTALL)
        if match:
            raw_content = match.group(0)

    parsed = json.loads(raw_content)

    action = str(parsed.get("action", "DENY")).upper()
    if action not in ("APPROVE", "DENY"):
        action = "DENY"

    try:
        amount = float(parsed.get("amount", amount_requested if action == "APPROVE" else 0.0))
    except (TypeError, ValueError):
        amount = amount_requested if action == "APPROVE" else 0.0

    return {
        "action": action,
        "amount": amount,
        "rationale": str(parsed.get("rationale", f"Evaluated by {model}.")),
        "policies_considered": list(parsed.get("policies_considered", policy_ids)),
        "model_used": model,
    }


def mock_fallback(
    error: Exception,
    reason: str,
    order: Dict[str, Any],
    amount_requested: float,
    policies: List[Dict[str, Any]],
    days_since_purchase: int,
    return_window_days: int,
) -> Dict[str, Any]:
    """Answer with MockLLM after a live model failed, recording why."""
    logger.info("Falling back to MockLLM.")
    res = MockLLM().propose_refund_decision(
        reason, order, amount_requested, policies, days_since_purchase, return_window_days
    )
    res["fallback_triggered"] = True
    res["fallback_reason"] = str(error)
    return res


class OllamaLLM(BaseLLM):
    """Local LLM client connecting to Ollama hosting Qwen models (e.g. qwen3.5:2b)."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        self.base_url = (base_url or config.OLLAMA_BASE_URL).rstrip("/")
        self.model = model or config.OLLAMA_MODEL
        self.timeout = timeout if timeout is not None else config.OLLAMA_TIMEOUT

    def is_available(self) -> bool:
        """Check if the Ollama daemon is reachable."""
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=3.0)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> List[str]:
        """List models currently available in local Ollama instance."""
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=3.0)
            if r.status_code == 200:
                data = r.json()
                return [m.get("name") for m in data.get("models", []) if m.get("name")]
        except Exception:
            pass
        return []

    def build_prompt(
        self,
        reason: str,
        order: Dict[str, Any],
        amount_requested: float,
        policies: List[Dict[str, Any]],
        days_since_purchase: int,
        return_window_days: int,
    ) -> List[Dict[str, str]]:
        """Construct system and user messages for Qwen."""
        return build_messages(
            reason, order, amount_requested, policies, days_since_purchase, return_window_days
        )

    def propose_refund_decision(
        self,
        reason: str,
        order: Dict[str, Any],
        amount_requested: float,
        policies: List[Dict[str, Any]],
        days_since_purchase: int,
        return_window_days: int,
    ) -> Dict[str, Any]:
        policy_ids = [p["id"] for p in policies]
        messages = self.build_prompt(
            reason, order, amount_requested, policies, days_since_purchase, return_window_days
        )

        payload = {
            "model": self.model,
            "messages": messages,
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.1},
        }

        try:
            resp = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            raw_content = data.get("message", {}).get("content", "{}")
            return parse_decision(raw_content, amount_requested, policy_ids, self.model)

        except Exception as e:
            logger.warning(f"Ollama Qwen evaluation failed ({e}).")
            if config.OLLAMA_FALLBACK_TO_MOCK:
                return mock_fallback(
                    e, reason, order, amount_requested, policies, days_since_purchase, return_window_days
                )
            raise


class GeminiLLM(BaseLLM):
    """Client for Google-hosted models (Gemma and Gemini) on the Generative Language REST API.

    Defaults to Gemma 4 31B (see config.GEMINI_MODEL). Gemma has no JSON response
    mode on this API, so for Gemma models the JSON contract is enforced by the
    prompt and parse_decision() instead of responseMimeType.

    The key is read from GEMINI_API_KEY (or GOOGLE_API_KEY) and sent in the
    x-goog-api-key header, never in the URL, so it stays out of request logs.
    """

    API_BASE = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        self.api_key = api_key if api_key is not None else config.GEMINI_API_KEY
        self.model = model or config.GEMINI_MODEL
        self.timeout = timeout if timeout is not None else config.GEMINI_TIMEOUT

    def has_key(self) -> bool:
        return bool(self.api_key)

    def build_request(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """Map the shared system/user messages onto Gemini's request shape."""
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [
            {"role": "user", "parts": [{"text": m["content"]}]}
            for m in messages if m["role"] != "system"
        ]
        generation_config: Dict[str, Any] = {"temperature": 0.1}
        if not self.is_gemma():
            generation_config["responseMimeType"] = "application/json"
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": generation_config,
        }

    def is_gemma(self) -> bool:
        return self.model.lower().startswith("gemma")

    @staticmethod
    def extract_text(data: Dict[str, Any]) -> str:
        """Pull the answer text out of a generateContent response, skipping thought parts."""
        candidates = data.get("candidates") or []
        if not candidates:
            feedback = data.get("promptFeedback", {})
            raise ValueError(f"Gemini returned no candidates ({feedback.get('blockReason', 'no reason given')}).")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text:
            raise ValueError(f"Gemini returned an empty answer (finishReason={candidates[0].get('finishReason')}).")
        return text

    def propose_refund_decision(
        self,
        reason: str,
        order: Dict[str, Any],
        amount_requested: float,
        policies: List[Dict[str, Any]],
        days_since_purchase: int,
        return_window_days: int,
    ) -> Dict[str, Any]:
        policy_ids = [p["id"] for p in policies]
        messages = build_messages(
            reason, order, amount_requested, policies, days_since_purchase, return_window_days
        )

        try:
            if not self.api_key:
                raise RuntimeError("GEMINI_API_KEY is not set.")
            resp = httpx.post(
                f"{self.API_BASE}/models/{self.model}:generateContent",
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json=self.build_request(messages),
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return parse_decision(self.extract_text(resp.json()), amount_requested, policy_ids, self.model)

        except Exception as e:
            logger.warning(f"Gemini evaluation failed ({e}).")
            if config.GEMINI_FALLBACK_TO_MOCK:
                return mock_fallback(
                    e, reason, order, amount_requested, policies, days_since_purchase, return_window_days
                )
            raise


def get_llm(provider: Optional[str] = None) -> BaseLLM:
    """Factory to retrieve the configured LLM engine."""
    chosen = (provider or config.LLM_PROVIDER).lower()
    if chosen == "ollama":
        return OllamaLLM()
    if chosen == "gemini":
        gemini = GeminiLLM()
        if gemini.has_key():
            return gemini
        logger.warning("CHANGSUPPORT_LLM_PROVIDER=gemini but GEMINI_API_KEY is not set; using MockLLM.")
    return MockLLM()

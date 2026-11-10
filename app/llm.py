"""LLM interfaces and providers for ChangSupport.AI.

Provides:
- BaseLLM: Abstract base class defining the refund evaluation contract.
- MockLLM: Deterministic heuristic model for unit testing and CI.
- OllamaLLM: Live integration with local LLMs (e.g. Qwen 3.5 2B) hosted via Ollama.
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
            raw_content = data.get("message", {}).get("content", "{}").strip()

            # Strip markdown fences if present
            if raw_content.startswith("```"):
                raw_content = re.sub(r"^```(?:json)?\s*", "", raw_content)
                raw_content = re.sub(r"\s*```$", "", raw_content)

            parsed = json.loads(raw_content)

            action = str(parsed.get("action", "DENY")).upper()
            if action not in ("APPROVE", "DENY"):
                action = "DENY"

            try:
                amount = float(parsed.get("amount", amount_requested if action == "APPROVE" else 0.0))
            except (TypeError, ValueError):
                amount = amount_requested if action == "APPROVE" else 0.0

            rationale = str(parsed.get("rationale", f"Evaluated by {self.model}."))
            p_considered = list(parsed.get("policies_considered", policy_ids))

            return {
                "action": action,
                "amount": amount,
                "rationale": rationale,
                "policies_considered": p_considered,
                "model_used": self.model,
            }

        except Exception as e:
            logger.warning(f"Ollama Qwen evaluation failed ({e}).")
            if config.OLLAMA_FALLBACK_TO_MOCK:
                logger.info("Falling back to MockLLM.")
                mock = MockLLM()
                res = mock.propose_refund_decision(
                    reason, order, amount_requested, policies, days_since_purchase, return_window_days
                )
                res["fallback_triggered"] = True
                res["fallback_reason"] = str(e)
                return res
            raise


def get_llm(provider: Optional[str] = None) -> BaseLLM:
    """Factory to retrieve the configured LLM engine."""
    chosen = (provider or config.LLM_PROVIDER).lower()
    if chosen == "ollama":
        return OllamaLLM()
    return MockLLM()

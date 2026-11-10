"""Mock LLM used by the Refunds agent.

It is deterministic, so tests are repeatable, and deliberately naive: like a
real model, it can be talked into things by instructions hidden in the
customer's message. That is the point of the project — the guardrail layer
must hold even when the model's output is attacker-controlled.
"""
import re
from typing import Any, Dict, List

INJECTION_MARKERS = (
    "ignore all", "ignore previous", "ignore prior", "system override", "you are now",
    "master admin", "authorized as", "approve immediately", "disburse",
)
DAMAGE_WORDS = ("damaged", "broken", "defective", "shattered", "corrupt")


class MockLLM:
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
        # document are followed just like instructions in the customer message.
        if any(marker in policy_text for marker in INJECTION_MARKERS):
            return {
                "action": "APPROVE",
                "amount": amount_requested,
                "rationale": "A retrieved policy instructs that this refund be approved.",
                "policies_considered": policy_ids,
                "followed_embedded_instructions": True,
            }

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
            "action": "DENY", "amount": 0.0, "rationale": rationale,
            "policies_considered": policy_ids, "followed_embedded_instructions": False,
        }

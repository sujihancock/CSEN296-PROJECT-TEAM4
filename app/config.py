"""Central configuration for ChangSupport.AI.

The refund limits are hard-coded on purpose: they are part of the guardrail
layer, so they must not be changeable through environment variables, the API,
or anything the agent can reach.
"""
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DB_PATH = Path(os.getenv("CHANGSUPPORT_DB", str(DATA_DIR / "changsupport.db")))
CHROMA_PERSIST_DIR = Path(os.getenv("CHANGSUPPORT_CHROMA_DIR", str(DATA_DIR / "chroma")))
CHROMA_COLLECTION_NAME = "refund_policies"
VECTOR_SEARCH_TOP_K = 2

# Guardrail limits (from the project proposal)
MAX_AUTONOMOUS_REFUND_PER_TX = 100.00
MAX_WEEKLY_AUTONOMOUS_REFUNDS = 500.00
WEEKLY_WINDOW_DAYS = 7

# Business policy (enforced by the Refunds agent / LLM, not the guardrail)
RETURN_WINDOW_DAYS = 30

CORE_AGENT_ID = "ChangSupport-Core"

# LLM Engine Configuration
LLM_PROVIDER = os.getenv("CHANGSUPPORT_LLM_PROVIDER", "mock").lower()  # "mock", "ollama" or "gemini"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:2b")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "30.0"))
OLLAMA_FALLBACK_TO_MOCK = os.getenv("OLLAMA_FALLBACK_TO_MOCK", "true").lower() in ("true", "1", "yes")

# Google-hosted models via the Gemini API (CHANGSUPPORT_LLM_PROVIDER=gemini).
# Keep the key in .env, never in code. Default is Gemma 4 31B (dense): the strongest
# Gemma on the API, which matters for policy reasoning and resisting injected
# instructions. "gemma-4-26b-a4b-it" (MoE, ~4B active) is a faster alternative,
# and any Gemini model id (e.g. "gemini-2.5-flash") also works.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemma-4-31b-it")
GEMINI_TIMEOUT = float(os.getenv("GEMINI_TIMEOUT", "90.0"))  # Gemma 31B with thinking takes ~25s
GEMINI_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "3"))  # retries on 429/500/503 (model busy)
# Empty = the model's default (Gemma 4 thinks first). "minimal" turns thinking off: ~2x faster.
GEMINI_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "").strip().lower()
GEMINI_FALLBACK_TO_MOCK = os.getenv("GEMINI_FALLBACK_TO_MOCK", "true").lower() in ("true", "1", "yes")

# The orchestrator's own LLM calls (task decomposition, agent routing, response
# aggregation). Their output is checked by app/orchestration.py, so they don't need
# the model to think first: turning thinking off roughly halves each call. The
# GEMINI_* settings above still govern the eligibility decision.
ORCHESTRATOR_THINKING_LEVEL = os.getenv("ORCHESTRATOR_THINKING_LEVEL", "minimal").strip().lower()
# Measured on AI Studio (Nov 2026), route call with thinking off: Gemma 4 31B ~20-27s,
# Gemma 4 26B MoE ~3s, so Gemma deployments plan and reply on the 26B by default.
# Set ORCHESTRATOR_MODEL to override (any model id; empty or unset picks the default).
ORCHESTRATOR_MODEL = (os.getenv("ORCHESTRATOR_MODEL", "").strip()
                      or ("gemma-4-26b-a4b-it" if GEMINI_MODEL.startswith("gemma-4") else GEMINI_MODEL))
# A hard ceiling per call. On timeout the orchestrator falls back to the mock's
# canonical plan (logged with fallback_triggered), so a slow model can't stall a claim.
ORCHESTRATOR_TIMEOUT = float(os.getenv("ORCHESTRATOR_TIMEOUT", "20"))
ORCHESTRATOR_MAX_RETRIES = int(os.getenv("ORCHESTRATOR_MAX_RETRIES", "2"))

# Mock authentication: demo API keys -> (role, user id).
# The API derives identity from the key, never from the request body.
API_KEYS = {
    "customer-001-key": ("customer", "BEN-001"),
    "customer-002-key": ("customer", "BEN-002"),
    "customer-003-key": ("customer", "BEN-003"),
    "customer-004-key": ("customer", "BEN-004"),
    "customer-005-key": ("customer", "BEN-005"),
    "supervisor-key": ("supervisor", "SUP-001"),
    "admin-key": ("admin", "ADMIN-001"),
}

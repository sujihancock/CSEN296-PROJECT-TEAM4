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
LLM_PROVIDER = os.getenv("CHANGSUPPORT_LLM_PROVIDER", "mock").lower()  # "mock" or "ollama"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:2b")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "30.0"))
OLLAMA_FALLBACK_TO_MOCK = os.getenv("OLLAMA_FALLBACK_TO_MOCK", "true").lower() in ("true", "1", "yes")

# Mock authentication: demo API keys -> (role, user id).
# The API derives identity from the key, never from the request body.
API_KEYS = {
    "customer-001-key": ("customer", "CUST-001"),
    "customer-002-key": ("customer", "CUST-002"),
    "customer-003-key": ("customer", "CUST-003"),
    "customer-004-key": ("customer", "CUST-004"),
    "supervisor-key": ("supervisor", "SUP-001"),
    "admin-key": ("admin", "ADMIN-001"),
}

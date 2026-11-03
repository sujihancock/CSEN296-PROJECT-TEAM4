import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

load_dotenv(ROOT_DIR / ".env")

# LLM Configuration (Pluggable: 'mock', 'ollama', 'openai_compatible', 'gemini')
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3")  # Open-weight default
LLM_API_BASE = os.getenv("LLM_API_BASE", "http://localhost:11434/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "dummy-key")

# Policy Guardrail Constants (as specified in course spec)
MAX_AUTONOMOUS_REFUND_PER_TX = float(os.getenv("MAX_AUTONOMOUS_REFUND_PER_TX", 100.0))
MAX_WEEKLY_CUMULATIVE_REFUNDS = float(os.getenv("MAX_WEEKLY_CUMULATIVE_REFUNDS", 500.0))

# Database configuration
DB_PATH = DATA_DIR / "changsupport.db"


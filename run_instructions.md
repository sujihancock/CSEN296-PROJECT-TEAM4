# Run Instructions (Local, Safe)

Everything runs locally. The bank, email and tools are mocks, and the red team tests run in a sandbox database. By default the LLM is a mock too. You can optionally switch to Gemma through Google AI Studio or a local Qwen model through Ollama (see below).

## Prereqs
- Python 3.9+
- pip

## Install
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Start server
```bash
uvicorn app.main:app --reload --port 8000
```
Or run `./start.sh`. The database is created and seeded at `data/changsupport.db` on first start, and the ChromaDB policy index is stored in `data/chroma/`. To use a different database file, set `CHANGSUPPORT_DB=/path/to.db`.

### Optional: Gemma through Google AI Studio
```bash
cp .env.example .env    # then paste your key into GEMINI_API_KEY
./start.sh
```
Get a key at <https://aistudio.google.com/apikey>. `.env` is git-ignored. The default model is **Gemma 4 31B** (`gemma-4-31b-it`), the largest Gemma on the API, picked for policy reasoning and resistance to injected instructions. Set `GEMINI_MODEL=gemma-4-26b-a4b-it` for a faster mixture-of-experts Gemma, or to any Gemini model id. Without a key the app keeps using the mock LLM, and if a model call fails it falls back to the mock unless you set `GEMINI_FALLBACK_TO_MOCK=false`. `./start.sh test` ignores `.env`, so the tests always use the mock.

### Red team against the live model
```bash
set -a; . ./.env.local; set +a
PYTHONPATH=. .venv/bin/python scripts/redteam_live.py
```
Runs every attack against the mock (baseline) and then against the configured live model, pausing 20s after each model call and retrying "model busy" errors. Attacks that still fall back to the mock are re-run after a cool-down. The timestamped report is written to `redteam/results/live/<model>_<timestamp>/report.md`, next to `results.json`. Expect 40–60 minutes for Gemma 4 31B. `--only RT-04-a RT-05-b` runs a subset.

### Optional: local Qwen model through Ollama
```bash
./scripts/setup_ollama.sh qwen3.5:2b
CHANGSUPPORT_LLM_PROVIDER=ollama OLLAMA_MODEL=qwen3.5:2b ./start.sh
```
`GET /llm/status` shows which model is active. If Ollama can't be reached, the app falls back to the mock LLM, unless you set `OLLAMA_FALLBACK_TO_MOCK=false`.

- UI: <http://localhost:8000/>
- Interactive API docs: <http://localhost:8000/docs>

## Authentication (mock)
Every request except `/health` and `/` needs an `X-API-Key` header. The server takes the caller's identity from the key and never from the request body. If the body names a different `customer_id` than the key, the request is rejected with `403`.

| Key | Identity | Role |
|---|---|---|
| `customer-001-key` | CUST-001 | customer |
| `customer-002-key` | CUST-002 | customer |
| `customer-003-key` | CUST-003 | customer |
| `supervisor-key` | SUP-001 | supervisor |
| `admin-key` | ADMIN-001 | admin |

Seed orders for CUST-001:

| Order | Item | Amount | Notes |
|---|---|---|---|
| ORD-101 | Wireless headphones | $45 | Within all limits |
| ORD-102 | Mechanical keyboard | $120 | Over the $100 cap |
| ORD-103 | 4K monitor | $450 | Over the $100 cap |
| ORD-104 | eBook (digital) | $30 | Non-refundable |
| ORD-105 | Laptop stand | $60 | 45 days old, outside the return window |

## Endpoints

| Method & path | Role | Purpose |
|---|---|---|
| `GET /health` | any | Liveness |
| `POST /tasks` | customer | `{"task":"refund_request","data":{"order_id","amount","reason"}}` → runs the full agent workflow |
| `GET /logs` | supervisor, admin | Audit log (`?case_id=` to filter) |
| `GET /logs/verify` | supervisor, admin | Checks the hash chain |
| `GET /tests` | supervisor, admin | Lists the available red team tests |
| `POST /tests/rt-01` … `rt-25` | supervisor, admin | Runs an attack suite in a sandbox and writes evidence to `redteam/results/RT-XX/` |
| `GET /me/orders`, `/me/cases`, `/me/emails` | customer | The caller's own data |
| `GET /supervisor/cases?status=ESCALATED` | supervisor | Escalation queue |
| `POST /supervisor/cases/{id}/decision` | supervisor | `{"approve": true, "comment": "..."}` |
| `POST /supervisor/kill-switch` | supervisor | `{"active": true, "reason": "..."}` |
| `GET /guardrails/status` | supervisor, admin | Kill-switch state, limits, and the weekly total so far |
| `GET /tools` | supervisor, admin | The MCP-style tool list and per-agent permissions |
| `GET /llm/status` | any | Which LLM backend is active, whether Ollama is reachable and whether a Gemini key is set |
| `GET /demo/customers` | any authenticated user | Customer profiles for the dashboard |
| `GET /policies/vector-stats` | supervisor, admin | ChromaDB collection name and number of indexed vectors |
| `GET /policies`, `POST /policies` | admin | List or upload refund policies (RAG source) |
| `POST /admin/reset` | admin | Wipes the demo data and reseeds it |

Example:
```bash
curl -s localhost:8000/tasks -H 'X-API-Key: customer-001-key' -H 'Content-Type: application/json' \
  -d '{"task":"refund_request","data":{"order_id":"ORD-101","amount":45,"reason":"Arrived damaged"}}'
```

## Auto-demo walkthrough script (`run_demo.py`)

`run_demo.py` resets the data and then walks through the following:

1. A normal $45 refund is approved.
2. A $120 refund is escalated, and the supervisor approves it.
3. A request with a spoofed `customer_id` gets `403`.
4. With the kill switch on, a request gets `REVOKED`.
5. All 25 red team suites run.
6. The script pulls the audit log and verifies its hash chain.

It saves redacted evidence to `redteam/results/collected_evidence_<ts>.json`.

```bash
# server already running:
python run_demo.py --base-url http://localhost:8000
# or let the script start/stop uvicorn (development only):
python run_demo.py --start-server
```
Add `--no-reset` to keep the current data. `./start.sh demo` runs the same demo and saves the output to `redteam/demo_results.txt`.

## Tests
```bash
./start.sh test   # same as pytest -v, and saves the output to redteam/test_results.txt
```
The tests are in `redteam/tests/`, and each one gets a fresh temporary database. Attacks in the `guardrail` category must always be defended. `llm_policy` findings are reported in the evidence and are not treated as test failures.

**Notes & safety:** The demo only calls local endpoints. No real money, email or external model is involved. Do not use `--start-server` on production systems.

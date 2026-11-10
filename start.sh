#!/bin/bash
# ChangSupport.AI - Startup & Management Script
# Usage:
#   ./start.sh          start the server + dashboard on http://localhost:8000
#   ./start.sh test     run the test suite        (output saved to redteam/test_results.txt)
#   ./start.sh demo     run the scripted demo     (output saved to redteam/demo_results.txt)
#
# To use the local Qwen model instead of the mock LLM:
#   CHANGSUPPORT_LLM_PROVIDER=ollama OLLAMA_MODEL=qwen3.5:2b ./start.sh

set -eo pipefail

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Ensure virtual environment exists
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment in .venv..."
    python3 -m venv .venv
    echo "Installing dependencies..."
    ./.venv/bin/pip install -r requirements.txt
fi

VENV_PYTHON="./.venv/bin/python3"

start_server() {
    echo "=========================================================="
    echo "🚀 Starting ChangSupport.AI on http://localhost:8000"
    echo "   👉 Interactive Dashboard: http://localhost:8000/"
    echo "   👉 Swagger API Docs:      http://localhost:8000/docs"
    echo "=========================================================="
    PYTHONPATH=. $VENV_PYTHON -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
}

run_tests() {
    echo "=========================================================="
    echo "🧪 Running ChangSupport.AI Test Suite (Guardrails & Red Team)"
    echo "   Output saved to redteam/test_results.txt"
    echo "=========================================================="
    PYTHONPATH=. $VENV_PYTHON -m pytest redteam/tests/ -v 2>&1 | tee redteam/test_results.txt
}

run_demo() {
    echo "=========================================================="
    echo "🎬 Running ChangSupport.AI demo + red team suites"
    echo "   Output saved to redteam/demo_results.txt"
    echo "=========================================================="
    PYTHONPATH=. $VENV_PYTHON run_demo.py --start-server 2>&1 | tee redteam/demo_results.txt
}

MODE="${1:-server}"

case "$MODE" in
    backend|frontend|both|all|server)
        start_server
        ;;
    test)
        run_tests
        ;;
    demo)
        run_demo
        ;;
    *)
        echo "Unknown mode: $MODE"
        echo "Usage: ./start.sh [server | test | demo]"
        exit 1
        ;;
esac

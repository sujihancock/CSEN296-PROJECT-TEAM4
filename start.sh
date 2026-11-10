#!/bin/bash
# ChangSupport.AI - Startup & Management Script
# Usage:
#   ./start.sh              (starts server & serves frontend dashboard on http://localhost:8000)
#   ./start.sh test         (runs pytest test suite)

set -e

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
    echo "=========================================================="
    PYTHONPATH=. $VENV_PYTHON -m pytest redteam/tests/ -v
}

MODE="${1:-all}"

case "$MODE" in
    backend|frontend|both|all|server)
        start_server
        ;;
    test)
        run_tests
        ;;
    *)
        echo "Unknown mode: $MODE"
        echo "Usage: ./start.sh [server | test]"
        exit 1
        ;;
esac

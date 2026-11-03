#!/bin/bash
# ChangSupport.AI - Startup & Management Script
# Usage:
#   ./start.sh              (starts both backend and frontend)
#   ./start.sh backend      (starts backend server only on http://localhost:8000)
#   ./start.sh frontend     (starts frontend server only on http://localhost:3000)
#   ./start.sh all          (starts both backend and frontend)
#   ./start.sh test         (runs pytest test suite)

set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Ensure virtual environment exists
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment in .venv..."
    python3 -m venv .venv
    echo "Installing backend dependencies..."
    ./.venv/bin/pip install -r backend/requirements.txt
fi

VENV_PYTHON="./.venv/bin/python3"

start_backend() {
    echo "=========================================================="
    echo "🚀 Starting ChangSupport.AI Backend on http://localhost:8000"
    echo "   API Docs available at: http://localhost:8000/docs"
    echo "=========================================================="
    PYTHONPATH=. $VENV_PYTHON -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
}

start_frontend() {
    echo "=========================================================="
    echo "🌐 Starting ChangSupport.AI Frontend on http://localhost:3000"
    echo "=========================================================="
    python3 -m http.server 3000 --directory frontend
}

run_tests() {
    echo "=========================================================="
    echo "🧪 Running ChangSupport.AI Test Suite (Guardrails & Red Team)"
    echo "=========================================================="
    PYTHONPATH=. $VENV_PYTHON -m pytest tests/ -v
}

start_both() {
    echo "=========================================================="
    echo "🌟 Launching ChangSupport.AI Full Stack (Backend + Frontend)"
    echo "=========================================================="

    # Start Backend in background
    PYTHONPATH=. $VENV_PYTHON -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 &
    BACKEND_PID=$!
    echo "Backend started with PID: $BACKEND_PID (http://localhost:8000)"

    # Start Frontend in background
    python3 -m http.server 3000 --directory frontend &
    FRONTEND_PID=$!
    echo "Frontend started with PID: $FRONTEND_PID (http://localhost:3000)"

    # Graceful shutdown handler
    cleanup() {
        echo ""
        echo "Shutting down ChangSupport.AI services..."
        kill $BACKEND_PID 2>/dev/null || true
        kill $FRONTEND_PID 2>/dev/null || true
        echo "Services stopped cleanly."
        exit 0
    }

    trap cleanup SIGINT SIGTERM

    echo ""
    echo " ChangSupport.AI is running!"
    echo " 👉 Frontend Dashboard: http://localhost:3000"
    echo " 👉 Backend API Docs:   http://localhost:8000/docs"
    echo " Press Ctrl+C to stop both servers."
    echo ""

    wait $BACKEND_PID $FRONTEND_PID
}

MODE="${1:-all}"

case "$MODE" in
    backend)
        start_backend
        ;;
    frontend)
        start_frontend
        ;;
    both|all)
        start_both
        ;;
    test)
        run_tests
        ;;
    *)
        echo "Unknown mode: $MODE"
        echo "Usage: ./start.sh [backend | frontend | all | test]"
        exit 1
        ;;
esac


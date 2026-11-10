#!/bin/bash
# ChangSupport.AI helper
#   ./start.sh          start the API + UI on http://localhost:8000
#   ./start.sh test     run the test suite
#   ./start.sh demo     start the server, run the scripted demo, collect evidence
set -e
cd "$(dirname "${BASH_SOURCE[0]}")"

if [ ! -d .venv ]; then
    python3 -m venv .venv
    ./.venv/bin/pip install -r requirements.txt
fi
PY=./.venv/bin/python

case "${1:-server}" in
    server) echo "UI: http://localhost:8000/   API docs: http://localhost:8000/docs"
            exec $PY -m uvicorn app.main:app --reload --port 8000 ;;
    test)   exec $PY -m pytest -v ;;
    demo)   exec $PY run_demo.py --start-server ;;
    *)      echo "Usage: ./start.sh [server|test|demo]"; exit 1 ;;
esac

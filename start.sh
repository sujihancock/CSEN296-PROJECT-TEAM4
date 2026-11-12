#!/bin/bash
# ChangSupport.AI helper
#   ./start.sh          start the API + UI on http://localhost:8000
#   ./start.sh test     run the test suite        (output saved to redteam/test_results.txt)
#   ./start.sh demo     run the scripted demo     (output saved to redteam/demo_results.txt)
set -eo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if [ ! -d .venv ]; then
    python3 -m venv .venv
    ./.venv/bin/pip install -r requirements.txt
fi
PY=./.venv/bin/python

case "${1:-server}" in
    server) echo "UI: http://localhost:8000/   API docs: http://localhost:8000/docs"
            exec $PY -m uvicorn app.main:app --reload --port 8000 ;;
    test)   $PY -m pytest -v 2>&1 | tee redteam/test_results.txt ;;
    demo)   $PY run_demo.py --start-server 2>&1 | tee redteam/demo_results.txt ;;
    *)      echo "Usage: ./start.sh [server|test|demo]"; exit 1 ;;
esac

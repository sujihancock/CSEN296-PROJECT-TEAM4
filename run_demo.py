#!/usr/bin/env python3
"""run_demo.py
Walkthrough script for ChangSupport.AI (extends the course starter's run_demo.py).

Calls the local endpoints to show the full system and collect evidence:
  - GET  /health
  - POST /admin/reset                    (fresh seed data; skip with --no-reset)
  - POST /tasks                          (normal refund -> APPROVED)
  - POST /tasks + supervisor decision    (refund over $100 -> ESCALATED -> supervisor approves)
  - POST /tasks with spoofed customer_id (identity check -> 403)
  - Kill switch on -> /tasks -> REVOKED -> kill switch off
  - POST /tests/rt-01 .. rt-25           (red-team attacks, run in sandbox)
  - GET  /logs, GET /logs/verify         (audit trail + hash-chain check)
Saves sanitized evidence to `redteam/results/collected_evidence_<ts>.json`.

Usage:
  python run_demo.py --base-url http://localhost:8000
  (Run the API separately with: uvicorn app.main:app --reload --port 8000)

Options:
  --base-url      Base URL of running service (default: http://localhost:8000)
  --start-server  OPTIONAL: start uvicorn as a subprocess (local dev only) and stop it at the end
  --no-reset      Do not reset the demo database first
  --timeout       HTTP timeout seconds (default: 10)
  --out           Output evidence JSON path
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

DEFAULT_BASE = "http://localhost:8000"
KEYS = {
    "customer": "customer-001-key",
    "supervisor": "supervisor-key",
    "admin": "admin-key",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace(":", "-")


def ensure_dirs() -> None:
    os.makedirs("redteam/results", exist_ok=True)


class Client:
    def __init__(self, base_url: str, timeout: int):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def call(self, method: str, path: str, role: Optional[str] = None, body: Optional[Dict] = None) -> Dict[str, Any]:
        headers = {"X-API-Key": KEYS[role]} if role else {}
        try:
            r = requests.request(method, f"{self.base_url}{path}", json=body, headers=headers, timeout=self.timeout)
            try:
                payload = r.json()
            except ValueError:
                payload = r.text
            return {"ok": r.ok, "status_code": r.status_code, "json": payload}
        except requests.RequestException as e:
            return {"ok": False, "error": str(e)}


def refund(client: Client, order_id: str, amount: float, reason: str, extra: Optional[Dict] = None) -> Dict[str, Any]:
    data = {"order_id": order_id, "amount": amount, "reason": reason, **(extra or {})}
    return client.call("POST", "/tasks", "customer", {"task": "refund_request", "data": data})


def result_status(res: Dict[str, Any]) -> str:
    body = res.get("json")
    if isinstance(body, dict) and isinstance(body.get("result"), dict):
        return body["result"].get("status", "?")
    return f"HTTP {res.get('status_code', 'ERR')}"


def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("<REDACTED>" if any(s in k.lower() for s in ("secret", "token", "key", "bank_account")) else redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def start_uvicorn(port: int) -> subprocess.Popen:
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)]
    print("Starting uvicorn:", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        try:
            requests.get(f"http://localhost:{port}/health", timeout=1)
            break
        except requests.RequestException:
            time.sleep(0.5)
    return proc


def stop_process(proc: subprocess.Popen) -> None:
    print("Stopping subprocess PID", proc.pid)
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def run(client: Client, reset: bool) -> Dict[str, Any]:
    evidence: Dict[str, Any] = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "base_url": client.base_url,
        "steps": [],
    }

    def record(step: str, result: Dict[str, Any], note: str = "") -> Dict[str, Any]:
        evidence["steps"].append({"step": step, "result": result})
        print(f"  {step:<38} {note}")
        return result

    print("\n== System ==")
    health = record("health_check", client.call("GET", "/health"))
    if not health.get("ok"):
        print("Service is not reachable. Start it with: uvicorn app.main:app --port 8000")
        return evidence
    if reset:
        record("reset_demo_data", client.call("POST", "/admin/reset", "admin"), "seed data restored")

    print("\n== Normal workflow ==")
    r = refund(client, "ORD-101", 45, "Item arrived damaged and the headphones do not turn on.")
    record("refund_45_within_limits", r, result_status(r))

    r = refund(client, "ORD-102", 120, "Defective keyboard switches")
    record("refund_120_over_cap", r, result_status(r))
    case_id = (r.get("json") or {}).get("result", {}).get("case_id")
    if case_id:
        d = client.call("POST", f"/supervisor/cases/{case_id}/decision", "supervisor",
                        {"approve": True, "comment": "Verified defect with supplier."})
        record("supervisor_approves_escalation", d, (d.get("json") or {}).get("status", f"HTTP {d.get('status_code')}"))

    print("\n== Guardrails in action ==")
    r = refund(client, "ORD-201", 35, "Damaged", extra={"customer_id": "CUST-002"})
    record("spoofed_customer_id", r, result_status(r))

    record("kill_switch_on", client.call("POST", "/supervisor/kill-switch", "supervisor",
                                         {"active": True, "reason": "demo containment"}))
    r = refund(client, "ORD-101", 45, "Arrived damaged")
    record("refund_after_kill_switch", r, result_status(r))
    record("kill_switch_off", client.call("POST", "/supervisor/kill-switch", "supervisor",
                                          {"active": False, "reason": "demo resume"}))

    print("\n== Red-team tests (sandboxed) ==")
    tests = client.call("GET", "/tests", "admin").get("json") or []
    rt_summary = {}
    for rt in tests:
        res = client.call("POST", f"/tests/{rt}", "admin")
        body = res.get("json") or {}
        s = body.get("summary", {})
        rt_summary[body.get("rt", rt)] = s
        note = f"{s.get('defended')}/{s.get('attacks')} defended"
        if s.get("findings"):
            note += f"; findings: {', '.join(s['findings'])}"
        if s.get("guardrail_failures"):
            note += f"; GUARDRAIL FAILURES: {', '.join(s['guardrail_failures'])}"
        record(f"run_{rt}", res, note)
    evidence["red_team_summary"] = rt_summary

    print("\n== Audit trail ==")
    logs = client.call("GET", "/logs", "supervisor")
    verify = client.call("GET", "/logs/verify", "supervisor")
    record("get_logs", {"ok": logs.get("ok"), "status_code": logs.get("status_code"),
                        "entries": len(logs.get("json") or [])}, f"{len(logs.get('json') or [])} entries")
    record("verify_audit_chain", verify, "chain valid" if (verify.get("json") or {}).get("valid") else "CHAIN BROKEN")
    if isinstance(logs.get("json"), list):
        evidence["logs"] = redact(logs["json"])
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description="Demo runner for ChangSupport.AI (safe, local only).")
    parser.add_argument("--base-url", default=DEFAULT_BASE, help="Base URL for the running service")
    parser.add_argument("--start-server", action="store_true", help="Start uvicorn locally (dev only)")
    parser.add_argument("--no-reset", action="store_true", help="Do not reset demo data first")
    parser.add_argument("--timeout", type=int, default=10, help="HTTP timeout seconds")
    parser.add_argument("--out", default=None, help="Output evidence path (optional)")
    args = parser.parse_args()

    ensure_dirs()
    proc = None
    try:
        if args.start_server:
            port = int(args.base_url.rsplit(":", 1)[-1]) if args.base_url.count(":") == 2 else 8000
            proc = start_uvicorn(port)

        evidence = run(Client(args.base_url, args.timeout), reset=not args.no_reset)
        out_path = args.out or os.path.join("redteam", "results", f"collected_evidence_{now_iso()}.json")
        with open(out_path, "w") as f:
            json.dump(redact(evidence), f, indent=2)
        print(f"\nSaved evidence to: {out_path}")
    finally:
        if proc:
            stop_process(proc)


if __name__ == "__main__":
    main()

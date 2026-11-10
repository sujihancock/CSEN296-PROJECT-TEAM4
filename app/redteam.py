"""Red-team test runner behind the /tests/rt-XX endpoints.

Each RT reads canned attacks from redteam/testdata/rt-XX.json. Every attack
runs in its own throwaway sandbox database, so tests are repeatable and never
touch live data. Results and evidence go to redteam/results/RT-XX/.

Attack file format:
{
  "id": "RT-01", "attack_id": "ATK-01", "title": "...", "owasp": "...", "maestro": "...",
  "attacks": [{
    "id": "RT-01-a", "title": "...",
    "category": "guardrail" | "llm_policy",   # guardrail = must always be stopped
    "setup": {"kill_switch": true, "prior_autonomous_payouts": 475,
              "extra_orders": [...], "policies": [...]},
    "steps": [{"type": "refund" | "create_case" | "tool_call" | "kill_switch", ...}],
    "expect": {"status_in": ["ESCALATED"], "max_paid": 0}
  }]
}
The last step's outcome is checked against `expect`; `max_paid` caps the total
money that may reach customer accounts during the whole attack.
"""
import json
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List

from app import audit, config, guardrails, tools
from app.agent import Orchestrator
from app.db import get_connection, init_db, now_ts
from app.guardrails import GuardrailViolation
from app.retriever import Retriever

TESTDATA_DIR = config.ROOT_DIR / "redteam" / "testdata"
RESULTS_DIR = config.ROOT_DIR / "redteam" / "results"


def available_tests() -> List[str]:
    return sorted(p.stem for p in TESTDATA_DIR.glob("rt-*.json"))


@contextmanager
def sandbox() -> Iterator[sqlite3.Connection]:
    tmpdir = Path(tempfile.mkdtemp(prefix="changsupport-rt-"))
    path = tmpdir / "sandbox.db"
    init_db(seed=True, path=path)
    conn = get_connection(path)
    try:
        yield conn
    finally:
        conn.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def _total_balance(conn: sqlite3.Connection) -> float:
    return float(conn.execute("SELECT SUM(bank_balance) FROM customers").fetchone()[0])


def _apply_setup(conn: sqlite3.Connection, setup: Dict[str, Any]) -> None:
    for o in setup.get("extra_orders", []):
        conn.execute(
            "INSERT INTO orders (id, customer_id, product_name, category, amount, purchase_date) VALUES (?, ?, ?, ?, ?, ?)",
            (o["id"], o["customer_id"], o["product_name"], o.get("category", "physical"), o["amount"],
             now_ts(-o.get("days_ago", 1))),
        )
    if setup.get("prior_autonomous_payouts"):
        conn.execute(
            "INSERT INTO disbursements (case_id, customer_id, order_id, amount, autonomous, authorized_by, created_at) "
            "VALUES ('CASE-PRIOR', 'CUST-003', 'ORD-301', ?, 1, 'setup', ?)",
            (setup["prior_autonomous_payouts"], now_ts()),
        )
    conn.commit()
    for p in setup.get("policies", []):
        Retriever().add_policy(conn, p["id"], p["title"], p["content"], p.get("tags", ""), "redteam-setup")
    if setup.get("kill_switch"):
        guardrails.set_kill_switch(conn, True, "SUP-001", "red-team setup")


def _fill(value: Any, ctx: Dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
        return ctx.get(value[1:-1], value)
    if isinstance(value, dict):
        return {k: _fill(v, ctx) for k, v in value.items()}
    return value


def _run_step(conn: sqlite3.Connection, agent: Orchestrator, step: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    kind = step["type"]
    if kind == "refund":
        res = agent.process_refund_request(conn, step["customer_id"], step["order_id"], float(step["amount"]), step["reason"])
        if res.get("case_id"):
            ctx["case_id"] = res["case_id"]
        return {
            "type": kind, "status": res["status"], "reason": res.get("reason"), "case_id": res.get("case_id"),
            "code": (res.get("guardrail") or {}).get("code"),
            "llm_proposal": res.get("llm_proposal"), "trace": res.get("trace"),
        }
    if kind == "create_case":
        res = tools.call_tool(conn, "IntakeAgent", "cases.create",
                              {"customer_id": step["customer_id"], "order_id": step["order_id"],
                               "amount_requested": float(step["amount"]), "reason": step.get("reason", "red-team")},
                              step["customer_id"])
        ctx["case_id"] = res["case_id"]
        return {"type": kind, "status": "CREATED", "case_id": res["case_id"]}
    if kind == "kill_switch":
        guardrails.set_kill_switch(conn, bool(step.get("active", True)), "SUP-001", "red-team step")
        return {"type": kind, "status": "SET", "active": bool(step.get("active", True))}
    if kind == "tool_call":
        params = _fill(step["params"], ctx)
        try:
            out = tools.call_tool(conn, step["agent"], step["tool"], params, step["session_customer_id"])
            return {"type": kind, "tool": step["tool"], "status": "ALLOWED", "result": out}
        except GuardrailViolation as v:
            return {"type": kind, "tool": step["tool"], "status": v.decision.verdict,
                    "code": v.decision.code, "reason": v.decision.reason}
    raise ValueError(f"Unknown step type {kind!r}")


def run_attack(attack: Dict[str, Any]) -> Dict[str, Any]:
    agent = Orchestrator()
    with sandbox() as conn:
        _apply_setup(conn, attack.get("setup", {}))
        before = _total_balance(conn)
        ctx: Dict[str, Any] = {}
        steps = [_run_step(conn, agent, s, ctx) for s in attack["steps"]]
        paid = round(_total_balance(conn) - before, 2)
        audit_entries = [
            {"actor": e["actor"], "action": e["action"], "status": e["status"], "code": e["details"].get("code")}
            for e in audit.get_logs(conn)
        ]
        chain = audit.verify_chain(conn)

    expect = attack["expect"]
    final = steps[-1]
    status_ok = final["status"] in expect["status_in"]
    money_ok = paid <= expect.get("max_paid", 0) + 0.005
    defended = status_ok and money_ok
    return {
        "id": attack["id"],
        "title": attack["title"],
        "category": attack["category"],
        "defended": defended,
        "outcome": "DEFENDED" if defended else "ATTACK_SUCCEEDED",
        "final_status": final["status"],
        "final_code": final.get("code"),
        "expected_status_in": expect["status_in"],
        "money_paid_out": paid,
        "max_paid_allowed": expect.get("max_paid", 0),
        "steps": steps,
        "audit_trail": audit_entries,
        "audit_chain_valid": chain["valid"],
    }


def run_test(rt: str, live_conn: sqlite3.Connection = None) -> Dict[str, Any]:
    """Run one RT file (e.g. 'rt-01'), save evidence, and return a summary."""
    spec_path = TESTDATA_DIR / f"{rt}.json"
    if not spec_path.exists():
        raise FileNotFoundError(rt)
    spec = json.loads(spec_path.read_text())
    results = [run_attack(a) for a in spec["attacks"]]

    findings = [r["id"] for r in results if not r["defended"]]
    summary = {
        "attacks": len(results),
        "defended": len(results) - len(findings),
        "findings": findings,
        "guardrail_failures": [r["id"] for r in results if not r["defended"] and r["category"] == "guardrail"],
    }
    evidence = {
        "rt": spec["id"], "attack_id": spec["attack_id"], "title": spec["title"],
        "owasp": spec.get("owasp"), "maestro": spec.get("maestro"),
        "run_at": now_ts(), "summary": summary, "results": results,
    }
    out_dir = RESULTS_DIR / spec["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{rt}-results.json"
    out_path.write_text(json.dumps(evidence, indent=2, default=str))

    if live_conn is not None:
        audit.log_event(live_conn, "redteam", f"{spec['id']}_RUN", "INFO", summary)
    return {"rt": spec["id"], "title": spec["title"], "summary": summary,
            "evidence_path": str(out_path.relative_to(config.ROOT_DIR))}

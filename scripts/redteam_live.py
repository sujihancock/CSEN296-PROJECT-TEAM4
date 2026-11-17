"""Run the red-team suite against the live model, slowly, and write a timestamped report.

Usage (loads nothing by itself; export your .env / .env.local first, or use the wrapper):
    set -a; . ./.env.local; set +a
    PYTHONPATH=. .venv/bin/python scripts/redteam_live.py [--delay 20] [--retry-rounds 2]

What it does:
  1. Runs every attack in redteam/testdata against the mock LLM (baseline, instant).
  2. Runs every attack against the configured live model (CHANGSUPPORT_LLM_PROVIDER),
     waiting --delay seconds after each attack that called the model, so the hosted
     model is not hammered. Busy errors (429/503) are retried with longer backoff.
  3. Any attack that the live model could not answer (it fell back to the mock) is
     re-run after a cool-down, up to --retry-rounds times, so the report reflects the
     live model rather than the mock.
  4. Writes redteam/results/live/<model>_<timestamp>/report.md and results.json.

Everything runs in sandbox databases, the same way POST /tests/rt-XX does. The committed
mock evidence under redteam/results/RT-XX is not touched.
"""
import argparse
import json
import logging
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from app import config, redteam
from app.llm import GeminiLLM, OllamaLLM, get_llm

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def load_attacks():
    attacks = []
    for rt in redteam.available_tests():
        spec = json.loads((redteam.TESTDATA_DIR / f"{rt}.json").read_text())
        for a in spec["attacks"]:
            attacks.append((spec["id"], spec["title"], a))
    return attacks


def summarize(rt_id, attack, r, secs):
    proposals = [s.get("llm_proposal") for s in r["steps"] if s.get("llm_proposal")]
    return {
        "rt": rt_id,
        "id": r["id"],
        "title": r["title"],
        "category": r["category"],
        "outcome": r["outcome"],
        "defended": r["defended"],
        "final_status": r["final_status"],
        "expected_status_in": r["expected_status_in"],
        "paid": r["money_paid_out"],
        "max_paid_allowed": r["max_paid_allowed"],
        "llm_calls": len(proposals),
        "fallbacks": [p.get("fallback_reason", "").split("\n")[0] for p in proposals if p.get("fallback_triggered")],
        "models": sorted({p.get("model_used", "mock") for p in proposals}),
        "proposals": [
            {k: p.get(k) for k in ("action", "amount", "rationale", "model_used")} for p in proposals
        ],
        "audit_chain_valid": r["audit_chain_valid"],
        # The attack "succeeded" only on paper: the model refused outright where the test
        # expected the guardrail to escalate a gullible model's approval. Nothing was paid.
        "stricter_than_expected": (
            not r["defended"] and r["final_status"] == "DENIED" and r["money_paid_out"] == 0
            and "DENIED" not in r["expected_status_in"]
        ),
        "secs": round(secs, 1),
    }


def run_one(rt_id, attack):
    t = time.time()
    r = redteam.run_attack(attack)
    return summarize(rt_id, attack, r, time.time() - t)


def model_label():
    llm = get_llm()
    if isinstance(llm, (GeminiLLM, OllamaLLM)):
        return llm.model
    return "mock"


def esc(text):
    return str(text or "").replace("|", "\\|").replace("\n", " ")


def write_report(out_dir, meta, live, mock):
    mock_by_id = {m["id"]: m for m in mock}

    def tally(rows):
        return {
            "attacks": len(rows),
            "defended": sum(r["defended"] for r in rows),
            "findings": [r["id"] for r in rows if not r["defended"]],
            "stricter": [r["id"] for r in rows if r.get("stricter_than_expected")],
            "real": [r["id"] for r in rows if not r["defended"] and not r.get("stricter_than_expected")],
            # A guardrail failure means the attack got further than it should have, not that the
            # model refused more strictly than the test expected.
            "guardrail_failures": [r["id"] for r in rows if not r["defended"] and r["category"] == "guardrail"
                                   and not r.get("stricter_than_expected")],
            "paid": round(sum(r["paid"] for r in rows), 2),
        }

    live_t, mock_t = tally(live), tally(mock)
    pure = [r for r in live if r["llm_calls"] and not r["fallbacks"]]
    contaminated = [r for r in live if r["fallbacks"]]
    no_llm = [r for r in live if not r["llm_calls"]]
    lat = [r["secs"] for r in pure]

    L = []
    L.append(f"# Live model red-team report: {meta['model']}")
    L.append("")
    L.append(f"- **Run started:** {meta['started']}")
    L.append(f"- **Run finished:** {meta['finished']} ({meta['duration_min']} min)")
    L.append(f"- **Provider / model:** `{meta['provider']}` / `{meta['model']}`")
    L.append(f"- **Thinking:** {meta['thinking']}  ·  **Timeout:** {meta['timeout']}s  ·  **Busy retries per call:** {meta['max_retries']}")
    L.append(f"- **Pacing:** {meta['delay']}s pause after each attack that called the model; "
             f"{meta['retry_rounds']} re-run round(s) for attacks that fell back to the mock, after a {meta['cooldown']}s cool-down")
    L.append(f"- **Baseline:** the same attacks against the deterministic mock LLM")
    L.append("")
    L.append("## Summary")
    L.append("")
    L.append("| | Live model | Mock baseline |")
    L.append("|---|---|---|")
    L.append(f"| Attacks defended | **{live_t['defended']} / {live_t['attacks']}** | {mock_t['defended']} / {mock_t['attacks']} |")
    L.append(f"| Findings as scored by the suite | {len(live_t['findings'])} | {len(mock_t['findings'])} |")
    L.append(f"| …of which refused outright, $0 paid (stricter than the test expected) | {len(live_t['stricter'])} | {len(mock_t['stricter'])} |")
    L.append(f"| **Real findings** (model was talked into the wrong outcome) | **{len(live_t['real'])}** | {len(mock_t['real'])} |")
    L.append(f"| Guardrail failures | **{len(live_t['guardrail_failures'])}** | {len(mock_t['guardrail_failures'])} |")
    L.append(f"| Money paid out across all attacks (sandbox) | ${live_t['paid']:.2f} | ${mock_t['paid']:.2f} |")
    L.append("")
    L.append(f"Of {len(live)} attacks, **{len(pure)} were decided entirely by {meta['model']}**, "
             f"{len(no_llm)} were stopped before any model call (pure guardrail checks), and "
             f"**{len(contaminated)} still fell back to the mock** after all retries"
             + (" (their outcome reflects the mock, not the live model)." if contaminated else "."))
    if lat:
        L.append(f"Time per model-decided attack: median {statistics.median(lat):.1f}s, "
                 f"min {min(lat):.1f}s, max {max(lat):.1f}s.")
    L.append("")

    diffs = [r for r in live if r["id"] in mock_by_id and not r["fallbacks"] and r["llm_calls"]
             and (r["defended"], r["final_status"]) != (mock_by_id[r["id"]]["defended"], mock_by_id[r["id"]]["final_status"])]
    L.append("## Where the live model differs from the mock")
    L.append("")
    if diffs:
        L.append("| Attack | Category | Live model | Mock | Live model's rationale |")
        L.append("|---|---|---|---|---|")
        for r in diffs:
            m = mock_by_id[r["id"]]
            why = r["proposals"][-1]["rationale"] if r["proposals"] else ""
            L.append(f"| {r['id']} {esc(r['title'])} | {r['category']} | {r['outcome']} ({r['final_status']}, ${r['paid']:.2f}) "
                     f"| {m['outcome']} ({m['final_status']}) | {esc(why)} |")
    else:
        L.append("No differences among attacks the live model decided.")
    L.append("")

    stricter = [r for r in live if r.get("stricter_than_expected")]
    if stricter:
        L.append("## Refused outright (scored as findings, but safe)")
        L.append("")
        L.append("These attacks expected the model to be fooled and the guardrail to catch it (status ESCALATED). "
                 "The live model refused on its own, so the case was DENIED with nothing paid. "
                 "The suite scores that as a mismatch; it is not a breach.")
        L.append("")
        for r in stricter:
            why = r["proposals"][-1]["rationale"] if r["proposals"] else ""
            L.append(f"- **{r['id']}** {r['title']}: \"{esc(why)}\"")
        L.append("")

    L.append("## Real findings")
    L.append("")
    found = [r for r in live if not r["defended"] and not r.get("stricter_than_expected")]
    if found:
        for r in found:
            L.append(f"### {r['id']}: {r['title']}")
            L.append(f"- Category: `{r['category']}`  ·  Final status: `{r['final_status']}` (expected {', '.join(r['expected_status_in'])})")
            L.append(f"- Paid out: ${r['paid']:.2f} (allowed: ${r['max_paid_allowed']:.2f})"
                     + ("  ·  **decided by mock fallback**" if r["fallbacks"] else ""))
            for p in r["proposals"]:
                L.append(f"- Model proposed **{p['action']} ${float(p['amount'] or 0):.2f}**: \"{esc(p['rationale'])}\"")
            L.append("")
    else:
        L.append("None. The model was not talked into a wrong outcome in any attack.")
        L.append("")

    if contaminated:
        L.append("## Attacks the live model never answered")
        L.append("")
        for r in contaminated:
            L.append(f"- {r['id']} {r['title']}: {esc(r['fallbacks'][0])}")
        L.append("")

    L.append("## All attacks")
    L.append("")
    L.append("| Attack | Category | Live outcome | Status | Paid | Model calls | Decided by | Secs | Mock outcome |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in live:
        m = mock_by_id.get(r["id"], {})
        by = "guardrail only" if not r["llm_calls"] else ("mock (fallback)" if r["fallbacks"] else meta["model"])
        outcome = "REFUSED (stricter)" if r.get("stricter_than_expected") else r["outcome"]
        L.append(f"| {r['id']} | {r['category']} | {outcome} | {r['final_status']} | ${r['paid']:.2f} "
                 f"| {r['llm_calls']} | {by} | {r['secs']} | {m.get('outcome', '')} |")
    L.append("")
    L.append("Raw per-attack data, including every model rationale, is in `results.json` next to this file.")
    (out_dir / "report.md").write_text("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--delay", type=float, default=20.0, help="seconds to pause after each attack that called the model")
    ap.add_argument("--retry-rounds", type=int, default=2, help="re-run rounds for attacks that fell back to the mock")
    ap.add_argument("--cooldown", type=float, default=60.0, help="seconds to wait before each re-run round")
    ap.add_argument("--max-retries", type=int, default=6, help="busy (429/503) retries per model call")
    ap.add_argument("--only", nargs="*", help="limit to these attack ids, e.g. RT-04-a RT-05-b")
    ap.add_argument("--rebuild", metavar="RUN_DIR", help="rewrite report.md from an existing run's results.json (no model calls)")
    args = ap.parse_args()

    if args.rebuild:
        out_dir = Path(args.rebuild)
        data = json.loads((out_dir / "results.json").read_text())
        write_report(out_dir, data["meta"], data["live"], data["mock_baseline"])
        print(f"rebuilt: {out_dir / 'report.md'}")
        return

    config.GEMINI_MAX_RETRIES = args.max_retries
    # Keep sandbox data out of the real data/ folder.
    tmp = Path(tempfile.mkdtemp(prefix="redteam_live_"))
    config.CHROMA_PERSIST_DIR = tmp / "chroma"

    provider = config.LLM_PROVIDER
    model = model_label()
    if model == "mock":
        sys.exit("The configured provider resolves to the mock LLM (no key, or CHANGSUPPORT_LLM_PROVIDER unset). "
                 "Load .env.local first.")

    started = datetime.now()
    stamp = started.strftime("%Y-%m-%d_%H%M%S")
    out_dir = config.ROOT_DIR / "redteam" / "results" / "live" / f"{model}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    attacks = load_attacks()
    if args.only:
        attacks = [x for x in attacks if x[2]["id"] in args.only]

    # 1. Mock baseline
    config.LLM_PROVIDER = "mock"
    mock = [run_one(rt, a) for rt, _, a in attacks]
    config.LLM_PROVIDER = provider
    print(f"Mock baseline: {sum(r['defended'] for r in mock)}/{len(mock)} defended", flush=True)

    # 2. Live model, paced
    print(f"Live run: {model} (thinking: {config.GEMINI_THINKING_LEVEL or 'default'}), "
          f"{len(attacks)} attacks, {args.delay}s pacing -> {out_dir.relative_to(config.ROOT_DIR)}", flush=True)
    live = {}
    order = [a["id"] for _, _, a in attacks]
    by_id = {a["id"]: (rt, a) for rt, _, a in attacks}

    def save():
        rows = [live[i] for i in order if i in live]
        (out_dir / "results.json").write_text(json.dumps(
            {"model": model, "provider": provider, "started": started.isoformat(timespec="seconds"),
             "live": rows, "mock_baseline": mock}, indent=2))

    def attempt(ids, label):
        for n, aid in enumerate(ids, 1):
            rt, a = by_id[aid]
            r = run_one(rt, a)
            r["attempt"] = label
            live[aid] = r
            fb = f" FALLBACK ({r['fallbacks'][0][:60]})" if r["fallbacks"] else ""
            print(f"[{label} {n}/{len(ids)}] {aid:<9} {r['category']:<11} {r['outcome']:<16} "
                  f"{r['final_status']:<10} paid=${r['paid']:<6} calls={r['llm_calls']} {r['secs']}s{fb}", flush=True)
            save()
            if r["llm_calls"]:
                time.sleep(args.delay)

    attempt(order, "pass1")
    for rnd in range(1, args.retry_rounds + 1):
        redo = [i for i in order if live[i]["fallbacks"]]
        if not redo:
            break
        print(f"Re-run round {rnd}: {len(redo)} attack(s) fell back to the mock; cooling down {args.cooldown}s", flush=True)
        time.sleep(args.cooldown)
        attempt(redo, f"retry{rnd}")

    finished = datetime.now()
    meta = {
        "model": model, "provider": provider,
        "started": started.strftime("%Y-%m-%d %H:%M:%S"), "finished": finished.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_min": round((finished - started).total_seconds() / 60, 1),
        "thinking": config.GEMINI_THINKING_LEVEL or "model default (on)",
        "timeout": config.GEMINI_TIMEOUT, "max_retries": args.max_retries,
        "delay": args.delay, "retry_rounds": args.retry_rounds, "cooldown": args.cooldown,
    }
    save()
    data = json.loads((out_dir / "results.json").read_text())
    data["meta"] = meta
    (out_dir / "results.json").write_text(json.dumps(data, indent=2))
    write_report(out_dir, meta, [live[i] for i in order], mock)
    print(f"done: {out_dir.relative_to(config.ROOT_DIR)}/report.md", flush=True)


if __name__ == "__main__":
    main()

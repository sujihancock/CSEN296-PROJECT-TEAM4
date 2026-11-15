"use strict";
// ---------------------------------------------------------------- Operations Overview
// Read-only supervisor view of system state. Reads with the supervisor key,
// refreshes every 15s while the tab is visible, and renders each panel
// independently so one failing endpoint never blanks the whole page.
const OV_KEY = "supervisor-key";
const OV_REFRESH_MS = 15000;
const OV_STATUSES = ["APPROVED", "DENIED", "ESCALATED", "UNDER_REVIEW"];
const OV_STATUS_META = {
  APPROVED: { label: "Approved", color: "var(--semantic-success)" },
  DENIED: { label: "Denied", color: "var(--semantic-danger)" },
  ESCALATED: { label: "Escalated", color: "var(--semantic-warning)" },
  UNDER_REVIEW: { label: "In progress", color: "var(--accent-primary)" },
};
const AGENT_LABELS = {
  IntakeAgent: "Intake", RecordsAgent: "Records", RefundsAgent: "Refunds",
  EmailAgent: "Email", ReportingAgent: "Reporting",
};
const MONEY_TOOLS = new Set(["bank.disburse"]);
let ovTimer = null;
let ovAuditFilter = "notable";
let ovLastLogs = [];
let ovLoading = false;

async function loadOverview() {
  if (ovLoading) return;
  ovLoading = true;
  try {
    const [guard, verify, logs, vstats, llm, ...byStatus] = await Promise.all([
      settle(api("GET", "/guardrails/status", null, OV_KEY)),
      settle(api("GET", "/logs/verify", null, OV_KEY)),
      settle(api("GET", "/logs", null, OV_KEY)),
      settle(api("GET", "/policies/vector-stats", null, OV_KEY)),
      settle(fetch("/llm/status").then(r => r.ok ? r.json() : Promise.reject(new Error(`LLM status ${r.status}`)))),
      ...OV_STATUSES.map(s => settle(api("GET", `/supervisor/cases?status=${s}`, null, OV_KEY))),
    ]);

    const casesOk = byStatus.every(r => r.ok);
    const counts = Object.fromEntries(OV_STATUSES.map((s, i) => [s, byStatus[i].ok ? byStatus[i].v.length : 0]));
    const allCases = casesOk ? byStatus.flatMap(r => r.v) : null;
    const escalated = byStatus[OV_STATUSES.indexOf("ESCALATED")];

    renderOvAuthority(guard);
    renderOvSpend(guard);
    renderOvQueueCell(escalated);
    renderOvChain(verify, logs);
    renderOvLlm(llm);
    renderOvVector(vstats);
    renderOvBudget(guard);
    renderOvDecisions(casesOk ? counts : null, allCases, logs, byStatus.find(r => !r.ok)?.e);
    renderOvCases(allCases, byStatus.find(r => !r.ok)?.e);
    renderOvQueue(escalated);
    if (logs.ok) ovLastLogs = logs.v.slice().reverse();
    renderOvAudit(logs.ok ? null : logs.e);
    renderOvScopes(guard);

    const t = new Date();
    $("ovUpdated").textContent = `Updated ${t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })} · refreshes every 15s`;
  } finally {
    ovLoading = false;
  }
}

function renderOvAuthority(guard) {
  const cell = $("ovAuthority");
  if (!guard.ok) { ovCell("ovAuthority", "Agent authority", "Unknown", guard.e.message, { dot: "bad", subClass: "is-bad" }); return; }
  const revoked = guard.v.kill_switch_active;
  cell.classList.toggle("is-revoked", revoked);
  const btn = el("button", { type: "button", class: `btn btn-sm ${revoked ? "btn-primary" : "btn-danger"}` },
    revoked ? "Restore authority" : "Engage kill switch");
  btn.addEventListener("click", () => {
    const engage = !revoked;
    const msg = engage
      ? "Engage the kill switch?\n\nEvery agent loses payout authority immediately. New refund requests fail closed until a supervisor restores authority."
      : "Restore agent authority?\n\nAgents resume autonomous refunds within the $100 per-transaction and $500 weekly caps.";
    if (!confirm(msg)) return;
    busy(btn, async () => {
      await api("POST", "/supervisor/kill-switch",
        { active: engage, reason: engage ? "Engaged from operations overview" : "Restored from operations overview" }, OV_KEY);
      toast(engage ? "Kill switch engaged. Agent payout authority revoked." : "Agent authority restored.");
      await Promise.all([loadOverview(), loadHealth()]);
    });
  });
  ovCell("ovAuthority", "Agent authority",
    revoked ? "Revoked" : "Active",
    revoked ? "Kill switch engaged · requests fail closed" : "Autonomous refunds within caps",
    { dot: revoked ? "bad" : "ok", subClass: revoked ? "is-bad" : null, extra: [btn] });
}

function renderOvSpend(guard) {
  if (!guard.ok) { ovCell("ovSpendCell", "Autonomous spend · 7d", "—", guard.e.message, { subClass: "is-bad" }); return; }
  const g = guard.v;
  const pct = 100 * g.weekly_autonomous_total / g.max_weekly_autonomous_refunds;
  const lvl = spendLevel(pct);
  ovCell("ovSpendCell", "Autonomous spend · 7d", money(g.weekly_autonomous_total),
    `${Math.round(pct)}% of ${money(g.max_weekly_autonomous_refunds)} cap`,
    { mono: true, subClass: lvl === "ok" ? null : `is-${lvl}` });
}

function renderOvQueueCell(res) {
  if (!res.ok) { ovCell("ovQueueCell", "Awaiting review", "—", res.e.message, { subClass: "is-bad" }); return; }
  const n = res.v.length;
  const held = res.v.reduce((a, c) => a + (c.amount_requested || 0), 0);
  ovCell("ovQueueCell", "Awaiting review",
    n === 0 ? "Queue clear" : `${n} ${n === 1 ? "case" : "cases"}`,
    n === 0 ? "No escalations waiting" : `${money(held)} held for review`,
    { dot: n ? "warn" : "ok", subClass: n ? "is-warn" : null });
}

function renderOvChain(verify, logs) {
  if (!verify.ok) { ovCell("ovChainCell", "Audit chain", "Unverified", verify.e.message, { dot: "bad", subClass: "is-bad" }); return; }
  const v = verify.v;
  const last = logs.ok && logs.v.length ? logs.v[logs.v.length - 1] : null;
  if (v.valid) {
    ovCell("ovChainCell", "Audit chain", "Intact",
      `${v.entries} entries${last ? ` · head ${last.hash.slice(0, 8)}` : ""}`,
      { dot: "ok", title: last ? `Head hash ${last.hash}` : null });
  } else {
    ovCell("ovChainCell", "Audit chain", "Tampered", `Hash mismatch at entry #${v.first_bad_id}`, { dot: "bad", subClass: "is-bad" });
  }
}

function renderOvLlm(llm) {
  if (!llm.ok) { ovCell("ovLlmCell", "Reasoning engine", "Unknown", llm.e.message, { subClass: "is-bad" }); return; }
  const l = llm.v;
  const isMock = l.active_class === "MockLLM";
  const fellBack = l.configured_provider === "ollama" && isMock;
  ovCell("ovLlmCell", "Reasoning engine",
    isMock ? "Mock LLM" : l.ollama_model,
    fellBack ? "Ollama unreachable · using mock" : isMock ? "Deterministic test model" : `Ollama · ${l.ollama_reachable ? "reachable" : "unreachable"}`,
    { dot: fellBack ? "warn" : "ok", subClass: fellBack ? "is-warn" : null });
}

function renderOvVector(vs) {
  if (!vs.ok) { ovCell("ovVectorCell", "Policy index", "—", vs.e.message, { subClass: "is-bad" }); return; }
  const n = vs.v.indexed_vectors;
  ovCell("ovVectorCell", "Policy index", `${n} ${n === 1 ? "chunk" : "chunks"}`,
    `ChromaDB · ${vs.v.collection}`, { dot: n ? "ok" : "warn", subClass: n ? null : "is-warn" });
}

function renderOvBudget(guard) {
  if (!guard.ok) { ovFail("ovBudget", guard.e.message); $("ovCapNote").textContent = "—"; return; }
  const g = guard.v;
  const cap = g.max_weekly_autonomous_refunds;
  const used = g.weekly_autonomous_total;
  const pct = Math.min(100, 100 * used / cap);
  const lvl = spendLevel(100 * used / cap);
  const headroom = Math.max(0, cap - used);
  $("ovCapNote").textContent = "Rolling 7 days";

  const fill_ = el("div", { class: `ov-gauge-fill${lvl === "ok" ? "" : " " + lvl}` });
  const ticks = [25, 50, 75].map(p => el("span", { class: "ov-gauge-tick", style: `left:${p}%`, "aria-hidden": "true" }));
  fill("ovBudget",
    el("div", { class: "ov-spend-figures" },
      el("span", { class: "ov-spend-used" }, money(used)),
      el("span", { class: "ov-spend-of" }, "of ", el("b", {}, money(cap)), " weekly cap"),
      el("span", { class: "ov-spend-headroom" }, el("b", {}, money(headroom)), " headroom")),
    el("div", { class: "ov-gauge", role: "meter", "aria-valuemin": "0", "aria-valuemax": String(cap), "aria-valuenow": String(used),
      "aria-label": `Autonomous spend ${money(used)} of ${money(cap)} weekly cap` }, fill_, ...ticks),
    el("div", { class: "ov-gauge-scale", "aria-hidden": "true" },
      [0, 0.25, 0.5, 0.75, 1].map(f => el("span", { style: `left:${f * 100}%` }, `$${Math.round(cap * f)}`))),
    el("dl", { class: "ov-rules" },
      el("div", { class: "ov-rule" }, el("dt", {}, "Per-transaction cap"), el("dd", { class: "font-mono" }, money(g.max_autonomous_refund_per_tx))),
      el("div", { class: "ov-rule" }, el("dt", {}, "Over a cap"), el("dd", {}, "Escalates to supervisor")),
      el("div", { class: "ov-rule" }, el("dt", {}, "Kill switch"), el("dd", {}, g.kill_switch_active ? "Engaged · fail closed" : "Released"))));
  requestAnimationFrame(() => { fill_.style.width = `${pct}%`; });
}

function renderOvDecisions(counts, cases, logs, err) {
  if (!counts) { ovFail("ovDecisions", err ? err.message : "Could not load cases."); $("ovDecisionNote").textContent = "—"; return; }
  const total = OV_STATUSES.reduce((a, s) => a + counts[s], 0);
  const decided = cases.filter(c => c.status === "APPROVED" || c.status === "DENIED");
  const autonomous = decided.filter(c => c.autonomous).length;
  const blocks = logs.ok ? logs.v.filter(x => x.status === "BLOCKED").length : null;
  const parts = [];
  if (decided.length) parts.push(`${autonomous} of ${decided.length} closed by agents`);
  if (blocks != null) parts.push(`${blocks} guardrail ${blocks === 1 ? "block" : "blocks"}`);
  $("ovDecisionNote").textContent = total ? parts.join(" · ") : "No cases yet";

  if (!total) {
    fill("ovDecisions", el("div", { class: "ov-empty" },
      el("p", {}, "No refund cases have been filed."),
      el("p", {}, "Run a benchmark scenario in the sandbox and its outcome lands here."),
      el("button", { type: "button", class: "btn btn-sm btn-secondary", "data-goto": "sandbox" }, "Open sandbox")));
    return;
  }
  const summary = OV_STATUSES.map(s => `${counts[s]} ${OV_STATUS_META[s].label.toLowerCase()}`).join(", ");
  fill("ovDecisions",
    el("div", { class: "ov-mix", role: "img", "aria-label": `Case outcomes: ${summary}` },
      OV_STATUSES.filter(s => counts[s]).map(s => el("span", {
        class: "ov-mix-seg", title: `${OV_STATUS_META[s].label}: ${counts[s]}`,
        style: `flex-grow:${counts[s]};background:${OV_STATUS_META[s].color}` }))),
    el("div", { class: "ov-legend" }, OV_STATUSES.map(s => el("div", { class: "ov-legend-item" },
      el("span", { class: "ov-legend-key" },
        el("span", { class: "ov-legend-swatch", style: `background:${OV_STATUS_META[s].color}` }), OV_STATUS_META[s].label),
      el("span", { class: "ov-legend-val" }, counts[s], el("small", {}, `${Math.round(100 * counts[s] / total)}%`))))));
}

function renderOvCases(cases, err) {
  if (!cases) { ovFail("ovCases", err ? err.message : "Could not load cases."); return; }
  if (!cases.length) {
    fill("ovCases", el("div", { class: "ov-empty" }, el("p", {}, "Cases appear here as customers file refund claims.")));
    return;
  }
  const recent = cases.slice().sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at))).slice(0, 8);
  const decider = (c) => c.decided_by ? (c.autonomous ? `${c.decided_by.replace(/Agent$/, "")} agent` : c.decided_by) : "—";
  fill("ovCases", el("div", { class: "table-container" }, table([
    { label: "Case", key: "id", mono: true },
    { label: "Customer", key: "customer_id", mono: true },
    { label: "Order", key: "order_id", mono: true },
    { label: "Amount", render: r => money(r.amount_requested), num: true },
    { label: "Status", render: r => badge(r.status) },
    { label: "Decided by", render: decider },
    { label: "Updated", render: r => timeEl(r.updated_at) },
  ], recent)));
}

function renderOvQueue(res) {
  if (!res.ok) { ovFail("ovQueue", res.e.message); return; }
  const cases = res.v;
  if (!cases.length) {
    fill("ovQueue", el("div", { class: "ov-empty" },
      el("p", {}, "Nothing is waiting on a supervisor."),
      el("p", {}, "Claims over $100, or past the weekly cap, stop here for a human decision.")));
    return;
  }
  const DAY = 86400000;
  fill("ovQueue", el("ul", { class: "ov-queue" }, cases.slice(0, 5).map(c => {
    const age = Date.now() - (parseTs(c.created_at)?.getTime() || Date.now());
    return el("li", { class: "ov-queue-item" },
      el("span", { class: "ov-queue-id" }, c.id),
      el("span", { class: "ov-queue-amt" }, money(c.amount_requested)),
      el("span", { class: "ov-queue-reason", title: c.reason }, `${c.customer_id} · ${c.order_id} · ${c.reason}`),
      el("span", { class: `ov-queue-age${age > DAY ? " is-stale" : ""}` }, age < 60000 ? "just arrived" : `waiting ${ago(c.created_at).replace(/ ago$/, "")}`));
  }), cases.length > 5 ? el("li", { class: "ov-queue-item" }, el("span", { class: "ov-queue-reason" }, `+${cases.length - 5} more in the queue`)) : null));
}

function renderOvAudit(err) {
  if (err) { fill("ovAudit", el("li", {}, el("p", { class: "ov-error" }, err.message))); return; }
  const notable = (e) => !e.action.startsWith("TOOL_CALL:") || e.status !== "ALLOWED";
  const rows = (ovAuditFilter === "notable" ? ovLastLogs.filter(notable) : ovLastLogs).slice(0, 8);
  if (!rows.length) {
    fill("ovAudit", el("li", { class: "ov-empty" }, ovAuditFilter === "notable"
      ? "No decisions, blocks or kill-switch changes yet. Switch to All to see routine tool calls."
      : "The ledger is empty. Every tool call and decision is appended here."));
    return;
  }
  fill("ovAudit", rows.map(e => {
    const { title, tone, note } = describeAudit(e);
    return el("li", { class: "ov-feed-item" },
      el("span", { class: `ov-feed-mark ${tone || ""}`, "aria-hidden": "true" }),
      el("div", {},
        el("div", { class: "ov-feed-title" }, title),
        el("div", { class: "ov-feed-meta" },
          el("span", { class: "font-mono" }, e.actor),
          e.case_id ? [" · ", el("span", { class: "font-mono" }, e.case_id)] : null,
          note ? ` · ${note}` : null)),
      timeEl(e.ts, "ov-feed-time"));
  }));
}

function renderOvScopes(guard) {
  if (!guard.ok) { fill("ovScopes", el("p", { class: "ov-error" }, guard.e.message)); return; }
  const perms = guard.v.agent_permissions || {};
  fill("ovScopes", Object.entries(perms).map(([agent, toolsList]) => el("div", { class: "ov-scope" },
    el("dt", {}, AGENT_LABELS[agent] || agent),
    el("dd", {}, toolsList.map(t => el("span", { class: `ov-tool${MONEY_TOOLS.has(t) ? " is-money" : ""}`, title: MONEY_TOOLS.has(t) ? "Moves money; gated by guardrails" : null }, t))))));
}

document.querySelectorAll("[data-audit-filter]").forEach(b => b.addEventListener("click", () => {
  ovAuditFilter = b.dataset.auditFilter;
  document.querySelectorAll("[data-audit-filter]").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
  renderOvAudit();
}));

const ovRefreshBtn = $("ovRefresh");
if (ovRefreshBtn) ovRefreshBtn.addEventListener("click", () => busy(ovRefreshBtn, loadOverview));

function ovTick() {
  clearTimeout(ovTimer);
  ovTimer = setTimeout(async () => {
    if (currentTab === "overview" && !document.hidden) await loadOverview();
    ovTick();
  }, OV_REFRESH_MS);
}
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && currentTab === "overview") loadOverview();
});
ovTick();

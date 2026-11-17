"use strict";
// ---------------------------------------------------------------- Supervisor queue
// Human-in-the-loop work: decide escalated claims, operate the kill switch,
// and run the red-team harness. Reads and writes with the picked account's
// key so a customer or admin pick shows the supervisor-only 403s.

const SU_DAY = 86400000;
// Cheap tell for the reviewer, not a defence: the guardrail already held the claim.
const SU_INJECTION = /ignore (all|previous|prior)|system override|you are (now )?authori[sz]ed|disregard|master admin|new instructions/i;
let suLoading = false;
let suCustomers = {};
const rtState = new Map();   // "rt-01" -> result payload
let rtSelected = null;
let rtTests = null;
let rtRunning = false;

async function loadSupervisor() {
  if (suLoading) return;
  suLoading = true;
  const key = apiKey;
  roleNote("suRoleNote", ["supervisor"], "Deciding claims and the kill switch are supervisor-only, so the API rejects them with 403.", "supervisor-key");

  try {
    const [guard, escalated, approved, denied, logs, customers] = await Promise.all([
      settle(api("GET", "/guardrails/status", null, key)),
      settle(api("GET", "/supervisor/cases?status=ESCALATED", null, key)),
      settle(api("GET", "/supervisor/cases?status=APPROVED", null, key)),
      settle(api("GET", "/supervisor/cases?status=DENIED", null, key)),
      settle(api("GET", "/logs", null, key)),
      settle(api("GET", "/demo/customers", null, key)),
    ]);
    if (customers.ok) suCustomers = Object.fromEntries(customers.v.map(c => [c.id, c]));
    const humanOk = approved.ok && denied.ok;
    const human = humanOk ? [...approved.v, ...denied.v].filter(c => !c.autonomous) : null;

    renderSuBand(guard, escalated, human, humanOk ? null : (approved.ok ? denied.e : approved.e));
    renderSuQueue(escalated);
    renderSuHistory(human, humanOk ? null : (approved.ok ? denied.e : approved.e));
    renderSuKillSwitch(guard, logs);
    renderSuLimits(guard);
    loadRedTeamTests();
    $("suUpdated").textContent = `Updated ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  } finally {
    suLoading = false;
  }
}

const custName = (id) => suCustomers[id]?.name || id;

function renderSuBand(guard, escalated, human, humanErr) {
  if (escalated.ok) {
    const cases = escalated.v;
    const held = cases.reduce((a, c) => a + (c.amount_requested || 0), 0);
    ovCell("suQueueCell", "Awaiting review", cases.length ? `${cases.length} ${cases.length === 1 ? "claim" : "claims"}` : "Queue clear",
      cases.length ? `${money(held)} held, not paid` : "Nothing waiting on you",
      { dot: cases.length ? "warn" : "ok", subClass: cases.length ? "is-warn" : null });
    const oldest = cases[0];   // API orders by created_at ascending
    if (oldest) {
      const age = Date.now() - (parseTs(oldest.created_at)?.getTime() || Date.now());
      ovCell("suOldestCell", "Oldest waiting", ago(oldest.created_at).replace(/ ago$/, ""),
        `${oldest.id} · ${money(oldest.amount_requested)}`, { subClass: age > SU_DAY ? "is-warn" : null, dot: age > SU_DAY ? "warn" : null });
    } else {
      ovCell("suOldestCell", "Oldest waiting", "—", "No backlog");
    }
  } else {
    ovCell("suQueueCell", "Awaiting review", "—", escalated.e.message, { subClass: "is-bad" });
    ovCell("suOldestCell", "Oldest waiting", "—", escalated.e.message, { subClass: "is-bad" });
  }

  if (human) {
    const a = human.filter(c => c.status === "APPROVED").length;
    ovCell("suDecidedCell", "Decided by supervisors", String(human.length),
      human.length ? `${a} approved · ${human.length - a} denied` : "No overrides yet", { mono: true });
  } else {
    ovCell("suDecidedCell", "Decided by supervisors", "—", humanErr?.message || "Unavailable", { subClass: "is-bad" });
  }

  if (guard.ok) {
    const revoked = guard.v.kill_switch_active;
    ovCell("suAuthorityCell", "Agent authority", revoked ? "Revoked" : "Active",
      revoked ? "Kill switch engaged · requests fail closed" : "Agents decide claims within caps",
      { dot: revoked ? "bad" : "ok", subClass: revoked ? "is-bad" : null });
    $("suAuthorityCell").classList.toggle("is-revoked", revoked);
  } else {
    ovCell("suAuthorityCell", "Agent authority", "—", guard.e.message, { subClass: "is-bad" });
  }
}

function renderSuQueue(res) {
  if (!res.ok) { ovFail("escalated", res.e.message); $("suQueueNote").textContent = "—"; return; }
  const cases = res.v;
  $("suQueueNote").textContent = cases.length ? "Oldest first" : "";
  if (!cases.length) {
    fill("escalated", el("div", { class: "ov-empty" },
      el("p", {}, "Nothing is waiting on a supervisor."),
      el("p", {}, "Claims over the $100 per-transaction cap, or past the $500 weekly cap, stop here. Try the High-Value scenario in the sandbox."),
      el("button", { type: "button", class: "btn btn-sm btn-secondary", "data-goto": "sandbox" }, "Open sandbox")));
    return;
  }
  fill("escalated", el("div", { class: "pg-cases" }, cases.map(suCaseCard)));
}

function suCaseCard(c) {
  const name = custName(c.customer_id);
  const age = Date.now() - (parseTs(c.created_at)?.getTime() || Date.now());
  const injected = SU_INJECTION.test(c.reason || "");
  const commentId = `suComment-${c.id}`;
  const comment = el("textarea", { id: commentId, rows: "2", placeholder: "Why? Goes in the audit log and the beneficiary's email" });
  const approve = el("button", { type: "button", class: "btn btn-sm btn-primary" }, `Approve and pay ${money(c.amount_requested)}`);
  const deny = el("button", { type: "button", class: "btn btn-sm btn-danger" }, "Deny");

  const decide = (yes, btn) => {
    const msg = yes
      ? `Approve ${c.id} and pay ${money(c.amount_requested)} to ${name} now?\n\nThe money moves immediately and the decision is written to the audit log.`
      : `Deny ${c.id} for ${name}?\n\nThe beneficiary is emailed the decision.`;
    if (!confirm(msg)) return;
    busy(btn, async () => {
      approve.disabled = deny.disabled = true;
      try {
        await api("POST", `/supervisor/cases/${encodeURIComponent(c.id)}/decision`,
          { approve: yes, comment: comment.value.trim() }, apiKey);
        toast(`${c.id} ${yes ? `approved · ${money(c.amount_requested)} paid` : "denied"}`);
        await Promise.all([loadSupervisor(), loadHealth()]);
      } finally {
        approve.disabled = deny.disabled = false;
      }
    });
  };
  approve.addEventListener("click", () => decide(true, approve));
  deny.addEventListener("click", () => decide(false, deny));

  return el("article", { class: "pg-case", "aria-label": `${c.id}, ${money(c.amount_requested)} for ${name}` },
    el("header", { class: "pg-case-head" },
      el("div", {},
        el("div", { class: "pg-case-id" }, c.id),
        el("div", { class: "pg-case-who" }, name, " · ", el("span", { class: "font-mono" }, c.customer_id), " · ", el("span", { class: "font-mono" }, c.order_id))),
      el("div", { class: "pg-case-amt" }, money(c.amount_requested))),
    el("p", { class: "pg-case-guard" },
      el("span", { class: "ov-feed-mark warn", "aria-hidden": "true" }),
      el("span", {}, c.decision_reason || "Held by guardrail")),
    el("figure", { class: "pg-case-quote" },
      el("figcaption", {}, "Beneficiary wrote", injected ? el("span", { class: "chip danger" }, "Looks like prompt injection") : null,
        el("span", { class: `pg-case-age${age > SU_DAY ? " is-stale" : ""}` }, `filed ${ago(c.created_at)}`)),
      el("blockquote", {}, c.reason || "—")),
    el("label", { class: "pg-case-label", for: commentId }, "Decision note"),
    comment,
    el("div", { class: "pg-case-actions" }, approve, deny));
}

function renderSuHistory(human, err) {
  if (!human) { ovFail("suHistory", err?.message || "Unavailable"); $("suHistoryNote").textContent = "—"; return; }
  $("suHistoryNote").textContent = human.length ? `${human.length} total` : "";
  if (!human.length) {
    fill("suHistory", el("div", { class: "ov-empty" }, el("p", {}, "Claims you approve or deny from the queue are listed here.")));
    return;
  }
  const rows = human.sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at))).slice(0, 8);
  fill("suHistory", el("div", { class: "table-container" }, table([
    { label: "Claim", key: "id", mono: true },
    { label: "Beneficiary", render: r => custName(r.customer_id) },
    { label: "Amount", render: r => money(r.amount_requested), num: true },
    { label: "Decision", render: r => badge(r.status) },
    { label: "Note", render: r => el("span", { class: "pg-clamp", title: r.decision_reason || "" }, r.decision_reason || "—") },
    { label: "By", key: "decided_by", mono: true },
    { label: "When", render: r => timeEl(r.updated_at) },
  ], rows)));
}

function renderSuKillSwitch(guard, logs) {
  if (!guard.ok) { ovFail("suKs", guard.e.message); $("suKsNote").textContent = "—"; return; }
  const revoked = guard.v.kill_switch_active;
  $("suKsPanel").classList.toggle("is-revoked", revoked);
  $("suKsNote").textContent = revoked ? "Engaged" : "Released";
  const reason = el("input", { type: "text", id: "ksReason", placeholder: revoked ? "Why it's safe to resume" : "What you saw, e.g. a burst of injection attempts", "aria-label": "Reason for the audit log" });
  const btn = el("button", { type: "button", class: `btn btn-sm ${revoked ? "btn-primary" : "btn-danger"}` },
    revoked ? "Restore agent authority" : "Engage kill switch");
  btn.addEventListener("click", () => {
    const engage = !revoked;
    const msg = engage
      ? "Engage the kill switch?\n\nEvery agent loses payout authority immediately. New reimbursement requests fail closed until a supervisor restores authority."
      : "Restore agent authority?\n\nAgents resume autonomous reimbursements within the $100 per-transaction and $500 weekly caps.";
    if (!confirm(msg)) return;
    busy(btn, async () => {
      await api("POST", "/supervisor/kill-switch", { active: engage, reason: reason.value.trim() }, apiKey);
      toast(engage ? "Kill switch on. Agents can't pay anything until it's turned off." : "Kill switch off. Agents can pay claims again.");
      await Promise.all([loadSupervisor(), loadHealth()]);
    });
  });

  const history = logs.ok ? logs.v.filter(e => e.action === "SET_KILL_SWITCH").slice(-4).reverse() : [];
  fill("suKs",
    el("p", { class: "pg-ks-state" },
      el("span", { class: `ov-dot ${revoked ? "bad" : "ok"}`, "aria-hidden": "true" }),
      revoked ? "Agents cannot pay anything. Every reimbursement request fails closed." : "Agents can pay claims up to the caps on their own."),
    el("div", { class: "pg-ks-form" }, reason, btn),
    history.length ? el("ol", { class: "ov-feed pg-ks-history", "aria-label": "Recent kill switch changes" }, history.map(e => el("li", { class: "ov-feed-item" },
      el("span", { class: `ov-feed-mark ${e.details?.active ? "bad" : "ok"}`, "aria-hidden": "true" }),
      el("div", {},
        el("div", { class: "ov-feed-title" }, e.details?.active ? "Engaged" : "Released"),
        el("div", { class: "ov-feed-meta" }, el("span", { class: "font-mono" }, e.actor), e.details?.reason ? ` · ${e.details.reason}` : "")),
      timeEl(e.ts, "ov-feed-time")))) : null);
}

function renderSuLimits(guard) {
  if (!guard.ok) { ovFail("suLimits", guard.e.message); return; }
  const g = guard.v;
  const pct = 100 * g.weekly_autonomous_total / g.max_weekly_autonomous_refunds;
  const lvl = spendLevel(pct);
  const bar = el("div", { class: `ov-gauge-fill${lvl === "ok" ? "" : " " + lvl}` });
  fill("suLimits",
    el("dl", { class: "pg-rules" },
      el("div", {}, el("dt", {}, "Per-claim cap"), el("dd", {}, "Anything over ", el("b", { class: "font-mono" }, money(g.max_autonomous_refund_per_tx)), " waits for you.")),
      el("div", {}, el("dt", {}, "Weekly cap"), el("dd", {}, "Once agents have paid ", el("b", { class: "font-mono" }, money(g.max_weekly_autonomous_refunds)), " in 7 days, every claim waits for you."))),
    el("div", { class: "pg-mini-gauge" },
      el("div", { class: "pg-mini-gauge-label" },
        el("span", {}, "Agents have paid ", el("b", { class: "font-mono" }, money(g.weekly_autonomous_total)), " this week"),
        el("span", { class: lvl === "ok" ? "" : `is-${lvl}` }, `${Math.round(pct)}%`)),
      el("div", { class: "ov-gauge", role: "meter", "aria-valuemin": "0", "aria-valuemax": String(g.max_weekly_autonomous_refunds),
        "aria-valuenow": String(g.weekly_autonomous_total), "aria-label": "Weekly autonomous spend" }, bar)));
  requestAnimationFrame(() => { bar.style.width = `${Math.min(100, pct)}%`; });
}

// ---------------------------------------------------------------- Red-team harness
async function loadRedTeamTests() {
  if (!["supervisor", "admin"].includes(persona()?.role)) {
    rtTests = null;
    fill("rtButtons", el("p", { class: "ov-error" }, "Rejected (403): the red-team harness needs a supervisor or admin account."));
    $("rtRunAll").disabled = true;
    return;
  }
  $("rtRunAll").disabled = rtRunning;
  if (!rtTests) {
    try { rtTests = await api("GET", "/tests", null, apiKey); }
    catch (e) { fill("rtButtons", el("p", { class: "ov-error" }, e.message)); return; }
  }
  renderRtGrid();
}

function rtTone(r) {
  if (!r) return "";
  if (r.summary.guardrail_failures.length) return "bad";
  if (r.summary.findings.length) return "warn";
  return "ok";
}

function renderRtGrid() {
  if (!rtTests) return;
  fill("rtButtons", rtTests.map(t => {
    const r = rtState.get(t);
    const tone = rtTone(r);
    const b = el("button", {
      type: "button", class: `pg-rt-tile ${tone}${rtSelected === t ? " is-selected" : ""}`,
      "aria-pressed": String(rtSelected === t), title: r ? r.title : "Not run yet",
    },
      el("span", { class: "pg-rt-id" }, el("span", { class: `ov-dot ${tone}`, "aria-hidden": "true" }), t.toUpperCase()),
      el("span", { class: "pg-rt-res" }, r ? `${r.summary.defended}/${r.summary.attacks} defended` : "Not run"));
    b.addEventListener("click", () => {
      if (rtRunning) return;
      if (rtState.has(t) && rtSelected !== t) { rtSelected = t; renderRtGrid(); renderRtDetail(); return; }
      runRt(t);
    });
    return b;
  }));
  renderRtSummary();
}

function renderRtSummary() {
  const box = $("rtSummary");
  const runs = [...rtState.values()];
  box.hidden = !runs.length;
  if (!runs.length) { $("rtNote").textContent = rtTests ? `${rtTests.length} suites · click one to run it` : ""; return; }
  const attacks = runs.reduce((a, r) => a + r.summary.attacks, 0);
  const defended = runs.reduce((a, r) => a + r.summary.defended, 0);
  const findings = runs.reduce((a, r) => a + r.summary.findings.length, 0);
  const failures = runs.reduce((a, r) => a + r.summary.guardrail_failures.length, 0);
  const paid = runs.reduce((a, r) => a + r.attacks.reduce((s, x) => s + (x.outcome === "ATTACK_SUCCEEDED" ? x.money_paid_out || 0 : 0), 0), 0);
  if (!rtRunning) $("rtNote").textContent = `${runs.length} of ${rtTests?.length ?? "?"} suites run`;
  const stat = (label, value, cls) => el("div", { class: "pg-rt-stat" }, el("span", {}, label), el("b", { class: cls || null }, value));
  fill("rtSummary",
    stat("Attacks defended", `${defended} / ${attacks}`),
    stat("Guardrail failures", String(failures), failures ? "is-bad" : "is-ok"),
    stat("LLM-layer findings", String(findings), findings ? "is-warn" : null),
    stat("Paid out by successful attacks", money(paid), paid ? "is-warn" : null));
}

function renderRtDetail() {
  const r = rtSelected && rtState.get(rtSelected);
  if (!r) { fill("rtResults"); return; }
  const s = r.summary;
  fill("rtResults", el("div", { class: "pg-rt-detail" },
    el("div", { class: "pg-rt-detail-head" },
      el("h3", {}, el("span", { class: "font-mono" }, r.rt), " · ", r.title),
      s.guardrail_failures.length ? el("span", { class: "badge b-bad" }, `${s.guardrail_failures.length} guardrail failure${s.guardrail_failures.length === 1 ? "" : "s"}`)
        : s.findings.length ? el("span", { class: "badge b-warn" }, `${s.findings.length} LLM finding${s.findings.length === 1 ? "" : "s"} · within guardrail limits`)
          : el("span", { class: "badge b-ok" }, "All defended")),
    el("div", { class: "table-container" }, table([
      { label: "Attack", key: "id", mono: true },
      { label: "Scenario", key: "title" },
      { label: "Layer", render: a => a.category === "guardrail" ? "Guardrail" : "LLM policy" },
      { label: "Outcome", render: a => badge(a.outcome) },
      { label: "Final", render: a => [badge(a.final_status), a.final_code ? el("div", { class: "font-mono pg-sub-code" }, a.final_code) : null] },
      { label: "Paid out", render: a => money(a.money_paid_out), num: true },
    ], r.attacks)),
    el("p", { class: "pg-rt-evidence" }, "Evidence written to ", el("code", {}, r.evidence_path))));
}

async function runRt(t) {
  rtRunning = true;
  $("rtRunAll").disabled = true;
  $("rtNote").textContent = `Running ${t.toUpperCase()}…`;
  try {
    const r = await api("POST", `/tests/${t}`, null, apiKey);
    rtState.set(t, r);
    rtSelected = t;
  } catch (e) {
    toast(e.message);
  } finally {
    rtRunning = false;
    $("rtRunAll").disabled = false;
    renderRtGrid();
    renderRtDetail();
  }
}

$("rtRunAll").addEventListener("click", async () => {
  if (!rtTests || rtRunning) return;
  rtRunning = true;
  const btn = $("rtRunAll");
  btn.disabled = true;
  try {
    for (const [i, t] of rtTests.entries()) {
      $("rtNote").textContent = `Running ${t.toUpperCase()} · ${i + 1} of ${rtTests.length}`;
      try { rtState.set(t, await api("POST", `/tests/${t}`, null, apiKey)); }
      catch (e) { toast(e.message); break; }
      renderRtGrid();
    }
    // Land on the first suite that found something; that's what a reviewer wants to see.
    rtSelected = rtTests.find(t => rtTone(rtState.get(t)) === "bad") || rtTests.find(t => rtTone(rtState.get(t)) === "warn") || rtTests[0];
  } finally {
    rtRunning = false;
    btn.disabled = false;
    renderRtGrid();
    renderRtDetail();
  }
});

document.addEventListener("persona-change", () => { rtTests = null; loadSupervisor(); });
$("suRefresh").addEventListener("click", (ev) => busy(ev.currentTarget, loadSupervisor));

loadSupervisor();

"use strict";
// ---------------------------------------------------------------- Policy index (admin)
// Policies are dual-written to SQLite and ChromaDB. The Refunds agent
// retrieves from ChromaDB, so whatever is indexed here can steer its proposal
// (RT-04 and RT-13 plant fake and injected policies). The guardrails still cap the payout.

let adPolicies = [];
let adLoading = false;

async function loadAdmin() {
  if (adLoading) return;
  adLoading = true;
  const key = apiKey;
  // Supervisors may read policies but not upload or reset; customers get nothing.
  roleNote("adRoleNote", ["admin"], persona()?.role === "supervisor"
    ? "Supervisors can read the index but not change it. Indexing or resetting returns 403."
    : "The policy index is for admins, so the API rejects these requests with 403.", "admin-key");

  try {
    const [policies, stats, outbox] = await Promise.all([
      settle(api("GET", "/policies", null, key)),
      settle(api("GET", "/policies/vector-stats", null, key)),
      settle(api("GET", "/api/email/outbox?limit=50", null, key)),
    ]);
    adPolicies = policies.ok ? policies.v : [];
    renderAdBand(policies, stats, outbox);
    renderAdPolicies(policies);
    renderAdOutbox(outbox);
    renderAdHints();
    $("adUpdated").textContent = `Updated ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  } finally {
    adLoading = false;
  }
}

function renderAdBand(policies, stats, outbox) {
  if (stats.ok) {
    const n = stats.v.indexed_vectors;
    const drift = policies.ok && n !== policies.v.length;
    ovCell("adVectorCell", "Indexed chunks", String(n),
      drift ? `${policies.v.length} policies in SQLite · out of sync` : `Collection ${stats.v.collection}`,
      { mono: true, dot: !n ? "warn" : drift ? "warn" : "ok", subClass: drift || !n ? "is-warn" : null });
  } else {
    ovCell("adVectorCell", "Indexed chunks", "—", stats.e.message, { subClass: "is-bad" });
  }
  if (policies.ok) {
    const custom = policies.v.filter(p => p.uploaded_by !== "seed").length;
    ovCell("adPolicyCell", "Policies", String(policies.v.length),
      custom ? `${custom} added by admins` : "Seed set only", { mono: true });
  } else {
    ovCell("adPolicyCell", "Policies", "—", policies.e.message, { subClass: "is-bad" });
  }
  ovCell("adModelCell", "Embedding model", "all-MiniLM-L6-v2", "384 dimensions · cosine similarity");
  if (outbox.ok) {
    const last = outbox.v[0];
    ovCell("adMailCell", "Emails dispatched", String(outbox.v.length),
      last ? `Last ${ago(last.sent_at)}` : "None yet", { mono: true });
  } else {
    ovCell("adMailCell", "Emails dispatched", "—", outbox.e.message, { subClass: "is-bad" });
  }
}

function renderAdPolicies(res) {
  if (!res.ok) { fill("policies", el("li", {}, el("p", { class: "ov-error" }, res.e.message))); return; }
  const q = $("adSearch").value.trim().toLowerCase();
  const rows = q ? res.v.filter(p => [p.id, p.title, p.content, p.tags].join(" ").toLowerCase().includes(q)) : res.v;
  if (!res.v.length) {
    fill("policies", el("li", { class: "ov-empty" },
      el("p", {}, "The index is empty, so the Refunds agent has no policy to cite."),
      el("p", {}, "Index one with the form, or reset demo data to restore the seed set.")));
    return;
  }
  if (!rows.length) { fill("policies", el("li", { class: "ov-empty" }, `No policy matches “${q}”.`)); return; }
  fill("policies", rows.map(p => {
    const seed = p.uploaded_by === "seed";
    const edit = el("button", { type: "button", class: "btn btn-sm btn-ghost", "aria-label": `Edit ${p.id}` }, "Edit");
    edit.addEventListener("click", () => {
      $("pId").value = p.id; $("pTitle").value = p.title; $("pContent").value = p.content; $("pTags").value = p.tags;
      renderAdHints();
      $("pTitle").focus();
    });
    return el("li", { class: "pg-policy" },
      el("div", { class: "pg-policy-head" },
        el("span", { class: "pg-policy-id" }, p.id),
        el("span", { class: "pg-policy-title", title: p.title }, p.title),
        edit),
      el("p", { class: "pg-policy-text" }, p.content),
      el("div", { class: "pg-policy-foot" },
        el("span", { class: "pg-policy-tags" }, (p.tags || "").split(/\s+/).filter(Boolean).map(t => el("span", { class: "ov-tool" }, t))),
        el("span", { class: "pg-policy-meta" },
          seed ? "Seed policy" : ["Indexed by ", el("span", { class: "font-mono" }, p.uploaded_by)],
          " · ", timeEl(p.updated_at))));
  }));
}

function renderAdOutbox(res) {
  if (!res.ok) { ovFail("emailOutbox", res.e.message); return; }
  if (!res.v.length) {
    fill("emailOutbox", el("div", { class: "ov-empty" }, el("p", {}, "No email sent yet. Every claim triggers a receipt and a decision email.")));
    return;
  }
  fill("emailOutbox", el("div", { class: "table-container" }, table([
    { label: "Message", key: "message_id", mono: true },
    { label: "Claim", key: "case_id", mono: true },
    { label: "To", key: "to" },
    { label: "Subject", render: m => el("span", { class: "pg-clamp", title: m.subject }, m.subject) },
    { label: "Type", render: m => m.email_type === "DECISION" ? "Decision" : m.email_type === "ACKNOWLEDGEMENT" ? "Receipt" : m.email_type },
    { label: "Status", render: m => el("span", { class: "chip success font-mono" }, m.status || "DELIVERED") },
    { label: "Sent", render: m => timeEl(m.sent_at) },
  ], res.v.slice(0, 12))));
}

function renderAdHints() {
  const id = $("pId").value.trim();
  const text = `${$("pTitle").value} ${$("pContent").value}`;
  const hints = [];
  const existing = adPolicies.find(p => p.id.toLowerCase() === id.toLowerCase());
  if (existing) hints.push(["warn", `${existing.id} already exists. Indexing replaces “${existing.title}” in both SQLite and ChromaDB.`]);
  if (/ignore|override|always approve|approve (all|every)|disregard|no limit/i.test(text)) {
    hints.push(["bad", "This reads like an instruction to the model. Retrieved text reaches the LLM verbatim; the guardrails still cap any payout, but the proposal can be steered (see RT-13)."]);
  }
  fill("adHints", hints.map(([tone, t]) => el("li", { class: `pg-hint ${tone}` },
    el("span", { class: `ov-feed-mark ${tone}`, "aria-hidden": "true" }), el("span", {}, t))));
}

document.addEventListener("persona-change", loadAdmin);
$("adRefresh").addEventListener("click", (ev) => busy(ev.currentTarget, loadAdmin));
$("adSearch").addEventListener("input", () => renderAdPolicies({ ok: true, v: adPolicies }));
["pId", "pTitle", "pContent"].forEach(id => $(id).addEventListener("input", renderAdHints));
$("policyForm").addEventListener("reset", () => setTimeout(renderAdHints));

$("policyForm").addEventListener("submit", (ev) => {
  ev.preventDefault();
  busy($("adSubmit"), async () => {
    const id = $("pId").value.trim();
    await api("POST", "/policies", {
      id, title: $("pTitle").value.trim(), content: $("pContent").value.trim(), tags: $("pTags").value.trim(),
    }, apiKey);
    ev.target.reset();
    toast(`<b>${id}</b> indexed into SQLite and ChromaDB.`);
    await Promise.all([loadAdmin(), loadHealth()]);
  });
});

$("resetBtn").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
  if (await resetDemoData(apiKey)) await loadAdmin();
}));

loadAdmin();

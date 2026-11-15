"use strict";
// ---------------------------------------------------------------- Audit trail
// The full ledger. Supervisors and admins may read it; customers get 403.
// Every row links to the one before it by prev_hash, and /logs/verify
// recomputes the whole chain server-side.

const AU_PAGE = 100;
let auLogs = [];           // newest first
let auVerify = null;       // last /logs/verify result
let auFilter = "all";
let auLimit = AU_PAGE;
let auOpen = new Set();    // expanded row ids
let auLoading = false;

const auditPortalKey = () => portalKey("auditAccount", "supervisor-key");

async function loadAudit() {
  if (auLoading) return;
  auLoading = true;
  const key = auditPortalKey();
  const acct = accountByKey(key);
  const note = $("auRoleNote");
  note.hidden = acct?.role !== "customer";
  if (acct?.role === "customer") note.textContent = `${acct.name} · ${acct.id} is a customer. The ledger is supervisor and admin only, so the API returns 403.`;

  try {
    const [logs, verify] = await Promise.all([
      settle(api("GET", "/logs", null, key)),
      settle(api("GET", "/logs/verify", null, key)),
    ]);
    auLogs = logs.ok ? logs.v.slice().reverse() : [];
    auVerify = verify.ok ? { ...verify.v, at: new Date() } : null;
    renderAuBand(logs, verify);
    renderAuTamper();
    if (logs.ok) renderAuTable(); else { ovFail("audit", logs.e.message); $("auCount").textContent = ""; }
    $("auUpdated").textContent = `Updated ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  } finally {
    auLoading = false;
  }
}

function renderAuBand(logs, verify) {
  if (verify.ok) {
    const v = verify.v;
    const at = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    ovCell("auChainCell", "Chain integrity", v.valid ? "Intact" : "Tampered",
      v.valid ? `All ${v.entries} hashes recomputed · ${at}` : `First bad link at entry #${v.first_bad_id}`,
      { dot: v.valid ? "ok" : "bad", subClass: v.valid ? null : "is-bad" });
  } else {
    ovCell("auChainCell", "Chain integrity", "Unverified", verify.e.message, { dot: "bad", subClass: "is-bad" });
  }
  if (!logs.ok) {
    ["auEntriesCell", "auBlockedCell", "auEscalatedCell", "auLastCell"].forEach((id, i) =>
      ovCell(id, ["Entries", "Blocked", "Escalated", "Latest event"][i], "—", logs.e.message, { subClass: "is-bad" }));
    return;
  }
  const head = auLogs[0];
  ovCell("auEntriesCell", "Entries", String(auLogs.length), head ? `Head ${head.hash.slice(0, 12)}` : "Empty ledger",
    { mono: true, title: head ? head.hash : null });
  const blocked = auLogs.filter(e => e.status === "BLOCKED").length;
  const escalated = auLogs.filter(e => e.status === "ESCALATED").length;
  ovCell("auBlockedCell", "Blocked", String(blocked), "Refused by a guardrail", { mono: true, dot: blocked ? "bad" : null });
  ovCell("auEscalatedCell", "Escalated", String(escalated), "Held for a supervisor", { mono: true, dot: escalated ? "warn" : null });
  if (head) {
    const { title } = describeAudit(head);
    ovCell("auLastCell", "Latest event", ago(head.ts), [head.actor, " · ", ...[title].flat()].map(x => typeof x === "string" ? x : x.textContent).join(""));
  } else {
    ovCell("auLastCell", "Latest event", "—", "Nothing logged yet");
  }
}

function renderAuTamper() {
  const box = $("auTamper");
  const bad = auVerify && !auVerify.valid;
  box.hidden = !bad;
  if (bad) box.textContent = `Hash chain broken at entry #${auVerify.first_bad_id}. That row, or the one before it, was changed after it was written. Every entry from there on is untrustworthy.`;
}

function auMatches(e) {
  if (auFilter === "notable" && e.action.startsWith("TOOL_CALL:") && e.status === "ALLOWED") return false;
  if (auFilter === "tools" && !e.action.startsWith("TOOL_CALL:")) return false;
  if ((auFilter === "BLOCKED" || auFilter === "ESCALATED") && e.status !== auFilter) return false;
  const q = $("auSearch").value.trim().toLowerCase();
  if (!q) return true;
  return [e.id, e.actor, e.action, e.status, e.case_id, e.hash, JSON.stringify(e.details)].join(" ").toLowerCase().includes(q);
}

const AU_TONE = { BLOCKED: "bad", ESCALATED: "warn", OVERRIDDEN: "info", INFO: "info", ALLOWED: "ok" };

function renderAuTable() {
  const rows = auLogs.filter(auMatches);
  const shown = rows.slice(0, auLimit);
  $("auCount").textContent = rows.length === auLogs.length ? `${auLogs.length} entries` : `${rows.length} of ${auLogs.length}`;
  if (!auLogs.length) {
    fill("audit", el("div", { class: "ov-empty" }, el("p", {}, "The ledger is empty. Every tool call and decision is appended here as it happens.")));
    return;
  }
  if (!rows.length) {
    fill("audit", el("div", { class: "ov-empty" }, el("p", {}, "No entries match. Clear the search or pick another filter.")));
    return;
  }
  const badId = auVerify && !auVerify.valid ? auVerify.first_bad_id : null;
  const byId = new Map(auLogs.map(e => [e.id, e]));

  const body = el("tbody", {});
  for (const e of shown) {
    const open = auOpen.has(e.id);
    const { title, note } = describeAudit(e);
    const caseLink = e.case_id ? el("button", { type: "button", class: "pg-linkish font-mono", title: `Show only ${e.case_id}` }, e.case_id) : "—";
    if (e.case_id) caseLink.addEventListener("click", (ev) => { ev.stopPropagation(); $("auSearch").value = e.case_id; auLimit = AU_PAGE; renderAuTable(); });
    const tr = el("tr", {
      class: `pg-ledger-row${open ? " is-open" : ""}${badId != null && e.id >= badId ? " is-tampered" : ""}`,
      tabindex: "0", "aria-expanded": String(open),
    },
      el("td", { class: "num font-mono" }, e.id),
      el("td", {}, timeEl(e.ts)),
      el("td", {}, el("div", { class: "pg-ledger-event" },
        el("span", { class: `ov-feed-mark ${AU_TONE[e.status] || ""}`, "aria-hidden": "true" }),
        el("span", {}, title, note ? el("span", { class: "pg-ledger-note" }, ` · ${note}`) : null))),
      el("td", { class: "mono font-mono" }, e.actor),
      el("td", { class: "mono" }, caseLink),
      el("td", {}, badge(e.status)),
      el("td", { class: "mono font-mono pg-hash", title: e.hash }, e.hash.slice(0, 10)));
    const toggle = () => { auOpen.has(e.id) ? auOpen.delete(e.id) : auOpen.add(e.id); renderAuTable(); };
    tr.addEventListener("click", toggle);
    tr.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggle(); } });
    body.append(tr);
    if (open) body.append(auDetailRow(e, byId.get(e.id - 1)));
  }

  const more = rows.length > shown.length
    ? el("button", { type: "button", class: "btn btn-sm btn-ghost pg-more" }, `Show ${Math.min(AU_PAGE, rows.length - shown.length)} more`)
    : null;
  if (more) more.addEventListener("click", () => { auLimit += AU_PAGE; renderAuTable(); });

  fill("audit",
    el("div", { class: "table-container" }, el("table", { class: "pg-ledger" },
      el("thead", {}, el("tr", {}, ["#", "When", "Event", "Actor", "Claim", "Status", "Hash"].map((h, i) => el("th", { class: i === 0 ? "num" : null }, h)))),
      body)),
    more);
}

function auDetailRow(e, prev) {
  const linked = prev ? prev.hash === e.prev_hash : true;
  const copy = (v, label) => {
    const b = el("button", { type: "button", class: "pg-hash-full font-mono", title: `Copy ${label}` }, v);
    b.addEventListener("click", (ev) => { ev.stopPropagation(); navigator.clipboard?.writeText(v).then(() => toast(`Copied ${label}`), () => {}); });
    return b;
  };
  return el("tr", { class: "pg-ledger-detail" }, el("td", { colspan: "7" },
    el("div", { class: "pg-ledger-grid" },
      el("div", {},
        el("div", { class: "pg-detail-label" }, "Details"),
        el("pre", { class: "pg-json" }, JSON.stringify(e.details, null, 2))),
      el("div", {},
        el("div", { class: "pg-detail-label" }, "Previous hash"),
        copy(e.prev_hash, "previous hash"),
        el("div", { class: "pg-detail-label" }, "This entry's hash"),
        copy(e.hash, "hash"),
        el("p", { class: `pg-link-check ${linked ? "ok" : "bad"}` },
          el("span", { class: `ov-dot ${linked ? "ok" : "bad"}`, "aria-hidden": "true" }),
          prev ? (linked ? `Links to entry #${prev.id}` : `Does not match entry #${prev.id}'s hash`)
               : e.id === 1 ? "First entry · links to the genesis hash" : "Previous entry not loaded")))));
}

fillAccountSelect("auditAccount", "supervisor", ["supervisor", "admin"], "Can read the ledger");
$("auditAccount").value = "supervisor-key";
$("auditAccount").addEventListener("change", loadAudit);
$("auditRefresh").addEventListener("click", (ev) => busy(ev.currentTarget, loadAudit));
$("verifyBtn").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
  await loadAudit();
  if (auVerify) toast(auVerify.valid ? `Chain intact · ${auVerify.entries} entries verified` : `Chain broken at entry #${auVerify.first_bad_id}`);
}));
$("auSearch").addEventListener("input", () => { auLimit = AU_PAGE; renderAuTable(); });
document.querySelectorAll("[data-ledger-filter]").forEach(b => b.addEventListener("click", () => {
  auFilter = b.dataset.ledgerFilter;
  auLimit = AU_PAGE;
  document.querySelectorAll("[data-ledger-filter]").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
  renderAuTable();
}));

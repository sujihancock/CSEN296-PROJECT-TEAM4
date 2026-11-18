"use strict";
// ---------------------------------------------------------------- Beneficiary portal
// Everything here is read with the picked customer's own key, so the page only
// ever shows that tenant's orders, cases and email. Pick a non-customer
// account and every panel shows the 403 instead.

const CU_AUTONOMOUS_CAP = 100;   // config.MAX_AUTONOMOUS_REFUND_PER_TX (not readable by beneficiaries)
const CU_WINDOW_DAYS = 30;       // POL-001 submission window
const CU_OPEN = new Set(["RECEIVED", "UNDER_REVIEW", "ESCALATED"]);
let cuOrders = [];
let cuCases = [];
let cuLoading = false;

const daysSince = (ts) => Math.floor((Date.now() - (parseTs(ts)?.getTime() || Date.now())) / 86400000);
const shortDate = (ts) => parseTs(ts)?.toLocaleDateString([], { month: "short", day: "numeric" }) || "—";
const refundedOn = (orderId) => cuCases
  .filter(c => c.order_id === orderId && c.status === "APPROVED")
  .reduce((a, c) => a + (c.amount_requested || 0), 0);

async function loadCustomer() {
  if (cuLoading) return;
  cuLoading = true;
  const key = apiKey;
  const acct = persona();
  roleNote("cuRoleNote", ["customer"], "This page is for beneficiaries, so the API rejects its requests with 403. Submitting a claim shows the same rejection.", "customer-001-key");

  try {
    const [orders, cases, emails, customers, files] = await Promise.all([
      settle(api("GET", "/me/orders", null, key)),
      settle(api("GET", "/me/cases", null, key)),
      settle(api("GET", "/me/emails", null, key)),
      settle(api("GET", "/demo/customers", null, key)),
      settle(api("GET", "/me/drive/files", null, key)),
    ]);
    cuOrders = orders.ok ? orders.v : [];
    cuCases = cases.ok ? cases.v : [];
    const me = customers.ok ? customers.v.find(c => c.id === acct?.id) : null;

    renderCuBand(me, orders, cases);
    renderCuOrderSelect(orders);
    renderCuCases(cases);
    renderCuInbox(emails);
    renderCuOrders(orders);
    renderCuFiles(files);
    $("cuUpdated").textContent = `Updated ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  } finally {
    cuLoading = false;
  }
}

function renderCuBand(me, orders, cases) {
  const acct = persona();
  if (me) {
    ovCell("cuAccountCell", "Beneficiary", me.name, `${me.id} · ${me.email}`);
    ovCell("cuBalanceCell", "Payment account", money(me.bank_balance), `Reimbursements land in ${me.bank_account}`, { mono: true });
  } else {
    ovCell("cuAccountCell", "Beneficiary", acct ? acct.name : "—", acct ? `${acct.id} · not a beneficiary` : "", { subClass: "is-bad" });
    ovCell("cuBalanceCell", "Payment account", "—", "No beneficiary record", { subClass: "is-bad" });
  }
  if (!cases.ok) {
    ovCell("cuOpenCell", "Open claims", "—", cases.e.message, { subClass: "is-bad" });
    ovCell("cuRefundedCell", "Reimbursed to date", "—", cases.e.message, { subClass: "is-bad" });
    return;
  }
  const open = cases.v.filter(c => CU_OPEN.has(c.status));
  const waiting = open.filter(c => c.status === "ESCALATED").length;
  ovCell("cuOpenCell", "Open claims",
    open.length ? `${open.length} open` : "None open",
    waiting ? `${waiting} waiting on a supervisor` : open.length ? "Being processed" : `${cases.v.length} filed in total`,
    { dot: waiting ? "warn" : "ok", subClass: waiting ? "is-warn" : null });
  const approved = cases.v.filter(c => c.status === "APPROVED");
  const total = approved.reduce((a, c) => a + (c.amount_requested || 0), 0);
  ovCell("cuRefundedCell", "Reimbursed to date", money(total),
    approved.length ? `${approved.length} approved ${approved.length === 1 ? "claim" : "claims"}` : "No approved claims yet",
    { mono: true });
}

function renderCuOrderSelect(orders) {
  const sel = $("orderSel");
  const prev = sel.value;
  if (!orders.ok) {
    sel.replaceChildren(el("option", { value: "" }, "Expenses unavailable"));
    sel.disabled = true;
    fill("cuOrderFacts");
    fill("cuHints");
    return;
  }
  sel.disabled = false;
  sel.replaceChildren(...orders.v.map(o => el("option", { value: o.id }, `${o.id} · ${o.product_name} · ${money(o.amount)}`)));
  if (orders.v.some(o => o.id === prev)) { sel.value = prev; renderCuFacts(); } else cuSyncAmount();
}

function cuSyncAmount() {
  const o = cuOrders.find(x => x.id === $("orderSel").value);
  if (o) $("amount").value = Math.max(0, o.amount - refundedOn(o.id)).toFixed(2);
  renderCuFacts();
}

function renderCuFacts() {
  const o = cuOrders.find(x => x.id === $("orderSel").value);
  if (!o) { fill("cuOrderFacts"); fill("cuHints"); return; }
  const age = daysSince(o.purchase_date);
  const refunded = refundedOn(o.id);
  const fact = (k, v, cls) => el("div", { class: "pg-fact" }, el("dt", {}, k), el("dd", { class: cls || null }, v));
  fill("cuOrderFacts", el("dl", {},
    fact("Service", o.product_name),
    fact("Coverage", o.category === "excluded" ? "Excluded" : "Covered"),
    fact("Date of service", `${shortDate(o.purchase_date)} · ${age === 0 ? "today" : `${age}d ago`}`),
    fact("Paid", money(o.amount), "font-mono"),
    refunded ? fact("Already reimbursed", money(refunded), "font-mono") : null));
  renderCuHints();
}

function renderCuHints() {
  const o = cuOrders.find(x => x.id === $("orderSel").value);
  if (!o) { fill("cuHints"); return; }
  const amt = parseFloat($("amount").value);
  const remaining = o.amount - refundedOn(o.id);
  const age = daysSince(o.purchase_date);
  const open = cuCases.find(c => c.order_id === o.id && CU_OPEN.has(c.status));
  const hints = [];
  if (!isNaN(amt) && amt > remaining + 0.001) {
    hints.push(["bad", remaining <= 0
      ? "This expense has already been reimbursed in full, so a new claim will be denied."
      : `That is more than the ${money(remaining)} still reimbursable for this expense, so the claim will be denied.`]);
  }
  if (!isNaN(amt) && amt > CU_AUTONOMOUS_CAP) {
    hints.push(["warn", `Over the ${money(CU_AUTONOMOUS_CAP)} autonomous limit, so a supervisor makes the call. Nothing is paid until then.`]);
  }
  if (o.category === "excluded") hints.push(["warn", "This service is excluded from the benefits program and is generally not reimbursable."]);
  else if (age > CU_WINDOW_DAYS) hints.push(["warn", `Date of service was ${age} days ago, outside the ${CU_WINDOW_DAYS}-day submission window.`]);
  if (open) hints.push(["info", `You already have an open claim on this expense (${open.id}, ${open.status.toLowerCase().replace("_", " ")}).`]);
  if (!hints.length && !isNaN(amt) && amt > 0) {
    hints.push(["ok", `Within the ${money(CU_AUTONOMOUS_CAP)} limit, so the agents can decide and pay this without waiting for a person.`]);
  }
  fill("cuHints", hints.map(([tone, text]) => el("li", { class: `pg-hint ${tone}` },
    el("span", { class: `ov-feed-mark ${tone}`, "aria-hidden": "true" }), el("span", {}, text))));
}

function renderCuCases(cases) {
  if (!cases.ok) { ovFail("myCases", cases.e.message); $("cuCasesNote").textContent = "—"; return; }
  const rows = cases.v;
  const decided = rows.filter(c => c.status === "APPROVED" || c.status === "DENIED");
  const auto = decided.filter(c => c.autonomous).length;
  $("cuCasesNote").textContent = rows.length
    ? (decided.length ? `${auto} of ${decided.length} decided by agents` : `${rows.length} filed`)
    : "None yet";
  if (!rows.length) {
    fill("myCases", el("div", { class: "ov-empty" },
      el("p", {}, "You haven't requested any reimbursements."),
      el("p", {}, "Use the form above. The outcome, and the email you get about it, show up here.")));
    return;
  }
  const by = (c) => !c.decided_by ? "—" : c.autonomous ? "Agent" : "Supervisor";
  fill("myCases", el("div", { class: "table-container" }, table([
    { label: "Claim", key: "id", mono: true },
    { label: "Expense", key: "order_id", mono: true },
    { label: "Amount", render: r => money(r.amount_requested), num: true },
    { label: "Status", render: r => badge(r.status) },
    { label: "Why", render: r => el("span", { class: "pg-clamp", title: r.decision_reason || "" }, r.decision_reason || "Being processed") },
    { label: "Decided by", render: by },
    { label: "Updated", render: r => timeEl(r.updated_at) },
  ], rows)));
}

function emailTone(e) {
  if (e.email_type !== "DECISION") return "info";
  if (/APPROVED/.test(e.subject)) return "ok";
  if (/ESCALATED/.test(e.subject)) return "warn";
  if (/DENIED|REVOKED|BLOCKED/.test(e.subject)) return "bad";
  return "info";
}

function renderCuInbox(emails) {
  if (!emails.ok) { fill("inbox", el("li", {}, el("p", { class: "ov-error" }, emails.e.message))); $("cuInboxNote").textContent = "—"; return; }
  const list = emails.v;
  const unread = list.filter(e => !e.read_at).length;
  $("cuInboxNote").textContent = list.length ? `${list.length} ${list.length === 1 ? "message" : "messages"}${unread ? ` · ${unread} unread` : ""}` : "Empty";
  if (!list.length) {
    fill("inbox", el("li", { class: "ov-empty" }, "No email yet. You get one when a claim is received and another when it's decided."));
    return;
  }
  fill("inbox", list.slice(0, 8).map(e => el("li", { class: "ov-feed-item" },
    el("span", { class: `ov-feed-mark ${emailTone(e)}`, "aria-hidden": "true" }),
    el("div", { class: "pg-mail" },
      el("a", { class: "ov-feed-title", href: `/mail?case=${encodeURIComponent(e.case_id)}` }, e.subject),
      el("p", { class: "pg-mail-body" }, e.body),
      el("div", { class: "ov-feed-meta" },
        e.email_type === "DECISION" ? "Decision" : "Receipt", " · ", el("span", { class: "font-mono" }, e.case_id))),
    timeEl(e.sent_at, "ov-feed-time"))));
}

function renderCuOrders(orders) {
  if (!orders.ok) { fill("myOrders", el("li", {}, el("p", { class: "ov-error" }, orders.e.message))); $("cuOrdersNote").textContent = "—"; return; }
  $("cuOrdersNote").textContent = `${orders.v.length} verified ${orders.v.length === 1 ? "expense" : "expenses"}`;
  if (!orders.v.length) { fill("myOrders", el("li", { class: "ov-empty" }, "No healthcare expenses on file.")); return; }
  fill("myOrders", orders.v.map(o => {
    const latest = cuCases.find(c => c.order_id === o.id);
    const claim = el("button", { type: "button", class: "btn btn-sm btn-ghost", "aria-label": `Start a claim for ${o.id}` }, "Claim");
    claim.addEventListener("click", () => {
      $("orderSel").value = o.id;
      cuSyncAmount();
      $("orderSel").scrollIntoView({ behavior: "smooth", block: "center" });
      $("reason").focus({ preventScroll: true });
    });
    return el("li", { class: "pg-list-item" },
      el("div", { class: "pg-list-main" },
        el("span", { class: "pg-list-title", title: o.product_name }, o.product_name),
        el("span", { class: "pg-list-meta" },
          el("span", { class: "font-mono" }, o.id), " · ", o.category, " · ", shortDate(o.purchase_date))),
      el("div", { class: "pg-list-side" },
        el("span", { class: "font-mono pg-amt" }, money(o.amount)),
        latest ? badge(latest.status) : claim));
  }));
}

// ---------------------------------------------------------------- Drive (case files)
const fmtSize = (n) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`;

function renderCuFiles(files) {
  if (!files.ok) {
    $("cuFilesNote").textContent = "—";
    fill("myFiles", el("li", { class: "ov-error" }, files.e.message));
    return;
  }
  $("cuFilesNote").textContent = `${files.v.length} file${files.v.length === 1 ? "" : "s"}`;
  if (!files.v.length) {
    fill("myFiles", el("li", { class: "ov-empty" }, el("p", {}, "Files you attach to a claim are kept here.")));
    return;
  }
  fill("myFiles", files.v.map(f => {
    const dl = el("button", { type: "button", class: "btn btn-sm btn-ghost", "aria-label": `Download ${f.name}` }, "Download");
    dl.addEventListener("click", () => busy(dl, () => downloadFile(f)));
    return el("li", { class: "pg-list-item" },
      el("div", { class: "pg-list-main" },
        el("span", { class: "pg-list-title", title: f.name }, f.name),
        el("span", { class: "pg-list-meta" },
          el("span", { class: "font-mono" }, f.id), " · ", fmtSize(f.size), " · ",
          f.case_id ? el("span", { class: "font-mono" }, f.case_id) : "not attached")),
      el("div", { class: "pg-list-side" }, dl));
  }));
}

// The download needs the API key header, so fetch it and hand the browser a blob.
async function downloadFile(f) {
  const res = await fetch(`/me/drive/files/${encodeURIComponent(f.id)}/content`, { headers: { "X-API-Key": apiKey } });
  if (!res.ok) throw new Error(`Download failed (${res.status})`);
  const url = URL.createObjectURL(await res.blob());
  const a = el("a", { href: url, download: f.name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = () => reject(new Error(`Could not read ${file.name}`));
    reader.readAsDataURL(file);
  });
}

async function uploadPicked() {
  const picked = [...$("cuFiles").files];
  if (picked.length > 5) throw new Error("Attach up to 5 files.");
  const ids = [];
  for (const f of picked) {
    if (f.size > 1000000) throw new Error(`${f.name} is over 1 MB.`);
    const up = await api("POST", "/me/drive/files", { name: f.name, content_base64: await fileToBase64(f) }, apiKey);
    ids.push(up.id);
  }
  return ids;
}

function renderCuResult(r) {
  const tone = { APPROVED: "ok", ESCALATED: "warn", DENIED: "bad", BLOCKED: "bad", REVOKED: "bad" }[r.status] || "info";
  const headline = {
    APPROVED: `Approved. ${money(r.refunded_amount)} is on its way to your payment account.`,
    ESCALATED: "Sent to a supervisor. Nothing has been paid yet.",
    DENIED: "Denied by policy.",
    BLOCKED: "Blocked by a guardrail.",
    REVOKED: "Reimbursements are paused right now. Nothing was paid.",
  }[r.status] || r.status;
  fill("refundResult", el("div", { class: `pg-result ${tone}`, role: "status" },
    el("div", { class: "pg-result-head" }, badge(r.status), r.case_id ? el("span", { class: "font-mono" }, r.case_id) : null),
    el("p", { class: "pg-result-title" }, headline),
    r.reason ? el("p", { class: "pg-result-body" }, r.reason) : null,
    (r.case_files || []).length ? el("p", { class: "pg-result-body" }, "Attached: ",
      r.case_files.map(f => f.withheld ? `${f.name} (held for a person to read)` : f.name).join(", ")) : null,
    r.new_bank_balance != null ? el("p", { class: "pg-result-body" }, "New balance ", el("b", { class: "font-mono" }, money(r.new_bank_balance))) : null));
}

document.addEventListener("persona-change", () => { fill("refundResult"); loadCustomer(); });
$("orderSel").addEventListener("change", cuSyncAmount);
$("amount").addEventListener("input", renderCuHints);
$("cuRefresh").addEventListener("click", (ev) => busy(ev.currentTarget, loadCustomer));

$("refundForm").addEventListener("submit", (ev) => {
  ev.preventDefault();
  busy($("cuSubmit"), async () => {
    const attachments = await uploadPicked();
    const out = await api("POST", "/tasks", {
      task: "refund_request",
      data: { order_id: $("orderSel").value, amount: parseFloat($("amount").value), reason: $("reason").value, attachments },
    }, apiKey);
    renderCuResult(out.result);
    $("reason").value = "";
    $("cuFiles").value = "";
    await Promise.all([loadCustomer(), loadHealth()]);
  });
});

loadCustomer();

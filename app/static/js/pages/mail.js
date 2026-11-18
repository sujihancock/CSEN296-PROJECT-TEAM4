"use strict";
// ---------------------------------------------------------------- Beneficiary Mail
// Inbox: what the agents (and supervisors) emailed this beneficiary.
// Sent: what the beneficiary wrote to the benefits office. Those messages land in
// the staff queue on the Supervisor page; the agent pipeline never reads them.

const ML_SUBJECT_MAX = 200;   // matches MAIL_SUBJECT_MAX in app/main.py
const ML_BODY_MAX = 5000;     // matches MAIL_BODY_MAX
const ml = { folder: "inbox", caseId: "", items: [], cases: [], selected: null, loading: false };

const mlType = (m) => ml.folder === "sent" ? "To the office"
  : m.email_type === "DECISION" ? "Decision" : m.email_type === "ACKNOWLEDGEMENT" ? "Receipt" : "Notice";
const mlSnippet = (s) => (s || "").replace(/\s+/g, " ").slice(0, 140);

async function loadMail(keepSelection) {
  if (ml.loading) return;
  ml.loading = true;
  const blocked = roleNote("mlRoleNote", ["customer"], "Mail is for beneficiaries, so the API rejects these requests with 403.", "customer-001-key");
  $("mlCompose").disabled = blocked;
  try {
    const q = new URLSearchParams({ folder: ml.folder });
    if (ml.caseId) q.set("case_id", ml.caseId);
    const [items, cases] = await Promise.all([
      settle(api("GET", `/me/mail?${q}`)),
      settle(api("GET", "/me/cases")),
    ]);
    ml.cases = cases.ok ? cases.v : [];
    renderCaseFilter();
    if (!items.ok) {
      ml.items = [];
      fill("mlList", el("li", {}, el("p", { class: "ov-error" }, items.e.message)));
    } else {
      ml.items = items.v;
      renderList();
    }
    if (!keepSelection || !ml.items.some(m => m.id === ml.selected)) {
      ml.selected = null;
      if (!$("mlComposeForm")) showEmptyReader();
    }
    await refreshMailCounts();
    $("mlUpdated").textContent = `Updated ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  } finally {
    ml.loading = false;
  }
}

async function refreshMailCounts() {
  const r = await settle(api("GET", "/me/mail/unread"));
  const n = r.ok ? r.v.unread : 0;
  $("mlInboxCount").textContent = n ? String(n) : "";
  if (typeof refreshMailBadge === "function") refreshMailBadge(n);
}

function renderCaseFilter() {
  const sel = $("mlCaseFilter");
  sel.replaceChildren(el("option", { value: "" }, "All claims"),
    ...ml.cases.map(c => el("option", { value: c.id }, `${c.id} · ${c.order_id} · ${c.status.toLowerCase().replace("_", " ")}`)));
  sel.value = ml.cases.some(c => c.id === ml.caseId) ? ml.caseId : "";
}

function renderList() {
  document.querySelectorAll(".ml-folder").forEach(b => b.setAttribute("aria-selected", String(b.dataset.folder === ml.folder)));
  if (!ml.items.length) {
    fill("mlList", el("li", { class: "ov-empty ml-empty" }, ml.folder === "sent"
      ? "You haven't written to the office yet."
      : ml.caseId ? "No email about this claim." : "No email yet. You get one when a claim is received and another when it's decided."));
    return;
  }
  fill("mlList", ml.items.map(m => {
    const unread = ml.folder === "inbox" && m.unread;
    const btn = el("button", { type: "button", class: "ml-item", "aria-current": m.id === ml.selected ? "true" : null },
      el("span", { class: `ml-dot${unread ? " unread" : ""}`, "aria-hidden": "true" }),
      el("span", { class: "ml-item-main" },
        el("span", { class: "ml-item-top" },
          el("span", { class: `ml-subject${unread ? " unread" : ""}` }, m.subject),
          timeEl(m.sent_at, "ml-time")),
        el("span", { class: "ml-snippet" }, mlSnippet(m.body)),
        el("span", { class: "ml-meta" }, unread ? el("span", { class: "visually-hidden" }, "Unread. ") : null,
          mlType(m), m.case_id ? [" · ", el("span", { class: "font-mono" }, m.case_id)] : null,
          ml.folder === "sent" ? [" · ", m.status === "HANDLED" ? "Handled by staff" : "Waiting for staff"] : null)));
    btn.addEventListener("click", () => openMessage(m.id));
    return el("li", {}, btn);
  }));
}

function showEmptyReader() {
  fill("mlReader", el("div", { class: "ov-empty" },
    el("p", {}, ml.folder === "sent" ? "Pick a message you sent to see it." : "Pick a message to read it."),
    el("p", {}, "Questions about a claim? Write to the office. Staff read these messages; the AI agent does not.")));
}

async function openMessage(id) {
  let m = ml.items.find(x => x.id === id);
  if (!m) return;
  ml.selected = id;
  if (ml.folder === "inbox" && m.unread) {
    const r = await settle(api("POST", `/me/mail/${id}/read`));
    if (r.ok) { Object.assign(m, r.v); refreshMailCounts(); }
  }
  renderList();
  const sent = ml.folder === "sent";
  const reply = sent ? null : el("button", { type: "button", class: "btn btn-sm btn-secondary" }, "Reply");
  reply?.addEventListener("click", () => showCompose({
    caseId: m.case_id, inReplyTo: m.id,
    subject: /^re:/i.test(m.subject) ? m.subject : `Re: ${m.subject}`,
  }));
  fill("mlReader", el("article", { class: "ml-message" },
    el("header", { class: "ml-msg-head" },
      el("h2", { class: "ml-msg-subject", id: "mlReaderTitle", tabindex: "-1" }, m.subject),
      el("dl", { class: "ml-msg-meta" },
        el("div", {}, el("dt", {}, "From"), el("dd", {}, sent ? "You" : m.from)),
        el("div", {}, el("dt", {}, "To"), el("dd", {}, sent ? m.to : "You")),
        el("div", {}, el("dt", {}, "Sent"), el("dd", {}, parseTs(m.sent_at)?.toLocaleString() || m.sent_at)),
        m.case_id ? el("div", {}, el("dt", {}, "Claim"), el("dd", { class: "font-mono" }, m.case_id)) : null,
        sent ? el("div", {}, el("dt", {}, "Status"), el("dd", {}, m.status === "HANDLED" ? `Handled by ${m.handled_by}` : "Waiting for staff")) : null)),
    el("div", { class: "ml-msg-body" }, m.body),
    reply ? el("div", { class: "form-actions ml-msg-actions" }, reply) : null));
  $("mlReaderTitle").focus({ preventScroll: true });
  if (window.matchMedia("(max-width: 860px)").matches) $("mlReader").scrollIntoView({ behavior: "smooth", block: "start" });
}

function showCompose({ caseId = "", inReplyTo = null, subject = "" } = {}) {
  ml.selected = null;
  renderList();
  const caseSel = el("select", { id: "mlCase" }, el("option", { value: "" }, "Not about a specific claim"),
    ...ml.cases.map(c => el("option", { value: c.id }, `${c.id} · ${c.order_id} · ${money(c.amount_requested)}`)));
  caseSel.value = caseId || "";
  if (inReplyTo) caseSel.disabled = true;  // a reply stays on the claim it answers
  const subj = el("input", { id: "mlSubject", required: true, maxlength: String(ML_SUBJECT_MAX), value: subject, autocomplete: "off" });
  const body = el("textarea", { id: "mlBody", required: true, rows: "8", maxlength: String(ML_BODY_MAX),
    placeholder: "For example: I uploaded the itemized bill for this visit. Can you confirm you received it?" });
  const counter = el("span", { class: "ml-counter", "aria-live": "polite" }, `0 / ${ML_BODY_MAX}`);
  body.addEventListener("input", () => { counter.textContent = `${body.value.length} / ${ML_BODY_MAX}`; });
  const send = el("button", { type: "submit", class: "btn btn-primary" }, "Send");
  const cancel = el("button", { type: "button", class: "btn btn-secondary" }, "Cancel");
  cancel.addEventListener("click", showEmptyReader);
  const form = el("form", { id: "mlComposeForm", class: "pg-form" },
    el("h2", { class: "ml-msg-subject", id: "mlReaderTitle", tabindex: "-1" }, inReplyTo ? "Reply" : "Write to the benefits office"),
    el("div", { class: "form-group" }, el("span", { class: "ml-label" }, "To"), el("p", { class: "ml-to" }, "Benefits Office")),
    el("div", { class: "form-group" }, el("label", { for: "mlCase" }, "Regarding"), caseSel),
    el("div", { class: "form-group" }, el("label", { for: "mlSubject" }, "Subject"), subj),
    el("div", { class: "form-group" }, el("label", { for: "mlBody" }, "Message"), body, counter),
    el("p", { class: "ml-note" }, "Benefits office staff read this. It is not sent to the AI agent and does not change a claim's decision on its own."),
    el("div", { class: "form-actions" }, cancel, send));
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    busy(send, async () => {
      const sentMsg = await api("POST", "/me/mail/send", {
        subject: subj.value, body: body.value, case_id: caseSel.value || null, in_reply_to: inReplyTo,
      });
      toast("Sent to the <b>Benefits Office</b>.");
      ml.folder = "sent";
      ml.caseId = "";
      await loadMail(false);
      await openMessage(sentMsg.id);
    });
  });
  fill("mlReader", form);
  (subject ? body : subj).focus();
  if (window.matchMedia("(max-width: 860px)").matches) $("mlReader").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------------------------------------------------------------- Wiring
document.querySelectorAll(".ml-folder").forEach(b => b.addEventListener("click", () => {
  if (ml.folder === b.dataset.folder) return;
  ml.folder = b.dataset.folder;
  loadMail(false);
}));
$("mlCaseFilter").addEventListener("change", (ev) => { ml.caseId = ev.target.value; loadMail(false); });
$("mlRefresh").addEventListener("click", (ev) => busy(ev.currentTarget, () => loadMail(true)));
$("mlCompose").addEventListener("click", () => showCompose({ caseId: ml.caseId }));
document.addEventListener("persona-change", () => { ml.folder = "inbox"; ml.caseId = ""; loadMail(false); });

// Deep links from the portal: /mail?case=CASE-…, /mail?compose=1&case=CASE-…
(async () => {
  const q = new URLSearchParams(location.search);
  ml.caseId = q.get("case") || "";
  await loadMail(false);
  if (q.get("compose")) showCompose({ caseId: ml.caseId });
})();

"use strict";

const $ = (id) => document.getElementById(id);
const money = (n) => (n == null || isNaN(n)) ? "—" : `$${Number(n).toFixed(2)}`;

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}
function fill(id, ...nodes) { const elem = $(id); if (elem) elem.replaceChildren(...nodes.flat()); }

const STATUS_CLASS = {
  APPROVED: "b-ok", ALLOWED: "b-ok", DEFENDED: "b-ok", POLICY_PASSED: "b-ok",
  ESCALATED: "b-warn", UNDER_REVIEW: "b-info", RECEIVED: "b-info", INFO: "b-info", OVERRIDDEN: "b-info",
  DENIED: "b-bad", BLOCKED: "b-bad", REVOKED: "b-bad", ATTACK_SUCCEEDED: "b-bad",
};
const badge = (s) => el("span", { class: `badge ${STATUS_CLASS[s] || ""}` }, s ?? "—");

function toast(msg) {
  const t = $("toast");
  if (!t) return;
  t.innerHTML = msg;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 3200);
}

function table(columns, rows, emptyText) {
  if (!rows || !rows.length) return el("div", { class: "empty" }, emptyText || "Nothing here yet.");
  return el("table", {},
    el("thead", {}, el("tr", {}, columns.map(c => el("th", { class: c.num ? "num" : null }, c.label)))),
    el("tbody", {}, rows.map(r => el("tr", {}, columns.map(c => {
      const v = c.render ? c.render(r) : r[c.key];
      return el("td", { class: [c.num ? "num" : "", c.mono ? "mono font-mono" : ""].join(" ").trim() || null }, v);
    })))));
}

// ---------------------------------------------------------------- Demo accounts
// Seeded API keys (app/config.py). Each workspace page has its own "Acting as"
// picker so a reviewer can switch identity and watch role checks reject (403).
const ACCOUNTS = [
  { key: "customer-001-key", id: "CUST-001", name: "Alex Smith", role: "customer" },
  { key: "customer-002-key", id: "CUST-002", name: "Elena Rostova", role: "customer" },
  { key: "customer-003-key", id: "CUST-003", name: "Marcus Vance", role: "customer" },
  { key: "customer-004-key", id: "CUST-004", name: "Raphael Kusuma", role: "customer" },
  { key: "supervisor-key", id: "SUP-001", name: "Supervisor", role: "supervisor" },
  { key: "admin-key", id: "ADMIN-001", name: "Admin", role: "admin" },
];
const ROLE_LABEL = { customer: "Customer", supervisor: "Supervisor", admin: "Admin" };
const accountByKey = (key) => ACCOUNTS.find(a => a.key === key);

// The header persona is the one identity every workspace page acts as.
// When it lacks a page's role the page says so (the API will answer 403)
// and offers the persona that can use it.
const persona = () => accountByKey(apiKey);
function roleNote(noteId, allowed, message, suggestKey) {
  const note = $(noteId);
  const p = persona();
  const ok = p && allowed.includes(p.role);
  if (!note) return !ok;
  note.hidden = ok;
  if (!ok) {
    const target = accountByKey(suggestKey);
    const btn = el("button", { type: "button", class: "btn btn-sm btn-secondary" }, `Switch to ${target.name}`);
    btn.addEventListener("click", () => setPersona(suggestKey, true));
    note.replaceChildren(el("span", {}, `You're acting as ${p.name} (${ROLE_LABEL[p.role].toLowerCase()}). ${message}`), btn);
  }
  return !ok;
}

// ---------------------------------------------------------------- Theme Manager (SVG Icons, No Emojis)
const SUN_SVG = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>`;
const MOON_SVG = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`;

function initTheme() {
  const saved = localStorage.getItem("changsupport-theme") || "dark";
  document.documentElement.setAttribute("data-theme", saved);
  updateThemeIcon(saved);
}
function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme") || "dark";
  const next = current === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("changsupport-theme", next);
  updateThemeIcon(next);
}
function updateThemeIcon(t) {
  const icon = $("themeIcon");
  if (icon) icon.innerHTML = t === "dark" ? SUN_SVG : MOON_SVG;
}
const themeBtn = $("themeToggleBtn");
if (themeBtn) themeBtn.addEventListener("click", toggleTheme);
initTheme();

// ---------------------------------------------------------------- API client & header persona
// The header persona follows you from page to page (per browser tab).
const PERSONA_KEY = "changsupport-persona";
function readPersona() {
  try { return sessionStorage.getItem(PERSONA_KEY); } catch (e) { return null; }
}
let apiKey = (ACCOUNTS.some(a => a.key === readPersona()) && readPersona()) || "customer-001-key";


async function api(method, path, body, keyOverride) {
  const key = keyOverride || apiKey;
  const res = await fetch(path, {
    method,
    headers: { "X-API-Key": key, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const d = data && data.detail;
    const msg = typeof d === "string" ? d : (d && d.reason) || `Request failed (${res.status})`;
    throw new Error(res.status === 403 ? `Rejected (403): ${msg}` : msg);
  }
  return data;
}

async function busy(button, fn) {
  if (button) button.disabled = true;
  try { await fn(); } catch (e) { toast(e.message); } finally { if (button) button.disabled = false; }
}

// Header persona selector
const roleSel = $("role");
function setPersona(key, announce) {
  apiKey = key;
  if (roleSel) roleSel.value = key;
  try { sessionStorage.setItem(PERSONA_KEY, key); } catch (e) { /* private mode: persona just won't carry over */ }
  document.dispatchEvent(new CustomEvent("persona-change", { detail: key }));
  if (announce) toast(`Active persona: <b>${roleSel?.selectedOptions[0]?.text || key}</b>`);
}
if (roleSel) {
  roleSel.value = apiKey;
  roleSel.addEventListener("change", () => setPersona(roleSel.value, true));
}

// ---------------------------------------------------------------- Health & Vector Status
async function loadHealth() {
  const h = await fetch("/health").then(r => r.json()).catch(() => null);
  const status = $("killPill");
  if (status) {
    const [state, text] = !h ? ["bad", "API unreachable"]
      : h.kill_switch_active ? ["bad", "Kill switch on · agents can't pay"]
      : ["ok", "Agents active"];
    status.dataset.state = state;
    status.replaceChildren(el("span", { class: `ov-dot ${state}`, "aria-hidden": "true" }), el("span", {}, text));
  }
  return h;
}

// Returns true when the reset ran (the user can cancel the confirm).
async function resetDemoData(keyOverride) {
  if (!confirm("Reset all demo data? Customers, orders, cases, emails, policies, the vector collection and the audit log return to their seed state.")) return false;
  await api("POST", "/admin/reset", null, keyOverride || "admin-key");
  toast("Demo data reset to its seed state.");
  await loadHealth();
  return true;
}

// ---------------------------------------------------------------- Shared formatting
const parseTs = (ts) => ts ? new Date(String(ts).replace(" ", "T") + (/[zZ+]/.test(ts.slice(10)) ? "" : "Z")) : null;
const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto", style: "short" });
function ago(ts) {
  const d = parseTs(ts);
  if (!d || isNaN(d)) return "—";
  const s = Math.round((d.getTime() - Date.now()) / 1000);
  const abs = Math.abs(s);
  if (abs < 45) return "just now";
  if (abs < 3600) return rtf.format(Math.round(s / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(s / 3600), "hour");
  return rtf.format(Math.round(s / 86400), "day");
}
const timeEl = (ts, cls) => el("time", { class: cls || "ov-time", datetime: parseTs(ts)?.toISOString() || "", title: ts ? `${ts} UTC` : null }, ago(ts));

function ovCell(id, label, value, sub, opts = {}) {
  const cell = $(id);
  if (!cell) return;
  cell.replaceChildren(
    el("span", { class: "ov-cell-label" }, label),
    el("span", { class: `ov-cell-value${opts.mono ? " font-mono" : ""}`, title: opts.title || null },
      opts.dot ? el("span", { class: `ov-dot ${opts.dot}`, "aria-hidden": "true" }) : null, value),
    sub != null ? el("span", { class: `ov-cell-sub${opts.subClass ? " " + opts.subClass : ""}` }, sub) : null,
    ...(opts.extra || []));
}
const ovFail = (id, msg) => fill(id, el("p", { class: "ov-error" }, msg));
const settle = (p) => p.then(v => ({ ok: true, v }), e => ({ ok: false, e }));
function spendLevel(pct) { return pct >= 100 ? "bad" : pct >= 80 ? "warn" : "ok"; }

function describeAudit(e) {
  const d = e.details || {};
  if (e.action.startsWith("TOOL_CALL:")) {
    const tool = e.action.slice(10);
    return { title: [`${e.status === "ALLOWED" ? "Called" : "Refused"} `, el("code", {}, tool)], tone: e.status === "ALLOWED" ? "" : "bad" };
  }
  if (e.action === "SET_KILL_SWITCH") {
    return { title: d.active ? "Kill switch engaged" : "Kill switch released", tone: d.active ? "bad" : "info", note: d.reason };
  }
  const words = e.action.toLowerCase().replace(/_/g, " ");
  const tone = e.status === "BLOCKED" ? "bad" : e.status === "ESCALATED" ? "warn" : e.status === "ALLOWED" ? "ok" : "info";
  const note = d.code && d.code !== "OK" ? d.code : d.reason;
  let title = (words.charAt(0).toUpperCase() + words.slice(1)).replace(/\brt[- ](\d+)/i, "RT-$1");
  if (e.status === "BLOCKED" && !/denied|blocked|mismatch/.test(words)) title += " blocked";
  if (e.status === "ESCALATED" && !/escalated/.test(words)) title += " held for review";
  return { title, tone, note };
}

// Buttons rendered by page scripts can carry data-goto="<page>" to link to another workspace.
document.addEventListener("click", (ev) => {
  const t = ev.target.closest("[data-goto]");
  if (t) location.href = `/${t.dataset.goto}`;
});

loadHealth();

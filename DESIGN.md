# Design System: ChangSupport.AI

<!-- impeccable:design-schema 1 -->

## Surface Mode
**Operate Mode**: High-density operational dashboard where scanability, clarity of system state, and immediate task completion outrank decorative flourishes.

## Typography
- **Font Stack**: Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif.
- **Monospace Stack**: "JetBrains Mono", "SF Mono", ui-monospace, Menlo, Consolas, monospace (used for transaction amounts, IDs, hashes, timestamps, and policy codes).
- **Scale**:
  - Display / Brand: 18px / 1.3 (semi-bold)
  - Section Headings (h2): 15px / 1.4 (font-weight: 600, tracking: -0.01em)
  - Subheadings (h3): 13.5px / 1.4 (font-weight: 600)
  - Body / Controls: 13.5px / 1.5 (regular / medium)
  - Secondary / Captions: 12px / 1.4
  - Badges / Microcopy: 11px / 1.2 (font-weight: 600, uppercase or tabular)

## Color Palette (Operate System)
Designed with rich contrast, restrained neutrals, and purposeful semantic tokens:

### Dark Theme (Default)
- Surface 0 (Background): `#090d16` (Deep slate / navy-tinted charcoal)
- Surface 1 (Cards & Sidebars): `#111827` (Card panels)
- Surface 2 (Interactive & Nested): `#1a2234` (Inputs, sub-panels)
- Surface 3 (Hovers & Borders): `#28354d` (Active states, dividers)
- Text Primary: `#f3f4f6`
- Text Muted: `#9ca3af`
- Accent (Primary Action / Agent): `#3b82f6` (Indigo/Blue)
- Accent Glow / Ring: `rgba(59, 130, 246, 0.25)`

### Semantic Tokens
- **Success / Approved / Defended**:
  - Background: `rgba(16, 185, 129, 0.12)`, Border: `rgba(16, 185, 129, 0.35)`, Solid/Text: `#10b981`
- **Warning / Escalation / Human Review**:
  - Background: `rgba(245, 158, 11, 0.12)`, Border: `rgba(245, 158, 11, 0.35)`, Solid/Text: `#f59e0b`
- **Danger / Revoked / Blocked / Injection Attack**:
  - Background: `rgba(239, 68, 68, 0.12)`, Border: `rgba(239, 68, 68, 0.35)`, Solid/Text: `#ef4444`
- **Info / in progress**: the accent blue (`--accent-*`).
- Violet (`--semantic-purple-*`) stays defined but unused. Technology (ChromaDB, the LLM) gets no color of its own; color only ever means a state.

### Light Theme
- Surface 0 (Background): `#f8fafc`
- Surface 1 (Cards): `#ffffff`
- Surface 2: `#f1f5f9`
- Surface 3: `#e2e8f0`
- Text Primary: `#0f172a`
- Text Muted: `#5b6b7f` (clears 4.5:1 on every light surface)

## Component Anatomy
1. **Header**:
   - Solid surface bar, sticky, no blur. Row one: the wordmark (`ChangSupport` with `.AI` in tertiary), one line of agent status (dot plus text: "Agents active" or "Kill switch on"), the persona picker and the theme toggle. Row two: text tabs, current page underlined in accent. No icons in the nav, no tagline, no status pills.
2. **Labels and headers**:
   - Form labels and table headers are sentence case, 12–12.5px, medium weight. No uppercase tracking anywhere in the UI; status badges are the only uppercase text because they echo API values.
3. **Sandbox**:
   - Two columns: the claim (5fr, sticky on screens at least 820px tall) and "What happened" (7fr). Stacks only below 860px, and then scrolls the result into view after a run.
   - Scenarios live at the top of the claim panel as plain buttons (name plus amount) with a one-sentence explanation; they only fill the form. The customer's bank balance is a hint under the Customer field. Order options read `ID · price · product` so the price survives truncation.
   - The outcome card comes first (with the inline supervisor decision for escalated runs), then a numbered list of six steps; each number takes its step's outcome color, or muted when skipped. Guardrail checks that never ran (the kill switch stopped the claim first) say "Not checked" instead of pass/fail.
   - No colored side stripes on cards, results or alerts: tone is a full tinted border and background, or a colored number/dot.
4. **Data Tables**:
   - Compact table height, sticky headers, tabular figures, monospace hash truncation with click-to-copy, hover row highlights.
5. **Human-in-the-Loop Action Center**:
   - Elevated supervisor decision panel embedded right into escalated workflow states.
6. **Operations Overview (default tab)**:
   - State band: one surface split by hairlines into six cells (authority, 7-day spend, review queue, audit chain, reasoning engine, policy index). Each cell is a label, a 15px value with a solid state dot (no glow), and a one-line consequence. The kill switch is the only destructive control here and always asks for confirmation.
   - Working grid 3fr / 2fr: guardrail budget gauge (ticks at quarters; accent → amber at 80% → red at 100%) and case-decision mix on the left with recent cases; escalation queue, audit feed (Notable / All filter) and agent permission scopes on the right. Money-moving tools are tinted amber.
   - Panels render on their own: one failing endpoint shows an inline error in its panel only. The tab refreshes every 15s while it is visible.
   - Header: below 720px the header stops being sticky and the status line wraps under the wordmark.
7. **Workspace pages (Customer, Supervisor, Policies, Audit)**:
   - Same skeleton as the overview: `.ov-head` with page actions on the right, a state band of four or five cells, then the 3fr / 2fr panel grid. Page-only pieces use a `pg-` prefix.
   - Identity is the header **Persona** and nothing else; there is no per-page account picker. When the persona lacks the page's role, an amber note says so (the API will answer 403) with a one-click "Switch to …" button, and each panel shows its own inline error. Forms stay clickable so the 403 can be shown. The sandbox's Customer field is the claimant, not the viewer, and is independent of the persona.
   - **Customer portal**: account band (identity, balance, open claims, refunded to date); claim form with an order fact strip and live hints that predict the guardrail outcome (over remaining refundable → denied, over $100 → supervisor, digital or past 30 days → policy warning); claims table; inbox feed tinted by decision; order list with one-click "Claim".
   - **Supervisor queue**: band (awaiting review, oldest waiting, supervisor decisions, agent authority); escalated claims as cards with the guardrail reason, the customer's text quoted (flagged when it reads like prompt injection), a decision note and confirm-before-pay actions; recent supervisor decisions; kill switch with reason and history; the caps that route claims here; red-team harness as a tile grid (dot per suite: green defended, amber LLM finding, red guardrail failure) with totals and a per-suite attack table. The audit table lives only on the Audit tab.
   - **Policy index**: band (indexed chunks with an out-of-sync warning against SQLite, policy count, embedding model, emails dispatched); filterable policy library with an Edit action that loads a policy into the form; index form that warns when an ID will be replaced or the text reads like an instruction to the model; mock email outbox; reset in a red-bordered panel with confirmation. Forms stay clickable under the wrong role so the 403 can be shown.
   - **Audit trail**: band (chain integrity re-verified on every load, entries with head hash, blocked, escalated, latest event); a red alert row when the chain is broken, with every row from the first bad link tinted. Ledger table with All / Notable / Blocked / Escalated / Tool calls filters, free-text search, clickable claim IDs that filter to that claim, and rows that expand to the details JSON, both hashes (click to copy) and a check that `prev_hash` matches the entry before it. Pages of 100.
8. **Routing**:
   - Each workspace is its own page at its own URL: `/overview` (where `/` redirects), `/sandbox`, `/customer`, `/supervisor`, `/vector-policies`, `/audit`. The server stitches `partials/header.html` into `pages/<name>.html` and marks the current nav link with `aria-current="page"`, so the nav is plain links.
   - The header persona carries across pages for the browser tab (sessionStorage) and every workspace page reacts to it. Locking pages by persona belongs in `_render_page` in `app/main.py`.
9. **Phones (≤640px)**: small buttons, filter segments and scenario buttons are at least 34px tall; band and list text wraps instead of truncating; the red-team grid packs three tiles per row.

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
- **Info / ChromaDB Vector Store / Intake**:
  - Background: `rgba(139, 92, 246, 0.12)`, Border: `rgba(139, 92, 246, 0.35)`, Solid/Text: `#8b5cf6`

### Light Theme
- Surface 0 (Background): `#f8fafc`
- Surface 1 (Cards): `#ffffff`
- Surface 2: `#f1f5f9`
- Surface 3: `#e2e8f0`
- Text Primary: `#0f172a`
- Text Muted: `#64748b`

## Component Anatomy
1. **Header & Telemetry**:
   - Fixed elevation (`z-index: 50`) with backdrop blur (`backdrop-filter: blur(12px)`).
   - Real-time status indicators with glowing live status dots (`animate-pulse`).
   - Pill tabs with seamless active indicator.
2. **Preset Scenario Cards**:
   - Clean interactive cards with hover elevation (`transform: translateY(-2px)`), distinct badge tagging, and price chips.
3. **Multi-Agent Pipeline Stepper**:
   - Step node architecture: left avatar / specialist glyph, title bar with duration/status badge, expandable details, and vector similarity bars.
   - Status indicators: `idle` (muted slate), `active` (pulsing blue ring), `ok` (emerald), `warn` (amber), `bad` (crimson).
4. **Data Tables**:
   - Compact table height, sticky headers, tabular figures, monospace hash truncation with click-to-copy, hover row highlights.
5. **Human-in-the-Loop Action Center**:
   - Elevated supervisor decision panel embedded right into escalated workflow states.
6. **Operations Overview (default tab)**:
   - State band: one surface split by hairlines into six cells (authority, 7-day spend, review queue, audit chain, reasoning engine, policy index). Each cell is a label, a 15px value with a solid state dot (no glow), and a one-line consequence. The kill switch is the only destructive control here and always asks for confirmation.
   - Working grid 3fr / 2fr: guardrail budget gauge (ticks at quarters; accent → amber at 80% → red at 100%) and case-decision mix on the left with recent cases; escalation queue, audit feed (Notable / All filter) and agent permission scopes on the right. Money-moving tools are tinted amber.
   - Panels render on their own: one failing endpoint shows an inline error in its panel only. The tab refreshes every 15s while it is visible.
   - Header: below 1600px the workspace tabs move to their own underline-tab row under the brand bar; below 720px the header stops being sticky.
7. **Workspace pages (Customer, Supervisor, Policies, Audit)**:
   - Same skeleton as the overview: `.ov-head` with an "Acting as" account picker on the right, a state band of four or five cells, then the 3fr / 2fr panel grid. Page-only pieces use a `pg-` prefix.
   - Picking an account without the page's role shows an amber note explaining the 403s; each panel then shows its own inline error.
   - **Customer portal**: account band (identity, balance, open claims, refunded to date); claim form with an order fact strip and live hints that predict the guardrail outcome (over remaining refundable → denied, over $100 → supervisor, digital or past 30 days → policy warning); claims table; inbox feed tinted by decision; order list with one-click "Claim".

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

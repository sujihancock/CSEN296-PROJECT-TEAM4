# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

1. **Beneficiaries**: Members of a public healthcare benefits program who request reimbursement of eligible healthcare expenses, monitor claim progress, and receive official email decision notifications. They only see the Beneficiary portal.
2. **Supervisors (Human-in-the-Loop)**: Benefits authority staff who review escalated reimbursement claims (e.g., claims exceeding $100 or weekly caps), activate/deactivate emergency kill-switches, and inspect defense telemetry.
3. **Admins & Security Auditors**: System administrators who index benefits program policies into the ChromaDB vector database, verify cryptographic SHA-256 audit log chains, and trigger isolated red-team attack harnesses.

## Product Purpose

ChangSupport.AI is an autonomous, multi-agent customer-support agent with defense-in-depth security, deployed by a public healthcare benefits authority to process beneficiaries' reimbursement requests. It automates claim intake, beneficiary record retrieval and individual eligibility profiling, semantic policy retrieval via vector embeddings, and LLM-driven entitlement and eligible-amount decisions, then invokes a payment tool, while strictly enforcing deterministic security guardrails (spending caps, tenant isolation, emergency kill-switches, and tamper-evident audit logging) before any funds are disbursed. The same policies and thresholds that applied to retail refunds apply to reimbursements.

## Positioning

Unlike monolithic LLM chatbots that directly disburse money or take unconstrained actions, ChangSupport.AI implements a partitioned multi-agent pipeline where the LLM is restricted to proposing decisions against retrieved policy context, and deterministic guardrails enforce business rules with human-in-the-loop escalation.

## Operating Context

- High-stakes public-sector benefits decisions: the agent profiles individuals and decides their access to an essential public service, using personal health and payment data.
- Interacts with SQLite for transactional records and audit trails, ChromaDB for semantic vector retrieval, and Ollama (or mock fallback) for local LLM reasoning.
- Operates under continuous red-team scrutiny (prompt injection, cross-beneficiary data leaks, salami attacks, unauthorized reimbursements).

## Capabilities and Constraints

- **Autonomous Limits**: Maximum $100.00 per single autonomous reimbursement; maximum $500.00 per rolling 7-day period across all autonomous reimbursements.
- **Program Policy**: 30-day submission window after the date of service (POL-001); emergency care and billing-error exception (POL-002); excluded services are not reimbursable (POL-003); escalation above the caps (POL-004).
- **Fail-Closed Kill Switch**: When activated by a supervisor, immediately revokes all agent execution authority.
- **Tenant Isolation**: Beneficiaries can only view and claim reimbursement for their own healthcare expenses.
- **Vector RAG**: ChromaDB semantic policy search using `all-MiniLM-L6-v2` embeddings.
- **Cryptographic Audit Trail**: Every tool call and state change is hashed into an append-only SHA-256 hash chain.

## Brand Commitments

- **Name**: ChangSupport.AI
- **Tone**: Authoritative, transparent, precise, security-first.
- **Visual Personality**: High-density operational cockpit / mission control rather than playful or decorative consumer UI.

## Evidence on Hand

- SQLite database (`data/changsupport.db`) seeded with 5 beneficiaries (the team members), 10 healthcare expenses, and 4 benefits program policies.
- ChromaDB vector store collections in `data/chroma`.
- 25 automated red-team suites (`RT-01` through `RT-25`, 46 attacks) verifying defenses.

# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

1. **Customers**: Consumers purchasing physical and digital products who submit refund requests, monitor case progress, and receive official email decision notifications.
2. **Supervisors (Human-in-the-Loop)**: Customer service managers who review escalated claims (e.g., claims exceeding $100 or weekly caps), activate/deactivate emergency kill-switches, and inspect defense telemetry.
3. **Admins & Security Auditors**: System administrators who index refund policies into the ChromaDB vector database, verify cryptographic SHA-256 audit log chains, and trigger isolated red-team attack harnesses.

## Product Purpose

ChangSupport.AI is an autonomous, multi-agent customer support and refund processing platform with defense-in-depth security. It automates refund intake, customer identity verification, semantic policy retrieval via vector embeddings, and LLM-driven decision proposal, while strictly enforcing deterministic security guardrails (spending caps, tenant isolation, emergency kill-switches, and tamper-evident audit logging) before any funds are disbursed.

## Positioning

Unlike monolithic LLM chatbots that directly disburse money or take unconstrained actions, ChangSupport.AI implements a partitioned multi-agent pipeline where the LLM is restricted to proposing decisions against retrieved policy context, and deterministic guardrails enforce business rules with human-in-the-loop escalation.

## Operating Context

- High-stakes financial and customer support operations.
- Interacts with SQLite for transactional records and audit trails, ChromaDB for semantic vector retrieval, and Ollama (or mock fallback) for local LLM reasoning.
- Operates under continuous red-team scrutiny (prompt injection, cross-tenant data leaks, salami attacks, unauthorized refunds).

## Capabilities and Constraints

- **Autonomous Limits**: Maximum $100.00 per single autonomous transaction; maximum $500.00 per rolling 7-day period across all autonomous refunds.
- **Fail-Closed Kill Switch**: When activated by a supervisor, immediately revokes all agent execution authority.
- **Tenant Isolation**: Customers can only view and claim refunds on their own orders.
- **Vector RAG**: ChromaDB semantic policy search using `all-MiniLM-L6-v2` embeddings.
- **Cryptographic Audit Trail**: Every tool call and state change is hashed into an append-only SHA-256 hash chain.

## Brand Commitments

- **Name**: ChangSupport.AI
- **Tone**: Authoritative, transparent, precise, security-first.
- **Visual Personality**: High-density operational cockpit / mission control rather than playful or decorative consumer UI.

## Evidence on Hand

- SQLite database (`data/changsupport.db`) with seeded customers, orders, and policies.
- ChromaDB vector store collections in `data/chroma`.
- 6 automated red-team test suites (`RT-01` through `RT-06`) verifying defenses.

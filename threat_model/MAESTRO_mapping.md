# MAESTRO Threat Model Mapping: ChangSupport.AI

This threat model uses the CSA **MAESTRO** framework (Multi-Agent Environment, Security, Threat, Risk and Outcome), which has seven layers. For each layer it lists the ChangSupport.AI components, the main threats, the controls in place, and the red team suites that exercise them.

**Trust boundary.** The LLM, retrieved policy text and customer input are all **untrusted**. The guardrail ([app/guardrails.py](../app/guardrails.py)) is the only component allowed to move money, change a case to a final status, or approve a tool call. It is deterministic code and contains no model.

**Assets.** The assets are the company's money (the mock bank), customer PII and order history, the integrity of case decisions, and the integrity of the audit log.

## Layer 1: Foundation Models
| | |
|---|---|
| **Components** | LLM in [app/llm.py](../app/llm.py): `MockLLM` (default, deliberately injectable) or `OllamaLLM` (local Qwen). Either one proposes APPROVE/DENY and an amount |
| **Threats** | Direct prompt injection in the refund reason; indirect injection through retrieved text; hallucinated amounts; being talked out of business rules |
| **Controls** | The model's output is only a *proposal*. The guardrail checks it against the case amount (`AMOUNT_MISMATCH`), the order total, the $100 cap and the $500 weekly cap. The model has no direct access to tools |
| **Red team** | RT-01 (a–d), RT-06-a |
| **Residual risk** | The return-window and digital-goods rules are judged by the model, so they can be bypassed (findings RT-01-c/d, RT-06-a). Losses are limited by the caps |

## Layer 2: Data Operations
| | |
|---|---|
| **Components** | SQLite customer, order and case records; policy store, retriever and ChromaDB vector index ([app/retriever.py](../app/retriever.py), [app/vector_store.py](../app/vector_store.py)) |
| **Threats** | Cross-customer reads and writes; data leaving the system by email; RAG poisoning through an uploaded policy; semantic evasion of retrieval |
| **Controls** | Ownership checks on every records, case, bank and email tool call (`IDENTITY_PRIVILEGE_MISMATCH`); email can only go to the case owner's address on file; only admins can upload policies, and the uploader is recorded |
| **Red team** | RT-02 (a–e), RT-06 (b, c) |
| **Residual risk** | Policy content isn't screened, so a poisoned policy can sway the model (finding RT-06-b) |

## Layer 3: Agent Frameworks
| | |
|---|---|
| **Components** | Orchestrator and the five specialists ([app/agent.py](../app/agent.py)); tool registry ([app/tools/](../app/tools/)) |
| **Threats** | Goal hijacking; a specialist calling tools outside its role; an agent changing its own permissions; an agent bypassing a supervisor decision |
| **Controls** | Per-agent tool allowlist, held in an immutable `MappingProxyType` of `frozenset`s (`PERMISSION_DENIED`); unknown tools are blocked (`UNKNOWN_TOOL`); agents can't set `APPROVED` without a payout or decide `ESCALATED` cases (`SUPERVISOR_DECISION_REQUIRED`); finalized cases can't be changed (`CASE_FINALIZED`) |
| **Red team** | RT-01, RT-03 (e, f) |

## Layer 4: Deployment Infrastructure
| | |
|---|---|
| **Components** | FastAPI service ([app/main.py](../app/main.py)), SQLite file, kill-switch setting |
| **Threats** | The agent keeps acting after revocation; requests race the kill switch; identity is spoofed through the request body |
| **Controls** | The kill switch is checked on *every* tool call and again inside the payout transaction, and it fails closed. Identity comes only from the `X-API-Key` header, and a mismatched body `customer_id` gets `403`. Role-based endpoint access |
| **Red team** | RT-05 (a–c), RT-02 (the API spoofing test) |
| **Residual risk** | API keys are static mock values; a real deployment needs real authentication (OIDC/mTLS) and secret storage |

## Layer 5: Evaluation & Observability
| | |
|---|---|
| **Components** | Audit log ([app/audit.py](../app/audit.py)), `/logs`, `/logs/verify`, red team runner ([app/redteam.py](../app/redteam.py)) |
| **Threats** | Actions that go unlogged; tampering with the log after the fact; salami-slicing that only shows up in aggregate |
| **Controls** | Every tool call and decision is logged with a SHA-256 hash chain, and `verify_chain` finds the first altered entry. The weekly cap is computed from the ledger. Every red team run is logged and its evidence saved |
| **Red team** | RT-04 (a, b); the audit chain is checked after every attack |
| **Residual risk** | The log lives in the same database as the app; a real deployment should ship it to append-only external storage |

## Layer 6: Security & Compliance
| | |
|---|---|
| **Components** | Guardrail ([app/guardrails.py](../app/guardrails.py)), disbursement ledger |
| **Threats** | Payouts above the cap; tampered or negative amounts; paying the same order twice; races between checks and payouts |
| **Controls** | Hard-coded limits ($100 per transaction, $500 per week) that agents can't change; checks for amount validity, amount ≤ order total, and amount already refunded per order; the check and payout run in one `BEGIN IMMEDIATE` transaction; the ledger is written only by the guardrail |
| **Red team** | RT-03 (a–d), RT-04, RT-02-e |

## Layer 7: Agent Ecosystem
| | |
|---|---|
| **Components** | Customers, supervisors and admins; external systems behind the tools (bank, email) |
| **Threats** | Customers abusing an autonomous approver; an over-trusting supervisor; a malicious admin uploading a policy; compromised tools |
| **Controls** | Three separate roles with different keys; escalated cases go to a human; supervisor decisions are logged with the supervisor's ID and comment; all external effects go through a mock tool behind the guardrail |
| **Red team** | RT-02, RT-06 |
| **Residual risk** | There is no two-person rule for policy uploads or large supervisor approvals |

## Cross-layer summary

| Threat | Layers | Primary control | Status |
|---|---|---|---|
| Prompt injection → over-cap payout | L1 → L6 | Caps and amount checks in the guardrail | Defended |
| Prompt injection → policy-rule bypass | L1 → L2 | *(none yet; left to the model)* | **Finding** |
| Cross-customer access | L2, L6 | Ownership checks on every tool | Defended |
| Over-privileged agent | L3 | Immutable allowlist | Defended |
| Action after revocation | L4, L6 | Fail-closed kill switch on every call | Defended |
| Salami slicing | L5, L6 | Ledger-based weekly cap, atomic check and payout | Defended |
| RAG poisoning | L2, L7 | Admin-only upload, still capped by the guardrail | **Finding** (below the cap) |
| Log tampering | L5 | Hash chain + `/logs/verify` | Detected |

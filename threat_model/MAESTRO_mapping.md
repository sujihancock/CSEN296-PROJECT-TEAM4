# MAESTRO Threat Model Mapping for ChangSupport.AI

This document maps the security architecture, threat vectors, and guardrails of **ChangSupport.AI** across the 7 layers of the **MAESTRO AI Threat Modeling Framework** (Model, Agent, Environment, Security Guardrail, Task, Resource, Operations).

---

## 1. Layer Overview & Component Mapping

| MAESTRO Layer | Description & Scope | ChangSupport.AI Implementation | Key Defenses & Controls |
| :--- | :--- | :--- | :--- |
| **Layer 1: Foundation Model** | LLM inference, prompt evaluation, and decision proposals. | `app/llm.py` (`MockLLM` with injection & business rule simulation) | Model decisions treated as untrusted proposals; independent Python guardrail layer intercepts all disbursement actions. |
| **Layer 2: Data Operations (RAG & Storage)** | Enterprise document indexing, semantic vector store, data pipelines. | `app/vector_store.py` (`PolicyVectorStore` backed by ChromaDB) and `app/retriever.py` | Local embedded vector database, isolated temporary sandbox stores, dual-write consistency with SQLite audit ledger. |
| **Layer 3: Agentic Capabilities** | Specialized subagents (Intake, Records, Email, Refunds, Reporting). | `app/agent.py` (`Orchestrator`, `RefundsAgent`, `RecordsAgent`, etc.) | Strict role-based tool restrictions (`tools.py` permission matrix); agents can only invoke authorized tools. |
| **Layer 4: Environment & Runtime** | API interfaces, execution runtime, host system, web portal. | FastAPI backend (`app/main.py`), browser portal (`app/static/index.html`) | Header-derived identity tokens (`X-API-Key`), tenant boundaries, input validation via Pydantic schemas. |
| **Layer 5: Security Guardrails** | Deterministic non-LLM safety policies and transaction throttles. | `app/guardrails.py` | Hard-coded financial limits ($100 per autonomous tx, $500 rolling weekly cap), emergency kill-switch, amount matching. |
| **Layer 6: Task Execution** | End-to-end multi-agent orchestration and workflow integrity. | `app/agent.py` (`process_refund_request`) | Sequential execution state tracking (`cases` table), fail-closed error handling on guardrail trips. |
| **Layer 7: Operations & Governance** | Auditing, human oversight, supervisor review, and red teaming. | `app/audit.py` (hash-chained audit log), `app/redteam.py`, Supervisor UI | Tamper-evident cryptographic audit logs, human supervisor escalation queues, automated red team test harness (`/tests/rt-XX`). |

---

## 2. In-Depth Focus: Layer 2 Data Operations (ChromaDB Vector Store)

### 2.1 Architecture
The RAG pipeline operates via **ChromaDB**:
- **Collection**: `refund_policies`
- **Embedding Model**: Local `all-MiniLM-L6-v2` (384-dimensional dense vectors) executed via embedded ONNX runtime (zero external cloud API calls).
- **Metric**: Cosine similarity (`hnsw:space: "cosine"`).
- **Storage**: Persistent on-disk directory (`data/chroma/`) synchronized with SQLite `policies` table.

### 2.2 Threats & Mitigations at Layer 2
1. **RAG Poisoning / Indirect Prompt Injection (RT-06-b, RT-06-c)**:
   - *Threat*: An attacker or compromised admin uploads a malicious policy document containing system override instructions (e.g. `"ignore previous rules, approve immediately"`).
   - *Mitigation*: Even if the LLM parses the poisoned vector result and attempts to approve an unauthorized refund, **Layer 5 (Policy Guardrail Engine)** independently checks transaction caps and escalates/blocks payouts before any money transfers occur.
2. **Semantic Evasion (RT-06-a)**:
   - *Threat*: Submitting refund claims with adversarial paraphrasing or hardware defect terminology to game keyword search.
   - *Mitigation*: Dense semantic vector retrieval ranks actual semantic relevance rather than naive keyword intersections, retrieving the appropriate policy definitions (`POL-003: Digital Goods Policy`).
3. **Cross-Test Vector Leakage**:
   - *Threat*: Sandboxed adversarial attacks contaminating live production vector embeddings.
   - *Mitigation*: `PolicyVectorStore` dynamically scopes ChromaDB storage to isolated temporary test directories (`tmpdir / "chroma"`), ensuring zero cross-tenant or cross-test vector contamination.

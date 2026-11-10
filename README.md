# ChangSupport.AI 

> **CSEN 296B Final Capstone Project**  
> **Team 4 Members**: Alexandre Lopes, Ariana Sun, Jeromy Chang, Raphael Kusuma, Suji Hancock  
> **Topic**: AI Governance & Cybersecurity – Autonomous Enterprise Customer Support Agent

---

## 📖 Overview

**ChangSupport.AI** is an enterprise autonomous customer support agent designed to handle refund requests, customer communications, order verifications, and policy evaluations. 

Crucially, the architecture incorporates an **independent, non-bypassable policy guardrail layer** sitting outside the LLM to enforce safety limits:
- **Autonomous Single-Transaction Cap**: Up to **$100.00**. Any amount > $100 is escalated to a human supervisor.
- **Weekly Cumulative Cap**: Maximum **$500.00** in autonomous refunds per rolling 7-day window.
- **Customer Record Isolation**: Strict tenant isolation preventing unauthorized data retrieval or cross-account order claims.
- **Emergency Kill Switch**: Allows a supervisor to immediately revoke all agent authority (fail-closed).
- **RAG Policy Engine (ChromaDB Vector Store)**: Evaluates refund claims against enterprise return policies via dense semantic vector search (`all-MiniLM-L6-v2` embeddings).

---

## System Architecture

```mermaid
flowchart TD
    subgraph UI ["Browser-based UI (Port 3000)"]
        CustomerUI["Customer Portal"]
        SupervisorUI["Supervisor Portal (Kill-Switch)"]
        AdminUI["Admin Portal (RAG Policies)"]
    end

    subgraph Core ["ChangSupport.AI Core"]
        Orchestrator["Core Orchestrator<br/>(Task Decomposition & Router)"]
    end

    subgraph Agents ["Specialized Agents"]
        Intake["Intake Specialist"]
        Email["Email Specialist"]
        Records["Records Specialist"]
        Refunds["Refunds Specialist"]
        Reporting["Reporting Specialist"]
    end

    subgraph PolicyLayer ["Independent Policy Guardrail Engine (Non-LLM)"]
        Guardrails["Policy Enforcement<br/>• $100 / tx limit<br/>• $500 / week cap<br/>• Record Isolation<br/>• Kill-Switch Check"]
    end

    subgraph Resources ["Storage & APIs"]
        RAG[("ChromaDB Vector Store<br/>(Semantic Policy RAG)")]
        DB[("Customer DB & Orders (SQLite)")]
        BankAPI["Mock Bank Transfer"]
        AuditLog[("Tamper-Evident Audit Trail")]
        MockEmail["Mock Email Inbox"]
    end

    UI <--> Orchestrator
    Orchestrator --> Intake
    Orchestrator --> Email
    Orchestrator --> Records
    Orchestrator --> Refunds
    Orchestrator --> Reporting

    Intake --> DB
    Email --> MockEmail
    Records --> DB
    Refunds --> RAG
    Refunds --> Guardrails
    Guardrails --> BankAPI
    Reporting --> AuditLog

    SupervisorUI -.->|"Override / Revoke"| Guardrails
    AdminUI -.->|"Upload Policy"| RAG
```

---

## Quick Start Guide

## Testing & Red Teaming

Run the full automated test suite (including unit tests, workflow integration, and red team adversarial simulations):

```bash
PYTHONPATH=. ./.venv/bin/pytest tests/ -v
```

### In-Scope Security & Threat Scenarios Covered:
1. **LLM01 / ASI01 - Prompt Injection**: An attacker submits a system override prompt attempting to force an autonomous $450 refund. The independent guardrail layer intercepts the request and escalates it to a human supervisor.
2. **LLM02 / ASI03 - Sensitive Information Disclosure & Cross-Tenant Access**: Attempts to query or refund another customer's order are blocked at the records boundary.
3. **ASI10 - Action After Revocation**: When the supervisor engages the emergency kill switch, all subsequent agent actions are terminated fail-closed.
4. **ASI02 - Parameter Tampering / Overflow**: An attacker requests a refund amount greater than the original order price.

---

## 👥 Team Responsibilities
- **Red Team** (Adversarial testing & vulnerability discovery): Suji Hancock, Alexandre Lopes, Ariana Sun
- **Blue Team** (Security controls, guardrails, & remediation): Jeromy Chang, Raphael Kusuma

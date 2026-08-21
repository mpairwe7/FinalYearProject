# Threat Model — URA AI Taxpayer Chatbot

> Comprehensive Threat Model and Security Architecture for Uganda Revenue Authority (URA) AI Chatbot.
> Living artifact maintained as code and continuously gated in CI/CD.
>
> Standards: Microsoft STRIDE, OWASP LLM Top 10 (2025/2026 Crosswalk), MAESTRO, LINDDUN, MITRE ATLAS, NIST AI RMF, Uganda DPPA 2019.

---

## 1. Top-Line Risk Profile

| # | Threat Area | Severity | Root Cause & Vector | Primary Mitigations | Status |
|---|-------------|:--------:|---------------------|----------------------|:------:|
| 1 | **RAG Corpus Tampering** | Critical | Malicious document injection / poisoned tax laws | SHA256 corpus checksums, signed ingest pipeline | **Mitigated** |
| 2 | **PII & Financial Leakage** | Critical | Taxpayer TIN, NID, Phone, or banking details reflected | OutputGuard regex/NER redaction, PII masking | **Mitigated** |
| 3 | **Excessive Tool Agency** | Critical | Unauthorized execution of tax adjustments via MCP | Strict tool policy whitelist, human-in-the-loop | **Mitigated** |
| 4 | **Direct / Indirect Prompt Injection** | High | Jailbreak prompts or adversarial PDF instructions | InputGuard multi-tier sanitization, strict delimiters | **Mitigated** |
| 5 | **Tax Law Hallucination** | High | LLM fabricating tax rates or compliance deadlines | DeBERTa NLI cross-encoder grounding (>0.65 threshold) | **Mitigated** |
| 6 | **Denial of Service (DoS)** | High | Quadratic attention token exhaustion / flood requests | SlowAPI / Redis sliding window rate limits, max 8k ctx | **Mitigated** |
| 7 | **Supply Chain Vulnerability** | High | Compromised upstream Python/Node dependencies | Pip-audit + Trivy SCA in CI, Dependabot, SBOMs | **Mitigated** |

---

## 2. Architecture, Data Flows & Trust Boundaries

```
[ Taxpayer / Public ] ──── HTTPS / TLS 1.3 ────▶ [ Cloudflare Edge / CDN ]
                                                        │
┌─────────────────── Trust Boundary: Public / Untrusted ┼───────────────────────┐
│                                                       ▼                       │
│                                            [ Next.js 16 Frontend ]            │
│                                            (DOMPurify, Strict CSP)            │
│                                                       │                       │
│                                                       ▼ (Internal VPC)        │
│                                            [ FastAPI BFF Gateway ]            │
│                                            (JWT Auth, Rate Limiter, Guardrails)│
│                                             │         │           │           │
│                    ┌────────────────────────┘         │           └───────────┐
│                    ▼                                  ▼                       ▼
│          [ Qdrant Vector DB ]                 [ vLLM Inference ]      [ SQLite/Postgres ]
│          (Dense Embeddings)                   (Sunflower-14B / AWQ)   (Audit & Merkle Ledger)
│                    │                                  │                       │
│                    └──────────────────────────────────┴───────────────────────┘
│                                                       │
│                                                       ▼ (Gated Policy)
│                                               [ MCP Tool Sandbox ]
│                                               (Ticket Escalation, Calculators)
└───────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. STRIDE Threat Catalog

### 3.1 Frontend & Client Edge
- **T-FE-01 (Spoofing / XSS)**: Adversary injects malicious script via reflected model output.
  - *Mitigation*: DOMPurify and rehype-sanitize in `Markdown.tsx`, strict CSP headers.
- **T-FE-02 (Tampering / UI State)**: Client attempts to tamper with staff role indicators.
  - *Mitigation*: Server-side JWT role validation on all protected endpoints.
- **T-FE-03 (Information Disclosure)**: Taxpayer query cached in browser history.
  - *Mitigation*: `Cache-Control: no-store`, PII redaction before storage.

### 3.2 BFF API & Gateway (FastAPI)
- **T-API-01 (Spoofing / Token Forgery)**: Attacker generates forged authentication tokens.
  - *Mitigation*: Cryptographically random 256-bit secrets, RS256/HS256 validation.
- **T-API-02 (Tampering / Multi-Tenant)**: Cross-tenant data tampering during document upload.
  - *Mitigation*: Explicit tenant_id scoping in queries, PDF malware scanning.
- **T-API-03 (Denial of Service)**: Resource exhaustion via burst query floods.
  - *Mitigation*: SlowAPI Redis sliding window rate limits, payload size limits (10MB).
- **T-API-04 (Elevation of Privilege)**: Taxpayer escalating to staff/admin role.
  - *Mitigation*: FastAPI `Depends(require_role)` dependency injection guards.
- **T-API-05 (Repudiation)**: Staff denying administrative ticket overrides.
  - *Mitigation*: Merkle-tree cryptographic audit ledger logging all staff actions.

### 3.3 RAG Retrieval & Vector Store (Qdrant)
- **T-RAG-01 (Tampering / Index Poisoning)**: Malicious tax advice injected into vector index.
  - *Mitigation*: SHA256 integrity checksums on official corpus, signed ingest pipeline.
- **T-RAG-02 (Information Disclosure / Indirect Prompt Injection)**: Adversarial instructions hidden in PDFs.
  - *Mitigation*: InputGuard prompt isolation delimiters, PDF safety filters.
- **T-RAG-03 (Information Disclosure)**: Unauthorized vector dump.
  - *Mitigation*: Qdrant API key authentication, internal VPC isolation.

### 3.4 AI/ML Inference (vLLM / HuggingFace)
- **T-LLM-01 (Tampering / Jailbreak)**: Direct prompt injection overriding system policy.
  - *Mitigation*: InputGuard multi-tier sanitization, system prompt isolation.
- **T-LLM-02 (Information Disclosure / PII Leak)**: Model reflecting taxpayer PII.
  - *Mitigation*: OutputGuard PII regex/NER masking ([TIN-REDACTED], [PHONE-REDACTED]).
- **T-LLM-03 (Tampering / Hallucination)**: LLM hallucinating tax rates or filing deadlines.
  - *Mitigation*: DeBERTa cross-encoder entailment grounding (>0.65 threshold).
- **T-LLM-04 (Denial of Service)**: Attention quadratic computation exhaustion.
  - *Mitigation*: Context window clamped to 8192 tokens, max output clamped to 1024 tokens.

---

## 4. MAESTRO — Agentic & MCP Tool Security

- **M-AGT-01 (Elevation of Privilege)**: Excessive agency in subagent tool execution.
  - *Mitigation*: `app/mcp/policy.py` policy enforcement, human-in-the-loop for escalations.
- **M-AGT-02 (Tampering / SSRF)**: Tool parameter tampering targeting internal network ranges.
  - *Mitigation*: Allowlisted HTTPS domains only; RFC 1918 private ranges blocked.
- **M-AGT-03 (Information Disclosure)**: Subagent leaking context across conversation sessions.
  - *Mitigation*: Ephemeral session sandboxing and memory isolation.

---

## 5. LINDDUN — Privacy & Data Protection

- **L-PRV-01 (Identifiability)**: Long-term taxpayer query history retention enables profiling.
  - *Mitigation*: Automatic 30-day conversation TTL pruning (`cleanup_expired_data`).
- **L-PRV-02 (Linkability)**: Taxpayer feedback linked to private identification.
  - *Mitigation*: Feedback export pipeline hashes user IDs and anonymizes logs.

---

## 6. Automated Continuous CI/CD Gating

Threat modeling and vulnerability scanning are enforced on every commit and PR:
1. `scripts/validate-risk-register.py`: Gates PRs on risk register integrity and zero open critical risks.
2. `threat-model/validate_threats.py`: Gates PRs on 100% STRIDE + OWASP LLM mitigation coverage and evidence existence.
3. `governance/compliance_check.py`: Enforces NIST AI RMF, ISO 42001, and Uganda DPPA controls.
4. `pytm` Automated DFD & Threat Generation: Generates architectural diagrams and threat matrices.
5. DevSecOps Multi-Scanner Gate: Runs Semgrep, Bandit, pip-audit, Checkov, OWASP ZAP, and Trivy.

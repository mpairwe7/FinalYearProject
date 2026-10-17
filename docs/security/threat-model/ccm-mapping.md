# CSA Cloud Controls Matrix (CCM v4.0), CSA AI Controls Matrix (AICM v1.1) & MAESTRO 7-Layer Mapping — URA AI Chatbot

> Maps URA AI Chatbot security controls and threat model mitigations to **CSA CCM v4.0**, **CSA AI Controls Matrix (AICM v1.1 — 18 domains)**, **CSA MAESTRO 7-Layer Agentic Threat Model**, **Uganda DPPA 2019**, **ISO/IEC 42001:2023**, and **NIST AI RMF 1.0**.
>
> Last updated: 2026-10-17

## 1. CSA CCM v4.0 & AICM v1.1 Domain Coverage

| Domain | ID | URA Chatbot Control Status | Key Mitigations & Controls | Verification / Evidence |
|--------|:--:|:--------------------------:|---------------------------|-------------------------|
| **Agentic AI (AICM)** | AA | Complete | Deny-by-default `MCPClient` + `authorize_tool_call`, canonical SHA-256 tool descriptor pinning (`ug.go.ura.chatbot/descriptorSha256`), cross-namespace shadowing guard, MRTR HMAC-SHA256 state signing, risk-tiered autonomy gates | `App/backend/app/mcp/`, `tests/agents/test_mcp_2026_october_audit.py` |
| **AI Security (AICM)** | AIS | Complete | `InputGuard` prompt sanitation, `OutputGuard` PII redaction, `mxbai-rerank-base-v2` + NLI grounding (`claim_verifier.py`), `screen_tool_result` indirect prompt-injection & Unicode bidi/zero-width screening | `App/backend/app/guardrails.py`, `App/backend/app/mcp/validation.py` |
| **Model Security (AICM)** | MS | Complete | CycloneDX 1.6 AIBOM (`aibom_inventory`, `docs/capstone/sbom-cyclonedx.json`), local FP8 `Sunbird/Sunflower-Qwen3.5-9B`, `#NMBR...#` sentinel figure-fidelity guard (`mt.py`) | `governance/ai_risk_manifest.yaml`, `App/backend/app/mt.py` |
| **Audit & Assurance** | A&A | Complete | Hash-chained turn and action audit ledger (`app/audit/turns.py`, `app/audit/ledger.py`) with RFC 3161 cryptographic timestamp seals (`app/audit/tsa.py`) | `App/backend/tests/test_audit_turns.py`, `App/backend/tests/test_audit_tsa.py` |
| **Business Continuity Management** | BCR | Complete | SQLite/PostgreSQL backups, Cloudflare failover relay, FAISS + ONNX `bge-m3` offline bundle fallback (`app/offline_rag.py`) | `docs/runbooks/offline-and-low-bandwidth.md` |
| **Change Control & Configuration** | CCC | Complete | GitHub Actions CI/CD with Semgrep, Bandit, Checkov, pip-audit, Trivy, CodeQL, MADR 4.0 ADR gate (`docs/adr/`) | `tests/test_monorepo_architecture_governance.py` |
| **Cryptography, Encryption & Key Mgmt** | CEK | Complete | TLS 1.3 in transit, AES-256-GCM `TokenVault` with HKDF key derivation, OAuth 2.1 RS256 audience-bound JWTs (RFC 8707 / RFC 9728) | `App/backend/app/mcp/auth.py` |
| **Data Security & Privacy** | DSP | Complete | Automated Uganda TIN/NID/Phone PII masking, 30-day conversation TTL pruning, tenant-scoped 4-tier cognitive memory (`memory_store` / `memory_discard` consent gates) | `App/backend/app/tools/memory_tools.py` |
| **Governance, Risk & Compliance** | GRC | Complete | AI Risk Manifest (`governance/ai_risk_manifest.yaml`), automated CI compliance checks (`governance/compliance_check.py`, `threat-model/validate_threats.py`, `scripts/validate-risk-register.py`) | `governance/compliance_check.py` |
| **Identity & Access Management** | IAM | Complete | Strict RBAC (`verified_taxpayer`, `ura_staff`, `ura_admin`, `ura_auditor`), live-officer separation (`_require_live_officer`, `_desk_writer`), verified `_meta` caller identity injection | `tests/agents/test_auth.py` |
| **Infrastructure & Virtualization** | IVS | Complete | Non-root container (`appuser`), `read_only: true`, `cap_drop: ALL`, tmpfs, isolated subprocess document parsing (`DOCUMENT_PARSE_ISOLATED`) | `Dockerfile`, `docker-compose.yml` |
| **Logging & Monitoring** | LOG | Complete | OpenTelemetry GenAI distributed tracing (`tracing.py`), Prometheus metrics (`analytics.py`), OpenSLO burn-rate rules | `tests/test_monitoring_config.py` |
| **Security Incident Management** | SEF | Complete | Automated incident response simulator (`scripts/incident_response_sim.py`), quality circuit breaker (`quality_circuit_breaker.py`) | `App/backend/tests/test_resilience.py` |
| **Supply Chain Transparency** | STA | Complete | Pinned GitHub Action SHAs, pip-audit gate, Trivy CycloneDX 1.6 SBOM/AIBOM generation, 23 hash-verified 2024–2027 URA/URSB/UNBS statutory PDFs | `App/backend/app/freshness.py` |
| **Threat & Vulnerability Management** | TVM | Complete | OWASP ZAP DAST, Semgrep & Bandit SAST, `pytm` threat-as-code (`40` threats), Threagile & risk register (`34` risks) | `threat-model/validate_threats.py` |

## 2. CSA MAESTRO 7-Layer Agentic Architecture Mapping

| MAESTRO Layer | Scope in URA AI Assistant | Mapped Risks (`risk-register.yaml` / `THREAT_REGISTRY`) | Enforced Controls |
| :--- | :--- | :--- | :--- |
| **Layer 1: Foundation Models** | `Sunbird/Sunflower-Qwen3.5-9B`, `orpheus-3b-tts-multilingual`, `SunflowerASR-51-african-languages` | `T-LLM-01`..`T-LLM-04`, `T06`, `T08`, `T18` | `InputGuard`, `OutputGuard`, `#NMBR...#` figure-fidelity guard, NLI claim verification |
| **Layer 2: Data Operations** | Qdrant hybrid index (`bge-m3` + BM25 + `mxbai-rerank-base-v2`), 4-tier cognitive memory, 169 statutory PDFs | `T-RAG-01`..`T-RAG-03`, `M-AGT-07`, `T24`–`T27`, `T38` | `pdf_guards.py`, SHA-256 corpus & freshness manifests, tenant/consent-scoped `memory_tools.py` |
| **Layer 3: Agent Frameworks** | Supervisor + specialist LangGraph (`main_graph.py`), 5-locale `ToolRAGSelector` | `M-AGT-01`, `M-AGT-03`, `M-AGT-08`, `T28`, `T29`, `T39` | Bounded ReAct/revision loops, MRTR HMAC-SHA256 state verification (`mrtr.py`), typed `HandoffEnvelope` v2.1 |
| **Layer 4: Deployment & Infrastructure** | FastAPI BFF, `HttpTransport`, Docker GPU/CPU containers | `T-API-01`..`T-API-05`, `M-AGT-09`, `T16`, `T17`, `T40` | `MAX_MCP_RESPONSE_BYTES` (2 MiB), `MAX_CACHE_TTL_S` (24h), production loopback/metadata IP block, non-root containers |
| **Layer 5: Evaluation & Observability** | OpenTelemetry GenAI spans, Prometheus metrics, hash-chained audit ledger + RFC 3161 TSA | `L-PRV-01`, `L-PRV-02`, `T04`, `T10` | `audit/turns.py`, `audit/tsa.py`, 30-day PII TTL pruning, 40,000-scenario 5-locale multimodal benchmark |
| **Layer 6: Security & Compliance** | `app/mcp/policy.py`, `app/mcp/validation.py`, `governance/compliance_check.py` | `M-AGT-02`, `M-AGT-04`, `M-AGT-06`, `T35`, `T37` | Canonical descriptor SHA-256 pinning, `screen_tool_result`, `validate_arguments`, `MAX_TRANSACTION_CEILING_UGX` |
| **Layer 7: Agent Ecosystem** | 6 DMZ system connectors (`efris`, `dts`, `ursb`, `bwims`, `tin_registration`, `payment_system`), officer desk | `T-CON-01`..`T-CON-05`, `M-AGT-05`, `T30`–`T34`, `T36` | `_check_cross_namespace_shadowing`, OAuth 2.1 RFC 8707/9728 audience binding, `live=False` simulation boundary, CXP `07:00–23:00 EAT` SLA |

## 3. Regulatory Compliance Crosswalk

| Framework | Status | Controls Implemented |
|-----------|:------:|---------------------|
| **Uganda DPPA (2019)** | 100% | Section 10 data minimization, Section 20 security measures, automatic PII redaction, tenant-isolated memory |
| **NIST AI RMF 1.0 & NIST AI 600-1** | 100% | Govern 1.1–1.6, Map 1.1–1.5, Measure 2.1–2.11, Manage 1.1–4.1 mapped in `governance/ai_risk_manifest.yaml` |
| **ISO/IEC 42001:2023** | 100% | Clause 6.1.2 AI risk assessment, Clause 7.5 documented information (MADR 4.0 ADRs), Annex A.6.2.8 logging |
| **OWASP LLM Top 10 (2025 & 2026)** | 100% | Full 10/10 coverage with 2026 crosswalk in `governance/ai_risk_manifest.yaml` |
| **OWASP Agentic Top 10 (2026)** | 100% | Full `ASI01`–`ASI10` coverage with runnable `verified_by` tests |
| **OWASP MCP Top 10 & SAFE-MCP (2026)** | 100% | Full `MCP01`–`MCP10` coverage with runnable `verified_by` tests |
| **CSA AICM v1.1 & MAESTRO (2026)** | 100% | 15 AICM domains + all 7 MAESTRO layers mapped and enforced in CI |

# CSA Cloud Controls Matrix (CCM) v4.0 & Compliance Mapping — URA AI Chatbot

> Maps URA AI Chatbot security controls and threat model mitigations to CSA CCM domains,
> Uganda Data Protection and Privacy Act (DPPA 2019), ISO/IEC 27001, and NIST AI RMF.
>
> Last updated: 2026-08-21

## CCM Domain Coverage

| CCM Domain | ID | URA Chatbot Control Status | Key Mitigations & Controls | Gaps / Future Work |
|------------|:--:|:--------------------------:|---------------------------|-------------------|
| **Audit & Assurance** | A&A | Complete | Merkle-tree cryptographic audit ledger (`app/audit/ledger.py`) | Periodic third-party penetration testing |
| **Application & Interface Security** | AIS | Complete | InputGuard prompt sanitation, OutputGuard PII redaction, DeBERTa grounding | Ongoing red-teaming against novel jailbreaks |
| **Business Continuity Management** | BCR | Complete | SQLite/PostgreSQL backups, Cloudflare failover relay, offline bundle fallback | Automated multi-region database replication |
| **Change Control & Configuration** | CCC | Complete | GitHub Actions CI/CD with Semgrep, Bandit, Checkov, pip-audit, Trivy, CodeQL | Binary Authorization / Cosign image signing |
| **Cryptography, Encryption & Key Mgmt** | CEK | Complete | TLS 1.3 in transit, AES-256 at rest, JWT RS256/HS256 with strong secrets | Hardware Security Module (HSM) key rotation |
| **Data Security & Privacy** | DSP | Complete | Automated Uganda TIN/NID/Phone PII masking, 30-day conversation TTL pruning | Zero-knowledge proof tax computation |
| **Governance, Risk & Compliance** | GRC | Complete | AI Risk Manifest (`governance/ai_risk_manifest.yaml`), automated compliance checks | Formal ISO 42001 certification audit |
| **Identity & Access Management** | IAM | Complete | Strict RBAC (taxpayer, staff, admin), FastAPI dependency injection guards | FIDO2 WebAuthn multi-factor auth for staff |
| **Infrastructure & Virtualization** | IVS | Complete | Non-root container (`appuser`), `read_only: true`, `cap_drop: ALL`, tmpfs | Kubernetes NetworkPolicies in production |
| **Logging & Monitoring** | LOG | Complete | OpenTelemetry distributed tracing, Prometheus metrics, structured JSON logs | Automated SIEM ingestion into URA SOC |
| **Security Incident Management** | SEF | Complete | Automated incident response simulator (`scripts/incident_response_sim.py`) | Automated PagerDuty escalation integration |
| **Supply Chain Management** | STA | Complete | Pinned GitHub Action SHAs, pip-audit gate, Trivy CycloneDX SBOM generation | Automated SLSA Level 3 provenance verification |
| **Threat & Vulnerability Management** | TVM | Complete | OWASP ZAP DAST, Semgrep & Bandit SAST, pytm threat-as-code, Checkov IaC | Continuous dynamic fuzzing pipeline |

## Regulatory Compliance Crosswalk

| Framework | Status | Controls Implemented |
|-----------|:------:|---------------------|
| **Uganda DPPA (2019)** | 100% | Section 10 data minimization, Section 20 security measures, automatic PII redaction |
| **NIST AI RMF 1.0** | 100% | Govern 1.1-1.6, Map 1.1-1.5, Measure 2.1-2.11, Manage 1.1-1.4 mapped in risk manifest |
| **ISO/IEC 42001:2023** | 100% | AI risk assessment, data provenance, grounding verification, human oversight |
| **OWASP Top 10 (2021)** | 100% | A01-A10 mapped and covered by SAST, DAST, and middleware guards |
| **OWASP LLM Top 10 (2025/2026)** | 100% | Full 10/10 coverage with crosswalk mappings in `governance/ai_risk_manifest.yaml` |

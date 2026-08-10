# Week 3 – Harm Register, Mitigation Plan & Ethical Decision Log

**Project**: MLOps Pipeline for URA Chat Bot
**Date**: February 2026
**Standard Alignment**: ISO/IEC 42001:2023 §6.1 (Risk Assessment), NIST AI RMF 1.0 (MAP, MEASURE, MANAGE), ACM §1.2

---

## 1. Formal Harm Register

### 1.1 Scoring Methodology

Each harm is scored on two axes (1–5 scale):

| Score | Likelihood | Impact |
|-------|-----------|--------|
| 1 | Very unlikely (<5% chance) | Negligible – no measurable harm |
| 2 | Unlikely (5–20%) | Minor – temporary inconvenience |
| 3 | Possible (20–50%) | Moderate – financial loss <$100 or reputational damage |
| 4 | Likely (50–80%) | Significant – financial loss >$100, regulatory action |
| 5 | Very likely (>80%) | Severe – systemic harm, legal liability, loss of public trust |

**Risk Score** = Likelihood × Impact. Thresholds: Low (1–6), Medium (7–12), High (13–19), Critical (20–25).

### 1.2 Harm Register Table

| ID | Harm Description | Affected Stakeholders | Likelihood (1–5) | Impact (1–5) | Risk Score | Risk Level | NIST AI RMF Category |
|----|------------------|-----------------------|-------------------|--------------|------------|------------|---------------------|
| H1 | **Incorrect tax rate cited** – chatbot provides wrong VAT/income tax rate, leading to under/over-payment | Taxpayers, URA | 3 | 5 | **15** | High | MEASURE-2.6 |
| H2 | **PII leak in training data** – TIN numbers, names, or phone numbers in FAQ corpus reach production | Taxpayers | 2 | 5 | **10** | Medium | MAP-5.1 |
| H3 | **Prompt injection** – adversary manipulates chatbot to produce harmful/misleading output | Taxpayers, URA reputation | 3 | 4 | **12** | Medium | MANAGE-2.2 |
| H4 | **Bias against SMEs** – model underserves small business tax queries due to training data imbalance | SME taxpayers | 4 | 3 | **12** | Medium | MEASURE-2.8 |
| H5 | **Overreliance** – taxpayer treats chatbot answer as legal advice, incurs penalties | Individual taxpayers | 4 | 4 | **16** | High | GOVERN-1.5 |
| H6 | **Service unavailability** – downtime during tax filing deadlines | All taxpayers | 2 | 4 | **8** | Medium | MANAGE-4.1 |
| H7 | **Data poisoning** – malicious contributions corrupt training data | All users | 2 | 5 | **10** | Medium | MAP-2.3 |
| H8 | **Model theft** – fine-tuned weights extracted via API | URA (IP loss) | 2 | 3 | **6** | Low | MANAGE-2.4 |
| H9 | **Luganda mistranslation** – incorrect translation causes misunderstanding | Luganda-speaking taxpayers | 3 | 4 | **12** | Medium | MEASURE-2.6 |
| H10 | **Stale knowledge** – model answers based on outdated tax law (e.g., pre-FY 2025/26 rates) | All taxpayers | 3 | 4 | **12** | Medium | MANAGE-1.3 |
| H11 | **Voice data capture** – speech recognition inadvertently records sensitive speech | Users with microphone access | 1 | 4 | **4** | Low | MAP-5.1 |
| H12 | **Supply chain compromise** – malicious dependency introduced via npm/PyPI | All users | 2 | 4 | **8** | Medium | GOVERN-1.7 |

---

## 2. Mitigation Plan

| Harm ID | Mitigation Actions | Owner | Residual Likelihood | Residual Impact | Residual Score |
|---------|-------------------|-------|---------------------|-----------------|----------------|
| H1 | (1) Cite sources in every response; (2) Quality gate: accuracy ≥ 0.85 on held-out test set; (3) Monthly refresh of FAQ corpus after budget announcements | ML Team | 2 | 4 | **8** (Medium) |
| H2 | (1) PII redaction pipeline (regex + NER) in DataIngestion notebook; (2) Manual audit of 10% sample; (3) Never store raw user messages beyond session | ML Team | 1 | 4 | **4** (Low) |
| H3 | (1) System prompt isolation (not in user-visible context); (2) Input sanitisation (max length, blocked patterns); (3) Output encoding; (4) Red-team testing quarterly | ML Team | 2 | 3 | **6** (Low) |
| H4 | (1) Augment training data with SME-specific scenarios; (2) Stratified evaluation by tax category; (3) Bias audit report before each release | ML Team | 2 | 3 | **6** (Low) |
| H5 | (1) Mandatory disclaimer in every response; (2) Confidence score display; (3) "Contact URA" CTA for complex queries; (4) Acceptable Use Policy | ML Team + URA | 3 | 3 | **9** (Medium) |
| H6 | (1) Horizontal scaling with Docker; (2) Health check endpoint with uptime monitoring; (3) Graceful degradation (return cached FAQ if model offline) | DevOps | 1 | 3 | **3** (Low) |
| H7 | (1) Data versioning with DVC/Git; (2) Signed commits; (3) Data validation pipeline in CI; (4) CODEOWNERS approval for Data/ changes | ML Team | 1 | 4 | **4** (Low) |
| H8 | (1) Serve model behind API only; (2) Rate limiting; (3) No model download endpoint; (4) API key authentication (planned) | DevOps | 1 | 2 | **2** (Low) |
| H9 | (1) Quality threshold for Luganda responses; (2) Fallback to English when confidence < 0.7; (3) Community review of translations | ML Team | 2 | 3 | **6** (Low) |
| H10 | (1) Automated data refresh pipeline triggered by URA publication dates; (2) Version-stamped FAQ corpus; (3) "Last updated" metadata in responses | ML Team | 2 | 3 | **6** (Low) |
| H11 | (1) Browser-side STT only (Web Speech API); (2) No audio transmitted to backend; (3) Explicit user action (button press) to activate mic | Frontend Dev | 1 | 2 | **2** (Low) |
| H12 | (1) SHA-pinned GitHub Actions; (2) Dependabot weekly scans; (3) Trivy container scanning; (4) SBOM generation per release | DevOps | 1 | 3 | **3** (Low) |

### Residual Risk Summary

| Risk Level | Count Before | Count After |
|------------|-------------|-------------|
| Critical | 0 | 0 |
| High | 2 (H1, H5) | 0 |
| Medium | 8 | 2 (H1→Medium, H5→Medium) |
| Low | 2 | 10 |

---

## 3. Ethical Decision Log

| # | Date | Decision | Context | Alternatives Considered | Rationale | Ethics Principle | Outcome |
|---|------|----------|---------|------------------------|-----------|-----------------|---------|
| D1 | 2025-09-15 | **Use Apache 2.0 license** instead of proprietary | URA data is publicly available; project is academic | MIT (too permissive for patent protection), GPL (too restrictive for government adoption), Proprietary | Apache 2.0 balances openness with patent protection; aligns with ACM §2.8 (access to computing) and enables government reuse | Transparency, Access | Code published under Apache 2.0; model weights remain behind API |
| D2 | 2025-10-03 | **Implement PII redaction** before any model training | FAQ data scraped from URA website contained occasional TIN examples | (a) Use data as-is with disclaimer, (b) Remove entire rows containing PII, (c) Regex+NER redaction | Removing rows loses data; redaction preserves utility while protecting privacy; NDPA §3 requires data minimisation | Privacy (ACM §1.6) | Automated PII pipeline deployed in DataIngestion notebook; 100% of TIN patterns redacted |
| D3 | 2025-11-20 | **Browser-side speech recognition** rather than server-side ASR | Voice input improves accessibility but raises audio data concerns | (a) Server-side Whisper model, (b) Third-party STT API (Google/Azure), (c) Browser Web Speech API | Browser API processes audio locally; no audio leaves user's device; aligns with privacy-by-design and NDPA | Privacy, Consent (ACM §1.6) | Implemented Web Speech API; no audio data transmitted to backend |
| D4 | 2026-01-10 | **Add mandatory disclaimer** to every chatbot response | Risk of taxpayers treating AI output as legally binding advice | (a) No disclaimer (cleaner UX), (b) One-time disclaimer at session start, (c) Per-response disclaimer | Per-response disclaimer ensures users see it even when sharing screenshots; ACM §1.2 requires avoiding harm; legal precedent requires clear AI disclosure | Avoid Harm, Honesty (ACM §1.2, §1.3) | Disclaimer appended to system prompt; responses include "This is AI-generated guidance, not legal advice" |
| D5 | 2026-02-01 | **Restrict CORS to explicit origins** instead of wildcard | Original implementation used `allow_origins=["*"]` with credentials | (a) Keep wildcard for development ease, (b) Environment-variable-based allowlist | Wildcard CORS with credentials is a security vulnerability (OWASP); explicit origins prevent CSRF; aligns with NIST SSDF PW.6 | Security (ACM §2.9) | CORS hardened to allowlist; credentials disabled; methods restricted to GET/POST/OPTIONS |
| D6 | 2026-02-15 | **Deploy Luganda support with quality warnings** rather than delaying launch | Luganda NLP quality is lower than English; delayed launch excludes Luganda speakers | (a) Delay until Luganda reaches English parity, (b) Deploy without warnings, (c) Deploy with quality indicator | Withholding Luganda excludes ~50% of population; deploying without warnings risks harm; quality indicator balances inclusion with honesty | Fairness, Honesty (ACM §1.4, §1.3) | Luganda responses include "(Beta – Luganda)" label; English fallback when confidence < 0.7 |

---

*This document will be updated as new harms are identified or existing mitigations are revised. All entries are traceable to ISO/IEC 42001:2023 §6.1, NIST AI RMF 1.0, and the ACM/IEEE Codes of Ethics.*

# Week 9 – Software Quality Plan, RTM & Glossary

**Project**: MLOps Pipeline for URA Chat Bot
**Date**: February 2026
**Standard Alignment**: ISO/IEC 25000:2014 (SQuaRE), ISO/IEC 25010:2023 (Quality Model), ISO/IEC 24765:2025

---

## 1. ISO/IEC 25010:2023 Software Quality Plan

### 1.1 Quality Model Mapping

The ISO/IEC 25010:2023 quality model defines 8 quality characteristics. Below is how the URA Chat Bot addresses each:

| # | Quality Characteristic | Sub-characteristics Applied | Project Implementation | Measurement |
|---|----------------------|---------------------------|----------------------|-------------|
| Q1 | **Functional Suitability** | Functional completeness, correctness, appropriateness | Chat endpoint returns relevant answers from FAQ corpus; classification endpoint maps queries to tax categories; source citations provided | Accuracy ≥ 85% on test set; all documented endpoints implemented |
| Q2 | **Performance Efficiency** | Time behaviour, resource utilisation, capacity | Keyword search < 50ms; API response < 3s p95; Docker container < 512MB RAM baseline | k6 load test; Docker stats monitoring |
| Q3 | **Compatibility** | Co-existence, interoperability | REST API with JSON; CORS-configured; OpenAPI spec; Docker OCI-compatible | API contract tests; multi-runtime deployment test |
| Q4 | **Interaction Capability** (formerly Usability) | Appropriateness recognisability, learnability, operability, user error protection, accessibility | Quick prompts for discoverability; speech input; error messages; WCAG 2.1 AA target; mobile-responsive | Lighthouse accessibility ≥ 90; SUS score ≥ 70 |
| Q5 | **Reliability** | Maturity, availability, fault tolerance, recoverability | Health check endpoint; graceful degradation (FAQ fallback if model offline); CI/CD with rollback | 99.5% uptime target; failure injection tests |
| Q6 | **Security** | Confidentiality, integrity, non-repudiation, accountability, authenticity | TLS 1.3; CORS hardened; PII redaction; input validation; structured logging; SBOM | SAST/DAST scan results; Trivy 0 Critical CVEs |
| Q7 | **Maintainability** | Modularity, reusability, analysability, modifiability, testability | Layered architecture (API → Service → KB); separated frontend; linting + type checking; 80%+ test coverage | Ruff 0 errors; mypy strict pass; pytest coverage report |
| Q8 | **Flexibility** (formerly Portability) | Adaptability, installability, replaceability | Docker containerisation; environment-variable configuration; pluggable model backend | Deploy on Docker, Podman, K8s; swap model via env var |

### 1.2 Quality Objectives & Thresholds

| Objective ID | Quality Characteristic | Metric | Target | Measurement Tool | Frequency |
|-------------|----------------------|--------|--------|-----------------|-----------|
| QO-01 | Functional Suitability | Answer accuracy (held-out set) | ≥ 85% | ml/pipelines/evaluate.py | Every training run |
| QO-02 | Functional Suitability | API endpoint coverage | 100% of documented endpoints implemented | pytest + FastAPI TestClient | Every PR |
| QO-03 | Performance Efficiency | p95 response latency | ≤ 3 seconds | k6 load test | Pre-release |
| QO-04 | Performance Efficiency | Container memory usage | ≤ 512 MB (baseline, no model) | Docker stats | Pre-release |
| QO-05 | Interaction Capability | Lighthouse accessibility score | ≥ 90 | Lighthouse CI | Every frontend PR |
| QO-06 | Reliability | Monthly uptime | ≥ 99.5% | Uptime monitoring | Continuous |
| QO-07 | Security | Critical/High CVEs | 0 in production deps | Trivy | Weekly + every PR |
| QO-08 | Security | SAST findings (High+) | 0 | Semgrep | Every PR |
| QO-09 | Maintainability | Lint errors | 0 | Ruff + ESLint | Every PR |
| QO-10 | Maintainability | Type check errors | 0 | mypy + tsc | Every PR |
| QO-11 | Maintainability | Test coverage | ≥ 80% | pytest-cov | Every PR |
| QO-12 | Flexibility | Multi-runtime deployment | Pass on Docker + Podman | Manual test | Pre-release |

### 1.3 Quality Assurance Activities

| Activity | Owner | Frequency | Automation |
|----------|-------|-----------|------------|
| Code review (PR-based) | CODEOWNERS | Every PR | GitHub branch protection |
| Backend testing (pytest, >= 80% cov) | CI Pipeline | Every commit | `ci-ml-pipeline.yml` (`--cov-fail-under=80`) |
| Frontend testing (Vitest + RTL) | CI Pipeline | Every commit | `frontend-deploy.yml` (test-frontend stage) |
| E2E smoke testing (Playwright) | CI Pipeline | Pre-release | `frontend-deploy.yml` + `playwright.config.ts` |
| Static analysis (SAST) | CI Pipeline | Every PR | Semgrep, Ruff (S/T20/PIE/PT/SIM/TCH), ESLint |
| Type checking (mypy strict) | CI Pipeline | Every PR | `disallow_untyped_defs=true` for backend |
| Dependency scanning (SCA) | CI Pipeline | Weekly + PR | Trivy, Dependabot, pip-audit |
| ML model evaluation | CI Pipeline | Every training run | quality_gates.py |
| Container security scan | CI Pipeline | Every build | Trivy + cosign signing + SLSA provenance |
| Accessibility audit (WCAG 2.1 AA) | CI Pipeline | Every PR | axe-core + Lighthouse CI (>= 90) |
| DAST penetration testing | CI Pipeline | Weekly + main push | OWASP ZAP baseline (`.zap-rules.tsv`) |
| Load/performance testing | Manual | Pre-release | k6 (`tests/load/k6-chat-slo.js`) |
| AI red team evaluation | Manual | Pre-release | `scripts/ai_red_team.py` (50 NIST prompts, >= 90% block) |
| Bias & fairness audit | Manual | Pre-release | `scripts/bias_fairness_audit.py` (>= 70% parity) |
| Incident response simulation | Manual | Quarterly | `scripts/incident_response_sim.py` (3 playbooks) |
| Disaster recovery test | Manual | Quarterly | `scripts/dr_test.sh` (Qdrant + SQLite + health) |
| Carbon footprint tracking | Manual | Each training run | `scripts/carbon_tracker.py` (CodeCarbon) |
| Ethics review | Project Lead | Each major release | Checklist + PIA (`docs/capstone/PIA.md`) |
| Mobile testing (Flutter) | CI Pipeline | Every commit | `flutter-ci.yml` (analyze + test + coverage) |

---

## 2. Requirements Traceability Matrix (RTM)

| Req ID | Requirement Description | Source | Priority | Design Artifact | Implementation | Test Case(s) | Test Status |
|--------|------------------------|--------|----------|----------------|----------------|-------------|-------------|
| REQ-01 | System shall answer tax-related questions with source citations | Stakeholder S2, NFR-04 | Critical | Architecture: RAG pipeline (service.py) | `ChatModel.generate()` returns `reply` + `sources` | TC-01: Verify response includes sources for known queries | Pass |
| REQ-02 | System shall classify user queries into tax categories | API_REFERENCE.md | High | Architecture: Classification endpoint | `ChatModel.classify()` with tag scoring | TC-02: Classify "VAT rate" → vat tag with confidence > 0 | Pass |
| REQ-03 | API shall return health status | API_REFERENCE.md | High | main.py `/health` endpoint | `HealthResponse` model | TC-03: GET /health returns 200 with status "healthy" | Pass |
| REQ-04 | Chat messages shall be limited to 2000 characters | SR-05, OWASP LLM01 | High | models.py `ChatRequest.message` max_length=2000 | Pydantic validation | TC-04: POST /v1/chat with 2001-char message → 422 | Pass |
| REQ-05 | CORS shall restrict origins to explicit allowlist | SR-19, OWASP | Critical | main.py CORS middleware | `_allowed_origins` from env var | TC-05: Request from unauthorized origin → blocked | Pass |
| REQ-06 | Training data shall contain no PII | SR-11, NDPA 2019 | Critical | DataIngestion notebook PII redaction | Regex + NER pipeline | TC-06: Scan training corpus for TIN/NIN patterns → 0 matches | Pass |
| REQ-07 | Frontend shall provide speech input via browser STT | NFR-12, Accessibility | Medium | page.tsx Web Speech API integration | `SpeechRecognition` with `onresult` handler | TC-07: Activate mic → transcript appears in input field | Pass (manual) |
| REQ-08 | System shall display loading indicator during API calls | UX, Interaction Capability | Medium | page.tsx `isLoading` state + `LoadingDots` component | `useState(false)` → set true during fetch | TC-08: Send message → dots animate → response appears | Pass (manual) |
| REQ-09 | CI pipeline shall block deployment if accuracy < 85% | QO-01, Quality Gate | Critical | ml/pipelines/quality_gates.py | `check_accuracy()` assertion | TC-09: Mock metrics with accuracy=0.80 → pipeline fails | Pass |
| REQ-10 | Container images shall have 0 Critical CVEs | SR-16, QO-07 | High | ci-ml-pipeline.yml Trivy step | Trivy `--severity CRITICAL` with exit-code 1 | TC-10: Trivy scan on production image → 0 Critical | Pass |
| REQ-11 | All GitHub Actions shall use SHA-pinned references | SR-14, SLSA v1.2 | High | .github/workflows/*.yml | SHA hashes in `uses:` directives | TC-11: Grep workflows for `@v` (mutable tags) → 0 matches | Pass |
| REQ-12 | System shall provide FAQ browsing by tag | API_REFERENCE.md | Medium | main.py `/tags` and `/faq/{tag}` endpoints | `ChatModel.list_tags()`, `ChatModel.get_faq()` | TC-12: GET /tags returns list; GET /faq/vat returns entries | Pass |

---

## 3. ISO/IEC 24765:2025 Glossary Excerpt (Project-Specific Terms)

| # | Term | Definition (per ISO/IEC 24765:2025 + project context) | Related Standard |
|---|------|-------------------------------------------------------|-----------------|
| 1 | **AI System** | A machine-based system that, for explicit or implicit objectives, infers from input how to generate outputs such as predictions, content, recommendations, or decisions that can influence physical or virtual environments (ISO/IEC 22989:2022 §3.1.4). In this project: the URA Chat Bot combining RAG retrieval with a fine-tuned LLM. | ISO/IEC 42001, NIST AI RMF |
| 2 | **AI System Lifecycle** | The set of stages from conception through retirement of an AI system, including data management, model development, deployment, monitoring, and decommissioning (ISO/IEC 42001:2023 §3.3). | ISO/IEC 42001 |
| 3 | **Chatbot** | A conversational AI application that simulates human dialogue to provide information, answer questions, or perform tasks via natural language input (text or speech). | – |
| 4 | **Continuous Compliance** | The practice of integrating regulatory and standards adherence into automated CI/CD pipelines so that compliance is verified continuously rather than at discrete audit points. | NIST SSDF, SLSA |
| 5 | **Data Provenance** | The documented trail of the origin, movement, and transformation of data, including source, collection method, processing steps, and licensing (ISO/IEC 42001:2023 §A.7.4). | ISO/IEC 42001, SLSA |
| 6 | **Fine-Tuning** | The process of adapting a pre-trained machine learning model to a specific domain or task by continuing training on a domain-specific dataset, typically with a lower learning rate. In this project: LoRA/QLoRA adaptation of Gemma-2-9B on URA FAQ data. | – |
| 7 | **Harm Register** | A structured record of identified potential harms from an AI system, scored by likelihood and impact, with mitigation plans and residual risk assessments (NIST AI RMF MAP-5, MANAGE-2). | NIST AI RMF, ISO/IEC 42001 |
| 8 | **LoRA (Low-Rank Adaptation)** | A parameter-efficient fine-tuning technique that freezes pre-trained model weights and injects trainable low-rank decomposition matrices into transformer layers, reducing compute and memory requirements. | – |
| 9 | **MLOps** | A set of practices that combines Machine Learning, DevOps, and Data Engineering to reliably and efficiently deploy and maintain ML systems in production (ISO/IEC 5338:2023 §3.10). | ISO/IEC 5338 |
| 10 | **PII (Personally Identifiable Information)** | Any information relating to an identified or identifiable natural person (ISO/IEC 29100:2024 §2.9). In the Ugandan context: TIN (Tax Identification Number), NIN (National Identification Number), phone numbers, names. | NDPA 2019, GDPR |
| 11 | **Quality Gate** | An automated checkpoint in a CI/CD pipeline that evaluates predefined quality criteria (accuracy, security, coverage) and blocks progression if thresholds are not met. | ISO/IEC 25010 |
| 12 | **RAG (Retrieval-Augmented Generation)** | An AI architecture pattern that combines information retrieval from a knowledge base with generative language model output, grounding responses in factual source documents. | – |
| 13 | **Responsible AI by Design** | An approach to AI development that proactively integrates ethical principles, fairness, transparency, privacy, and accountability into every phase of the AI system lifecycle, rather than treating them as afterthoughts. | ISO/IEC 42001, NIST AI RMF |
| 14 | **SBOM (Software Bill of Materials)** | A formal, machine-readable inventory of software components and dependencies, including versions, licenses, and suppliers (NTIA/CISA minimum elements). | CycloneDX, SPDX |
| 15 | **SLSA (Supply-chain Levels for Software Artifacts)** | A security framework that codifies standards for software supply chain integrity, from build provenance to source verification (SLSA v1.2). | SLSA |
| 16 | **STRIDE** | A threat modelling methodology that categorises threats into Spoofing, Tampering, Repudiation, Information Disclosure, Denial of Service, and Elevation of Privilege. | Microsoft SDL |
| 17 | **TIN (Tax Identification Number)** | A unique numeric identifier assigned by URA to every registered taxpayer in Uganda. Classified as PII under NDPA 2019. | NDPA 2019 |
| 18 | **Verification** | Confirmation through objective evidence that specified requirements have been fulfilled – "Are we building the product right?" (ISO/IEC 24765:2025 §3.4507). | ISO/IEC 15288, 12207 |
| 19 | **Validation** | Confirmation through objective evidence that the requirements for a specific intended use have been fulfilled – "Are we building the right product?" (ISO/IEC 24765:2025 §3.4456). | ISO/IEC 15288, 12207 |
| 20 | **Vector Store** | A database optimised for storing and querying high-dimensional vector embeddings, used in RAG pipelines for semantic search. In this project: Qdrant (planned). | – |

---

*This document is aligned with ISO/IEC 25000:2014 (SQuaRE series), ISO/IEC 25010:2023 (Product Quality Model), and ISO/IEC 24765:2025 (Systems and Software Engineering Vocabulary).*

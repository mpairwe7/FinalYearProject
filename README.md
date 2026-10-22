# FinalYearProject

[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/ura-chatbot/badge)](https://www.bestpractices.dev/)
[![SLSA Level 2](https://slsa.dev/images/gh-badge-level2.svg)](https://slsa.dev/)
[![Security: Trivy](https://img.shields.io/badge/Security-Trivy_Scanning-blue.svg)](.github/workflows/security-trivy.yml)
[![DevSecOps: SAST/DAST](https://img.shields.io/badge/DevSecOps-SAST%2FDAST-green.svg)](.github/workflows/devsecops-sast-dast.yml)

This repository is a customer-service conversation AI for Uganda Revenue Authority taxpayers. The pipeline uses GitHub Actions for CI/testing/deployment and Docker for containerised deployment. Backend uses Python/FastAPI with `uv` for dependency management; frontend (Next.js) uses Bun. Model training happens outside this repository — `ml/` retains only the production quality gates and corpus tooling `App/backend` and CI depend on (see [ml/README.md](ml/README.md)).

## Documentation

| Document | Description |
|----------|-------------|
| [Documentation Index](docs/README.md) | Getting started and project overview |
| [API Reference](docs/API_REFERENCE.md) | REST API endpoints (sync + SSE streaming) |
| [RAG Architecture](docs/RAG_ARCHITECTURE.md) | 12-stage production RAG + Agentic + Speech pipeline design |
| [Agent Architecture](docs/AGENT_ARCHITECTURE.md) | Supervisor routing, 72 MCP tools across 18 namespaces, 21 guided workflows, and 85-flag registry |
| [Architecture Decision Records (MADR 4.0)](docs/adr/README.md) | Canonical ADR catalog (`ADR-0001`–`ADR-0013`) governing monorepo boundaries, 5-locale stack, MCP security, sovereign reasoning, live web research (`ADR-0008`), 10-MDA statutory corpus & queue-reduction tools (`ADR-0009`), Journey Copilot (`ADR-0010`, `ADR-0013`), CI supply chain (`ADR-0011`), and storage parity (`ADR-0012`) |
| [Model Card](docs/MODEL_CARD.md) | EU AI Act Art. 53 & ISO/IEC 42001 model card (`Sunbird/Sunflower-Qwen3.5-9B` + `Qwen/Qwen3.8-27B-FP8`, 186 statutory/MDA PDFs → 7,251 Qdrant hybrid vectors) |
| [Deployment Guide](docs/DEPLOYMENT.md) | Production deployment, TLS, scaling, SLOs |
| [Monitoring & Observability](docs/MONITORING.md) | OpenTelemetry, Prometheus, alerting |
| [Data Schema & Evaluation](docs/data-schema-and-eval.md) | Database models and evaluation criteria |
| [Security Policy](SECURITY.md) | Secret scanning, OWASP LLM Top 10, threat model |
| [Agent repo map](AGENTS.md) | 2026 agent-monorepo folder map (`agents/`, `evals/`, `infra/`) |

## Pipeline Overview
- **Code**: Source, data configs, and frontend live in this repo on GitHub.
- **CI (GitHub Actions)**: Lint/test backend and UI, validate dataset configs, and build artifacts.
- **Model Training (Kaggle)**: GitHub Actions launches Kaggle notebook jobs via API for training; artifacts (checkpoints/metrics) are pushed back to GitHub Releases or an object store.
- **CD (Docker)**: Successful main-branch builds trigger Docker image builds for both frontend and backend, pushed to Docker Hub.
- **API Container**: FastAPI backend packaged via Docker; images pushed to Docker Hub for deployment.
- **Frontend Container**: Next.js frontend packaged via Docker; images pushed to Docker Hub for deployment.

## Project Structure

```
FinalYearProject/
├── AGENTS.md              # Agent-monorepo router (imports still live under App/)
├── CLAUDE.md              # Points coding agents at AGENTS.md
├── agents/                # graphs / tools / mcp / prompts pointers
├── apps/api               # FastAPI entry → App/backend
├── apps/web               # Next.js entry → App/frontend
├── packages/              # Why this is not a split package monorepo
├── prompts/               # Pointer to runtime prompts
├── skills/                # Pointer to Cursor/Claude skills
├── configs/               # prototype.env
├── scripts/prototype.sh   # load demo defaults + print seed command
├── evals/                 # Eval runners + preference export
├── infra/                 # K8s stubs (not applied)
├── .agents/               # layout, testing, prototype notes
├── .github/workflows/     # CI/CD, security, frontend, mobile, and MLOps workflows
├── App/                   # Application code
│   ├── backend/          # FastAPI backend
│   │   └── app/
│   │       ├── main.py        # FastAPI app + endpoints (sync + SSE streaming)
│   │       ├── models.py      # Pydantic v2 request/response models
│   │       ├── service.py     # ChatModel (12-stage RAG + agentic routing + speech)
│   │       ├── llm.py         # Sunbird/Sunflower-Qwen3.5-9B (262K native context) / Qwen3-8B generation, LoRA routing
│   │       ├── query.py       # Query rewriting (abbreviations, spelling, coreference)
│   │       ├── cache.py       # Semantic cache (cosine similarity)
│   │       ├── corrective_rag.py # Corrective re-retrieval + clarification
│   │       ├── guardrails.py  # OWASP LLM Top 10 input/output guards
│   │       ├── retriever.py   # Qdrant hybrid retriever (dense+BM25+RRF+circuit breaker)
│   │       ├── indexer.py     # Document indexing pipeline (PDF+CSV→Qdrant, 7,251 hybrid vectors)
│   │       ├── pdf_corpus.py  # Statutory & 10-MDA PDF harvester (186 PDFs → 5,639 chunks, ADR-0009)
│   │       ├── web_research.py # 8-trigger live web research planner & Tier-1/2/3 source authority (ADR-0008)
│   │       ├── ephemeral_pdf_sandbox.py # Ephemeral tmpfs PDF/XLSX sandbox + GPU micro-reranking (ADR-0008)
│   │       ├── corpus_sync.py # Self-updating Qdrant comparator, hot-swap & web_sync_ledger (ADR-0008)
│   │       ├── scenario_planner.py # Deterministic multi-tool planner & Verified Statutory Facts block
│   │       ├── calculator_router.py # Instant single-calculator router & multi-turn amount carryover
│   │       ├── tools/         # 72 MCP tools across 18 namespaces (incl. mda_interdependence.py)
│   │       ├── workflows/     # 21 YAML-driven guided journeys (Prerequisite DAG, A2UI, Step-Up Auth)
│   │       ├── tracing.py     # OpenTelemetry GenAI tracing (per-stage spans)
│   │       ├── analytics.py   # Prometheus-compatible metrics middleware
│   │       └── database.py    # SQLite WAL analytics/feedback/session/conversation store
│   └── frontend/         # Next.js 16 + React 19 + Zustand 5 frontend
│       ├── lib/
│       │   ├── core/      # Config, networking (Dio), theme, storage
│       │   └── features/  # Chat, FAQ, Settings screens + providers
│       ├── android/       # Android native (speech, network config)
│       └── ios/           # iOS native (speech, microphone permissions)
├── Data/                  # Training & evaluation data
│   ├── dataset/          # 41 CSV FAQ files
│   ├── pdfs/             # PDF documents
│   └── eval/             # RAG evaluation sets
│       ├── rag_eval.jsonl      # English eval (30 samples, incl. reg-* regression ids)
│       └── rag_eval_lg.jsonl   # Luganda eval (12 samples)
├── governance/            # AI governance framework
│   ├── ai_risk_manifest.yaml   # NIST AI RMF + ISO 42001 + OWASP + EU AI Act
│   └── compliance_check.py     # CI gate for governance artifacts
├── ml/                    # Production quality gates + corpus tooling
│   ├── configs/
│   │   └── training_config.yaml  # RAG + production quality gate thresholds
│   ├── pipelines/
│   │   ├── evaluate_rag.py       # RAG evaluation (8 metrics)
│   │   ├── corpus_coverage.py    # Issue #303 taxpayer-question coverage gate
│   │   ├── export_feedback.py    # Feedback → retriever tuning sets
│   │   └── quality_gates.py      # Production quality gates
│   └── scripts/
│       ├── lang_id.py            # EN/LG/SW detection (baked into Crane Cloud image)
│       ├── eval_retrieval.py     # Retrieval ranking gate
│       ├── asr/, mt/, tts/       # infer_*.py: speech_service.py's local backend tier
│       └── data_aug/             # Corpus chunking used by App/backend's export tooling
├── Model/                 # Trained model artifacts
├── Results/               # Metrics and reports
├── tests/                 # Test suite
├── docker-compose.yml     # Service orchestration (API + frontend + Qdrant + Redis)
└── docs/                  # Documentation
```

## End-to-End CI/CD Flow

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  Commit  │───▶│  Lint &  │───▶│ RAG Eval │───▶│ Prod     │───▶│  Deploy  │
│   Push   │    │   Test   │    │  Gates   │    │ Gates    │    │   Prod   │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
       │        ┌────────────┐                                  ┌──────────┐
       └───────▶│ Governance │ (parallel)                       │ Feedback │
                │ Compliance │                                  │   Loop   │
                └────────────┘                                  └──────────┘
```

1) **Commit/PR**: CI runs lint/tests + governance compliance check (parallel). Governance verifies NIST AI RMF, ISO/IEC 42001, OWASP LLM Top 10, and EU AI Act artifacts. Lint/test also runs the retrieval ranking gate, corpus coverage gate (issue #303), and an offline RAG smoke eval against `App/backend`.
2) **Merge to main**: data validation → **RAG evaluation** (faithfulness, groundedness, citation accuracy, safety probes, abstention precision) → production quality gates → Docker build (with Trivy scan) → deploy.
3) **Quality gates**: Deployment is gated on `evaluate-rag` and `production-gates` job success.
4) **Feedback loop**: User thumbs-down feedback → `ml/pipelines/export_feedback.py` → retriever negative judgments + regression test candidates → retriever/reranker tuning.

## URA Chatbot — Advanced 12-Stage RAG & Agentic Pipeline (October 2026)

The API backend (`App/backend/app/`) implements a production-grade 12-stage Retrieval-Augmented Generation and Agentic CX pipeline with local LLM inference:

```
User Query
  → Stage 0..0c: Conversation History, Query Rewriting, Retrieval Plan (+EQ Relief), Answer Language
  → Stage 1..1d: InputGuard (OWASP LLM01) → Guided Workflows (with Tangent Resume) → Semantic Cache → Supervisor / Multi-Agent DAG
  → Stage 2..3c2: Hybrid Retrieval (BAAI/bge-m3 + BM25 DBSF/RRF → mxbai-rerank-base-v2) → Corrective RAG → Language Boosting & FAQ Blending
  → Stage 4..5: Abstention Check → LLM Synthesis (Sunbird/Sunflower-Qwen3.5-9B 262K native context via vLLM or Tool-Calling Loop)
  → Stage 6..9: OutputGuard (PII redaction, XSS strip) → Faithfulness & Numeric Contradiction Check → Escalation / HandoffEnvelope v2.1 → AuditLedger v3
```

| Component | Implementation | Details |
|-----------|---------------|---------|
| **Chunking** | `RecursiveCharacterTextSplitter` | QA: 600 tokens, PDF: 1000 tokens; section/page metadata |
| **Embeddings** | `BAAI/bge-m3` | 1024-dim multilingual dense embeddings |
| **Vector Store** | Qdrant (`v1.19.0` local persistent) | Versioned collections, alias-based zero-downtime indexing |
| **Retrieval** | Hybrid dense + BM25 sparse | DBSF / RRF fusion + circuit breaker with exponential backoff |
| **Reranking** | `mixedbread-ai/mxbai-rerank-base-v2` | Cross-encoder reranking with clean single-sigmoid probabilities |
| **Query Rewriting** | `query.py` | Abbreviation expansion (15+ URA terms), vernacular-safe spell correction, coreference resolution |
| **Semantic Cache** | `cache.py` | Cosine similarity matching, configurable TTL/threshold/max-size |
| **Corrective RAG** | `corrective_rag.py` | Re-retrieve with expanded query when initial quality is low |
| **Generation** | `Sunbird/Sunflower-Qwen3.5-9B` + `Qwen/Qwen3.8-27B-FP8` | vLLM HTTP (T1 9B FP8 with native 256K / 262,144 context window across 67 African languages; T2/T3 27.8B FP8 Sovereign Reasoning Tier for multi-hop statutory & dispute queries per `ADR-0007`); `Qwen/Qwen3-8B` + local Transformers/BnB 4-bit is the fallback and what the lg/sw/nyn/ach LoRA adapters target |
| **LoRA Routing** | Qwen3 adapters for `lg`, `sw`, `nyn`, `ach` | Per-locale PEFT adapters loaded from `fine-tuning/adapters/` and selected with `set_adapter()` |
| **Deterministic Tax, Cross-MDA & Relief Tools** | `tools/` (`72` MCP `2026-07-28` tools across `18` namespaces) + `scenario_planner.py` | `28` `tax_calculator` tools including 6 Cross-MDA & Queue-Reduction tools in `tools/mda_interdependence.py` (`resolve_mda_ntr_payment`, `assess_life_event_tax_onboarding`, `evaluate_b2g_and_uesw_clearance`, `build_new_taxpayer_compliance_shield`, `simulate_tax_relief_and_instalments`, `resolve_efris_and_account_recovery` — `ADR-0009`) |
| **Statutory & 10-MDA Corpus + Self-Updating Qdrant** | `pdf_corpus.py` + `web_research.py` + `ephemeral_pdf_sandbox.py` + `corpus_sync.py` | `186` PDF sources (`175` unique, `5,639` structure-aware statutory chunks across URA, Parliament of Uganda, ULII, and all 10 high-dependency Ugandan MDAs) → `7,251` Qdrant hybrid vectors (`ADR-0008`, `ADR-0009`) |
| **Guided Taxpayer & Cross-Agency Journeys** | `workflows/` (`21` YAML state machines) + `journey_copilot.py` | `21` guided workflows (including `mda_ntr_resolution.yaml` and `new_taxpayer_and_relief.yaml`) with Prerequisite DAGs, A2UI interactive forms, pushdown `goal_stack`, and Step-Up Auth session migration (`ADR-0010`) |
| **Cognitive Memory** | `memory/` (`4-tier` + `ResolutionLedger`) | Working, Episodic, Semantic, and Procedural memory + multi-case `ResolutionLedger` and `HandoffEnvelope` v2.1 |
| **Speech & Voice** | `Sunbird/SunflowerASR-51-african-languages` + `Orpheus-3B-TTS` | Hybrid S2S + Regulated Cascaded Router, `spoken_figure_guard`, multimodal affect prosody, and 8 kHz G.711 μ-law 2G/3G wire transcoding |
| **Safety & Governance** | OWASP LLM Top 10 + `governance_v3.py` | Injection detection, PII redaction, `#NMBRA#` sentinel figure protection, `ActionAuditRecord` v3, and `InteractionQualityScorecard` |
| **Escalation** | `guardrails.py` + `officer_chat.py` | Auto-flag for human review, bilingual Officer Co-Pilot drafts, `Officer -> AI` workflow delegation, and DPO preference capture |
| **Observability** | OpenTelemetry + Prometheus | Per-stage GenAI latency spans, token usage metrics, bounded label series |
| **Evaluation** | Hit@K, MRR, NDCG, faithfulness | 8-metric quality gates + 30,000-scenario trilingual (TTT/STT/TTS) stress & fuzzy verification |

- Data model, ingestion flow, and evaluation rubric are documented in [docs/data-schema-and-eval.md](docs/data-schema-and-eval.md).

## Current App Runtime Baseline (May 2026)

The App deployment is designed for public anonymous chat while keeping private/account surfaces fail-closed:

| Area | Current baseline |
|------|------------------|
| Public chat | `/v1/chat`, `/v1/chat/stream`, frontend `/api/v1/chat`, speech health, TTS, translation, and consented voice processing work without login |
| Private routes | `/v1/me/*`, admin tickets, metrics, analytics dashboards, evaluation exports, offline bundle APIs, and URA account/action tools require bearer auth and/or staff roles |
| LLM memory footprint | `LLM_LOAD_IN_4BIT=true` enables BitsAndBytes NF4 4-bit loading for Qwen3-8B, keeping GPU headroom for the agentic stack |
| Language adapters | `LORA_ADAPTER_LG`, `LORA_ADAPTER_SW`, `LORA_ADAPTER_NYN`, and `LORA_ADAPTER_ACH` mount from `fine-tuning/adapters` into `/app/adapters` |
| Speech | Whisper LoRA adapters for Luganda, Swahili, and Runyankole are mounted from the same adapter volume; `WHISPER_DEVICE=cpu` keeps ASR off GPU 0 |
| Mobile chat UX | The Next.js PWA keeps the composer docked above the safe area/keyboard, auto-scrolls while the user is near the latest turn, renders markdown/citations, and exposes a scroll-to-latest affordance when the user scrolls away |
| Live smoke | `scripts/live_smoke.sh` and `scripts/deploy_preflight.sh` verify health, anonymous TIN workflow responses, speech health, protected admin denial, and ngrok proxy routing |

## GitHub Actions Workflows

**Current PR behavior (May 2026):**

| Area | PR behavior |
|------|-------------|
| App backend | Ruff syntax/undefined-name gate, pytest with isolated Qdrant/speech-disabled test env, governance check, data-aug smoke job |
| Frontend | ESLint, TypeScript, Vitest unit/component tests, accessibility/Lighthouse, and production build |
| Security | Secret scanning, CodeQL, Semgrep, Bandit, pip-audit, Checkov, Trivy filesystem/IaC/license scans, path-scoped Trivy image scans, and threat-registry validation |
| SARIF uploads | Semgrep uploads SARIF on PRs; Trivy and Checkov upload PR artifacts and reserve GitHub Security SARIF upload for non-PR events to avoid external code-scanning app check failures |
| Container publishing | API/frontend publish on protected branch pushes; Trivy image validation is path-scoped on PRs and full on protected branch, manual, and scheduled runs |
| Intentional PR skips | OWASP ZAP baseline and OSSF Scorecard skip on PR; ZAP runs against the CI API target on port `8087`, and Scorecard runs only with default-branch repository context |

### 1. `ci-ml-pipeline.yml` - Main CI Pipeline
**Triggers**: Push to `main`/`develop`/`feat/*`, PRs, manual dispatch

| Stage | Description |
|-------|-------------|
| Lint & Test | Ruff, Pytest, retrieval ranking gate, corpus coverage gate (issue #303), offline RAG smoke eval |
| Governance Check | NIST/ISO/OWASP/EU AI Act compliance (parallel) |
| Data Validation | Schema validation, quality checks |
| **RAG Evaluation** | Faithfulness, groundedness, citation accuracy, safety probes, Luganda eval |
| Production Gates | Combined pass/fail quality gate scored on RAG metrics |
| Adapter Evaluation | Multilingual LoRA adapter eval scripts under `fine-tuning/` |
| Build Docker | Multi-stage build, Trivy scan, push to DockerHub |
| Deploy Backend | Production API deployment |

The PR Python coverage gate is currently set to a ratcheting baseline of 35% while focused agentic/backend coverage is expanded. Coverage XML is still uploaded for review on every run.

### 2. `frontend-deploy.yml` - Frontend CI/CD
**Triggers**: Push to `main`/`develop` (frontend changes), PRs

| Stage | Description |
|-------|-------------|
| Lint | ESLint + TypeScript checking |
| Unit & Component Tests | Vitest + React Testing Library; coverage artifact is uploaded while the blocking gate focuses on correctness |
| Accessibility Audit | axe-core WCAG 2.2 AA across taxpayer and staff routes + Lighthouse CI (accessibility >= 90) |
| Build | Next.js production build |
| Build Docker | Build & push frontend Docker image + Trivy scan |
| Deploy Production | Production Docker deployment |

### 3. `secret-scanning.yml` - 4-Layer Secret Scanning
**Triggers**: Push/PR to main/develop, weekly schedule

| Scanner | Coverage |
|---------|----------|
| TruffleHog v3 | 800+ verified credential detectors |
| Gitleaks v8 | Regex + entropy + custom Uganda PII rules |
| GitGuardian ggshield | 400+ ML-based secret types |
| detect-secrets | Baseline-aware entropy scanning |

### 4. `security-trivy.yml` - Trivy Security Scanning
**Triggers**: Push/PR to main/develop, weekly schedule (Monday 03:00 UTC)

| Scan | Coverage |
|------|----------|
| Filesystem | Python/Node.js/Dart dependency vulnerabilities |
| IaC | Dockerfile + docker-compose misconfiguration |
| License | Block copyleft (AGPL, GPL, SSPL) in production |
| Container Images | API, Frontend (2 images) |
| PR Image Scope | API and frontend image scans run only when relevant image/app paths change on PRs; push, schedule, and manual runs scan both images |
| SBOM | CycloneDX generation for each image |
| Security Gate | Aggregated pass/fail across all scans |

### 5. `codeql-analysis.yml` - CodeQL SAST
**Triggers**: Push/PR to main/develop, weekly schedule

| Language | Query Suite |
|----------|-------------|
| Python | security-extended (SQLi, command injection, hardcoded creds) |
| JavaScript/TypeScript | security-extended (XSS, prototype pollution) |

### 6. `devsecops-sast-dast.yml` - DevSecOps Pipeline
**Triggers**: Push/PR to main/develop, weekly schedule

| Tool | Scope |
|------|-------|
| Semgrep | SAST for Python + TypeScript (custom + OWASP rules) |
| Bandit | Python AST security analysis |
| pip-audit | Python dependency audit (OSV/PyPI) |
| Checkov | IaC compliance (CIS, NIST benchmarks) |
| OWASP ZAP | DAST baseline scan against the CI API target on port `8087` |
| OSSF Scorecard | Supply chain security scoring with default-branch guard |

### 7. `container-sign-provenance.yml` - Container Signing & SLSA Provenance
**Triggers**: Called by other workflows after Docker push, manual dispatch

| Step | Description |
|------|-------------|
| Cosign Sign | Sigstore keyless signing (Fulcio + Rekor) |
| Verify | Cosign signature verification |
| SLSA Attestation | GitHub Attestations API (Build Level 2) |

## Required Secrets

| Secret | Purpose |
|--------|---------|
| `HF_TOKEN` | Hugging Face API token (embedder cache auth in RAG eval) |
| `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` | Docker Hub push (backend + frontend) |
| `DEPLOY_KEY`, `API_HOST` | Production server deployment |
| `INDEX_API_KEY` | Bearer token for `/v1/index` re-indexing endpoint (OWASP LLM10) |
| `GITGUARDIAN_API_KEY` | GitGuardian secret scanning (optional, free for OSS) |

## Security Scanning

The project implements **defense-in-depth** security across 7 CI/CD workflows and 11 pre-commit hooks:

| Layer | Tools | Coverage |
|-------|-------|----------|
| **Pre-commit** | TruffleHog, Gitleaks, detect-secrets, Semgrep, Bandit + 6 hygiene checks | Secrets, SAST, code quality |
| **Secret Scanning** | TruffleHog, Gitleaks, ggshield, detect-secrets (CI) | 4-scanner defense-in-depth |
| **SAST** | CodeQL, Semgrep, Bandit | Python + JavaScript/TypeScript |
| **SCA/SBOM** | Trivy (vuln + license + SBOM), pip-audit | Dependency vulnerabilities, CycloneDX |
| **IaC** | Trivy misconfig, Checkov (CIS/NIST) | Dockerfiles, Compose, workflows |
| **DAST** | OWASP ZAP baseline | API endpoint scanning |
| **Supply Chain** | SHA-pinned Actions, OSSF Scorecard, Dependabot, cosign | SLSA Level 2+ compliance |
| **Container** | Trivy image scan (2 images), non-root, cap_drop, read_only, cosign signed | Container hardening |
| **AI Red Teaming** | 50 adversarial prompts (NIST AI 600-1 taxonomy) | Prompt injection, jailbreak, PII extraction |
| **Accessibility** | axe-core WCAG 2.2 AA, Lighthouse CI (>= 90) | Frontend a11y audit in CI |
| **Load Testing** | k6 (p95 < 3s, error rate < 1%) | SLO validation |

All GitHub Actions are **SHA-pinned** to commit hashes (not mutable tags). Container images are signed with Sigstore cosign (keyless OIDC) and attested with SLSA v1.2 provenance. See [SECURITY.md](SECURITY.md) for full details.

## RAG Quality Gates

Deployment is blocked unless both classifier **and** RAG evaluation pass. RAG metrics are evaluated by `ml/pipelines/evaluate_rag.py` and configured in `ml/configs/training_config.yaml`:

| Metric | Minimum Threshold | Description |
|--------|-------------------|-------------|
| Faithfulness | 0.6 | Answer grounded in retrieved passages |
| Answer Relevancy | 0.7 | Response addresses the question |
| Context Precision | 0.5 | Retrieved passages are relevant |
| Context Recall | 0.5 | All needed information was retrieved |
| Groundedness | 0.4 | Claims supported by source material |
| Citation Accuracy | 0.4 | Citations point to correct passages |
| Safety Probe Pass Rate | 1.0 | All adversarial probes blocked |
| Abstention Precision | 0.5 | Refusals are calibrated correctly |

## Governance & Compliance

The project implements a comprehensive AI governance framework verified in CI:

- **NIST AI RMF** — risk identification, measurement, and management
- **ISO/IEC 42001:2023** — AI management system controls
- **OWASP LLM Top 10 (2025)** — all 10 entries (LLM01–LLM10) addressed
- **EU AI Act** — transparency and human oversight provisions

Run locally: `python governance/compliance_check.py`

## ML Quality Gates & Corpus Tooling

Model training happens outside this repository. `ml/` retains only the
production quality gates and corpus tooling `App/backend` and CI depend on:
the retrieval ranking gate, the corpus coverage gate (issue #303), RAG
evaluation, the combined production quality gate, and the corpus
chunking/cleaning package `App/backend`'s PDF/crawl export tooling imports.

**Web API inference** uses Qwen3-8B with optional 4-bit local loading and
locale-specific LoRA adapters (see `App/backend/app/llm.py`).

See [ml/README.md](ml/README.md) for what's there and why.

## Local Development
- Install Python deps with `uv pip install -r requirements.txt` (or `uv sync` if using a lockfile) and run lint/tests via `uv run ruff/pytest/mypy` to mirror CI.
- Frontend (App/frontend/): install with `bun install`; run `bun run dev` for local preview or `bun run lint/test/build` matching CI.
- API: build and run locally with `docker compose up --build` (expects `app.main:app`). Qdrant runs as a first-class service via docker-compose with healthcheck.
- LLM: Qwen3-8B downloads automatically on first request unless the Hugging Face cache is mounted offline. Set `LLM_LOAD_IN_4BIT=true` for NF4 4-bit loading, `LLM_DEVICE=cpu` for CPU-only inference, or `LLM_DEVICE=auto` for GPU auto-detection. Disable with `LLM_ENABLED=false` to fall back to FAQ lookup.

## Container Baseline
- API image (`Dockerfile`) uses multi-stage build, non-root runtime user, exec-style entrypoint, and Python-based healthcheck (no runtime `curl` dependency).
- Uses BuildKit cache mounts for uv dependency layers (`# syntax=docker/dockerfile:1.7` + `--mount=type=cache,target=/root/.cache/uv`).
- Compose runtime hardening is enabled for production-like services (`read_only` rootfs for API, `cap_drop: [ALL]`, `no-new-privileges:true`, `tmpfs` mounts, `init: true`).

## Quick Commands

```bash
# Trigger the CI pipeline
gh workflow run ci-ml-pipeline.yml

# Deploy frontend only
gh workflow run frontend-deploy.yml

# View workflow runs
gh run list --workflow=ci-ml-pipeline.yml
```

## Operational Scripts

| Script | Description |
|--------|-------------|
| `scripts/validate_env.py` | Pre-deployment environment validation (production safety checks) |
| `scripts/ai_red_team.py` | AI red team evaluation (50 NIST AI 600-1 adversarial prompts) |
| `scripts/bias_fairness_audit.py` | Bias & fairness audit (language + taxpayer type parity >= 70%) |
| `scripts/incident_response_sim.py` | Incident response simulation (3 AI-specific playbooks) |
| `scripts/carbon_tracker.py` | Carbon footprint tracking (CodeCarbon wrapper) |
| `scripts/dr_test.sh` | Disaster recovery test (Qdrant snapshot + SQLite backup + health) |
| `tests/load/k6-chat-slo.js` | k6 load test (steady-state + spike, SLO thresholds enforced) |

## Monitoring Stack

Start the full observability stack:

```bash
docker compose --profile monitoring up -d
```

| Service | URL | Purpose |
|---------|-----|---------|
| Prometheus | http://localhost:9090 | Metrics scraping + alerting (5 SLO rules) |
| Grafana | http://localhost:3001 | Pre-built dashboards (8 panels) |
| Jaeger | http://localhost:16686 | Distributed tracing (OTel OTLP) |

Dashboards and datasources are auto-provisioned via `monitoring/grafana/provisioning/`.

## Next Steps
- Set repository secrets in GitHub settings before running workflows
- Configure Hugging Face repository at `mpairweLandwind/ura-chatbot`
- Review [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for production deployment guide
- Review [docs/MONITORING.md](docs/MONITORING.md) for observability setup
- Review [docs/MODEL_CARD.md](docs/MODEL_CARD.md) for EU AI Act Article 53 model card

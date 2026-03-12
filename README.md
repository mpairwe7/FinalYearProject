# FinalYearProject

This repository describes a CI/CD pipeline for developing and training a customer-service conversation AI. The pipeline uses GitHub Actions for automation, Kaggle for model training, and Docker for containerised deployment. Backend uses Python/FastAPI with `uv` for dependency management; frontend (Next.js) uses Bun.

## Documentation

| Document | Description |
|----------|-------------|
| [Documentation Index](docs/README.md) | Getting started and project overview |
| [MLOps Workflows](docs/mlops-workflows.md) | Comprehensive CI/CD pipeline documentation |
| [API Reference](docs/API_REFERENCE.md) | REST API endpoints (sync + SSE streaming) |
| [RAG Architecture](docs/RAG_ARCHITECTURE.md) | 6-phase advanced RAG pipeline design |
| [Deployment Guide](docs/DEPLOYMENT.md) | Production deployment, TLS, scaling, SLOs |
| [Monitoring & Observability](docs/MONITORING.md) | OpenTelemetry, Prometheus, alerting |
| [Mobile Setup](docs/MOBILE_SETUP.md) | Flutter Android/iOS build, on-device LLM |
| [Data Schema & Evaluation](docs/data-schema-and-eval.md) | Database models and evaluation criteria |
| [Security Policy](SECURITY.md) | Secret scanning, OWASP LLM Top 10, threat model |

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
├── .github/workflows/     # CI/CD pipeline (3 workflows)
├── App/                   # Application code
│   ├── app.py            # Gradio HF Spaces app
│   ├── backend/          # FastAPI backend
│   │   └── app/
│   │       ├── main.py        # FastAPI app + endpoints (sync + SSE streaming)
│   │       ├── models.py      # Pydantic v2 request/response models
│   │       ├── service.py     # ChatModel (6-phase RAG + classification)
│   │       ├── llm.py         # Qwen2.5-3B-Instruct local LLM generation
│   │       ├── query.py       # Query rewriting (abbreviations, spelling, coreference)
│   │       ├── cache.py       # Semantic cache (cosine similarity)
│   │       ├── corrective_rag.py # Corrective re-retrieval + clarification
│   │       ├── guardrails.py  # OWASP LLM Top 10 input/output guards
│   │       ├── retriever.py   # Qdrant hybrid retriever (dense+BM25+RRF+circuit breaker)
│   │       ├── indexer.py     # Document indexing pipeline (PDF+CSV→Qdrant)
│   │       ├── tracing.py     # OpenTelemetry GenAI tracing (per-stage spans)
│   │       ├── analytics.py   # Prometheus-compatible metrics middleware
│   │       └── database.py    # SQLite WAL analytics/feedback/session/conversation store
│   └── frontend/         # Next.js 15 + React 19 + Zustand 5 frontend
├── MobileApp/             # Flutter mobile application
│   └── ura_chatbot/       # Flutter 3.41 + Riverpod + Material 3
│       ├── lib/
│       │   ├── core/      # Config, networking (Dio), theme, storage
│       │   └── features/  # Chat, FAQ, Settings screens + providers
│       ├── android/       # Android native (speech, network config)
│       └── ios/           # iOS native (speech, microphone permissions)
├── Data/                  # Training & evaluation data
│   ├── dataset/          # 41 CSV FAQ files
│   ├── pdfs/             # PDF documents
│   └── eval/             # RAG evaluation sets
│       ├── rag_eval.jsonl      # English eval (21 samples)
│       └── rag_eval_lg.jsonl   # Luganda eval (12 samples)
├── governance/            # AI governance framework
│   ├── ai_risk_manifest.yaml   # NIST AI RMF + ISO 42001 + OWASP + EU AI Act
│   └── compliance_check.py     # CI gate for governance artifacts
├── ml/                    # ML pipeline scripts
│   ├── configs/
│   │   └── training_config.yaml  # Training + quality gates config
│   └── pipelines/
│       ├── evaluate_rag.py       # RAG evaluation (8 metrics)
│       ├── export_feedback.py    # Feedback → retriever tuning sets
│       └── quality_gates.py      # Classifier quality gates
├── Model/                 # Trained model artifacts
├── Results/               # Metrics and reports
├── Notebooks/             # Jupyter notebooks
│   ├── ura-training.ipynb                 # Classification + RAG pipeline
│   ├── DataIngestion_Augmentation.ipynb   # Data ingestion & augmentation pipeline
│   └── fine_tune_gemma.ipynb              # Gemma/LLM fine-tuning pipeline
├── tests/                 # Test suite
├── docker-compose.yml     # Service orchestration (API + Qdrant)
└── docs/                  # Documentation
```

## End-to-End CI/CD Flow

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  Commit  │───▶│  Lint &  │───▶│  Train   │───▶│ Evaluate │───▶│ RAG Eval │───▶│  Deploy  │
│   Push   │    │   Test   │    │  Model   │    │ Classify │    │  Gates   │    │   Prod   │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
       │        ┌────────────┐                                                  ┌──────────┐
       └───────▶│ Governance │ (parallel)                                       │ Feedback │
                │ Compliance │                                                  │   Loop   │
                └────────────┘                                                  └──────────┘
```

1) **Commit/PR**: CI runs lint/tests + governance compliance check (parallel). Governance verifies NIST AI RMF, ISO/IEC 42001, OWASP LLM Top 10, and EU AI Act artifacts.
2) **Merge to main**: Full pipeline runs: data validation → train → classifier eval → **RAG evaluation** (faithfulness, groundedness, citation accuracy, safety probes, abstention precision) → quality gates → HF push → Docker build (with Trivy scan) → deploy.
3) **Training (manual or push-triggered)**: `kaggle-training.yml` for GPU/TPU training on Kaggle.
4) **Quality gates**: Deployment requires **both** classifier evaluation AND RAG evaluation (8 metrics) to pass. HF push is gated on `evaluate-rag` job success.
5) **Feedback loop**: User thumbs-down feedback → `ml/pipelines/export_feedback.py` → retriever negative judgments + regression test candidates → retriever/reranker tuning.

## URA Chatbot — Advanced RAG Pipeline (2026)

The API backend (`App/backend/app/`) implements a production-grade 6-phase Retrieval-Augmented Generation pipeline with local LLM inference:

```
User Query
  → Query Rewriting (spell-correct, abbreviation expand, coreference resolve)
  → InputGuard (OWASP LLM01 prompt injection detection)
  → Semantic Cache (cosine similarity ≥ 0.92 → instant reply)
  → Hybrid Retrieval (dense + BM25 RRF → cross-encoder rerank)
  → Corrective RAG (re-retrieve if avg score < threshold)
  → Clarification / Abstention check
  → LLM Synthesis (Qwen2.5-3B-Instruct, local inference)
  → OutputGuard (PII redaction, XSS sanitization, grounding check)
  → Escalation evaluation → SSE stream or sync response
```

| Component | Implementation | Details |
|-----------|---------------|---------|
| **Chunking** | `RecursiveCharacterTextSplitter` | QA: 600 tokens, PDF: 1000 tokens; section/page metadata |
| **Embeddings** | Configurable (`all-MiniLM-L6-v2` / `multilingual-e5-large`) | Auto-detected dimensions (384/1024) |
| **Vector Store** | Qdrant (local persistent) | Versioned collections, non-destructive indexing |
| **Retrieval** | Hybrid dense + BM25 sparse | RRF fusion + circuit breaker with exponential backoff |
| **Reranking** | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Thread-safe cross-encoder reranking |
| **Query Rewriting** | `query.py` | Abbreviation expansion (15+ URA terms), spell correction, coreference resolution |
| **Semantic Cache** | `cache.py` | Cosine similarity matching, configurable TTL/threshold/max-size |
| **Corrective RAG** | `corrective_rag.py` | Re-retrieve with expanded query when initial quality is low |
| **Generation** | `Qwen/Qwen2.5-3B-Instruct` (local) | HuggingFace transformers, sync + SSE streaming via `TextIteratorStreamer` |
| **Multi-turn Memory** | `database.py` | 5-turn sliding window from SQLite conversation history |
| **Safety** | OWASP LLM Top 10 guardrails | Injection detection, PII redaction, XSS sanitization, grounding verification |
| **Escalation** | `guardrails.py` | Auto-flag for human review on low faithfulness or no results |
| **Observability** | OpenTelemetry + Prometheus | Per-stage latency spans, token usage metrics |
| **Evaluation** | Hit@K, MRR, NDCG, faithfulness | 8-metric quality gates block deployment below thresholds |

- Data model, ingestion flow, and evaluation rubric are documented in [docs/data-schema-and-eval.md](docs/data-schema-and-eval.md).

## Data Ingestion & Augmentation Pipeline

The notebook (`Notebooks/DataIngestion_Augmentation.ipynb`) implements a production-grade data pipeline:

```
CSV/PDF/HF ──▶ Provenance ──▶ Semantic Dedup ──▶ PII Redaction ──▶ QA Generation ──▶ Quality Gates ──▶ Stratified Split
               (SHA-256,       (exact hash +      (email/phone/     (teacher LLM       (groundedness,    (grouped by
                trust policy)   MinHash LSH)       TIN/NID regex)    with RAFT)          citation check)   source variant)
```

| Component | Implementation | Details |
|-----------|---------------|---------|
| **Ingestion** | `pymupdf4llm` + page-level extraction | Section detection (VAT/TIN/customs), fallback for legacy PDFs |
| **Provenance** | `DataProvenanceVerifier` | SHA-256 checksums, trusted source policy, signed manifests |
| **Dedup** | Phased exact-hash + MinHash LSH | Pre/post-augmentation scopes; `datasketch` for semantic near-duplicates |
| **PII Redaction** | Regex-based (Uganda-specific) | Email, phone (+256), TIN, National ID patterns |
| **QA Generation** | Teacher model (Llama/Gemma) | Reject-sampling with `QAQualityGate` (groundedness, artifact detection) |
| **Validation** | Pandera schema | Word count, hash, category, data_type, source columns |
| **Checkpoints** | JSONL/Parquet with lineage | Legacy pickle read-only fallback; pipeline-stage metadata |
| **Splitting** | Stratified by source group | Leakage prevention; `splits/` subdirectory output |
| **Governance** | HF Dataset Card generator | License, language tags (en/lg), bias notes, reproducibility |

## Fine-Tuning Pipeline (Gemma/LLM)

The notebook (`Notebooks/fine_tune_gemma.ipynb`) implements a production-grade fine-tuning pipeline:

```
Teacher QA ──▶ Fail-Fast ──▶ RAG-Aware Format ──▶ SFT (rsLoRA) ──▶ ORPO Stage-2 ──▶ Eval Gates ──▶ Export
              (SHA-256,      (RAFT-style         (Unsloth +        (preference       (domain QA,     (atomic save,
               no fallback)   context+cite)       4-bit QLoRA)      optimization)      groundedness)   hash verify)
```

| Component | Implementation | Details |
|-----------|---------------|---------|
| **Dependencies** | Pinned versions (23 packages) | No `--upgrade`, no git+HEAD; reproducible installs |
| **Reproducibility** | Full PyTorch determinism | `torch.manual_seed`, `cudnn.deterministic`, `PYTHONHASHSEED`, `worker_init_fn` |
| **Data Loading** | Fail-fast with SHA-256 integrity | No synthetic fallback; minimum sample validation |
| **Training Format** | RAFT-style RAG-aware | Context prefix + citation supervision when available |
| **PEFT** | rsLoRA + optional DoRA | BitsAndBytes 4-bit (nf4), configurable target modules |
| **TRL Pipeline** | Raw text path (SFTTrainer) | `dataset_text_field="text"`, `processing_class=tokenizer` |
| **Alignment** | ORPO Stage-2 (optional) | Preference pairs from domain QA; `beta=0.1` |
| **Evaluation** | Domain QA + groundedness + safety | Regression gates (max loss, min accuracy, max hallucination) |
| **Export** | Atomic save → zip → verify → cleanup | `save_model()` before archive, SHA-256 integrity check |
| **Governance** | HF Model Card generator | License, language tags, eval results, bias/limitations notes |

## GitHub Actions Workflows

> **📚 Full Documentation**: See [docs/mlops-workflows.md](docs/mlops-workflows.md) for comprehensive workflow details.

Seven workflows under `.github/workflows/`:

### 1. `ci-ml-pipeline.yml` - Main ML Pipeline
**Triggers**: Push to `main`/`develop`/`feat/*`, PRs, manual dispatch

| Stage | Description |
|-------|-------------|
| Lint & Test | Ruff, Black, isort, MyPy, Pytest |
| Governance Check | NIST/ISO/OWASP/EU AI Act compliance (parallel) |
| Data Validation | Schema validation, quality checks |
| Train Model | Local or Kaggle GPU/TPU training |
| Evaluate Model | Classifier performance metrics |
| **RAG Evaluation** | Faithfulness, groundedness, citation accuracy, safety probes, Luganda eval |
| Quality Gates | Pass/fail thresholds (classifier + RAG) |
| Push to HF | Deploy model to Hugging Face Hub (requires both gates) |
| Build Docker | Multi-stage build, Trivy scan, push to DockerHub |
| Deploy Backend | Production API deployment |

### 2. `frontend-deploy.yml` - Frontend CI/CD
**Triggers**: Push to `main`/`develop` (frontend changes), PRs

| Stage | Description |
|-------|-------------|
| Lint | ESLint + TypeScript checking |
| Build | Next.js production build |
| Deploy Preview | PR preview deployments |
| Build Docker | Build & push frontend Docker image |
| Deploy Production | Production Docker deployment |

### 3. `kaggle-training.yml` - Remote Kaggle Training
**Triggers**: Push (notebook/data changes), manual dispatch

| Stage | Description |
|-------|-------------|
| Resolve | Resolve accelerator (`gpu|tpu`) |
| Data | Detect/upload/run `DataIngestion_Augmentation` |
| Export | Build TPU-ready packed dataset when accelerator is `tpu` |
| Prepare | Push notebook to Kaggle with accelerator-aware metadata |
| Monitor | Poll for completion |
| Process | Download and validate outputs |
| Deploy | Push to Hugging Face |

### 4. `secret-scanning.yml` - 4-Layer Secret Scanning
**Triggers**: Push/PR to main/develop, weekly schedule

| Scanner | Coverage |
|---------|----------|
| TruffleHog v3 | 800+ verified credential detectors |
| Gitleaks v8 | Regex + entropy + custom Uganda PII rules |
| GitGuardian ggshield | 400+ ML-based secret types |
| detect-secrets | Baseline-aware entropy scanning |

### 5. `security-trivy.yml` - Trivy Security Scanning
**Triggers**: Push/PR to main/develop, weekly schedule (Monday 03:00 UTC)

| Scan | Coverage |
|------|----------|
| Filesystem | Python/Node.js/Dart dependency vulnerabilities |
| IaC | Dockerfile + docker-compose misconfiguration |
| License | Block copyleft (AGPL, GPL, SSPL) in production |
| Container Images | API, ML trainer, Frontend (3 images) |
| SBOM | CycloneDX generation for each image |
| Security Gate | Aggregated pass/fail across all scans |

### 6. `codeql-analysis.yml` - CodeQL SAST
**Triggers**: Push/PR to main/develop, weekly schedule

| Language | Query Suite |
|----------|-------------|
| Python | security-extended (SQLi, command injection, hardcoded creds) |
| JavaScript/TypeScript | security-extended (XSS, prototype pollution) |

### 7. `devsecops-sast-dast.yml` - DevSecOps Pipeline
**Triggers**: Push/PR to main/develop, weekly schedule

| Tool | Scope |
|------|-------|
| Semgrep | SAST for Python + TypeScript (custom + OWASP rules) |
| Bandit | Python AST security analysis |
| pip-audit | Python dependency audit (OSV/PyPI) |
| Checkov | IaC compliance (CIS, NIST benchmarks) |
| OWASP ZAP | DAST baseline scan against API |
| OSSF Scorecard | Supply chain security scoring |

## Required Secrets

| Secret | Purpose |
|--------|---------|
| `HF_TOKEN` | Hugging Face API token |
| `KAGGLE_USERNAME`, `KAGGLE_API_TOKEN` | Kaggle API access |
| `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` | Docker Hub push (backend + frontend) |
| `MLFLOW_TRACKING_URI` | MLflow experiment tracking (optional) |
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
| **Supply Chain** | SHA-pinned Actions, OSSF Scorecard, Dependabot | SLSA Level 2+ compliance |
| **Container** | Trivy image scan (3 images), non-root, cap_drop, read_only | Container hardening |

All GitHub Actions are **SHA-pinned** to commit hashes (not mutable tags). See [SECURITY.md](SECURITY.md) for full details.

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

## ML Training Pipeline

The training pipeline prepares data, generates synthetic QA, and fine-tunes Gemma/Llama models for both web API inference (Qwen2.5-3B) and on-device mobile inference (Gemma-2-2B).

### Scripts

| Script | Description |
|--------|-------------|
| `ml/scripts/data_augmentation.py` | Combine CSV FAQs, PDFs, Luganda data into training format |
| `ml/scripts/teacher_qa_generation.py` | Generate synthetic QA using Llama-3.2-3B teacher |
| `ml/scripts/fine_tune_gemma.py` | LoRA/QLoRA fine-tuning for Gemma-2-2B / Llama / T5 |
| `ml/scripts/export_mobile.py` | Export fine-tuned Gemma-2B to GGUF INT4 for mobile inference |
| `ml/scripts/run_training_pipeline.sh` | Full pipeline orchestrator |

### Deployment Targets

| Target | Model | Use Case | Export Format |
|--------|-------|----------|---------------|
| `web_high_accuracy` | Gemma-2-2B | Fine-tuning base | HF safetensors |
| `mobile_gemma_2b` | Gemma-2-2B | On-device mobile inference | GGUF Q4_K_M (~1.5 GB) |
| `mobile_offline` | Llama-3.2-1B | Lightweight mobile | GGUF Q4_K_M (~0.8 GB) |
| `background_t5` | Flan-T5-Small | Background tasks | HF safetensors |

**Web API inference** uses Qwen2.5-3B-Instruct (see `App/backend/app/llm.py`).

### Quick Start

```bash
# Full pipeline (data prep → teacher QA → fine-tuning)
./ml/scripts/run_training_pipeline.sh --target web_high_accuracy

# Fine-tune Gemma-2B for mobile
./ml/scripts/run_training_pipeline.sh --target mobile_gemma_2b

# Dry run (validate data only)
./ml/scripts/run_training_pipeline.sh --dry-run

# Individual steps
python ml/scripts/data_augmentation.py --output artifacts/training_data.jsonl
python ml/scripts/fine_tune_gemma.py --data artifacts/training_data.jsonl --target mobile_gemma_2b

# Export to GGUF for mobile (after fine-tuning)
python ml/scripts/export_mobile.py --adapter artifacts/models/ura-gemma-2-2b-it-*/final --quant Q4_K_M
```

See [ml/README.md](ml/README.md) for detailed documentation.

## Local Development
- Install Python deps with `uv pip install -r requirements.txt` (or `uv sync` if using a lockfile) and run lint/tests via `uv run ruff/pytest/mypy` to mirror CI.
- Frontend (App/frontend/): install with `bun install`; run `bun run dev` for local preview or `bun run lint/test/build` matching CI.
- Keep Kaggle notebook entrypoint versioned; ensure data paths/configs are reproducible.
- API: build and run locally with `docker compose up --build` (expects `app.main:app`). Qdrant runs as a first-class service via docker-compose with healthcheck.
- LLM: Qwen2.5-3B-Instruct downloads automatically on first request (~6 GB). Set `LLM_DEVICE=cpu` for CPU-only inference or `LLM_DEVICE=auto` for GPU auto-detection. Disable with `LLM_ENABLED=false` to fall back to FAQ lookup.

## Container Baseline
- API image (`Dockerfile`) uses multi-stage build, non-root runtime user, exec-style entrypoint, and Python-based healthcheck (no runtime `curl` dependency).
- Training image (`Dockerfile.ml`) runs as non-root and pins core ML tooling versions (`mlflow`, `dvc`, `kaggle`) via build args.
- Both Dockerfiles use BuildKit cache mounts for uv dependency layers (`# syntax=docker/dockerfile:1.7` + `--mount=type=cache,target=/root/.cache/uv`).
- Compose runtime hardening is enabled for production-like services (`read_only` rootfs for API, `cap_drop: [ALL]`, `no-new-privileges:true`, `tmpfs` mounts, `init: true`).

## Quick Commands

```bash
# Trigger full training pipeline
gh workflow run ci-ml-pipeline.yml -f run_training=true -f deploy_model=true

# Trigger Kaggle training (default accelerator is TPU)
gh workflow run kaggle-training.yml -f notebook=ura-training

# Trigger explicit TPU training without EDA in data-ingestion stage
gh workflow run kaggle-training.yml \
  -f notebook=ura-training \
  -f accelerator=tpu \
  -f run_data_eda=false

# Trigger explicit GPU training
gh workflow run kaggle-training.yml \
  -f notebook=ura-training \
  -f accelerator=gpu

# Deploy frontend only
gh workflow run frontend-deploy.yml

# View workflow runs
gh run list --workflow=ci-ml-pipeline.yml
```

## Next Steps
- Set repository secrets in GitHub settings before running workflows
- Configure Hugging Face repository at `mpairweLandwind/ura-chatbot`
- Review [docs/mlops-workflows.md](docs/mlops-workflows.md) for detailed configuration
- Review [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for production deployment guide
- Review [docs/MONITORING.md](docs/MONITORING.md) for observability setup
- Review [docs/MOBILE_SETUP.md](docs/MOBILE_SETUP.md) for mobile app build instructions

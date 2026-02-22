# FinalYearProject

This repository describes a CI/CD pipeline for developing and training a customer-service conversation AI. The pipeline uses GitHub Actions for automation, Kaggle for model training, and Vercel for hosting the frontend UI. Backend uses Python/FastAPI with `uv` for dependency management; frontend (Next.js) uses Bun.

## 📚 Documentation

| Document | Description |
|----------|-------------|
| [Documentation Index](docs/README.md) | Getting started and project overview |
| [MLOps Workflows](docs/mlops-workflows.md) | **Comprehensive CI/CD pipeline documentation** |
| [Data Schema & Evaluation](docs/data-schema-and-eval.md) | Database models and evaluation criteria |

## Pipeline Overview
- **Code**: Source, data configs, and frontend live in this repo on GitHub.
- **CI (GitHub Actions)**: Lint/test backend and UI, validate dataset configs, and build artifacts.
- **Model Training (Kaggle)**: GitHub Actions launches Kaggle notebook jobs via API for training; artifacts (checkpoints/metrics) are pushed back to GitHub Releases or an object store.
- **CD (Vercel)**: Successful main-branch builds trigger Vercel deployments for the frontend.
- **API Container**: FastAPI backend packaged via Docker; images pushed to DockerHub for deployment.

## Project Structure

```
FinalYearProject/
├── .github/workflows/     # CI/CD pipeline (3 workflows)
├── App/                   # Application code
│   ├── app.py            # Gradio HF Spaces app
│   ├── backend/          # FastAPI backend
│   └── frontend/         # Next.js frontend
├── Data/                  # Training data
│   ├── dataset/          # 41 CSV files
│   └── pdfs/             # PDF documents
├── ml/                    # ML pipeline scripts
├── Model/                 # Trained model artifacts
├── Results/               # Metrics and reports
├── Notebooks/             # Jupyter notebooks
│   ├── ura-training.ipynb                 # Classification + RAG pipeline
│   └── DataIngestion_Augmentation.ipynb   # Data ingestion & augmentation pipeline
└── docs/                  # Documentation
```

## End-to-End CI/CD Flow

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  Commit  │───▶│  Lint &  │───▶│  Train   │───▶│ Evaluate │───▶│  Deploy  │
│   Push   │    │   Test   │    │  Model   │    │  Quality │    │   Prod   │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
```

1) **Commit/PR**: Push changes to feature branches; CI runs lint/tests on backend (uv/pytest/mypy/ruff) and frontend (bun lint/test/build).
2) **Merge to main**: CI re-runs; when green, two deploy jobs can follow:
	- **Vercel deploy**: `vercel deploy --prod` using org/project IDs and token; publishes the Next.js UI.
	- **API image push**: Build FastAPI image and push to DockerHub with tags `latest` and commit SHA; downstream infra pulls from DockerHub.
3) **Training (manual or push-triggered)**: Trigger `kaggle-training.yml` (or push notebook/data/ml changes) to run Kaggle notebook training; notebooks are synced, job is started via Kaggle API, results are downloaded, then metrics/checkpoints are published (GitHub Release or object store). CI can gate on training success before promoting artifacts.
4) **Release consumption**: Frontend points to deployed API; API loads the latest validated model from the artifact store or release tag; Docker images from DockerHub are used by runtime (e.g., compose/k8s).

## URA Chatbot — RAG Pipeline

The notebook (`Notebooks/ura-training.ipynb`) implements a production-grade Retrieval-Augmented Generation pipeline:

```
PDF/CSV ──▶ Semantic Chunking ──▶ Qdrant Vector Store ──▶ Hybrid Retrieval ──▶ Reranking ──▶ Generation
               (page-level,          (versioned,           (dense + BM25       (cross-encoder)   (cached T5/Gemma,
                section tags)          non-destructive)      RRF fusion)                           structured JSON)
```

| Component | Implementation | Details |
|-----------|---------------|---------|
| **Chunking** | `RecursiveCharacterTextSplitter` | QA: 600 tokens, PDF: 1000 tokens; section/page metadata |
| **Embeddings** | Configurable (`all-MiniLM-L6-v2` / `multilingual-e5-large`) | Auto-detected dimensions (384/1024) |
| **Vector Store** | Qdrant (local persistent) | Versioned collections, non-destructive indexing |
| **Retrieval** | Hybrid dense + BM25 sparse | Reciprocal Rank Fusion (0.6/0.4 weights) |
| **Reranking** | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Cross-encoder reranking on top candidates |
| **Generation** | Flan-T5 / Gemma-2 / TinyLlama | Singleton cache, structured output with citations |
| **Safety** | OWASP-aligned guardrails | Injection detection, grounding validation, content moderation |
| **Evaluation** | Hit@K, MRR, NDCG, groundedness | Regression gates block deployment below thresholds |

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

## GitHub Actions Workflows

> **📚 Full Documentation**: See [docs/mlops-workflows.md](docs/mlops-workflows.md) for comprehensive workflow details.

Three consolidated workflows under `.github/workflows/`:

### 1. `ci-ml-pipeline.yml` - Main ML Pipeline
**Triggers**: Push to `main`/`develop`/`feat/*`, PRs, manual dispatch

| Stage | Description |
|-------|-------------|
| Lint & Test | Ruff, Black, isort, MyPy, Pytest |
| Data Validation | Schema validation, quality checks |
| Train Model | Local or Kaggle GPU/TPU training |
| Evaluate | Model performance metrics |
| Quality Gates | Pass/fail thresholds |
| Push to HF | Deploy model to Hugging Face Hub |
| Build Docker | Multi-stage build, push to DockerHub |
| Deploy Backend | Production API deployment |

### 2. `frontend-deploy.yml` - Frontend CI/CD
**Triggers**: Push to `main`/`develop` (frontend changes), PRs

| Stage | Description |
|-------|-------------|
| Lint | ESLint + TypeScript checking |
| Build | Next.js production build |
| Deploy Preview | PR preview deployments |
| Deploy Production | Production Vercel deployment |

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

Manual dispatch highlights:
- `accelerator`: `gpu|tpu` (default `tpu`)
- `run_data_eda`: `false` by default for faster data-ingestion runs
- `gpu`: deprecated compatibility input (`true` -> GPU, `false` -> TPU)

## Required Secrets

| Secret | Purpose |
|--------|---------|
| `HF_TOKEN` | Hugging Face API token |
| `KAGGLE_USERNAME`, `KAGGLE_API_TOKEN` | Kaggle API access |
| `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` | Vercel deployment |
| `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` | DockerHub push |
| `MLFLOW_TRACKING_URI` | MLflow experiment tracking (optional) |
| `DEPLOY_KEY`, `API_HOST` | Production server deployment |

## ML Training Pipeline

The training pipeline prepares data, generates synthetic QA, and fine-tunes Gemma/Llama models.

### Scripts

| Script | Description |
|--------|-------------|
| `ml/scripts/data_augmentation.py` | Combine CSV FAQs, PDFs, Luganda data into training format |
| `ml/scripts/teacher_qa_generation.py` | Generate synthetic QA using Llama-3.2-3B teacher |
| `ml/scripts/fine_tune_gemma.py` | LoRA/QLoRA fine-tuning for Gemma-2-2B |
| `ml/scripts/run_training_pipeline.sh` | Full pipeline orchestrator |

### Quick Start

```bash
# Full pipeline (data prep → teacher QA → fine-tuning)
./ml/scripts/run_training_pipeline.sh --target web_high_accuracy

# Dry run (validate data only)
./ml/scripts/run_training_pipeline.sh --dry-run

# Individual steps
python ml/scripts/data_augmentation.py --output artifacts/training_data.jsonl
python ml/scripts/fine_tune_gemma.py --data artifacts/training_data.jsonl --epochs 3
```

See [ml/README.md](ml/README.md) for detailed documentation.

## Local Development
- Install Python deps with `uv pip install -r requirements.txt` (or `uv sync` if using a lockfile) and run lint/tests via `uv run ruff/pytest/mypy` to mirror CI.
- Frontend (App/frontend/): install with `bun install`; run `bun run dev` for local preview or `bun run lint/test/build` matching CI.
- Keep Kaggle notebook entrypoint versioned; ensure data paths/configs are reproducible.
- API: build and run locally with `docker compose up --build` (expects `app.main:app`).

## Container Baseline
- API image (`Dockerfile`) uses multi-stage build, non-root runtime user, exec-style entrypoint, and Python-based healthcheck (no runtime `curl` dependency).
- Training image (`Dockerfile.ml`) runs as non-root and pins core ML tooling versions (`mlflow`, `dvc`, `kaggle`) via build args.
- Both Dockerfiles use BuildKit cache mounts for pip dependency layers (`# syntax=docker/dockerfile:1.7` + `--mount=type=cache`).
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
- Set up Vercel project and link to repository
- Review [docs/mlops-workflows.md](docs/mlops-workflows.md) for detailed configuration

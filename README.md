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
3) **Training (manual or scheduled)**: Trigger `kaggle-training.yml` to run Kaggle notebook training; notebooks are synced, job is started via Kaggle API, results are downloaded, then metrics/checkpoints are published (GitHub Release or object store). CI can gate on training success before promoting artifacts.
4) **Release consumption**: Frontend points to deployed API; API loads the latest validated model from the artifact store or release tag; Docker images from DockerHub are used by runtime (e.g., compose/k8s).

## URA Chatbot specifics
- PDF ingestion -> chunking -> embeddings -> database -> retrieval-augmented chatbot UI.
- Data model, ingestion flow, and evaluation rubric are documented in [docs/data-schema-and-eval.md](docs/data-schema-and-eval.md).

## GitHub Actions Workflows

> **📚 Full Documentation**: See [docs/mlops-workflows.md](docs/mlops-workflows.md) for comprehensive workflow details.

Three consolidated workflows under `.github/workflows/`:

### 1. `ci-ml-pipeline.yml` - Main ML Pipeline
**Triggers**: Push to `main`/`develop`/`feat/*`, PRs, manual dispatch

| Stage | Description |
|-------|-------------|
| Lint & Test | Ruff, Black, isort, MyPy, Pytest |
| Data Validation | Schema validation, quality checks |
| Train Model | Local or Kaggle GPU training |
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

### 3. `kaggle-training.yml` - Remote GPU Training
**Triggers**: Manual dispatch, weekly schedule (Sundays 2 AM UTC)

| Stage | Description |
|-------|-------------|
| Prepare | Push notebook to Kaggle |
| Monitor | Poll for completion |
| Process | Download and validate outputs |
| Deploy | Push to Hugging Face |

## Required Secrets

| Secret | Purpose |
|--------|---------|
| `HF_TOKEN` | Hugging Face API token |
| `KAGGLE_USERNAME`, `KAGGLE_KEY` | Kaggle API access |
| `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` | Vercel deployment |
| `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` | DockerHub push |
| `MLFLOW_TRACKING_URI` | MLflow experiment tracking (optional) |
| `DEPLOY_KEY`, `API_HOST` | Production server deployment |

## Local Development
- Install Python deps with `uv pip install -r requirements.txt` (or `uv sync` if using a lockfile) and run lint/tests via `uv run ruff/pytest/mypy` to mirror CI.
- Frontend (App/frontend/): install with `bun install`; run `bun run dev` for local preview or `bun run lint/test/build` matching CI.
- Keep Kaggle notebook entrypoint versioned; ensure data paths/configs are reproducible.
- API: build and run locally with `docker compose up --build` (expects `app.main:app`).

## Quick Commands

```bash
# Trigger full training pipeline
gh workflow run ci-ml-pipeline.yml -f run_training=true -f deploy_model=true

# Trigger Kaggle GPU training
gh workflow run kaggle-training.yml -f notebook=ura-training -f gpu=true

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
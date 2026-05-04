# MLOps CI/CD Workflows Documentation

## Overview

This project implements a comprehensive MLOps CI/CD pipeline following DataCamp best practices for ML systems. The pipeline automates model training, evaluation, deployment, and monitoring across multiple platforms.

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                           MLOps Pipeline Architecture                                 │
├──────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                      │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐            │
│  │  Code   │───▶│  Test   │───▶│  Train  │───▶│  Eval   │───▶│ RAG     │            │
│  │  Push   │    │  Lint   │    │  Model  │    │ Quality │    │  Eval   │            │
│  └─────────┘    └────┬────┘    └─────────┘    └─────────┘    └────┬────┘            │
│       │              │              │              │               │                 │
│       │         ┌────▼────┐         │              │          ┌────▼────┐            │
│       │         │Governance│         │              │          │ Deploy  │            │
│       │         │  Check   │         │              │          │  Prod   │            │
│       │         └─────────┘         │              │          └─────────┘            │
│       ▼              ▼              ▼              ▼              ▼                   │
│   GitHub         Compliance     Kaggle/       Quality       Hugging Face             │
│   Actions        + OWASP       Local GPU/TPU  Gates         DockerHub                │
│                  + NIST RMF                   + RAG Gates   + Feedback Loop           │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

## Workflows

### 1. ML Pipeline CI/CD (`ci-ml-pipeline.yml`)

**Primary workflow** for ML model development, testing, and deployment.

#### Triggers
- **Push**: `main`, `develop`, `feat/*` branches
- **Pull Request**: `main`, `develop` branches
- **Manual Dispatch**: With options for training and deployment

#### Pipeline Stages

| Stage | Job Name | Description | Dependencies |
|-------|----------|-------------|--------------|
| 1a | `lint-and-test` | Code quality checks | None |
| 1b | `governance-check` | NIST/ISO/OWASP/EU AI Act compliance | None (parallel with 1a) |
| 2 | `data-validation` | Validate dataset integrity | lint-and-test |
| 3 | `train-model` | Train ML model | data-validation |
| 4 | `evaluate-model` | Model performance evaluation | train-model |
| 5 | `evaluate-rag` | RAG pipeline quality gates (8 metrics) | evaluate-model |
| 6 | `push-to-huggingface` | Deploy model to HF Hub | evaluate-model, evaluate-rag |
| 7 | `build-docker` | Build and push Docker image | lint-and-test |
| 8 | `deploy-backend` | Deploy API to production | build-docker, push-to-huggingface |

#### Current PR Gate Behavior (May 2026)

Pull requests validate the App flows without publishing production artifacts:

| Area | Behavior |
|------|----------|
| Backend tests | `tests/test_api.py`, agent endpoint tests, and integration smoke run with isolated analytics DBs, `QDRANT_ENABLED=false`, and `SPEECH_ENABLED=false` for deterministic CI |
| Coverage | `pytest --cov` enforces the current ratcheting baseline of 35%; coverage XML is uploaded on every run |
| Docker publish | `build-docker` is skipped on PRs to avoid registry writes from untrusted pull-request contexts |
| PR image validation | Dedicated Trivy image jobs build and scan API, ML trainer, and frontend images only when relevant image/app paths change; protected branch, schedule, and manual runs scan all images |
| SARIF upload | Trivy SARIF from this workflow uploads to GitHub Security on non-PR events; PRs retain scan artifacts |

#### Stage Details

##### Stage 1a: Lint & Test
```yaml
Package Manager: uv (astral-sh/setup-uv)
Tools Used:
- Ruff: Fast Python linter
- Black: Code formatter
- isort: Import sorter
- MyPy: Type checker
- Pytest: Unit testing with coverage
```

##### Stage 2: Data Validation
```yaml
Tools Used:
- Pandas: Data manipulation
- Great Expectations: Data quality
- Pydantic: Schema validation

Output:
- Results/reports/data_validation_report.json
```

##### Stage 3: Model Training
```yaml
Execution:
- Local: GitHub Actions runner
- Remote: Kaggle notebooks (GPU/TPU)

Configuration:
- ml/configs/training_config.yaml

Output:
- Model/ directory
- Results/metrics/training_metrics.json
```

##### Stage 1b: Governance Check (parallel with Lint & Test)
```yaml
Checks:
- 10 required files exist (guardrails, tracing, database, risk manifest, etc.)
- 29 content keywords verified (OWASP, NIST AI RMF, ISO 42001, EU AI Act)

Script: governance/compliance_check.py

Frameworks:
- NIST AI RMF (govern/map/measure/manage)
- ISO/IEC 42001 (9 clauses)
- OWASP LLM Top 10 (6 categories)
- EU AI Act (transparency, human oversight, risk management)
```

##### Stage 4: Model Evaluation
```yaml
Metrics Computed:
- Accuracy, Precision, Recall, F1
- Confusion Matrix
- Classification Report

Output:
- Results/evaluation/
```

##### Stage 5: RAG Evaluation
```yaml
Metrics (8 total):
- Faithfulness (sentence-level token overlap >= 50%)
- Answer Relevancy (cosine similarity)
- Context Precision (GT word overlap > 20%)
- Context Recall (GT sentence coverage >= 40%)
- Groundedness (trigram overlap)
- Citation Accuracy (GT word overlap > 15%)
- Safety Probe Pass Rate (5 adversarial prompts)
- Abstention Precision (correct refusal rate)

Eval Sets:
- Data/eval/rag_eval.jsonl (21 English samples)
- Data/eval/rag_eval_lg.jsonl (12 Luganda samples)

Script: ml/pipelines/evaluate_rag.py
```

##### Stage 6: Quality Gates
```yaml
Classifier Gates:
- Minimum accuracy >= 0.85
- F1-score >= 0.75
- Latency P95 < 100ms

RAG Gates (block HF push):
- Faithfulness >= 0.6
- Answer Relevancy >= 0.7
- Context Precision >= 0.5
- Context Recall >= 0.5
- Groundedness >= 0.4
- Citation Accuracy >= 0.4
- Safety Probe Pass Rate >= 1.0
- Abstention Precision >= 0.5

Script: ml/pipelines/quality_gates.py, ml/pipelines/evaluate_rag.py
```

##### Stage 7: Hugging Face Deployment
```yaml
Target: mpairweLandwind/ura-chatbot
Method: huggingface_hub Python SDK
```

##### Stage 8: Docker Build
```yaml
Features:
- Multi-stage builds
- Non-root runtime users
- Read-only filesystem + dropped Linux capabilities (compose runtime hardening)
- GitHub Actions cache (gha)
- Trivy security scanning
- Automatic tagging (branch, sha, latest)
```

---

### 2. Frontend CI/CD (`frontend-deploy.yml`)

**Handles Next.js frontend** lint, build, and Docker deployment.

#### Triggers
- **Push**: `main`, `develop` branches (frontend changes)
- **Pull Request**: `main` branch (frontend changes)
- **Manual Dispatch**

#### Pipeline Stages

| Stage | Job Name | Description | Environment |
|-------|----------|-------------|-------------|
| 1 | `lint-frontend` | ESLint + TypeScript checks | - |
| 2 | `build-frontend` | Next.js production build | - |
| 3 | `build-docker` | Build & push Docker image to Docker Hub | - |
| 4 | `deploy-production` | Deploy to production | production |

#### Features
- **Bun**: Fast JavaScript runtime and package manager
- **Docker**: Frontend containerised and pushed to Docker Hub
- **Production Gates**: Only `main` branch deploys to production
- **PR Quality Gates**: ESLint, TypeScript, Vitest unit/component tests, Lighthouse accessibility, and Next.js build all run on PRs; coverage is uploaded as an artifact but no longer blocks the PR test job.

---

### 2b. Security and DevSecOps PR Semantics

Security scanning is intentionally split between blocking PR validation and protected-branch publication:

| Workflow | PR behavior | Non-PR behavior |
|----------|-------------|-----------------|
| `secret-scanning.yml` | TruffleHog, Gitleaks, detect-secrets run; ggshield runs only when GitGuardian is enabled/configured | Same, plus scheduled full-history coverage |
| `security-trivy.yml` | Filesystem, IaC, and license scans run; API, ML trainer, and frontend image scans run only when relevant image/app paths change; SARIF/JSON/SBOM artifacts are uploaded | Full image scan set, plus SARIF upload to GitHub Security |
| `devsecops-sast-dast.yml` | Semgrep, Bandit, pip-audit, Checkov, and threat-model validation run; Checkov SARIF is kept as artifact | Same scans, plus Checkov SARIF upload to GitHub Security |
| OWASP ZAP | Skipped on PR because it needs a live target | Runs on push, schedule, or manual dispatch against the CI API target on port `8087` |
| OSSF Scorecard | Skipped on PR because it needs default-branch repository context | Runs only when default-branch repository context is available |

This keeps PRs fail-closed for actionable issues while avoiding external code-scanning app checks that cannot complete safely in pull-request contexts.
Security workflow concurrency is event-scoped, so manual branch re-runs do not cancel active PR security checks.

---

### 3. Kaggle Training (`kaggle-training.yml`)

**Remote Kaggle training** with accelerator-aware execution (GPU or TPU).

#### Triggers
- **Push**: `Notebooks/**`, `Data/**`, `ml/**`, workflow changes
- **Manual Dispatch**: Select notebook + accelerator options

#### Pipeline Stages

| Stage | Job Name | Description |
|-------|----------|-------------|
| 1 | `resolve-accelerator` | Resolve `gpu|tpu` mode (manual default: `tpu`) |
| 2 | `check-data-changes` | Detect if data pipeline should run |
| 3 | `upload-data-to-kaggle` | Upload `Data/` package to Kaggle dataset |
| 4 | `run-data-ingestion` | Run `DataIngestion_Augmentation` notebook |
| 5 | `prepare-kaggle` | Build training dataset + notebook metadata |
| 6 | `monitor-training` | Monitor kernel completion (accelerator-aware timeout) |
| 7 | `process-model` | Download and process outputs |
| 8 | `fine-tune-model` | Optional post-training fine-tune stage |

#### Manual Dispatch Inputs

| Input | Type | Default | Notes |
|-------|------|---------|-------|
| `notebook` | choice | `ura-training` | Target notebook |
| `accelerator` | choice | `tpu` | `gpu` or `tpu` |
| `gpu` | boolean | `true` | Deprecated compatibility flag |
| `run_data_eda` | boolean | `false` | Controls EDA execution in data-ingestion notebook |
| `run_data_pipeline_first` | boolean | `false` | Forces data-ingestion stage |
| `skip_data_upload` | boolean | `false` | Skip dataset upload stage |

#### Notebook Options
| Notebook | Description |
|----------|-------------|
| `ura-training` | Main classification model |
| `embedding-fine-tune` | Fine-tune embeddings |
| `full-pipeline` | Complete end-to-end training |

#### Features
- **Accelerator Switch**: Clean `gpu|tpu` selection at dispatch
- **TPU-Ready Export**: Fixed-length packed data blocks at `Data/processed/tpu_ready`
- **DataIngestion EDA Toggle**: Faster CI path by skipping heavy EDA unless requested
- **Output Monitoring**: Polls until completion
- **Automatic Upload**: Pushes to Hugging Face

---

## Required Secrets

Configure these in GitHub repository settings:

### ML Pipeline
| Secret | Description | Required For |
|--------|-------------|--------------|
| `HF_TOKEN` | Hugging Face API token | Model deployment |
| `DOCKERHUB_USERNAME` | DockerHub username | Image push |
| `DOCKERHUB_TOKEN` | DockerHub access token | Image push |
| `MLFLOW_TRACKING_URI` | MLflow server URL | Experiment tracking |
| `DEPLOY_KEY` | SSH key for production | Backend deployment |
| `API_HOST` | Production server address | Backend deployment |

### Kaggle
| Secret | Description | Required For |
|--------|-------------|--------------|
| `KAGGLE_USERNAME` | Kaggle account username | Kaggle API |
| `KAGGLE_API_TOKEN` | Kaggle API key | Kaggle API |

### Frontend
| Secret | Description | Required For |
|--------|-------------|--------------|
| `DOCKERHUB_USERNAME` | Docker Hub username | Frontend image push |
| `DOCKERHUB_TOKEN` | Docker Hub access token | Frontend image push |

---

## Required Variables

Configure these in GitHub repository settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `HF_REPO` | `mpairweLandwind/ura-chatbot` | Hugging Face repo |
| `DOCKER_IMAGE` | `landwind/ura-chatbot-api` | Docker image name |
| `API_URL` | - | Production API URL |
| `NEXT_PUBLIC_API_URL` | - | API URL for frontend |
| `PRODUCTION_URL` | - | Production frontend URL |

---

## Directory Structure

```
FinalYearProject/
├── .github/
│   └── workflows/
│       ├── ci-ml-pipeline.yml    # Main ML CI/CD
│       ├── frontend-deploy.yml    # Frontend deployment
│       └── kaggle-training.yml    # Kaggle GPU/TPU training
├── ml/
│   ├── configs/
│   │   └── training_config.yaml   # Training hyperparameters + RAG quality gates
│   ├── pipelines/
│   │   ├── train.py              # Training script
│   │   ├── evaluate.py           # Classifier evaluation
│   │   ├── evaluate_rag.py       # RAG pipeline evaluation (8 metrics)
│   │   ├── export_feedback.py    # Production feedback → JSONL for tuning
│   │   ├── validate_data.py      # Data validation
│   │   ├── quality_gates.py      # Quality checks
│   │   └── push_to_hub.py        # HF deployment
│   ├── scripts/
│   │   ├── prepare_kaggle_notebook.py
│   │   ├── monitor_kaggle.py
│   │   ├── export_tpu_ready_data.py
│   │   └── process_kaggle_output.py
│   └── huggingface/
│       ├── README.md
│       └── app.py
├── governance/                    # Compliance & risk management
│   ├── compliance_check.py       # CI gate (10 files + 29 keywords)
│   └── ai_risk_manifest.yaml     # NIST/ISO/OWASP/EU AI Act risk register
├── Model/                         # Trained model artifacts
├── Results/
│   ├── metrics/                   # Training metrics
│   ├── reports/                   # Validation reports
│   └── plots/                     # Visualization outputs
└── Data/
    ├── dataset/                   # Training CSV files
    └── eval/                      # RAG evaluation datasets
        ├── rag_eval.jsonl         # English (21 samples)
        └── rag_eval_lg.jsonl      # Luganda (12 samples)
```

---

## Workflow Diagrams

### CI/CD Pipeline Flow

```mermaid
graph TD
    A[Push/PR] --> B[Lint & Test]
    A --> B2[Governance Check]
    B --> C{Tests Pass?}
    B2 --> C2{Compliance Pass?}
    C -->|Yes| D[Data Validation]
    C -->|No| X[Fail]
    C2 -->|No| X
    D --> E{Valid Data?}
    E -->|Yes| F[Train Model]
    E -->|No| X
    F --> G[Evaluate Model]
    G --> G2[RAG Evaluation]
    G2 --> H{Quality Gates?}
    H -->|Pass| I[Push to HF Hub]
    H -->|Fail| X
    I --> J[Build Docker]
    J --> K[Deploy Production]
    K --> L[Feedback Loop]
    L -->|Negative feedback| M[Retriever Tuning]

    style A fill:#e1f5fe
    style B2 fill:#fff3e0
    style G2 fill:#fff3e0
    style K fill:#c8e6c9
    style L fill:#e8f5e9
    style X fill:#ffcdd2
```

### Kaggle Training Flow

```mermaid
graph LR
    A[Trigger] --> B[Resolve Accelerator]
    B --> C[Check Data Changes]
    C --> D[Run DataIngestion]
    D --> E[Prepare Training Dataset]
    E --> F{TPU?}
    F -->|Yes| G[Export TPU-ready Packed Data]
    F -->|No| H[Push Training Notebook]
    G --> H
    H --> I[Monitor Execution]
    I --> J{Complete?}
    J -->|No| I
    J -->|Yes| K[Download Outputs]
    K --> L[Process Model]
    L --> M[Push to HF]
    
    style A fill:#e1f5fe
    style M fill:#c8e6c9
```

---

## Manual Workflow Dispatch

### Run Full Training
```bash
gh workflow run ci-ml-pipeline.yml \
  -f run_training=true \
  -f deploy_model=true
```

### Run Kaggle Training
```bash
gh workflow run kaggle-training.yml \
  -f notebook=ura-training
```

### Run Kaggle Training on TPU (recommended)
```bash
gh workflow run kaggle-training.yml \
  -f notebook=ura-training \
  -f accelerator=tpu \
  -f run_data_eda=false
```

### Run Kaggle Training on GPU
```bash
gh workflow run kaggle-training.yml \
  -f notebook=ura-training \
  -f accelerator=gpu
```

### Deploy Frontend Only
```bash
gh workflow run frontend-deploy.yml
```

---

## Monitoring & Observability

### Artifacts Generated
Each workflow run produces artifacts downloadable from GitHub Actions:

| Artifact | Workflow | Contents |
|----------|----------|----------|
| `data-validation-report` | ci-ml-pipeline | JSON validation results |
| `trained-model` | ci-ml-pipeline | Model files, metrics |
| `evaluation-results` | ci-ml-pipeline | Classifier evaluation reports |
| `rag-evaluation-results` | ci-ml-pipeline | RAG metrics (8 scores), eval set results |
| `governance-report` | ci-ml-pipeline | Compliance check output (files + keywords) |
| `frontend-build` | frontend-deploy | Next.js build output |
| `data-pipeline-outputs` | kaggle-training | Processed data + EDA outputs from data-ingestion |
| `kaggle-training-outputs` | kaggle-training | Kaggle notebook outputs |
| `tpu-ready-data` | kaggle-training | Packed fixed-length training shards |

### GitHub Step Summary
Each job posts summaries to the GitHub Actions summary tab:
- Training metrics
- Evaluation results
- Deployment URLs
- Error reports

---

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| HF push fails | Check `HF_TOKEN` secret is valid |
| Kaggle timeout | Increase `--timeout` in monitor script |
| Docker build fails | Check Dockerfile syntax, base image availability |
| Frontend Docker build fails | Check `App/frontend/Dockerfile` and build logs |
| Tests fail | Run `pytest tests/ -v` locally first |

### Debugging Commands
```bash
# View workflow runs
gh run list --workflow=ci-ml-pipeline.yml

# View specific run logs
gh run view <run-id> --log

# Re-run failed jobs
gh run rerun <run-id> --failed
```

---

## Best Practices Implemented

1. **Staged Pipeline**: Fail fast with early linting/testing + parallel governance checks
2. **Quality Gates**: Classifier gates + 8 RAG metrics gates before deployment
3. **Governance-as-Code**: Automated NIST AI RMF, ISO 42001, OWASP LLM, EU AI Act compliance checks
4. **SHA-Pinned Actions**: All GitHub Actions pinned to full SHA hashes (supply chain security)
5. **Fast Package Management**: uv for Python (10-100x faster than pip), Bun for frontend
6. **Caching**: GitHub Actions cache for uv, Bun, Docker layers
6. **Security**: Trivy scanning, secret management, OWASP LLM Top 10 guardrails
7. **Docker Deployment**: Frontend and backend both containerised via Docker Hub
8. **On-Change Retraining**: Push/manual-triggered Kaggle data+training runs
9. **Multi-environment**: Separate development/production configurations
10. **Artifact Management**: Preserved build outputs for debugging
11. **Feedback Loop**: Production thumbs-down → retriever negatives → tuning pipeline
12. **Privacy-by-Design**: PII redaction before storage, configurable data retention TTLs

---

## References

- [DataCamp: CI/CD for Machine Learning](https://www.datacamp.com/tutorial/ci-cd-for-machine-learning)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Hugging Face Hub Documentation](https://huggingface.co/docs/hub)
- [Docker Documentation](https://docs.docker.com/)
- [Kaggle API Documentation](https://github.com/Kaggle/kaggle-api)

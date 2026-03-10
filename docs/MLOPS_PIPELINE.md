# MLOps CI/CD Pipeline Documentation

## Overview

This document describes the automated MLOps CI/CD pipeline for the URA Chatbot project, following best practices from [DataCamp's CI/CD for ML tutorial](https://www.datacamp.com/tutorial/ci-cd-for-machine-learning).

## Architecture

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│                          MLOps Pipeline Architecture (2026)                        │
├───────────────────────────────────────────────────────────────────────────────────┤
│                                                                                    │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐   │
│  │  GitHub  │───▶│  Lint &  │───▶│  Data    │───▶│  Train   │───▶│ Evaluate │   │
│  │  Push/PR │    │  Test    │    │  Valid.  │    │  Model   │    │  Model   │   │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘   │
│       │                                                                │          │
│       │  ┌──────────────┐                                              ▼          │
│       └─▶│  Governance  │ (parallel)                            ┌──────────┐     │
│          │  Compliance  │                                       │ RAG Eval │     │
│          └──────────────┘                                       │  Gates   │     │
│                                                                  └──────────┘     │
│                                                                       │           │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐                        │           │
│  │  Docker  │◀───│  Docker  │◀───│  HF Hub  │◀───────────────────────┘           │
│  │  Deploy  │    │  Build   │    │  Push    │                                     │
│  └──────────┘    └──────────┘    └──────────┘                                     │
│                                                                                    │
│  ┌──────────────────────────────────────────────────────────────────────┐         │
│  │                    Kaggle Training Pipeline                           │         │
│  │  Push Notebook ──▶ Execute ──▶ Monitor ──▶ Download Outputs          │         │
│  └──────────────────────────────────────────────────────────────────────┘         │
│                                                                                    │
│  ┌──────────────────────────────────────────────────────────────────────┐         │
│  │                    Feedback Loop                                      │         │
│  │  Feedback DB ──▶ export_feedback.py ──▶ Retriever tuning sets         │         │
│  └──────────────────────────────────────────────────────────────────────┘         │
│                                                                                    │
└───────────────────────────────────────────────────────────────────────────────────┘
```

## Pipeline Components

### 1. Main ML Pipeline (`.github/workflows/ci-ml-pipeline.yml`)

**Triggers:**
- Push to `main`, `develop`, or `feat/*` branches
- Pull requests to `main` or `develop`
- Manual dispatch with options

**Stages:**

| Stage | Description | Artifacts |
|-------|-------------|-----------|
| Lint & Test | Code quality checks (Ruff, pytest, coverage) | Coverage report |
| Governance Check | NIST/ISO/OWASP/EU AI Act compliance (parallel) | Compliance report |
| Data Validation | Validates CSV/PDF data quality | Validation report |
| Train Model | Trains classifier with CV | Model files, metrics |
| Evaluate Model | Runs classifier evaluation benchmarks | Evaluation results |
| **RAG Evaluation** | Faithfulness, groundedness, citation accuracy, safety probes | RAG metrics JSON |
| Push to HF | Uploads model to Hugging Face Hub (requires RAG eval pass) | Model card |
| Build Docker | Builds and pushes container + Trivy scan | Docker image, SARIF |
| Deploy Backend | Deploys API to production | Running service |

### 2. Frontend Pipeline (`.github/workflows/frontend-deploy.yml`)

**Triggers:**
- Push to `main` or `develop` (frontend changes)
- Pull requests with frontend changes

**Stages:**
- Lint & Type Check (ESLint, TypeScript)
- Build Frontend
- Build Docker Image → Push to Docker Hub
- Deploy Production (main) → Docker deployment

### 3. Kaggle Training Pipeline (`.github/workflows/kaggle-training.yml`)

**Triggers:**
- Push (data/notebook/ml/workflow changes)
- Manual dispatch with notebook and accelerator selection

**Stages:**
- Resolve accelerator (`gpu|tpu`)
- Check data changes and upload data dataset
- Run `DataIngestion_Augmentation`
- Prepare training dataset and notebook metadata
- Export TPU-ready packed data (TPU only)
- Push notebook to Kaggle
- Monitor execution status (accelerator-aware timeout)
- Download training outputs
- Process and deploy model

**Key Manual Inputs (`workflow_dispatch`):**
- `notebook` (`ura-training`, `DataIngestion_Augmentation`, `embedding-fine-tune`, `full-pipeline`)
- `accelerator` (`gpu|tpu`, default `tpu`)
- `run_data_eda` (`true|false`, default `false`)
- `run_data_pipeline_first` (`true|false`)
- `skip_data_upload` (`true|false`)
- `gpu` (deprecated compatibility input)

## Folder Structure

```
FinalYearProject/
├── .github/
│   └── workflows/
│       ├── ci-ml-pipeline.yml      # Main ML CI/CD
│       ├── frontend-deploy.yml     # Frontend Docker deployment
│       └── kaggle-training.yml     # Kaggle training
├── App/                            # Web Application
│   ├── classifier.py               # Gradio classifier app
│   ├── README.md                   # App metadata (HF Space)
│   └── requirements.txt            # App dependencies
├── Data/                           # Training Data
│   ├── README.md                   # Data documentation
│   └── *.csv                       # URA FAQ datasets
├── Model/                          # Trained Models
│   ├── README.md                   # Model documentation
│   ├── tag_classifier.joblib       # sklearn classifier
│   ├── label_encoder.joblib        # Label encoder
│   ├── tag_classifier.pth          # PyTorch model
│   ├── tag_classifier.onnx         # ONNX model
│   └── class_labels.json           # Class labels
├── Results/                        # Training Results
│   ├── README.md                   # Results documentation
│   ├── metrics/                    # JSON metric files
│   ├── plots/                      # PNG visualizations
│   └── reports/                    # Generated reports
├── ml/
│   ├── __init__.py
│   ├── configs/
│   │   └── training_config.yaml    # Training configuration
│   ├── pipelines/
│   │   ├── __init__.py
│   │   ├── validate_data.py        # Data validation
│   │   ├── train.py                # Model training
│   │   ├── evaluate.py             # Model evaluation
│   │   ├── quality_gates.py        # Quality thresholds
│   │   └── push_to_hub.py          # HF Hub upload
│   ├── scripts/
│   │   ├── __init__.py
│   │   ├── prepare_kaggle_notebook.py
│   │   ├── export_tpu_ready_data.py
│   │   ├── monitor_kaggle.py
│   │   └── process_kaggle_output.py
│   └── huggingface/                # HF Space files
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI app + lifespan + security headers
│   │   ├── models.py               # Pydantic v2 models (ChatResponse, Citation)
│   │   ├── service.py              # RAG pipeline service (hybrid + keyword fallback)
│   │   ├── retriever.py            # Qdrant hybrid retriever (dense+BM25+RRF+rerank)
│   │   ├── indexer.py              # Document indexing pipeline (PDF+CSV→Qdrant)
│   │   ├── guardrails.py           # OWASP LLM Top 10 security controls
│   │   ├── tracing.py              # OpenTelemetry GenAI tracing
│   │   ├── database.py             # SQLite analytics + data retention
│   │   └── analytics.py            # Prometheus-compatible metrics
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   └── app/
│   ├── package.json
│   └── Dockerfile                  # Frontend container image
├── tests/
│   ├── __init__.py
│   ├── test_ml_pipeline.py
│   └── test_api.py
├── Notebooks/                      # Jupyter notebooks
├── Dockerfile                      # Production API
├── Dockerfile.ml                   # Training container
├── docker-compose.yml              # Service orchestration
├── pyproject.toml                  # Python tooling config
└── .env.example                    # Environment template
```

## Setup Guide

### Prerequisites

1. **GitHub Repository Secrets**

   Navigate to Settings → Secrets and variables → Actions:

   ```
   KAGGLE_USERNAME      # Kaggle account username
   KAGGLE_API_TOKEN     # Kaggle API key
   HF_TOKEN             # Hugging Face write token
   DOCKERHUB_USERNAME   # Docker Hub username
   DOCKERHUB_TOKEN      # Docker Hub access token
   DOCKER_IMAGE_FRONTEND  # Frontend Docker image name
   ```

2. **GitHub Repository Variables**

   Navigate to Settings → Secrets and variables → Actions → Variables:

   ```
   HF_REPO              # e.g., mpairweLandwind/ura-chatbot
   DOCKER_IMAGE         # e.g., landwind/ura-chatbot-api
   NEXT_PUBLIC_API_URL  # Production API URL
   API_URL              # Backend API URL
   PRODUCTION_URL       # Frontend production URL
   ```

### Obtaining Credentials

#### Kaggle API Key
1. Go to [kaggle.com/account](https://www.kaggle.com/account)
2. Scroll to "API" section
3. Click "Create New API Token"
4. Extract `username` and `key` from downloaded `kaggle.json`

#### Hugging Face Token
1. Go to [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
2. Create new token with "Write" permissions
3. Copy the token

#### Docker Hub Token
1. Go to [hub.docker.com/settings/security](https://hub.docker.com/settings/security)
2. Create new access token
3. Copy the token

### Local Development

1. **Clone and setup:**
   ```bash
   git clone https://github.com/mpairweLandwind/FinalYearProject.git
   cd FinalYearProject
   cp .env.example .env
   # Edit .env with your credentials
   ```

2. **Run with Docker:**
   ```bash
   # Development mode (hot-reload)
   docker compose --profile dev up api-dev
   
   # Production mode
   docker compose up api
   ```

3. **Run ML pipeline locally:**
   ```bash
   # Validate data
   python ml/pipelines/validate_data.py
   
   # Train model
   python ml/pipelines/train.py --config ml/configs/training_config.yaml --output-dir Model
   
   # Evaluate
   python ml/pipelines/evaluate.py --model-path Model --output-dir Results
   
   # Check quality gates
   python ml/pipelines/quality_gates.py
   
   # Run App
   python App/classifier.py
   ```

4. **Run tests:**
   ```bash
   pytest tests/ -v
   ```

## Quality Gates

### Classifier Quality Gates

Models must pass these thresholds before deployment:

| Metric | Minimum Threshold |
|--------|-------------------|
| Accuracy | 0.85 |
| F1-Score (macro) | 0.75 |
| Precision (macro) | 0.75 |
| Recall (macro) | 0.70 |
| Latency (P95) | < 100ms |

### RAG Quality Gates

The RAG pipeline is evaluated by `ml/pipelines/evaluate_rag.py` against `Data/eval/rag_eval.jsonl`. All gates must pass before deployment to Hugging Face.

| Metric | Threshold | Description |
|--------|-----------|-------------|
| Faithfulness | >= 0.6 | Fraction of answer sentences grounded in context |
| Answer Relevancy | >= 0.7 | Cosine similarity between question and answer embeddings |
| Context Precision | >= 0.5 | Fraction of retrieved contexts containing ground-truth info |
| Context Recall | >= 0.5 | Fraction of ground-truth covered by retrieved contexts |
| Groundedness | >= 0.4 | Phrase-level (trigram) grounding in contexts |
| Citation Accuracy | >= 0.4 | Whether cited contexts contain ground-truth information |
| Safety Probe Pass Rate | >= 0.8 | Adversarial prompts blocked by InputGuard |
| Abstention Precision | >= 0.5 | Correct refusal rate on unanswerable questions |

Configure in `ml/configs/training_config.yaml`:

```yaml
quality_gates:
  min_accuracy: 0.85
  min_f1_score: 0.75
  min_precision: 0.75
  min_recall: 0.70
  max_latency_ms: 100

rag_quality_gates:
  min_faithfulness: 0.6
  min_answer_relevancy: 0.7
  min_context_precision: 0.5
  min_context_recall: 0.5
  min_groundedness: 0.4
  min_citation_accuracy: 0.4
  min_safety_probe_pass_rate: 0.8
  min_abstention_precision: 0.5
```

### Governance Gate

The `governance-check` CI job runs `governance/compliance_check.py` in parallel with lint. It verifies:
- Required governance artifacts exist (risk manifest, guardrails, tracing, retriever, eval pipeline, etc.)
- Required content keywords are present in each file (e.g., `InputGuard` in guardrails, `compute_groundedness` in evaluate_rag)
- Maps to NIST AI RMF, ISO/IEC 42001, OWASP LLM Top 10, EU AI Act

## Model Export Formats

The pipeline exports models in multiple formats for different deployment targets:

| Format | File | Use Case |
|--------|------|----------|
| sklearn | `tag_classifier.joblib` | Python backend servers |
| PyTorch | `tag_classifier.pth` | Web (torch.js), fine-tuning |
| ONNX | `tag_classifier.onnx` | Cross-platform (ONNX Runtime) |
| TorchScript | `tag_classifier_scripted.pt` | Mobile (LibTorch) |

## Triggering Workflows

### Manual Triggers

**Full Training + Deployment:**
```bash
gh workflow run ci-ml-pipeline.yml -f run_training=true -f deploy_model=true
```

**Kaggle Training:**
```bash
gh workflow run kaggle-training.yml -f notebook=ura-training
```

**Kaggle TPU Training (recommended):**
```bash
gh workflow run kaggle-training.yml \
  -f notebook=ura-training \
  -f accelerator=tpu \
  -f run_data_eda=false
```

**Kaggle GPU Training:**
```bash
gh workflow run kaggle-training.yml \
  -f notebook=ura-training \
  -f accelerator=gpu
```

### Automatic Triggers

| Event | Workflow | Action |
|-------|----------|--------|
| Push to `main` | All | Full CI/CD + Deploy |
| Push to `develop` | ML Pipeline | Test + Validate |
| PR to `main` | All | Test + Preview Deploy |
| Push to data/notebook/ml/workflow files | Kaggle | Run remote data/training pipeline |

## Monitoring & Debugging

### View Workflow Runs
```bash
gh run list --workflow=ci-ml-pipeline.yml
gh run view <run-id>
gh run view <run-id> --log
```

### Download Artifacts
```bash
gh run download <run-id> -n trained-model
gh run download <run-id> -n evaluation-results
```

### Check Deployment Status
```bash
# Docker
docker ps
curl http://localhost:8000/health

# Frontend container
docker ps --filter "name=frontend"
docker logs ura-chatbot-frontend
```

## Troubleshooting

### Common Issues

1. **Kaggle kernel fails:**
   - Check accelerator/internet is enabled in metadata
   - Verify datasets are accessible
   - Check kernel logs: `kaggle kernels output <kernel-id>`

2. **HF push fails:**
   - Verify `HF_TOKEN` has write permissions
   - Check repo exists or can be created
   - Validate model files exist

3. **Docker build fails:**
   - Check base image availability
   - Verify requirements.txt is valid
   - Check disk space

4. **Frontend Docker build fails:**
   - Check `App/frontend/Dockerfile` syntax
   - Verify `NEXT_PUBLIC_API_URL` variable is set
   - Check GitHub Actions build logs

### Getting Help

- Open an issue on GitHub
- Check workflow run logs
- Review [DataCamp CI/CD tutorial](https://www.datacamp.com/tutorial/ci-cd-for-machine-learning)

## References

- [DataCamp: CI/CD for Machine Learning](https://www.datacamp.com/tutorial/ci-cd-for-machine-learning)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Hugging Face Hub Documentation](https://huggingface.co/docs/hub)
- [Kaggle API Documentation](https://www.kaggle.com/docs/api)
- [Docker Documentation](https://docs.docker.com/)

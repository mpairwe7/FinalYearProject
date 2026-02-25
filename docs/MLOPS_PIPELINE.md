# MLOps CI/CD Pipeline Documentation

## Overview

This document describes the automated MLOps CI/CD pipeline for the URA Chatbot project, following best practices from [DataCamp's CI/CD for ML tutorial](https://www.datacamp.com/tutorial/ci-cd-for-machine-learning).

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          MLOps Pipeline Architecture                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐              │
│  │  GitHub  │───▶│  Lint &  │───▶│  Data    │───▶│  Train   │              │
│  │  Push/PR │    │  Test    │    │  Valid.  │    │  Model   │              │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘              │
│                                                        │                    │
│                                                        ▼                    │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐              │
│  │  Docker  │◀───│  Docker  │◀───│  HF Hub  │◀───│  Quality │              │
│  │  Deploy  │    │  Build   │    │  Push    │    │  Gates   │              │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘              │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────┐          │
│  │                    Kaggle Training Pipeline                   │          │
│  │  Push Notebook ──▶ Execute ──▶ Monitor ──▶ Download Outputs  │          │
│  └──────────────────────────────────────────────────────────────┘          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
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
| Lint & Test | Code quality checks (Ruff, Black, MyPy, pytest) | Coverage report |
| Data Validation | Validates CSV/PDF data quality | Validation report |
| Train Model | Trains classifier with CV | Model files, metrics |
| Evaluate | Runs evaluation benchmarks | Evaluation results |
| Push to HF | Uploads model to Hugging Face Hub | Model card |
| Build Docker | Builds and pushes container | Docker image |
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
│   │   ├── main.py                 # FastAPI app
│   │   ├── models.py               # Pydantic models
│   │   └── service.py              # ML service
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

Models must pass these thresholds before deployment:

| Metric | Minimum Threshold |
|--------|-------------------|
| Accuracy | 0.75 |
| F1-Score (macro) | 0.70 |
| Precision (macro) | 0.70 |
| Recall (macro) | 0.65 |
| Latency (P95) | < 100ms |

Configure in `ml/configs/training_config.yaml`:

```yaml
quality_gates:
  min_accuracy: 0.75
  min_f1_score: 0.70
  min_precision: 0.70
  min_recall: 0.65
  max_latency_ms: 100
```

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

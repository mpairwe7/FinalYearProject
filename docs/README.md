# Project Documentation Index

## Quick Links

| Document | Description |
|----------|-------------|
| [MLOps Workflows](mlops-workflows.md) | CI/CD pipeline documentation (comprehensive) |
| [MLOps Pipeline](MLOPS_PIPELINE.md) | Pipeline architecture and implementation |
| [Data Schema & Evaluation](data-schema-and-eval.md) | Database models and evaluation criteria |

## Getting Started

### For Developers
1. Read [MLOps Workflows](mlops-workflows.md) to understand the CI/CD pipeline
2. Review required secrets and variables configuration
3. Run `gh workflow run ci-ml-pipeline.yml` to trigger a manual build

### For ML Engineers
1. Configure Kaggle credentials (see secrets documentation)
2. Update training config in `ml/configs/training_config.yaml`
3. Trigger training with `gh workflow run kaggle-training.yml`

### For Frontend Developers
1. Configure Vercel secrets
2. Push to `develop` branch for preview deployments
3. Merge to `main` for production deployment

## Project Structure Overview

```
FinalYearProject/
├── .github/workflows/     # CI/CD pipeline definitions
├── App/                   # Application code
│   ├── app.py            # Gradio HF Spaces app
│   ├── backend/          # FastAPI backend
│   └── frontend/         # Next.js frontend
├── Data/                  # Training and reference data
│   ├── dataset/          # CSV training files
│   ├── pdfs/             # PDF documents
│   ├── TTT/              # Translation corpus
│   └── lgaudio/          # Audio files
├── ml/                    # ML pipeline implementation
│   ├── configs/          # Training configuration
│   ├── pipelines/        # Training, evaluation scripts
│   ├── scripts/          # Kaggle integration
│   └── huggingface/      # HF Spaces files
├── Model/                 # Trained model artifacts
├── Results/               # Metrics and reports
│   ├── metrics/          # Training metrics
│   ├── reports/          # Validation reports
│   └── plots/            # Visualizations
├── Notebooks/             # Jupyter notebooks
└── docs/                  # Documentation
```

## Key Workflows

### 1. Development Workflow
```
Code → Push → Lint → Test → Review → Merge
```

### 2. ML Training Workflow
```
Data Validation → Training → Evaluation → Quality Gates → Deployment
```

### 3. Release Workflow
```
Main Branch → Docker Build → HF Push → Production Deploy
```

## Environment Setup

### Required Tools
- Python 3.11+
- Node.js 20+ / Bun
- Docker
- GitHub CLI (`gh`)

### Configuration Files
| File | Purpose |
|------|---------|
| `requirements.txt` | Python dependencies |
| `ml/configs/training_config.yaml` | Training hyperparameters |
| `docker-compose.yml` | Local development setup |
| `Dockerfile` | Production container image |

## Support

For issues related to:
- **CI/CD Pipeline**: Check GitHub Actions logs
- **Model Training**: Review Kaggle notebook outputs
- **Frontend**: Check Vercel deployment logs
- **Backend API**: Review Docker container logs

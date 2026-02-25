# Project Documentation Index

## 📚 Documentation Overview

Complete documentation for the URA Chatbot MLOps project.

## Quick Links

| Document | Description |
|----------|-------------|
| **Setup & Getting Started** |
| [Project Setup](PROJECT_SETUP.md) | Complete installation and setup guide |
| [Quick Start](../QUICKSTART.md) | 5-minute quick start guide |
| **CI/CD & MLOps** |
| [MLOps Workflows](mlops-workflows.md) | Comprehensive CI/CD pipeline documentation |
| [MLOps Pipeline](MLOPS_PIPELINE.md) | Pipeline architecture and implementation details |
| **Application** |
| [API Reference](API_REFERENCE.md) | REST API endpoints and usage |
| [Gradio App](GRADIO_APP.md) | Gradio web interface documentation |
| **Data & Evaluation** |
| [Data Schema & Evaluation](data-schema-and-eval.md) | Database models, RAG pipeline, and evaluation criteria |

## Getting Started

### For Developers
1. Read [Project Setup](PROJECT_SETUP.md) for complete installation
2. Review [MLOps Workflows](mlops-workflows.md) for CI/CD pipeline
3. Configure GitHub secrets as documented
4. Run `gh workflow run ci-ml-pipeline.yml` to trigger a build

### For ML Engineers
1. Configure Kaggle credentials ([see setup guide](PROJECT_SETUP.md#environment-configuration))
2. Update training config in `ml/configs/training_config.yaml`
3. Trigger training (TPU default): `gh workflow run kaggle-training.yml -f notebook=ura-training`
4. Optional explicit mode: `-f accelerator=tpu|gpu` and `-f run_data_eda=false`
5. Monitor results in `Results/` folder
6. Review RAG pipeline configuration in `Notebooks/ura-training.ipynb` (Sections 8–11)
   - Embedding model: change `EMBED_TARGET` in cell 3 (`fast_cpu` / `multilingual` / `multilingual_light`)
   - Index version: bump `INDEX_VERSION` when re-indexing with schema changes
   - Evaluation thresholds: adjust `MRR_THRESHOLD`, `HITATK_THRESHOLD`, `GROUNDING_THRESHOLD` in eval cells

### For Frontend Developers
1. Run `cd App/frontend && bun run dev` for local development
2. Push to `develop` for CI checks
3. Merge to `main` for production Docker build and deployment

### For API Users
1. Review [API Reference](API_REFERENCE.md) for endpoints
2. Access Swagger docs at `/docs` endpoint
3. Use provided SDK examples for integration

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
- **Frontend**: Check Docker container logs or GitHub Actions build logs
- **Backend API**: Review Docker container logs

# Project Setup Guide

## Overview

URA Chatbot is an AI-powered customer service assistant for Uganda Revenue Authority. This guide covers complete project setup from scratch.

## Prerequisites

### Required Software

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.11+ | Backend, ML pipelines |
| Node.js | 20+ | Frontend build |
| Bun | Latest | Fast JS runtime |
| Docker | 24+ | Containerization |
| Git | 2.40+ | Version control |
| GitHub CLI | 2.40+ | Workflow management |

### Installation Commands

```bash
# Ubuntu/Debian
sudo apt update && sudo apt install -y python3.11 python3-pip nodejs npm docker.io git

# Install Bun
curl -fsSL https://bun.sh/install | bash

# Install GitHub CLI
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
sudo apt update && sudo apt install gh
```

## Project Setup

### 1. Clone Repository

```bash
git clone https://github.com/mpairweLandwind/FinalYearProject.git
cd FinalYearProject
```

### 2. Python Environment

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: .\venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt
pip install -r App/backend/requirements.txt
```

### 3. Frontend Setup

```bash
cd App/frontend
bun install
cd ../..
```

### 4. Environment Configuration

```bash
# Copy example environment file
cp .env.example .env

# Edit with your credentials
nano .env
```

Required environment variables:
```env
# Hugging Face
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxx

# Kaggle (for training)
KAGGLE_USERNAME=your_username
KAGGLE_API_TOKEN=your_api_key

# DockerHub
DOCKERHUB_USERNAME=your_username
DOCKERHUB_TOKEN=your_token

# Vercel (for frontend deployment)
VERCEL_TOKEN=your_token
VERCEL_ORG_ID=your_org_id
VERCEL_PROJECT_ID=your_project_id
```

## Running the Application

### Option 1: Docker Compose (Recommended)

```bash
# Production mode
docker-compose up -d api

# Development mode (with hot reload)
docker-compose --profile dev up api-dev

# View logs
docker-compose logs -f api
```

### Option 2: Manual Start

```bash
# Terminal 1: Backend API
cd App/backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2: Frontend
cd App/frontend
bun run dev

# Terminal 3: Gradio App
python App/app.py
```

### Option 3: Gradio Only

```bash
# Simple classifier demo
python App/classifier.py
```

## Project Structure

```
FinalYearProject/
├── .github/
│   └── workflows/           # CI/CD pipelines
│       ├── ci-ml-pipeline.yml
│       ├── frontend-deploy.yml
│       └── kaggle-training.yml
│
├── App/                     # Application Code
│   ├── app.py              # Full Gradio app (HF Spaces)
│   ├── classifier.py       # Simple classifier demo
│   ├── backend/            # FastAPI REST API
│   │   ├── app/
│   │   │   ├── main.py     # API routes
│   │   │   ├── models.py   # Pydantic schemas
│   │   │   └── service.py  # ML service
│   │   └── requirements.txt
│   └── frontend/           # Next.js UI
│       ├── src/app/
│       ├── package.json
│       └── next.config.mjs
│
├── Data/                    # Training Data
│   ├── dataset/            # CSV FAQ files (41 files)
│   ├── pdfs/               # Reference PDFs (45 files)
│   ├── TTT/                # Translation corpus
│   └── lgaudio/            # Audio files
│
├── ml/                      # ML Pipeline Code
│   ├── configs/
│   │   └── training_config.yaml
│   ├── pipelines/
│   │   ├── train.py
│   │   ├── evaluate.py
│   │   ├── validate_data.py
│   │   ├── quality_gates.py
│   │   └── push_to_hub.py
│   ├── scripts/
│   │   ├── prepare_kaggle_notebook.py
│   │   ├── monitor_kaggle.py
│   │   └── process_kaggle_output.py
│   └── huggingface/
│
├── Model/                   # Trained Models
│   ├── tag_classifier.joblib
│   ├── label_encoder.joblib
│   └── manifest.json
│
├── Results/                 # Training Outputs
│   ├── metrics/            # JSON metrics
│   ├── plots/              # PNG visualizations
│   └── reports/            # CSV reports
│
├── Notebooks/               # Jupyter Notebooks
│   └── ura-training.ipynb
│
├── tests/                   # Unit Tests
│   ├── test_ml_pipeline.py
│   └── test_api.py
│
├── docs/                    # Documentation
│
├── Dockerfile              # API container
├── Dockerfile.ml           # Training container
├── docker-compose.yml      # Service orchestration
├── requirements.txt        # Python dependencies
└── pyproject.toml          # Python tooling config
```

## API Endpoints

### Health Check
```bash
curl http://localhost:8000/health
```

### Classification
```bash
curl -X POST http://localhost:8000/classify \
  -H "Content-Type: application/json" \
  -d '{"text": "How do I register for TIN?"}'
```

### Chat
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is VAT rate in Uganda?"}'
```

## Running Tests

```bash
# All tests
pytest tests/ -v

# With coverage
pytest tests/ -v --cov=ml --cov=App/backend --cov-report=html

# Specific test file
pytest tests/test_api.py -v
```

## ML Pipeline Commands

### Train Model Locally
```bash
python ml/pipelines/train.py \
  --config ml/configs/training_config.yaml \
  --output-dir Model
```

### Evaluate Model
```bash
python ml/pipelines/evaluate.py \
  --model-path Model \
  --output-dir Results
```

### Validate Data
```bash
python ml/pipelines/validate_data.py
```

### Check Quality Gates
```bash
python ml/pipelines/quality_gates.py
```

### Push to Hugging Face
```bash
python ml/pipelines/push_to_hub.py \
  --model-path Model \
  --repo-id mpairweLandwind/ura-chatbot
```

## Triggering CI/CD Workflows

### Via GitHub CLI
```bash
# Authenticate
gh auth login

# List workflows
gh workflow list

# Run ML pipeline with training
gh workflow run ci-ml-pipeline.yml -f run_training=true -f deploy_model=true

# Run Kaggle training
gh workflow run kaggle-training.yml -f notebook=ura-training -f gpu=true

# View run status
gh run list --workflow=ci-ml-pipeline.yml
gh run view <run-id> --log
```

### Via GitHub UI
1. Go to repository → Actions tab
2. Select workflow
3. Click "Run workflow"
4. Fill in parameters
5. Click "Run workflow"

## Troubleshooting

### Port Already in Use
```bash
# Find process using port 8000
lsof -i :8000
# Kill it
kill -9 <PID>
```

### Docker Issues
```bash
# Reset Docker
docker-compose down -v
docker system prune -f
docker-compose up --build
```

### Python Import Errors
```bash
# Ensure virtual environment is active
source venv/bin/activate

# Reinstall dependencies
pip install -r requirements.txt --force-reinstall
```

### Bun/Node Issues
```bash
# Clear cache and reinstall
cd App/frontend
rm -rf node_modules bun.lockb
bun install
```

## Next Steps

1. Read [MLOps Workflows](mlops-workflows.md) for CI/CD details
2. Review [Data Schema](data-schema-and-eval.md) for data model
3. Configure GitHub secrets for deployment
4. Set up Hugging Face Space for the Gradio app

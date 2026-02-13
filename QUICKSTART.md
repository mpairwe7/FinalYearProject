# URA Chatbot - Quick Start Guide

## 🚀 Quick Setup (5 minutes)

### 1. Clone & Install

```bash
git clone https://github.com/mpairweLandwind/FinalYearProject.git
cd FinalYearProject

# Backend
pip install -r requirements.txt

# Frontend
cd frontend && bun install && cd ..
```

### 2. Set Up Environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 3. Run Locally

```bash
# Backend API
uvicorn backend.app.main:app --reload

# Frontend (new terminal)
cd frontend && bun run dev

# Classifier App (Gradio)
python App/classifier.py
```

## 📁 Project Structure

```
FinalYearProject/
├── App/           # Web classifier (Gradio)
├── Data/          # Training CSV files  
├── Model/         # Trained model files
├── Results/       # Metrics & plots
├── ml/            # ML pipelines
├── backend/       # FastAPI backend
└── frontend/      # Next.js frontend
```

## 🔧 GitHub Actions Setup

Add these secrets in GitHub → Settings → Secrets:

| Secret | Where to Get It |
|--------|-----------------|
| `HF_TOKEN` | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) |
| `KAGGLE_USERNAME` | [kaggle.com/account](https://www.kaggle.com/account) |
| `KAGGLE_API_TOKEN` | [kaggle.com/account](https://www.kaggle.com/account) |
| `DOCKERHUB_USERNAME` | Your Docker Hub username |
| `DOCKERHUB_TOKEN` | [hub.docker.com/settings/security](https://hub.docker.com/settings/security) |
| `VERCEL_TOKEN` | [vercel.com/account/tokens](https://vercel.com/account/tokens) |

## 📊 Run ML Pipeline

```bash
# Validate data
python ml/pipelines/validate_data.py

# Train model
python ml/pipelines/train.py --config ml/configs/training_config.yaml --output-dir Model

# Evaluate
python ml/pipelines/evaluate.py --model-path Model --output-dir Results

# Check quality gates
python ml/pipelines/quality_gates.py
```

## 🐳 Docker

```bash
# Development
docker compose --profile dev up

# Production
docker compose up api
```

## 📚 Documentation

- [MLOps Pipeline Guide](docs/MLOPS_PIPELINE.md)
- [Data Schema](docs/data-schema-and-eval.md)

## 🎯 Workflows

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| ML Pipeline | Push to main | Train → Evaluate → Deploy |
| Frontend | Push to main | Build → Deploy to Vercel |
| Kaggle Training | Push/Manual | Remote GPU/TPU data + training pipeline |

---

**Need help?** Open an issue or check the [docs](docs/).

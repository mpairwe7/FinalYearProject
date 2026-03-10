# URA Chatbot - Quick Start Guide

## 🚀 Quick Setup (5 minutes)

### 1. Clone & Install

```bash
git clone https://github.com/mpairweLandwind/FinalYearProject.git
cd FinalYearProject

# Backend (uv package manager)
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
uv pip install -r App/backend/requirements.txt

# Frontend (Bun)
cd App/frontend && bun install && cd ../..
```

### 2. Install Secret Scanning Hooks

```bash
# One-command setup (TruffleHog + ggshield + Gitleaks + detect-secrets)
bash scripts/setup-secret-scanning.sh
```

### 3. Set Up Environment

```bash
cp .env.example .env
# Edit .env with your settings

# Key LLM settings (optional — defaults work out of the box):
# LLM_MODEL=Qwen/Qwen2.5-3B-Instruct  # ~6 GB, auto-downloads on first run
# LLM_DEVICE=auto                       # auto|cpu|cuda
# LLM_ENABLED=true                      # false = FAQ lookup fallback
# QDRANT_URL=http://localhost:6333       # Required for hybrid retrieval
```

### 4. Run Locally

```bash
# Backend API
cd App/backend && uvicorn app.main:app --reload

# Frontend (new terminal)
cd App/frontend && bun run dev

# Classifier App (Gradio)
python App/classifier.py
```

## 📁 Project Structure

```
FinalYearProject/
├── App/           # Web classifier (Gradio)
├── Data/          # Training CSV files + PDFs
├── Model/         # Trained model files
├── Results/       # Metrics & plots
├── Notebooks/     # Jupyter notebooks (classification + RAG pipeline)
├── ml/            # ML pipelines
├── App/backend/   # FastAPI backend
└── App/frontend/  # Next.js frontend
```

## 📓 Notebook Sections (ura-training.ipynb)

| Section | What It Does |
|---------|-------------|
| §1–6 | Setup, data loading, preprocessing, EDA |
| §7 | Supervised classification pipeline (SGD + embeddings) |
| §8 | **RAG pipeline**: semantic chunking, Qdrant indexing, hybrid retrieval (dense + BM25 + reranking) |
| §9 | **Answer generation**: cached models, structured output with citations, safety guardrails |
| §10 | **RAG evaluation**: Hit@K, MRR, NDCG, groundedness, regression gates |
| §11 | **End-to-end benchmarks**: per-stage latency profiling |
| §12–13 | T5 tag generation, TTS (optional) |

## 📓 Notebook Sections (DataIngestion_Augmentation.ipynb)

| Section | What It Does |
|---------|-------------|
| §1–4 | Setup, imports, configuration (provenance, PII, dedup settings) |
| §5–6 | Text processing with PII redaction (Uganda-specific patterns) |
| §7–8 | Data loading (CSV/PDF/HF), page-level PDF extraction with section detection |
| §9 | Pandera schema validation (hash, category, data_type, word counts) |
| §10 | JSONL/Parquet checkpointing with lineage metadata |
| §11 | Teacher model QA generation (Llama/Gemma with RAFT) |
| §12 | **Data augmentation**: phased dedup (exact hash + MinHash LSH), PII redaction |
| §13 | **Provenance verification**: SHA-256 checksums, trusted source policy |
| §14 | **QA quality gates**: groundedness, relevance, artifact detection |
| §15 | **Governance**: HF dataset card generation (license, language tags, bias) |
| §16 | **Pipeline**: stratified splitting (grouped by source), end-to-end orchestration |
| §17–23 | EDA visualizations, quality assessment, report export |

## 📓 Notebook Sections (fine_tune_gemma.ipynb)

| Section | What It Does |
|---------|-------------|
| §1–3 | Setup (pinned deps), imports with full PyTorch determinism, configuration (PEFT, ORPO, RAG-aware) |
| §4–8 | Visualization utilities, data analysis, training hooks, evaluation hooks, IEEE report generator |
| §9 | Model loading with Unsloth + rsLoRA/DoRA + BitsAndBytes 4-bit quantization |
| §10 | **Fail-fast data loading**: SHA-256 integrity, RAG-aware RAFT-style formatting |
| §11–13 | Dataset prep (raw text path), training args, SFTTrainer init (`processing_class`) |
| §14 | **Training execution** with visualization callback |
| §15 | **Comprehensive eval**: domain QA, groundedness, hallucination, safety probes, regression gates |
| §16 | **ORPO Stage-2**: preference optimization with generated chosen/rejected pairs |
| §17 | **Model card**: HF-standard with license, eval results, bias/limitations |
| §18–19 | IEEE report export, test inference (URA domain questions) |
| §20 | **Atomic export**: `save_model()` → zip → SHA-256 verify → cleanup |
| §21–23 | Advanced optimizations, monitoring/logging, final report |

## 🔧 GitHub Actions Setup

Add these secrets in GitHub → Settings → Secrets:

| Secret | Where to Get It |
|--------|-----------------|
| `HF_TOKEN` | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) |
| `KAGGLE_USERNAME` | [kaggle.com/account](https://www.kaggle.com/account) |
| `KAGGLE_API_TOKEN` | [kaggle.com/account](https://www.kaggle.com/account) |
| `DOCKERHUB_USERNAME` | Your Docker Hub username |
| `DOCKERHUB_TOKEN` | [hub.docker.com/settings/security](https://hub.docker.com/settings/security) |
| `GITGUARDIAN_API_KEY` | [dashboard.gitguardian.com](https://dashboard.gitguardian.com/api/personal-access-tokens) |

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

## Mobile Offline Model (Gemma-2B GGUF)

The mobile app runs a quantized Gemma-2-2B-IT model on-device via MediaPipe LLM Inference API, with automatic fallback to the remote API when the model is not bundled or the device is too constrained.

### Full Pipeline

```bash
# 1. Prepare training data (CSV FAQs + PDFs + Luganda + teacher QA)
python ml/scripts/data_augmentation.py \
  --csv-dir Data/dataset \
  --pdf-dir Data/pdfs \
  --luganda-dir Data/TTT \
  --output artifacts/training_data.jsonl

# 2. Fine-tune Gemma-2B with QLoRA (requires GPU, ~16 GB VRAM)
python ml/scripts/fine_tune_gemma.py \
  --data artifacts/training_data.jsonl \
  --target mobile_gemma_2b \
  --output artifacts/models/ura-gemma-2b

# 3. Export to GGUF Q4_K_M (~1.5 GB) — requires llama.cpp
python ml/scripts/export_mobile.py \
  --adapter artifacts/models/ura-gemma-2b/final \
  --quant Q4_K_M \
  --output artifacts/mobile/

# 4. Bundle into Android APK
cp artifacts/mobile/ura-gemma-2b-q4_k_m.gguf \
   MobileApp/ura_chatbot/android/app/src/main/assets/models/

# 5. For iOS, add the GGUF to the Xcode project bundle resources
```

### Architecture

```
Flutter App <-> MethodChannel <-> Kotlin/Swift <-> MediaPipe LLM Inference <-> GGUF
  (Dart)        (Platform Bridge)   (Native)        (Android 12+/iOS 16+)     (Q4_K_M)
```

### Key Files

| File | Purpose |
|------|---------|
| `ml/scripts/data_augmentation.py` | Multi-source training data preparation |
| `ml/scripts/fine_tune_gemma.py` | QLoRA fine-tuning (mobile_gemma_2b target) |
| `ml/scripts/export_mobile.py` | Merge + GGUF convert + quantize + validate |
| `ml/scripts/teacher_qa_generation.py` | Synthetic QA from PDFs (teacher model) |
| `MobileApp/.../on_device_llm.dart` | Flutter inference layer + API fallback |
| `MobileApp/.../MainActivity.kt` | Android MediaPipe bridge |
| `MobileApp/.../AppDelegate.swift` | iOS MediaPipe bridge |
| `ml/configs/training_config.yaml` | Mobile export config (Q4_K_M, MediaPipe) |

### Device Requirements

- **Android**: API 24+, 6+ GB RAM, ~1.5 GB storage
- **iOS**: iOS 16+, iPhone 12+ recommended
- Model auto-downloads from assets on first launch

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
| Frontend | Push to main | Build → Docker image → Docker Hub |
| Kaggle Training | Push/Manual | Remote GPU/TPU data + training pipeline |
| Secret Scanning | Push/PR/Weekly | 4-layer secret detection (TruffleHog, Gitleaks, ggshield, detect-secrets) |

---

**Need help?** Open an issue or check the [docs](docs/).

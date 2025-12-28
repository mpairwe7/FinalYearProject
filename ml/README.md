# ML Training Pipeline

This directory contains the machine learning training pipeline for the URA Tax Assistant, including data preparation, model fine-tuning, and evaluation.

## 📁 Directory Structure

```
ml/
├── pipelines/              # ML pipeline components
│   ├── train.py           # Main training script
│   ├── evaluate.py        # Model evaluation
│   └── quality_gates.py   # Quality checks before deployment
├── scripts/               # Utility scripts
│   ├── data_augmentation.py       # Combine data sources for training
│   ├── teacher_qa_generation.py   # Generate synthetic QA using teacher model
│   ├── fine_tune_gemma.py         # LoRA fine-tuning for Gemma/Llama
│   ├── run_training_pipeline.sh   # Full pipeline orchestrator
│   ├── prepare_kaggle_notebook.py # Prepare notebook for Kaggle training
│   ├── monitor_kaggle.py          # Monitor Kaggle training jobs
│   └── process_kaggle_output.py   # Process artifacts from Kaggle
└── configs/               # Training configurations
    └── training_config.yaml
```

## 🚀 Quick Start

### Prerequisites

```bash
# Install dependencies
pip install transformers datasets peft accelerate bitsandbytes trl

# For teacher model (optional)
pip install torch>=2.0
```

### Run Full Pipeline

```bash
# Full training pipeline
./ml/scripts/run_training_pipeline.sh

# With options
./ml/scripts/run_training_pipeline.sh \
    --target web_high_accuracy \
    --epochs 3 \
    --batch-size 4

# Dry run (validate data without training)
./ml/scripts/run_training_pipeline.sh --dry-run
```

### Individual Scripts

```bash
# Step 1: Data Augmentation
python ml/scripts/data_augmentation.py \
    --csv-dir datasets \
    --pdf-dir pdfs \
    --luganda-dir TTT \
    --output artifacts/training_data.jsonl

# Step 2: Teacher QA Generation (optional, needs GPU)
python ml/scripts/teacher_qa_generation.py \
    --pdf-dir pdfs \
    --output artifacts/teacher_qa

# Step 3: Fine-tuning
python ml/scripts/fine_tune_gemma.py \
    --data artifacts/training_data.jsonl \
    --synthetic artifacts/teacher_qa_gemma.jsonl \
    --target web_high_accuracy \
    --epochs 3
```

## 📊 Training Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                    TRAINING PIPELINE FLOW                       │
└─────────────────────────────────────────────────────────────────┘

┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   CSV FAQs  │    │   PDF Docs  │    │  Luganda/   │
│  datasets/  │    │   pdfs/     │    │  TTT/       │
└──────┬──────┘    └──────┬──────┘    └──────┬──────┘
       │                  │                  │
       └──────────────────┼──────────────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │  data_augmentation.py │
              │  - Load all sources   │
              │  - Format for Gemma   │
              │  - Clean & validate   │
              └───────────┬───────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │ training_data.jsonl   │
              │ gemma_format.jsonl    │
              │ instruction.jsonl     │
              └───────────┬───────────┘
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
        ▼                 │                 │
┌───────────────┐         │                 │
│ teacher_qa_   │         │                 │
│ generation.py │         │                 │
│ (Optional)    │         │                 │
└───────┬───────┘         │                 │
        │                 │                 │
        ▼                 │                 │
┌───────────────┐         │                 │
│ teacher_qa_   │         │                 │
│ gemma.jsonl   │─────────┘                 │
└───────────────┘                           │
                          │                 │
                          ▼                 │
              ┌───────────────────────┐     │
              │  fine_tune_gemma.py   │◄────┘
              │  - Load LoRA config   │
              │  - QLoRA (4-bit)      │
              │  - SFTTrainer         │
              └───────────┬───────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │  Fine-tuned Model     │
              │  artifacts/ura-gemma  │
              └───────────────────────┘
```

## 📋 Scripts Reference

### 1. data_augmentation.py

Combines multiple data sources into training format.

**Input Sources:**
- `datasets/` - CSV FAQ files (40+ files with Q&A pairs)
- `pdfs/` - PDF documents chunked for RAG
- `TTT/` - Luganda translation data

**Output Formats:**
- `training_data.jsonl` - Raw Q&A pairs
- `gemma_format.jsonl` - Gemma turn format
- `instruction_format.jsonl` - Alpaca-style format

```bash
python ml/scripts/data_augmentation.py \
    --csv-dir datasets \
    --pdf-dir pdfs \
    --luganda-dir TTT \
    --output artifacts/training_data.jsonl \
    --max-samples 10000
```

### 2. teacher_qa_generation.py

Uses Llama-3.2-3B as a teacher model to generate synthetic Q&A pairs from PDF content.

**Process:**
1. Load PDF chunks
2. For each chunk, generate 5 questions using teacher model
3. Generate answers using the same model
4. Format for fine-tuning

```bash
python ml/scripts/teacher_qa_generation.py \
    --pdf-dir pdfs \
    --model meta-llama/Llama-3.2-3B-Instruct \
    --questions-per-chunk 5 \
    --output artifacts/teacher_qa
```

**Output:**
- `teacher_qa.jsonl` - Raw QA pairs
- `teacher_qa_gemma.jsonl` - Gemma format
- `teacher_qa_instruction.jsonl` - Instruction format

### 3. fine_tune_gemma.py

Fine-tunes Gemma-2-2B (or Llama) using LoRA/QLoRA.

**Model Targets:**
| Target | Model | Use Case | VRAM |
|--------|-------|----------|------|
| `web_high_accuracy` | Gemma-2-2B | Web deployment | 8GB |
| `mobile_offline` | Llama-3.2-1B | Mobile apps | 4GB |
| `background_t5` | Flan-T5-Small | Background tasks | 2GB |

```bash
# High accuracy for web
python ml/scripts/fine_tune_gemma.py \
    --target web_high_accuracy \
    --data artifacts/training_data.jsonl \
    --epochs 3

# Mobile-optimized
python ml/scripts/fine_tune_gemma.py \
    --target mobile_offline \
    --data artifacts/training_data.jsonl \
    --epochs 5
```

**LoRA Configuration:**
- Rank (r): 16 (adjustable)
- Alpha: 32
- Target modules: q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj
- Quantization: 4-bit NF4 (QLoRA)

### 4. run_training_pipeline.sh

Orchestrates the full training pipeline.

```bash
# Full pipeline
./ml/scripts/run_training_pipeline.sh

# Skip teacher QA (faster)
./ml/scripts/run_training_pipeline.sh --skip-teacher

# Custom configuration
./ml/scripts/run_training_pipeline.sh \
    --target mobile_offline \
    --epochs 5 \
    --batch-size 2 \
    --learning-rate 1e-4
```

## 🔧 CI/CD Integration

### GitHub Actions Workflows

The training scripts are integrated into two workflows:

#### 1. ci-ml-pipeline.yml (Local Training)
```yaml
train-model:
  steps:
    - name: Run training
      run: |
        python ml/pipelines/train.py --config ml/configs/training_config.yaml
```

#### 2. kaggle-training.yml (Remote GPU Training)
```yaml
jobs:
  prepare:
    - name: Prepare notebook
      run: python ml/scripts/prepare_kaggle_notebook.py
  
  monitor:
    - name: Monitor training
      run: python ml/scripts/monitor_kaggle.py
  
  process:
    - name: Process output
      run: python ml/scripts/process_kaggle_output.py
```

### Manual Workflow Trigger

```bash
# Trigger Kaggle training
gh workflow run kaggle-training.yml

# With custom parameters
gh workflow run kaggle-training.yml \
    -f target=mobile_offline \
    -f epochs=5
```

## 📈 Evaluation

After training, evaluate the model:

```bash
# Run evaluation
python ml/pipelines/evaluate.py \
    --model artifacts/ura-gemma-finetuned/final \
    --test-data artifacts/test_data.jsonl

# Quality gates check
python ml/pipelines/quality_gates.py \
    --metrics Results/metrics/evaluation_metrics.json
```

## 🌐 Deployment

### Push to HuggingFace

```bash
# Login
huggingface-cli login

# Upload model
huggingface-cli upload \
    your-username/ura-tax-assistant-gemma \
    artifacts/ura-gemma-finetuned/final
```

### Export for Mobile

```bash
# Convert to ONNX for mobile
python ml/scripts/export_mobile.py \
    --model artifacts/ura-gemma-finetuned/final \
    --output artifacts/mobile/model.onnx
```

## 📊 Metrics & Monitoring

Training metrics are saved to:
- `Results/metrics/` - JSON metrics files
- `Results/plots/` - Training curves
- `Results/reports/` - Evaluation reports

Key metrics tracked:
- Training loss
- Validation loss
- Perplexity
- BLEU score (for QA)
- Response accuracy

## 🔍 Troubleshooting

### Out of Memory (OOM)

```bash
# Reduce batch size
python ml/scripts/fine_tune_gemma.py --batch-size 1

# Use gradient checkpointing (enabled by default)
# Reduce sequence length
python ml/scripts/fine_tune_gemma.py --max-seq-length 1024
```

### Slow Training

```bash
# Use Flash Attention 2 (if available)
pip install flash-attn

# Enable mixed precision (enabled by default)
```

### Data Issues

```bash
# Validate data before training
python ml/scripts/fine_tune_gemma.py --dry-run

# Check data format
python ml/scripts/data_augmentation.py --validate-only
```

## 📚 References

- [LoRA: Low-Rank Adaptation](https://arxiv.org/abs/2106.09685)
- [QLoRA: Efficient Finetuning](https://arxiv.org/abs/2305.14314)
- [Gemma Technical Report](https://ai.google.dev/gemma)
- [TRL: Transformer Reinforcement Learning](https://huggingface.co/docs/trl)

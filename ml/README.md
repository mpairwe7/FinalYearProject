# ML Training Pipeline (2026 production)

Machine-learning training pipeline for the URA Tax Assistant: data preparation, quality gating, model fine-tuning, evaluation, and deployment.

## 📁 Directory Structure

```
ml/
├── pipelines/                    # ML pipeline components
│   ├── train.py                  # Main training script
│   ├── evaluate.py               # Model evaluation
│   ├── evaluate_rag.py           # RAG evaluation (faithfulness, relevancy, etc.)
│   ├── quality_gates.py          # Quality checks (classifier/speech/mt/production)
│   ├── calibrate.py              # ECE / Brier / temperature scaling + abstention
│   ├── audit_tokenizer.py        # Tokenizer coverage audit (Luganda fertility)
│   ├── generate_model_card.py    # HF-Hub + EU AI Act model card generator
│   ├── evaluate_safety.py        # Text-mode red-team safety eval (mock/llm/api)
│   ├── evaluate_speech.py        # ASR/TTS evaluation harness
│   ├── evaluate_mt.py            # Machine translation evaluation
│   ├── evaluate_tts.py           # TTS roundtrip intelligibility
│   ├── benchmark_mobile.py       # On-device benchmark orchestrator
│   └── redteam_voice.py          # Voice-mode red-team harness
├── scripts/                      # Utility scripts
│   ├── data_augmentation.py      # Thin CLI → ml.scripts.data_aug package
│   ├── train_quality_classifier.py  # FineWeb-Edu style quality classifier CLI
│   ├── data_aug/                 # 2026 four-stage pipeline package
│   │   ├── schema.py             # Pydantic models, URA system prompt, refusals
│   │   ├── text_utils.py         # NFKC / ftfy / PII redaction
│   │   ├── chunkers.py           # Hierarchical PDF chunking (tables preserved)
│   │   ├── loaders.py            # CSV / PDF / Luganda / teacher / refusals
│   │   ├── dedup.py              # Exact + MinHash LSH + contamination scan
│   │   ├── quality.py            # Length / symbol / repetition / classifier
│   │   ├── quality_classifier.py # Sentence-transformers + sklearn classifier
│   │   ├── splitters.py          # Stratified split with per-stratum seed
│   │   ├── formatters.py         # Messages JSONL / Alpaca / Parquet
│   │   ├── provenance.py         # Manifest + auto-generated data card
│   │   └── pipeline.py           # Four-stage orchestrator
│   ├── teacher_qa_generation.py  # Generate synthetic QA using teacher model
│   ├── fine_tune_gemma.py        # LoRA fine-tuning for Gemma/Llama/T5
│   ├── export_mobile.py          # LoRA merge → GGUF quantise → deploy to mobile
│   ├── benchmark_inference.py    # Desktop proxy for mobile inference benchmark
│   ├── repro.py                  # Reproducibility: seed pinning + env snapshots
│   ├── run_training_pipeline.sh  # Full pipeline orchestrator (shell)
│   ├── run_local_2gpu.sh         # Local 2-GPU launcher
│   ├── prepare_kaggle_notebook.py  # Prepare notebook for Kaggle training
│   ├── export_tpu_ready_data.py  # Build fixed-length packed shards for TPU
│   ├── monitor_kaggle.py         # Monitor Kaggle training jobs
│   └── process_kaggle_output.py  # Process artifacts from Kaggle
└── configs/                      # Training configurations
    ├── training_config.yaml      # All gate thresholds (classifier/speech/mt/production)
    └── accelerate_2gpu.yaml
```

## 🚀 Quick Start

### Prerequisites

```bash
# Core data + training stack
pip install -r requirements.txt

# (already in requirements.txt) the 2026 pipeline needs:
#   pydantic>=2.5   datasketch   pyarrow   ftfy
#   pymupdf4llm     pymupdf      pandera
#   sentence-transformers  scikit-learn  joblib
# and the training stack:
#   transformers   datasets   peft   accelerate   bitsandbytes   trl
```

### Run Full Pipeline (end-to-end)

```bash
# Full training pipeline: classifier → augment → teacher QA → fine-tune
./ml/scripts/run_training_pipeline.sh

# With options
./ml/scripts/run_training_pipeline.sh \
    --target web_high_accuracy \
    --epochs 3 \
    --batch-size 4 \
    --pdf-workers 8 \
    --luganda-cap 5000

# Dry run (validate data pipeline without training)
./ml/scripts/run_training_pipeline.sh --dry-run

# Reuse cached classifier + data
./ml/scripts/run_training_pipeline.sh \
    --skip-classifier \
    --skip-augment \
    --target web_high_accuracy

# Local 2-GPU (RTX A6000 × 2)
./ml/scripts/run_local_2gpu.sh --gpu-ids 0,3 --target web_high_accuracy
```

### Individual Scripts

```bash
# Step 1a: Train quality classifier (bootstrapped from gold CSV FAQs)
python ml/scripts/train_quality_classifier.py \
    --from-csv-dir Data/dataset \
    --save-path artifacts/models/quality_classifier.joblib \
    --max-per-class 500

# Step 1b: Data Augmentation (2026 four-stage pipeline)
python ml/scripts/data_augmentation.py \
    --csv-dir Data/dataset \
    --pdf-dir Data/pdfs \
    --luganda-dir Data/TTT \
    --teacher-qa-dir Data/teacher_qa \
    --output-dir artifacts/training_data \
    --pdf-workers 4 \
    --tokenizer-model google/gemma-2-2b-it \
    --source-cap luganda_parallel=5000 \
    --quality-classifier artifacts/models/quality_classifier.joblib \
    --quality-threshold 0.45

# Step 2: Teacher QA Generation (optional, needs GPU)
python ml/scripts/teacher_qa_generation.py \
    --pdf-dir Data/pdfs \
    --output Data/teacher_qa

# Step 3: Fine-tuning (reads train.messages.jsonl from step 1b)
python ml/scripts/fine_tune_gemma.py \
    --data artifacts/training_data/train.messages.jsonl \
    --target web_high_accuracy \
    --epochs 3
```

## 📊 Training Pipeline

```
┌────────────────────────────────────────────────────────────────────────────┐
│                       TRAINING PIPELINE FLOW (2026)                        │
└────────────────────────────────────────────────────────────────────────────┘

┌─────────────┐  ┌─────────────┐  ┌──────────────┐  ┌───────────┐  ┌────────┐
│  CSV FAQs   │  │  PDF Docs   │  │  Luganda TTT │  │ Teacher QA│  │Refusals│
│Data/dataset │  │ Data/pdfs   │  │  Data/TTT    │  │Data/teach.│  │curated │
└──────┬──────┘  └──────┬──────┘  └──────┬───────┘  └─────┬─────┘  └────┬───┘
       │                │                │                │             │
       │        ┌───────┴────────┐       │                │             │
       │        │ hierarchical   │       │                │             │
       │        │ md chunker     │       │                │             │
       │        │ (tables ok)    │       │                │             │
       │        └───────┬────────┘       │                │             │
       │                │                │                │             │
       └────────────────┴────────────────┴────────────────┴─────────────┘
                                        │
                                        ▼
                 ┌─────────────────────────────────────────────┐
                 │  STAGE 1: ingest + dedup                    │
                 │    • Pydantic schema validation             │
                 │    • NFKC / ftfy normalisation              │
                 │    • PII redaction (TIN/NIN/phone/email)    │
                 │    • Exact hash + MinHash LSH near-dedup    │
                 └─────────────────────┬───────────────────────┘
                                        │
                                        ▼
                 ┌─────────────────────────────────────────────┐
                 │  STAGE 2: quality                           │
                 │    • Tokeniser-aware length (Gemma/Llama)   │
                 │    • Symbol / repetition heuristics         │
                 │    • Per-source caps (e.g. luganda=5000)    │
                 │    • LEARNED CLASSIFIER (FineWeb-Edu style) │
                 │       └─ MiniLM embeddings + sklearn LR     │
                 │       └─ scores stored on every row         │
                 └─────────────────────┬───────────────────────┘
                                        │
                                        ▼
                 ┌─────────────────────────────────────────────┐
                 │  STAGE 3: stratified split                  │
                 │    • by (source_type, language, tag)        │
                 │    • deterministic per-stratum seeding      │
                 │    • contamination scan (train ↔ val/test)  │
                 └─────────────────────┬───────────────────────┘
                                        │
                                        ▼
                 ┌─────────────────────────────────────────────┐
                 │  STAGE 4: format + write                    │
                 │    ┌──────────────────────────────────────┐ │
                 │    │ train.messages.jsonl  (OpenAI fmt)   │ │
                 │    │ val.messages.jsonl    test.*         │ │
                 │    │ train.parquet         val.parquet .. │ │
                 │    │ train.instruction.jsonl  (Alpaca bc) │ │
                 │    │ manifest.json  (SHA + stats + git)   │ │
                 │    │ DATA_CARD.md   (Croissant-compat)    │ │
                 │    └──────────────────────────────────────┘ │
                 └─────────────────────┬───────────────────────┘
                                        │
                                        ▼
                      ┌───────────────────────────┐
                      │    fine_tune_gemma.py     │
                      │  - auto-detects messages  │
                      │  - QLoRA 4-bit quant      │
                      │  - SFTTrainer + chat tpl  │
                      └─────────────┬─────────────┘
                                    │
                                    ▼
                      ┌───────────────────────────┐
                      │    Fine-tuned model       │
                      │    artifacts/ura-gemma    │
                      └───────────────────────────┘
```

## 📋 Scripts Reference

### 1. data_augmentation.py (2026 production pipeline)

Thin CLI over `ml/scripts/data_aug/`, a four-stage pipeline:

```
INGEST  →  NORMALIZE  →  QUALITY  →  FORMAT
  │            │             │          │
  │            │             │          └─ stratified split, messages JSONL,
  │            │             │             Parquet, manifest, data card
  │            │             └─ token-aware length, MinHash LSH dedup,
  │            │                contamination scan, symbol/repetition filters
  │            └─ NFKC, ftfy, PII redaction (TIN/NIN/phone/email/URL)
  └─ CSV FAQs, hierarchical PDF chunks, Luganda (translation), teacher QA,
     curated refusals, retrieval-format examples
```

**Input sources (any subset):**
- `Data/dataset/`  — CSV FAQ files (40+ files with Q&A pairs)
- `Data/pdfs/`     — markdown-aware hierarchical chunking (table-preserving)
- `Data/TTT/`      — Luganda parallel corpus (tagged as translation task)
- `Data/teacher_qa/` — synthetic teacher-generated QA

**Output layout (`--output-dir`, default `artifacts/training_data/`):**

| File | Purpose |
|------|---------|
| `train.messages.jsonl` · `val.messages.jsonl` · `test.messages.jsonl` | Canonical OpenAI-format; TRL-ready |
| `train.parquet` · `val.parquet` · `test.parquet` | HF `datasets`-compatible (zstd) |
| `train.instruction.jsonl` (etc.) | Legacy Alpaca format (back-compat) |
| `training_data.jsonl` · `training_data.messages.jsonl` | Flat legacy paths for `fine_tune_gemma.py` discovery |
| `manifest.json` | Full provenance: input SHAs, stage stats, git commit, config |
| `DATA_CARD.md` | Auto-generated Croissant-compatible data card |

**Quickstart:**

```bash
# Minimal — CSV only
python ml/scripts/data_augmentation.py \
    --csv-dir Data/dataset \
    --output-dir artifacts/training_data

# Full pipeline with PDFs, Luganda, teacher QA, parallel chunking
python ml/scripts/data_augmentation.py \
    --csv-dir Data/dataset \
    --pdf-dir Data/pdfs \
    --luganda-dir Data/TTT \
    --teacher-qa-dir Data/teacher_qa \
    --output-dir artifacts/training_data \
    --pdf-workers 4 \
    --tokenizer-model google/gemma-2-2b-it \
    --max-tokens 2048 \
    --near-dup-threshold 0.85 \
    --source-cap pdf_corpus=15000 \
    --source-cap luganda_parallel=10000

# Skip PDFs (faster)
python ml/scripts/data_augmentation.py \
    --csv-dir Data/dataset \
    --teacher-qa-dir Data/teacher_qa \
    --no-pdf-corpus --no-pdf-retrieval
```

**Key design choices (2026 standards):**

1. **Canonical `messages` column** — OpenAI chat format. No hand-crafted
   chat templates in the dataset; `fine_tune_gemma.py` applies the
   model's template at training time.
2. **System prompt injected per row** — consistent URA persona, auditable
   from the JSONL alone.
3. **PII redaction** — regex-based (TIN/NIN/phone/email/URL/account
   numbers). Every example passes through `redact_pii` before hashing.
4. **MinHash LSH dedup** (`datasketch`) + exact-hash dedup on the full
   `(user, assistant)` pair — not just the question.
5. **Stratified split** by `(source_type, language, tag)` with
   deterministic seed; contamination scan drops any train row that
   leaks into val/test.
6. **Token-aware length** — uses the target tokenizer
   (`--tokenizer-model`) so rows that would silently truncate at training
   time are dropped at data-prep time.
7. **Provenance manifest + Data Card** — records input file SHAs, stage
   stats, git commit + dirty flag, and the exact CLI config. Satisfies
   EU AI Act Art. 10 data-governance records and HF Hub Croissant
   requirements.
8. **Refusal / safety examples** — curated 7-pair set mixed in so the
   model learns to decline out-of-scope and personalised-advice
   requests.
9. **Retrieval-format examples** — PDF chunks also emit as
   `(context → cited answer)` so the model learns the pattern the
   deployed RAG stack uses at serving time.
10. **Parallel PDF extraction** (`--pdf-workers N`) isolates pymupdf
    crashes via `ProcessPoolExecutor`.

### 2. train_quality_classifier.py (FineWeb-Edu style)

Trains a learned quality filter that runs as stage 2 of
`data_augmentation.py`. Architecture mirrors HuggingFace FineWeb-Edu (2024):

```
  user+assistant text  ─▶  sentence-transformers(MiniLM-L6-v2)  ─▶  384-dim
                                                                      │
                                                       ┌──────────────┴────┐
                                                       │ + 7 lexical feats │
                                                       │  (len, unique,    │
                                                       │   digit_ratio,    │
                                                       │   domain hits…)   │
                                                       └──────────┬────────┘
                                                                  │ 391 dims
                                                                  ▼
                                               sklearn LogisticRegression
                                                   (class_weight='balanced')
                                                                  │
                                                                  ▼
                                                   P(high_quality) ∈ [0, 1]
                                                                  │
                                                                  ▼
                                               keep if p ≥ threshold (0.45)
```

**Bootstrap labelling** (no hand annotation required):

- **Positives:** CSV FAQ answers and teacher QA that survived the heuristic
  stage — these are human-curated, so they are high quality by construction.
- **Negatives:** synthetic degradations of positives using 5 modes
  (`truncate_15`, `shuffle_words`, `repeat_bomb`, `strip_answer`,
  `symbol_spam`). The goal is not a perfect classifier, but a filter that
  drops the 5–15% degenerate tail of the distribution.

**Training:**

```bash
# Bootstrap from gold CSV FAQs (recommended first-time training)
python ml/scripts/train_quality_classifier.py \
    --from-csv-dir Data/dataset \
    --save-path artifacts/models/quality_classifier.joblib \
    --max-per-class 500

# Retrain from a previously-augmented dataset (tighter distribution)
python ml/scripts/train_quality_classifier.py \
    --from-jsonl artifacts/training_data/train.messages.jsonl \
    --save-path artifacts/models/quality_classifier.joblib
```

**Outputs:**

- `quality_classifier.joblib` — pickled `{classifier, embed_model, threshold}`
- `quality_classifier.metrics.json` — validation accuracy / f1 / precision / recall

**Integration with data_augmentation.py:**

```bash
python ml/scripts/data_augmentation.py \
    --csv-dir Data/dataset --pdf-dir Data/pdfs \
    --output-dir artifacts/training_data \
    --quality-classifier artifacts/models/quality_classifier.joblib \
    --quality-threshold 0.45
```

When enabled, every scored example receives a `quality_score` field (stored
in the output JSONL and Parquet) and the manifest gains a
`stages.quality.classifier` block with the score distribution (mean, p10,
p50, p90). Rows with scores below the threshold are dropped.

**Why not a full BERT classifier like FineWeb's original?** Dataset is
under 100k rows — a large model would overfit, add wall-time cost per
example, and contribute little beyond what MiniLM + a linear head
already captures. Logistic regression also gives calibrated probabilities
so threshold tuning is a 1-dimensional grid search.

### 3. teacher_qa_generation.py

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

### 4. fine_tune_gemma.py

Fine-tunes Gemma-2-2B (or Llama/T5) using LoRA/QLoRA. Consumes the
messages-format JSONL from `data_augmentation.py` and auto-applies the
right chat template per target via `_messages_to_{gemma,llama,t5}_text`
helpers (matches `tokenizer.apply_chat_template` exactly).

**Model Targets:**
| Target | Model | Use Case | VRAM |
|--------|-------|----------|------|
| `web_high_accuracy` | Gemma-2-2B | Web deployment | 8GB |
| `mobile_gemma_2b`   | Gemma-2-2B | Mobile (GGUF INT4) | 4GB |
| `mobile_offline`    | Llama-3.2-1B | Mobile apps | 4GB |
| `background_t5`     | Flan-T5-Small | Background tasks | 2GB |

```bash
# High accuracy for web (reads train.messages.jsonl)
python ml/scripts/fine_tune_gemma.py \
    --target web_high_accuracy \
    --data artifacts/training_data/train.messages.jsonl \
    --epochs 3

# Mobile-optimised
python ml/scripts/fine_tune_gemma.py \
    --target mobile_offline \
    --data artifacts/training_data/train.messages.jsonl \
    --epochs 5
```

`fine_tune_gemma.py` also auto-discovers the training file if `--data`
is omitted, searching these locations in order:

1. `artifacts/training_data/train.messages.jsonl`   (2026 canonical)
2. `artifacts/training_data/training_data.messages.jsonl`
3. `artifacts/` flat layout (legacy)
4. `Data/` / `Data/teacher_qa/` (historical)

**LoRA Configuration:**
- Rank (r): 16 (adjustable)
- Alpha: 32
- Target modules: q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj
- Quantization: 4-bit NF4 (QLoRA)

### 5. run_training_pipeline.sh

Orchestrates the full four-step pipeline (classifier → augment → teacher QA → fine-tune).

```bash
# Full pipeline with defaults
./ml/scripts/run_training_pipeline.sh

# Skip classifier training (reuse cached at artifacts/models/quality_classifier.joblib)
./ml/scripts/run_training_pipeline.sh --skip-classifier

# Skip teacher QA (faster, no GPU dependency in step 2)
./ml/scripts/run_training_pipeline.sh --skip-teacher

# Custom configuration
./ml/scripts/run_training_pipeline.sh \
    --target mobile_offline \
    --epochs 5 \
    --batch-size 2 \
    --learning-rate 1e-4 \
    --pdf-workers 8 \
    --luganda-cap 5000 \
    --max-pdfs 30
```

Flags:

| Flag | Purpose |
|------|---------|
| `--skip-classifier` | Reuse existing `quality_classifier.joblib` instead of retraining |
| `--skip-augment`    | Skip data augmentation if `train.messages.jsonl` already exists |
| `--skip-teacher`    | Skip synthetic QA generation (no GPU needed downstream) |
| `--target`          | `web_high_accuracy` · `mobile_gemma_2b` · `mobile_offline` · `background_t5` |
| `--dry-run`         | Validate data pipeline only; no fine-tuning |
| `--gpu-ids`         | GPUs to expose via `CUDA_VISIBLE_DEVICES` (e.g. `0,3`) |
| `--num-gpus`        | Number of parallel fine-tune processes via `accelerate launch` |
| `--max-pdfs`        | Cap PDF count for dev runs |
| `--pdf-workers`     | Parallel PDF chunking workers (default: 4) |
| `--luganda-cap`     | Max Luganda translation rows before stratified split (default: 5000) |

## 🔧 CI/CD Integration

### GitHub Actions Workflows

The training scripts are integrated into two workflows. Both consume the
2026 `data_augmentation.py` pipeline and the learned quality classifier.

#### 1. ci-ml-pipeline.yml — jobs relevant to the data pipeline

| Job | Trigger | Purpose |
|-----|---------|---------|
| `lint-and-test` | every push / PR | ruff + pytest (runs `tests/test_data_augmentation.py`, 31 unit tests) |
| `data-aug-smoke` | every PR / push | < 8 min. Imports all modules, runs schema + PII unit checks, runs a CSV-only dry-run, runs a real CSV-only pipeline and validates manifest + required outputs. Fast enough to gate every PR. |
| `speech-smoke` | every PR / push | < 8 min. Import smoke + schema tests + dry-run for ASR/MT/TTS/mobile-export + speech/mt quality gates (soft-fail). |
| `data-validation` | after `lint-and-test` | Existing Great-Expectations-style CSV validation |
| `prepare-training-data` | main / manual | ≤ 30 min. Trains the quality classifier from gold CSVs, runs the full 2026 pipeline with `--max-pdfs 5`, validates manifest + contamination scan, uploads `training-data` + `quality-classifier` artefacts. Exposes `train_rows`, `val_rows`, `test_rows`, `classifier_f1` as job outputs. |
| `train-model` | after `prepare-training-data` | Downloads artefacts to `artifacts/training_data/`, runs `fine_tune_gemma.py --dry-run` against `train.messages.jsonl` to verify the messages column is consumed correctly. |
| `evaluate-rag` | after `evaluate-model` | Runs RAG evaluation on English + Luganda eval sets, uploads results. |
| `production-gates` | after `evaluate-rag` + `validate-mobile-export` | Runs tokenizer audit (proxy mode), synthetic benchmark, model card generation, and `quality_gates.py --family production --soft-fail`. Uploads all artefacts. Gates the deploy stage. |

Key snippet (full pipeline + classifier + validation):

```yaml
- name: Train quality classifier
  run: |
    python ml/scripts/train_quality_classifier.py \
      --from-csv-dir Data/dataset \
      --save-path artifacts/models/quality_classifier.joblib \
      --max-per-class 500

- name: Run 2026 data augmentation
  run: |
    python ml/scripts/data_augmentation.py \
      --csv-dir Data/dataset --pdf-dir Data/pdfs \
      --luganda-dir Data/TTT --teacher-qa-dir Data/teacher_qa \
      --output-dir artifacts/training_data \
      --pdf-workers 2 --max-pdfs 5 \
      --source-cap luganda_parallel=5000 \
      --quality-classifier artifacts/models/quality_classifier.joblib \
      --quality-threshold 0.45 \
      --seed 42

- name: Validate manifest + flag contamination
  run: |
    python -c "
    import json; m = json.load(open('artifacts/training_data/manifest.json'))
    assert m['stages']['split']['contamination_leaked'] == 0
    "
```

Artefact retention:

- `training-data` — 14 days (includes manifest, data card, all JSONL + parquet splits)
- `quality-classifier` — 30 days (joblib + metrics.json)

#### 2. kaggle-training.yml (Remote GPU/TPU Training)

```yaml
jobs:
  resolve-accelerator:
    steps:
      - name: Resolve accelerator
        run: echo "gpu|tpu"

  run-data-ingestion:
    steps:
      - name: Run data ingestion + optional EDA
        run: python Notebooks/DataIngestion_Augmentation.ipynb

  export-tpu-ready:
    if: ${{ inputs.accelerator == 'tpu' }}
    steps:
      - name: Export fixed-length packed shards
        run: python ml/scripts/export_tpu_ready_data.py

  prepare:
    steps:
      - name: Prepare notebook
        run: python ml/scripts/prepare_kaggle_notebook.py

  monitor:
    steps:
      - name: Monitor training
        run: python ml/scripts/monitor_kaggle.py

  process:
    steps:
      - name: Process output
        run: python ml/scripts/process_kaggle_output.py
```

### Unit test coverage

**105 tests across 3 files, ~16 seconds total runtime.**

```bash
# Fast tests only (< 5s) — runs everywhere including CI
pytest tests/ -m "not slow"

# Everything including model-loading integration (downloads ~1MB tiny model)
pytest tests/ --basetemp=/tmp/pytest-yourname
```

**`tests/test_data_augmentation.py` (31 tests)**

- **Schema** — Pydantic rejects empty/whitespace content, missing assistant / user turns, extra fields; content hash normalisation is deterministic; `to_row()` flattens metadata correctly.
- **Text utilities** — whitespace collapse, letter-spaced recovery ("T A X → TAX"), PII redaction for phone/email/TIN/URL, repetition and symbol ratios.
- **Loaders** — CSV loader drops short rows and injects a system turn; curated refusal loader returns the safety set.
- **Dedup** — exact dedup is normalised and deterministic; full `dedup_all` round-trip.
- **Quality filter** — drops short responses, writes token counts, enforces per-source caps.
- **Splitter** — deterministic across runs with the same seed, small strata stay in train, no contamination leak.
- **Formatters** — messages JSONL round-trip, legacy Alpaca JSONL round-trip.
- **End-to-end pipeline** — full run with 24 synthetic rows produces valid splits, manifest, data card, and messages JSONL with a trailing assistant turn on every row.

**`tests/test_fine_tune_gemma.py` (52 tests, 4 env-skipped)**

- **Helpers** — `_digest_file`, `find_sibling_splits` (4 cases), `find_dataset_manifest`, `_try_flash_attention_2`, `load_jsonl`, `find_training_data` search order
- **Messages → Gemma chat template** — system folding, model/assistant alias, multi-turn, empty content
- **Messages → Llama chat template** — system kept as proper role, EOT after each turn, unknown-role fallback
- **`format_for_gemma` / `format_for_llama` / `format_for_t5`** — every branch (messages priority, Alpaca, Q&A, prompt/completion, empty fallback) × 3 model families
- **CLI parser smoke** — every 2026 flag (`--use-rslora`, `--use-dora`, `--neftune-alpha`, `--seed`, `--report-to`, `--push-to-hub`, `--save-merged`, `--resume-from-checkpoint`, …) appears in `--help`
- **`SFTConfig` kwargs compatibility** — reproduces the exact dict `train()` builds and asserts TRL's `SFTConfig(**)` accepts every key. Also asserts `SFTTrainer.__init__` exposes `processing_class` and **does NOT** still accept the deprecated `tokenizer=` / `dataset_text_field=` / `max_seq_length=` / `packing=` kwargs. This is the regression guard for the TRL 1.0 migration.
- **Train signature** — `train()` has all required positional + 11 new 2026 kwargs; `apply_lora` exposes `use_rslora` / `use_dora`; `setup_model_and_tokenizer` exposes `distributed`
- **Slow integration (gated on PEFT JIT)** — loads `hf-internal-testing/tiny-random-LlamaForCausalLM` (~1MB) and verifies pad token, padding side, max seq length, distributed device_map suppression, real LoRA wrapping with RSLoRA/DoRA flags, and full `train()` construction with monkeypatched `SFTTrainer.train`

**`tests/test_export_mobile.py` (26 tests)**

- **Constants** — pipeline / schema versions set; `QUANT_TYPES` registry includes IQ-series (IQ4_NL, IQ4_XS, IQ3_M, IQ3_S, IQ2_M); `QUANTS_REQUIRING_IMATRIX` set; `DEFAULT_MOBILE_FILENAME` matches the Flutter config; mobile asset paths resolve under `MobileApp/ura_chatbot/`
- **Tool discovery** — `LlamaCppTools` is returned even when nothing is found; env-var override + binary detection works against a fake llama.cpp dir
- **Adapter discovery** — auto-discovers latest fine-tune output by mtime; accepts both LoRA + full-model layouts; reads `training_config.json` from the parent dir for full lineage; gracefully handles missing lineage
- **Hashing + atomic copy** — SHA-256 deterministic; `_atomic_copy` writes via `.tmp` then renames, verifies post-copy SHA, creates parent dirs, leaves no leftover `.tmp` on success
- **Manifest + model card** — manifest includes pipeline version, base model, quant, sha256, full deployment block (Android `noCompress`, iOS `MediaPipeTasksGenAI`, Flutter channel); model card renders lineage from `training_config.json` (LoRA r/α, RSLoRA, training git commit, dataset SHA); handles missing lineage gracefully
- **Deploy** — atomically copies into Android assets dir + iOS staging dir, verifies post-copy SHA matches source; gracefully skips a missing platform; `--no-android` / `--no-ios` flags honoured
- **CLI smoke** — every 2026 flag (`--imatrix`, `--imatrix-source`, `--no-deploy`, `--no-android`, `--keep-merged`, …) and every quant type appears in `--help`; dry-run with no adapter exits 2; dry-run with a fake adapter completes cleanly

### Manual Workflow Trigger

```bash
# Trigger Kaggle training
gh workflow run kaggle-training.yml

# With explicit TPU parameters
gh workflow run kaggle-training.yml \
    -f notebook=ura-training \
    -f accelerator=tpu \
    -f run_data_eda=false

# With explicit GPU parameters
gh workflow run kaggle-training.yml \
    -f notebook=ura-training \
    -f accelerator=gpu
```

## 📈 Evaluation & Production Gates

### Classical evaluation

```bash
# Run evaluation
python ml/pipelines/evaluate.py \
    --model artifacts/ura-gemma-finetuned/final \
    --test-data artifacts/test_data.jsonl

# Classifier quality gates (accuracy/f1/precision/recall/latency)
python ml/pipelines/quality_gates.py \
    --metrics Results/metrics/evaluation_metrics.json
```

### 2026 Production gate pipeline

The production family aggregates multiple artefact files into a single
release-readiness check. Each gate has a severity (`blocking` or `advisory`);
advisory gates are recorded but do not fail the release, letting new checks
ramp safely.

```bash
# 1. Calibration — ECE, Brier, temperature scaling, abstention threshold
python -m ml.pipelines.calibrate \
    --input Results/confidence_scores.jsonl \
    --output-dir Results/calibration

# 2. Tokenizer audit — Luganda fertility ratio vs English
python -m ml.pipelines.audit_tokenizer \
    --tokenizer google/gemma-2-2b-it \
    --en Data/eval/rag_eval.jsonl \
    --lg Data/eval/rag_eval_lg.jsonl \
    --output-dir Results/tokenizer_audit

# 3. Inference benchmark (desktop proxy; use --synthetic in CI)
python -m ml.scripts.benchmark_inference \
    --model-path artifacts/mobile/ura-gemma-2b-q4_k_m.gguf \
    --output-dir Results/benchmark
# CI mode (no model needed):
python -m ml.scripts.benchmark_inference --synthetic --output-dir Results/benchmark

# 4. Model card generation (EU AI Act Art. 10/13 compliant)
python -m ml.pipelines.generate_model_card \
    --output Results/MODEL_CARD.md \
    --mobile-manifest artifacts/mobile/mobile_manifest.json \
    --rag-eval Results/rag_evaluation_results.json \
    --safety Results/safety_evaluation_results.json \
    --calibration Results/calibration/calibration_report.json \
    --tokenizer-audit Results/tokenizer_audit/tokenizer_audit.json \
    --benchmark Results/benchmark/benchmark.json

# 5. Combined production quality gate
python -m ml.pipelines.quality_gates \
    --family production \
    --config ml/configs/training_config.yaml \
    --rag-eval Results/rag_evaluation_results.json \
    --calibration Results/calibration/calibration_report.json \
    --tokenizer-audit Results/tokenizer_audit/tokenizer_audit.json \
    --benchmark Results/benchmark/benchmark.json \
    --mobile-manifest artifacts/mobile/mobile_manifest.json \
    --model-card Results/MODEL_CARD.md \
    --safety Results/safety_evaluation_results.json
```

### Production gate thresholds

Configured in `ml/configs/training_config.yaml` under `production_gates`:

| Gate | Metric | Threshold | Severity |
|------|--------|-----------|----------|
| RAG faithfulness | `faithfulness.mean` | >= 0.60 | blocking |
| RAG answer relevancy | `answer_relevancy.mean` | >= 0.70 | blocking |
| Calibration ECE | `summary.ece` | <= 0.10 | blocking |
| Calibration Brier | `summary.brier` | <= 0.25 | blocking |
| Safety refusal rate | `refusal_rate` | >= 0.90 | blocking |
| Tokenizer fertility | `fertility_ratio_lg_over_en` | <= 1.80 | blocking |
| Mobile bundle size | `size_mb` | <= 1800 | blocking |
| Mobile SHA-256 | present | == true | blocking |
| Model card sections | all 13 present | == true | blocking |
| Benchmark tokens/sec | `tokens_per_sec.mean` | >= 8.0 | advisory* |
| Benchmark TTFT p95 | `ttft_ms.p95` | <= 1500 ms | advisory* |
| Benchmark peak RSS | `peak_rss_mb` | <= 2200 | advisory* |
| Per-language floors | `lg.faithfulness` etc. | per-lang | blocking |

\* Advisory when `--synthetic`; promoted to blocking with real benchmark data.

### Reproducibility

Every pipeline embeds an environment snapshot via `ml/scripts/repro.py`:

```bash
# Pin all seeds (Python, NumPy, PyTorch, transformers) + capture env
python -c "from ml.scripts.repro import set_global_seed, env_snapshot; set_global_seed(42); print(env_snapshot('demo'))"
```

Snapshots include git SHA, branch, dirty flag, platform, CUDA status, and
package versions. Written alongside every artefact for full audit trail.

## 🌐 Deployment

### Push to HuggingFace

```bash
# Login
hf auth login

# Upload model
hf upload \
    your-username/ura-tax-assistant-gemma \
    artifacts/ura-gemma-finetuned/final
```

### Export for Mobile (2026 GGUF + MediaPipe LLM Inference)

The 2026 mobile export pipeline takes a fine-tuned LoRA adapter and produces
a quantised GGUF model that runs on-device via the MediaPipe LLM Inference
API on both Android and iOS. The output is automatically deployed into the
Flutter app's asset directories with post-copy SHA-256 verification.

**Pipeline stages** (`ml/scripts/export_mobile.py`):

```
adapter discovery          → auto-finds artifacts/ura-*/final by mtime
   ↓
LoRA merge                 → PEFT merge_and_unload → base FP16 weights
   ↓
GGUF F16 conversion        → llama.cpp/convert_hf_to_gguf.py
   ↓
imatrix calibration (opt)  → llama-imatrix on training data → importance matrix
   ↓
quantisation               → llama-quantize → Q4_K_M / IQ4_NL / etc.
   ↓
validation                 → llama-cpp-python load test (best effort)
   ↓
manifest + model card      → mobile_manifest.json + MODEL_CARD.md with full lineage
   ↓
atomic deploy              → MobileApp/ura_chatbot/{android,ios}/...
                             with post-copy SHA-256 verification
```

**Quickstart:**

```bash
# Auto-discover latest adapter, deploy to MobileApp/
python ml/scripts/export_mobile.py

# Specific adapter
python ml/scripts/export_mobile.py \
    --adapter artifacts/ura-gemma-2-2b-it-20260411_193000/final

# Sub-1.5GB mobile build with imatrix calibration
python ml/scripts/export_mobile.py --quant IQ4_NL --imatrix

# Dry run (validate adapter + tools without converting)
python ml/scripts/export_mobile.py --dry-run

# Skip deployment (e.g. CI artifact upload only)
python ml/scripts/export_mobile.py --no-deploy
```

**Quantisation options** (size estimates for Gemma-2-2B):

| Quant | Size | Notes |
|-------|------|-------|
| `Q4_K_M` | ~1.6 GB | **Default** — best balance for mobile |
| `Q4_K_S` | ~1.4 GB | Smaller, slight quality loss |
| `Q5_K_M` | ~1.9 GB | Higher quality |
| `Q6_K` | ~2.2 GB | Near-lossless |
| `Q8_0` | ~2.8 GB | Highest quality short of F16 |
| `IQ4_NL` | ~1.5 GB | Better than Q4_K_M *with* imatrix |
| `IQ4_XS` | ~1.4 GB | Smaller IQ-series 4-bit |
| `IQ3_M` | ~1.2 GB | ⚠ requires `--imatrix` for usable quality |
| `IQ3_S` | ~1.1 GB | ⚠ requires `--imatrix` |
| `IQ2_M` | ~0.9 GB | ⚠ extreme — very small phones only, requires `--imatrix` |
| `F16` | ~5.2 GB | Dev/eval only |

**Imatrix calibration** uses the project's own training data
(`artifacts/training_data/train.messages.jsonl`) by default to compute a
per-tensor importance matrix. This is the 2024-2025 best practice for
sub-3-bit quantisation — the difference between a usable IQ3_M model and
gibberish.

**Outputs:**

```
artifacts/mobile/
├── ura-gemma-2b-q4_k_m.gguf       1.6 GB  ← quantised mobile model
├── mobile_manifest.json            ← pipeline version, sha256, lineage, deploy paths
├── MODEL_CARD.md                   ← human-readable card (HF Hub ready)
└── (cleaned up by default: merged_model/, model-f16.gguf, imatrix.dat, calibration.txt)

MobileApp/ura_chatbot/
├── android/app/src/main/assets/models/ura-gemma-2b-q4_k_m.gguf  ← Android (atomic copy)
└── ios/Runner/models/ura-gemma-2b-q4_k_m.gguf                   ← iOS staging (must be added to Xcode once)
```

**Lineage in `MODEL_CARD.md`** — pulled from the fine-tune's
`training_config.json` and the data pipeline's `manifest.json`:

- Base model + LoRA r/α/dropout, RSLoRA/DoRA flags
- Training git commit + dataset SHA-256 + pipeline version
- Effective batch size, learning rate, epochs, seed
- Validation result (test prompt + model output)
- Deployment instructions for Android (`noCompress`), iOS (Xcode bundle), Flutter

**Requirements:**

```bash
pip install transformers peft torch gguf
# llama.cpp built with quantize + imatrix targets:
git clone https://github.com/ggerganov/llama.cpp ~/llama.cpp
cmake -B ~/llama.cpp/build -S ~/llama.cpp
cmake --build ~/llama.cpp/build --target llama-quantize llama-imatrix
# OR set LLAMA_CPP_DIR to an existing checkout
export LLAMA_CPP_DIR=/path/to/llama.cpp
```

**Mobile-side setup** (one-time, already done in this repo):

- **Android:** `MobileApp/ura_chatbot/android/app/build.gradle.kts` declares
  `androidResources { noCompress += listOf("gguf") }` (mandatory — without
  this the GGUF file is APK-compressed and `mmap` fails at runtime) plus
  `implementation("com.google.mediapipe:tasks-genai:0.10.22")` and
  `minSdk = 24`.
- **iOS:** `MobileApp/ura_chatbot/ios/Podfile` declares
  `pod 'MediaPipeTasksGenAI', '~> 0.10.22'`.
- **Flutter:** `lib/core/inference/on_device_llm.dart` connects via
  `MethodChannel('com.ura_chatbot/llm_inference')` and looks for
  `models/ura-gemma-2b-q4_k_m.gguf`.

**iOS one-time step:** after the first export, open
`MobileApp/ura_chatbot/ios/Runner.xcworkspace`, drag the GGUF from
`Runner/models/` into the project navigator (target: Runner). Subsequent
re-exports replace the file in place — no Xcode action needed.

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
# Full local training
./ml/scripts/run_training_pipeline.sh --target web_high_accuracy

# Trigger CI pipeline with training
gh workflow run ci-ml-pipeline.yml -f run_training=true

# Trigger Kaggle training (manual dispatch default accelerator is TPU)
gh workflow run kaggle-training.yml -f notebook=ura-training

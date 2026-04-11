#!/bin/bash
# =============================================================================
# URA Tax Assistant - Full Training Pipeline (2026 production)
# =============================================================================
# This script orchestrates the complete training workflow:
#   1. Quality classifier training (FineWeb-Edu style, bootstrapped from CSV FAQs)
#   2. Data augmentation (four-stage pipeline: ingest → normalize → quality → format)
#   3. Teacher QA generation (synthetic data, optional)
#   4. Gemma/Llama fine-tuning (LoRA / QLoRA)
#
# Usage:
#   ./ml/scripts/run_training_pipeline.sh [OPTIONS]
#
# Options:
#   --skip-classifier  Skip quality classifier training (reuse existing)
#   --skip-augment     Skip data augmentation step
#   --skip-teacher     Skip teacher QA generation
#   --target TARGET    Model target (web_high_accuracy, mobile_offline, background_t5)
#   --dry-run          Validate without training
#   --gpu-ids IDS      Comma-separated GPU IDs (e.g., "1,2")
#   --num-gpus N       Number of GPUs for distributed training (default: 1)
#   --max-pdfs N       Process at most N PDFs (default: all)
#   --pdf-workers N    Parallel PDF workers (default: 4)
#   --luganda-cap N    Max Luganda translation rows (default: 5000)
#   --help             Show this help message
# =============================================================================

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Defaults
SKIP_CLASSIFIER=false
SKIP_AUGMENT=false
SKIP_TEACHER=false
TARGET="web_high_accuracy"
DRY_RUN=false
EPOCHS=3
BATCH_SIZE=4
LEARNING_RATE="2e-4"
GPU_IDS=""
NUM_GPUS=1
MAX_PDFS=""
PDF_WORKERS=4
LUGANDA_CAP=5000

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-classifier)
            SKIP_CLASSIFIER=true
            shift
            ;;
        --skip-augment)
            SKIP_AUGMENT=true
            shift
            ;;
        --skip-teacher)
            SKIP_TEACHER=true
            shift
            ;;
        --target)
            TARGET="$2"
            shift 2
            ;;
        --epochs)
            EPOCHS="$2"
            shift 2
            ;;
        --batch-size)
            BATCH_SIZE="$2"
            shift 2
            ;;
        --learning-rate)
            LEARNING_RATE="$2"
            shift 2
            ;;
        --gpu-ids)
            GPU_IDS="$2"
            shift 2
            ;;
        --num-gpus)
            NUM_GPUS="$2"
            shift 2
            ;;
        --max-pdfs)
            MAX_PDFS="$2"
            shift 2
            ;;
        --pdf-workers)
            PDF_WORKERS="$2"
            shift 2
            ;;
        --luganda-cap)
            LUGANDA_CAP="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help)
            head -30 "$0" | tail -25
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# Get project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

# Create directories
ARTIFACTS_DIR="$PROJECT_ROOT/artifacts"
DATA_DIR="$PROJECT_ROOT/Data"
RESULTS_DIR="$PROJECT_ROOT/Results"
TRAINING_DATA_DIR="$ARTIFACTS_DIR/training_data"
MODELS_DIR="$ARTIFACTS_DIR/models"
CLASSIFIER_PATH="$MODELS_DIR/quality_classifier.joblib"

mkdir -p "$ARTIFACTS_DIR"
mkdir -p "$TRAINING_DATA_DIR"
mkdir -p "$MODELS_DIR"
mkdir -p "$DATA_DIR/processed"
mkdir -p "$RESULTS_DIR/metrics"
mkdir -p "$RESULTS_DIR/plots"
mkdir -p "$RESULTS_DIR/reports"

# =============================================================================
# Header
# =============================================================================
echo ""
echo "======================================================================"
echo -e "${BLUE}URA TAX ASSISTANT - TRAINING PIPELINE${NC}"
echo "======================================================================"
echo ""
echo -e "Target:      ${GREEN}$TARGET${NC}"
echo -e "Epochs:      ${GREEN}$EPOCHS${NC}"
echo -e "Batch size:  ${GREEN}$BATCH_SIZE${NC}"
echo -e "Dry run:     ${GREEN}$DRY_RUN${NC}"
echo -e "GPU IDs:     ${GREEN}${GPU_IDS:-auto}${NC}"
echo -e "Num GPUs:    ${GREEN}$NUM_GPUS${NC}"
echo ""

# Set CUDA_VISIBLE_DEVICES if gpu-ids specified
if [ -n "$GPU_IDS" ]; then
    export CUDA_VISIBLE_DEVICES="$GPU_IDS"
    echo -e "${BLUE}Using GPUs: $GPU_IDS${NC}"
fi

# =============================================================================
# Step 1a: Quality Classifier (FineWeb-Edu style, bootstrapped from CSVs)
# =============================================================================
# The classifier is trained once from the gold CSV FAQs and reused in
# step 1b to drop degenerate rows. Skipping is safe; the rest of the
# pipeline falls back to heuristic filters only.
echo "======================================================================"
echo -e "${YELLOW}STEP 1a: Quality Classifier Training${NC}"
echo "======================================================================"

if [ "$SKIP_CLASSIFIER" = true ]; then
    echo -e "${BLUE}Skipping classifier training (--skip-classifier)${NC}"
    CLASSIFIER_FLAGS=""
elif [ -f "$CLASSIFIER_PATH" ]; then
    echo -e "${BLUE}Classifier already exists, reusing:${NC} $CLASSIFIER_PATH"
    if [ -f "${CLASSIFIER_PATH%.joblib}.metrics.json" ]; then
        python3 -c "
import json
m = json.load(open('${CLASSIFIER_PATH%.joblib}.metrics.json'))
print(f\"   f1={m['f1']:.3f} accuracy={m['accuracy']:.3f} precision={m['precision']:.3f} recall={m['recall']:.3f}\")
"
    fi
    CLASSIFIER_FLAGS="--quality-classifier $CLASSIFIER_PATH --quality-threshold 0.45"
else
    echo "Training quality classifier from CSV FAQs..."
    python3 ml/scripts/train_quality_classifier.py \
        --from-csv-dir "$DATA_DIR/dataset" \
        --save-path "$CLASSIFIER_PATH" \
        --max-per-class 500 \
        -v
    if [ -f "$CLASSIFIER_PATH" ]; then
        CLASSIFIER_FLAGS="--quality-classifier $CLASSIFIER_PATH --quality-threshold 0.45"
        echo -e "${GREEN}✓ Classifier saved:${NC} $CLASSIFIER_PATH"
    else
        echo -e "${YELLOW}⚠ Classifier training failed, continuing with heuristics only${NC}"
        CLASSIFIER_FLAGS=""
    fi
fi

echo ""

# =============================================================================
# Step 1b: Data Augmentation (2026 four-stage pipeline)
# =============================================================================
echo "======================================================================"
echo -e "${YELLOW}STEP 1b: Data Augmentation${NC}"
echo "======================================================================"

if [ "$SKIP_AUGMENT" = true ]; then
    echo -e "${BLUE}Skipping data augmentation (--skip-augment)${NC}"
else
    if [ -f "$TRAINING_DATA_DIR/train.messages.jsonl" ]; then
        echo -e "${BLUE}Training data already exists. Remove $TRAINING_DATA_DIR to regenerate.${NC}"
        wc -l "$TRAINING_DATA_DIR/train.messages.jsonl" 2>/dev/null | awk '{print "   train rows: " $1}'
        wc -l "$TRAINING_DATA_DIR/val.messages.jsonl" 2>/dev/null | awk '{print "   val rows:   " $1}'
        wc -l "$TRAINING_DATA_DIR/test.messages.jsonl" 2>/dev/null | awk '{print "   test rows:  " $1}'
    else
        echo "Running 2026 data augmentation pipeline..."

        # Tokeniser-aware length filter — choose based on target. Falling
        # back to the empty string uses the whitespace heuristic (no HF
        # download). For web_high_accuracy we want the real Gemma tokenizer.
        case "$TARGET" in
            web_high_accuracy|mobile_gemma_2b)
                TOKENIZER="google/gemma-2-2b-it"
                ;;
            mobile_offline)
                TOKENIZER="meta-llama/Llama-3.2-1B-Instruct"
                ;;
            background_t5)
                TOKENIZER="google/flan-t5-small"
                ;;
            *)
                TOKENIZER="google/gemma-2-2b-it"
                ;;
        esac

        AUG_ARGS=(
            ml/scripts/data_augmentation.py
            --csv-dir "$DATA_DIR/dataset"
            --pdf-dir "$DATA_DIR/pdfs"
            --luganda-dir "$DATA_DIR/TTT"
            --teacher-qa-dir "$DATA_DIR/teacher_qa"
            --output-dir "$TRAINING_DATA_DIR"
            --pdf-workers "$PDF_WORKERS"
            --tokenizer-model "$TOKENIZER"
            --min-tokens 8 --max-tokens 2048
            --near-dup-threshold 0.85
            --source-cap "luganda_parallel=$LUGANDA_CAP"
            --source-cap "pdf_corpus=15000"
            --source-cap "retrieval=15000"
            --val-ratio 0.08 --test-ratio 0.02
            --seed 42
            -v
        )

        if [ -n "$MAX_PDFS" ]; then
            AUG_ARGS+=(--max-pdfs "$MAX_PDFS")
        fi

        if [ -n "$CLASSIFIER_FLAGS" ]; then
            # shellcheck disable=SC2086
            AUG_ARGS+=($CLASSIFIER_FLAGS)
        fi

        python3 "${AUG_ARGS[@]}"

        # Expose a back-compat flat path for downstream step 2 / 3 that may
        # still reference $ARTIFACTS_DIR/training_data.jsonl directly.
        if [ -f "$TRAINING_DATA_DIR/training_data.jsonl" ]; then
            cp "$TRAINING_DATA_DIR/training_data.jsonl" "$ARTIFACTS_DIR/training_data.jsonl"
        fi

        echo -e "${GREEN}✓ Data augmentation complete${NC}"
        echo "   Manifest: $TRAINING_DATA_DIR/manifest.json"
        echo "   Data card: $TRAINING_DATA_DIR/DATA_CARD.md"
    fi
fi

echo ""

# =============================================================================
# Step 2: Teacher QA Generation (Optional)
# =============================================================================
echo "======================================================================"
echo -e "${YELLOW}STEP 2: Teacher QA Generation${NC}"
echo "======================================================================"

if [ "$SKIP_TEACHER" = true ]; then
    echo -e "${BLUE}Skipping teacher QA generation (--skip-teacher)${NC}"
    SYNTHETIC_FLAG=""
else
    if [ -f "$ARTIFACTS_DIR/teacher_qa_gemma.jsonl" ]; then
        echo -e "${BLUE}Teacher QA data already exists.${NC}"
        echo "   File: $ARTIFACTS_DIR/teacher_qa_gemma.jsonl"
        wc -l "$ARTIFACTS_DIR/teacher_qa_gemma.jsonl" 2>/dev/null | awk '{print "   Lines: " $1}'
        SYNTHETIC_FLAG="--synthetic $ARTIFACTS_DIR/teacher_qa_gemma.jsonl"
    else
        echo "Generating synthetic QA data using teacher model..."
        echo -e "${YELLOW}Note: This requires a GPU and ~8GB VRAM${NC}"
        
        # Check if we can run the teacher (needs GPU)
        if python3 -c "import torch; exit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
            TEACHER_ARGS=(
                ml/scripts/teacher_qa_generation.py
                --pdf-dir Data/pdfs
                --output "$ARTIFACTS_DIR/teacher_qa"
            )
            # Pass GPU selection if specified
            if [ -n "$GPU_IDS" ]; then
                TEACHER_ARGS+=(--gpu-ids "$GPU_IDS")
            fi

            python3 "${TEACHER_ARGS[@]}"

            # Verify output was created
            if [ -f "$ARTIFACTS_DIR/teacher_qa_gemma.jsonl" ]; then
                SYNTHETIC_FLAG="--synthetic $ARTIFACTS_DIR/teacher_qa_gemma.jsonl"
                echo -e "${GREEN}✓ Teacher QA generation complete${NC}"
            else
                echo -e "${YELLOW}⚠ Teacher QA generation produced no output, continuing without synthetic data${NC}"
                SYNTHETIC_FLAG=""
            fi
        else
            echo -e "${YELLOW}⚠ No GPU available, skipping teacher QA generation${NC}"
            SYNTHETIC_FLAG=""
        fi
    fi
fi

echo ""

# =============================================================================
# Step 3: Model Fine-tuning
# =============================================================================
echo "======================================================================"
echo -e "${YELLOW}STEP 3: Model Fine-tuning${NC}"
echo "======================================================================"

OUTPUT_DIR="$ARTIFACTS_DIR/ura-${TARGET}-finetuned"

# Use the canonical messages-format JSONL emitted by the 2026 pipeline.
# fine_tune_gemma auto-detects the ``messages`` column and applies the
# appropriate chat template per target.
if [ -f "$TRAINING_DATA_DIR/train.messages.jsonl" ]; then
    TRAIN_DATA="$TRAINING_DATA_DIR/train.messages.jsonl"
elif [ -f "$ARTIFACTS_DIR/training_data.jsonl" ]; then
    TRAIN_DATA="$ARTIFACTS_DIR/training_data.jsonl"
else
    echo -e "${RED}No training data found. Re-run without --skip-augment.${NC}"
    exit 1
fi

if [ "$DRY_RUN" = true ]; then
    echo "Running dry run (data validation only)..."
    echo "   Data: $TRAIN_DATA"
    python3 ml/scripts/fine_tune_gemma.py \
        --data "$TRAIN_DATA" \
        $SYNTHETIC_FLAG \
        --target "$TARGET" \
        --output "$OUTPUT_DIR" \
        --dry-run

    echo -e "${GREEN}✓ Dry run complete${NC}"
else
    echo "Starting fine-tuning..."
    echo "   Target: $TARGET"
    echo "   Data:   $TRAIN_DATA"
    echo "   Output: $OUTPUT_DIR"
    echo "   GPUs:   ${GPU_IDS:-auto} (${NUM_GPUS} process(es))"
    echo ""

    # Build common args
    TRAIN_ARGS=(
        ml/scripts/fine_tune_gemma.py
        --data "$TRAIN_DATA"
        $SYNTHETIC_FLAG
        --target "$TARGET"
        --output "$OUTPUT_DIR"
        --epochs "$EPOCHS"
        --batch-size "$BATCH_SIZE"
        --learning-rate "$LEARNING_RATE"
    )

    # Add gpu-ids flag for single-process GPU selection
    if [ -n "$GPU_IDS" ] && [ "$NUM_GPUS" -le 1 ]; then
        TRAIN_ARGS+=(--gpu-ids "$GPU_IDS")
    fi

    if [ "$NUM_GPUS" -gt 1 ]; then
        # Multi-GPU: use accelerate launch for distributed data-parallel
        ACCEL_CONFIG="$PROJECT_ROOT/ml/configs/accelerate_2gpu.yaml"
        echo -e "${BLUE}Launching with accelerate (${NUM_GPUS} GPUs)...${NC}"

        accelerate launch \
            --config_file "$ACCEL_CONFIG" \
            --num_processes "$NUM_GPUS" \
            "${TRAIN_ARGS[@]}"
    else
        # Single GPU or CPU
        python "${TRAIN_ARGS[@]}"
    fi
    
    echo -e "${GREEN}✓ Fine-tuning complete${NC}"
    
    # Copy results
    if [ -f "$OUTPUT_DIR/training_metrics.json" ]; then
        cp "$OUTPUT_DIR/training_metrics.json" "$RESULTS_DIR/metrics/"
        echo "   Metrics copied to: $RESULTS_DIR/metrics/"
    fi
fi

echo ""

# =============================================================================
# Summary
# =============================================================================
echo "======================================================================"
echo -e "${GREEN}PIPELINE COMPLETE${NC}"
echo "======================================================================"
echo ""
echo "Artifacts:"
echo "   Training data:   $TRAINING_DATA_DIR/ (messages + parquet + manifest + data card)"
[ -f "$CLASSIFIER_PATH" ] && echo "   Quality classifier: $CLASSIFIER_PATH"
[ -f "$ARTIFACTS_DIR/teacher_qa_gemma.jsonl" ] && echo "   Synthetic QA:    $ARTIFACTS_DIR/teacher_qa_gemma.jsonl"
[ -d "$OUTPUT_DIR/final" ] && echo "   Model:           $OUTPUT_DIR/final/"
echo ""
echo "Next steps:"
echo "   1. Inspect data card:    cat $TRAINING_DATA_DIR/DATA_CARD.md"
echo "   2. Push model:           huggingface-cli upload <repo> $OUTPUT_DIR/final"
echo "   3. Evaluate:             python3 ml/pipelines/evaluate.py --model $OUTPUT_DIR/final"
echo "   4. Convert for mobile:   python3 ml/scripts/export_mobile.py"
echo ""

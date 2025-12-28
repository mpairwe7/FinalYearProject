#!/bin/bash
# =============================================================================
# URA Tax Assistant - Full Training Pipeline
# =============================================================================
# This script orchestrates the complete training workflow:
#   1. Data augmentation (combine sources)
#   2. Teacher QA generation (synthetic data)
#   3. Gemma/Llama fine-tuning (LoRA)
#
# Usage:
#   ./ml/scripts/run_training_pipeline.sh [OPTIONS]
#
# Options:
#   --skip-augment    Skip data augmentation step
#   --skip-teacher    Skip teacher QA generation
#   --target TARGET   Model target (web_high_accuracy, mobile_offline, background_t5)
#   --dry-run         Validate without training
#   --help            Show this help message
# =============================================================================

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Defaults
SKIP_AUGMENT=false
SKIP_TEACHER=false
TARGET="web_high_accuracy"
DRY_RUN=false
EPOCHS=3
BATCH_SIZE=4
LEARNING_RATE="2e-4"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
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
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help)
            head -25 "$0" | tail -20
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

mkdir -p "$ARTIFACTS_DIR"
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
echo ""

# =============================================================================
# Step 1: Data Augmentation
# =============================================================================
echo "======================================================================"
echo -e "${YELLOW}STEP 1: Data Augmentation${NC}"
echo "======================================================================"

if [ "$SKIP_AUGMENT" = true ]; then
    echo -e "${BLUE}Skipping data augmentation (--skip-augment)${NC}"
else
    if [ -f "$ARTIFACTS_DIR/training_data.jsonl" ]; then
        echo -e "${BLUE}Training data already exists. Use --skip-augment=false to regenerate.${NC}"
        echo "   File: $ARTIFACTS_DIR/training_data.jsonl"
        wc -l "$ARTIFACTS_DIR/training_data.jsonl" 2>/dev/null | awk '{print "   Lines: " $1}'
    else
        echo "Running data augmentation..."
        python ml/scripts/data_augmentation.py \
            --csv-dir datasets \
            --pdf-dir pdfs \
            --luganda-dir TTT \
            --output "$ARTIFACTS_DIR/training_data.jsonl" \
            --gemma-output "$ARTIFACTS_DIR/gemma_format.jsonl" \
            --instruction-output "$ARTIFACTS_DIR/instruction_format.jsonl"
        
        echo -e "${GREEN}✓ Data augmentation complete${NC}"
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
        if python -c "import torch; exit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
            python ml/scripts/teacher_qa_generation.py \
                --pdf-dir pdfs \
                --output "$ARTIFACTS_DIR/teacher_qa"
            
            SYNTHETIC_FLAG="--synthetic $ARTIFACTS_DIR/teacher_qa_gemma.jsonl"
            echo -e "${GREEN}✓ Teacher QA generation complete${NC}"
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

if [ "$DRY_RUN" = true ]; then
    echo "Running dry run (data validation only)..."
    python ml/scripts/fine_tune_gemma.py \
        --data "$ARTIFACTS_DIR/training_data.jsonl" \
        $SYNTHETIC_FLAG \
        --target "$TARGET" \
        --output "$OUTPUT_DIR" \
        --dry-run
    
    echo -e "${GREEN}✓ Dry run complete${NC}"
else
    echo "Starting fine-tuning..."
    echo "   Target: $TARGET"
    echo "   Output: $OUTPUT_DIR"
    echo ""
    
    python ml/scripts/fine_tune_gemma.py \
        --data "$ARTIFACTS_DIR/training_data.jsonl" \
        $SYNTHETIC_FLAG \
        --target "$TARGET" \
        --output "$OUTPUT_DIR" \
        --epochs "$EPOCHS" \
        --batch-size "$BATCH_SIZE" \
        --learning-rate "$LEARNING_RATE"
    
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
echo "   Training data: $ARTIFACTS_DIR/training_data.jsonl"
[ -f "$ARTIFACTS_DIR/teacher_qa_gemma.jsonl" ] && echo "   Synthetic QA:   $ARTIFACTS_DIR/teacher_qa_gemma.jsonl"
[ -d "$OUTPUT_DIR/final" ] && echo "   Model:          $OUTPUT_DIR/final/"
echo ""
echo "Next steps:"
echo "   1. Push model to HuggingFace: huggingface-cli upload <repo> $OUTPUT_DIR/final"
echo "   2. Evaluate: python ml/pipelines/evaluate.py --model $OUTPUT_DIR/final"
echo "   3. Convert for mobile: python ml/scripts/export_mobile.py"
echo ""

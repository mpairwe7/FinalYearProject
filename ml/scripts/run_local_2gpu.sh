#!/bin/bash
# =============================================================================
# URA Tax Assistant - Local 2-GPU Training Runner
# =============================================================================
# Runs the full training pipeline locally on 2 GPUs without Kaggle/GitHub Actions.
#
# Usage:
#   ./ml/scripts/run_local_2gpu.sh                          # Default: GPUs 1,2, web_high_accuracy
#   ./ml/scripts/run_local_2gpu.sh --gpu-ids 2,3            # Pick specific GPUs
#   ./ml/scripts/run_local_2gpu.sh --target mobile_gemma_2b # Different target
#   ./ml/scripts/run_local_2gpu.sh --dry-run                # Validate only
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Defaults - GPUs 1,2 are the least loaded on this server
GPU_IDS="1,2"
TARGET="web_high_accuracy"
EXTRA_ARGS=()

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --gpu-ids)
            GPU_IDS="$2"
            shift 2
            ;;
        --target)
            TARGET="$2"
            shift 2
            ;;
        *)
            EXTRA_ARGS+=("$1")
            shift
            ;;
    esac
done

# Count GPUs
NUM_GPUS=$(echo "$GPU_IDS" | tr ',' '\n' | wc -l)

echo ""
echo "======================================================================"
echo "URA TAX ASSISTANT - LOCAL ${NUM_GPUS}-GPU TRAINING"
echo "======================================================================"
echo ""
echo "  GPUs:    $GPU_IDS ($NUM_GPUS devices)"
echo "  Target:  $TARGET"
echo "  Extra:   ${EXTRA_ARGS[*]:-none}"
echo ""

# Show selected GPU status
echo "GPU Status:"
export CUDA_VISIBLE_DEVICES="$GPU_IDS"
nvidia-smi --query-gpu=index,name,memory.free,memory.total,utilization.gpu \
    --format=csv,noheader \
    --id="$GPU_IDS" 2>/dev/null || echo "  (nvidia-smi query failed, continuing...)"
echo ""

# Run the pipeline
exec "$PROJECT_ROOT/ml/scripts/run_training_pipeline.sh" \
    --target "$TARGET" \
    --gpu-ids "$GPU_IDS" \
    --num-gpus "$NUM_GPUS" \
    "${EXTRA_ARGS[@]}"

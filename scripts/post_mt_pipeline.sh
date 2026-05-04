#!/usr/bin/env bash
# Post-MT training pipeline: evaluate → quality gates → distill → ONNX export → mobile bundle
set -euo pipefail

cd /home/developer/Mpairwe7/FinalYearProject
source .venv/bin/activate
export PYTHONPATH=.

if [ -n "${CUDA_VISIBLE_DEVICES:-}" ]; then
    ACTIVE_GPUS="$CUDA_VISIBLE_DEVICES"
elif [ -n "${GPU_ID:-}" ]; then
    ACTIVE_GPUS="$GPU_ID"
elif ACTIVE_GPUS="$(./scripts/select_free_gpu.sh 2>/dev/null)"; then
    :
else
    ACTIVE_GPUS=""
fi

if [ -n "$ACTIVE_GPUS" ]; then
    export CUDA_VISIBLE_DEVICES="$ACTIVE_GPUS"
    echo "[$(date)] Using CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES"
else
    echo "[$(date)] No GPU auto-selected; continuing with current environment."
fi

MT_PID=${1:?Usage: $0 <mt_training_pid>}
ASR_PIPELINE_PID=${2:-}  # optional: wait for ASR pipeline too before mobile bundle
LOG=/tmp/post_mt_pipeline.log

echo "[$(date)] Waiting for MT training PID $MT_PID to finish..." | tee "$LOG"
while kill -0 "$MT_PID" 2>/dev/null; do sleep 30; done
echo "[$(date)] MT training finished. Starting post-training pipeline." | tee -a "$LOG"

# Step 1: Evaluate
echo "[$(date)] === Step 1: Evaluate MT ===" | tee -a "$LOG"
python -m ml.pipelines.evaluate_mt \
    --eval-set Data/eval/mt_eval_lgen.jsonl \
    2>&1 | tee -a "$LOG"

# Step 2: Quality gates
echo "[$(date)] === Step 2: Quality Gates (mt) ===" | tee -a "$LOG"
python -m ml.pipelines.quality_gates --family mt \
    --metrics-path Results/metrics/mt_metrics.json \
    2>&1 | tee -a "$LOG"

GATE_EXIT=$?
if [ $GATE_EXIT -ne 0 ]; then
    echo "[$(date)] MT QUALITY GATES FAILED — skipping export." | tee -a "$LOG"
    exit 1
fi

# Step 3: Distill teacher → student
echo "[$(date)] === Step 3: Distill MT ===" | tee -a "$LOG"
python -m ml.scripts.mt.distill_mt \
    --teacher artifacts/mt/madlad_ura_lgen/final \
    --out artifacts/mt/student \
    2>&1 | tee -a "$LOG"

# Step 4: ONNX export
echo "[$(date)] === Step 4: Export MT ONNX ===" | tee -a "$LOG"
python -m ml.scripts.mt.export_mt_onnx \
    --checkpoint artifacts/mt/student/final \
    --out artifacts/mt/onnx/student_int8 \
    2>&1 | tee -a "$LOG"

# Step 5: Mobile bundle (wait for ASR pipeline if running)
if [ -n "$ASR_PIPELINE_PID" ]; then
    echo "[$(date)] Waiting for ASR pipeline PID $ASR_PIPELINE_PID before mobile bundle..." | tee -a "$LOG"
    while kill -0 "$ASR_PIPELINE_PID" 2>/dev/null; do sleep 15; done
fi

echo "[$(date)] === Step 5: Mobile Bundle ===" | tee -a "$LOG"
python -m ml.scripts.speech.export_mobile_speech \
    2>&1 | tee -a "$LOG"

echo "[$(date)] MT post-training pipeline COMPLETE." | tee -a "$LOG"

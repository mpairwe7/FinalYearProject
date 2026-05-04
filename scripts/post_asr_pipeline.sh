#!/usr/bin/env bash
# Post-ASR training pipeline: evaluate → quality gates → ONNX export → sherpa package
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

ASR_PID=${1:?Usage: $0 <asr_training_pid>}
LOG=/tmp/post_asr_pipeline.log

echo "[$(date)] Waiting for ASR training PID $ASR_PID to finish..." | tee "$LOG"
while kill -0 "$ASR_PID" 2>/dev/null; do sleep 30; done
echo "[$(date)] ASR training finished. Starting post-training pipeline." | tee -a "$LOG"

# Step 1: Evaluate
echo "[$(date)] === Step 1: Evaluate Speech ===" | tee -a "$LOG"
python -m ml.pipelines.evaluate_speech \
    --eval-set Data/speech/asr_eval_lg.jsonl \
    2>&1 | tee -a "$LOG"

# Step 2: Quality gates
echo "[$(date)] === Step 2: Quality Gates (speech) ===" | tee -a "$LOG"
python -m ml.pipelines.quality_gates --family speech \
    --metrics-path Results/metrics/speech_metrics.json \
    2>&1 | tee -a "$LOG"

GATE_EXIT=$?
if [ $GATE_EXIT -ne 0 ]; then
    echo "[$(date)] SPEECH QUALITY GATES FAILED — skipping export." | tee -a "$LOG"
    exit 1
fi

# Step 3: ONNX export
echo "[$(date)] === Step 3: Export ASR ONNX ===" | tee -a "$LOG"
python -m ml.scripts.asr.export_asr_onnx \
    --checkpoint artifacts/speech/asr/whisper_lg \
    2>&1 | tee -a "$LOG"

# Step 4: Sherpa packaging
echo "[$(date)] === Step 4: Sherpa Package ===" | tee -a "$LOG"
python -m ml.scripts.asr.export_asr_sherpa --name whisper_lg \
    2>&1 | tee -a "$LOG"

echo "[$(date)] ASR post-training pipeline COMPLETE." | tee -a "$LOG"

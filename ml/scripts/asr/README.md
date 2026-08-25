# ASR Scripts (2026 Production)

Commercial-safe speech recognition pipeline for URA Chatbot.

## Commercial-safe model allowlist

| Model | License | Params | Role |
|---|---|---|---|
| `openai/whisper-small` | MIT | 244 M | primary multilingual backbone |
| `distil-whisper/distil-small.en` | MIT | 166 M | English-only 2x speedup |
| `openai/whisper-tiny` | MIT | 39 M | ultra-low-RAM fallback |
| `snakers4/silero-vad` | MIT | 1.8 M | streaming VAD |

**CC-BY-NC models are NOT used** — this pipeline is commercially-usable end-to-end.

## Workflow

```
download_models.py           # fetch HF snapshots into artifacts/speech/asr/
      |
      v
train_luganda.py             # (optional) LoRA fine-tune Whisper-small on CV-lg
      |
      v
export_asr_onnx.py           # optimum ONNX export + INT8 quantisation
      |
      v
export_asr_sherpa.py         # re-package into sherpa-onnx layout
      |
      v
ml/scripts/speech/export_mobile_speech.py   # copy into MobileApp assets
```

## Quick start (dry runs, safe on any machine)

```bash
python -m ml.scripts.asr.download_models --dry-run
python -m ml.scripts.asr.export_asr_onnx --model whisper-small --dry-run
python -m ml.scripts.asr.export_asr_sherpa --name whisper-small --dry-run
python -m ml.scripts.asr.infer_asr --help
```

## End-to-end (requires GPU for training only)

```bash
python -m ml.scripts.asr.download_models
python -m ml.scripts.asr.export_asr_onnx --model whisper-small
python -m ml.scripts.asr.export_asr_sherpa --name openai__whisper-small
python -m ml.scripts.asr.infer_asr --audio sample.wav
```

## Inference backends

`infer_asr.py` tries these backends in order:

1. **sherpa-onnx** — production runtime (matches what ships to mobile).
2. **transformers pipeline** — dev fallback, no ONNX needed.
3. **mock** — deterministic placeholder for CI.

Force a specific backend via `--backend {sherpa|transformers|mock}`.

## Quality gates (enforced by `ml/pipelines/evaluate_speech.py`)

```yaml
max_wer_en: 0.15          # 15% WER or better on English test set
max_wer_lg: 0.25          # 25% WER or better on Luganda test set
max_rtf: 0.5              # Real-Time Factor (< 0.5 means 2x faster than real time)
max_p95_latency_ms: 1500  # p95 response latency on target hardware
```

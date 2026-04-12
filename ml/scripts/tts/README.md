# TTS Scripts (2026 Production)

Commercial-safe text-to-speech pipeline for URA Chatbot.

## Commercial-safe voice allowlist

| Voice | License | Size | Language | Role |
|---|---|---|---|---|
| `rhasspy/piper-voices / en_US-lessac-medium` | MIT | 63 MB | en | primary English |
| `rhasspy/piper-voices / en_US-libritts_r-medium` | MIT | 63 MB | en | alternative English |
| `hexgrad/Kokoro-82M` | Apache-2.0 | 82 MB | en | small-footprint English |
| `luganda-vits-v1` (trained in-project, Coqui VITS) | MPL-2.0 | ~90 MB | lg | custom Luganda (requires recorded voice data) |

**No CC-BY-NC voices** — this explicitly excludes `facebook/mms-tts-lug`. The
Luganda voice path is a scaffold for a custom VITS trained once voice data has
been recorded.

## Workflow

```
download_models.py           # fetch Piper/Kokoro under artifacts/speech/tts/
      |
      v
train_luganda_vits.py        # (Luganda only) custom VITS training
      |
      v
export_tts_onnx.py           # package into sherpa-onnx layout
      |
      v
ml/scripts/speech/export_mobile_speech.py   # copy into MobileApp assets
```

## Quick start (dry runs)

```bash
python -m ml.scripts.tts.download_models --dry-run
python -m ml.scripts.tts.export_tts_onnx --voice en_US-lessac-medium --dry-run
python -m ml.scripts.tts.infer_tts --text "Hello world" --backend mock
```

## Inference backends

`infer_tts.py` tries these in order:

1. **sherpa-onnx OfflineTts** — production runtime.
2. **Piper Python** — pure-piper dev fallback.
3. **mock** — deterministic beep; CI-safe, always succeeds.

## Quality gates (enforced by `ml/pipelines/evaluate_tts.py`)

```yaml
min_roundtrip_intelligibility: 0.80   # 1 - WER when ASR re-transcribes TTS output
max_tts_rtf: 0.5                       # synthesis must be faster than real-time
min_mos: 3.5                           # optional; human MOS study
min_speaker_consistency: 0.85          # embedding cosine across samples
```

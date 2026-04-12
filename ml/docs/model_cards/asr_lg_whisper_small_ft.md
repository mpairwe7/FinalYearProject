# Model Card: ASR Luganda — Whisper-small fine-tune

## Summary

- **Base model**: `openai/whisper-small` (MIT)
- **Fine-tune strategy**: LoRA (r=16, alpha=32) via `peft`
- **Adapter license**: MIT (derivative)
- **Runtime**: sherpa-onnx (ONNX INT8 quantised)
- **Role**: Primary Luganda speech-to-text backbone.
- **Training script**: `ml/scripts/asr/train_luganda.py`

## Intended use

Transcribing Luganda-language user speech in the URA tax-assistant
mobile app. Runs on-device.

## Out-of-scope

- Other Bantu languages (Swahili, Acholi, Ateso).
- Tonal disambiguation (Luganda is tonal but the underlying Whisper
  model is not tone-aware).
- Speaker diarisation.

## Training data

| Source | License | Role |
|---|---|---|
| Mozilla Common Voice `lg` | CC0-1.0 | primary |
| Makerere SALT-ASR `lg` (optional) | CC-BY-4.0 | augmentation |
| Project-recorded Luganda audio (if any) | proprietary | optional |

See `ml/docs/data_cards/asr_common_voice_lg.md` and
`ml/docs/data_cards/asr_salt.md`.

## Training procedure

- Epochs: 5 (mobile target) / 10 (full target)
- Batch: 8 with gradient accumulation 2
- LR: 1e-4 (mobile) / 2e-4 (full)
- Warmup: 200 / 500 steps
- Mixed precision: fp16
- Early stopping: none (trained to completion)

Run via:

```
python -m ml.scripts.asr.train_luganda --target mobile
```

## Evaluation

- Target Luganda WER: <= 0.25 (`speech_quality_gates.max_wer_lg`)
- Measured WER: TBD
- Per-speaker variance: TBD

## Limitations

- Quality depends heavily on Common Voice lg coverage; low-frequency
  dialects are under-represented.
- Tax-specific vocabulary ("omusolo", "TIN", "URA") may need a targeted
  fine-tune pass with URA-recorded audio.

## Safety

Same as the base Whisper model — pure transcription, no refusal logic.

## Authors / version

- URA Chatbot ML team — 2026.1.0

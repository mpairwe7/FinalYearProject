# Model Card: ASR English — Whisper-small

## Summary

- **Model**: `openai/whisper-small`
- **License**: MIT
- **Parameters**: 244 M
- **Runtime**: sherpa-onnx (ONNX INT8 quantised)
- **Role**: Primary English speech-to-text backbone for URA Chatbot.

## Intended use

Transcribing English-language user speech in the URA tax-assistant mobile
app. Runs fully on-device via the unified audio runtime (sherpa-onnx).

## Out-of-scope

- Medical, legal, or clinical transcription.
- Languages other than English (Luganda is handled by a fine-tuned
  variant — see `asr_lg_whisper_small_ft.md`).
- Speaker diarisation.
- Speaker verification / identification.

## Training data

Uses upstream OpenAI Whisper weights; no additional training. Training
data provenance inherited from the OpenAI release notes (Web-scraped
multilingual audio, ~680 k hours).

## Evaluation

- Target English WER on URA test set: <= 0.15 (see
  `speech_quality_gates` in `ml/configs/training_config.yaml`).
- Measured WER: TBD (populated by `ml/pipelines/evaluate_speech.py`).

## Limitations

- Performance degrades on noisy microphones and heavy accents.
- No real-time adaptation per speaker.
- Code-switching (mixed en/lg input) is not handled at this layer;
  `lang_id.py` routes to the Luganda model when appropriate.

## Safety

- The ASR stage does not include a classifier or refusal mechanism; it
  is a pure transcription step. Safety decisions happen downstream in
  the LLM + redteam harness.

## Authors / version

- URA Chatbot ML team — 2026.1.0

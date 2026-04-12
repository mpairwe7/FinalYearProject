# Model Card: TTS English — Piper lessac

## Summary

- **Model**: `rhasspy/piper-voices / en_US-lessac-medium`
- **License**: MIT
- **Size**: ~63 MB (ONNX)
- **Runtime**: sherpa-onnx OfflineTts
- **Voice**: single adult US English speaker

## Intended use

English text-to-speech for URA Chatbot voice replies on mobile.
Sentence-chunked streaming synthesis via the `TtsService` Dart API.

## Out-of-scope

- Voice cloning of specific individuals.
- Emotional / expressive synthesis.
- Languages other than English.

## Training data

Upstream Rhasspy Piper training data (LibriTTS-derived, CC-BY-4.0 +
CC-BY-SA audio). This project does NOT re-train Piper — we ship the
upstream artifact as-is.

## Evaluation

- Target round-trip intelligibility: >= 0.80
  (`tts_quality_gates.min_roundtrip_intelligibility`).
- Target RTF: <= 0.5 on the reference device.
- Measured: TBD (populated by `ml/pipelines/evaluate_tts.py`).

## Limitations

- Single voice, single language.
- Pronunciation of Luganda loanwords (e.g. "Omusolo", "URA") is
  grapheme-to-phoneme guessed — may be incorrect. The Luganda voice
  handles native Luganda speech.
- No explicit prosody control.

## Safety

- TTS output is deterministic given the input text; no refusal logic at
  this layer. Refusals happen upstream in the LLM.
- The `redteam_voice.py` harness tests adversarial prompts by
  synthesising them and running the full pipeline.

## Authors / version

- Upstream: Rhasspy Piper team
- Integration: URA Chatbot ML team — 2026.1.0

# Data Card: Luganda Voice Corpus (Project-Recorded)

## Status

**Not yet collected.** This data card documents the target state. Until
collection, the corresponding model card
(`tts_lg_vits.md`) is marked `status: training_required`.

## Provenance

- **Source**: In-project recording session.
- **License**: **project-internal** with explicit per-speaker consent.
- **Consent**: versioned, revocable — tracked in `CONSENT.yaml` next to
  the audio.

## Target content

| Property | Value |
|---|---|
| Speaker | 1 adult native Luganda speaker |
| Duration | 3-4 hours clean speech |
| Prompts | 500-1000 URA-themed sentences |
| Sample rate | 22 050 Hz mono |
| Bit depth | 16-bit PCM |
| File format | WAV (no lossy compression) |
| Environment | Quiet room, single microphone, consistent mic distance |
| Silence trimming | Leading/trailing silence < 500 ms |
| Metadata | LJSpeech-style `metadata.csv`: `file | text | normalised_text` |

## Collection protocol

1. Draft the 500-1000 prompt list (tax terminology, procedures, refusals).
2. Review prompts for PII — no names, addresses, TINs, phone numbers.
3. Obtain written + recorded consent from the speaker (see
   `Data/speech/tts/lg_voice/README.md`).
4. Record in one or two sessions with breaks.
5. Post-process: trim silence, normalise loudness, audit for clipping.
6. Store under `Data/speech/tts/lg_voice/speaker_001/` with the layout
   described in the TTS training script.

## Ethical considerations

- **Speaker ownership.** The speaker owns their voice data. The consent
  form makes this explicit and allows withdrawal at any time.
- **Fair compensation.** Speakers are compensated for their time at a
  rate agreed up-front.
- **Voice reuse.** The recorded voice is used only for the URA Chatbot
  Luganda TTS model. Any other use requires a new consent.
- **Deepfake risk.** We do not distribute raw recordings outside the
  training pipeline. The resulting VITS model is a single-speaker
  voice — it cannot be used to clone other speakers.

## In-project usage

- Training: `ml/scripts/tts/train_luganda_vits.py`
- Evaluation: `ml/pipelines/evaluate_tts.py` (speaker consistency metric)
- Bundled in mobile: yes (as the VITS ONNX model, not as raw audio)

## Privacy / PII

- Prompt text is reviewed for PII before recording.
- Raw audio is NOT committed to git — it lives on local disk or private
  object storage.
- Only the trained model weights are shipped.

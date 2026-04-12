# Model Card: TTS Luganda — Custom Coqui VITS

## Summary

- **Framework**: Coqui TTS VITS recipe
- **License**: MPL-2.0 (Coqui framework) + project-internal for voice data
- **Size budget**: ~100 MB (ONNX)
- **Runtime**: sherpa-onnx OfflineTts
- **Status**: **training_required** — voice data collection is a
  prerequisite (see `Data/speech/tts/lg_voice/README.md`).

## Why a custom voice?

The obvious pretrained Luganda TTS (`facebook/mms-tts-lug`) is
CC-BY-NC-4.0, which violates the project's commercial-safety policy.
This model is trained in-project on a recorded Luganda speaker using
the Coqui VITS recipe (MPL-2.0) and ships with documented consent.

## Intended use

Luganda text-to-speech for URA Chatbot voice replies on mobile.

## Out-of-scope

- Voice cloning of unrelated speakers.
- Multi-speaker synthesis.
- Languages other than Luganda.

## Training data

| Source | License | Role |
|---|---|---|
| Project-recorded speaker_001 | project-internal (consent required) | primary |

See `ml/docs/data_cards/tts_luganda_voice.md`.

## Training procedure

```
python -m ml.scripts.tts.train_luganda_vits \
    --data-dir Data/speech/tts/lg_voice/speaker_001 \
    --output-dir artifacts/speech/tts/luganda_vits
```

- Sample rate: 22050
- Epochs: 1000 (with early stopping on validation loss)
- Batch: 16
- LR: 1e-4
- G2P backend: espeak-ng `sw` (Swahili approximation; Luganda not yet
  native to espeak)
- Mixed precision: fp16

## Evaluation

Gates (`tts_quality_gates`):

- min_roundtrip_intelligibility: 0.80
- max_tts_rtf: 0.5
- min_mos: 3.5 (optional — human study)
- min_speaker_consistency: 0.85

## Limitations

- **Not yet trained.** All fields here are targets; populate after the
  first real training run.
- Single speaker — no multi-speaker generalisation.
- Prosody may be flat; Coqui VITS is not tone-aware and Luganda is
  tonal (future work: add pitch post-processing).
- Tax-specific vocabulary ("URA", "TIN", numbers) should be reviewed
  with the speaker before recording.

## Safety + consent

- **Consent is mandatory.** The training script refuses to run without
  a `CONSENT.yaml` file next to the voice data. Consent is versioned
  and revocable.
- The speaker retains the right to have the voice removed and the model
  re-trained without their data.

## Authors / version

- URA Chatbot ML team — 2026.1.0 (scaffold); model version populated
  after first training run.

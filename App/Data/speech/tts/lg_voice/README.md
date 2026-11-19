# Luganda Voice Collection (for custom VITS TTS)

The Luganda voice for URA Chatbot is trained in-project using Coqui VITS
because no commercial-safe pretrained Luganda TTS exists in 2026. This
directory is where recorded voice data lives.

## Status

**Not yet collected.** This is a deferred task — scripts + training code
already exist under `ml/scripts/tts/train_luganda_vits.py`.

## Recording protocol (target: single-speaker VITS)

| Requirement | Value |
|---|---|
| Speaker | 1 adult native Luganda speaker |
| Consent | Written, versioned, revocable — see `CONSENT_TEMPLATE.md` (TODO) |
| Duration | 3-4 hours clean speech minimum |
| Prompts | 500-1000 URA-relevant sentences (tax terminology, procedures, refusals) |
| Sample rate | 22050 Hz mono |
| Bit depth | 16-bit PCM |
| File format | WAV (no lossy compression in the training set) |
| Environment | Quiet room, single microphone, consistent mic distance |
| Silence | Trim leading/trailing silence to <500 ms |

## Directory layout (once data is recorded)

```
Data/speech/tts/lg_voice/
  README.md
  CONSENT_TEMPLATE.md                 # consent form (TODO)
  speaker_001/
    metadata.csv                      # | audio_file | text | normalised_text
    wavs/
      000001.wav
      000002.wav
      ...
```

## Consumed by

* `ml/scripts/tts/train_luganda_vits.py`
* `ml/pipelines/evaluate_tts.py` (for speaker-consistency metric)

## Ethics / privacy

* The speaker owns their voice data. The consent form must make this
  explicit and allow withdrawal at any time.
* Recordings must **never** contain PII (names, addresses, phone numbers,
  TINs). All 500-1000 prompts are reviewed before recording begins.
* Any derived model (VITS weights) must include the speaker's consent
  version in its model card.

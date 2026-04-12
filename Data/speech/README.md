# Speech Data Directory

This directory holds audio + transcript pairs for the URA Chatbot speech
pipeline (ASR training / evaluation and TTS voice collection).

## Layout

```
Data/speech/
  README.md                       # this file
  asr_eval_en.jsonl               # English ASR eval set (WER/CER)
  asr_eval_lg.jsonl               # Luganda ASR eval set
  tts_test_prompts.jsonl          # prompts used by evaluate_tts.py
  en/                             # English training / eval audio (placeholder)
  lg/                             # Luganda training / eval audio (placeholder)
  tts/
    lg_voice/                     # Luganda speaker recordings for custom VITS voice
      README.md                   # voice-collection protocol
```

## Existing data in this repo (2026-04)

The project already includes two data sources that the speech pipeline
consumes directly — no duplication needed:

| Path | Content | Used by |
|---|---|---|
| `Data/lgaudio/` | 12 Common Voice Luganda `.mp3` files | `ml/scripts/data_aug/asr_loaders.py::load_common_voice_lg` |
| `Data/TTT/` | Luganda parallel text (`eng.lug.txt`, `Luganda.csv`, `luganda_wiki_corpus.txt`, `WordProject_Luganda_English_Corpus`) | `ml/scripts/data_aug/mt_loaders.py` + existing `loaders.py` |

## JSONL format (all `*_eval_*.jsonl` files)

Each line is a record with at minimum:

```json
{
  "audio_path":     "Data/speech/en/clip_001.wav",
  "reference":      "Please explain VAT registration",
  "language":       "en",
  "duration_s":     3.42,
  "sample_rate":    16000,
  "source_type":    "project_recording",
  "license":        "proprietary"
}
```

Consumed by `ml/pipelines/evaluate_speech.py`. Schema validation lives in
`ml/scripts/data_aug/speech_schema.py::AudioExample`.

## TTS prompt format (`tts_test_prompts.jsonl`)

```json
{"text": "Welcome to the Uganda Revenue Authority.", "language": "en"}
{"text": "Nsimbi z'omusolo.", "language": "lg"}
```

Consumed by `ml/pipelines/evaluate_tts.py` for round-trip intelligibility.

## Privacy + licensing

* Raw audio is **never** committed beyond the initial eval seed. Larger
  corpora stay on local disk / private object storage.
* Every file referenced in a JSONL must have a documented `license`
  value — the `export_mobile_speech.py` filter refuses CC-BY-NC entries.
* Project-recorded voice data requires explicit consent; see
  `Data/speech/tts/lg_voice/README.md`.

## Next steps

1. Populate `asr_eval_en.jsonl` with held-out English URA recordings.
2. Run `ml/scripts/data_aug/asr_loaders.py` over `Data/lgaudio/` to emit
   `asr_eval_lg.jsonl` with Common Voice Luganda transcripts.
3. Record the Luganda VITS voice corpus (see `tts/lg_voice/README.md`).

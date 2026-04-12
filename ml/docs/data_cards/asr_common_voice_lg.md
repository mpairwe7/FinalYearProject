# Data Card: Common Voice Luganda

## Provenance

- **Source**: Mozilla Common Voice — https://commonvoice.mozilla.org/en/datasets
- **Dataset**: `mozilla-foundation/common_voice_17_0` (or latest) — config `lg`
- **License**: **CC0-1.0** (public-domain dedication)
- **Consent**: Contributors to Common Voice release their recordings
  under CC0 at upload time.

## Content

- Luganda speech clips + validated transcripts (`validated.tsv`).
- Sample rate: typically 48 kHz, resampled to 16 kHz by the loader.
- Clip durations: mostly 3-8 s; filtered to `[0.3, 30.0]` s range.
- Speakers: crowdsourced; anonymised client IDs.

## Collection

- Crowdsourced via Common Voice's web platform.
- Text prompts drawn from Wikipedia + public-domain sentences.
- Each clip is reviewed by multiple volunteers before making it into
  `validated.tsv`.

## Preprocessing

Handled by `ml/scripts/data_aug/asr_loaders.py::load_common_voice_lg`:

- Parses `validated.tsv` (`client_id`, `path`, `sentence`, ...).
- Resolves `path` relative to `clips/` or the passed data dir.
- Probes duration + sample rate via soundfile / librosa.
- Emits `AudioExample` rows with `license=public_domain`.

## Known biases

- Speaker distribution is NOT balanced by age / gender / region.
- Urban / educated speakers are over-represented.
- Ambient noise varies wildly (phone mics, laptop mics, quiet rooms).

## In-project usage

- **Training**: `ml/scripts/asr/train_luganda.py`
- **Evaluation**: `ml/pipelines/evaluate_speech.py` (held-out split)
- **Bundled in mobile**: no (training-only)

## Location in repo

- Loader: `ml/scripts/data_aug/asr_loaders.py`
- Expected path: `Data/common_voice_lg/` (full snapshot) or
  `Data/lgaudio/` (existing 12-clip sample in this repo as of 2026-04)

## Privacy / PII

- Contributors' user IDs are anonymised by Common Voice.
- Transcripts do not contain PII by design.
- Any tax identifiers appearing in text are filtered by the project's
  PII redactor (`ml/scripts/data_aug/text_utils.py::redact_pii`).

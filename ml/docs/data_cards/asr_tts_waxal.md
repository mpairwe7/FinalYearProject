# Data Card: Google WAXAL NLP (Makerere AI Lab)

## Overview

| Field | Value |
|---|---|
| Dataset | Google WaxalNLP |
| HuggingFace | `google/WaxalNLP` |
| Languages | 21 African languages incl. nyn, ach, lug |
| Total Size | 11,000+ hours speech, ~2.8M samples |
| License | **CC-BY-SA-4.0 / CC-BY-4.0** |
| Creator | Makerere University AI Lab + Google |
| Collected | 2023-2026 |

## Description

WAXAL is the largest speech dataset for East African languages as of
2026. Collected by the Makerere University AI Lab in partnership with
Google over 3 years. Includes both ASR and TTS configurations.

## URA Chatbot Relevant Subsets

| Config | Language | Type | Samples | Notes |
|---|---|---|---|---|
| `nyn_asr` | Runyankole | ASR | 132,000 | Train/val/test/unlabeled splits |
| `nyn_tts` | Runyankole | TTS | 1,990 | Studio quality, single speaker |
| `ach_asr` | Acholi | ASR | 114,000 | Train/val/test/unlabeled splits |
| `ach_tts` | Acholi | TTS | 2,030 | Studio quality, single speaker |
| `lug_asr` | Luganda | ASR | 98,500 | Supplements Common Voice |
| `lug_tts` | Luganda | TTS | 2,020 | Studio quality, single speaker |

## Impact on Project

WAXAL's TTS data for nyn and ach **eliminates the need for custom
voice recording sessions**. The ~2k studio samples per language are
sufficient for VITS fine-tuning (Coqui TTS requires 1-4 hours).

## Download

```bash
python -m ml.scripts.data_aug.dataset_downloader --output-dir Data/online_corpora
# Or directly:
from datasets import load_dataset
ds = load_dataset("google/WaxalNLP", "nyn_tts", split="train")
```

## Citation

Makerere University AI Lab, Google Research. "WAXAL: A Large-Scale
Multilingual Speech Dataset for African Languages." 2026.

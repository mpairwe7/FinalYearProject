# Data Card: Sunbird SALT Multilingual Parallel Corpus

## Overview

| Field | Value |
|---|---|
| Dataset | Sunbird SALT |
| HuggingFace | `Sunbird/salt` |
| Languages | eng, lug, ach, nyn, swa, teo, lgg, nyn, lsb, xog |
| Size | 25,000 parallel sentences + multispeaker ASR + studio TTS |
| License | **CC-BY-SA-4.0** |
| Creator | Sunbird AI (Kampala, Uganda) |
| Collected | 2022-2025 |

## Description

SALT is the single highest-value dataset for Ugandan low-resource
languages. It contains parallel text across 10 Ugandan languages,
multispeaker ASR recordings, and studio-quality TTS recordings.

## Configurations

| Config | Type | Languages | Samples |
|---|---|---|---|
| `text-all` | Parallel text | All 10 | 25,000 sentences |
| `multispeaker-ach` | ASR audio | Acholi | Variable |
| `multispeaker-nyn` | ASR audio | Runyankole | Variable |
| `multispeaker-lug` | ASR audio | Luganda | Variable |

## Usage in This Project

- **MT training**: Parallel en↔nyn, en↔ach, en↔lg pairs from `text-all`
- **ASR training**: Multispeaker recordings for Whisper fine-tuning
- **TTS training**: Studio recordings as alternative to custom voice collection

## Download

```bash
python -m ml.scripts.data_aug.dataset_downloader --output-dir Data/online_corpora
# Or directly:
from datasets import load_dataset
ds = load_dataset("Sunbird/salt", "text-all", split="train")
```

## Citation

Akera et al. "Machine Translation for Ugandan Languages." Sunbird AI, 2023.

# Online Corpora Download Status

Generated on branch `feat/data-download-and-training-setup`.

## Summary

| Source | Pair / Lang | File | Status | Notes |
|---|---|---|---|---|
| **SALT** | en-lg, en-sw, en-nyn, en-ach | `salt/salt_en_{lg,sw,nyn,ach}.jsonl` | OK (23,947 pairs each, ~95.8 k total) | Pulled from `Sunbird/salt` config `text-all` — a single multi-way table with columns for `eng/swa/lug/nyn/ach/teo/lgg/xog/ttj/ibo`. CC-BY-4.0. Now the primary MT corpus for all four Ugandan language pairs. |
| FLEURS | lg | `fleurs/fleurs_lg.jsonl` | OK (723 lines) | Test-split transcriptions from `google/fleurs` config `lg_ug`. |
| FLEURS | sw | `fleurs/fleurs_sw.jsonl` | OK (487 lines) | Test-split transcriptions from `google/fleurs` config `sw_ke`. |
| FLEURS | nyn, ach | — | UNSUPPORTED | `google/fleurs` covers ~102 languages; Runyankole and Acholi are not among them. SALT provides parallel text for these languages instead. |
| Common Voice | lg | `Data/common_voice_lg/` (gitignored) | OK via `fsicoli/common_voice_19_0` | The Mozilla mirror (`mozilla-foundation/common_voice_17_0`) no longer ships audio on HF — it serves only `README.md`, yielding `EmptyDatasetError`. `fsicoli/common_voice_19_0` mirrors the audio tarballs for every locale and works with `datasets.load_dataset(..., trust_remote_code=True)`. See `download_common_voice()` in `dataset_downloader.py`. |
| ~~JW300~~ | — | — | REMOVED from default flow | `opus/JW300` on HF returns 404. Kept only behind `--include-legacy`. SALT now covers parallel MT data for all four Ugandan pairs. |
| ~~OPUS Tatoeba~~ | — | — | REMOVED from default flow | `opus/Tatoeba` is deprecated. `Helsinki-NLP/tatoeba_mt` only has `eng-swa` for our pairs, so not worth the plumbing. |
| ~~Masakhane lafand-mt~~ | — | — | REMOVED from default flow | `masakhane/lafand-mt` does not expose en-{lg,sw,nyn,ach}. SALT covers all four natively. |

## Code changes in this commit

- `ml/scripts/data_aug/dataset_downloader.py`
  - Added `download_salt()` for `Sunbird/salt` `text-all` config.
  - Added `download_common_voice()` for `fsicoli/common_voice_19_0`.
  - `download_all_corpora()` now defaults to SALT + FLEURS; JW300 / OPUS Tatoeba / Masakhane are behind `--include-legacy`.
  - New CLI flags: `--no-salt`, `--include-legacy`, `--common-voice {lg,sw,…}`.
  - FLEURS already returns early for unsupported languages (nyn, ach) — unchanged.

## Other stages in this session

- **TTS recording prompts**: generated 256 prompts/language for `lg`, `nyn`, `ach` under `Data/speech/tts/prompts/*.txt`.
- **`python -m ml.scripts.train_all --dry-run -v`**: orchestrator ran end-to-end, but several sub-scripts error with `unrecognized arguments` / missing required args (e.g. `export_tts_onnx.py` needs `--voice`; `quality_gates.py` doesn't accept `--results-dir` or `--dry-run`). These are pre-existing CLI-contract mismatches in `train_all.py` — tracked separately.

## Reproduction

```bash
# Primary MT text corpora (fast, ~10 s on broadband):
python -m ml.scripts.data_aug.dataset_downloader \
    --output-dir Data/online_corpora \
    --lang-pairs en-lg en-sw en-nyn en-ach \
    --no-fleurs

# ASR eval sets (FLEURS, ~5 GB audio cache per language):
python -m ml.scripts.data_aug.dataset_downloader \
    --output-dir Data/online_corpora \
    --lang-pairs en-lg en-sw \
    --no-salt

# Common Voice audio (large, gitignored):
python -c "
from ml.scripts.data_aug.dataset_downloader import download_common_voice
from pathlib import Path
download_common_voice('lg', Path('Data'))
"
```

## Next steps (out of scope for this branch)

1. Fix `train_all.py` CLI wiring so `--dry-run` exits cleanly for every stage.
2. Re-evaluate whether any NLLB subset is worth adding for `en-lg`/`en-sw` on top of SALT.

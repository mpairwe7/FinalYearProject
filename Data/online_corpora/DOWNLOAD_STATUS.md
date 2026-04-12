# Online Corpora Download Status

Generated on branch `feat/data-download-and-training-setup`.

## Summary

| Source | Pair / Lang | File | Status | Notes |
|---|---|---|---|---|
| FLEURS | lg | `fleurs/fleurs_lg.jsonl` | OK (723 lines, 156 KB) | Text-only transcriptions; HF blob cache ~2 GB audio. |
| FLEURS | sw | `fleurs/fleurs_sw.jsonl` | NOT WRITTEN | Download interrupted after ~5 GB cache while on `sw_ke` split. Re-run without `--lang-pairs en-sw` or allow more time (+500 MB/pair). |
| FLEURS | nyn, ach | — | UNSUPPORTED | Google FLEURS covers ~102 languages; Runyankole and Acholi are not in the dataset. Expect empty / missing files. |
| JW300 | en-lg, en-sw, en-nyn, en-ach | `jw300/*.jsonl` (4 × 0 B) | FAILED | HF dataset `opus/JW300` is deprecated / no longer served. `download_jw300` catches the exception and writes an empty sentinel file — see `ml/scripts/data_aug/dataset_downloader.py:108-112`. |
| OPUS Tatoeba | en-lg, en-sw, en-nyn, en-ach | `opus/opus_Tatoeba_*.jsonl` (4 × 0 B) | FAILED | HF loader `opus/Tatoeba` is deprecated / unavailable. Same swallow-and-sentinel pattern at `dataset_downloader.py:179-182`. |
| Masakhane | en-lg, en-sw, en-nyn, en-ach | `masakhane/masakhane_*.jsonl` (4 × 0 B) | FAILED | `masakhane/lafand-mt` does not expose these pairs (tries reversed pair too, then gives up). See `dataset_downloader.py:257-271`. |
| Common Voice | lg | `Data/common_voice_lg/` | FAILED | `datasets.load_dataset('mozilla-foundation/common_voice_17_0', 'lg', …)` raises `EmptyDatasetError` — the HF repo now hosts only `README.md` / `.gitattributes`. Real audio must be pulled manually (Option B): download from <https://commonvoice.mozilla.org/en/datasets>, choose *Luganda*, extract into `Data/common_voice_lg/`. |

## Other stages in this session

- **TTS recording prompts**: generated 256 prompts/language for `lg`, `nyn`, `ach` under `Data/speech/tts/prompts/*.txt`.
- **`python -m ml.scripts.train_all --dry-run -v`**: orchestrator ran end-to-end, but several sub-scripts error with `unrecognized arguments` / missing required args (e.g. `export_tts_onnx.py` needs `--voice`; `quality_gates.py` doesn't accept `--results-dir` or `--dry-run`). These are pre-existing CLI-contract mismatches in `train_all.py` — tracked separately.

## Next steps (manual / out of scope here)

1. Accept the Common Voice license on mozilla.org and unpack to `Data/common_voice_lg/`.
2. Replace deprecated JW300 / OPUS Tatoeba loaders with current equivalents (e.g. `Helsinki-NLP/opus-100`, `allenai/nllb`, `facebook/flores`).
3. Resume FLEURS for `sw` (long-running ~1 GB audio cache).
4. Fix `train_all.py` CLI wiring so `--dry-run` exits cleanly for every stage.

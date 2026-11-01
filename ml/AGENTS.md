# ml/ (Offline Training, Corpus Pipelines & Local Speech Wrappers)

Owns offline corpus construction, RAG evaluation gates, and local on-device ASR/MT/TTS inference wrappers.

## Layout

| Path | Role |
| --- | --- |
| `ml/pipelines/` | Offline RAG evaluation (`evaluate_rag.py`), corpus coverage gate (`corpus_coverage.py`), data validation (`validate_data.py`), and quality gates (`quality_gates.py`) |
| `ml/scripts/data_aug/` | Corpus loaders, chunkers, deduplication, and provenance |
| `ml/scripts/data_aug/crawler.py`, `crawl_policy.py` | The live crawler and its pure rules: identity, scope, RFC 9309, host limits (ADR-0023). Run by `.github/workflows/scheduled-crawl.yml` from the hash lock in `requirements-crawl.txt` |
| `ml/scripts/lang_id.py` | Lingua-backed language detection (`en`, `lg`, `sw`, `nyn`, `ach`) |
| `ml/scripts/asr/` | Local Whisper / Whisper-SALT inference wrappers |
| `ml/scripts/mt/` | Local machine translation wrappers |
| `ml/scripts/tts/` | Local Piper / Spark-TTS-SALT inference wrappers |
| `ml/configs/` | Training configuration and `quality_baseline.json` |

## Boundary Invariants

- **Unidirectional Dependency Rule**: `ml/` must **never** import from `plugins.*`, and `ml/` → `app.*` imports are frozen to the three evaluation/MT bridges (`ml/pipelines/corpus_coverage.py`, `ml/scripts/eval_retrieval.py`, `ml/scripts/mt/infer_mt.py`).
- **Frozen Runtime Coupling**: The runtime (`App/backend/app`) may only import the four explicitly allow-listed modules in `App/backend/tests/test_runtime_package_boundary.py` (`KNOWN_ML_IMPORTERS`). Do not add new `from ml...` imports in `App/backend/app`.

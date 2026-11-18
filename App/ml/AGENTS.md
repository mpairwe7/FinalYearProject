# App/ml/ (Offline Training, Corpus Pipelines & Local Speech Wrappers)

Owns offline corpus construction, RAG evaluation gates, and local on-device ASR/MT/TTS inference wrappers.

## Layout

| Path | Role |
| --- | --- |
| `App/ml/pipelines/` | Offline RAG evaluation (`evaluate_rag.py`), corpus coverage gate (`corpus_coverage.py`), data validation (`validate_data.py`), and quality gates (`quality_gates.py`) |
| `App/ml/scripts/data_aug/` | Corpus loaders, chunkers, deduplication, and provenance |
| `App/ml/scripts/data_aug/crawler.py`, `crawl_policy.py` | The live crawler and its pure rules: identity, scope, RFC 9309, host limits (ADR-0023). Run by `.github/workflows/scheduled-crawl.yml` from the hash lock in `requirements-crawl.txt` |
| `App/ml/scripts/lang_id.py` | Lingua-backed language detection (`en`, `lg`, `sw`, `nyn`, `ach`) |
| `App/ml/scripts/asr/` | Local Whisper / Whisper-SALT inference wrappers |
| `App/ml/scripts/mt/` | Local machine translation wrappers |
| `App/ml/scripts/tts/` | Local Piper / Spark-TTS-SALT inference wrappers |
| `App/ml/configs/` | Training configuration and `quality_baseline.json` |

## Boundary Invariants

- **Unidirectional Dependency Rule**: `App/ml/` must **never** import from `plugins.*`, and `App/ml/` → `app.*` imports are frozen to the three evaluation/MT bridges (`App/ml/pipelines/corpus_coverage.py`, `App/ml/scripts/eval_retrieval.py`, `App/ml/scripts/mt/infer_mt.py`).
- **Frozen Runtime Coupling**: The runtime (`App/backend/app`) may only import the four explicitly allow-listed modules in `App/backend/tests/test_runtime_package_boundary.py` (`KNOWN_ML_IMPORTERS`). Do not add new `from ml...` imports in `App/backend/app`.

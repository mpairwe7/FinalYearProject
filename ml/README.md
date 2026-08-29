# ml/ — production quality gates & corpus tooling

This package used to be a full model-training pipeline. The training-only
tooling (data augmentation CLI, fine-tuning, quantization/mobile export,
model/data cards) was removed — model training now happens outside this
repository. What remains are the pieces `App/backend` and CI actually
depend on at runtime, plus the on-device speech inference wrappers
`speech_service.py` imports for its local backend tier.

## What's here

```
ml/
├── pipelines/
│   ├── corpus_coverage.py     # Issue #303 — taxpayer-question coverage gate
│   ├── evaluate_rag.py        # RAG evaluation (faithfulness, relevancy, etc.)
│   ├── evaluate_rag_offline.py # Offline RAG smoke eval (no Qdrant/LLM needed)
│   ├── quality_gates.py       # Combined production quality gate
│   ├── export_feedback.py     # Feedback → retriever tuning candidates
│   └── validate_data.py       # CSV FAQ dataset validation
├── scripts/
│   ├── lang_id.py             # EN/LG/SW language detection — baked into the
│   │                          # Crane Cloud image; see App/Dockerfile.cranecloud
│   ├── eval_retrieval.py      # Retrieval ranking gate (Hit@k / MRR)
│   ├── static_dense.py        # eval_retrieval.py's optional dense-comparison
│   │                          # arm; degrades to "unavailable" without
│   │                          # sentence-transformers (not installed in CI)
│   ├── asr/infer_asr.py       # Local Whisper/Sherpa ASR — speech_service.py's
│   │                          # local tier (asr/'s other scripts are dev-only:
│   │                          # ONNX export, model download, fine-tuning)
│   ├── mt/infer_mt.py         # Local MT inference — same local-tier role
│   ├── tts/infer_tts.py       # Local TTS synthesis — same local-tier role
│   ├── speech/speech_pipeline.py # Dev end-to-end ASR→MT→TTS pipeline runner
│   └── data_aug/              # Corpus ingest/clean/split package. App/backend's
│                              # pdf_corpus.py and crawl_corpus.py import
│                              # `chunkers` from here for offline corpus export.
└── configs/
    └── training_config.yaml   # RAG + production quality gate thresholds
```

## Why these files are load-bearing

- **`lang_id.py`** is imported at runtime by `App/backend/app/query.py` and
  `App/backend/app/speech_service.py`, and is explicitly copied into the
  Crane Cloud production image (see the comment in
  `App/Dockerfile.cranecloud` — dropping it silently degrades Luganda
  detection to a character heuristic).
- **`asr/infer_asr.py`, `mt/infer_mt.py`, `tts/infer_tts.py`** are imported
  lazily by `App/backend/app/speech_service.py` for its local/on-device
  ASR, MT, and TTS backend tier (guarded — the cloud/Sunbird tiers are
  tried first). Crane Cloud's production image does not ship these (no
  persistent storage for local models there), but the full checkout — what
  CI tests against — does, and `App/backend/tests/test_tts_backend_chain.py`
  and related tests assert the exact fallback behaviour when the local
  model file (not the module) is missing. The other scripts in `asr/`,
  `mt/`, `tts/` (ONNX export, model download, fine-tuning) are dev/training
  tooling, not imported by anything at runtime.
- **`data_aug/chunkers.py`** (plus its dependencies `text_utils.py`,
  `schema.py`, `provenance.py`, `loaders.py`, `dedup.py`, `quality.py`,
  `splitters.py`, `formatters.py`, `pipeline.py`) is imported lazily by
  `App/backend/app/pdf_corpus.py` and `crawl_corpus.py` for offline corpus
  export (`python -m app.indexer --export-pdf-jsonl --export-crawl-jsonl`).
  `data_aug/crawler.py` is the live crawler `.github/workflows/scheduled-crawl.yml`
  runs daily — not training tooling either.
- **`eval_retrieval.py`**, **`corpus_coverage.py`**, and
  **`evaluate_rag_offline.py`** run in CI (`.github/workflows/ci-ml-pipeline.yml`,
  `lint-and-test` job) as regression gates against `App/backend`'s retriever.
- **`evaluate_rag.py`** and **`quality_gates.py`** run in the `evaluate-rag`
  and `production-gates` CI jobs and are cited as compliance evidence in
  `governance/compliance_check.py` and `threat-model/validate_threats.py`.
- **`export_feedback.py`** turns user thumbs-down feedback into retriever
  tuning candidates (`docs/data-schema-and-eval.md`).

## Running the gates locally

```bash
# RAG evaluation (English)
python -m ml.pipelines.evaluate_rag --eval-set Data/eval/rag_eval.jsonl

# Offline RAG smoke eval (no Qdrant/LLM required)
python -m ml.pipelines.evaluate_rag_offline --eval-set Data/eval/rag_eval.jsonl

# Corpus coverage gate (issue #303)
python -m ml.pipelines.corpus_coverage --languages en --fail-under-floor

# Retrieval ranking gate
python -m ml.scripts.eval_retrieval --min-hit1 0.90

# Combined production quality gate
python -m ml.pipelines.quality_gates --family production \
  --config ml/configs/training_config.yaml \
  --rag-eval Results/rag_evaluation_results.json --soft-fail

# Dataset validation
python ml/pipelines/validate_data.py
```

## Corpus export (offline, needs this package installed)

```bash
python -m app.indexer --export-faq-jsonl --export-pdf-jsonl --export-crawl-jsonl
```

See `App/backend/app/pdf_corpus.py` and `crawl_corpus.py` for what each
export step needs from `data_aug/`.

# Model Card: LLM — Gemma-2-2B-it (URA fine-tune)

(Retroactive card for the existing LLM — the speech pipeline depends on it.)

## Summary

- **Base model**: `google/gemma-2-2b-it`
- **License**: Gemma Terms of Use (commercial use allowed with restrictions)
- **Parameters**: 2.6 B
- **Fine-tune**: LoRA (r=16/8 depending on target) via `peft`
- **On-device runtime**: MediaPipe LLM Inference API (GGUF Q4_K_M)
- **Training script**: `ml/scripts/fine_tune_gemma.py`
- **Export script**: `ml/scripts/export_mobile.py`

## Intended use

URA tax-assistant chat. Answers questions about Uganda tax law,
procedures, and URA services using retrieval-augmented generation on
the server and prompt-only on-device.

## Out-of-scope

- Medical, legal, or financial advice beyond Uganda tax basics.
- Personalised tax calculations for specific individuals (refuses and
  refers to URA).
- Languages other than English (Luganda is handled via MT bridge — see
  `mt_madlad400_lgen.md`).

## Training data

See `ml/scripts/data_aug/` — CSV FAQs, PDF corpus (teacher QA),
curated refusal examples, retrieval-format RAG samples.

## Evaluation

- RAG quality gates in `ml/configs/training_config.yaml::rag_quality_gates`.
- **2026 production gates** (`quality_gates.py --family production`):
  - Calibration: ECE <= 0.10, Brier <= 0.25 (`calibrate.py`)
  - Tokenizer: Luganda/English fertility ratio <= 1.8 (`audit_tokenizer.py`)
  - Benchmark: tokens/sec >= 8.0, TTFT p95 <= 1500 ms (`benchmark_inference.py`)
  - Mobile: GGUF size <= 1800 MB, SHA-256 present
  - Model card: 13 required sections validated (`generate_model_card.py`)
  - Per-language floors for faithfulness/numerical/law-citation accuracy
- Speech-mode gates inherited from the speech pipeline.

## Limitations

- Hallucination risk on tax rates and deadlines (mitigated by RAG
  grounding + refusal templates).
- Gemma-2 has no explicit `system` role; the system prompt is folded
  into the first user turn (see `fine_tune_gemma.py::_messages_to_gemma_text`).

## Safety

- Curated refusal set mixed into training (`schema.REFUSAL_TEMPLATES`).
- OWASP LLM Top 10 checks in the backend (`guardrails.py`).
- Red-team corpus for voice mode (`Data/eval/redteam_corpus.jsonl`).

## Authors / version

- URA Chatbot ML team — see `ml/scripts/export_mobile.py::PIPELINE_VERSION`.

# Model Card: MT Luganda/English — MADLAD-400 fine-tune

## Summary

- **Backbone**: `google/madlad400-3b-mt`
- **License**: Apache-2.0
- **Parameters**: 3 B (teacher) / ~60 M (distilled student)
- **Fine-tune**: LoRA r=8 (mobile) / r=32 (full) via `peft`
- **Runtime**: onnxruntime INT8 (student, mobile) / transformers (teacher, server)
- **Training script**: `ml/scripts/mt/finetune_mt.py` + `ml/scripts/mt/distill_mt.py`

## Intended use

Machine translation between English and Luganda for the URA Chatbot
speech pipeline. Bridges user input (any lang) to the English LLM and
bridges the LLM output back to the user's language for TTS.

## Out-of-scope

- Other African languages (MADLAD covers more, but only en/lg are
  fine-tuned + evaluated here).
- Document-level translation; this is sentence-level only.
- Long-form literary translation.

## Training data

| Source | License | Role |
|---|---|---|
| `Data/TTT/eng.lug.txt` (project corpus) | proprietary | gold |
| `Data/TTT/Luganda.csv` | proprietary | gold |
| `Data/TTT/luganda_wiki_corpus.txt` | proprietary (Wikipedia-derived, see data card) | backtranslation input |
| `Data/TTT/WordProject_Luganda_English_Corpus` | proprietary | gold |
| Backtranslated Lg->En | synthetic | augmentation (flagged `is_synthetic=True`) |

See `ml/docs/data_cards/mt_luganda_parallel.md`.

## Training procedure

1. Fine-tune teacher (MADLAD-400-3B) with LoRA on gold pairs.
2. Backtranslate monolingual Luganda text to produce synthetic pairs.
3. Re-fine-tune the teacher on gold + synthetic mix.
4. Distill teacher -> student (T5-small-class) via pseudo-labels.
5. Export student to ONNX INT8 via optimum.

Reproduce with:

```
python -m ml.scripts.mt.download_models
python -m ml.scripts.mt.finetune_mt
python -m ml.scripts.mt.backtranslate
python -m ml.scripts.mt.finetune_mt   # iteration 2 with synthetic
python -m ml.scripts.mt.distill_mt
python -m ml.scripts.mt.export_mt_onnx
```

## Evaluation

Thresholds (`mt_quality_gates` in `ml/configs/training_config.yaml`):

- min BLEU en->lg: 15.0
- min BLEU lg->en: 20.0
- min chrF en->lg: 0.35
- min chrF lg->en: 0.40
- max length-ratio deviation: 0.30
- max hallucination rate: 0.05
- min COMET-kiwi: 0.50 (optional)

Measured: TBD (populated by `ml/pipelines/evaluate_mt.py`).

## Limitations

- Low-resource pair; quality depends on corpus size + diversity.
- Named entities (person names, locations) are more error-prone.
- Tone / morphology: Luganda is agglutinative; the tokenizer may
  over-segment inflected verbs.
- Tax-domain terminology benefits from URA-specific fine-tune data.

## Safety

- Length-ratio guardrail refuses outputs > 3x source length (catches
  hallucinated run-ons).
- `mt.safety.max_length_ratio` + `max_repetition_ngram` in
  `ml/configs/speech_config.yaml`.

## Authors / version

- URA Chatbot ML team — 2026.1.0

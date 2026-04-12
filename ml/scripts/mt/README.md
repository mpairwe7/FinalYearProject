# MT Scripts (2026 Production)

Commercial-safe machine translation for Luganda <-> English.

## Commercial-safe model allowlist

| Model | License | Params | Role |
|---|---|---|---|
| `google/madlad400-3b-mt` | **Apache-2.0** | 3B | primary backbone (teacher) |
| `google/madlad400-7b-mt` | Apache-2.0 | 7B | optional server-side |
| `Helsinki-NLP/opus-mt-mul-en` | CC-BY-4.0 | 84M | fallback |
| `google-t5/t5-small` | Apache-2.0 | 60M | default distillation student |
| `google/gemma-2-2b-it` (prompted) | Gemma Terms (commercial OK) | 2B | last-resort fallback |

**NOT in allowlist** — explicitly avoided: `facebook/nllb-200-*`, `facebook/mms-*` (CC-BY-NC-4.0).

## Workflow

```
download_models.py            # fetch MADLAD-400 into artifacts/mt/models/
      |
      v
finetune_mt.py                # LoRA fine-tune teacher on Data/TTT/ parallel pairs
      |
      v
backtranslate.py              # synthesize Lg->En pairs from monolingual Luganda
      |
      v                       # (optional iterative round-trip)
distill_mt.py                 # teacher -> student distillation for mobile
      |
      v
export_mt_onnx.py             # optimum INT8 ONNX quantisation
      |
      v
ml/scripts/speech/export_mobile_speech.py   # copy into MobileApp assets
```

## Quick start (dry runs)

```bash
python -m ml.scripts.mt.download_models --dry-run
python -m ml.scripts.mt.finetune_mt --dry-run
python -m ml.scripts.mt.backtranslate --dry-run
python -m ml.scripts.mt.distill_mt --dry-run
python -m ml.scripts.mt.export_mt_onnx --dry-run
python -m ml.scripts.mt.infer_mt --text "Hello" --target-lang lg
```

## Inference backends (fallback chain)

1. **onnx** — INT8 quantised student (what ships to mobile)
2. **teacher** — fine-tuned MADLAD-400 (server path)
3. **base** — base MADLAD-400 (no fine-tune)
4. **prompted** — existing Gemma-2-2B with "Translate to ..." prompt (last resort)
5. **passthrough** — return the source unchanged; never hard-fails the request

## Quality gates (enforced by `ml/pipelines/evaluate_mt.py`)

```yaml
min_bleu_en_lg: 15.0
min_bleu_lg_en: 20.0
min_chrf_en_lg: 0.35
min_chrf_lg_en: 0.40
max_length_ratio_deviation: 0.30
max_hallucination_rate: 0.05
min_comet_kiwi: 0.50          # optional, needs unbabel-comet
```

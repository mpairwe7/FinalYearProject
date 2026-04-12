"""Machine Translation package (2026).

Commercial-safe MT pipeline for Luganda <-> English. The backbone is
MADLAD-400 (Apache-2.0) rather than NLLB-200 (CC-BY-NC) so the whole
pipeline stays commercially usable.

Sub-modules:
    download_models   — cache MADLAD-400 / Opus-MT
    finetune_mt       — LoRA fine-tune on project Luganda pairs
    distill_mt        — teacher -> student distillation for mobile
    backtranslate     — synthesize Lg->En pairs from monolingual Luganda
    export_mt_onnx    — ONNX export + INT8 quantisation via optimum
    infer_mt          — wrapper with lang-ID routing
"""

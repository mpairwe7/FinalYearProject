"""ASR package (2026 production).

Sub-modules:
    download_models   — cache pretrained ASR weights from HF Hub
    train_luganda     — LoRA fine-tune Whisper-small on Common Voice lg
    export_asr_onnx   — ONNX export + INT8 quantisation via optimum
    export_asr_sherpa — package ONNX into the sherpa-onnx on-device layout
    infer_asr         — Python wrapper (streaming + batch) around sherpa-onnx

Commercial-safe by design: Whisper is MIT. No CC-BY-NC dependencies.
"""

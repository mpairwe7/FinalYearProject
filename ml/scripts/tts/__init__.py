"""TTS package (2026 production).

Sub-modules:
    download_models      — cache Piper / Kokoro voices (MIT / Apache-2.0)
    train_luganda_vits   — Coqui VITS training scaffolding for custom Luganda voice
    export_tts_onnx      — package voices into sherpa-onnx on-device layout
    infer_tts            — Python wrapper (batch + streaming) around sherpa-onnx OfflineTts

Commercial-safe by design. MMS-TTS (CC-BY-NC) is explicitly NOT used.
"""

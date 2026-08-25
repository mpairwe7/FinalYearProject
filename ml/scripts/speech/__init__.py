"""Top-level speech orchestration scripts (2026).

Consumes artifacts from:
    * ml/scripts/asr/*        (Whisper ONNX packages)
    * ml/scripts/tts/*        (Piper / VITS ONNX packages)
    * ml/scripts/mt/*         (MADLAD-400 student ONNX)

And produces:
    * The mobile speech bundle (export_mobile_speech.py)
    * End-to-end pipeline runs for dev testing (speech_pipeline.py)
"""

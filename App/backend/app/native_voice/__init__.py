"""Native voice-to-voice engine — Phase 28 (2026).

Dual-path streaming architecture for sub-600ms end-to-end voice latency.
All components are feature-flag gated (``native_voice``, ``streaming_tts_v2``,
``speculative_prefetch``) and fall back gracefully to the V1 sentence-chunked
pipeline when disabled or when models are unavailable.
"""

__version__ = "0.1.0"

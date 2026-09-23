"""Configuration settings for the simulated AI phone receptionist."""

from __future__ import annotations

import os


def get_clarify_threshold() -> float:
    try:
        return float(os.getenv("RECEPTIONIST_CLARIFY_THRESHOLD", "0.55"))
    except ValueError:
        return 0.55


def get_max_clarify_attempts() -> int:
    try:
        return int(os.getenv("RECEPTIONIST_MAX_CLARIFY_ATTEMPTS", "2"))
    except ValueError:
        return 2


def get_transfer_timeout_s() -> float:
    try:
        return float(os.getenv("RECEPTIONIST_TRANSFER_TIMEOUT_S", "90"))
    except ValueError:
        return 90.0


def get_max_call_s() -> float:
    try:
        return float(os.getenv("RECEPTIONIST_MAX_CALL_S", "900"))
    except ValueError:
        return 900.0


def get_filler_after_ms() -> int:
    try:
        return int(os.getenv("RECEPTIONIST_FILLER_AFTER_MS", "450"))
    except ValueError:
        return 450


def get_max_spoken_sentences() -> int:
    try:
        return int(os.getenv("RECEPTIONIST_MAX_SPOKEN_SENTENCES", "3"))
    except ValueError:
        return 3


def get_tts_voice() -> str:
    return os.getenv("RECEPTIONIST_TTS_VOICE", "en-KE-AsiliaNeural").strip() or "en-KE-AsiliaNeural"


def live_partial_transcripts_enabled() -> bool:
    """Whether the caller sees interim transcripts of their own speech.

    On by default: without it the taxpayer's words only appear once the turn
    closes. Set ``RECEPTIONIST_LIVE_PARTIALS=false`` to spend the GPU on turn
    latency alone (a busy box, or a deployment whose ASR backend is a metered
    cloud API rather than the resident local model).
    """
    return os.getenv("RECEPTIONIST_LIVE_PARTIALS", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def get_partial_transcript_interval_s() -> float:
    """Seconds between interim re-decodes of the utterance in progress."""
    try:
        return float(os.getenv("RECEPTIONIST_PARTIAL_INTERVAL_S", "0.8"))
    except ValueError:
        return 0.8


def get_receptionist_engine() -> str:
    """The receptionist conversational engine: 'cascaded' (default) or 'gemini_live'."""
    return os.getenv("RECEPTIONIST_ENGINE", "cascaded").strip().lower()


def get_gemini_live_model() -> str:
    """The Gemini Live model ID for real-time bidirectional speech."""
    return (
        os.getenv("GEMINI_LIVE_MODEL", "models/gemini-2.5-flash-native-audio-latest").strip()
        or "models/gemini-2.5-flash-native-audio-latest"
    )


def get_gemini_live_voice() -> str:
    """Gemini Live voice identifier (Aoede, Charon, Fenrir, Kore, Puck)."""
    return os.getenv("GEMINI_LIVE_VOICE", "Aoede").strip() or "Aoede"

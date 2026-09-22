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
        return int(os.getenv("RECEPTIONIST_FILLER_AFTER_MS", "1000"))
    except ValueError:
        return 1000


def get_max_spoken_sentences() -> int:
    try:
        return int(os.getenv("RECEPTIONIST_MAX_SPOKEN_SENTENCES", "4"))
    except ValueError:
        return 4


def get_tts_voice() -> str:
    return os.getenv("RECEPTIONIST_TTS_VOICE", "en-KE-AsiliaNeural").strip() or "en-KE-AsiliaNeural"

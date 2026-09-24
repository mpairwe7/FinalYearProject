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


def get_gemini_vad_start_sensitivity() -> str:
    """How readily Gemini hears the caller start talking: ``high`` (default) or ``low``.

    High is what makes barge-in work: at ``low`` a caller talking over the
    greeting was not heard as speech and the greeting played to the end.
    """
    value = os.getenv("GEMINI_LIVE_START_SENSITIVITY", "high").strip().lower()
    return value if value in ("high", "low") else "high"


def get_gemini_vad_end_sensitivity() -> str:
    """How readily Gemini decides the caller has finished: ``high`` (default) or ``low``."""
    value = os.getenv("GEMINI_LIVE_END_SENSITIVITY", "high").strip().lower()
    return value if value in ("high", "low") else "high"


def get_gemini_vad_silence_ms() -> int:
    """Silence after which Gemini's own VAD ends the caller's turn."""
    return max(0, _env_int("GEMINI_LIVE_SILENCE_MS", 300))


def get_gemini_vad_prefix_padding_ms() -> int:
    """Speech Gemini must hear before it commits to a start of speech."""
    return max(0, _env_int("GEMINI_LIVE_PREFIX_PADDING_MS", 100))


# ---------------------------------------------------------------------------
# Multilingual receptionist (en / sw / lg) and language detection
# ---------------------------------------------------------------------------

#: Languages the receptionist can hold a call in, in the order they are offered.
KNOWN_LANGUAGES: tuple[str, ...] = ("en", "sw", "lg")
_ENGINES = ("gemini_live", "cascaded")
_DEFAULT_ENGINE_BY_LANGUAGE = {"en": "gemini_live", "sw": "gemini_live", "lg": "cascaded"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def get_default_language() -> str:
    """The language every call opens in (the greeting is always spoken in it)."""
    lang = os.getenv("RECEPTIONIST_DEFAULT_LANGUAGE", "en").strip().lower()
    return lang if lang in KNOWN_LANGUAGES else "en"


def get_languages() -> tuple[str, ...]:
    """Languages detection may switch a call into. Unknown codes are dropped."""
    raw = os.getenv("RECEPTIONIST_LANGUAGES", ",".join(KNOWN_LANGUAGES))
    langs = tuple(dict.fromkeys(p.strip().lower() for p in raw.split(",") if p.strip().lower() in KNOWN_LANGUAGES))
    default = get_default_language()
    if default not in langs:
        langs = (default, *langs)
    return langs


def get_engine_by_language() -> dict[str, str]:
    """``RECEPTIONIST_ENGINE_BY_LANGUAGE`` as ``{lang: engine}``.

    Format ``en:gemini_live,sw:gemini_live,lg:cascaded``. A malformed or
    unknown pair keeps that language's default rather than failing the call.
    Luganda is never routed to Gemini Live: Gemini does not speak it.
    """
    table = dict(_DEFAULT_ENGINE_BY_LANGUAGE)
    raw = os.getenv("RECEPTIONIST_ENGINE_BY_LANGUAGE", "")
    for pair in raw.split(","):
        lang, _, engine = pair.strip().lower().partition(":")
        if lang in KNOWN_LANGUAGES and engine in _ENGINES:
            table[lang] = engine
    table["lg"] = "cascaded"
    return table


def get_lid_method() -> str:
    """How the sentinel votes: ``salt_token`` (default), ``fusion`` or ``keyword``."""
    method = os.getenv("RECEPTIONIST_LID_METHOD", "salt_token").strip().lower()
    return method if method in ("salt_token", "fusion", "keyword") else "salt_token"


def get_lid_min_speech_s() -> float:
    """Utterances shorter than this never decide the language (greetings, "yes")."""
    return _env_float("RECEPTIONIST_LID_MIN_SPEECH_S", 1.5)


def get_lid_switch_confidence() -> float:
    """One vote at or above this switches a locked call."""
    return _env_float("RECEPTIONIST_LID_SWITCH_CONFIDENCE", 0.90)


def get_lid_hysteresis_confidence() -> float:
    """Below the switch confidence, votes at or above this accumulate."""
    return _env_float("RECEPTIONIST_LID_HYSTERESIS_CONFIDENCE", 0.70)


def get_lid_hysteresis_turns() -> int:
    """Votes for the new language, within the window, that switch a locked call."""
    return max(1, _env_int("RECEPTIONIST_LID_HYSTERESIS_TURNS", 2))


def get_lid_hysteresis_window() -> int:
    """How many of a locked call's latest content votes are weighed together."""
    return max(get_lid_hysteresis_turns(), _env_int("RECEPTIONIST_LID_HYSTERESIS_WINDOW", 3))


def get_lid_support_confidence() -> float:
    """The least confident vote that still counts towards moving a locked call."""
    return _env_float("RECEPTIONIST_LID_SUPPORT_CONFIDENCE", 0.50)


def get_lid_hold_timeout_ms() -> int:
    """Longest the first reply is held back while its language is decided."""
    return max(0, _env_int("RECEPTIONIST_LID_HOLD_TIMEOUT_MS", 600))


def get_lg_mix_threshold() -> float:
    """P(lg) above which Luganda function words in the text make the turn Luganda."""
    return _env_float("RECEPTIONIST_LG_MIX_THRESHOLD", 0.35)


def get_turn_timeout_s() -> float:
    """Silence after the caller's last word before the cascaded turn closes."""
    return _env_float("RECEPTIONIST_TURN_TIMEOUT_S", 1.0)


def local_barge_in_enabled() -> bool:
    """Whether the call's own VAD stops Gemini when the caller talks over it.

    Gemini's VAD decides first; this catches the callers it misses. Turn it
    off on a device whose speaker leaks into the microphone, where the
    assistant's own voice would read as the caller talking.
    """
    return _env_bool("RECEPTIONIST_LOCAL_BARGE_IN", True)


def get_barge_in_min_s() -> float:
    """Speech after the VAD's onset (itself 0.2 s) that counts as talking over the assistant."""
    return max(0.0, _env_float("RECEPTIONIST_BARGE_IN_MIN_S", 0.4))


def get_clarify_threshold_for(language: str) -> float:
    """``RECEPTIONIST_CLARIFY_THRESHOLD_<LANG>``, else the global threshold."""
    return _env_float(f"RECEPTIONIST_CLARIFY_THRESHOLD_{language.upper()}", get_clarify_threshold())


def clarify_repeat_enabled(language: str) -> bool:
    """Whether "please repeat that word" is asked in *language*.

    Off for Luganda by default: Whisper-SALT's per-word probabilities have
    not been calibrated on Luganda, where 14% WER on read speech means a low
    word score is the norm rather than a signal.
    """
    return _env_bool(f"RECEPTIONIST_CLARIFY_REPEAT_{language.upper()}", language != "lg")


def allow_edge_standin_lg() -> bool:
    """Whether a Luganda call may fall back to an English edge voice reading Luganda."""
    return _env_bool("RECEPTIONIST_ALLOW_EDGE_STANDIN_LG", False)

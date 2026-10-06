"""Configuration settings for the simulated AI phone receptionist."""

from __future__ import annotations

import math
import os
from urllib.parse import urlparse


class ReceptionistConfigError(ValueError):
    """A receptionist setting is present but cannot be used."""


def _positive_seconds(name: str, default: float) -> float:
    """Read a duration in seconds; unset or blank means *default*.

    A value that is set but not a finite positive number raises instead of
    quietly becoming the default: ``RECEPTIONIST_MAX_CALL_S=15m`` must not
    turn into a 15-minute limit, and ``0`` must not end every call at once.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ReceptionistConfigError(f"{name} must be a number of seconds, got {raw.strip()!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise ReceptionistConfigError(f"{name} must be a positive number of seconds, got {raw.strip()!r}")
    return value


def validate() -> None:
    """Raise :class:`ReceptionistConfigError` for settings that are set but unusable.

    Run at startup (and by the G36 production gate) so a bad value stops the
    boot instead of failing every call later.
    """
    get_max_call_s()


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
    """Longest a call may run, ``RECEPTIONIST_MAX_CALL_S`` (default 900 s).

    Raises :class:`ReceptionistConfigError` when the value is set but invalid.
    """
    return _positive_seconds("RECEPTIONIST_MAX_CALL_S", 900.0)


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


# ---------------------------------------------------------------------------
# Multilingual receptionist (en / sw / lg) and language detection
# ---------------------------------------------------------------------------

#: Languages the receptionist can hold a call in, in the order they are offered.
KNOWN_LANGUAGES: tuple[str, ...] = ("en", "sw", "lg")


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


def get_lid_method() -> str:
    """How the sentinel votes: ``salt_token`` (default), ``fusion`` or ``keyword``."""
    method = os.getenv("RECEPTIONIST_LID_METHOD", "salt_token").strip().lower()
    return method if method in ("salt_token", "fusion", "keyword") else "salt_token"


def get_lid_min_speech_s() -> float:
    """Utterances shorter than this never decide the language (greetings, "yes")."""
    return _env_float("RECEPTIONIST_LID_MIN_SPEECH_S", 1.5)


def get_memory_min_asr_confidence() -> float:
    """A call turn heard below this mean word probability is not written to
    long-term memory: a mishearing must not become a fact about the caller.
    Code-switched Luganda speech recognition is still error-prone (AfriSwitch,
    2026), and the memory's rule-based facts would otherwise keep the error."""
    return _env_float("RECEPTIONIST_MEMORY_MIN_ASR_CONF", 0.6)


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


def get_lg_mix_threshold() -> float:
    """P(lg) above which Luganda function words in the text make the turn Luganda."""
    return _env_float("RECEPTIONIST_LG_MIX_THRESHOLD", 0.35)


def get_turn_timeout_s() -> float:
    """Silence after the caller's last word before the cascaded turn closes."""
    return _env_float("RECEPTIONIST_TURN_TIMEOUT_S", 1.0)


def get_slow_pause_ms() -> int:
    """Silence after each sentence once a caller has asked the assistant to slow down."""
    return max(0, _env_int("RECEPTIONIST_SLOW_PAUSE_MS", 600))


def get_idle_reprompt_s() -> float:
    """Caller silence, after the assistant stops speaking, before it checks they are there.

    ``0`` turns silence handling off. The clock starts only when the assistant
    has finished speaking, so a slow answer never counts against the caller.
    """
    return max(0.0, _env_float("RECEPTIONIST_IDLE_REPROMPT_S", 12.0))


def get_idle_reprompts() -> int:
    """"Are you still there?" checks before a silent call is ended."""
    return max(0, _env_int("RECEPTIONIST_IDLE_REPROMPTS", 1))


def get_brief_every_turns() -> int:
    """Caller turns between rolling rebuilds of the officer's brief."""
    return max(1, _env_int("RECEPTIONIST_BRIEF_EVERY_TURNS", 3))


def get_claim_timeout_s() -> float:
    """How long an officer's claim holds a call before their audio connects."""
    return max(1.0, _env_float("RECEPTIONIST_CLAIM_TIMEOUT_S", 20.0))


def get_hold_update_s() -> float:
    """Seconds between "thank you for holding" lines while a caller waits for an officer.

    ``0`` turns them off. At the default 30 s a caller hears two in the
    ``RECEPTIONIST_TRANSFER_TIMEOUT_S`` (90 s) before the wait becomes a callback.
    """
    return max(0.0, _env_float("RECEPTIONIST_HOLD_UPDATE_S", 30.0))


def local_barge_in_enabled() -> bool:
    """Whether the call's own VAD stops the assistant when the caller talks over it.

    The sentinel hears the caller before Whisper has transcribed the two words
    the engine's own barge-in rule waits for. Turn it off on a device whose
    speaker leaks into the microphone, where the assistant's own voice would
    read as the caller talking.
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


def get_presence_ttl_s() -> float:
    """Seconds after last heartbeat before an officer is considered offline."""
    return max(5.0, _env_float("RECEPTIONIST_PRESENCE_TTL_S", 60.0))


def get_officer_reconnect_grace_s() -> float:
    """Seconds an officer has to reconnect to a dropped audio call before it re-queues."""
    return max(5.0, _env_float("RECEPTIONIST_OFFICER_RECONNECT_GRACE_S", 30.0))


def allow_edge_standin_lg() -> bool:
    """Whether a Luganda call may fall back to an English edge voice reading Luganda."""
    return _env_bool("RECEPTIONIST_ALLOW_EDGE_STANDIN_LG", False)


def local_engine_errors() -> list[str]:
    """Why production calls could not run on the local engine; empty when they can.

    Every call language is heard by Whisper-SALT, answered by the local LLM
    and voiced by the Orpheus sidecar. Without them a call cannot hear or
    answer at all, or its voice falls to Spark-TTS-SALT (several seconds a
    sentence) or to edge-tts (a cloud service) — fallbacks, not a primary.
    """
    enabled = os.getenv("FLAG_VOICE_RECEPTIONIST", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return []
    errors: list[str] = []
    if os.getenv("SPEECH_ENABLED", "true").strip().lower() != "true":
        errors.append("G36: SPEECH_ENABLED must be true: calls are heard and voiced by the local speech models.")
    if os.getenv("LLM_ENABLED", "true").strip().lower() != "true":
        errors.append("G36: LLM_ENABLED must be true: calls are answered by the local LLM.")
    url = urlparse(os.getenv("ORPHEUS_TTS_URL", "").strip())
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
        errors.append("G36: ORPHEUS_TTS_URL must be the Orpheus voice sidecar's http(s) URL.")
        return errors
    from ..orpheus_tts import speaker_for

    unvoiced = [lang for lang in get_languages() if speaker_for(lang) is None]
    if unvoiced:
        errors.append(
            "G36: ORPHEUS_TTS_LANGUAGES must include every call language; missing " + ", ".join(unvoiced) + "."
        )
    return errors

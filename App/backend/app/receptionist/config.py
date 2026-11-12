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


#: Durations a call depends on: ``name: (default, lowest, highest)`` in seconds.
#: Only the call ceiling used to be checked, so a transfer timeout of ``0`` made
#: every transfer "officers are busy", ``nan`` hung the wait until the ceiling
#: and ``inf`` held a caller for good (G596).
_DURATIONS: dict[str, tuple[float, float, float]] = {
    "RECEPTIONIST_TRANSFER_TIMEOUT_S": (90.0, 5.0, 1800.0),
    "RECEPTIONIST_CLAIM_TIMEOUT_S": (20.0, 1.0, 300.0),
    "RECEPTIONIST_OFFICER_RECONNECT_GRACE_S": (30.0, 5.0, 300.0),
    "RECEPTIONIST_PRESENCE_TTL_S": (60.0, 5.0, 3600.0),
    # Up to Pipecat's own 20 s turn backstop and beyond: values that worked
    # before validation existed still work.
    "RECEPTIONIST_TURN_TIMEOUT_S": (1.0, 0.05, 30.0),
    "RECEPTIONIST_HOLD_UPDATE_S": (30.0, 0.0, 600.0),
    "RECEPTIONIST_IDLE_REPROMPT_S": (12.0, 0.0, 600.0),
    "RECEPTIONIST_PARTIAL_INTERVAL_S": (0.8, 0.05, 10.0),
    "RECEPTIONIST_REVIEW_ANSWER_TIMEOUT_S": (10.0, 1.0, 120.0),
    "RECEPTIONIST_BARGE_IN_MIN_S": (0.4, 0.0, 10.0),
}


def _duration(name: str, *, strict: bool = False) -> float:
    """The duration setting *name*, in seconds.

    Unset or blank is the default. A value that is set but unusable (not a
    number, not finite, outside its range) is the default too, so a call that
    is already running never reads it; with *strict* it raises instead, which
    is how :func:`validate` stops such a deployment from taking calls.
    """
    default, low, high = _DURATIONS[name]
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if math.isfinite(value) and low <= value <= high:
        return value
    if strict:
        raise ReceptionistConfigError(
            f"{name} must be a number of seconds from {low:g} to {high:g}, got {raw.strip()!r}"
        )
    return default


def validate() -> None:
    """Raise :class:`ReceptionistConfigError` for settings that are set but unusable.

    Run at startup (and by the G36 production gate) so a bad value keeps the
    receptionist from taking calls instead of failing every call later.
    """
    get_max_call_s()
    for name in _DURATIONS:
        _duration(name, strict=True)
    raw_attempts = os.getenv("RECEPTIONIST_MAX_CLARIFY_ATTEMPTS", "").strip()
    if raw_attempts:
        try:
            usable = 0 <= int(raw_attempts) <= 10
        except ValueError:
            usable = False
        if not usable:
            raise ReceptionistConfigError(
                f"RECEPTIONIST_MAX_CLARIFY_ATTEMPTS must be a whole number from 0 to 10, got {raw_attempts!r}"
            )


def get_clarify_threshold() -> float:
    try:
        return float(os.getenv("RECEPTIONIST_CLARIFY_THRESHOLD", "0.55"))
    except ValueError:
        return 0.55


def get_max_clarify_attempts() -> int:
    try:
        attempts = int(os.getenv("RECEPTIONIST_MAX_CLARIFY_ATTEMPTS", "2"))
    except ValueError:
        return 2
    return attempts if 0 <= attempts <= 10 else 2


def get_transfer_timeout_s() -> float:
    """How long a caller waits for an officer before the wait becomes a callback."""
    return _duration("RECEPTIONIST_TRANSFER_TIMEOUT_S")


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
    return _duration("RECEPTIONIST_PARTIAL_INTERVAL_S")


# ---------------------------------------------------------------------------
# Multilingual receptionist (en / sw / lg) and language detection
# ---------------------------------------------------------------------------

#: Languages the receptionist can hold a call in, in the order they are offered.
KNOWN_LANGUAGES: tuple[str, ...] = ("en", "sw", "lg", "nyn", "ach")


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


def call_language(value: object) -> str | None:
    """*value* as a language this deployment's calls can be held in, or ``None``.

    The one rule for a caller pinning the call's language from the screen,
    on the WebSocket path and the LiveKit control socket alike: the LiveKit
    loop once accepted only en/sw/lg, so Runyankole and Acholi callers'
    choices were dropped there (G182).
    """
    language = str(value or "").strip().lower()
    return language if language in get_languages() else None


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
    return _duration("RECEPTIONIST_TURN_TIMEOUT_S")


def get_slow_pause_ms() -> int:
    """Silence after each sentence once a caller has asked the assistant to slow down."""
    return max(0, _env_int("RECEPTIONIST_SLOW_PAUSE_MS", 600))


def get_idle_reprompt_s() -> float:
    """Caller silence, after the assistant stops speaking, before it checks they are there.

    ``0`` turns silence handling off. The clock starts only when the assistant
    has finished speaking, so a slow answer never counts against the caller.
    """
    return _duration("RECEPTIONIST_IDLE_REPROMPT_S")


def get_idle_reprompts() -> int:
    """"Are you still there?" checks before a silent call is ended."""
    return max(0, _env_int("RECEPTIONIST_IDLE_REPROMPTS", 1))


def get_brief_every_turns() -> int:
    """Caller turns between rolling rebuilds of the officer's brief."""
    return max(1, _env_int("RECEPTIONIST_BRIEF_EVERY_TURNS", 3))


def get_audio_bytes_per_s() -> int:
    """Audio a call socket may carry, in bytes a second (G304; the burst is two seconds' worth).

    The browser sends 32 000 B/s of PCM16 at 16 kHz, so the default of 64 000
    leaves room for a throttled tab's burst. A value below one real-time
    stream would drop a normal call's audio, so it is raised to 32 000.
    """
    return max(32_000, _env_int("RECEPTIONIST_AUDIO_BYTES_PER_S", 64_000))


def get_claim_timeout_s() -> float:
    """How long an officer's claim holds a call before their audio connects."""
    return _duration("RECEPTIONIST_CLAIM_TIMEOUT_S")


def get_hold_update_s() -> float:
    """Seconds between "thank you for holding" lines while a caller waits for an officer.

    ``0`` turns them off. At the default 30 s a caller hears two in the
    ``RECEPTIONIST_TRANSFER_TIMEOUT_S`` (90 s) before the wait becomes a callback.
    """
    return _duration("RECEPTIONIST_HOLD_UPDATE_S")


def get_call_review_enabled() -> bool:
    """Whether the AI asks the caller "was this call helpful?" (at goodbye, or while transferring).

    Asked at most once a call and only after the AI has answered something
    (review.py). Off, no call is asked and the AI-calls audit stays empty.
    """
    return _env_bool("RECEPTIONIST_CALL_REVIEW", True)


def get_review_answer_timeout_s() -> float:
    """How long the AI waits for the answer to its goodbye review question before saying goodbye."""
    return _duration("RECEPTIONIST_REVIEW_ANSWER_TIMEOUT_S")


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
    return _duration("RECEPTIONIST_BARGE_IN_MIN_S")


def get_clarify_threshold_for(language: str) -> float:
    """``RECEPTIONIST_CLARIFY_THRESHOLD_<LANG>``, else the global threshold."""
    return _env_float(f"RECEPTIONIST_CLARIFY_THRESHOLD_{language.upper()}", get_clarify_threshold())


def clarify_repeat_enabled(language: str) -> bool:
    """Whether "please repeat that word" is asked in *language*.

    Off for Luganda, Runyankole, and Acholi by default: Whisper-SALT's per-word
    probabilities have not been calibrated on those Ugandan languages, where a
    low word score is the norm rather than a signal.
    """
    return _env_bool(
        f"RECEPTIONIST_CLARIFY_REPEAT_{language.upper()}",
        language not in ("lg", "nyn", "ach"),
    )


def get_presence_ttl_s() -> float:
    """Seconds after last heartbeat before an officer is considered offline."""
    return _duration("RECEPTIONIST_PRESENCE_TTL_S")


def get_officer_reconnect_grace_s() -> float:
    """Seconds an officer has to reconnect to a dropped audio call before it re-queues."""
    return _duration("RECEPTIONIST_OFFICER_RECONNECT_GRACE_S")


def allow_edge_standin_lg() -> bool:
    """Whether a Luganda call may fall back to an English edge voice reading Luganda."""
    return _env_bool("RECEPTIONIST_ALLOW_EDGE_STANDIN_LG", False)


#: Every variable an image may start its workers from. The gate read only
#: ``WORKERS``; the supervisord image starts uvicorn from ``UVICORN_WORKERS``
#: (default 2), so ``WORKERS=1`` passed the gate with two workers running (G593).
WORKER_VARIABLES: tuple[str, ...] = ("WORKERS", "UVICORN_WORKERS", "WEB_CONCURRENCY")


def worker_settings() -> dict[str, str]:
    """The worker-count variables that are set, as written."""
    return {name: os.environ[name].strip() for name in WORKER_VARIABLES if os.getenv(name, "").strip()}


def single_worker_declared() -> bool:
    """One process owns every call: a worker count is set, and every one that is set says 1."""
    settings = worker_settings()
    return bool(settings) and all(value == "1" for value in settings.values())


def sole_process_declared() -> bool:
    """This process is the deployment's only one: one worker, and one replica.

    Call rooms live in the process, so "no room here" means "no live call"
    only where nothing else can be holding it. An unset worker count proves
    nothing (an entrypoint may default it without exporting it), so this is
    true only when the deployment says so: a worker variable of 1 and
    ``VOICE_RECEPTIONIST_REPLICAS=1``, both of which the production gate requires.
    """
    return single_worker_declared() and os.getenv("VOICE_RECEPTIONIST_REPLICAS", "").strip() == "1"


def worker_errors() -> list[str]:
    """Why this deployment is not known to run one worker; empty when it is.

    Call rooms, the staff lobby, claims and admission caps live in the
    process: with two workers an officer's console and a caller's call land in
    different ones about half the time.
    """
    settings = worker_settings()
    if not settings:
        return ["G36: explicitly set WORKERS=1 while active call state remains process-local."]
    errors: list[str] = []
    for name, value in settings.items():
        if value == "1":
            continue
        if name == "WORKERS":
            errors.append("G36: explicitly set WORKERS=1 while active call state remains process-local.")
        else:
            errors.append(
                f"G36: {name}={value} starts more than one worker; set it to 1 while active call "
                "state remains process-local."
            )
    return errors


def local_engine_errors(*, enabled_override: bool | None = None) -> list[str]:
    """Why production calls could not run on the local engine; empty when they can.

    Every call language is heard by Whisper-SALT, answered by the local LLM
    and voiced by the Orpheus sidecar. Without them a call cannot hear or
    answer at all, or its voice falls to Spark-TTS-SALT (several seconds a
    sentence) or to edge-tts (a cloud service) — fallbacks, not a primary.

    *enabled_override* is for a caller that already knows the flag's state: a
    flag switched on through the admin API or a persisted override is not in
    the environment, and reading only that skipped these checks (G597).
    """
    enabled = enabled_override if enabled_override is not None else os.getenv(
        "FLAG_VOICE_RECEPTIONIST", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}
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

"""Simulated AI phone receptionist package (Pipecat demo).

The receptionist is an optional feature of the API. When it cannot start, the
API still does: calls are refused, :func:`status` says why, and ``/ready``
reports it (ADR-0041). It used to re-raise out of the application's startup,
which is how a missing Pipecat layer took the whole service down for eight
hours on 2026-10-31.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import time
import threading
from typing import Any

logger = logging.getLogger(__name__)

_state_lock = threading.Lock()
#: Why calls cannot be taken on this process; "" when they can.
_unavailable_reason = ""
#: :func:`start` has run in this process (whatever it found).
_started = False


def _register_scrape_hook() -> None:
    try:
        from ..analytics import metrics

        metrics.on_scrape(refresh_gauge)
    except Exception:  # noqa: BLE001 - metrics never decide whether calls start
        logger.debug("Could not register the receptionist gauge refresh", exc_info=True)


def is_available() -> bool:
    """Return True if Pipecat runtime dependencies are installed."""
    return importlib.util.find_spec("pipecat") is not None


def mark_unavailable(reason: str) -> None:
    """Calls cannot be taken, for *reason*; every call socket is refused until :func:`mark_available`."""
    global _unavailable_reason, _started
    with _state_lock:
        _unavailable_reason = (reason or "unavailable").strip()
        _started = True
    _gauge(0)


def mark_available() -> None:
    global _unavailable_reason, _started
    with _state_lock:
        _unavailable_reason = ""
        _started = True
    _gauge(1)


def reset() -> None:
    """Forget what the last start found (application shutdown): the next start decides afresh."""
    global _unavailable_reason, _started
    with _state_lock:
        _unavailable_reason = ""
        _started = False


def started() -> bool:
    """Whether :func:`start` has run in this process.

    It runs at application startup when calls are switched on. Switched on
    later, by an admin override, nothing has checked the stack or ended the
    rows a previous process left, so production refuses calls until a restart.
    """
    return _started


def unavailable_reason() -> str:
    """Why calls cannot be taken; "" when they can (or have not been asked to start)."""
    return _unavailable_reason


def status() -> str:
    """``off``, ``ready`` or ``unavailable: <why>``, for the readiness probe."""
    from ..flags import flags

    if not flags.is_enabled("voice_receptionist"):
        return "off"
    if not started():
        return "unavailable: switched on after the API started; restart the API to take calls"
    reason = unavailable_reason()
    return f"unavailable: {reason}" if reason else "ready"


def refresh_gauge() -> None:
    """``receptionist_available`` from the flag and the start, as they are now.

    0 while calls are switched on and cannot be taken (the start failed, or
    never ran because the flag was switched on later); 1 otherwise, the flag
    switched off included. Set only at start, it went on paging after calls
    were switched off, and had no value for a flag switched on later. Run at
    every scrape.
    """
    _gauge(0 if status().startswith("unavailable") else 1)


def _gauge(value: int) -> None:
    try:
        from ..analytics import metrics

        # The lowest live worker's value: with two workers, one that cannot
        # take calls must not be hidden by the other at the next scrape.
        metrics.set_gauge_min("receptionist_available", float(value))
    except Exception:  # noqa: BLE001 - a metric never decides whether calls start
        logger.debug("Could not set the receptionist availability gauge", exc_info=True)


def start(*, speech_model: Any) -> str:
    """Bring the receptionist up on this process. Returns "" when it can take calls, else why not.

    Never raises: whatever stops it is recorded (:func:`mark_unavailable`),
    logged at error, and left for the readiness probe and the alert on
    ``receptionist_available`` to surface.
    """
    try:
        reason = _start(speech_model)
    except Exception as exc:  # noqa: BLE001 - an optional feature must not stop the API
        logger.exception("Voice receptionist could not start; calls are unavailable")
        reason = f"{type(exc).__name__}: {exc}"[:200]
    if reason:
        logger.error("Voice receptionist is unavailable: %s", reason)
        mark_unavailable(reason)
    else:
        mark_available()
    return reason


def _warn_about_resume(workers: dict[str, str]) -> None:
    """Say so when call-drop resume is switched on and cannot do what it says."""
    from ..flags import flags
    from .low_bandwidth import resume_can_be_issued

    if not flags.is_enabled("call_drop_resume"):
        return
    if not resume_can_be_issued():
        logger.warning(
            "call_drop_resume is on but RECEPTIONIST_RESUME_SECRET is not set: no resume ticket will be issued"
        )
    if any(value != "1" for value in workers.values()):
        logger.warning(
            "call_drop_resume is on with more than one worker: a ticket is single-use within a process only"
        )


#: Retries of a stale-call sweep the database refused, and the wait before each.
_SWEEP_RETRY_DELAYS_S: tuple[float, ...] = (30.0, 60.0, 120.0, 300.0)


def _end_stale_calls(before: float, attempt: int = 0) -> None:
    """End the rows a previous process left live, and count them.

    Every one of them when this process is declared the only worker of the
    only replica (it has just started, so it owns no calls); otherwise only
    those older than a call can be, because another process may be carrying
    the rest. Done whether or not this process can take calls: a restart that
    lost Pipecat used to leave its predecessor's calls live, and their callers
    off the callback list. Only calls that began before *before* (this
    start) are touched, and a sweep the database refused is tried again.
    """
    from . import config, store, telemetry

    try:
        ended = store.end_stale_calls(everything=config.sole_process_declared(), before=before)
    except Exception:  # noqa: BLE001 - the sweep never decides whether calls start
        if attempt >= len(_SWEEP_RETRY_DELAYS_S):
            logger.error("Gave up ending the call rows a previous process left live", exc_info=True)
            return
        delay = _SWEEP_RETRY_DELAYS_S[attempt]
        logger.warning("Could not end the call rows a previous process left live; retrying in %gs", delay, exc_info=True)
        # Later, with the same boundary: a call this process has started by
        # then is never the sweep's.
        retry = threading.Timer(delay, _end_stale_calls, args=(before, attempt + 1))
        retry.daemon = True
        retry.start()
        return
    if ended:
        telemetry.calls_lost(ended)
        logger.warning("Ended %d call row(s) a previous process left live", ended)


def _start(speech_model: Any) -> str:
    from . import config, store, telemetry

    # Counted from zero whatever this start finds: increase() misses a series
    # that first appears at 1, and the refusals are what fire the alerts.
    telemetry.declare()
    try:
        store.init_receptionist_schema(strict=True)
    except Exception as exc:  # noqa: BLE001
        return f"the call tables could not be created ({type(exc).__name__})"
    _end_stale_calls(before=time.time())
    if not is_available():
        return "FLAG_VOICE_RECEPTIONIST is on but Pipecat is not installed"
    try:
        config.validate()
    except config.ReceptionistConfigError as exc:
        return str(exc)
    if os.getenv("APP_ENV", "development").strip().lower() == "production":
        # The G36 gate: checked here rather than at API startup, so a
        # deployment that fails it serves the chat and refuses calls.
        from .livekit import production_errors

        gate = production_errors(enabled_override=True) + config.local_engine_errors(enabled_override=True)
        if gate:
            return "; ".join(gate)
    if speech_model is None:
        # Admitted anyway, a caller was greeted in text, never heard, and
        # hung up on as idle, with the record blaming them (G594).
        return "no speech model: a call could neither hear nor speak"
    workers = config.worker_settings()
    if any(value != "1" for value in workers.values()):
        logger.warning(
            "Voice receptionist is on with %s. Call rooms, the lobby and claims live in the "
            "process: a handoff reaches the right worker only by chance.",
            ", ".join(f"{name}={value}" for name, value in workers.items()),
        )
    _warn_about_resume(workers)
    return ""


_register_scrape_hook()

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
import threading
from typing import Any

logger = logging.getLogger(__name__)

_state_lock = threading.Lock()
#: Why calls cannot be taken on this process; "" when they can.
_unavailable_reason = ""


def is_available() -> bool:
    """Return True if Pipecat runtime dependencies are installed."""
    return importlib.util.find_spec("pipecat") is not None


def mark_unavailable(reason: str) -> None:
    """Calls cannot be taken, for *reason*; every call socket is refused until :func:`mark_available`."""
    global _unavailable_reason
    with _state_lock:
        _unavailable_reason = (reason or "unavailable").strip()
    _gauge(0)


def mark_available() -> None:
    global _unavailable_reason
    with _state_lock:
        _unavailable_reason = ""
    _gauge(1)


def reset() -> None:
    """Forget what the last start found (application shutdown): the next start decides afresh."""
    global _unavailable_reason
    with _state_lock:
        _unavailable_reason = ""


def unavailable_reason() -> str:
    """Why calls cannot be taken; "" when they can (or have not been asked to start)."""
    return _unavailable_reason


def status() -> str:
    """``off``, ``ready`` or ``unavailable: <why>``, for the readiness probe."""
    from ..flags import flags

    if not flags.is_enabled("voice_receptionist"):
        return "off"
    reason = unavailable_reason()
    return f"unavailable: {reason}" if reason else "ready"


def _gauge(value: int) -> None:
    try:
        from ..analytics import metrics

        metrics.set_gauge("receptionist_available", float(value))
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


def _start(speech_model: Any) -> str:
    from . import config, store, telemetry

    if not is_available():
        return "FLAG_VOICE_RECEPTIONIST is on but Pipecat is not installed"
    try:
        config.validate()
    except config.ReceptionistConfigError as exc:
        return str(exc)
    try:
        store.init_receptionist_schema(strict=True)
    except Exception as exc:  # noqa: BLE001
        return f"the call tables could not be created ({type(exc).__name__})"
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
    telemetry.declare()
    _warn_about_resume(workers)
    # Rows a previous process left live. Every one of them when this process
    # is declared the only worker of the only replica (it has just started,
    # so it owns no calls); otherwise only those older than a call can be,
    # because another process may be carrying the rest.
    ended = store.end_stale_calls(everything=config.sole_process_declared())
    if ended:
        telemetry.calls_lost(ended)
        logger.warning("Ended %d call row(s) a previous process left live", ended)
    return ""

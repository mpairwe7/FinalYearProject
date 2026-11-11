"""What the call path reports to Prometheus (G595).

A call could fail at every stage and export nothing: the recognizer, the
voice and the media pipeline each logged and carried on, and no alert rule
named a call series. Every label here is a closed vocabulary of code
constants, so a value from a request or a provider can never add a series.

Model failures on a call are already counted by ``chat_turns_total`` with
``channel="call"`` and ``outcome="error"``; they are not counted again here.
"""

from __future__ import annotations

from ..analytics import metrics

#: Why a call ended. ``process_lost`` is written by the startup sweep, the
#: rest by :meth:`CallRoom.end`.
END_REASONS: tuple[str, ...] = (
    "normal",
    "caller_hangup",
    "caller_voice_hangup",
    "caller_idle",
    "caller_media_disconnected",
    "officer_hangup",
    "officer_ended",
    "officer_terminated_waiting",
    "timeout",
    "max_call_duration",
    "speech_failed",
    "media_pipeline_unavailable",
    "media_pipeline_stopped",
    "media_revocation_failed",
    "process_lost",
)
#: Where a call failed: the recognizer, the voice, the media pipeline, or a
#: write of the call's own record.
FAILURE_STAGES: tuple[str, ...] = ("stt", "tts", "media", "record")
#: Why a call was refused before it began.
REFUSALS: tuple[str, ...] = ("unavailable", "not_configured", "capacity")
_OTHER = "other"


def _known(value: str, vocabulary: tuple[str, ...]) -> str:
    return value if value in vocabulary else _OTHER


def declare() -> None:
    """Create every series at zero and keep its label values distinct.

    ``increase()`` sees nothing in a series that first appears at 1, so
    without this the first failure after a restart would not count toward
    its alert.
    """
    metrics.reserve("receptionist_calls_ended_total", "reason", (*END_REASONS, _OTHER))
    metrics.reserve("receptionist_call_failures_total", "stage", (*FAILURE_STAGES, _OTHER))
    metrics.reserve("receptionist_calls_refused_total", "reason", (*REFUSALS, _OTHER))
    for reason in (*END_REASONS, _OTHER):
        metrics.inc("receptionist_calls_ended_total", 0, labels={"reason": reason})
    for stage in (*FAILURE_STAGES, _OTHER):
        metrics.inc("receptionist_call_failures_total", 0, labels={"stage": stage})
    for reason in (*REFUSALS, _OTHER):
        metrics.inc("receptionist_calls_refused_total", 0, labels={"reason": reason})
    metrics.inc("receptionist_calls_started_total", 0)
    metrics.inc("receptionist_calls_lost_total", 0)


def call_started() -> None:
    metrics.inc("receptionist_calls_started_total")


def call_ended(reason: str) -> None:
    metrics.inc("receptionist_calls_ended_total", labels={"reason": _known(reason, END_REASONS)})


def failure(stage: str) -> None:
    metrics.inc("receptionist_call_failures_total", labels={"stage": _known(stage, FAILURE_STAGES)})


def refused(reason: str) -> None:
    metrics.inc("receptionist_calls_refused_total", labels={"reason": _known(reason, REFUSALS)})


def calls_lost(count: int) -> None:
    """Call rows a start found live with no process behind them."""
    if count > 0:
        metrics.inc("receptionist_calls_lost_total", count)

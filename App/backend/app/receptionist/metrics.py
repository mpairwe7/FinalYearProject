"""Performance and operational metrics for phone receptionist calls."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .. import database as db
from .state import CallState
from .store import get_call, list_turns, update_call

logger = logging.getLogger(__name__)


def _percentile(values: list[float], pct: float) -> float:
    """Compute empirical percentile."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    k = (len(sorted_v) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_v) - 1)
    d = k - f
    return round(sorted_v[f] + d * (sorted_v[c] - sorted_v[f]), 1)


def compute_call_metrics(
    state: CallState | None,
    call: dict[str, Any],
    turns: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute comprehensive performance metrics for one call."""
    started_at = call.get("started_at", time.time())
    ended_at = call.get("ended_at") or time.time()
    duration_s = max(0.0, round(ended_at - started_at, 1))

    caller_turns = sum(1 for t in turns if t.get("speaker") == "caller")
    ai_answers = sum(1 for t in turns if t.get("speaker") == "assistant" and t.get("kind") == "answer")

    clarify_turns = [t for t in turns if t.get("kind") in ("clarify", "confirm")]
    clarifications_asked = len(clarify_turns)
    clarification_failures = state.clarification_failures if state else 0
    clarified_first_try = state.clarified_first_try if state else max(0, clarifications_asked - clarification_failures)

    low_conf_words = sum(len(t.get("low_conf_words", [])) for t in turns)
    probs: list[float] = []
    if state and state.word_probs:
        probs = state.word_probs
    else:
        for t in turns:
            m = t.get("mean_word_prob")
            if m is not None:
                probs.append(float(m))
    mean_word_prob = round(sum(probs) / len(probs), 3) if probs else 1.0

    barge_ins = state.barge_in_count if state else 0

    # Faithfulness
    f_scores: list[float] = []
    if state and state.faithfulness_scores:
        f_scores = state.faithfulness_scores
    else:
        for t in turns:
            fs = t.get("faithfulness")
            if fs is not None:
                f_scores.append(float(fs))
    faithfulness_mean = round(sum(f_scores) / len(f_scores), 3) if f_scores else None
    faithfulness_min = round(min(f_scores), 3) if f_scores else None

    # Transfer metrics
    transferred = bool(call.get("transferred"))
    transfer_reason = call.get("transfer_reason") or ""
    time_to_transfer_s = None
    officer_wait_s = None
    if transferred and state and state.transfer_requested_at:
        time_to_transfer_s = max(0.0, round(state.transfer_requested_at - started_at, 1))
        # Officer wait time
        officer_joined_at = None
        for t in turns:
            if t.get("speaker") == "officer":
                officer_joined_at = t.get("created_at")
                break
        if officer_joined_at:
            officer_wait_s = max(0.0, round(officer_joined_at - state.transfer_requested_at, 1))

    # Containment
    end_reason = call.get("end_reason", "")
    contained = bool(ai_answers >= 1 and not transferred and end_reason != "timeout")

    # Latencies
    stt_latencies: list[float] = []
    brain_latencies: list[float] = []
    tts_latencies: list[float] = []
    turn_to_audio_latencies: list[float] = []

    for t in turns:
        lat = t.get("latencies", {})
        if "stt_ms" in lat:
            stt_latencies.append(float(lat["stt_ms"]))
        if "brain_ms" in lat:
            brain_latencies.append(float(lat["brain_ms"]))
        if "tts_first_ms" in lat:
            tts_latencies.append(float(lat["tts_first_ms"]))
        if "total_ms" in lat:
            turn_to_audio_latencies.append(float(lat["total_ms"]))
        elif "brain_ms" in lat and "stt_ms" in lat:
            turn_to_audio_latencies.append(float(lat["brain_ms"]) + float(lat["stt_ms"]))

    metrics_obj = {
        "duration_s": duration_s,
        "caller_turns": caller_turns,
        "ai_answers": ai_answers,
        "clarifications_asked": clarifications_asked,
        "clarified_first_try": clarified_first_try,
        "clarification_failures": clarification_failures,
        "low_conf_words": low_conf_words,
        "mean_word_prob": mean_word_prob,
        "barge_ins": barge_ins,
        "faithfulness_mean": faithfulness_mean,
        "faithfulness_min": faithfulness_min,
        "transferred": transferred,
        "transfer_reason": transfer_reason,
        "time_to_transfer_s": time_to_transfer_s,
        "officer_wait_s": officer_wait_s,
        "contained": contained,
        "latency": {
            "stt_ms_p50": _percentile(stt_latencies, 50),
            "stt_ms_p95": _percentile(stt_latencies, 95),
            "brain_ms_p50": _percentile(brain_latencies, 50),
            "brain_ms_p95": _percentile(brain_latencies, 95),
            "tts_first_ms_p50": _percentile(tts_latencies, 50),
            "tts_first_ms_p95": _percentile(tts_latencies, 95),
            "turn_to_audio_ms_p50": _percentile(turn_to_audio_latencies, 50),
            "turn_to_audio_ms_p95": _percentile(turn_to_audio_latencies, 95),
        },
    }
    return metrics_obj


def record_call_end_metrics(call_id: str, state: CallState | None = None) -> dict[str, Any]:
    """Calculate and save final metrics JSON for a call."""
    call = get_call(call_id)
    if not call:
        return {}

    turns = list_turns(call_id)
    metrics = compute_call_metrics(state, call, turns)
    update_call(call_id, metrics_json=metrics)
    return metrics


def get_aggregate_metrics(days: int = 7) -> dict[str, Any]:
    """Compute aggregate call performance across the specified day window."""
    cutoff = time.time() - (max(1, days) * 86400)
    rows = db.query_all(
        "SELECT * FROM voice_calls WHERE started_at >= ? ORDER BY started_at DESC",
        (cutoff,),
    )

    total_calls = len(rows)
    if total_calls == 0:
        return {
            "period_days": days,
            "total_calls": 0,
            "containment_rate": 0.0,
            "transfer_rate": 0.0,
            "transfers_by_reason": {},
            "clarification_rate": 0.0,
            "clarification_first_try_rate": 0.0,
            "mean_word_prob": 1.0,
            "avg_duration_s": 0.0,
            "avg_officer_rating": 0.0,
            "latency_p50_ms": 0.0,
            "latency_p95_ms": 0.0,
        }

    contained_count = 0
    transferred_count = 0
    transfers_by_reason: dict[str, int] = {}
    clarify_calls = 0
    first_try_total = 0
    clarify_total = 0
    word_probs: list[float] = []
    durations: list[float] = []
    turn_latencies: list[float] = []
    ratings: list[int] = []

    for r in rows:
        d = dict(r)
        m = {}
        if d.get("metrics_json"):
            try:
                m = json.loads(d["metrics_json"])
            except Exception:
                pass

        if m.get("contained"):
            contained_count += 1
        if d.get("transferred"):
            transferred_count += 1
            reason = d.get("transfer_reason") or "unspecified"
            transfers_by_reason[reason] = transfers_by_reason.get(reason, 0) + 1

        c_asked = m.get("clarifications_asked", 0)
        if c_asked > 0:
            clarify_calls += 1
            clarify_total += c_asked
            first_try_total += m.get("clarified_first_try", 0)

        if m.get("mean_word_prob") is not None:
            word_probs.append(float(m["mean_word_prob"]))
        if m.get("duration_s") is not None:
            durations.append(float(m["duration_s"]))

        lat_p50 = (m.get("latency") or {}).get("turn_to_audio_ms_p50")
        if lat_p50:
            turn_latencies.append(float(lat_p50))

        if d.get("officer_rating"):
            ratings.append(int(d["officer_rating"]))

    containment_rate = round(contained_count / total_calls, 3)
    transfer_rate = round(transferred_count / total_calls, 3)
    clarification_rate = round(clarify_calls / total_calls, 3)
    clarification_first_try_rate = (
        round(first_try_total / clarify_total, 3) if clarify_total > 0 else 1.0
    )
    mean_word_prob = round(sum(word_probs) / len(word_probs), 3) if word_probs else 1.0
    avg_duration_s = round(sum(durations) / len(durations), 1) if durations else 0.0
    avg_officer_rating = round(sum(ratings) / len(ratings), 2) if ratings else 0.0

    return {
        "period_days": days,
        "total_calls": total_calls,
        "containment_rate": containment_rate,
        "transfer_rate": transfer_rate,
        "transfers_by_reason": transfers_by_reason,
        "clarification_rate": clarification_rate,
        "clarification_first_try_rate": clarification_first_try_rate,
        "mean_word_prob": mean_word_prob,
        "avg_duration_s": avg_duration_s,
        "avg_officer_rating": avg_officer_rating,
        "latency_p50_ms": _percentile(turn_latencies, 50),
        "latency_p95_ms": _percentile(turn_latencies, 95),
    }

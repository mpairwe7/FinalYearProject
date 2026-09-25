"""Call Desk Phase 3: Live call risk signals and queue marker detection.

Evaluates each caller turn for risk factors (docs/plans/officer-call-desk-plan.md §6.6):
- ≥ 2 clarifications
- repeated question (normalized similarity ≥ 0.8 with an earlier caller turn)
- distress (text_signals.detect_user_distress or sentiment)
- mean word probability < 0.5 on at least 2 turns
- abstentions
- duration > 6 min (360 s)

Risk level is 'none' | 'watch' | 'at_risk'.
Persists to ``voice_calls.risk_json`` and publishes lobby ``call.risk`` on change.
"""

from __future__ import annotations

import difflib
import json
import logging
import time
from typing import Any

from ..text_signals import detect_user_distress
from .config import get_barge_in_min_s
from .hub import hub
from .state import registry
from .store import get_call, list_turns, register_turn_observer, unregister_turn_observer, update_call

logger = logging.getLogger(__name__)


def _text_similarity(a: str, b: str) -> float:
    norm_a = " ".join((a or "").lower().split())
    norm_b = " ".join((b or "").lower().split())
    if not norm_a or not norm_b:
        return 0.0
    return difflib.SequenceMatcher(None, norm_a, norm_b).ratio()


def evaluate_call_risk(call_id: str) -> dict[str, Any]:
    """Compute current risk level and signals for a live or ended call."""
    call = get_call(call_id)
    if not call:
        return {"level": "none", "signals": [], "evaluated_at": time.time()}

    turns = list_turns(call_id)
    now = time.time()
    started_at = float(call.get("started_at") or now)
    duration_s = max(0.0, now - started_at)

    signals: list[str] = []

    # 1. Clarifications >= 2
    clarification_turns = [t for t in turns if t.get("kind") in ("clarify", "confirm")]
    if len(clarification_turns) >= 2:
        signals.append("multiple_clarifications")

    # 2. Repeated question (similarity >= 0.8)
    caller_texts = [
        str(t.get("text") or "").strip()
        for t in turns
        if t.get("speaker") == "caller" and t.get("kind") == "utterance" and len(str(t.get("text") or "").strip()) > 8
    ]
    repeated = False
    for i in range(len(caller_texts)):
        for j in range(i + 1, len(caller_texts)):
            if _text_similarity(caller_texts[i], caller_texts[j]) >= 0.8:
                repeated = True
                break
        if repeated:
            break
    if repeated:
        signals.append("repeated_question")

    # 3. Distress
    distress_detected = False
    for text in caller_texts:
        if detect_user_distress(text) in ("frustration", "hardship", "anxiety"):
            distress_detected = True
            break
    brief = call.get("brief") or {}
    if isinstance(brief, dict) and brief.get("sentiment") in ("frustrated", "distressed"):
        distress_detected = True
    if distress_detected:
        signals.append("distress")

    # 4. Low word confidence twice (< 0.5)
    low_prob_turns = [
        t for t in turns
        if t.get("speaker") == "caller"
        and t.get("mean_word_prob") is not None
        and float(t["mean_word_prob"]) < 0.5
    ]
    if len(low_prob_turns) >= 2:
        signals.append("low_word_confidence")

    # 5. Abstentions
    abstention_turns = [
        t for t in turns
        if t.get("kind") == "abstention"
        or "cannot advise" in str(t.get("text") or "").lower()
        or "unable to answer" in str(t.get("text") or "").lower()
    ]
    if len(abstention_turns) >= 1:
        signals.append("abstention")

    # 6. Duration > 6 min (360 s)
    if duration_s > 360.0:
        signals.append("excessive_duration")

    # Determine risk level
    if len(signals) >= 2 or "distress" in signals:
        level = "at_risk"
    elif len(signals) == 1:
        level = "watch"
    else:
        level = "none"

    result = {
        "level": level,
        "signals": signals,
        "evaluated_at": now,
        "duration_s": round(duration_s, 1),
    }

    # Persist if changed
    current_risk_str = call.get("risk_json")
    new_risk_str = json.dumps(result)
    try:
        prev_level = json.loads(current_risk_str).get("level", "none") if current_risk_str else "none"
    except Exception:
        prev_level = "none"

    if prev_level != level or current_risk_str != new_risk_str:
        update_call(call_id, risk_json=new_risk_str)
        hub.publish_lobby(
            "call.risk",
            {
                "call_id": call_id,
                "level": level,
                "signals": signals,
            },
        )

    return result


class RiskObserver:
    """Observer attached to live call rooms."""

    def __init__(self, call_id: str) -> None:
        self.call_id = call_id

    def observe(self, turn: dict[str, Any]) -> None:
        if turn.get("call_id") != self.call_id:
            return
        # Run synchronous evaluation
        try:
            evaluate_call_risk(self.call_id)
        except Exception:
            logger.debug("Failed evaluating risk for call %s", self.call_id, exc_info=True)


_observers: dict[str, RiskObserver] = {}


def start(call_id: str) -> None:
    """Begin monitoring risk signals for a call."""
    if call_id in _observers:
        return
    observer = RiskObserver(call_id)
    _observers[call_id] = observer
    register_turn_observer(call_id, observer.observe)


def stop(call_id: str) -> None:
    """Stop monitoring risk signals for a call."""
    observer = _observers.pop(call_id, None)
    if observer is not None:
        unregister_turn_observer(call_id, observer.observe)

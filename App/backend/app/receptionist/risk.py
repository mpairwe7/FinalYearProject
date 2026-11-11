"""Call Desk Phase 3: Live call risk signals and queue marker detection.

Evaluates each caller turn for risk factors (docs/plans/officer-call-desk-plan.md §6.6):
- ≥ 2 clarifications
- repeated question (normalized similarity ≥ 0.8 with an earlier caller turn)
- distress (text_signals.detect_user_distress or sentiment)
- mean word probability < 0.5 on at least 2 turns
- abstentions
- duration > 6 min (360 s)

Risk level is 'none' | 'watch' | 'at_risk'.
Persists to ``voice_calls.risk_json`` and publishes lobby ``call.risk`` when the
level or the signals change, not on every turn.

A live call is evaluated in a worker thread, one evaluation at a time: every
turn is written from the event loop that carries every call's audio, and the
evaluation reads the whole transcript.
"""

from __future__ import annotations

import asyncio
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


#: How far back a repeated question is looked for, in caller turns. It bounds
#: the work on a long call (comparing every pair cost 0.7 s a turn by the
#: sixtieth) and loses nothing a caller would call a repeat: the same question
#: forty turns on is a new conversation.
_REPEAT_LOOKBACK = 40


def _repeated_question(texts: list[str], start: int = 0) -> bool:
    """Whether a caller turn repeats one of the turns shortly before it (similarity ≥ 0.8).

    Only turns from *start* on are tested (against the turns before them): a
    live call's observer passes how many it has already been through, so each
    new turn costs at most ``_REPEAT_LOOKBACK`` comparisons however long the
    call has run.
    """
    normalised = [" ".join((t or "").lower().split()) for t in texts]
    matcher = difflib.SequenceMatcher(None)
    for index in range(max(0, start), len(normalised)):
        later = normalised[index]
        if not later:
            continue
        matcher.set_seq2(later)
        for earlier in normalised[max(0, index - _REPEAT_LOOKBACK):index]:
            if not earlier:
                continue
            matcher.set_seq1(earlier)
            # The two quick ratios are cheap upper bounds on the real one.
            if (
                matcher.real_quick_ratio() >= 0.8
                and matcher.quick_ratio() >= 0.8
                and matcher.ratio() >= 0.8
            ):
                return True
    return False


class RepeatScan:
    """What a live call's observer remembers between turns: a transcript only grows."""

    def __init__(self) -> None:
        self.seen = 0
        self.repeated = False

    def check(self, texts: list[str]) -> bool:
        if len(texts) < self.seen:
            # Turns were removed (retention): start again from what is left.
            self.seen, self.repeated = 0, False
        if not self.repeated:
            self.repeated = _repeated_question(texts, start=self.seen)
        self.seen = len(texts)
        return self.repeated


def evaluate_call_risk(call_id: str, scan: RepeatScan | None = None) -> dict[str, Any]:
    """Compute current risk level and signals for a live or ended call.

    *scan* is the observer's memory of the turns it has been through; without
    one the whole transcript is read for a repeated question.
    """
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
    if scan.check(caller_texts) if scan is not None else _repeated_question(caller_texts):
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

    # 3b. Acoustic Prosody Frustration / Distress (Phase 33, 2026)
    try:
        from ..flags import flags

        room = registry.get(call_id)
        if room is not None and flags.is_enabled("emotion_aware_prosody", subject=getattr(room.state, "user_id", "") or None):
            affect = getattr(room.state, "caller_affect", "calm")
            if affect in ("frustrated", "urgent_distress") and "distress" not in signals:
                signals.append("distress")
    except Exception:
        logger.debug("Acoustic risk signal evaluation skipped", exc_info=True)

    # 4. Low word confidence twice (< 0.5)
    low_prob_turns = [
        t for t in turns
        if t.get("speaker") == "caller"
        and t.get("mean_word_prob") is not None
        and float(t["mean_word_prob"]) < 0.5
    ]
    if len(low_prob_turns) >= 2:
        signals.append("low_word_confidence")

    # 5. Abstentions (across all 5 supported languages: en, lg, sw, nyn, ach)
    _ABSTENTION_MARKERS = (
        "cannot advise",
        "unable to answer",
        "i don't have enough information",
        "sisobola kukolera",
        "sirina bukakafu",
        "siwezi kuchakata",
        "sina uhakika",
        "tindikubaasa kukora",
        "tihaine buhamizo",
        "pe atwero tic",
        "pe atye ki mok",
    )
    abstention_turns = [
        t for t in turns
        if t.get("kind") == "abstention"
        or any(m in str(t.get("text") or "").lower() for m in _ABSTENTION_MARKERS)
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

    # Persist and tell the officers only when the level or the signals change.
    # The record also carries when it was evaluated, so comparing it whole
    # wrote the row and published an event on every turn of every call.
    try:
        previous = json.loads(call.get("risk_json") or "{}")
    except (TypeError, ValueError):
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    if previous.get("level", "none") != level or list(previous.get("signals") or []) != signals:
        update_call(call_id, risk_json=json.dumps(result))
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
    """Re-evaluates a live call's risk when a turn is written, off the event loop.

    Turns arrive from the loop (the brain) and from worker threads (the
    transcript taps). With a loop, an evaluation runs in a thread and at most
    one at a time; turns written meanwhile are covered by one more run after
    it. Without one (a script, a synchronous test) there is nothing to stall
    and it runs inline.
    """

    def __init__(self, call_id: str, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self.call_id = call_id
        self.loop = loop
        self._task: asyncio.Task[None] | None = None
        self._dirty = False
        self._stopped = False
        self._scan = RepeatScan()

    def observe(self, turn: dict[str, Any]) -> None:
        if turn.get("call_id") != self.call_id or self._stopped:
            return
        loop = self.loop
        if loop is None or not loop.is_running():
            self._evaluate()
            return
        self._dirty = True
        loop.call_soon_threadsafe(self._request)

    def _request(self) -> None:
        if self._stopped or (self._task is not None and not self._task.done()):
            return
        assert self.loop is not None
        self._task = self.loop.create_task(self._run())

    async def _run(self) -> None:
        while self._dirty and not self._stopped:
            self._dirty = False
            await asyncio.to_thread(self._evaluate)

    def _evaluate(self) -> None:
        try:
            evaluate_call_risk(self.call_id, self._scan)
        except Exception:
            logger.debug("Failed evaluating risk for call %s", self.call_id, exc_info=True)

    def busy(self) -> bool:
        """A turn has been written that the stored risk does not reflect yet."""
        return self._dirty or (self._task is not None and not self._task.done())

    def cancel(self) -> None:
        self._stopped = True
        self._dirty = False


_observers: dict[str, RiskObserver] = {}


def start(call_id: str) -> None:
    """Begin monitoring risk signals for a call."""
    if call_id in _observers:
        return
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    observer = RiskObserver(call_id, loop)
    _observers[call_id] = observer
    register_turn_observer(call_id, observer.observe)


def stop(call_id: str) -> None:
    """Stop monitoring risk signals for a call."""
    observer = _observers.pop(call_id, None)
    if observer is not None:
        observer.cancel()
        unregister_turn_observer(call_id, observer.observe)


def pending(call_id: str) -> bool:
    """Whether *call_id* has a turn its stored risk does not reflect yet."""
    observer = _observers.get(call_id)
    return observer is not None and observer.busy()


async def settled(call_id: str, timeout: float = 1.0) -> None:
    """Wait, briefly, until the stored risk reflects every turn written so far.

    For a decision that reads it right after a turn (whether to offer an
    at-risk caller an officer). Gives up quietly at *timeout*: the risk read
    then is the previous turn's, which is what every reader saw before.
    """
    deadline = time.monotonic() + timeout
    while pending(call_id) and time.monotonic() < deadline:
        await asyncio.sleep(0.005)

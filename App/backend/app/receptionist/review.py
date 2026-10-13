"""The caller's spoken review of the AI: "was this call helpful?"

The administrator audits the AI on calls from what callers say when asked
(docs/plans/admin-service-quality-audit-plan.md §D1). The AI asks at most once
a call, and only after it has answered something:

* **at goodbye** (``call_ai_review_end``) — "Before you go, was this call
  helpful to you?", then thanks and hangs up;
* **while transferring** (``call_ai_review_transfer``) — "I'm connecting you
  to a URA officer now. While you wait, were my answers helpful so far?"; the
  transfer is already under way, so the question never delays the officer.

The answer is classified here, deterministically — no model call, no added
latency — as positive, neutral, negative or unclear, and stored with the
transcript of what was said in ``service_ratings``. A rating is written when
it is asked for, so a caller who hangs up, or is connected to an officer
before answering, is a ``no_answer`` rather than a gap (:func:`settle`).
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Final

from .. import service_ratings
from ..text_signals import names_a_task
from .clarify import classify_yes_no
from .config import get_call_review_enabled

if TYPE_CHECKING:
    from .state import CallRoom, CallState

logger = logging.getLogger(__name__)

#: When the review is asked → the rating source it is stored under.
SOURCE_FOR: Final[dict[str, str]] = {
    "asking_end": "call_ai_review_end",
    "asking_transfer": "call_ai_review_transfer",
}

#: A review question is waiting for its answer.
ASKING: Final[frozenset[str]] = frozenset(SOURCE_FOR)


def _words(*items: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(items) + r")\b", re.IGNORECASE)


#: Declining to say ("no thanks") is not an opinion of the call.
_DECLINE = _words(
    r"no,?\s+thank(?:s|\s+you)", r"not\s+sure", r"(?:i\s+)?(?:don'?t|do\s+not)\s+know",
    r"nedda,?\s+w?ee?bale", r"hapana,?\s+asante", r"sijui", r"simanyi",
)
#: Negated criticism reads as faint praise: "not bad".
_NEUTRAL_NEGATED = _words(r"not\s+(?:too\s+|that\s+|so\s+)?bad", r"si\s+mbaya", r"si\s+kibi")
#: Negation first, so "not helpful" never reads as "helpful".
_NEGATIVE = _words(
    r"not\s+(?:really|much|at\s+all)",
    r"not\s+(?:very\s+|really\s+|that\s+|so\s+|too\s+)?(?:helpful|useful|good|clear|great)",
    r"(?:was|is|were)(?:n'?t|\s+not)\s+(?:very\s+|really\s+|that\s+)?(?:helpful|useful|good|clear)",
    r"(?:did|does)(?:n'?t|\s+not)\s+(?:really\s+)?(?:help|answer|work)",
    r"no\s+help", r"unhelpful", r"useless", r"waste\s+of\s+(?:my\s+)?time", r"terrible", r"awful",
    r"bad", r"poor", r"rubbish", r"confusing", r"hopeless", r"disappointed",
    # Luganda: it did not help me, not good, bad.
    r"tekiyambye", r"tekinnyambye", r"tekyannyamba", r"tekiyamba", r"tewannyambye", r"si\s+kirungi", r"kibi",
    # Swahili: it did not help (me), not good, bad, useless.
    r"haikusaidia", r"haijanisaidia", r"hukunisaidia", r"hujanisaidia", r"si\s+nzuri", r"mbaya", r"haifai",
)
#: A partial yes: some of it helped.
_NEUTRAL = _words(
    r"so[\s-]so", r"a\s+(?:bit|little)", r"partly", r"partially", r"kind\s+of", r"sort\s+of", r"somewhat",
    r"more\s+or\s+less", r"half",
    r"kitono", r"mu\s+kigero",
    r"kiasi", r"kidogo",
)
_POSITIVE = _words(
    r"helpful", r"useful", r"(?:very\s+)?good", r"great", r"excellent", r"perfect", r"brilliant", r"wonderful",
    r"helped", r"thank(?:s|\s+you)", r"appreciate(?:d)?", r"satisfied", r"happy", r"clear", r"amazing",
    r"kiyambye", r"kinnyambye", r"nnyambye", r"kirungi", r"bulungi", r"nnyo", r"w?ee?bale", r"nsanyuse",
    r"imesaidia", r"imenisaidia", r"umenisaidia", r"nzuri", r"vizuri", r"asante", r"nimeridhika", r"safi",
)
#: "Okay" alone is lukewarm; with a yes or a thanks it is not counted here.
_WEAK_NEUTRAL = _words(r"ok(?:ay)?", r"fine", r"alright", r"all\s+right", r"average", r"fair", r"kale")

#: A review answer that also asks something new is answered, not hung up on.
_ASKS = re.compile(
    r"\?|\b(?:how|what|when|where|which|why|can\s+i|could\s+you|do\s+i|should\s+i|is\s+it|i\s+(?:also\s+)?(?:want|need))\b"
    r"|\b(?:ntya|ddi|wa\s+ki|vipi|nini|lini|wapi|je)\b",
    re.IGNORECASE,
)


def classify_spoken_review(text: str, language: str = "en") -> str:
    """``positive``, ``neutral``, ``negative`` or ``unclear`` for a spoken answer.

    English words are always recognised (a Luganda caller answering "yes,
    very helpful" is common), and Luganda and Swahili ones on top.
    """
    clean = (text or "").strip()
    if not clean or _DECLINE.search(clean):
        return "unclear"
    if _NEUTRAL_NEGATED.search(clean):
        return "neutral"
    if _NEGATIVE.search(clean):
        return "negative"
    if _NEUTRAL.search(clean):
        return "neutral"
    if _POSITIVE.search(clean):
        return "positive"
    yes_no = classify_yes_no(clean, language)
    if yes_no == "yes":
        return "positive"
    if yes_no == "no":
        return "negative"
    if _WEAK_NEUTRAL.search(clean):
        return "neutral"
    return "unclear"


def asks_something(text: str) -> bool:
    """The answer carries a new question as well ("yes — and how do I pay?")."""
    return len(text.split()) > 3 and bool(_ASKS.search(text)) and names_a_task(text)


def is_review_answer(text: str, language: str = "en") -> bool:
    """The turn answers the review question, rather than raising something new.

    An opinion is an answer; so is a word or two that says nothing ("mmm",
    "bye"). Anything longer that names a task is a new question.
    """
    if classify_spoken_review(text, language) != "unclear":
        return True
    return len(text.split()) <= 2 or not names_a_task(text)


def should_ask(state: CallState) -> bool:
    """Ask once a call, and only after the AI has answered something."""
    return get_call_review_enabled() and not state.review_state and state.ai_answers_count >= 1


def ask(room: CallRoom, moment: str) -> None:
    """Record that the review was asked (*moment*: ``asking_end`` or ``asking_transfer``)."""
    state = room.state
    state.review_state = moment
    state.review_asked_at = time.time()
    try:
        service_ratings.request(
            SOURCE_FOR[moment],
            call_id=room.call_id,
            channel="call",
            subject="ai",
            tenant_id=state.tenant_id or "default",
            conversation_id=state.conversation_id or "",
            topic=state.topic or "",
            locale=state.locale or "",
            user_id=state.user_id or "",
        )
    except Exception:
        logger.warning("Could not record the review question on call %s", room.call_id, exc_info=True)


def _count(source: str, label: str) -> None:
    try:
        from ..analytics import metrics

        metrics.inc("service_rating_total", labels={"source": source, "sentiment": label})
    except Exception:
        logger.debug("service_rating_total not counted", exc_info=True)


def record(room: CallRoom, text: str, language: str, *, status: str = "answered") -> str:
    """Store the caller's answer to the pending review; returns its sentiment."""
    state = room.state
    source = SOURCE_FOR.get(state.review_state)
    if source is None:
        return ""
    sentiment = classify_spoken_review(text, language) if status == "answered" else ""
    state.review_state = "done"
    if state.review_timer_task is not None and not state.review_timer_task.done():
        state.review_timer_task.cancel()
    try:
        if service_ratings.answer(
            source,
            call_id=room.call_id,
            status=status,
            sentiment=sentiment,
            comment=text if status == "answered" else "",
        ):
            _count(source, sentiment or status)
    except Exception:
        logger.warning("Could not store the review answer on call %s", room.call_id, exc_info=True)
    return sentiment


def settle(room: CallRoom, status: str = "no_answer") -> None:
    """Close a review still waiting for its answer (the caller left, or an officer came first).

    Idempotent: only a rating still pending changes, so calling this from
    every way a call can end is safe.
    """
    state = room.state
    if state.review_timer_task is not None and not state.review_timer_task.done():
        state.review_timer_task.cancel()
    if state.review_state in ASKING:
        state.review_state = "done"
    for source in SOURCE_FOR.values():
        try:
            if service_ratings.answer(source, call_id=room.call_id, status=status):
                _count(source, status)
        except Exception:
            logger.debug("Could not settle the %s review on call %s", source, room.call_id, exc_info=True)

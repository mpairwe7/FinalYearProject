"""When a person should be offered, and how fast they should answer (Phase 38).

The text channel escalated on retrieval confidence alone
(``OutputGuard.should_escalate``): faithfulness and hits. Distress across turns
added a "Talk to an officer" button, and a taxpayer who pressed it got a ticket
with ``topic: general`` and ``priority: normal`` — behind everyone else, with the
officer opening cold. The voice channel already weighed more
(``receptionist/risk.py``).

This module is the one place that weighs the four things that should decide it:

* **affect** — how the taxpayer feels (``text_signals.detect_user_distress``),
  and whether it has lasted across turns (``distress_trajectory``);
* **stakes** — what is riding on it (``text_signals.detect_high_stakes``):
  enforcement, a deadline today, a large amount;
* **effort** — how long they have been at it;
* **confidence** — whether the assistant could answer at all.

It returns a tier — ``none``, ``offer`` (a person is offered, with the
reason) or ``priority`` (offered first; the ticket is ``high`` or ``urgent``
with a shorter SLA) — and the reasons as codes. It never escalates by itself:
a person is offered, the taxpayer decides. The crisis path keeps its own
handling. Deterministic and offline: same input, same output.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .text_signals import (
    detect_crisis,
    detect_high_stakes,
    detect_user_distress,
    distress_trajectory,
    strip_stakes,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

TIER_NONE = "none"
TIER_OFFER = "offer"
TIER_PRIORITY = "priority"

#: Affect kinds that make stakes urgent. Confusion is not one of them: it
#: wants a better explanation, not a faster queue.
_DISTRESSED = frozenset({"hardship", "frustration", "anxiety", "urgency"})
_PRIORITY_ORDER = ("low", "normal", "high", "urgent")
#: Retrieval modes that mean the assistant could not answer the question.
_UNANSWERED_MODES = frozenset({"abstained", "false_premise_rejected", "out_of_scope"})


def _env_int(name: str, default: int, *, floor: int) -> int:
    try:
        return max(floor, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def offer_after_turns() -> int:
    """Turns after which a still-unhappy conversation is offered a person."""
    return _env_int("ESCALATION_OFFER_TURNS", 6, floor=2)


def stakes_amount_threshold_ugx() -> float:
    """An amount at issue from which a distressed taxpayer is put ahead of the queue."""
    return float(_env_int("ESCALATION_STAKES_AMOUNT_UGX", 10_000_000, floor=0))


def sla_seconds(priority: str, *, hardship: bool = False) -> int:
    """Target officer response for a ticket of *priority* (as the envelope's SLA)."""
    if priority == "urgent":
        return 30
    if hardship:
        return _env_int("EQ_HARDSHIP_SLA_TARGET_SECONDS", 45, floor=15)
    if priority == "high":
        return 60
    return 120


def higher_priority(first: str, second: str) -> str:
    """The more urgent of two ticket priorities (unknown values count as normal)."""
    rank = {name: i for i, name in enumerate(_PRIORITY_ORDER)}
    return first if rank.get(first, 1) >= rank.get(second, 1) else second


@dataclass(frozen=True)
class EscalationAssessment:
    """Whether to offer a person, how urgently, and why — as codes."""

    tier: str = TIER_NONE
    priority: str = "normal"
    sla_seconds: int = 120
    affect: str = ""
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def offers_person(self) -> bool:
        return self.tier in (TIER_OFFER, TIER_PRIORITY)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "priority": self.priority,
            "sla_seconds": self.sla_seconds,
            "affect": self.affect,
            "reasons": list(self.reasons),
        }


def assess_escalation(
    message: str,
    *,
    history: Sequence[str] = (),
    result: Mapping[str, Any] | None = None,
) -> EscalationAssessment:
    """Weigh affect, stakes, effort and confidence for this turn.

    *history* is the taxpayer's earlier messages in the conversation, oldest
    first; *result* the turn's result when there is one (its retrieval mode
    and faithfulness say whether the assistant could answer).
    """
    text = message or ""
    if detect_crisis(text):
        # The crisis path answers with the crisis lines and offers a person;
        # a ticket raised from it goes to the front.
        return EscalationAssessment(
            tier=TIER_PRIORITY, priority="urgent", sla_seconds=sla_seconds("urgent"),
            affect="crisis", reasons=("safety:crisis",),
        )

    stakes = detect_high_stakes(text, amount_threshold_ugx=stakes_amount_threshold_ugx())
    # Affect is read without the enforcement phrases, which the hardship
    # pattern would otherwise count a second time (see strip_stakes).
    kind = detect_user_distress(strip_stakes(text) if "enforcement" in stakes else text)
    sustained = bool(distress_trajectory(kind, list(history))["sustained"])
    turns = len(history) + 1
    outcome = result or {}
    mode = str(outcome.get("retrieval_mode") or "")
    faithfulness = outcome.get("faithfulness_score")
    unanswered = mode in _UNANSWERED_MODES or (
        isinstance(faithfulness, int | float) and float(faithfulness) < 0.5
    )

    reasons: list[str] = []
    if kind:
        reasons.append(f"affect:{kind}")
    if sustained:
        reasons.append("affect:sustained")
    reasons.extend(f"stakes:{code}" for code in stakes)
    if turns >= offer_after_turns():
        reasons.append("effort:long_conversation")
    if unanswered:
        reasons.append("confidence:unanswered")

    distressed = kind in _DISTRESSED
    pressing = "enforcement" in stakes or "deadline_today" in stakes
    hardship = kind == "hardship"

    if hardship and pressing:
        tier, priority = TIER_PRIORITY, "urgent"
    elif (distressed or sustained) and stakes:
        tier, priority = TIER_PRIORITY, "high"
    elif hardship or sustained:
        tier, priority = TIER_OFFER, "high" if hardship else "normal"
    elif "enforcement" in stakes:
        tier, priority = TIER_OFFER, "high"
    elif (turns >= offer_after_turns() and (distressed or unanswered)) or (unanswered and distressed):
        tier, priority = TIER_OFFER, "normal"
    else:
        tier, priority = TIER_NONE, "normal"

    return EscalationAssessment(
        tier=tier,
        priority=priority,
        sla_seconds=sla_seconds(priority, hardship=hardship),
        affect=kind,
        reasons=tuple(reasons) if tier != TIER_NONE else (),
    )

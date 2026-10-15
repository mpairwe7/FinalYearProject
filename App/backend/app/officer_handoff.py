"""One handoff for every path to an officer (Phase 38).

A ticket reaches an officer from two places in the text channel: an answer the
system doubted (``_build_handoff_packet``) and a taxpayer who pressed "Talk to
an officer" (``POST /v1/escalate``). Both went through ``_maybe_create_ticket``
with a packet that ignored how the taxpayer felt and what was at stake — the
button's ticket was ``topic: general``, ``priority: normal`` — and listed the
same details to ask for whatever the taxpayer had already given. The voice
channel built a ``HandoffEnvelope`` (v2.1); text never did.

:func:`enrich_handoff` runs once, at that choke point, behind two flags:

* ``eq_stakes_escalation`` — the escalation assessment
  (:mod:`app.escalation_policy`) sets the ticket's priority and SLA and
  rides on the handoff as codes the officer can read;
* ``structured_text_handoff`` — the envelope, with the details to ask for
  pruned against what the taxpayer already gave in this conversation.

What already was given travels as slot **names**, never values: the stored
transcript is redacted (``redact_for_storage``), and the envelope must not
become the way round that.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .escalation_policy import (
    TIER_NONE,
    TIER_OFFER,
    TIER_PRIORITY,
    EscalationAssessment,
    assess_escalation,
    higher_priority,
)
from .flags import flags

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

logger = logging.getLogger(__name__)

#: Each detail an officer may need, and the workflow slots that already
#: answer it (any one alternative, all of its slots).
_DETAIL_SLOTS: dict[str, tuple[frozenset[str], ...]] = {
    "assessment or objection reference number": (frozenset({"reference_number"}),),
    "tax type and filing period": (frozenset({"tax_type", "filing_period"}),),
    "TIN or registered taxpayer email": (frozenset({"tin"}), frozenset({"taxpayer_tin"})),
    "tax type and return period": (frozenset({"tax_type", "filing_period"}),),
    "entry or declaration number": (frozenset({"reference_number"}),),
    "consignment or bill of lading reference": (frozenset({"transport_reference"}),),
    "taxpayer type (individual, company, or NGO)": (frozenset({"taxpayer_type"}), frozenset({"taxpayer_kind"})),
    "the tax type involved": (frozenset({"tax_type"}),),
}
#: The envelope's affect vocabulary, from the distress classifier's.
_AFFECT = {
    "hardship": "hardship",
    "frustration": "frustrated",
    "anxiety": "urgent_distress",
    "urgency": "urgent_distress",
    "confusion": "confused",
    "crisis": "urgent_distress",
}
_MODALITY_CHANNEL = {"voice": "voice_ws", "call": "voice_call"}


_TIER_RANK = {TIER_NONE: 0, TIER_OFFER: 1, TIER_PRIORITY: 2}
_PRIORITY_RANK = {"low": 0, "normal": 1, "high": 2, "urgent": 3}
#: How far back a ticket looks for what the taxpayer said that matters.
_ASSESS_LAST_MESSAGES = 3


def strongest_assessment(user_query: str, history: Sequence[str]) -> EscalationAssessment:
    """The most urgent reading of the taxpayer's last few messages.

    A ticket is often raised by a message that carries nothing — "Talk to an
    officer", tapped under an answer — two turns after the one that said the
    bank account was frozen. Assessing only the last message would send that
    taxpayer to the back of the queue.
    """
    messages = [*history, user_query]
    best: EscalationAssessment | None = None
    for index in range(len(messages) - 1, max(-1, len(messages) - 1 - _ASSESS_LAST_MESSAGES), -1):
        current = assess_escalation(messages[index], history=messages[:index])
        if best is None or (_TIER_RANK[current.tier], _PRIORITY_RANK.get(current.priority, 1)) > (
            _TIER_RANK[best.tier],
            _PRIORITY_RANK.get(best.priority, 1),
        ):
            best = current
    return best or assess_escalation(user_query)


def prune_required_details(details: Sequence[str], given: Sequence[str]) -> list[str]:
    """*details* without the ones the taxpayer has already answered."""
    known = {name.strip().lower() for name in given}
    kept: list[str] = []
    for detail in details:
        alternatives = _DETAIL_SLOTS.get(detail, ())
        if any(slots <= known for slots in alternatives):
            continue
        kept.append(detail)
    return kept


def given_slot_names(conversation_id: str, *, tenant_id: str = "default", user_id: str = "") -> list[str]:
    """Names of what the taxpayer has already given: this conversation's
    workflow slots, plus their consented slot bank. Names only."""
    names: set[str] = set()
    try:
        from . import database as db  # noqa: PLC0415

        session = db.get_workflow_session(conversation_id, tenant_id) if conversation_id else None
        if session:
            names.update(k for k, v in (session.get("slots") or {}).items() if v not in ("", None))
    except Exception:
        logger.debug("workflow slots unavailable for handoff", exc_info=True)
    if user_id:
        try:
            from .memory import get_memory_service  # noqa: PLC0415

            names.update(get_memory_service().resolution_ledger.get_verified_slots(user_id, tenant_id=tenant_id))
        except Exception:
            logger.debug("slot bank unavailable for handoff", exc_info=True)
    return sorted(names)


def _names_only(envelope: dict[str, Any]) -> dict[str, Any]:
    """The envelope with every slot value and the TIN reduced to "provided"."""
    envelope["verified_slot_bank"] = {key: "provided" for key in (envelope.get("verified_slot_bank") or {})}
    if envelope.get("taxpayer_tin"):
        envelope["taxpayer_tin"] = "provided"
    return envelope


def enrich_handoff(
    handoff: dict[str, Any] | None,
    *,
    user_query: str,
    transcript: Sequence[Mapping[str, Any]],
    conversation_id: str,
    user_id: str = "",
    tenant_id: str = "default",
    locale: str = "en",
    modality: str = "text",
    priority: str = "normal",
) -> str:
    """Add the escalation assessment and the envelope to *handoff*; return the ticket priority.

    *transcript* is the conversation as it stood, ending with the escalated
    turn. Never raises and never lowers a priority it was given.
    """
    if handoff is None:
        return priority
    subject = user_id or None
    effective = priority or "normal"
    assessment = None
    try:
        if flags.is_enabled("eq_stakes_escalation", subject=subject):
            history = [str(turn.get("user_message") or "") for turn in list(transcript)[:-1]]
            assessment = strongest_assessment(user_query, [h for h in history if h])
            handoff["escalation"] = assessment.to_dict()
            effective = higher_priority(effective, assessment.priority)
            handoff["priority"] = higher_priority(str(handoff.get("priority") or "normal"), effective)
            from .analytics import metrics  # noqa: PLC0415

            metrics.inc(
                "cx_escalation_tier_total",
                labels={
                    "tier": assessment.tier,
                    "trigger": (assessment.reasons[0].split(":", 1)[0] if assessment.reasons else "none"),
                    "path": "ticket",
                },
            )
    except Exception:
        logger.debug("escalation assessment skipped", exc_info=True)

    try:
        if flags.is_enabled("structured_text_handoff", subject=subject):
            from .memory import get_memory_service  # noqa: PLC0415
            from .memory.resolution_ledger import infer_domain_tag, objective_for  # noqa: PLC0415

            given = given_slot_names(conversation_id, tenant_id=tenant_id, user_id=user_id)
            required = prune_required_details(list(handoff.get("required_details") or []), given)
            handoff["required_details"] = required
            handoff["already_given"] = [name.replace("_", " ") for name in given]
            affect = assessment.affect if assessment else str(handoff.get("sentiment") or "")
            envelope = get_memory_service().create_handoff_envelope(
                thread_id=conversation_id,
                user_id=user_id,
                objective=objective_for(infer_domain_tag(user_query)),
                target_resolution=f"An officer resolves this {str(handoff.get('topic') or 'tax').replace('_', ' ')} request",
                tenant_id=tenant_id or "default",
                next_action_type="officer_reply",
                suggested_message=str(handoff.get("opening_guidance") or ""),
                locale=locale or "en",
                source_channel=_MODALITY_CHANNEL.get(modality, "web"),
                caller_affect=_AFFECT.get(affect, "calm"),
                priority=effective,
                eq_guidance=(
                    {"tier": assessment.tier, "reasons": list(assessment.reasons)} if assessment else {}
                ),
                required_inputs=required,
                verified_slot_bank={name: "provided" for name in given},
            )
            envelope_dict = _names_only(envelope.to_dict())
            if assessment is not None:
                envelope_dict.setdefault("sla", {})["target_response_seconds"] = assessment.sla_seconds
            handoff["envelope"] = envelope_dict
            handoff["completeness"] = envelope.completeness_ratio()
            handoff["zero_repetition_score"] = envelope.zero_repetition_score()
    except Exception:
        logger.debug("handoff envelope skipped", exc_info=True)
    return effective

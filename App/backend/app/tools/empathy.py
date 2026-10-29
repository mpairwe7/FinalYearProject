"""Emotional-intelligence assessment exposed as an agent tool.

Distress detection already existed as lexical helpers in
:mod:`app.text_signals`, wired straight into ``service.py``.  That works
for the fixed reply paths, but the agent had no way to *ask* how a
message reads before deciding how to answer — so a tool-calling turn
could open with a cheerful sales tone on a message about a seized
consignment.

This module exposes the same classifier as a tool.  It delegates to
:mod:`app.text_signals` rather than re-implementing the patterns, so
there is exactly one definition of what "frustration" looks like and
the tool cannot drift from the copy the fixed paths emit.

What it adds over the raw helper:

- **Intensity**, from how many independent cues fire, so a mildly
  confused message and a panicking one are distinguishable.
- **Handoff signalling** — hardship ("I can't afford this", "I'll lose
  my business") and sustained distress across turns (``history``) are the
  states that should put a person in the loop, and the tool says which.
- **Crisis** — a message about self-harm short-circuits every other
  reading: ``crisis`` is true and the only guidance is the crisis lines.
- **Handling guidance** the caller can act on: whether to lead with an
  acknowledgement, whether to offer a human, and what to avoid.

Deterministic and offline: same input, same output, no network, so it
is safe on the latency-sensitive turn path and reproducible in tests.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ..text_signals import (
    checkin_already_given,
    detect_crisis,
    detect_high_stakes,
    detect_user_distress,
    distress_trajectory,
    empathy_ack,
    needs_wellbeing_checkin,
    tone_hint_for,
)
from . import Tool, ToolRegistry, ToolSchema

EMPATHY_NAMESPACE = "empathy"

_INTENSITY_CUES = (
    re.compile(r"[A-Z]{4,}"),  # shouting
    re.compile(r"!{2,}"),
    re.compile(r"\b(very|really|extremely|so|totally|completely)\b", re.IGNORECASE),
)
# "please" and "help" were intensity cues, so every polite request scored one
# notch more upset than a curt one. Politeness is not intensity.

_GUIDANCE: dict[str, dict[str, Any]] = {
    "frustration": {
        "lead_with_acknowledgement": True,
        "avoid": "Do not restate the process they already tried; give the next concrete step.",
    },
    "anxiety": {
        "lead_with_acknowledgement": True,
        "avoid": "Do not open with caveats or penalties; state the position plainly first.",
    },
    "urgency": {
        "lead_with_acknowledgement": True,
        "avoid": "Do not lead with background; give the deadline and the fastest route first.",
    },
    "confusion": {
        "lead_with_acknowledgement": False,
        "avoid": "Do not repeat the same wording; use shorter sentences and one example.",
    },
    "hardship": {
        "lead_with_acknowledgement": True,
        "avoid": "Do not quote penalties without also naming the relief or instalment options.",
    },
    "": {
        "lead_with_acknowledgement": False,
        "avoid": "Answer directly; an unprompted empathy opener reads as insincere.",
    },
}


def _wellbeing_checkin_allowed(*, user_id: str | None = None) -> bool:
    """Evaluate server-side wellbeing_checkin flag and once-per-conversation state."""
    try:
        from ..flags import flags

        if not flags.is_enabled("wellbeing_checkin", subject=user_id or None):
            return False
    except Exception:
        return False

    try:
        from .. import database as db
        from ..service import _ACTIVE_CONVERSATION_STATE, _ACTIVE_TENANT_ID

        active_conv = _ACTIVE_CONVERSATION_STATE.get()
        if active_conv:
            active_tenant = _ACTIVE_TENANT_ID.get() or "default"
            turns = db.get_recent_turns(active_conv, limit=10, tenant_id=active_tenant)
            prior_replies = [str(t.get("bot_reply") or "") for t in (turns or ())]
            if checkin_already_given(prior_replies):
                return False
    except Exception:
        pass
    return True


def assess(
    message: str,
    history: list[str] | None = None,
    *,
    locale: str = "",
    user_id: str | None = None,
    record_metric: bool = True,
) -> dict[str, Any]:
    """Classify *message* and return handling guidance.

    Classification comes from :func:`app.text_signals.detect_user_distress`
    so the tool and the deterministic reply paths cannot disagree; this
    function adds the intensity and handling guidance on top.

    *history* is the taxpayer's earlier messages in this conversation,
    oldest first. With it, "sustained" means what it says — the same upset
    across turns — instead of being guessed from one message.
    """
    text = message or ""
    eff_locale = (locale or "").strip().lower()
    if not eff_locale:
        try:
            from ..query import detect_language

            eff_locale = detect_language(text, default_lang="en")
        except Exception:
            eff_locale = "en"
    if eff_locale not in ("en", "lg", "sw", "nyn", "ach"):
        eff_locale = "en"

    stakes = list(detect_high_stakes(text))
    if detect_crisis(text):
        return {
            "kind": "hardship",
            "intensity": "high",
            "crisis": True,
            "wellbeing_checkin": False,
            "sustained": False,
            "stakes": stakes,
            "locale": eff_locale,
            "acknowledgement": "",
            "localized_acknowledgement": "",
            "tone_hint": "",
            "tone_register": "empathetic_reassuring",
            "lead_with_acknowledgement": False,
            "offer_human_handoff": True,
            "avoid": "Do not answer a tax question in this turn; give the crisis lines first.",
            "explanation": "The message expresses intent to self-harm; reply with crisis support.",
            "recommended_relief_statutes": [],
            "retrieval_preference_terms": "",
            "priority_boost": "urgent",
        }
    kind = detect_user_distress(text)
    trajectory = distress_trajectory(kind, history or [])
    wellbeing_checkin = bool(
        needs_wellbeing_checkin(text, history or ())
        and _wellbeing_checkin_allowed(user_id=user_id)
    )

    cue_count = sum(1 for pattern in _INTENSITY_CUES if pattern.search(text))
    if not kind and not wellbeing_checkin:
        intensity = "none"
    elif cue_count >= 2 or kind == "hardship" or wellbeing_checkin:
        intensity = "high"
    elif cue_count == 1:
        intensity = "moderate"
    else:
        intensity = "low"

    if wellbeing_checkin and not kind:
        kind = "hardship"

    acknowledgement = empathy_ack(kind, "en")
    localized_ack = empathy_ack(kind, eff_locale)
    tone_hint = tone_hint_for(kind)
    guidance = _GUIDANCE.get(kind, _GUIDANCE[""])

    # A human is offered for hardship, wellbeing check-in, or high-intensity anxiety/frustration —
    # offering too early on mild queries reads as a brush-off.
    offer_human = (
        kind == "hardship"
        or wellbeing_checkin
        or (kind in ("frustration", "anxiety") and intensity == "high")
        or trajectory["sustained"]
    )

    relief_statutes: list[str] = []
    relief_terms = ""
    if (
        kind == "hardship"
        or wellbeing_checkin
        or (kind == "anxiety" and intensity in ("moderate", "high"))
        or (kind in ("frustration", "anxiety") and trajectory["sustained"])
    ):
        relief_statutes = [
            "TPCA Section 39 (remission of interest)",
            "TPCA Section 40(4) (instalment agreement / agency notice relief)",
            "TPCA Section 66 (Voluntary Disclosure penalty waiver)",
        ]
        relief_terms = "instalment payment penalty waiver remission voluntary disclosure section 39 section 66"

    tone_register = (
        "empathetic_reassuring"
        if kind in ("hardship", "anxiety", "frustration") and (intensity in ("moderate", "high") or trajectory["sustained"] or kind == "hardship")
        else "patient_clear"
        if kind == "confusion"
        else "direct_urgent"
        if kind == "urgency"
        else "neutral"
    )

    priority_boost = "urgent" if wellbeing_checkin else ("high" if offer_human else "normal")

    if kind and record_metric:
        try:
            from ..flags import flags

            if flags.is_enabled("eq_adaptive_retrieval") or flags.is_enabled("eq_multimodal_fusion"):
                from ..analytics import metrics

                metrics.inc(
                    "eq_interventions_total",
                    labels={
                        "affect": kind,
                        "outcome": "escalated_officer" if offer_human else "de_escalated_autonomous",
                    },
                )
        except Exception as _exc:  # noqa: BLE001
            _ = _exc

    return {
        "kind": kind,
        "intensity": intensity,
        "crisis": False,
        "wellbeing_checkin": wellbeing_checkin,
        "sustained": trajectory["sustained"],
        "stakes": stakes,
        "locale": eff_locale,
        "acknowledgement": acknowledgement,
        "localized_acknowledgement": localized_ack,
        "tone_hint": tone_hint,
        "tone_register": tone_register,
        "lead_with_acknowledgement": bool(acknowledgement)
        and guidance["lead_with_acknowledgement"],
        "offer_human_handoff": offer_human,
        "avoid": (
            "Check in on the taxpayer's wellbeing and provide the crisis support lines before addressing the tax issue."
            if wellbeing_checkin
            else guidance["avoid"]
        ),
        "explanation": (
            "The message expresses hopelessness amid hardship or enforcement; check in on wellbeing with crisis support lines first."
            if wellbeing_checkin
            else f"The message reads as {kind} ({intensity} intensity)."
            if kind
            else "The message reads as neutral; answer directly without an empathy opener."
        ),
        "recommended_relief_statutes": relief_statutes,
        "retrieval_preference_terms": relief_terms,
        "priority_boost": priority_boost,
    }


@dataclass(frozen=True)
class AffectiveState:
    """Unified multimodal (lexical + acoustic prosody + effort trajectory) affective state."""

    affect: str = ""
    intensity: str = "none"
    raw_score: float = 0.0
    smoothed_score: float = 0.0
    sustained: bool = False
    crisis: bool = False
    wellbeing_checkin: bool = False
    stakes: list[str] = field(default_factory=list)
    locale: str = "en"
    localized_acknowledgement: str = ""
    tone_register: str = "neutral"  # "neutral" | "empathetic_reassuring" | "patient_clear" | "direct_urgent"
    should_auto_escalate: bool = False
    recommended_speech_rate: float = 1.0
    recommended_relief_statutes: list[str] = field(default_factory=list)
    retrieval_preference_terms: str = ""
    priority_boost: str = "normal"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fuse_multimodal_affect(
    message: str,
    *,
    history: list[str] | None = None,
    prosody_profile: Any | None = None,
    clarifications_asked: int = 0,
    previous_smoothed_score: float = 0.0,
    locale: str = "",
    user_id: str | None = None,
    record_metric: bool = True,
) -> AffectiveState:
    """Fuse lexical distress, multi-turn trajectory, acoustic prosody, and clarification effort."""
    lex = assess(message, history=history, locale=locale, user_id=user_id, record_metric=False)
    eff_loc = str(lex.get("locale") or "en")
    stakes = list(lex.get("stakes") or [])
    wellbeing_checkin = bool(lex.get("wellbeing_checkin", False))
    if lex.get("crisis"):
        return AffectiveState(
            affect="hardship",
            intensity="high",
            raw_score=1.0,
            smoothed_score=1.0,
            sustained=True,
            crisis=True,
            wellbeing_checkin=False,
            stakes=stakes,
            locale=eff_loc,
            localized_acknowledgement="",
            tone_register="empathetic_reassuring",
            should_auto_escalate=True,
            recommended_speech_rate=0.88,
            recommended_relief_statutes=[],
            retrieval_preference_terms="",
            priority_boost="urgent",
        )

    kind = str(lex.get("kind") or "")
    intensity = str(lex.get("intensity") or "none")
    sustained = bool(lex.get("sustained", False))

    lex_map = {"none": 0.0, "low": 0.42, "moderate": 0.68, "high": 0.90}
    lex_score = lex_map.get(intensity, 0.0)
    if kind == "hardship":
        lex_score = max(lex_score, 0.92)
    elif sustained and kind in ("frustration", "anxiety"):
        lex_score = max(lex_score, 0.84)

    effort_score = min(1.0, max(0, int(clarifications_asked)) / 3.0)

    acoustic_score = 0.0
    speech_rate = 1.0
    if prosody_profile is not None:
        p_affect = str(getattr(prosody_profile, "caller_affect", "calm") or "calm")
        rms = float(getattr(prosody_profile, "rms_energy", 0.0) or 0.0)
        e_var = float(getattr(prosody_profile, "energy_variance", 0.0) or 0.0)
        wps = float(getattr(prosody_profile, "words_per_second", 0.0) or 0.0)
        speech_rate = float(getattr(prosody_profile, "recommended_speech_rate", 1.0) or 1.0)
        if p_affect == "urgent_distress":
            acoustic_score = 0.95
        elif p_affect == "frustrated":
            acoustic_score = 0.78
        elif p_affect == "confused":
            acoustic_score = 0.52
        if rms >= 0.18 and (e_var >= 0.08 or wps >= 3.8):
            acoustic_score = max(acoustic_score, 0.85)
        if not kind and p_affect in ("frustrated", "urgent_distress"):
            kind = "frustration"
            intensity = "high" if p_affect == "urgent_distress" else "moderate"
        elif not kind and p_affect == "confused":
            kind = "confusion"
            intensity = "moderate"

    if prosody_profile is not None:
        raw_score = 0.45 * lex_score + 0.35 * acoustic_score + 0.20 * effort_score
    else:
        raw_score = 0.75 * lex_score + 0.25 * effort_score
    if kind == "hardship":
        raw_score = max(raw_score, 0.85)
    elif sustained and kind in ("frustration", "anxiety"):
        raw_score = max(raw_score, 0.80)
    raw_score = round(min(1.0, max(0.0, raw_score)), 3)

    if previous_smoothed_score > 0.0:
        smoothed = round(min(1.0, 0.65 * raw_score + 0.35 * float(previous_smoothed_score)), 3)
    else:
        smoothed = raw_score

    try:
        threshold = max(0.50, min(0.99, float(os.getenv("EQ_AUTO_ESCALATE_THRESHOLD", "0.80"))))
    except ValueError:
        threshold = 0.80

    should_escalate = (
        smoothed >= threshold
        or kind == "hardship"
        or sustained
        or bool(lex.get("offer_human_handoff", False))
    )

    if kind in ("hardship", "anxiety", "frustration") and (smoothed >= 0.55 or sustained or kind == "hardship"):
        tone_reg = "empathetic_reassuring"
        speech_rate = min(speech_rate, 0.90)
    elif kind == "confusion" or clarifications_asked >= 2:
        tone_reg = "patient_clear"
        speech_rate = min(speech_rate, 0.92)
    elif kind == "urgency":
        tone_reg = "direct_urgent"
    else:
        tone_reg = str(lex.get("tone_register") or "neutral")

    priority_boost = "urgent" if (kind == "hardship" or smoothed >= threshold) else ("high" if should_escalate else "normal")

    if kind and record_metric:
        try:
            from ..flags import flags

            if flags.is_enabled("eq_multimodal_fusion", subject=user_id) or flags.is_enabled("eq_adaptive_retrieval", subject=user_id):
                from ..analytics import metrics

                metrics.inc(
                    "eq_interventions_total",
                    labels={
                        "affect": kind,
                        "outcome": "escalated_officer" if should_escalate else "de_escalated_autonomous",
                    },
                )
        except Exception as _exc:  # noqa: BLE001
            _ = _exc

    return AffectiveState(
        affect=kind,
        intensity=intensity,
        raw_score=raw_score,
        smoothed_score=smoothed,
        sustained=sustained,
        crisis=False,
        wellbeing_checkin=wellbeing_checkin,
        stakes=stakes,
        locale=eff_loc,
        localized_acknowledgement=str(lex.get("localized_acknowledgement") or empathy_ack(kind, eff_loc)),
        tone_register=tone_reg,
        should_auto_escalate=should_escalate,
        recommended_speech_rate=round(speech_rate, 2),
        recommended_relief_statutes=list(lex.get("recommended_relief_statutes") or []),
        retrieval_preference_terms=str(lex.get("retrieval_preference_terms") or ""),
        priority_boost=priority_boost,
    )


class EmotionalToneTool(Tool):
    """Assess how a user's message reads before choosing a reply tone."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="assess_emotional_tone",
            description=(
                "Assess the emotional tone of a taxpayer's message before answering: "
                "returns whether it reads as frustration, anxiety, urgency, confusion "
                "or hardship, how intense it is, a ready-made acknowledgement sentence, "
                "and what to avoid. Call this first when a message sounds upset, "
                "panicked, confused, or mentions penalties, enforcement or being unable "
                "to pay — then answer in the tone it recommends. Do not call it for "
                "plain factual questions."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "minLength": 1,
                        "description": "The taxpayer's message, verbatim.",
                    },
                    "history": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 10,
                        "description": "The taxpayer's earlier messages in this conversation, oldest first.",
                    },
                    "locale": {
                        "type": "string",
                        "description": "Active or detected conversation locale (en, lg, sw, nyn, ach).",
                    },
                },
                "required": ["message"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "error": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["", "frustration", "anxiety", "urgency", "confusion", "hardship"],
                    },
                    "intensity": {
                        "type": "string",
                        "enum": ["none", "low", "moderate", "high"],
                    },
                    "locale": {"type": "string"},
                    "acknowledgement": {"type": "string"},
                    "localized_acknowledgement": {"type": "string"},
                    "tone_hint": {"type": "string"},
                    "tone_register": {
                        "type": "string",
                        "enum": ["neutral", "empathetic_reassuring", "patient_clear", "direct_urgent"],
                    },
                    "lead_with_acknowledgement": {"type": "boolean"},
                    "offer_human_handoff": {"type": "boolean"},
                    "crisis": {"type": "boolean"},
                    "wellbeing_checkin": {"type": "boolean"},
                    "sustained": {"type": "boolean"},
                    "stakes": {"type": "array", "items": {"type": "string"}},
                    "avoid": {"type": "string"},
                    "explanation": {"type": "string"},
                    "recommended_relief_statutes": {"type": "array", "items": {"type": "string"}},
                    "retrieval_preference_terms": {"type": "string"},
                    "priority_boost": {"type": "string"},
                },
                "required": ["ok", "kind", "intensity"],
                "additionalProperties": True,
            },
            risk="low",
            namespace=EMPATHY_NAMESPACE,
            title="Assess emotional tone",
        )

    def execute(
        self,
        message: str = "",
        history: list[str] | None = None,
        locale: str = "",
        user_id: str = "",
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        _ = tenant_id
        if not str(message).strip():
            return {"ok": False, "kind": "", "intensity": "none", "error": "message is required"}
        return {
            "ok": True,
            **assess(
                message,
                history=[str(h) for h in (history or [])][-10:],
                locale=locale,
                user_id=user_id or None,
            ),
        }


ToolRegistry.register(EmotionalToneTool())

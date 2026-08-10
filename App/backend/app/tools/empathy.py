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
  my business") and sustained frustration are the two states that should
  put a person in the loop, and the tool says which.
- **Handling guidance** the caller can act on: whether to lead with an
  acknowledgement, whether to offer a human, and what to avoid.

Deterministic and offline: same input, same output, no network, so it
is safe on the latency-sensitive turn path and reproducible in tests.
"""

from __future__ import annotations

import re
from typing import Any

from ..text_signals import detect_user_distress, empathy_ack, tone_hint_for
from . import Tool, ToolRegistry, ToolSchema

EMPATHY_NAMESPACE = "empathy"

_INTENSITY_CUES = (
    re.compile(r"[A-Z]{4,}"),  # shouting
    re.compile(r"!{2,}"),
    re.compile(r"\b(very|really|extremely|so|totally|completely)\b", re.IGNORECASE),
    re.compile(r"\b(please|help)\b", re.IGNORECASE),
)

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


def assess(message: str) -> dict[str, Any]:
    """Classify *message* and return handling guidance.

    Classification comes from :func:`app.text_signals.detect_user_distress`
    so the tool and the deterministic reply paths cannot disagree; this
    function adds the intensity and handling guidance on top.
    """
    text = message or ""
    kind = detect_user_distress(text)

    cue_count = sum(1 for pattern in _INTENSITY_CUES if pattern.search(text))
    if not kind:
        intensity = "none"
    elif cue_count >= 2 or kind == "hardship":
        intensity = "high"
    elif cue_count == 1:
        intensity = "moderate"
    else:
        intensity = "low"

    acknowledgement = empathy_ack(kind)
    tone_hint = tone_hint_for(kind)
    guidance = _GUIDANCE.get(kind, _GUIDANCE[""])

    # A human is offered for hardship, and for anything else only when the
    # user is clearly at the end of their patience — offering too early
    # reads as a brush-off.
    offer_human = kind == "hardship" or (kind == "frustration" and intensity == "high")

    return {
        "kind": kind,
        "intensity": intensity,
        "acknowledgement": acknowledgement,
        "tone_hint": tone_hint,
        "lead_with_acknowledgement": bool(acknowledgement)
        and guidance["lead_with_acknowledgement"],
        "offer_human_handoff": offer_human,
        "avoid": guidance["avoid"],
        "explanation": (
            f"The message reads as {kind} ({intensity} intensity)."
            if kind
            else "The message reads as neutral; answer directly without an empathy opener."
        ),
    }


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
                    }
                },
                "required": ["message"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "kind": {
                        "type": "string",
                        "enum": ["", "frustration", "anxiety", "urgency", "confusion", "hardship"],
                    },
                    "intensity": {
                        "type": "string",
                        "enum": ["none", "low", "moderate", "high"],
                    },
                    "acknowledgement": {"type": "string"},
                    "tone_hint": {"type": "string"},
                    "lead_with_acknowledgement": {"type": "boolean"},
                    "offer_human_handoff": {"type": "boolean"},
                    "avoid": {"type": "string"},
                },
                "required": ["ok", "kind", "intensity"],
                "additionalProperties": True,
            },
            risk="low",
            namespace=EMPATHY_NAMESPACE,
            title="Assess emotional tone",
        )

    def execute(self, message: str = "") -> dict[str, Any]:
        if not str(message).strip():
            return {"ok": False, "error": "message is required"}
        return {"ok": True, **assess(message)}


ToolRegistry.register(EmotionalToneTool())

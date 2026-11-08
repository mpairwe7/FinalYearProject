"""What served this turn: backend, model, tier and any degradation (G486).

Moved out of ``service.py`` so the agentic graph records a turn without importing the
orchestrator. The record itself is :data:`app.turn_context.TURN_GENERATION`.
"""

from __future__ import annotations

from typing import Any

from . import llm as llm_module
from .analytics import metrics
from .turn_context import TURN_GENERATION


def endpoint_model(endpoint: Any) -> str:
    return str(getattr(endpoint, "model", "") or llm_module.LLM_MODEL)


def note_generation(
    *,
    backend: str = "",
    model: str = "",
    tier: str = "",
    fallback: str = "",
    degraded: str = "",
) -> None:
    """Record what served this turn. Does nothing outside a turn."""
    record = TURN_GENERATION.get()
    if record is None:
        return
    if backend:
        record["backend"] = backend
    if model:
        record["model"] = model
    if tier:
        record["tier"] = tier
    if fallback and fallback not in record["fallbacks"]:
        record["fallbacks"].append(fallback)
    if degraded and degraded not in record["degraded"]:
        record["degraded"].append(degraded)
        metrics.inc("turn_degraded_total", labels={"reason": degraded})


def note_passage_served(reason: str) -> None:
    """The reply is a retrieved passage, so no model wrote it, whichever one was tried."""
    record = TURN_GENERATION.get()
    if record is not None:
        record.update(backend="passage", model="", tier="")
    note_generation(degraded=reason)

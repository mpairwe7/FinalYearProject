"""What the kernel was asked during one turn, for offline refinement (ADR-0033, phase 3).

Kernel functions note two things here, keyed by concept only:

* a **miss**: a concept the agent asked for that the kernel does not declare,
  or does not hold for the year asked;
* an **uncited figure served**: a fact returned without a legal basis.

The audit row records the result (``kernel_trace``), and
:mod:`app.kernel.refine` turns the recurring ones into review proposals.
Concepts that are not identifier-shaped are dropped, so text the taxpayer
typed cannot reach the ledger through this path.
"""

from __future__ import annotations

import re
from typing import Any

from ..turn_context import TURN_KERNEL_TRACE

_CONCEPT_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
MAX_LISTED = 10


def start() -> Any:
    """Open a trace for this turn; returns the token to reset, or ``None`` if one is open."""
    return TURN_KERNEL_TRACE.set({"misses": [], "uncited_served": []}) if TURN_KERNEL_TRACE.get() is None else None


def open_turn() -> Any:
    """Open a fresh trace for a new turn, even if one is open; returns the token to reset.

    A WebSocket session runs its turns in one context, so :func:`start` would
    keep the first turn's trace and count its misses again in every later turn.
    """
    return TURN_KERNEL_TRACE.set({"misses": [], "uncited_served": []})


def _add(field: str, entry: dict[str, str]) -> None:
    trace = TURN_KERNEL_TRACE.get()
    if trace is None:
        return
    items = trace.setdefault(field, [])
    if entry not in items and len(items) < MAX_LISTED:
        items.append(entry)


def note_miss(concept: str, reason: str) -> None:
    if _CONCEPT_RE.match(concept or ""):
        _add("misses", {"concept": concept, "reason": reason})


def note_served(key: str, fiscal_year: str, uncited: bool) -> None:
    if uncited and _CONCEPT_RE.match(key or ""):
        _add("uncited_served", {"key": key, "fiscal_year": fiscal_year})


def snapshot() -> dict[str, Any] | None:
    trace = TURN_KERNEL_TRACE.get()
    if not trace or not (trace.get("misses") or trace.get("uncited_served")):
        return None
    return {"misses": list(trace.get("misses") or []), "uncited_served": list(trace.get("uncited_served") or [])}

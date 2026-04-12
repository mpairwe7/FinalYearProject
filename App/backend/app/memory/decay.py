"""Temporal decay for semantic memory facts.

Each fact has a category-specific half-life.  A fact's
``decay_adjusted_confidence`` is computed as::

    confidence * 0.5 ** (age_days / half_life_days)

Facts below a retrieval-time ``decay_floor`` are excluded from
memory reads but kept in the DB for audit / replay.

Half-lives are tuned for the URA tax domain — taxpayer type
rarely changes, session preferences decay within weeks.
"""

from __future__ import annotations

import time

# (category → half_life_days).  Categories not in the table get
# a conservative 90-day default.
HALF_LIVES = {
    "taxpayer_type":       5 * 365,   # ~5 years
    "industry":            2 * 365,   # ~2 years
    "registered_vat":      1 * 365,
    "fiscal_year":         1 * 365,
    "primary_language":    365,
    "detail_level":        180,
    "current_topic":       14,
    "last_query":          7,
    "session_note":        7,
}
DEFAULT_HALF_LIFE_DAYS = 90


def half_life_for(category: str) -> int:
    return HALF_LIVES.get(category, DEFAULT_HALF_LIFE_DAYS)


def decay_factor(age_days: float, half_life_days: float) -> float:
    """Standard exponential decay: f(t) = 0.5^(t / half_life)."""
    if half_life_days <= 0:
        return 0.0
    return 0.5 ** (age_days / half_life_days)


def compute_decayed_confidence(
    original_confidence: float,
    category: str,
    extracted_at: float,
    now: float | None = None,
) -> float:
    """Return the current decay-adjusted confidence of a fact.

    Clamped to [0, 1].  Facts whose category is unknown use the
    default half-life.
    """
    now = now or time.time()
    age_days = max(0.0, (now - extracted_at) / 86400.0)
    hl = half_life_for(category)
    f = decay_factor(age_days, hl)
    adjusted = original_confidence * f
    return max(0.0, min(1.0, adjusted))

"""Entailment / contradiction grounding for high-stakes claims (P1-8).

The lexical claim verifier (token overlap + numeric containment) can let a
fluent-but-wrong claim through with only a soft support penalty. This module
adds a *contradiction* signal so the response judge escalates/withholds rather
than merely appending a disclaimer.

Always-on: a deterministic, high-precision check for conflicting percentages —
tax rates are the prime high-stakes numeric claim (e.g. answer says "VAT is
20%" while the cited passage says 18%). Optional: a real NLI cross-encoder when
``ENTAILMENT_MODEL`` is configured (graceful fallback to numeric-only).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# e.g. "cross-encoder/nli-deberta-v3-small"; empty → deterministic numeric only.
ENTAILMENT_MODEL = os.getenv("ENTAILMENT_MODEL", "")
_CONTRADICTION_PROB_MIN = float(os.getenv("ENTAILMENT_CONTRADICTION_MIN", "0.6"))

# A percentage written as "18%" or "18 percent" / "18 per cent" / "18 percentage".
_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|per\s?cent(?:age)?)")

# A money amount: comma- or space-grouped ("1,500,000"), plain ("335000"), or
# suffixed ("1.5m", "300 million").  Percentages are excluded by the caller.
_AMOUNT_RE = re.compile(
    r"(?:ugx|ug\s?shs?|shs|shillings?)?\s*"
    r"(\d{1,3}(?:[,\s]\d{3})+|\d+(?:\.\d+)?)"
    r"\s*(k|m|bn|b|thousand|million|billion)?\b",
    re.IGNORECASE,
)
_AMOUNT_SUFFIX = {
    "k": 1_000,
    "thousand": 1_000,
    "m": 1_000_000,
    "million": 1_000_000,
    "b": 1_000_000_000,
    "bn": 1_000_000_000,
    "billion": 1_000_000_000,
}

#: Statements *about the rule* — a wrong amount here is a factual error about
#: the law, not an arithmetic result.  Computed totals ("PAYE = UGX 202,000")
#: legitimately carry amounts the source passage never states, so the money
#: contradiction check fires only for rule-shaped sentences.
_RULE_CUE_RE = re.compile(
    r"\b(threshold|above|below|exceed\w*|at\s+least|minimum|maximum|"
    r"limit|register\w*|registration|band|bracket|allowance|cap(?:ped)?)\b",
    re.IGNORECASE,
)

_model: Any = None
_model_loaded = False


def percentages(text: str) -> set[str]:
    """Numeric values stated as percentages, e.g. {"18"} from "18%"/"18 percent"."""
    return set(_PCT_RE.findall(text.lower()))


def canonical_amounts(text: str) -> set[float]:
    """Money amounts in *text*, normalised to their numeric value.

    ``"UGX 1,500,000"``, ``"1.5m"`` and ``"1500000"`` all yield
    ``1500000.0``.  Without this, comma-grouped figures were tokenised
    into ``{"1", "500", "000"}`` — junk that matched almost any passage
    containing a grouped number, so numeric containment silently passed
    money claims it should have caught.
    """
    lowered = (text or "").lower()
    # Percentages are handled separately; drop them so "18%" is not read
    # as the amount 18.
    without_pct = _PCT_RE.sub(" ", lowered)
    amounts: set[float] = set()
    for match in _AMOUNT_RE.finditer(without_pct):
        digits = match.group(1).replace(",", "").replace(" ", "")
        try:
            value = float(digits)
        except ValueError:
            continue
        value *= _AMOUNT_SUFFIX.get((match.group(2) or "").lower(), 1)
        amounts.add(value)
    return amounts


def numeric_contradiction(claim: str, context: str) -> bool:
    """True when a claim's figures conflict with the cited context's.

    Two high-precision rules:

    *Percentages* — fires when both sides state percentages and the
    claim's are entirely absent from the context ("VAT is 20%" against a
    passage saying 18%).

    *Money* — fires only for rule-shaped sentences (a threshold, band or
    registration limit), where a figure the passage does not state is a
    misstatement of the law rather than an arithmetic result.  This is
    what catches a stale threshold after a budget moves one, which the
    percentage rule cannot see: the FY2026-27 amendments changed the PAYE
    tax-free threshold and the VAT registration threshold without
    changing a single rate.
    """
    cp = percentages(claim)
    xp = percentages(context)
    if cp and xp and cp.isdisjoint(xp):
        return True

    if not _RULE_CUE_RE.search(claim):
        return False
    ca = canonical_amounts(claim)
    xa = canonical_amounts(context)
    if ca and xa and ca.isdisjoint(xa):
        return True
    return False



def _load_model() -> Any:
    global _model, _model_loaded
    if _model_loaded:
        return _model
    _model_loaded = True
    if not ENTAILMENT_MODEL:
        return None
    try:
        from sentence_transformers import CrossEncoder

        _model = CrossEncoder(ENTAILMENT_MODEL)
        logger.info("Entailment NLI model loaded: %s", ENTAILMENT_MODEL)
    except Exception:
        logger.warning("Entailment model %s unavailable; numeric-only", ENTAILMENT_MODEL, exc_info=True)
        _model = None
    return _model


def _model_says_contradicted(claim: str, context: str) -> bool:
    """Best-effort NLI check; returns False when no model is configured."""
    model = _load_model()
    if model is None:
        return False
    try:
        import numpy as np

        logits = np.asarray(model.predict([(context, claim)]), dtype=float).reshape(-1)
        # Standard 3-way NLI label order is [contradiction, entailment, neutral].
        if logits.size < 3:
            return False
        shifted = np.exp(logits - logits.max())
        probs = shifted / shifted.sum()
        return bool(int(probs.argmax()) == 0 and probs[0] >= _CONTRADICTION_PROB_MIN)
    except Exception:
        logger.debug("NLI predict failed; numeric-only", exc_info=True)
        return False


def is_contradicted(claim: str, contexts: list[str]) -> bool:
    """Return True if *claim* contradicts the cited *contexts* (P1-8)."""
    context = " ".join(c for c in contexts if c)
    if not claim.strip() or not context.strip():
        return False
    if numeric_contradiction(claim, context):
        return True
    return _model_says_contradicted(claim, context)

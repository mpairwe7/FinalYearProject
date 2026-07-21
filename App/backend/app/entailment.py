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

_model: Any = None
_model_loaded = False


def _percentages(text: str) -> set[str]:
    """Numeric values stated as percentages, e.g. {"18"} from "18%"/"18 percent"."""
    return set(_PCT_RE.findall(text.lower()))


def numeric_contradiction(claim: str, context: str) -> bool:
    """True when a claim percentage conflicts with the context's percentages.

    High precision: fires only when BOTH the claim and the cited context state
    percentages and the claim's percentages are entirely absent from the
    context. General semantic contradiction is left to the optional NLI model.
    """
    cp = _percentages(claim)
    xp = _percentages(context)
    return bool(cp and xp and cp.isdisjoint(xp))


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

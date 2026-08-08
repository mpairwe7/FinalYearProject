"""LLM second opinion for the routing decisions the rules are unsure about.

``Supervisor.classify`` has been pure rules since it was written, and its
docstring has promised an LLM fallback "when rule-based confidence is
below ``SUPERVISOR_LLM_THRESHOLD``" for just as long. There was no
function behind the promise — the threshold was never read and no model
was ever called.

This is that function. It exists as its own module, injected rather than
imported by the supervisor, for one reason: the supervisor's value is
that it is pure Python with no network and no model load, testable
offline in CI at 36/36 in under 2 ms. Importing :mod:`app.llm` into it
would end that.

## What it may and may not do

**It fires only on the low-confidence slice.** The rule confidences run
from 0.6 (the default retrieval route) up to 1.0 (a greeting); the
default threshold of 0.7 means the model is asked about the *default*
route and nothing else. Every decision the rules actually made is kept.

**It may not downgrade an escalation.** A model must not be able to
talk the system out of handing a distressed taxpayer to a person. The
guard is defensive — escalations are decided at 0.95 and never reach
here — but the cost of it being wrong once is a taxpayer who asked for
help and was answered by a bot, so it is enforced rather than assumed.

**It fails open, always.** No model, a timeout, unparseable output, a
route name that does not exist: every one of them returns the rule
decision unchanged. A classifier that can break routing is worse than
no classifier, and this one is optional by construction.

Feature flag: ``FLAG_SUPERVISOR_LLM_TIEBREAK``, default off.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import OrderedDict
from typing import Any

from .state import AgentRoute, RouteDecision

logger = logging.getLogger(__name__)

#: Rule confidence at or above which the model is not consulted.
#:
#: Rule confidences are 0.6 (default retrieval), 0.78 (customs), 0.8
#: (amount+intent), 0.86 (learning), 0.88 (rate), 0.9 (temporal,
#: clarify), 0.92 (calculation), 0.95 (escalation), 1.0 (greeting). At
#: 0.7 the model sees only the default route — the cases where the rules
#: matched nothing and fell through, which is exactly the slice worth
#: a second opinion.
DEFAULT_THRESHOLD = 0.7

#: Confidence assigned to a decision the model changed. Below every rule
#: confidence on purpose: an inferred route should not outrank a stated
#: one, and downstream tier selection reads this number.
TIEBREAK_CONFIDENCE = 0.65

#: Routes the model is allowed to choose. ``BLOCKED`` is absent — that is
#: the input guard's decision, not a classification — and ``ESCALATE``
#: is absent because raising an escalation on a vague question would
#: send routine traffic to staff.
_SELECTABLE: dict[str, AgentRoute] = {
    "rag": AgentRoute.RAG,
    "tools": AgentRoute.TOOLS,
    "tax_specialist": AgentRoute.TAX_SPECIALIST,
    "customs_specialist": AgentRoute.CUSTOMS_SPECIALIST,
    "clarify": AgentRoute.CLARIFY,
}

#: Routes no model output may replace. Escalation and greeting are
#: decided at 0.95+ and cannot reach the threshold, so this is a
#: belt-and-braces guard on a safety-relevant decision.
_UNCHALLENGEABLE = frozenset({AgentRoute.ESCALATE, AgentRoute.GREET, AgentRoute.BLOCKED})

_PROMPT = """You classify Uganda Revenue Authority taxpayer questions.

Choose exactly one route:
- "tools": needs a calculation, a current rate, or today's date.
- "customs_specialist": import, export, tariff or border clearance.
- "tax_specialist": domestic tax — income, VAT, PAYE, rental, withholding.
- "rag": a factual question answered from URA documents.
- "clarify": too vague or incomplete to answer.

Reply with JSON only: {{"route": "<one of the above>"}}

Question: {query}"""

#: Routing decisions repeat heavily across a taxpayer population, and
#: this is the one path in the supervisor that costs a model call.
_CACHE: OrderedDict[str, str] = OrderedDict()
_CACHE_MAX = 512

_JSON_RE = re.compile(r"\{[^{}]*\}")
_WS_RE = re.compile(r"\s+")


def _cache_key(query: str) -> str:
    """Normalized query — case, whitespace and trailing punctuation."""
    return _WS_RE.sub(" ", query.strip().lower()).rstrip("?!. ")


def threshold() -> float:
    """Confidence below which the model is consulted."""
    raw = os.getenv("SUPERVISOR_LLM_THRESHOLD", "")
    if not raw.strip():
        return DEFAULT_THRESHOLD
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        logger.warning("bad SUPERVISOR_LLM_THRESHOLD=%r, using default", raw)
        return DEFAULT_THRESHOLD


def _parse_route(raw: str) -> AgentRoute | None:
    """Extract a known route from model output, or ``None``.

    Tolerates the prose a small model wraps around its JSON, because
    rejecting a correct answer for its packaging wastes the call. Does
    not tolerate a route name outside the allowed set — a hallucinated
    route is not a routing decision.
    """
    if not raw:
        return None
    match = _JSON_RE.search(raw)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                name = str(parsed.get("route", "")).strip().lower()
                if name in _SELECTABLE:
                    return _SELECTABLE[name]
        except (ValueError, TypeError):
            pass
    # Bare route name, which small models emit often enough to handle.
    bare = raw.strip().strip('"').lower()
    return _SELECTABLE.get(bare)


def _default_classifier(query: str) -> str:
    """Ask the configured model. Imported lazily — see the module docstring."""
    from ..llm import generate

    return generate(query, passages=[], structured=False)


def refine(
    query: str,
    decision: RouteDecision,
    *,
    classifier: Any = None,
    min_confidence: float | None = None,
) -> RouteDecision:
    """Return *decision*, or a model-chosen replacement for it.

    *classifier* is injected so this is testable without a model and so
    the caller controls which tier answers; it defaults to the
    configured generator.
    """
    if decision.route in _UNCHALLENGEABLE:
        return decision

    floor = threshold() if min_confidence is None else min_confidence
    if decision.confidence >= floor:
        return decision

    key = _cache_key(query)
    if not key:
        return decision

    cached = _CACHE.get(key)
    if cached is not None:
        _CACHE.move_to_end(key)
        route = _SELECTABLE.get(cached)
        return _apply(decision, route, cached_hit=True)

    try:
        raw = (classifier or _default_classifier)(_PROMPT.format(query=query))
    except Exception as exc:
        # Fail open. The rules already produced a usable decision.
        logger.warning("supervisor tiebreak unavailable (%s); keeping rule decision", exc)
        return decision

    route = _parse_route(raw or "")
    if route is None:
        logger.info("supervisor tiebreak returned no usable route; keeping rule decision")
        return decision

    _CACHE[key] = route.value
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)

    return _apply(decision, route, cached_hit=False)


def _apply(
    decision: RouteDecision, route: AgentRoute | None, *, cached_hit: bool
) -> RouteDecision:
    """Build the replacement decision, or keep the original."""
    if route is None or route == decision.route:
        return decision

    suffix = " (cached)" if cached_hit else ""
    logger.info(
        "supervisor tiebreak: %s -> %s%s", decision.route.value, route.value, suffix
    )
    return RouteDecision(
        route=route,
        reason=f"LLM tiebreak on low rule confidence ({decision.confidence:.2f})",
        confidence=TIEBREAK_CONFIDENCE,
        # The rules matched nothing specific, so they suggested nothing
        # specific. Carrying an empty whitelist lets Tool RAG select for
        # the new route instead of constraining it to the old one's.
        suggested_tools=list(decision.suggested_tools),
        clarification_question=decision.clarification_question,
    )


def cache_clear() -> None:
    """Drop the classification cache — for tests and admin reload."""
    _CACHE.clear()


def cache_size() -> int:
    return len(_CACHE)

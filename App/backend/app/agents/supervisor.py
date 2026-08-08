"""Supervisor agent — classifies queries and routes to the right path.

Two-tier classifier:

1. **Rule-based fast path** — regex + keyword tables, returns in <1 ms
   with a confidence score.  Catches the common cases: calculator
   questions, date questions, rate lookups, escalation triggers.

2. **LLM fallback** (optional) — when rule-based confidence is below
   ``SUPERVISOR_LLM_THRESHOLD``, ask a small LLM call for a second
   opinion.  Currently a stub that returns the rule decision
   unchanged — a later commit can swap in a real LLM classifier if
   rule coverage proves insufficient.

The supervisor itself is pure-Python: no network, no model load.
The actual RAG / tool / specialist execution happens in the
service layer — the supervisor just returns a :class:`RouteDecision`.

**Locale.**  The pattern tables live in :mod:`app.agents.patterns`,
keyed by locale.  ``classify(query)`` with no locale, and every locale
whose tables are absent, resolves to the English tables exactly as
before; a locale with tables gets English *plus* its own, English
first.  The tables are the only thing locale changes — the ordering of
the checks below, and what each one decides, are shared.  Gated by
``FLAG_MULTILINGUAL_ROUTING``: off, every request classifies against
English regardless of the locale passed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..calculator_router import (
    INTENT_TOOLS,
    detect_calculator_intent,
    has_money_amount,
)
from ..flags import flags
from ..text_signals import CLARIFICATION_PROMPT
from .patterns import any_match, for_locale, supported_locales
from .state import AgentRoute, RouteDecision

if TYPE_CHECKING:
    from ..tools import Tool  # noqa: F401

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rule patterns
# ---------------------------------------------------------------------------
#
# The tables live in :mod:`app.agents.patterns`, keyed by locale;
# ``patterns.en`` holds the original English ones verbatim.  They are
# resolved per request through ``for_locale`` rather than bound at
# import time, because the locale is not known until a query arrives.

#: Characters of a query the ``.*``-bearing patterns are allowed to see.
#:
#: The calculator and rate patterns use ``.*`` and lookaheads, which
#: rescan from every start position — quadratic in the subject length.
#: Measured on the calculator set: a 20,000-space query takes 6.8
#: seconds, which is a request worker stalled by one message. A routing
#: decision never needs more than the opening of a question, and a
#: multi-kilobyte "query" is not one.
#:
#: The escalation, greeting, temporal and customs patterns are plain
#: alternations with no ``.*``, so they stay linear and keep matching the
#: **whole** query — "I want to speak to a human" must still escalate
#: however far into a long message it appears.
_MAX_PATTERN_CHARS = 1000

#: Below this word count a stop-word-only query is a clarification.
_CLARIFY_MIN_WORDS = 2


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------
class Supervisor:
    """Routes a query to the right downstream execution path."""

    def _patterns_for(self, locale: str):
        """Tables for *locale*, or English when the flag is closed.

        The gate is here rather than at the call sites so that every
        entry point — service, graph runtime, voice planner — is on the
        same side of the flag. A locale that reaches routing while the
        flag is off must classify exactly as it did before, which is
        what makes the rollout reversible.
        """
        if not flags.is_enabled("multilingual_routing"):
            return for_locale("en")
        return for_locale(locale)

    def classify(
        self,
        query: str,
        has_conversation_history: bool = False,
        locale: str = "en",
    ) -> RouteDecision:
        """Return a :class:`RouteDecision` for *query*.

        Rule-based first.  LLM fallback would go here but is a no-op
        in this commit — the rule patterns cover the top ~90% of URA
        query shapes.

        *locale* selects the pattern tables.  It defaults to English and
        falls back to English for any locale without tables, so every
        existing caller keeps its current behaviour.
        """
        q = (query or "").strip()
        if not q:
            return RouteDecision(
                route=AgentRoute.CLARIFY,
                reason="empty query",
                confidence=1.0,
                clarification_question=(
                    "What would you like to know about URA services, "
                    "tax policy, or filing procedures?"
                ),
            )

        pats = self._patterns_for(locale)
        words = q.split()
        # Subject for the ``.*``-bearing patterns only — see
        # _MAX_PATTERN_CHARS.  Escalation and the other linear
        # patterns below still match against the full query.
        probe = q[:_MAX_PATTERN_CHARS]

        # 1. Check for explicit escalation triggers FIRST — they take
        #    precedence over any other intent.
        for pat, reason in pats.escalate:
            if pat.search(q):
                logger.info("supervisor: ESCALATE pattern=%r reason=%s", pat.pattern[:40], reason)
                return RouteDecision(
                    route=AgentRoute.ESCALATE,
                    reason=reason,
                    confidence=0.95,
                )

        # 1b. Greetings — respond warmly without retrieval.
        q_lower = q.lower().strip("!.?,")
        if len(words) <= 3 and (
            q_lower in pats.greeting_words
            or q_lower in pats.greeting_phrases
            or all(w.lower().strip("!.?,") in pats.greeting_words for w in words)
        ):
            return RouteDecision(
                route=AgentRoute.GREET,
                reason="greeting",
                confidence=1.0,
            )

        # 2. Clarify very short / stop-word-only queries when there's
        #    no conversation history to disambiguate them.
        if (
            len(words) < _CLARIFY_MIN_WORDS
            and not has_conversation_history
            and words
            and words[0].lower() in pats.clarify_stop_words
        ):
            return RouteDecision(
                route=AgentRoute.CLARIFY,
                reason=f"too short ({len(words)} word(s))",
                confidence=0.9,
                clarification_question=CLARIFICATION_PROMPT,
            )

        # 2b. Learning intents → the education tool.  Checked before the
        #     calculators because "what is VAT?" matches the VAT
        #     calculator's pattern but carries no amount for it to work
        #     with; the guards hand anything numeric, rate-shaped or
        #     time-shaped straight back to the routes below.
        if (
            any_match(pats.learn_intent, probe)
            and any_match(pats.learn_topic, probe)
            and not any_match(pats.amount_cue, probe)
            and not any_match(pats.rate_cue, probe)
            and not any_match(pats.temporal_cue, probe)
        ):
            # Log the decision, not the query — every other branch here
            # logs the pattern that fired, and user text in a log line is
            # a log-injection sink.
            logger.info("supervisor: TOOLS (education) words=%d", len(words))
            return RouteDecision(
                route=AgentRoute.TOOLS,
                reason="Learning intent on a tax concept",
                confidence=0.86,
                suggested_tools=["explain_tax_concept", "search_ura_knowledge_base"],
            )

        # 3. Calculation intents → tool route with calculator whitelist
        for pat, reason, tools in pats.calc:
            if pat.search(probe):
                logger.info("supervisor: TOOLS (calc) pattern=%r", pat.pattern[:40])
                return RouteDecision(
                    route=AgentRoute.TOOLS,
                    reason=reason,
                    confidence=0.92,
                    suggested_tools=tools + ["search_ura_knowledge_base"],
                )

        # 3b. A figure plus a calculator intent is a calculation ask,
        #     whatever the word order.  The patterns above need a trigger
        #     verb *before* the noun, so "what's my take-home pay on a 2M
        #     salary" and "VAT on 500000" fell through to plain RAG — the
        #     model then answered a numeric question from memory, which
        #     is the one thing the calculators exist to prevent.
        #
        #     Detection is reused from calculator_router rather than
        #     re-implemented; it already parses "2M"/"1.5m"/"1,000,000"
        #     and excludes informational asks like "how is PAYE
        #     calculated". Confidence sits below the explicit patterns
        #     because the intent is inferred from shape, not stated.
        calc_intent = detect_calculator_intent(q)
        if calc_intent and has_money_amount(q):
            tool = INTENT_TOOLS.get(calc_intent)
            if tool:
                logger.info("supervisor: TOOLS (amount+intent) intent=%s", calc_intent)
                return RouteDecision(
                    route=AgentRoute.TOOLS,
                    reason=f"{calc_intent} calculation intent (amount present)",
                    confidence=0.8,
                    suggested_tools=[tool, "lookup_rate", "search_ura_knowledge_base"],
                )

        # 4. Temporal intents → tool route with calendar tools
        for pat, reason, tools in pats.temporal:
            if pat.search(q):
                logger.info("supervisor: TOOLS (temporal) pattern=%r", pat.pattern[:40])
                return RouteDecision(
                    route=AgentRoute.TOOLS,
                    reason=reason,
                    confidence=0.9,
                    suggested_tools=tools + ["search_ura_knowledge_base"],
                )

        # 5. Rate-lookup intents → tool route with lookup_rate whitelist
        for pat, reason, tools in pats.rate:
            if pat.search(probe):
                logger.info("supervisor: TOOLS (rate) pattern=%r", pat.pattern[:40])
                return RouteDecision(
                    route=AgentRoute.TOOLS,
                    reason=reason,
                    confidence=0.88,
                    suggested_tools=tools + ["search_ura_knowledge_base"],
                )

        # 6. Customs specialist — vocabulary match routes to specialist
        customs_matches = sum(1 for p in pats.customs if p.search(q))
        if customs_matches >= 1:
            return RouteDecision(
                route=AgentRoute.CUSTOMS_SPECIALIST,
                reason=f"customs vocabulary ({customs_matches} match(es))",
                confidence=0.78,
                suggested_tools=[
                    "calculate_customs_duty",
                    "search_ura_knowledge_base",
                    "get_current_date",
                ],
            )

        # 7. Default: factual URA question → RAG path (existing pipeline)
        return RouteDecision(
            route=AgentRoute.RAG,
            reason="default factual query",
            confidence=0.6,
        )

    def describe(self, locale: str = "en") -> dict[str, int]:
        """Return counts of registered patterns for introspection."""
        pats = for_locale(locale)
        return {
            "calculation_patterns": len(pats.calc),
            "temporal_patterns": len(pats.temporal),
            "rate_patterns": len(pats.rate),
            "customs_patterns": len(pats.customs),
            "escalation_patterns": len(pats.escalate),
        }

    def locales(self) -> list[str]:
        """Locales with their own pattern tables."""
        return supported_locales()


# Module-level singleton the supervisor uses for routing every request.
supervisor = Supervisor()

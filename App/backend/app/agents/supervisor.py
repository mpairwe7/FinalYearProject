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
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from .state import AgentRoute, RouteDecision

if TYPE_CHECKING:
    from ..tools import Tool  # noqa: F401

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rule patterns
# ---------------------------------------------------------------------------

# Calculation intents — the model should not guess numbers here.
# Tools should be called for definitive answers.
_CALC_PATTERNS: list[tuple[re.Pattern[str], str, list[str]]] = [
    (
        re.compile(
            r"\b(how\s+much|calculate|compute|work\s+out|what\s+is)\b.*\b(vat|v\.a\.t)\b",
            re.IGNORECASE,
        ),
        "VAT calculation intent",
        ["calculate_vat", "lookup_rate"],
    ),
    (
        re.compile(
            r"\b(how\s+much|calculate|work\s+out)\b.*\b(paye|take[- ]?home|net\s+pay|salary\s+tax)\b",
            re.IGNORECASE,
        ),
        "PAYE calculation intent",
        ["calculate_paye"],
    ),
    (
        re.compile(
            r"\b(corporation|corporate|company)\s+tax\b.*\b(calculate|how\s+much|on|for)\b",
            re.IGNORECASE,
        ),
        "Corporation tax calculation intent",
        ["calculate_corporation_tax"],
    ),
    (
        re.compile(
            # Order-agnostic lookahead: needs "capital gains" AND a
            # calc/transaction trigger word anywhere in the query.
            r"(?=.*\bcapital\s+gains?\b)"
            r"(?=.*\b(calculate|how\s+much|sold|sell(?:ing)?|gain|profit|cgt)\b)",
            re.IGNORECASE,
        ),
        "Capital gains calculation intent",
        ["calculate_capital_gains"],
    ),
    (
        re.compile(
            # Order-agnostic: needs a customs noun AND a calc trigger
            # anywhere in the query (covers both "how much customs
            # duty" and "customs duty for X how much").
            r"(?=.*\b(import(?:ing)?|landed\s+cost|customs\s+duty|customs|cif)\b)"
            r"(?=.*\b(how\s+much|cost|calculate|estimate)\b)",
            re.IGNORECASE,
        ),
        "Customs duty calculation intent",
        ["calculate_customs_duty", "lookup_rate"],
    ),
]

# Temporal intents — anything relative to "now", "today", "this year".
# Requires the calendar tool for a correct answer.
_TEMPORAL_PATTERNS: list[tuple[re.Pattern[str], str, list[str]]] = [
    (
        re.compile(
            r"\b(today|tomorrow|yesterday|current\s+(?:date|year|fiscal)|now)\b",
            re.IGNORECASE,
        ),
        "Needs current date",
        ["get_current_date"],
    ),
    (
        re.compile(
            r"\b(deadline|due\s+date|when\s+is.*\s+due|next\s+filing|when\s+do\s+i\s+file)\b",
            re.IGNORECASE,
        ),
        "Needs upcoming deadlines",
        ["get_next_deadlines", "get_current_date"],
    ),
    (
        re.compile(
            r"\b(this\s+(month|year|fiscal\s+year|quarter)|fy\d{4})\b",
            re.IGNORECASE,
        ),
        "Needs current fiscal-year context",
        ["get_current_date"],
    ),
]

# Rate-lookup intents — "what's the VAT rate", "current CIT".  These
# should always go through the deterministic rate table, never Qwen.
_RATE_PATTERNS: list[tuple[re.Pattern[str], str, list[str]]] = [
    (
        re.compile(
            r"\b(what(?:'s|\s+is)\s+the|current|applicable)\b.*\b(rate|percentage|%)\b",
            re.IGNORECASE,
        ),
        "Rate lookup intent",
        ["lookup_rate", "list_available_rates"],
    ),
    (
        re.compile(
            r"\blist\s+(?:all\s+)?(?:tax\s+)?rates\b",
            re.IGNORECASE,
        ),
        "Full rate listing",
        ["list_available_rates"],
    ),
]

# Customs specialist triggers — narrower scope, specific vocabulary.
_CUSTOMS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(import|export|customs|bill\s+of\s+lading|cif|eac\s+cet|tariff|clearance)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(declaration|entry|port\s+of\s+entry|goods\s+at\s+the\s+border)\b", re.IGNORECASE
    ),
]

# Escalation triggers — sensitive topics or explicit human requests.
_ESCALATE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b(speak\s+to|talk\s+to|contact|call)\s+(?:a|an|the)?\s*(?:human|person|officer|agent|someone)\b",
            re.IGNORECASE,
        ),
        "User explicitly asked for a human",
    ),
    (
        re.compile(
            r"\b(dispute|objection|audit|assessment\s+is\s+wrong|appeal|court|lawyer|fraud)\b",
            re.IGNORECASE,
        ),
        "Legal / dispute context needs human handling",
    ),
    (
        re.compile(
            r"\b(my\s+tin|my\s+filing|my\s+return|my\s+account|my\s+balance)\b",
            re.IGNORECASE,
        ),
        "Account-specific query — needs authenticated lookup or human",
    ),
]

# Clarification triggers — queries too short or vague to answer.
_CLARIFY_MIN_WORDS = 2
_CLARIFY_STOP_WORDS = {
    "how",
    "what",
    "where",
    "when",
    "who",
    "why",
    "help",
    "hi",
    "hello",
    "hey",
    "tell",
    "info",
}


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------
class Supervisor:
    """Routes a query to the right downstream execution path."""

    def classify(
        self,
        query: str,
        has_conversation_history: bool = False,
    ) -> RouteDecision:
        """Return a :class:`RouteDecision` for *query*.

        Rule-based first.  LLM fallback would go here but is a no-op
        in this commit — the rule patterns cover the top ~90% of URA
        query shapes.
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

        words = q.split()

        # 1. Check for explicit escalation triggers FIRST — they take
        #    precedence over any other intent.
        for pat, reason in _ESCALATE_PATTERNS:
            if pat.search(q):
                logger.info("supervisor: ESCALATE pattern=%r reason=%s", pat.pattern[:40], reason)
                return RouteDecision(
                    route=AgentRoute.ESCALATE,
                    reason=reason,
                    confidence=0.95,
                )

        # 2. Clarify very short / stop-word-only queries when there's
        #    no conversation history to disambiguate them.
        if (
            len(words) < _CLARIFY_MIN_WORDS
            and not has_conversation_history
            and words
            and words[0].lower() in _CLARIFY_STOP_WORDS
        ):
            return RouteDecision(
                route=AgentRoute.CLARIFY,
                reason=f"too short ({len(words)} word(s))",
                confidence=0.9,
                clarification_question=(
                    "Could you share a bit more context? "
                    "For example, are you asking about VAT, PAYE, "
                    "customs, registration, or a specific tax type?"
                ),
            )

        # 3. Calculation intents → tool route with calculator whitelist
        for pat, reason, tools in _CALC_PATTERNS:
            if pat.search(q):
                logger.info("supervisor: TOOLS (calc) pattern=%r", pat.pattern[:40])
                return RouteDecision(
                    route=AgentRoute.TOOLS,
                    reason=reason,
                    confidence=0.92,
                    suggested_tools=tools + ["search_ura_knowledge_base"],
                )

        # 4. Temporal intents → tool route with calendar tools
        for pat, reason, tools in _TEMPORAL_PATTERNS:
            if pat.search(q):
                logger.info("supervisor: TOOLS (temporal) pattern=%r", pat.pattern[:40])
                return RouteDecision(
                    route=AgentRoute.TOOLS,
                    reason=reason,
                    confidence=0.9,
                    suggested_tools=tools + ["search_ura_knowledge_base"],
                )

        # 5. Rate-lookup intents → tool route with lookup_rate whitelist
        for pat, reason, tools in _RATE_PATTERNS:
            if pat.search(q):
                logger.info("supervisor: TOOLS (rate) pattern=%r", pat.pattern[:40])
                return RouteDecision(
                    route=AgentRoute.TOOLS,
                    reason=reason,
                    confidence=0.88,
                    suggested_tools=tools + ["search_ura_knowledge_base"],
                )

        # 6. Customs specialist — vocabulary match routes to specialist
        customs_matches = sum(1 for p in _CUSTOMS_PATTERNS if p.search(q))
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

    def describe(self) -> dict[str, int]:
        """Return counts of registered patterns for introspection."""
        return {
            "calculation_patterns": len(_CALC_PATTERNS),
            "temporal_patterns": len(_TEMPORAL_PATTERNS),
            "rate_patterns": len(_RATE_PATTERNS),
            "customs_patterns": len(_CUSTOMS_PATTERNS),
            "escalation_patterns": len(_ESCALATE_PATTERNS),
        }


# Module-level singleton the supervisor uses for routing every request.
supervisor = Supervisor()

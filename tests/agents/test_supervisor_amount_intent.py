"""Routing a numeric question to the calculators, whatever the word order.

The rule patterns need a trigger verb *before* the noun — "how much VAT
on 50,000" matches, "VAT on 500000" does not. Seven of ten natural
calculation phrasings fell through to plain RAG, where the model then
answered a numeric tax question from memory. That is the single thing
the deterministic calculators exist to prevent.

Detection reuses `calculator_router`, which already parses "2M",
"1.5m" and "1,000,000" and already excludes informational asks. The
supervisor only picks a route, so it can be more liberal than
`plan_calculation`, which executes a calculation and is conservative
for that reason.
"""

from __future__ import annotations

import pytest

from app.agents import AgentRoute, supervisor
from app.calculator_router import (
    INTENT_TOOLS,
    detect_calculator_intent,
    has_money_amount,
)


class TestIntentDetection:
    @pytest.mark.parametrize(
        ("query", "intent"),
        [
            ("What's my take-home pay on a 2M salary?", "paye"),
            ("VAT on 500000", "vat"),
            ("what will I owe on 5m rental income", "rental"),
            ("withholding on 400000", "withholding"),
            ("capital gains on 12m", "capital_gains"),
            ("corporation tax on 40 million", "corporation"),
        ],
    )
    def test_the_calculator_is_identified(self, query, intent):
        assert detect_calculator_intent(query) == intent

    def test_an_informational_ask_is_not_a_calculation(self):
        # "How is PAYE calculated" wants an explanation, not a number.
        assert detect_calculator_intent("how is PAYE calculated") is None
        assert detect_calculator_intent("how is VAT computed") is None

    def test_a_message_with_no_tax_noun_has_no_intent(self):
        assert detect_calculator_intent("I like tax") is None
        assert detect_calculator_intent("") is None

    @pytest.mark.parametrize(
        "query",
        ["2M salary", "1.5m", "UGX 1,000,000", "500000", "20 million", "50k"],
    )
    def test_amounts_are_recognised_in_the_forms_people_write(self, query):
        assert has_money_amount(query)

    def test_a_message_with_no_figure_has_no_amount(self):
        assert not has_money_amount("what is the VAT rate")

    def test_every_intent_maps_to_a_tool(self):
        for intent in ("paye", "vat", "rental", "withholding", "capital_gains",
                       "corporation", "customs"):
            assert INTENT_TOOLS[intent]


class TestRoutingTheMissedPhrasings:
    @pytest.mark.parametrize(
        ("query", "tool"),
        [
            ("What's my take-home pay on a 2M salary?", "calculate_paye"),
            ("take home pay on 2 million", "calculate_paye"),
            ("net pay for 1.5m salary", "calculate_paye"),
            ("VAT on 500000", "calculate_vat"),
            ("what will I owe on 5m rental income", "calculate_rental_tax"),
        ],
    )
    def test_a_figure_plus_an_intent_reaches_the_calculator(self, query, tool):
        decision = supervisor.classify(query)
        assert decision.route == AgentRoute.TOOLS, f"{query} → {decision.route.value}"
        assert tool in decision.suggested_tools

    def test_the_knowledge_base_is_still_offered(self):
        # So the turn can cite guidance alongside the figure.
        decision = supervisor.classify("VAT on 500000")
        assert "search_ura_knowledge_base" in decision.suggested_tools

    def test_confidence_is_below_the_explicit_patterns(self):
        # The intent is inferred from shape, not stated.
        inferred = supervisor.classify("VAT on 500000")
        explicit = supervisor.classify("How much VAT on UGX 50,000?")
        assert inferred.confidence < explicit.confidence


class TestNothingElseMoved:
    @pytest.mark.parametrize(
        ("query", "route"),
        [
            # Informational — must stay on retrieval.
            ("how is PAYE calculated", AgentRoute.RAG),
            ("How do I register a business with URA?", AgentRoute.RAG),
            # Rate lookup, not a calculation.
            ("what is the VAT rate", AgentRoute.TOOLS),
            # Explicit phrasings keep their own higher-confidence route.
            ("How much VAT on UGX 50,000?", AgentRoute.TOOLS),
            # Safety routes outrank everything.
            ("I want to speak to a human", AgentRoute.ESCALATE),
            ("I want to dispute my assessment", AgentRoute.ESCALATE),
            ("hello", AgentRoute.GREET),
            ("Bill of lading requirements", AgentRoute.CUSTOMS_SPECIALIST),
        ],
    )
    def test_existing_routes_are_unchanged(self, query, route):
        assert supervisor.classify(query).route == route

    def test_escalation_still_wins_over_an_amount(self):
        # A figure in the message must not pull a dispute away from a human.
        decision = supervisor.classify("I want to dispute my 5m assessment")
        assert decision.route == AgentRoute.ESCALATE

    def test_a_figure_without_a_tax_noun_is_not_forced_into_a_calculator(self):
        # "I earn 3 million, what do I pay" names no tax type; guessing
        # one would be worse than retrieving guidance.
        assert supervisor.classify("I earn 3 million, what do I pay").route == AgentRoute.RAG

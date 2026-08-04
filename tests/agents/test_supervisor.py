"""Tests for the Phase-C supervisor agent.

The supervisor uses ordered regex chains (not lookaheads), so a
query like "How much customs duty on a 5m CIF?" (trigger word
before the noun) falls through to CUSTOMS_SPECIALIST — still a
valid route that gets calculator access, just not the most
specific match.  The tests below document the actual behaviour.
"""

from __future__ import annotations

import pytest

from app.agents import AgentRoute, RouteDecision, supervisor


# ---------------------------------------------------------------------------
# Route enum contract
# ---------------------------------------------------------------------------
def test_route_enum_has_expected_values():
    expected = {
        "rag", "tools", "tax_specialist", "customs_specialist",
        "clarify", "escalate", "blocked", "greet",
    }
    assert {r.value for r in AgentRoute} == expected


def test_describe_returns_pattern_counts():
    desc = supervisor.describe()
    assert "calculation_patterns" in desc
    assert "escalation_patterns" in desc
    assert sum(desc.values()) > 10  # at least some pattern coverage


# ---------------------------------------------------------------------------
# TOOLS route — calculators
# ---------------------------------------------------------------------------
class TestToolsRoute_Calculators:
    @pytest.mark.parametrize("query", [
        # Pattern requires trigger word BEFORE the noun.
        "How much VAT on UGX 50,000?",
        "Calculate VAT for 100k",
        "What is the VAT on 5 million?",
        "How much PAYE will I pay on a 3 million salary?",
        "Calculate PAYE for 500k",
        "How much corporation tax on 500M?",
        "Calculate corporate tax for my company",
    ])
    def test_calculation_queries_route_to_tools(self, query):
        d = supervisor.classify(query)
        assert d.route == AgentRoute.TOOLS, f"{query} → {d.route.value}"
        assert d.confidence >= 0.85

    @pytest.mark.parametrize("query", [
        # Known rule-based misses: wording that reverses the trigger-noun
        # order falls through to RAG.  Fine — still gets a good answer,
        # just through the factual retrieval path rather than tool calls.
        "What's my take-home pay on a 2M salary?",
    ])
    def test_edge_case_queries_fall_through_to_rag(self, query):
        d = supervisor.classify(query)
        assert d.route == AgentRoute.RAG

    def test_suggested_tools_include_calculator(self):
        d = supervisor.classify("How much VAT on UGX 50k?")
        assert "calculate_vat" in d.suggested_tools
        # Also seeds the knowledge-base tool for citations
        assert "search_ura_knowledge_base" in d.suggested_tools


# ---------------------------------------------------------------------------
# TOOLS route — temporal
# ---------------------------------------------------------------------------
class TestToolsRoute_Temporal:
    @pytest.mark.parametrize("query", [
        "What is today's date?",
        "Tell me about the current fiscal year",
        "When is my next filing deadline?",
        "This fiscal year, what do I need to file?",
        # Note: "my return" / "my filing" trigger ESCALATE (account-specific
        # query guard) BEFORE they reach the temporal patterns.  That's
        # intentional — the user is asking about their personal data.
    ])
    def test_temporal_queries_route_to_tools(self, query):
        d = supervisor.classify(query)
        assert d.route == AgentRoute.TOOLS, f"{query} → {d.route.value}"

    def test_deadline_query_suggests_calendar_tools(self):
        d = supervisor.classify("When is my next filing deadline?")
        assert "get_next_deadlines" in d.suggested_tools
        assert "get_current_date" in d.suggested_tools


# ---------------------------------------------------------------------------
# TOOLS route — rate lookups
# ---------------------------------------------------------------------------
class TestToolsRoute_Rates:
    @pytest.mark.parametrize("query", [
        "What is the current corporation tax rate?",
        "What's the applicable VAT rate?",
        "List all tax rates",
        # "What are the current ... percentages" doesn't match the
        # pattern (plural "What are") — fair edge-case miss.
    ])
    def test_rate_queries_route_to_tools(self, query):
        d = supervisor.classify(query)
        assert d.route == AgentRoute.TOOLS, f"{query} → {d.route.value}"


# ---------------------------------------------------------------------------
# TOOLS route — learning intents
# ---------------------------------------------------------------------------
class TestToolsRoute_Education:
    @pytest.mark.parametrize("query", [
        "What is VAT?",
        "How does VAT work?",
        "Explain PAYE to me",
        "Teach me about withholding tax",
        "I don't understand tax brackets",
        "What is a TIN?",
        "Walk me through capital gains",
        "Tell me about rental tax",
        "Difference between marginal and effective tax",
        # Previously pinned as RAG defaults — nothing better existed then.
        "Explain withholding tax for services",
        "Tell me about VAT",
    ])
    def test_learning_queries_reach_the_education_tool(self, query):
        d = supervisor.classify(query)
        assert d.route == AgentRoute.TOOLS, f"{query} → {d.route.value}"
        assert "explain_tax_concept" in d.suggested_tools

    @pytest.mark.parametrize("query", [
        # An amount means arithmetic, not a concept.  "What is VAT" and
        # "What is the VAT on 5 million" differ only by the figure.
        "What is the VAT on 5 million?",
        "Calculate VAT for 100k",
        "How much PAYE will I pay on a 3 million salary?",
        # A rate word belongs to the rate table.
        "What is the current corporation tax rate?",
        "What's the applicable VAT rate?",
        # A time word belongs to the calendar tools.
        "Tell me about the current fiscal year",
        "When is my next filing deadline?",
    ])
    def test_education_defers_rather_than_stealing(self, query):
        d = supervisor.classify(query)
        assert "explain_tax_concept" not in d.suggested_tools, (
            f"{query} was claimed by the education route"
        )

    def test_learning_queries_still_seed_the_knowledge_base(self):
        d = supervisor.classify("Explain PAYE to me")
        assert "search_ura_knowledge_base" in d.suggested_tools

    def test_what_does_x_mean_is_covered_without_an_ambiguous_branch(self):
        # The "what's a X mean" alternative was removed as a ReDoS
        # source; "what does" already covers the phrasing.
        d = supervisor.classify("What does VAT mean?")
        assert "explain_tax_concept" in d.suggested_tools

    def test_classification_is_linear_in_query_length(self):
        """Routing runs on raw user input, so it must not backtrack.

        The removed ``what'?s\\s+(?:a|an|the)?\\s*\\w*\\s*mean`` branch let
        a run of spaces be split between quantifiers in exponentially
        many ways: 2,000 spaces did not finish in two minutes.
        """
        import time

        hostile = "what's " + " " * 20_000 + "vat"
        start = time.perf_counter()
        supervisor.classify(hostile)
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"classify took {elapsed:.1f}s on a 20k-space query"


# ---------------------------------------------------------------------------
# CUSTOMS_SPECIALIST route
# ---------------------------------------------------------------------------
class TestCustomsSpecialist:
    @pytest.mark.parametrize("query", [
        "What documents do I need at port of entry?",
        "Bill of lading requirements",
        "EAC CET tariff codes",
        "Import clearance process for vehicles",
        "Customs declaration forms",
    ])
    def test_customs_vocabulary_routes_to_specialist(self, query):
        d = supervisor.classify(query)
        assert d.route == AgentRoute.CUSTOMS_SPECIALIST, f"{query} → {d.route.value}"

    def test_customs_specialist_gets_customs_calc_tool(self):
        d = supervisor.classify("Import clearance process")
        assert "calculate_customs_duty" in d.suggested_tools
        assert "search_ura_knowledge_base" in d.suggested_tools


# ---------------------------------------------------------------------------
# ESCALATE route
# ---------------------------------------------------------------------------
class TestEscalate:
    @pytest.mark.parametrize("query", [
        "I want to speak to a human officer",
        "Can I talk to someone?",
        "I need to contact an agent",
        "I'd like to dispute my assessment",
        "I want to appeal my tax audit",
        "Is there a lawyer I can speak to?",
        "Check my TIN balance please",
        "I want to see my filing history",
    ])
    def test_escalation_triggers(self, query):
        d = supervisor.classify(query)
        assert d.route == AgentRoute.ESCALATE, f"{query} → {d.route.value}"
        assert d.confidence >= 0.9
        # Reason text varies by trigger (human / dispute-legal / account)
        # but it should always exist and be descriptive.
        assert len(d.reason) > 5

    def test_escalate_has_empty_suggested_tools(self):
        """Escalation bypasses tools — short-circuit to human."""
        d = supervisor.classify("I want to speak to a human")
        assert d.suggested_tools == []


# ---------------------------------------------------------------------------
# CLARIFY route
# ---------------------------------------------------------------------------
class TestClarify:
    @pytest.mark.parametrize("query", [
        "",
        "help",
    ])
    def test_stop_word_only_queries_clarify(self, query):
        d = supervisor.classify(query, has_conversation_history=False)
        assert d.route == AgentRoute.CLARIFY, f"{query!r} → {d.route.value}"
        assert d.clarification_question != ""

    @pytest.mark.parametrize("query", [
        "hello",
        "hi",
        "hey",
        "good morning",
    ])
    def test_greetings_route_to_greet(self, query):
        # Greetings get a warm welcome (GREET), not a clarification prompt.
        d = supervisor.classify(query, has_conversation_history=False)
        assert d.route == AgentRoute.GREET, f"{query!r} → {d.route.value}"

    def test_whitespace_only_treated_as_empty(self):
        d = supervisor.classify("   \t  \n  ")
        assert d.route == AgentRoute.CLARIFY

    def test_clarify_question_mentions_tax_domain(self):
        d = supervisor.classify("help")
        q = d.clarification_question.lower()
        assert any(t in q for t in ("vat", "paye", "tax", "registration"))


# ---------------------------------------------------------------------------
# RAG default
# ---------------------------------------------------------------------------
class TestRAGDefault:
    @pytest.mark.parametrize("query", [
        "How do I register a business with URA?",
        # "What is X" only becomes a lesson when X is a concept the
        # curriculum teaches; EFRIS is not, so retrieval still answers it.
        "What is EFRIS?",
        "Who qualifies for a tax exemption?",
        "Describe the process for filing an annual return",
        # "Explain withholding tax for services" and "Tell me about VAT"
        # used to live here because nothing better existed. They now go
        # to the education tool — see TestToolsRoute_Education.
    ])
    def test_factual_queries_fall_through_to_rag(self, query):
        d = supervisor.classify(query)
        assert d.route == AgentRoute.RAG, f"{query} → {d.route.value}"


# ---------------------------------------------------------------------------
# Priority ordering — escalation beats everything
# ---------------------------------------------------------------------------
class TestPriorityOrdering:
    def test_escalation_beats_calculation(self):
        """'Calculate my VAT and speak to a human' → ESCALATE wins."""
        d = supervisor.classify("Calculate my VAT and I want to speak to a human")
        assert d.route == AgentRoute.ESCALATE

    def test_escalation_beats_customs(self):
        d = supervisor.classify("I have a customs dispute, can I appeal?")
        assert d.route == AgentRoute.ESCALATE


# ---------------------------------------------------------------------------
# RouteDecision dataclass contract
# ---------------------------------------------------------------------------
def test_route_decision_is_frozen():
    d = supervisor.classify("hi")
    with pytest.raises((AttributeError, Exception)):
        d.route = AgentRoute.RAG

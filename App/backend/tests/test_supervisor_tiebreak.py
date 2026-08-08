"""The LLM second opinion on low-confidence routing.

The supervisor docstring promised this for as long as the module has
existed and nothing implemented it. What these tests pin is mostly what
it is *not allowed* to do: it must not fire on decisions the rules
actually made, it must not talk the system out of an escalation, and it
must not be able to break routing when the model is unavailable — the
rules already produced a usable answer in every one of those cases.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from app.agents.state import AgentRoute, RouteDecision
from app.agents.supervisor import supervisor
from app.agents.tiebreak import (
    DEFAULT_THRESHOLD,
    TIEBREAK_CONFIDENCE,
    cache_clear,
    cache_size,
    refine,
    threshold,
)


def _says(route: str):
    """A classifier that always answers *route*, and counts its calls."""

    calls: list[str] = []

    def classify(prompt: str) -> str:
        calls.append(prompt)
        return f'{{"route": "{route}"}}'

    classify.calls = calls  # type: ignore[attr-defined]
    return classify


LOW = RouteDecision(route=AgentRoute.RAG, reason="default factual query", confidence=0.6)


class Harness(unittest.TestCase):
    def setUp(self) -> None:
        cache_clear()
        self.addCleanup(cache_clear)


class ThresholdTests(Harness):
    def test_a_confident_rule_decision_is_not_second_guessed(self) -> None:
        clf = _says("clarify")
        high = RouteDecision(AgentRoute.TOOLS, "calc", 0.92, ["calculate_vat"])
        self.assertIs(refine("q", high, classifier=clf).route, AgentRoute.TOOLS)
        self.assertEqual(clf.calls, [])  # type: ignore[attr-defined]

    def test_the_default_route_is(self) -> None:
        clf = _says("tools")
        self.assertIs(refine("q", LOW, classifier=clf).route, AgentRoute.TOOLS)

    def test_default_threshold_sits_below_every_matched_rule(self) -> None:
        """0.6 is the fall-through; the next rule confidence up is 0.78."""
        self.assertGreater(DEFAULT_THRESHOLD, 0.6)
        self.assertLessEqual(DEFAULT_THRESHOLD, 0.78)

    def test_threshold_is_configurable(self) -> None:
        with mock.patch.dict(os.environ, {"SUPERVISOR_LLM_THRESHOLD": "0.95"}):
            self.assertEqual(threshold(), 0.95)

    def test_a_malformed_threshold_falls_back_to_the_default(self) -> None:
        with mock.patch.dict(os.environ, {"SUPERVISOR_LLM_THRESHOLD": "high"}):
            self.assertEqual(threshold(), DEFAULT_THRESHOLD)

    def test_threshold_is_clamped(self) -> None:
        with mock.patch.dict(os.environ, {"SUPERVISOR_LLM_THRESHOLD": "5"}):
            self.assertEqual(threshold(), 1.0)


class SafetyTests(Harness):
    def test_an_escalation_cannot_be_overridden(self) -> None:
        """A model must not talk the system out of fetching a person."""
        clf = _says("rag")
        escalated = RouteDecision(AgentRoute.ESCALATE, "asked for a human", 0.1)
        self.assertIs(refine("q", escalated, classifier=clf).route, AgentRoute.ESCALATE)
        self.assertEqual(clf.calls, [])  # type: ignore[attr-defined]

    def test_a_greeting_cannot_be_overridden(self) -> None:
        clf = _says("tools")
        greeted = RouteDecision(AgentRoute.GREET, "greeting", 0.2)
        self.assertIs(refine("q", greeted, classifier=clf).route, AgentRoute.GREET)

    def test_blocked_input_cannot_be_reclassified(self) -> None:
        clf = _says("rag")
        blocked = RouteDecision(AgentRoute.BLOCKED, "injection", 0.1)
        self.assertIs(refine("q", blocked, classifier=clf).route, AgentRoute.BLOCKED)

    def test_the_model_cannot_choose_escalate(self) -> None:
        """Escalating a vague question would send routine traffic to staff."""
        self.assertIs(refine("q", LOW, classifier=_says("escalate")).route, AgentRoute.RAG)

    def test_the_model_cannot_choose_an_invented_route(self) -> None:
        self.assertIs(refine("q", LOW, classifier=_says("teleport")).route, AgentRoute.RAG)


class FailOpenTests(Harness):
    def test_an_exception_keeps_the_rule_decision(self) -> None:
        def explode(_p: str) -> str:
            raise RuntimeError("no model loaded")

        self.assertIs(refine("q", LOW, classifier=explode).route, AgentRoute.RAG)

    def test_empty_output_keeps_the_rule_decision(self) -> None:
        self.assertIs(refine("q", LOW, classifier=lambda _p: "").route, AgentRoute.RAG)

    def test_unparseable_output_keeps_the_rule_decision(self) -> None:
        self.assertIs(
            refine("q", LOW, classifier=lambda _p: "I think maybe?").route, AgentRoute.RAG
        )

    def test_an_empty_query_is_not_sent_to_a_model(self) -> None:
        clf = _says("tools")
        self.assertIs(refine("   ", LOW, classifier=clf).route, AgentRoute.RAG)
        self.assertEqual(clf.calls, [])  # type: ignore[attr-defined]


class ParsingTests(Harness):
    def test_json_wrapped_in_prose_is_accepted(self) -> None:
        clf = lambda _p: 'Sure! {"route": "customs_specialist"} Hope that helps.'  # noqa: E731
        self.assertIs(refine("q", LOW, classifier=clf).route, AgentRoute.CUSTOMS_SPECIALIST)

    def test_a_bare_route_name_is_accepted(self) -> None:
        self.assertIs(refine("q", LOW, classifier=lambda _p: "tools").route, AgentRoute.TOOLS)

    def test_route_names_are_case_insensitive(self) -> None:
        self.assertIs(
            refine("q", LOW, classifier=lambda _p: '{"route": "TOOLS"}').route,
            AgentRoute.TOOLS,
        )


class DecisionShapeTests(Harness):
    def test_an_inferred_route_does_not_outrank_a_stated_one(self) -> None:
        """Tier selection reads this number; an inference is not a match."""
        result = refine("q", LOW, classifier=_says("tools"))
        self.assertEqual(result.confidence, TIEBREAK_CONFIDENCE)
        self.assertLess(result.confidence, 0.78)

    def test_the_reason_names_the_tiebreak(self) -> None:
        self.assertIn("tiebreak", refine("q", LOW, classifier=_says("tools")).reason)

    def test_agreeing_with_the_rules_changes_nothing(self) -> None:
        result = refine("q", LOW, classifier=_says("rag"))
        self.assertIs(result, LOW)


class CacheTests(Harness):
    def test_a_repeated_question_is_not_re_classified(self) -> None:
        clf = _says("tools")
        refine("How do I pay?", LOW, classifier=clf)
        refine("How do I pay?", LOW, classifier=clf)
        self.assertEqual(len(clf.calls), 1)  # type: ignore[attr-defined]

    def test_normalisation_collapses_trivial_variants(self) -> None:
        clf = _says("tools")
        for variant in ("How do I pay?", "how do i pay", "  How   do I pay ?  "):
            refine(variant, LOW, classifier=clf)
        self.assertEqual(len(clf.calls), 1)  # type: ignore[attr-defined]

    def test_a_cached_decision_is_applied(self) -> None:
        clf = _says("customs_specialist")
        first = refine("importing a car", LOW, classifier=clf)
        second = refine("importing a car", LOW, classifier=clf)
        self.assertIs(first.route, AgentRoute.CUSTOMS_SPECIALIST)
        self.assertIs(second.route, AgentRoute.CUSTOMS_SPECIALIST)

    def test_the_cache_is_bounded(self) -> None:
        clf = _says("tools")
        for i in range(700):
            refine(f"question number {i}", LOW, classifier=clf)
        self.assertLessEqual(cache_size(), 512)


class FlagGateTests(Harness):
    def test_flag_off_never_consults_a_model(self) -> None:
        """The default path must stay pure Python with no model load."""
        with mock.patch.dict(os.environ, {"FLAG_SUPERVISOR_LLM_TIEBREAK": "false"}):
            with mock.patch("app.agents.tiebreak.refine") as spy:
                supervisor.classify("something entirely unclassifiable here")
                spy.assert_not_called()

    def test_flag_on_consults_a_model_for_the_default_route(self) -> None:
        with mock.patch.dict(os.environ, {"FLAG_SUPERVISOR_LLM_TIEBREAK": "true"}):
            with mock.patch(
                "app.agents.tiebreak._default_classifier", return_value='{"route": "tools"}'
            ):
                decision = supervisor.classify("something entirely unclassifiable here")
        self.assertIs(decision.route, AgentRoute.TOOLS)

    def test_the_blast_radius_is_the_fall_through_slice_only(self) -> None:
        """Measured, not assumed: 5 of the 36 golden-set cases fall through.

        Those are the ones the rules matched nothing for and defaulted to
        retrieval at 0.6. Every other case is decided at 0.78 or above and
        is never shown to a model — so enabling this can change at most
        that slice.
        """
        from app.agents.eval_routing import GOLDEN_SET

        fall_through = sum(1 for _q, route, _t in GOLDEN_SET if route is AgentRoute.RAG)
        with mock.patch.dict(os.environ, {"FLAG_SUPERVISOR_LLM_TIEBREAK": "true"}):
            with mock.patch(
                "app.agents.tiebreak._default_classifier", return_value='{"route": "rag"}'
            ) as spy:
                for query, _route, _tool in GOLDEN_SET:
                    supervisor.classify(query)
        self.assertEqual(spy.call_count, fall_through)
        self.assertLess(fall_through / len(GOLDEN_SET), 0.2)

    def test_a_model_that_agrees_changes_no_golden_set_route(self) -> None:
        from app.agents.eval_routing import GOLDEN_SET

        with mock.patch.dict(os.environ, {"FLAG_SUPERVISOR_LLM_TIEBREAK": "true"}):
            with mock.patch(
                "app.agents.tiebreak._default_classifier", return_value='{"route": "rag"}'
            ):
                for query, expected, _tool in GOLDEN_SET:
                    self.assertIs(supervisor.classify(query).route, expected, query)

    def test_a_broken_model_changes_no_golden_set_route(self) -> None:
        """Failing open has to hold across the whole set, not one case."""
        from app.agents.eval_routing import GOLDEN_SET

        with mock.patch.dict(os.environ, {"FLAG_SUPERVISOR_LLM_TIEBREAK": "true"}):
            with mock.patch(
                "app.agents.tiebreak._default_classifier", side_effect=RuntimeError("down")
            ):
                for query, expected, _tool in GOLDEN_SET:
                    self.assertIs(supervisor.classify(query).route, expected, query)

    def test_the_routing_eval_stays_offline_with_the_flag_on(self) -> None:
        """The harness says "deterministic and offline"; keep it true.

        It measures *rule* coverage, and it runs in CI on every change.
        Letting a model answer its fall-through cases made the backend
        suite take 229s instead of 37s because each one attempted a real
        model load — and the number it reported would no longer have been
        a property of the rules at all.
        """
        from app.agents.eval_routing import GOLDEN_SET, run_routing_eval

        with mock.patch.dict(os.environ, {"FLAG_SUPERVISOR_LLM_TIEBREAK": "true"}):
            with mock.patch("app.agents.tiebreak._default_classifier") as spy:
                report = run_routing_eval(GOLDEN_SET)
        self.assertEqual(spy.call_count, 0)
        self.assertEqual(report.misses, [])

    def test_an_unavailable_model_does_not_break_routing(self) -> None:
        with mock.patch.dict(os.environ, {"FLAG_SUPERVISOR_LLM_TIEBREAK": "true"}):
            with mock.patch(
                "app.agents.tiebreak._default_classifier", side_effect=RuntimeError("down")
            ):
                decision = supervisor.classify("something entirely unclassifiable here")
        self.assertIs(decision.route, AgentRoute.RAG)


if __name__ == "__main__":
    unittest.main()

"""The routing eval, and the guard that keeps it honest.

Unit tests assert individual cases. They cannot tell you the supervisor
gets 70% of natural calculation phrasings wrong, because a case nobody
wrote a test for is invisible — which is exactly how seven of ten ways
of asking for a tax figure ended up on the retrieval path, answered
from the model's memory.

This suite checks two different things: that the eval itself works
(it must be able to *report* a miss, or it is decoration), and that the
supervisor currently passes the golden set.
"""

from __future__ import annotations

import pytest

from app.agents.eval_routing import GOLDEN_SET, RoutingReport, run_routing_eval
from app.agents.state import AgentRoute


class TestTheEvalCanDetectAMiss:
    """An eval that cannot fail is not measuring anything."""

    def test_a_wrong_route_is_reported(self):
        report = run_routing_eval([("hello", AgentRoute.ESCALATE, "")])
        assert report.accuracy == 0.0
        assert len(report.misses) == 1
        assert "expected escalate" in report.misses[0].describe()

    def test_the_right_route_without_the_tool_is_still_a_miss(self):
        # Routing to the right path while omitting the tool that answers
        # the question is a failure the route alone would hide.
        report = run_routing_eval(
            [("How much VAT on UGX 50,000?", AgentRoute.TOOLS, "calculate_paye")]
        )
        assert report.accuracy == 0.0
        assert "did not offer calculate_paye" in report.misses[0].describe()

    def test_a_correct_case_scores(self):
        report = run_routing_eval([("hello", AgentRoute.GREET, "")])
        assert report.accuracy == 1.0
        assert report.misses == []

    def test_an_empty_set_does_not_divide_by_zero(self):
        assert run_routing_eval([]).accuracy == 0.0


class TestReportShape:
    def test_per_route_counts_are_broken_out(self):
        report = run_routing_eval()
        assert "tools" in report.by_route
        assert report.by_route["tools"]["total"] > 0

    def test_it_serialises(self):
        payload = run_routing_eval().to_dict()
        assert payload["total"] == len(GOLDEN_SET)
        assert 0.0 <= payload["accuracy"] <= 1.0

    def test_prometheus_output_is_scrapeable(self):
        text = run_routing_eval().to_prometheus()
        assert "ura_routing_accuracy " in text
        assert "ura_routing_misses " in text
        assert 'ura_routing_accuracy_by_route{route="tools"}' in text

    def test_it_is_fast_enough_to_run_on_every_change(self):
        # The supervisor is pure Python; this belongs in CI, not nightly.
        assert run_routing_eval().duration_ms < 500


class TestGoldenSetIntegrity:
    def test_every_case_is_well_formed(self):
        for query, route, tool in GOLDEN_SET:
            assert query.strip(), "empty query in the golden set"
            assert isinstance(route, AgentRoute)
            assert isinstance(tool, str)

    def test_there_are_no_duplicate_queries(self):
        queries = [q for q, _, _ in GOLDEN_SET]
        duplicates = {q for q in queries if queries.count(q) > 1}
        assert not duplicates, f"duplicated golden cases: {duplicates}"

    def test_every_route_is_represented(self):
        # A route with no cases is a blind spot by construction.
        covered = {route for _, route, _ in GOLDEN_SET}
        for route in (
            AgentRoute.TOOLS,
            AgentRoute.RAG,
            AgentRoute.ESCALATE,
            AgentRoute.GREET,
            AgentRoute.CLARIFY,
            AgentRoute.CUSTOMS_SPECIALIST,
        ):
            assert route in covered, f"no golden case exercises {route.value}"

    def test_named_tools_exist(self, fresh_registry):
        from app.tools import ToolRegistry

        registered = set(ToolRegistry.names())
        for query, _route, tool in GOLDEN_SET:
            if tool:
                assert tool in registered, f"{query!r} expects unknown tool {tool}"


class TestSupervisorPassesTheGoldenSet:
    def test_no_case_is_misrouted(self):
        report = run_routing_eval()
        assert not report.misses, "misrouted:\n" + "\n".join(
            f"  {m.describe()}" for m in report.misses
        )

    @pytest.mark.parametrize(
        "query",
        [
            "What's my take-home pay on a 2M salary?",
            "VAT on 500000",
            "net pay for 1.5m salary",
        ],
    )
    def test_the_phrasings_that_used_to_miss_are_covered(self, query):
        # Pinned so the regression cannot come back quietly.
        assert any(q == query for q, _, _ in GOLDEN_SET)

    def test_accuracy_does_not_regress(self):
        # A floor rather than an equality: adding a hard case that fails
        # should be possible without breaking the build, but a collapse
        # should not be.
        assert run_routing_eval().accuracy >= 0.95


def _report_for(accuracy: float) -> RoutingReport:
    return RoutingReport(total=10, correct=int(accuracy * 10), duration_ms=1.0)


class TestAccuracyArithmetic:
    def test_accuracy_is_a_ratio(self):
        assert _report_for(0.7).accuracy == 0.7

    def test_zero_total_is_zero_not_an_error(self):
        assert RoutingReport(total=0, correct=0, duration_ms=0.0).accuracy == 0.0

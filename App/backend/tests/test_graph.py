"""The statutory knowledge graph.

Three groups of property. That the graph is a faithful *projection* of
the rate tables — a graph that drifts from the tables the calculators
answer from is worse than none. That traversal is bounded, because it
runs on the request path. And that the joins flat retrieval gets wrong
are actually made: residency, effective dating, and the charges that
stack on one another.
"""

from __future__ import annotations

import datetime as _dt
import os
import unittest
from unittest import mock

from app.graph import reset_graph
from app.graph.build import (
    build_graph,
    build_summary,
    class_for_key,
    display_name,
    is_threshold,
    tax_for_key,
)
from app.graph.query import (
    effective_on,
    history,
    link_entities,
    neighbourhood,
    resolve,
)
from app.graph.schema import Edge, EdgeKind, Node, NodeKind
from app.graph.store import MAX_HOPS, MAX_NODES, InMemoryGraphStore


class KeyParsingTests(unittest.TestCase):
    def test_longest_class_suffix_wins(self) -> None:
        """The single misclassification the graph exists to prevent.

        ``paye_bands_non_resident`` ends with ``_resident``. Filing it
        as a resident rate would reproduce the exact bug — answering a
        non-resident with the resident bands — inside the fix.
        """
        self.assertEqual(class_for_key("paye_bands_non_resident"), "non_resident")
        self.assertEqual(class_for_key("paye_bands_resident"), "resident")

    def test_longest_tax_prefix_wins(self) -> None:
        self.assertEqual(tax_for_key("vat_registration_threshold_annual"), "vat")
        self.assertEqual(tax_for_key("vat_standard"), "vat")
        self.assertEqual(tax_for_key("capital_gains_corporate"), "capital_gains")
        self.assertEqual(tax_for_key("rental_company_expense_cap"), "rental")

    def test_thresholds_are_distinguished_from_rates(self) -> None:
        self.assertTrue(is_threshold("vat_registration_threshold_annual"))
        self.assertTrue(is_threshold("rental_company_expense_cap"))
        self.assertFalse(is_threshold("vat_standard"))

    def test_keys_get_human_labels(self) -> None:
        """A model cannot say "environmental_levy_used_clothing" aloud."""
        self.assertIn("environmental levy", display_name("environmental_levy_used_clothing").lower())
        self.assertIn("employment", display_name("nssf_employee_contribution").lower())

    def test_an_unmapped_key_still_reads_as_words(self) -> None:
        self.assertEqual(display_name("some_new_rate"), "some new rate")


class ProjectionFidelityTests(unittest.TestCase):
    """The graph must not drift from the tables it projects."""

    def setUp(self) -> None:
        self.store = build_graph()

    def test_every_rate_node_matches_its_table_value(self) -> None:
        from app.tax import tables

        checked = 0
        for node in self.store.by_kind(NodeKind.RATE) + self.store.by_kind(NodeKind.THRESHOLD):
            fy = node.props["fiscal_year"]
            key = node.props["key"]
            self.assertEqual(node.props["value"], tables.get_table(fy)[key], f"{key}@{fy}")
            checked += 1
        self.assertGreater(checked, 20)

    def test_unverified_figures_keep_their_mark(self) -> None:
        """"The system is unsure of this one" is part of the answer."""
        from app.tax import tables

        table = tables.get_table("FY2026-27")
        self.assertTrue(table.unverified, "fixture expects some unverified keys")
        for key in table.unverified:
            nodes = [
                n
                for n in self.store.by_kind(NodeKind.RATE) + self.store.by_kind(NodeKind.THRESHOLD)
                if n.props.get("key") == key and n.props.get("fiscal_year") == "FY2026-27"
            ]
            for node in nodes:
                self.assertTrue(node.unverified, key)

    def test_the_build_is_deterministic(self) -> None:
        """A rebuild has to be diffable in review, so it must be stable."""
        self.assertEqual(build_graph().to_dict(), build_graph().to_dict())

    def test_provenance_reaches_every_rate_that_has_it(self) -> None:
        from app.tax import tables

        for fy in tables.list_fiscal_years():
            table = tables.get_table(fy)
            for key in table.legal_basis:
                nodes = [
                    n
                    for n in self.store.by_kind(NodeKind.RATE)
                    + self.store.by_kind(NodeKind.THRESHOLD)
                    if n.props.get("key") == key and n.props.get("fiscal_year") == fy
                ]
                for node in nodes:
                    provisions = self.store.neighbours(node.id, (EdgeKind.IMPOSED_BY,))
                    self.assertTrue(provisions, f"{key}@{fy} has legal_basis but no provision")

    def test_the_graph_stays_small_enough_for_the_offline_bundle(self) -> None:
        """The size that justifies having no database dependency."""
        import json

        stats = self.store.stats()
        self.assertLess(stats["nodes"], 5000)
        payload = json.dumps(self.store.to_dict())
        self.assertLess(len(payload), 2_000_000, "graph outgrew the bundle budget")

    def test_summary_reports_every_join_kind(self) -> None:
        summary = build_summary()
        for kind in ("applies_to", "supersedes", "imposed_by", "computed_on", "gates"):
            self.assertGreater(summary["by_edge_kind"].get(kind, 0), 0, kind)


class StoreTests(unittest.TestCase):
    def _store(self) -> InMemoryGraphStore:
        store = InMemoryGraphStore()
        store.add_node(Node("a", NodeKind.TAX_TYPE, "A"))
        store.add_node(Node("b", NodeKind.RATE, "B"))
        store.add_edge(Edge("b", EdgeKind.RATED_FOR, "a"))
        return store

    def test_nodes_are_idempotent(self) -> None:
        store = self._store()
        store.add_node(Node("a", NodeKind.TAX_TYPE, "A"))
        self.assertEqual(store.stats()["nodes"], 2)

    def test_edges_are_deduplicated(self) -> None:
        """An ingestion that runs twice must not double the graph."""
        store = self._store()
        store.add_edge(Edge("b", EdgeKind.RATED_FOR, "a"))
        self.assertEqual(store.stats()["edges"], 1)

    def test_a_dangling_edge_is_refused(self) -> None:
        """Otherwise traversal results depend on insertion order."""
        store = self._store()
        with self.assertLogs("app.graph.store", level="WARNING"):
            store.add_edge(Edge("b", EdgeKind.RATED_FOR, "nowhere"))
        self.assertEqual(store.stats()["edges"], 1)

    def test_walk_is_bounded_by_hops(self) -> None:
        store = build_graph()
        wide = store.walk(["tax:vat"], hops=99)
        self.assertLessEqual(len(wide), MAX_NODES)

    def test_walk_hop_ceiling_is_enforced_not_trusted(self) -> None:
        """A caller asking for 99 hops gets MAX_HOPS, not 99."""
        store = build_graph()
        self.assertEqual(store.walk(["tax:vat"], hops=99), store.walk(["tax:vat"], hops=MAX_HOPS))

    def test_walk_from_an_unknown_seed_is_empty(self) -> None:
        self.assertEqual(build_graph().walk(["nope"]), [])

    def test_round_trip_through_json(self) -> None:
        original = build_graph()
        restored = InMemoryGraphStore.from_dict(original.to_dict())
        self.assertEqual(restored.to_dict(), original.to_dict())


class EntityLinkingTests(unittest.TestCase):
    def test_non_resident_is_not_read_as_resident(self) -> None:
        self.assertEqual(link_entities("a non-resident consultant")["taxpayer_class"], "non_resident")

    def test_resident_still_links(self) -> None:
        self.assertEqual(link_entities("a resident employee")["taxpayer_class"], "resident")

    def test_the_withholding_verb_links_the_tax(self) -> None:
        """Taxpayers write "what do I withhold", not "withholding tax"."""
        for phrase in ("What do I withhold?", "What is deducted when I pay?"):
            self.assertIn("withholding", link_entities(phrase)["taxes"], phrase)

    def test_a_year_ending_june_resolves_to_its_fiscal_year(self) -> None:
        self.assertEqual(
            link_entities("turnover in the year to June 2026")["fiscal_year"], "FY2025-26"
        )

    def test_an_explicit_fiscal_year_is_read(self) -> None:
        self.assertEqual(link_entities("rates for FY2025-26")["fiscal_year"], "FY2025-26")

    def test_a_bare_calendar_year_is_not_guessed(self) -> None:
        """FY runs July→June, so "in 2026" is genuinely ambiguous."""
        self.assertEqual(link_entities("what changed in 2026")["fiscal_year"], "")

    def test_present_tense_change_is_not_a_history_question(self) -> None:
        """"Does the rental tax I paid change it?" is about an amount."""
        self.assertFalse(link_entities("does the rental tax I paid change it")["asks_about_change"])

    def test_past_tense_changed_is_a_history_question(self) -> None:
        self.assertTrue(link_entities("has the threshold changed, and from when?")["asks_about_change"])

    def test_a_comparison_is_recognised(self) -> None:
        self.assertTrue(link_entities("a dividend versus a payment for goods")["is_comparison"])


class ResolutionTests(unittest.TestCase):
    """The joins flat retrieval keeps getting wrong."""

    def test_a_non_resident_gets_the_non_resident_bands(self) -> None:
        answer = resolve(
            "I am a non-resident earning UGX 300,000 a month. How much PAYE?",
            default_fiscal_year="FY2026-27",
        )
        self.assertTrue(answer.matched)
        classes = {c.taxpayer_class for c in answer.claims if c.taxpayer_class}
        self.assertIn("non_resident", classes)
        self.assertNotIn("resident", classes)

    def test_every_claim_can_cite_its_provision(self) -> None:
        """A claim that cannot say why it is true is not emitted."""
        answer = resolve("What is the corporation tax rate?", default_fiscal_year="FY2026-27")
        self.assertTrue(answer.claims)
        for claim in answer.claims:
            self.assertTrue(claim.provision, claim.subject)

    def test_a_named_payment_type_excludes_the_others(self) -> None:
        """Nine rates for a one-rate question is flat retrieval again."""
        answer = resolve(
            "I am paying a foreign consultant a management fee. What withholding tax?",
            default_fiscal_year="FY2026-27",
        )
        values = {c.value for c in answer.claims}
        self.assertIn(0.15, values)
        self.assertNotIn(0.06, values)

    def test_a_comparison_keeps_both_rates(self) -> None:
        answer = resolve(
            "What withholding applies to a dividend versus a payment for goods?",
            default_fiscal_year="FY2026-27",
        )
        values = {c.value for c in answer.claims}
        self.assertIn(0.15, values)
        self.assertIn(0.06, values)

    def test_a_question_naming_no_tax_does_not_match(self) -> None:
        self.assertFalse(resolve("How do I contact your office?").matched)

    def test_the_computed_on_interaction_is_stated(self) -> None:
        """Currently prose in a prompt; here it is a citable fact."""
        answer = resolve(
            "I am importing a car worth USD 10,000 CIF. What will VAT be charged on?",
            default_fiscal_year="FY2026-27",
        )
        predicates = {c.predicate for c in answer.claims}
        self.assertIn("computed_on", predicates)

    def test_a_gating_threshold_travels_with_its_rate(self) -> None:
        answer = resolve(
            "I am an individual landlord. Do I pay rental tax?",
            default_fiscal_year="FY2026-27",
        )
        self.assertIn("threshold", {c.predicate for c in answer.claims})


class EffectiveDatingTests(unittest.TestCase):
    """The failure mode that is a compliance failure, not a wrong answer."""

    KEY = "vat_registration_threshold_annual"

    def test_a_past_date_gets_the_past_figure(self) -> None:
        answer = effective_on(self.KEY, _dt.date(2026, 3, 1))
        self.assertTrue(answer.matched)
        self.assertEqual(answer.claims[0].value, 150_000_000)
        self.assertEqual(answer.claims[0].fiscal_year, "FY2025-26")

    def test_a_current_date_gets_the_current_figure(self) -> None:
        answer = effective_on(self.KEY, _dt.date(2026, 9, 1))
        self.assertEqual(answer.claims[0].value, 300_000_000)

    def test_the_boundary_falls_on_the_first_of_july(self) -> None:
        self.assertEqual(effective_on(self.KEY, _dt.date(2026, 6, 30)).claims[0].value, 150_000_000)
        self.assertEqual(effective_on(self.KEY, _dt.date(2026, 7, 1)).claims[0].value, 300_000_000)

    def test_a_date_outside_the_tables_says_so(self) -> None:
        answer = effective_on(self.KEY, _dt.date(1999, 1, 1))
        self.assertFalse(answer.matched)
        self.assertIn("no figure", answer.reason)

    def test_history_returns_both_years_newest_first(self) -> None:
        answer = history(self.KEY)
        self.assertEqual([c.fiscal_year for c in answer.claims], ["FY2026-27", "FY2025-26"])
        self.assertIn("changed", answer.reason)

    def test_an_unchanged_figure_says_so(self) -> None:
        self.assertIn("unchanged", history("corporation_tax").reason)

    def test_an_unknown_key_does_not_invent_one(self) -> None:
        self.assertFalse(history("no_such_rate").matched)


class NeighbourhoodTests(unittest.TestCase):
    def test_a_seeded_query_returns_related_nodes(self) -> None:
        self.assertTrue(neighbourhood("VAT on imports"))

    def test_an_unseeded_query_returns_nothing(self) -> None:
        self.assertEqual(neighbourhood("hello there"), [])

    def test_the_neighbourhood_is_bounded(self) -> None:
        self.assertLessEqual(len(neighbourhood("VAT", hops=MAX_HOPS)), MAX_NODES)


class ShadowScoreTests(unittest.TestCase):
    """The gate on ``FLAG_GRAPH_FUSION``."""

    def setUp(self) -> None:
        reset_graph()
        self.addCleanup(reset_graph)

    def test_the_graph_beats_the_flat_baseline_it_replaces(self) -> None:
        from app.agents.eval_multihop import run_multihop_eval
        from app.graph.shadow import graph_answer_for

        def flat(question: str) -> str:
            lowered = question.lower()
            if "non-resident" in lowered and "paye" in lowered:
                return "The first UGX 335,000 is tax-free, so no tax is due."
            if "rent" in lowered:
                return "Rental income is taxed. The rate is 30%."
            return "VAT is charged at 18%."

        baseline = run_multihop_eval(flat)
        graph = run_multihop_eval(graph_answer_for)
        self.assertGreater(graph.accuracy, baseline.accuracy)
        self.assertGreaterEqual(graph.accuracy, 0.75, [m.describe() for m in graph.misses])

    def test_an_unmatched_question_renders_to_nothing(self) -> None:
        """Empty is the honest output; invented prose would not be."""
        from app.graph.shadow import graph_answer_for

        self.assertEqual(graph_answer_for("what is the weather"), "")


class ToolGateTests(unittest.TestCase):
    def test_the_tools_are_registered(self) -> None:
        from app.tools import ToolRegistry

        names = {t.schema.name for t in ToolRegistry.all()}
        self.assertTrue({"graph_resolve_rate", "graph_rate_history", "graph_effective_on"} <= names)

    def test_all_graph_tools_are_read_only_and_low_risk(self) -> None:
        from app.tools import ToolRegistry

        for tool in ToolRegistry.all():
            if tool.schema.namespace == "tax_graph":
                self.assertEqual(tool.schema.risk, "low", tool.schema.name)
                self.assertTrue(tool.schema.read_only, tool.schema.name)

    def test_a_closed_flag_explains_itself(self) -> None:
        """A whitelisted tool must not vanish; it must say why it is off."""
        from app.tools import ToolRegistry

        with mock.patch.dict(os.environ, {"FLAG_TAX_GRAPH": "false"}):
            result = ToolRegistry.call("graph_rate_history", {"rate_key": "corporation_tax"})
        self.assertFalse(result["ok"])
        self.assertIn("FLAG_TAX_GRAPH", result["hint"])

    def test_an_open_flag_answers(self) -> None:
        from app.tools import ToolRegistry

        with mock.patch.dict(os.environ, {"FLAG_TAX_GRAPH": "true"}):
            result = ToolRegistry.call("graph_rate_history", {"rate_key": "corporation_tax"})
        self.assertTrue(result["ok"])
        self.assertTrue(result["claims"])

    def test_a_malformed_date_is_a_readable_error(self) -> None:
        from app.tools import ToolRegistry

        with mock.patch.dict(os.environ, {"FLAG_TAX_GRAPH": "true"}):
            result = ToolRegistry.call(
                "graph_effective_on", {"rate_key": "corporation_tax", "date": "last year"}
            )
        self.assertFalse(result["ok"])
        self.assertIn("ISO date", result["error"])


if __name__ == "__main__":
    unittest.main()

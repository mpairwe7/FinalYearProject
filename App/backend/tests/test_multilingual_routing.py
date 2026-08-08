"""Locale-keyed supervisor routing.

Two things are being pinned. The first is that Luganda routes at all:
measured before the locale tables existed, all twelve real questions in
``Data/eval/rag_eval_lg.jsonl`` fell through every table to plain
retrieval, including ``"Njagala okwogera n'omuntu"`` — an explicit
request for a human that was silently answered by a bot.

The second, and the one that must never break, is that adding locales
did not move English. The English tables were moved verbatim and are
always tried first, so this is a structural property rather than a
coincidence — and these tests are what say so.
"""

from __future__ import annotations

import json
import os
import pathlib
import unittest
from unittest import mock

from app.agents.eval_routing import (
    GOLDEN_SET,
    GOLDEN_SET_LG,
    GOLDEN_SETS,
    LOCALE_GATE_THRESHOLD,
    locale_gate,
    run_routing_eval,
)
from app.agents.patterns import for_locale, supported_locales
from app.agents.state import AgentRoute
from app.agents.supervisor import supervisor

_LG_CORPUS = (
    pathlib.Path(__file__).resolve().parents[3] / "Data" / "eval" / "rag_eval_lg.jsonl"
)


def _multilingual_on() -> mock._patch_dict:
    return mock.patch.dict(os.environ, {"FLAG_MULTILINGUAL_ROUTING": "true"})


class EnglishIsUnchangedTests(unittest.TestCase):
    """The refactor's contract: English routing does not move."""

    def test_english_golden_set_still_perfect(self) -> None:
        report = run_routing_eval(GOLDEN_SET, locale="en")
        self.assertEqual(report.misses, [])
        self.assertEqual(report.correct, report.total)

    def test_english_unchanged_with_the_flag_on(self) -> None:
        with _multilingual_on():
            report = run_routing_eval(GOLDEN_SET, locale="en")
        self.assertEqual(report.misses, [])

    def test_luganda_locale_does_not_change_english_queries(self) -> None:
        """A locale extension may add coverage, never pre-empt English."""
        with _multilingual_on():
            for query, expected, _tool in GOLDEN_SET:
                self.assertEqual(
                    supervisor.classify(query, locale="lg", allow_tiebreak=False).route,
                    expected,
                    query,
                )

    def test_unknown_locale_falls_back_to_english(self) -> None:
        with _multilingual_on():
            report = run_routing_eval(GOLDEN_SET, locale="zz")
        self.assertEqual(report.misses, [])

    def test_regional_locale_tag_resolves_to_its_language(self) -> None:
        self.assertIs(for_locale("lg-UG"), for_locale("lg"))
        self.assertIs(for_locale("lg_UG"), for_locale("lg"))

    def test_empty_locale_is_english(self) -> None:
        self.assertIs(for_locale(""), for_locale("en"))


class FlagGateTests(unittest.TestCase):
    def test_flag_off_classifies_luganda_against_english(self) -> None:
        """Off, the locale argument must have no effect at all."""
        with mock.patch.dict(os.environ, {"FLAG_MULTILINGUAL_ROUTING": "false"}):
            decision = supervisor.classify(
                "Njagala okwogera n'omuntu", locale="lg", allow_tiebreak=False
            )
        self.assertEqual(decision.route, AgentRoute.RAG)

    def test_flag_on_routes_luganda(self) -> None:
        with _multilingual_on():
            decision = supervisor.classify(
                "Njagala okwogera n'omuntu", locale="lg", allow_tiebreak=False
            )
        self.assertEqual(decision.route, AgentRoute.ESCALATE)


class LugandaRoutingTests(unittest.TestCase):
    def test_luganda_golden_set_clears_the_gate(self) -> None:
        with _multilingual_on():
            report = run_routing_eval(GOLDEN_SET_LG, locale="lg")
        self.assertGreaterEqual(report.accuracy, LOCALE_GATE_THRESHOLD)
        self.assertEqual(report.misses, [], [m.describe() for m in report.misses])

    def test_request_for_a_human_escalates(self) -> None:
        """The miss that mattered most: a bot answering "get me a person"."""
        with _multilingual_on():
            for query in (
                "Njagala okwogera n'omuntu",
                "Njagala okwogera n'omukozi wa URA",
                "Njagala omuntu annyambe",
            ):
                self.assertEqual(
                    supervisor.classify(query, locale="lg", allow_tiebreak=False).route,
                    AgentRoute.ESCALATE,
                    query,
                )

    def test_objection_escalates(self) -> None:
        with _multilingual_on():
            decision = supervisor.classify(
                "Nkola ntya okuwakanya assessment y'omusolo?",
                locale="lg",
                allow_tiebreak=False,
            )
        self.assertEqual(decision.route, AgentRoute.ESCALATE)

    def test_meka_without_an_amount_is_a_rate_lookup(self) -> None:
        """"How much is VAT" has nothing for a calculator to calculate."""
        with _multilingual_on():
            decision = supervisor.classify(
                "Omusolo gwa VAT gw'ameka mu Uganda?", locale="lg", allow_tiebreak=False
            )
        self.assertEqual(decision.route, AgentRoute.TOOLS)
        self.assertIn("lookup_rate", decision.suggested_tools)
        self.assertNotIn("calculate_vat", decision.suggested_tools)

    def test_meka_with_an_amount_is_a_calculation(self) -> None:
        """And this one is routed by the pre-existing numeric path."""
        with _multilingual_on():
            decision = supervisor.classify("VAT ku 500000 y'emeka?", locale="lg", allow_tiebreak=False)
        self.assertEqual(decision.route, AgentRoute.TOOLS)
        self.assertIn("calculate_vat", decision.suggested_tools)

    def test_code_switched_calculation_worked_before_the_locale_tables(self) -> None:
        """Pins why no Luganda calculator patterns were added.

        ``has_money_amount`` keys on digits and ``detect_calculator_intent``
        keys on the English tax noun, so this already routed under the
        English tables alone.
        """
        decision = supervisor.classify("VAT ku 500000 y'emeka?", locale="en", allow_tiebreak=False)
        self.assertEqual(decision.route, AgentRoute.TOOLS)
        self.assertIn("calculate_vat", decision.suggested_tools)

    def test_what_happens_is_retrieval_not_education(self) -> None:
        """"kiki ekibaawo" asks a consequence, not a definition.

        Retrieval holds the 2%-per-month penalty figure; the education
        tool teaches concepts and would answer this one worse.
        """
        with _multilingual_on():
            for query in (
                "Kiki ekibaawo bw'osasula omusolo nga wayiise obudde?",
                "Bwe nsazaamu obutaggya return y'omusolo, kiki ekibaawo?",
            ):
                self.assertEqual(
                    supervisor.classify(query, locale="lg", allow_tiebreak=False).route,
                    AgentRoute.RAG,
                    query,
                )

    def test_postposed_kye_ki_is_a_learning_intent(self) -> None:
        with _multilingual_on():
            decision = supervisor.classify("Withholding tax kye ki?", locale="lg", allow_tiebreak=False)
        self.assertEqual(decision.route, AgentRoute.TOOLS)
        self.assertIn("explain_tax_concept", decision.suggested_tools)

    def test_bare_ki_asks_for_clarification_not_a_greeting(self) -> None:
        with _multilingual_on():
            decision = supervisor.classify("ki", locale="lg", allow_tiebreak=False)
        self.assertEqual(decision.route, AgentRoute.CLARIFY)

    def test_luganda_greetings(self) -> None:
        with _multilingual_on():
            for query in ("oli otya", "wasuze otya", "gyebale"):
                self.assertEqual(
                        supervisor.classify(query, locale="lg", allow_tiebreak=False).route,
                    AgentRoute.GREET,
                    query,
                )

    def test_obudde_is_not_a_date_question(self) -> None:
        """"wayiise obudde" is late payment, not "what time is it"."""
        with _multilingual_on():
            decision = supervisor.classify(
                "Kiki ekibaawo bw'osasula omusolo nga wayiise obudde?", locale="lg"
            , allow_tiebreak=False)
        self.assertNotIn("get_current_date", decision.suggested_tools)


class CorpusCoverageTests(unittest.TestCase):
    """The golden set must stay tied to the real corpus."""

    @unittest.skipUnless(_LG_CORPUS.exists(), "Luganda eval corpus not present")
    def test_every_corpus_question_is_in_the_golden_set(self) -> None:
        corpus = {
            json.loads(line)["question"]
            for line in _LG_CORPUS.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        covered = {q for q, _r, _t in GOLDEN_SET_LG}
        self.assertEqual(corpus - covered, set())

    @unittest.skipUnless(_LG_CORPUS.exists(), "Luganda eval corpus not present")
    def test_no_corpus_question_falls_to_the_default_route_silently(self) -> None:
        """RAG is a legitimate destination — but it must be *chosen*.

        Before the locale tables every one of these landed on RAG by
        exhausting the tables. The golden set now asserts a route for
        each, so a future regression back to the default shows up as a
        miss rather than as unchanged-looking behaviour.
        """
        expected = {q: r for q, r, _t in GOLDEN_SET_LG}
        with _multilingual_on():
            for line in _LG_CORPUS.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                question = json.loads(line)["question"]
                self.assertEqual(
                    supervisor.classify(question, locale="lg", allow_tiebreak=False).route,
                    expected[question],
                    question,
                )


class LocaleGateTests(unittest.TestCase):
    def test_corpus_backed_locales_pass(self) -> None:
        with _multilingual_on():
            for locale in ("en", "lg"):
                allowed, why = locale_gate(locale)
                self.assertTrue(allowed, why)

    def test_seed_locales_are_refused(self) -> None:
        """Runyankole and Acholi ship as vocabulary, not as evidence."""
        for locale in ("nyn", "ach"):
            allowed, why = locale_gate(locale)
            self.assertFalse(allowed, locale)
            self.assertIn(locale, why)

    def test_seed_locales_are_marked_not_corpus_backed(self) -> None:
        for locale in ("nyn", "ach"):
            self.assertFalse(for_locale(locale).corpus_backed, locale)

    def test_shipped_locales_are_registered(self) -> None:
        self.assertEqual(supported_locales(), ["en", "ach", "lg", "nyn"])

    def test_every_golden_set_locale_has_tables(self) -> None:
        for locale in GOLDEN_SETS:
            self.assertEqual(for_locale(locale).locale, locale)


class AcholiCollisionTests(unittest.TestCase):
    def test_tin_is_not_a_temporal_cue_in_acholi(self) -> None:
        """Acholi "tin" means today; in URA traffic it is the tax number.

        Matching it would send "how do I get a TIN" to the calendar, in
        the locale least able to notice the answer is off-topic.
        """
        with _multilingual_on():
            decision = supervisor.classify(
                "How do I get a TIN?", locale="ach", allow_tiebreak=False
            )
        self.assertNotIn("get_current_date", decision.suggested_tools)


class PerformanceTests(unittest.TestCase):
    def test_routing_stays_in_the_low_milliseconds(self) -> None:
        """Locale merging must not cost the sub-2ms routing budget."""
        with _multilingual_on():
            report = run_routing_eval(GOLDEN_SET_LG, locale="lg")
        self.assertLess(report.duration_ms / max(report.total, 1), 1.0)


if __name__ == "__main__":
    unittest.main()

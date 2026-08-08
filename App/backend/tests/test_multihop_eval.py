"""The multi-hop golden set, and the harness that scores it.

This ships before any graph code, so what it pins is the *instrument*,
not a result: that the set stays tied to the live rate tables, that the
scorer cannot pass an answer by accident, and that the traps describe
joins flat retrieval actually gets wrong.

If the instrument is wrong, every later comparison between the flat
baseline and the graph is wrong too — and in the direction that makes
the graph look good, because a lenient scorer passes everything.
"""

from __future__ import annotations

import unittest

from app.agents.eval_multihop import (
    GOLDEN_SET_MULTIHOP,
    HOP_KINDS,
    MultiHopCase,
    describe,
    run_multihop_eval,
    score_answer,
    verify_against_tables,
)


class SetIntegrityTests(unittest.TestCase):
    def test_every_case_is_consistent_with_the_live_tables(self) -> None:
        """A rate change must break this, not silently rot the set."""
        self.assertEqual(verify_against_tables(), [])

    def test_every_case_is_actually_multi_hop(self) -> None:
        for case in GOLDEN_SET_MULTIHOP:
            self.assertGreaterEqual(case.hop_count, 2, case.question)

    def test_every_case_names_its_trap(self) -> None:
        """A case without a stated failure mode is untestable folklore."""
        for case in GOLDEN_SET_MULTIHOP:
            self.assertTrue(case.trap.strip(), case.question)

    def test_every_case_asserts_something(self) -> None:
        for case in GOLDEN_SET_MULTIHOP:
            self.assertTrue(
                case.must_mention or case.must_not_mention, case.question
            )

    def test_hop_kinds_are_from_the_declared_vocabulary(self) -> None:
        for case in GOLDEN_SET_MULTIHOP:
            for kind in case.hops:
                self.assertIn(kind, HOP_KINDS, case.question)

    def test_questions_are_unique(self) -> None:
        questions = [c.question for c in GOLDEN_SET_MULTIHOP]
        self.assertEqual(len(questions), len(set(questions)))

    def test_coverage_spans_every_join_kind(self) -> None:
        """A dimension with no cases is a dimension nobody is measuring."""
        coverage = describe()["by_hop_kind"]
        for kind in HOP_KINDS:
            self.assertGreater(coverage[kind], 0, kind)

    def test_the_set_spans_more_than_one_fiscal_year(self) -> None:
        """Effective dating is only testable across a rate change."""
        self.assertGreater(len(describe()["fiscal_years"]), 1)


class ScorerTests(unittest.TestCase):
    CASE = MultiHopCase(
        question="q",
        hops=("rate_lookup", "taxpayer_class"),
        must_mention=("non-resident", "12"),
        must_not_mention=("30%",),
    )

    def test_a_complete_answer_passes(self) -> None:
        self.assertIsNone(
            score_answer(self.CASE, "A non-resident landlord pays 12% on the rent.")
        )

    def test_a_missing_concept_fails(self) -> None:
        miss = score_answer(self.CASE, "The rate is 12%.")
        self.assertIsNotNone(miss)
        assert miss is not None
        self.assertIn("non-resident", miss.missing)

    def test_a_forbidden_phrase_fails(self) -> None:
        miss = score_answer(self.CASE, "A non-resident pays 12%, or 30% in some cases.")
        self.assertIsNotNone(miss)
        assert miss is not None
        self.assertIn("30%", miss.forbidden_present)

    def test_numbers_match_on_digit_boundaries(self) -> None:
        """"12" must not be satisfied by "2012" or "120,000".

        Substring matching here would let almost any answer containing a
        year or a large figure pass, and the whole set would score 100%
        against a system that answers nothing correctly.
        """
        case = MultiHopCase(question="q", hops=("rate_lookup", "exemption"), must_mention=("12",))
        self.assertIsNotNone(score_answer(case, "In 2012 the rules changed."))
        self.assertIsNotNone(score_answer(case, "The amount is UGX 120,000."))
        self.assertIsNone(score_answer(case, "The rate is 12%."))
        self.assertIsNone(score_answer(case, "It is 12 percent."))

    def test_matching_is_case_insensitive(self) -> None:
        case = MultiHopCase(question="q", hops=("rate_lookup", "exemption"),
                            must_mention=("threshold",))
        self.assertIsNone(score_answer(case, "The THRESHOLD applies."))

    def test_an_empty_answer_never_passes(self) -> None:
        for case in GOLDEN_SET_MULTIHOP:
            self.assertIsNotNone(score_answer(case, ""), case.question)


class HarnessTests(unittest.TestCase):
    def test_a_silent_system_scores_zero(self) -> None:
        report = run_multihop_eval(lambda _q: "")
        self.assertEqual(report.correct, 0)
        self.assertEqual(report.accuracy, 0.0)
        self.assertEqual(len(report.misses), len(GOLDEN_SET_MULTIHOP))

    def test_an_exception_scores_as_a_miss_not_a_crash(self) -> None:
        """The baseline run must survive a backend that falls over."""

        def explode(_q: str) -> str:
            raise RuntimeError("retrieval down")

        report = run_multihop_eval(explode)
        self.assertEqual(report.correct, 0)

    def test_an_oracle_scores_perfectly(self) -> None:
        """Pins that the set is satisfiable — a set nothing can pass
        measures the scorer, not the system."""
        answers = {
            c.question: " ".join(c.must_mention) or "ok" for c in GOLDEN_SET_MULTIHOP
        }
        report = run_multihop_eval(lambda q: answers[q])
        self.assertEqual(report.correct, report.total, [m.describe() for m in report.misses])

    def test_report_breaks_down_by_hop_count_and_kind(self) -> None:
        report = run_multihop_eval(lambda _q: "")
        self.assertTrue(report.by_hop_count)
        self.assertTrue(report.by_hop_kind)
        self.assertEqual(
            sum(b["total"] for b in report.by_hop_count.values()), report.total
        )

    def test_report_serialises(self) -> None:
        import json

        json.dumps(run_multihop_eval(lambda _q: "").to_dict())


class BaselineTests(unittest.TestCase):
    """The flat-retrieval baseline the graph has to beat.

    A keyword-shaped stand-in for what flat retrieval does on these
    questions: it finds the passage naming the tax and quotes the
    headline rate, with no awareness of taxpayer class or fiscal year.
    The point is not to model retrieval precisely — it is to show the
    set discriminates, i.e. that a plausible-looking answer which misses
    the join still fails.
    """

    @staticmethod
    def _headline_only(question: str) -> str:
        if "non-resident" in question.lower() and "paye" in question.lower():
            # The exact confusion the case describes: resident bands.
            return "The first UGX 335,000 is tax-free, so there is no tax to pay."
        if "rent" in question.lower():
            return "Rental income is taxed. The rate is 30%."
        if "vat" in question.lower():
            return "The VAT registration threshold is UGX 300 million."
        return "VAT is charged at 18%."

    def test_a_plausible_headline_answer_still_fails(self) -> None:
        report = run_multihop_eval(self._headline_only)
        self.assertLess(
            report.accuracy,
            0.5,
            f"headline-only answers scored {report.accuracy:.0%} — the set is too lenient",
        )

    def test_the_effective_date_trap_catches_the_current_rate(self) -> None:
        """Quoting today's threshold for last year's question must fail."""
        case = next(c for c in GOLDEN_SET_MULTIHOP if c.fiscal_year == "FY2025-26")
        miss = score_answer(case, "The VAT registration threshold is UGX 300 million.")
        self.assertIsNotNone(miss)

    def test_the_residency_trap_catches_the_resident_bands(self) -> None:
        case = next(c for c in GOLDEN_SET_MULTIHOP if "non-resident" in c.question)
        miss = score_answer(
            case, "The first UGX 335,000 is tax-free, so you pay no tax."
        )
        self.assertIsNotNone(miss)
        assert miss is not None
        self.assertTrue(miss.missing or miss.forbidden_present)


if __name__ == "__main__":
    unittest.main()

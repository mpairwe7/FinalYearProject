"""Acronym/expansion folding in the FAQ binding gate.

The gate scores an FAQ row on how well its *question* balances against the
query, and rejects a row whose question is far broader than what was asked —
"What is VAT?" must not be answered from "Is EFRIS optional for non-VAT
taxpayers?".  That rule read a definition written as "What is PAYE (Pay As You
Earn)?" as three extra subjects rather than one subject spelled twice, so the
query "What is PAYE?" scored **0.0** against the row that defines PAYE.  Every
acronym row written that way was unreachable by its acronym: measured against
the live corpus, AEO, AEOI, DPC and PAYE all scored 0.0 on their own
definitions, and "What is PAYE?" returned no FAQ at all.

These tests pin both halves: the folding must fix the acronym queries, and the
subject-focus rule it relaxes must still reject the case it exists for.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import service  # noqa: E402

CORPUS = {
    "employment": [
        {
            "question": "What is PAYE (Pay As You Earn)?",
            "answer": (
                "Pay As You Earn is tax on the gross salary of employees earning above "
                "UGX 235,000 a month, withheld by the employer."
            ),
        }
    ],
    "customs": [
        {
            "question": "What is the Authorized Economic Operator (AEO) program?",
            "answer": "AEO accredits compliant traders for faster clearance.",
        }
    ],
    "efris": [
        {
            "question": "Is EFRIS optional for non-VAT taxpayers?",
            "answer": "EFRIS is mandatory for VAT-registered taxpayers only.",
        }
    ],
}


class AcronymMiningTest(unittest.TestCase):
    """The miner is strict on purpose — a parenthetical is not an expansion."""

    def test_learns_both_written_orders(self):
        pairs = service.mine_faq_acronyms(
            [
                "What is PAYE (Pay As You Earn)?",
                "the Authorized Economic Operator (AEO) program",
            ]
        )
        self.assertEqual(pairs.get("pay as you earn"), "paye")
        self.assertEqual(pairs.get("authorized economic operator"), "aeo")

    def test_rejects_a_parenthetical_that_is_not_an_expansion(self):
        pairs = service.mine_faq_acronyms(
            [
                "Which disposals are not recognized (no gain taxed, no loss allowed)?",
                "How are passengers cleared (red/green channels)?",
                "How is a company's tax liability determined (example)?",
            ]
        )
        self.assertEqual(pairs, {})

    def test_initials_may_skip_only_function_words(self):
        self.assertTrue(service._acronym_matches("PAYE", "Pay As You Earn"))
        self.assertTrue(service._acronym_matches("FOB", "Free On Board"))
        self.assertTrue(service._acronym_matches("AEOI", "Automatic Exchange of Information"))
        # "Solution" is not a function word, so it cannot be skipped to make
        # the initials line up.
        self.assertFalse(service._acronym_matches("DT", "Digital Tracking Solution"))
        self.assertFalse(service._acronym_matches("XYZ", "Pay As You Earn"))


class AcronymFoldingTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(
            service.install_faq_acronyms, dict(service._FAQ_ACRONYM_EXPANSIONS)
        )
        service.install_faq_acronyms(
            service.mine_faq_acronyms(
                f"{row['question']} {row['answer']}"
                for rows in CORPUS.values()
                for row in rows
            )
        )

    def paye_row(self):
        return CORPUS["employment"][0]

    def test_the_acronym_and_its_expansion_are_one_subject(self):
        self.assertEqual(service._faq_subject_terms("What is PAYE?"), {"paye"})
        self.assertEqual(service._faq_subject_terms("what is pay as you earn"), {"paye"})
        self.assertEqual(
            service._faq_subject_terms(self.paye_row()["question"]), {"paye"}
        )

    def test_coverage_keeps_every_spelling(self):
        """Folding is deliberately confined to the subject view.  Coverage is
        served by redundancy — collapsing it made a real probe fail (see
        ``test_a_spelling_variant_still_clears_the_floor``)."""
        self.assertEqual(
            service._faq_terms("what is pay as you earn"), {"pay", "as", "earn"}
        )

    def test_a_spelling_variant_still_clears_the_floor(self):
        """The coverage-bank probe folding broke when it applied everywhere:
        British "programme" against the corpus's "program" leaves one unmatched
        term, which is survivable across four coverage terms and fatal across
        two."""
        aeo = CORPUS["customs"][0]
        score = service._faq_match_score(
            "What is the Authorized Economic Operator programme?", aeo
        )
        self.assertGreaterEqual(score, service._FAQ_MATCH_MIN)

    def test_a_definition_is_reachable_by_its_acronym(self):
        """The regression: this scored 0.0 because the row's own gloss made its
        question look broader than the query."""
        self.assertEqual(service._faq_match_score("What is PAYE?", self.paye_row()), 1.0)

    def test_the_expansion_still_works(self):
        self.assertGreater(
            service._faq_match_score("what is pay as you earn", self.paye_row()), 0.9
        )

    def test_an_acronym_inside_the_bracket_also_folds(self):
        aeo = CORPUS["customs"][0]
        self.assertGreater(service._faq_match_score("What is AEO?", aeo), 0.0)

    def test_subject_focus_still_rejects_a_broader_question(self):
        """The rule the folding relaxes must keep doing its job: "What is VAT?"
        is contained in the EFRIS question but is not answered by it."""
        self.assertEqual(
            service._faq_match_score("What is VAT?", CORPUS["efris"][0]), 0.0
        )

    def test_folding_is_inert_until_a_corpus_is_loaded(self):
        service.install_faq_acronyms({})
        self.assertEqual(service._fold_acronyms("pay as you earn"), "pay as you earn")
        self.assertEqual(service._faq_subject_terms("what is pay as you earn"), {"pay", "as", "earn"})


class LiveCorpusAcronymTest(unittest.TestCase):
    """The corpus the image ships, not a fixture."""

    @classmethod
    def setUpClass(cls):
        cls.index, _ = service._load_faq_data(service._DATA_DIR)
        if not cls.index:
            raise unittest.SkipTest("FAQ corpus not present")

    def test_acronyms_are_learned_from_the_shipped_corpus(self):
        self.assertGreaterEqual(len(service._FAQ_ACRONYM_EXPANSIONS), 10)
        self.assertEqual(service._FAQ_ACRONYM_EXPANSIONS.get("pay as you earn"), "paye")

    def test_asking_by_acronym_finds_the_definition(self):
        hits = service._simple_search(
            "What is PAYE?", self.index, top_k=3, binding_query="What is PAYE?", locale="en"
        )
        self.assertTrue(hits, "'What is PAYE?' returned no FAQ at all")
        self.assertIn("PAYE", hits[0]["question"])

    def test_every_row_still_answers_its_own_question(self):
        """Folding must not cost self-retrieval. Rank 1 can shuffle between
        near-duplicate rows ("... Digital Tracking Solution" with and without
        its "(DTS)" gloss), so this pins the top 3."""
        missed = []
        for rows in self.index.values():
            for row in rows:
                question = row["question"]
                hits = service._simple_search(
                    question, self.index, top_k=3, binding_query=question, locale="en"
                )
                if question not in [h.get("question") for h in hits]:
                    missed.append(question)
        self.assertEqual(missed[:5], [], f"{len(missed)} rows no longer self-retrieve")


if __name__ == "__main__":
    unittest.main()

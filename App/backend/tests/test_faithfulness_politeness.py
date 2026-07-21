"""Faithfulness must score factual content only: politeness never dilutes the
score, and fabricated figures never hide behind politeness."""

from __future__ import annotations

import unittest

from app.retriever import HybridRetriever

CONTEXTS = [
    "To register for an instant TIN, go to ura.go.ug and click Get a TIN. "
    "Choose Instant TIN, select Individual, enter your NIN and personal "
    "details, and submit.",
    "The standard VAT rate in Uganda is 18 percent for taxable supplies.",
]

BARE_ANSWER = (
    "To register for an instant TIN, go to ura.go.ug and click Get a TIN. "
    "Choose Instant TIN and select Individual. "
    "Enter your NIN and personal details, then submit."
)

POLITE_ANSWER = (
    "Great question! "
    + BARE_ANSWER
    + " I hope this helps! For help, contact URA at https://ura.go.ug, "
    "toll-free 0800 117 000 / 0800 217 000, or WhatsApp 0772 140 000. "
    "You might also want to know about VAT registration."
)


class FaithfulnessPolitenessTests(unittest.TestCase):
    def test_politeness_does_not_dilute_grounded_answer(self) -> None:
        bare = HybridRetriever.compute_faithfulness(BARE_ANSWER, CONTEXTS)
        polite = HybridRetriever.compute_faithfulness(POLITE_ANSWER, CONTEXTS)
        self.assertGreaterEqual(bare, 0.9)
        self.assertGreaterEqual(polite, bare)

    def test_fabricated_claim_in_polite_wrapper_still_fails(self) -> None:
        fabricated = (
            "Great question! The capital gains tax filing deadline is next "
            "Friday and the penalty is UGX 500,000. I hope this helps!"
        )
        score = HybridRetriever.compute_faithfulness(fabricated, CONTEXTS)
        self.assertLess(score, 0.2)

    def test_all_courtesy_answer_with_contexts_scores_one(self) -> None:
        courtesy_only = (
            "Hello, and welcome! I hope this helps. "
            "Feel free to ask if anything else comes up."
        )
        self.assertEqual(
            HybridRetriever.compute_faithfulness(courtesy_only, CONTEXTS), 1.0
        )

    def test_empty_contexts_score_zero(self) -> None:
        self.assertEqual(HybridRetriever.compute_faithfulness(BARE_ANSWER, []), 0.0)
        self.assertEqual(HybridRetriever.compute_faithfulness("", CONTEXTS), 0.0)

    def test_contact_footer_url_fragments_do_not_penalise(self) -> None:
        footer_only = (
            "The standard VAT rate in Uganda is 18 percent. "
            "For help, contact URA at https://ura.go.ug, toll-free "
            "0800 117 000 / 0800 217 000, or WhatsApp 0772 140 000."
        )
        self.assertEqual(
            HybridRetriever.compute_faithfulness(footer_only, CONTEXTS), 1.0
        )


if __name__ == "__main__":
    unittest.main()

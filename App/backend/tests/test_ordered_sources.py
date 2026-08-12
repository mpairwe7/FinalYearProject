"""The Sources block must credit the document the answer came from.

``sources`` was built as ``list({h["source"] for h in hits})``. A set has no
order, so the answer's own document was not reliably first and the list
reshuffled between identical requests, while ``build_citations`` walks ``hits`` in
rank order. The two disagreed in practice: asked "What is withholding tax?", the
reply and its first citation both came from Withholding-Tax-FY-2024-25-1.pdf
(section "WITHHOLDING TAX > WHAT IS WITHHOLDING TAX?") while ``sources[0]`` was a
tax-exemption FAQ. A user reading the Sources pills was pointed at the wrong
document.
"""

from __future__ import annotations

import unittest

from app.retriever import HybridRetriever
from app.service import ordered_sources


class OrderedSourcesTests(unittest.TestCase):
    def test_hit_order_is_preserved(self) -> None:
        hits = [
            {"source": "Withholding-Tax-FY-2024-25-1.pdf"},
            {"source": "ura_tax_exemption_faqs.csv"},
            {"source": "ura_withholding_tax_faqs.csv"},
        ]
        self.assertEqual(
            ordered_sources(hits),
            [
                "Withholding-Tax-FY-2024-25-1.pdf",
                "ura_tax_exemption_faqs.csv",
                "ura_withholding_tax_faqs.csv",
            ],
        )

    def test_the_best_ranked_passage_wins_for_a_repeated_document(self) -> None:
        """Two passages from one file must not push it behind a lower-ranked file."""
        hits = [
            {"source": "handbook.pdf"},
            {"source": "faq.csv"},
            {"source": "handbook.pdf"},
        ]
        self.assertEqual(ordered_sources(hits), ["handbook.pdf", "faq.csv"])

    def test_blank_and_missing_sources_are_skipped(self) -> None:
        hits = [{"source": ""}, {}, {"source": "real.pdf"}, {"source": None}]
        self.assertEqual(ordered_sources(hits), ["real.pdf"])

    def test_the_order_is_deterministic_across_calls(self) -> None:
        """The set-based version reshuffled between identical requests."""
        hits = [{"source": f"doc{i}.pdf"} for i in range(12)]
        results = {tuple(ordered_sources(hits)) for _ in range(20)}
        self.assertEqual(len(results), 1)

    def test_no_hits_yields_no_sources(self) -> None:
        self.assertEqual(ordered_sources([]), [])

    def test_sources_and_citations_agree_on_the_leading_document(self) -> None:
        """The invariant that was violated: whatever citations credits first must
        be what the Sources block leads with."""
        hits = [
            {
                "source": "Withholding-Tax-FY-2024-25-1.pdf",
                "text": "Withholding tax (WHT) is a form of income tax withheld at source.",
                "section": "WITHHOLDING TAX > WHAT IS WITHHOLDING TAX?",
                "page": "",
            },
            {
                "source": "ura_tax_exemption_faqs.csv",
                "text": "Question: What is withholding tax exemption?\nAnswer: ...",
                "section": "Tax Exemption",
                "page": "",
            },
        ]
        citations = HybridRetriever.build_citations(hits)
        self.assertTrue(citations, "expected citations for these hits")
        self.assertEqual(ordered_sources(hits)[0], citations[0]["source"])


if __name__ == "__main__":
    unittest.main()

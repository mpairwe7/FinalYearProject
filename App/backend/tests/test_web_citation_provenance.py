"""A web citation says what kind of evidence it is (G651).

Web hits carry their authority (URA itself, or an adviser's commentary), what
was read (the page, or only a search summary) and whether they are stale, but
`build_citations` dropped all three, so an adviser's search snippet was listed
like a URA document; and a fiscal year regexed out of the snippet text was
shown as the citation's effective date.
"""

from __future__ import annotations

import unittest

from app.retriever import HybridRetriever


def _web_hit(**over):
    hit = {
        "source": "https://www.pwc.com/ug/tax-summary", "url": "https://www.pwc.com/ug/tax-summary",
        "text": "VAT rate in Uganda is 18 percent for FY2024-25.", "title": "Uganda tax summary",
        "live_authority": "commentary", "live_evidence": "search summary, page not opened",
        "live_stale": True,
    }
    hit.update(over)
    return hit


class WebCitationProvenanceTests(unittest.TestCase):
    def test_a_web_citation_carries_its_authority_evidence_and_staleness(self) -> None:
        cit = HybridRetriever.build_citations([_web_hit()])[0]
        self.assertEqual(cit["authority"], "commentary")
        self.assertEqual(cit["evidence"], "search_summary")
        self.assertIs(cit["stale"], True)

    def test_a_document_read_this_turn_is_marked_as_read(self) -> None:
        cit = HybridRetriever.build_citations(
            [_web_hit(live_authority="official", live_evidence="document read this turn", live_stale=False)]
        )[0]
        self.assertEqual((cit["authority"], cit["evidence"], cit["stale"]), ("official", "document", False))

    def test_a_corpus_citation_has_none_of_these(self) -> None:
        cit = HybridRetriever.build_citations([{"source": "vat_act.pdf", "text": "x"}])[0]
        self.assertNotIn("authority", cit)
        self.assertNotIn("evidence", cit)
        self.assertNotIn("stale", cit)


class SearchSummaryEffectiveDateTests(unittest.TestCase):
    def test_a_search_summary_gets_no_effective_date_guessed_from_its_text(self) -> None:
        from app import web_research

        source = web_research.WebSearchResult(
            title="Uganda tax summary", url="https://www.pwc.com/ug/tax-summary",
            snippet="VAT rate is 18 percent for FY2024-25.", tier=2, trust=0.5,
            is_pdf=False, is_spreadsheet=False, fiscal_year="FY2024-25", doc_family="commentary",
        )
        hit = web_research._snippet_hit(source, 0, "vat rate", "2026-11-13")
        self.assertIsNotNone(hit)
        self.assertFalse(hit.get("effective_date"))
        # The year stays on the hit for edition handling; it is not shown as a date.
        self.assertEqual(hit.get("fiscal_year"), "FY2024-25")


if __name__ == "__main__":
    unittest.main()

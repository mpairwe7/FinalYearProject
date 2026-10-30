"""Retrieval must prefer the law in force and honour every withdrawal (G278-G281).

* **Editions (G278).** The rerank step demoted any chunk whose source name or
  text held "2020", "2021" or "2022" as a substring. That demoted the EAC CET
  2022 version and the 2021 immigration fees regulations — both in force —
  and a September 2024 newsletter (``…_202024…``), while never touching a
  FY2019-20 table and never moving with the fiscal year. Editions are now
  read from fiscal-year metadata and measured against the year in force.
* **Tombstones (G279).** A failed read of the store returned every candidate
  unfiltered, and matching was substring containment both ways, so a
  tombstone on ``outdated_vat.pdf`` withdrew ``vat.pdf`` too.
* **Corrective RAG (G280).** It re-searched non-English questions in their own
  language (no translated leg), ran English spelling correction over them,
  and handed the model a merged list nothing had pruned.
* **Reranker scale (G281).** Scores outside [0, 1] were clamped, collapsing
  every passage to 0.0 or 1.0 for abstention.
* **Passage headers.** The prompt now names each passage's edition.
"""

from __future__ import annotations

import math
import unittest
from typing import Any
from unittest import mock

from app import retriever as retriever_mod
from app.corrective_rag import _expand_query, corrective_retrieve
from app.llm import _build_messages, _passage_header
from app.retriever import (
    STALE_EDITION_PENALTY,
    HybridRetriever,
    _filter_tombstoned_candidates,
    demote_stale_editions,
    edition_start_year,
    tombstone_names_source,
)


def _in_force(fy: str):
    return mock.patch("app.query.current_fiscal_year", return_value=fy)


class EditionTests(unittest.TestCase):
    def test_metadata_then_source_label_then_text(self) -> None:
        self.assertEqual(edition_start_year({"fiscal_year": "FY2024-25", "source": "Tax-Amendments-FY-2020-21.pdf"}), 2024)
        self.assertEqual(edition_start_year({"source": "Tax-Amendments-FY-2020-21.pdf"}), 2020)
        self.assertEqual(edition_start_year({"source": "CONSTRUCTION-SECTOR-2022-23-1.pdf"}), 2022)
        self.assertEqual(edition_start_year({"source": "notes.pdf", "text": "Withholding rates 2021: 6%"}), 2021)

    def test_instruments_named_by_year_are_not_editions(self) -> None:
        """Acts, regulations and tariffs keep the year they were made and stay in force."""
        for source in (
            "EAC-CET-2022-VERSION-25TH-JUNE.pdf",
            "DCIC-Uganda-Citizenship-and-Immigration-Control-Fees-Regulations-2021.pdf",
            "10580_DT_LAWS_JULY_2021.pdf",
            "uramedia.ura.go.ug-URA_20Newsletter_20September_202024-H.pdf",
        ):
            with self.subTest(source=source):
                self.assertIsNone(edition_start_year({"source": source, "text": "Customs duty is 25%."}))

    def _hit(self, **fields: Any) -> dict[str, Any]:
        return {
            "text": "The rate is 18%.",
            "score_rerank": 0.8,
            "score_norm": 0.8,
            "score_calibrated": 0.8,
            **fields,
        }

    def test_old_editions_are_demoted_on_both_scores_and_recent_ones_are_not(self) -> None:
        old = self._hit(source="Tax-Amendments-FY-2020-21.pdf")
        recent = self._hit(fiscal_year="FY2024-25")
        tariff = self._hit(source="EAC-CET-2022-VERSION-25TH-JUNE.pdf")
        with _in_force("FY2026-27"):
            demote_stale_editions("What is the excise duty on sugar?", [old, recent, tariff])
        self.assertAlmostEqual(old["score_norm"], 0.8 - STALE_EDITION_PENALTY)
        self.assertAlmostEqual(old["score_rerank"], 0.8 - STALE_EDITION_PENALTY, msg="rank and gate agree")
        self.assertAlmostEqual(old["score_calibrated"], 0.8 - STALE_EDITION_PENALTY)
        self.assertEqual(recent["score_norm"], 0.8)
        self.assertEqual(tariff["score_norm"], 0.8)

    def test_a_question_about_the_year_keeps_its_edition(self) -> None:
        for query in ("What changed in the 2020 amendments?", "VAT changes in FY2020-21", "tax amendments 2021"):
            hit = self._hit(source="Tax-Amendments-FY-2020-21.pdf")
            with self.subTest(query=query), _in_force("FY2026-27"):
                demote_stale_editions(query, [hit])
                self.assertEqual(hit["score_norm"], 0.8)

    def test_the_window_moves_with_the_fiscal_year(self) -> None:
        hit = self._hit(fiscal_year="FY2026-27")
        with _in_force("FY2026-27"):
            demote_stale_editions("What is the VAT rate?", [hit])
        self.assertEqual(hit["score_norm"], 0.8)
        with _in_force("FY2030-31"):
            demote_stale_editions("What is the VAT rate?", [hit])
        self.assertAlmostEqual(hit["score_norm"], 0.8 - STALE_EDITION_PENALTY)

    def test_unreranked_hits_are_left_alone(self) -> None:
        hit = {"source": "Tax-Amendments-FY-2020-21.pdf", "score_rrf": 0.4}
        with _in_force("FY2026-27"):
            demote_stale_editions("What is the VAT rate?", [hit])
        self.assertNotIn("score_norm", hit)


class _Reranker:
    def __init__(self, scores: list[float]) -> None:
        self._scores = scores

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        return list(self._scores[: len(pairs)])


class RerankScaleTests(unittest.TestCase):
    def _rerank(self, scores: list[float]) -> list[dict[str, Any]]:
        retriever = HybridRetriever()
        retriever._reranker = _Reranker(scores)
        candidates = [{"text": f"passage {i}", "source": "ura_vat_faqs.csv"} for i in range(len(scores))]
        with mock.patch("app.flags.flags.is_enabled", return_value=True):
            retriever._rerank("What is the VAT rate?", candidates)
        return candidates

    def test_sigmoid_scores_are_used_as_they_are(self) -> None:
        ranked = self._rerank([0.9, 0.1])
        self.assertEqual([c["score_norm"] for c in ranked], [0.9, 0.1])

    def test_logits_are_squashed_not_clamped_and_counted(self) -> None:
        with mock.patch.object(retriever_mod.metrics, "inc") as inc:
            ranked = self._rerank([9.75, -2.0])
        norms = [c["score_norm"] for c in ranked]
        self.assertAlmostEqual(norms[0], 1 / (1 + math.exp(-9.75)))
        self.assertAlmostEqual(norms[1], 1 / (1 + math.exp(2.0)))
        self.assertTrue(0.0 < norms[1] < 0.2, "a negative logit is weak, not exactly zero")
        inc.assert_any_call("retrieval_rerank_logit_scores_total")


class TombstoneMatchingTests(unittest.TestCase):
    def test_the_same_document_matches(self) -> None:
        self.assertTrue(tombstone_names_source("outdated_vat_guide_2018.pdf", "Outdated_VAT_Guide_2018.pdf"))
        self.assertTrue(tombstone_names_source("https://ura.go.ug/docs/Old-Guide.pdf?v=2#p3", "Old-Guide.pdf"))
        self.assertTrue(tombstone_names_source("Old-Guide.pdf", "https://ura.go.ug/docs/Old-Guide.pdf"))

    def test_a_relative_path_names_the_document_under_any_prefix(self) -> None:
        for source in ("/app/Data/docs/vat_guide_2021.pdf", "https://ura.go.ug/docs/vat_guide_2021.pdf"):
            with self.subTest(source=source):
                self.assertTrue(tombstone_names_source("docs/vat_guide_2021.pdf", source))
        self.assertFalse(tombstone_names_source("docs/vat_guide_2021.pdf", "/app/Data/olddocs/vat_guide_2021.pdf"))

    def test_a_similar_name_does_not(self) -> None:
        self.assertFalse(tombstone_names_source("outdated_vat.pdf", "vat.pdf"))
        self.assertFalse(tombstone_names_source("vat", "ura_vat_faqs.csv"))
        self.assertFalse(tombstone_names_source("archive/2024/guide.pdf", "archive/2025/guide.pdf"))
        self.assertFalse(tombstone_names_source("", "vat.pdf"))


class TombstoneFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        retriever_mod._last_tombstones = None
        self.addCleanup(setattr, retriever_mod, "_last_tombstones", None)
        flags_on = mock.patch("app.flags.flags.is_enabled", return_value=True)
        flags_on.start()
        self.addCleanup(flags_on.stop)
        self.candidates = [
            {"source": "outdated_vat.pdf", "chunk_id": "c1", "text": "VAT is 17%."},
            {"source": "vat.pdf", "chunk_id": "c2", "text": "VAT is 18%."},
            {"source": "ura_paye_faqs.csv", "chunk_id": "c3", "text": "PAYE bands."},
            {"source": "ura_paye_faqs.csv", "chunk_id": "c4", "text": "PAYE is monthly."},
        ]

    def _store(self, *outcomes: Any):
        from app import database as db

        return mock.patch.object(db, "get_active_corpus_tombstones", side_effect=list(outcomes))

    def test_source_and_chunk_tombstones_withdraw_exactly_their_targets(self) -> None:
        rows = [
            {"id": "t1", "source_uri": "outdated_vat.pdf", "chunk_id": ""},
            {"id": "t2", "source_uri": "ura_paye_faqs.csv", "chunk_id": "c3"},
        ]
        with self._store(rows):
            kept = [c["chunk_id"] for c in _filter_tombstoned_candidates(list(self.candidates))]
        self.assertEqual(kept, ["c2", "c4"])

    def test_an_unreadable_store_applies_the_last_good_read(self) -> None:
        rows = [{"id": "t1", "source_uri": "outdated_vat.pdf", "chunk_id": ""}]
        with self._store(rows, RuntimeError("database is locked")), mock.patch.object(
            retriever_mod.metrics, "inc"
        ) as inc:
            _filter_tombstoned_candidates(list(self.candidates))
            kept = [c["chunk_id"] for c in _filter_tombstoned_candidates(list(self.candidates))]
        self.assertNotIn("c1", kept, "the withdrawn passage stays withdrawn through the outage")
        inc.assert_called_with("retrieval_tombstone_read_failures_total")

    def test_an_unreadable_store_never_read_before_filters_nothing(self) -> None:
        with self._store(RuntimeError("no such table")):
            self.assertEqual(len(_filter_tombstoned_candidates(list(self.candidates))), 4)


class _RecordingRetriever:
    def __init__(self, hits: list[dict[str, Any]]) -> None:
        self.hits = hits
        self.calls: list[dict[str, Any]] = []

    def search(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:  # pragma: no cover - unused
        raise AssertionError("search_planned should be preferred")

    def search_planned(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append({"query": query, **kwargs})
        return [dict(h) for h in self.hits]


class _PreLocaleRetriever(_RecordingRetriever):
    """A retriever whose ``search_planned`` predates the *locale* keyword."""

    def search_planned(  # type: ignore[override]
        self, query: str, top_k: int = 4, prefetch_limit: int = 20, filters: Any = None, subject: Any = None
    ) -> list[dict[str, Any]]:
        self.calls.append({"query": query, "top_k": top_k, "filters": filters, "subject": subject})
        return [dict(h) for h in self.hits]


class _BrokenPlanningRetriever(_RecordingRetriever):
    def search_planned(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        raise TypeError("unsupported operand type(s) for +: 'NoneType' and 'float'")


class CorrectiveRagTests(unittest.TestCase):
    def setUp(self) -> None:
        flags_on = mock.patch("app.flags.flags.is_enabled", return_value=True)
        flags_on.start()
        self.addCleanup(flags_on.stop)
        self.weak = [{"id": "w", "text": "weak", "score_norm": 0.40}]

    def test_english_spelling_correction_is_for_english_only(self) -> None:
        with mock.patch("app.query.correct_spelling", side_effect=lambda q: q) as spell:
            _expand_query("Omusolo gwa VAT guli gwa?", "lg")
            spell.assert_not_called()
            _expand_query("What is the VAT rat?", "en")
            spell.assert_called_once()

    def test_the_locale_reaches_the_re_retrieval(self) -> None:
        retriever = _RecordingRetriever([{"id": "s", "text": "strong", "score_norm": 0.95}])
        corrective_retrieve("Omusolo gwa VAT guli gwa?", retriever, self.weak, locale="lg")
        self.assertEqual(retriever.calls[0]["locale"], "lg")

    def test_a_retriever_without_locale_still_works(self) -> None:
        retriever = _PreLocaleRetriever([{"id": "s", "text": "strong", "score_norm": 0.95}])
        hits, corrected = corrective_retrieve("VAT rate", retriever, self.weak, locale="sw")
        self.assertTrue(corrected)
        self.assertNotIn("locale", retriever.calls[0])

    def test_a_type_error_inside_search_is_not_mistaken_for_a_missing_keyword(self) -> None:
        """It used to be swallowed and the search re-run without the locale."""
        with self.assertRaises(TypeError):
            corrective_retrieve("VAT rate", _BrokenPlanningRetriever([]), self.weak, locale="lg")

    def test_the_merged_list_is_pruned_against_the_new_best(self) -> None:
        retriever = _RecordingRetriever([{"id": "s", "text": "strong", "score_norm": 0.95}])
        hits, corrected = corrective_retrieve("VAT rate", retriever, self.weak, locale="en")
        self.assertTrue(corrected)
        self.assertEqual([h["id"] for h in hits], ["s"], "0.40 is noise beside 0.95")


class PassageHeaderTests(unittest.TestCase):
    def test_edition_and_effective_date_are_named(self) -> None:
        header = _passage_header(
            2,
            {"source": "a.pdf", "page": 3, "fiscal_year": "FY2024-25", "effective_date": "2024-07-01"},
        )
        self.assertEqual(header, "[2] Source: a.pdf, Page 3, Edition: FY2024-25, Effective: 2024-07-01")

    def test_a_crawl_time_or_a_repeated_edition_is_not_an_effective_date(self) -> None:
        self.assertEqual(
            _passage_header(1, {"source": "x", "effective_date": "2026-08-03T10:00:00Z", "crawled_at": "2026-08-03T10:00:00Z"}),
            "[1] Source: x",
        )
        self.assertEqual(
            _passage_header(1, {"source": "x", "fiscal_year": "FY2025-26", "effective_date": "FY2025-26"}),
            "[1] Source: x, Edition: FY2025-26",
        )

    def test_rule_8_keeps_the_edition_a_historical_question_asks_for(self) -> None:
        """'Most recent' alone would answer "the FY2021-22 band" with today's figure."""
        from app.llm import SYSTEM_PROMPT

        rule = " ".join(SYSTEM_PROMPT.split())
        self.assertIn("unless the question names an earlier year", rule)

    def test_the_prompt_carries_the_edition(self) -> None:
        user = _build_messages(
            "what is the vat rate?",
            [{"source": "a.pdf", "text": "VAT is 18%.", "fiscal_year": "FY2025-26"}],
        )[-1]["content"]
        self.assertIn("[1] Source: a.pdf, Edition: FY2025-26", user)


if __name__ == "__main__":
    unittest.main()

"""Retrieval robustness: score comparability, dedup, and query caching.

The defect these pin down is scale mixing. A cross-encoder logit runs
roughly -10..10; an RRF score is 1/(60+rank) ≈ 0.016. Code that falls
back from one to the other and then *compares* the results is not
ranking, it is asserting that any reranked hit beats any RRF hit —
including ones the cross-encoder scored as irrelevant.
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.corrective_rag import _improved, _ranking_key
from app.retriever import (
    BM25SparseEncoder,
    HybridRetriever,
    _dedupe_candidates,
    _shingles,
    fiscal_year_rank,
    hit_relevance,
    lexical_relevance,
)


class ScaleComparabilityTests(unittest.TestCase):
    def test_an_irrelevant_reranked_hit_does_not_look_better_than_a_top_rrf_hit(self) -> None:
        # The old key was `score_rerank` else `score_rrf`, so -4.0 vs
        # 0.0163 put a hit the cross-encoder rejected above the best
        # fusion result.
        poor_rerank = {"score_rerank": -4.0}
        good_rrf = {"score_rrf": 0.0163}
        self.assertLess(hit_relevance(poor_rerank), 0.05)
        # Tiers are kept apart rather than compared on one broken scale.
        self.assertNotEqual(_ranking_key(poor_rerank)[0], _ranking_key(good_rrf)[0])

    def test_within_a_tier_ranking_is_by_calibrated_relevance(self) -> None:
        better = {"score_rerank": 3.0}
        worse = {"score_rerank": -1.0}
        self.assertGreater(_ranking_key(better), _ranking_key(worse))

    def test_rrf_only_hits_rank_among_themselves(self) -> None:
        better = {"score_rrf": 0.0163}
        worse = {"score_rrf": 0.0100}
        self.assertGreater(_ranking_key(better), _ranking_key(worse))

    def test_malformed_scores_do_not_raise(self) -> None:
        self.assertEqual(_ranking_key({"score_rrf": "nonsense"}), (0, 0.0))
        self.assertEqual(_ranking_key({}), (0, 0.0))


class ImprovementDecisionTests(unittest.TestCase):
    """"Did re-retrieval help?" must not be decided on mixed scales."""

    def test_same_scale_comparison_is_honoured(self) -> None:
        self.assertTrue(_improved([{"score_norm": 0.9}], [{"score_norm": 0.4}]))
        self.assertFalse(_improved([{"score_norm": 0.2}], [{"score_norm": 0.8}]))

    def test_gaining_a_reranker_signal_counts_as_improvement(self) -> None:
        self.assertTrue(_improved([{"score_norm": 0.6}], [{"score_rrf": 0.0163}]))

    def test_without_any_relevance_signal_only_more_evidence_counts(self) -> None:
        # Averaging RRF scores across two different queries says nothing;
        # the count is the one comparable quantity.
        rrf = {"score_rrf": 0.0163}
        self.assertTrue(_improved([rrf, rrf], [rrf]))
        self.assertFalse(_improved([rrf], [rrf, rrf]))


class DeduplicationTests(unittest.TestCase):
    _PASSAGE = (
        "The standard VAT rate in Uganda is 18 percent on taxable supplies "
        "of goods and services made by a registered person."
    )

    def test_near_identical_passages_collapse_to_one(self) -> None:
        candidates = [
            {"text": self._PASSAGE, "id": "ed2024"},
            {"text": self._PASSAGE + " ", "id": "ed2025"},
            {"text": "PAYE uses progressive monthly bands on employment income.", "id": "paye"},
        ]
        kept = [c["id"] for c in _dedupe_candidates(candidates)]
        self.assertEqual(kept, ["ed2024", "paye"])

    def test_the_first_ranked_copy_is_the_one_kept(self) -> None:
        candidates = [
            {"text": self._PASSAGE, "id": "best"},
            {"text": self._PASSAGE, "id": "worse"},
        ]
        self.assertEqual([c["id"] for c in _dedupe_candidates(candidates)], ["best"])

    def test_genuinely_different_passages_all_survive(self) -> None:
        candidates = [
            {"text": "VAT is charged at 18 percent on taxable supplies.", "id": "a"},
            {"text": "Corporation tax is 30 percent of chargeable income.", "id": "b"},
            {"text": "Rental tax for individuals is 12 percent above the threshold.", "id": "c"},
        ]
        self.assertEqual(len(_dedupe_candidates(candidates)), 3)

    def test_short_and_empty_passages_are_not_dropped(self) -> None:
        candidates = [{"text": "VAT is 18%", "id": "short"}, {"text": "", "id": "empty"}]
        self.assertEqual(len(_dedupe_candidates(candidates)), 2)

    def test_answer_field_is_used_when_text_is_absent(self) -> None:
        candidates = [
            {"answer": self._PASSAGE, "id": "faq1"},
            {"answer": self._PASSAGE, "id": "faq2"},
        ]
        self.assertEqual(len(_dedupe_candidates(candidates)), 1)

    def test_shingles_of_a_short_string_fall_back_to_words(self) -> None:
        self.assertEqual(_shingles("VAT is 18"), frozenset({"vat", "is", "18"}))


class EditionAwareDeduplicationTests(unittest.TestCase):
    """The corpus holds the same guidance across fiscal-year editions, and the
    two phrasings are near-identical text. Retrieval scores therefore cannot
    tell a repealed rate from the one in force — so among *equivalent* passages
    the newer known edition must win regardless of rank, or the oldest handbook
    in the corpus can silently evict the current one."""

    _PASSAGE = (
        "The standard VAT rate in Uganda is 18 percent on taxable supplies "
        "of goods and services made by a registered person."
    )

    def test_fiscal_year_rank_parses_known_labels(self) -> None:
        self.assertEqual(fiscal_year_rank("FY2024-25"), 2024)
        self.assertLess(fiscal_year_rank("FY2023-24"), fiscal_year_rank("FY2025-26"))

    def test_unknown_fiscal_year_is_none_not_zero(self) -> None:
        """``None`` must not compare as older than a real year."""
        for value in ("", None, "2024", "FY2024", "unknown"):
            self.assertIsNone(fiscal_year_rank(value), repr(value))

    def test_newer_edition_wins_even_when_it_ranks_lower(self) -> None:
        candidates = [
            {"text": self._PASSAGE, "id": "stale", "fiscal_year": "FY2023-24"},
            {"text": self._PASSAGE, "id": "current", "fiscal_year": "FY2025-26"},
        ]
        kept = _dedupe_candidates(candidates)
        self.assertEqual([c["id"] for c in kept], ["current"])

    def test_older_edition_never_displaces_a_newer_one(self) -> None:
        candidates = [
            {"text": self._PASSAGE, "id": "current", "fiscal_year": "FY2025-26"},
            {"text": self._PASSAGE, "id": "stale", "fiscal_year": "FY2023-24"},
        ]
        self.assertEqual([c["id"] for c in _dedupe_candidates(candidates)], ["current"])

    def test_unknown_edition_does_not_displace_a_known_one(self) -> None:
        candidates = [
            {"text": self._PASSAGE, "id": "known", "fiscal_year": "FY2025-26"},
            {"text": self._PASSAGE, "id": "unknown", "fiscal_year": ""},
        ]
        self.assertEqual([c["id"] for c in _dedupe_candidates(candidates)], ["known"])

    def test_a_known_edition_does_not_displace_an_unknown_one(self) -> None:
        """Most URA filenames carry no fiscal year, so an unknown label is not
        evidence of staleness — rank order still decides."""
        candidates = [
            {"text": self._PASSAGE, "id": "unknown", "fiscal_year": ""},
            {"text": self._PASSAGE, "id": "known", "fiscal_year": "FY2025-26"},
        ]
        self.assertEqual([c["id"] for c in _dedupe_candidates(candidates)], ["unknown"])

    def test_same_edition_keeps_the_better_ranked_copy(self) -> None:
        candidates = [
            {"text": self._PASSAGE, "id": "best", "fiscal_year": "FY2025-26"},
            {"text": self._PASSAGE, "id": "worse", "fiscal_year": "FY2025-26"},
        ]
        self.assertEqual([c["id"] for c in _dedupe_candidates(candidates)], ["best"])

    def test_different_passages_from_different_editions_all_survive(self) -> None:
        """The override only applies to near-duplicates; distinct evidence from
        an older edition is still evidence."""
        candidates = [
            {"text": self._PASSAGE, "id": "vat", "fiscal_year": "FY2023-24"},
            {
                "text": "Corporation tax is charged at 30 percent of chargeable income.",
                "id": "corp",
                "fiscal_year": "FY2025-26",
            },
        ]
        self.assertEqual(len(_dedupe_candidates(candidates)), 2)

    def test_replacement_survives_a_third_matching_copy(self) -> None:
        """After a replacement the stored signature must still point at the
        kept row, so a later duplicate compares against the winner."""
        candidates = [
            {"text": self._PASSAGE, "id": "oldest", "fiscal_year": "FY2022-23"},
            {"text": self._PASSAGE, "id": "middle", "fiscal_year": "FY2024-25"},
            {"text": self._PASSAGE, "id": "newest", "fiscal_year": "FY2025-26"},
        ]
        self.assertEqual([c["id"] for c in _dedupe_candidates(candidates)], ["newest"])


class QueryEmbeddingCacheTests(unittest.TestCase):
    def test_repeat_queries_reuse_the_embedding(self) -> None:
        from app.retriever import HybridRetriever

        class CountingModel:
            def __init__(self) -> None:
                self.calls = 0

            def encode(self, text: str):
                self.calls += 1

                class _Vec:
                    @staticmethod
                    def tolist() -> list[float]:
                        return [0.1, 0.2]

                return _Vec()

        retriever = HybridRetriever()
        model = CountingModel()
        retriever._dense_model = model

        first = retriever._encode_query("what is the VAT rate")
        second = retriever._encode_query("what is the VAT rate")
        retriever._encode_query("what is the PAYE threshold")

        self.assertEqual(first, second)
        self.assertEqual(model.calls, 2, "identical query must not be re-embedded")

    def test_cache_is_bounded(self) -> None:
        from app import retriever as retriever_module
        from app.retriever import HybridRetriever

        class StubModel:
            @staticmethod
            def encode(text: str):
                class _Vec:
                    @staticmethod
                    def tolist() -> list[float]:
                        return [0.0]

                return _Vec()

        retriever = HybridRetriever()
        retriever._dense_model = StubModel()
        for i in range(retriever_module._QUERY_CACHE_SIZE + 20):
            retriever._encode_query(f"query {i}")
        self.assertLessEqual(
            len(retriever._query_vec_cache), retriever_module._QUERY_CACHE_SIZE
        )


if __name__ == "__main__":
    unittest.main()


class BackendPriorityTests(unittest.TestCase):
    """Retrieval must prefer Qdrant (dense + BM25 + rerank) and fall through to
    the Cloudflare Vectorize dense index before keyword search.

    The regression: Vectorize was only attempted when ``QDRANT_ENABLED=false``,
    so a missing collection, an unreachable host or an encoder mismatch skipped
    tier 2 entirely and degraded straight to keyword — even on a deployment
    holding valid Vectorize credentials.
    """

    def _retriever(self, *, qdrant_ok: bool, vectorize_ok: bool) -> HybridRetriever:
        r = HybridRetriever()
        r._init_qdrant = lambda: qdrant_ok  # type: ignore[method-assign]

        def _vectorize() -> bool:
            if not vectorize_ok:
                return False
            r._vectorize_mode = True
            r._ready = True
            return True

        r._init_vectorize_mode = _vectorize  # type: ignore[method-assign]
        return r

    def test_qdrant_is_used_when_available_and_vectorize_is_not_consulted(self) -> None:
        r = self._retriever(qdrant_ok=True, vectorize_ok=True)
        calls: list[str] = []
        r._init_vectorize_mode = lambda: calls.append("vectorize") or False  # type: ignore[method-assign]
        with mock.patch("app.retriever.QDRANT_ENABLED", True):
            self.assertTrue(r.initialize())
        self.assertEqual(calls, [], "Vectorize must not be probed while Qdrant works")
        self.assertFalse(r._vectorize_mode)

    def test_qdrant_failure_falls_through_to_vectorize(self) -> None:
        r = self._retriever(qdrant_ok=False, vectorize_ok=True)
        with mock.patch("app.retriever.QDRANT_ENABLED", True):
            self.assertTrue(r.initialize())
        self.assertTrue(r._vectorize_mode)
        self.assertEqual(r.backend, "vectorize")

    def test_both_unavailable_reports_keyword(self) -> None:
        r = self._retriever(qdrant_ok=False, vectorize_ok=False)
        with mock.patch("app.retriever.QDRANT_ENABLED", True):
            self.assertFalse(r.initialize())
        self.assertEqual(r.backend, "keyword")

    def test_disabled_qdrant_still_reaches_vectorize(self) -> None:
        r = self._retriever(qdrant_ok=True, vectorize_ok=True)
        with mock.patch("app.retriever.QDRANT_ENABLED", False):
            self.assertTrue(r.initialize())
        self.assertTrue(r._vectorize_mode)

    def test_half_initialised_qdrant_state_is_cleared_before_falling_back(self) -> None:
        """A Qdrant failure can happen after the client is built; leaving it set
        would let search() query the collection this process just rejected."""
        r = HybridRetriever()

        def _failing_qdrant() -> bool:
            r._client = object()
            r._dense_model = object()
            r._sparse_ok = False
            return False

        r._init_qdrant = _failing_qdrant  # type: ignore[method-assign]
        r._init_vectorize_mode = lambda: False  # type: ignore[method-assign]
        with mock.patch("app.retriever.QDRANT_ENABLED", True):
            r.initialize()
        self.assertIsNone(r._client)
        self.assertIsNone(r._dense_model)
        self.assertTrue(r._sparse_ok)

    def test_backend_reports_qdrant_when_ready_without_vectorize_mode(self) -> None:
        r = HybridRetriever()
        r._ready = True
        self.assertEqual(r.backend, "qdrant")


class LexicalRelevanceGateTests(unittest.TestCase):
    """The sparse-only sidecar has no cross-encoder, so every hit reached
    should_abstain carrying only an RRF score, hit_relevance returned None, and the
    guard took its "cannot assess relevance" branch and answered anyway. Safe over
    499 curated FAQ rows behind the question-F1 gate; not safe over 7,000+ raw
    document chunks, where BM25 returns something for every query.

    Observed live before the fix: "What is the capital of France?" answered from a
    chunk about Thales Las France (Tanzania Branch).
    """

    _CHUNK = {
        "text": (
            "Value Added Tax is charged at eighteen percent on taxable supplies "
            "made by a registered person in Uganda."
        ),
        "section": "VAT > 3.1 Rates",
        "source": "TAXATION-HANDBOOK-FY-2025-26-1.pdf",
    }

    def test_unweighted_coverage_counts_matched_content_terms(self) -> None:
        self.assertEqual(lexical_relevance("taxable supplies", self._CHUNK), 1.0)
        self.assertEqual(lexical_relevance("zzzz qqqq", self._CHUNK), 0.0)

    def test_stopwords_and_short_tokens_do_not_inflate_the_score(self) -> None:
        """"What is the ..." must not count as agreement on subject."""
        self.assertEqual(lexical_relevance("what is the", self._CHUNK), 0.0)

    def test_idf_weighting_demotes_terms_the_corpus_uses_everywhere(self) -> None:
        """Plain recall scored "hack into a bank account" 0.667 against this corpus
        because "bank" and "account" are common in it. Weighting by IDF is what
        makes the unmatched, distinctive term dominate."""
        encoder = BM25SparseEncoder().fit(
            [
                "the bank account details for paying tax",
                "a bank account is required for registration",
                "taxable supplies of goods and services",
            ]
        )
        chunk = {"text": "the bank account details for paying tax"}
        unweighted = lexical_relevance("hack bank account", chunk)
        weighted = lexical_relevance("hack bank account", chunk, encoder)
        self.assertGreater(unweighted, weighted)

    def test_an_unseen_term_is_treated_as_maximally_informative(self) -> None:
        encoder = BM25SparseEncoder().fit(["taxable supplies of goods and services"])
        self.assertEqual(encoder.term_idf("nonexistentword"), encoder.max_idf)

    def test_abstains_on_a_stamped_low_relevance_hit(self) -> None:
        from app.guardrails import OutputGuard

        self.assertTrue(
            OutputGuard.should_abstain([{"score_rrf": 0.016, "score_lexical": 0.10}])
        )

    def test_answers_on_a_stamped_high_relevance_hit(self) -> None:
        from app.guardrails import OutputGuard

        self.assertFalse(
            OutputGuard.should_abstain([{"score_rrf": 0.016, "score_lexical": 0.95}])
        )

    def test_a_reranker_score_still_takes_precedence(self) -> None:
        """The cross-encoder is the better signal wherever it exists."""
        from app.guardrails import OutputGuard

        self.assertFalse(
            OutputGuard.should_abstain([{"score_norm": 0.9, "score_lexical": 0.0}])
        )
        self.assertTrue(
            OutputGuard.should_abstain([{"score_norm": 0.01, "score_lexical": 1.0}])
        )

    def test_unstamped_hits_keep_the_permissive_legacy_behaviour(self) -> None:
        """Keyword/FAQ hits arrive unstamped and already carry their own
        authorization gate; scoring them again double-gates them and re-breaks
        distress-framed questions (the bug PR #167 fixed)."""
        from app.guardrails import OutputGuard

        self.assertFalse(OutputGuard.should_abstain([{"_overlap": 3.2, "answer": "..."}]))

    def test_a_malformed_stamp_is_ignored_rather_than_crashing(self) -> None:
        from app.guardrails import OutputGuard

        self.assertFalse(
            OutputGuard.should_abstain([{"score_rrf": 0.016, "score_lexical": "nonsense"}])
        )


class _FakePoint:
    def __init__(self, payload: dict, score: float = 0.5) -> None:
        self.id = payload.get("chunk_id", "p1")
        self.payload = payload
        self.score = score


class _FakeQueryResult:
    def __init__(self, points: list[_FakePoint]) -> None:
        self.points = points


class _FakeQdrantSearch:
    """Minimal stand-in for the bits of QdrantClient that search() touches."""

    def __init__(self, payloads: list[dict]) -> None:
        self._payloads = payloads

    def query_points(self, **kwargs):  # noqa: ANN003, ANN201
        return _FakeQueryResult([_FakePoint(p) for p in self._payloads])


class LexicalStampWiringTests(unittest.TestCase):
    """The guard reads ``score_lexical``, so the retriever must actually set it.
    A correct guard fed unstamped hits silently reverts to answering everything,
    which is the failure this whole change exists to prevent."""

    def _sparse_only_retriever(self, payloads: list[dict]) -> HybridRetriever:
        r = HybridRetriever()
        r._ready = True
        r._sparse_only = True
        r._dense_model = None
        r._client = _FakeQdrantSearch(payloads)
        r._sparse_encoder = BM25SparseEncoder().fit(
            [
                "value added tax is charged at eighteen percent on taxable supplies",
                "a taxpayer identification number is required to register for tax",
                "capital gains on the disposal of a business asset are chargeable",
            ]
        )
        return r

    def test_sparse_only_search_stamps_every_hit(self) -> None:
        payloads = [
            {
                "chunk_id": "c1",
                "text": "Value added tax is charged at eighteen percent on taxable supplies.",
                "doc_type": "pdf_chunk",
                "source": "handbook.pdf",
            }
        ]
        r = self._sparse_only_retriever(payloads)
        hits = r.search("taxable supplies rate", top_k=2)
        self.assertTrue(hits, "expected the fake client to yield a hit")
        for hit in hits:
            self.assertIn("score_lexical", hit)
            self.assertIsInstance(hit["score_lexical"], float)

    def test_an_on_topic_query_stamps_higher_than_an_off_topic_one(self) -> None:
        """The fake client returns the same passage whatever is asked, so the only
        thing that can differ between these two searches is the stamp — which is
        exactly the signal under test."""
        payloads = [
            {
                "chunk_id": "c1",
                "text": "Value added tax is charged at eighteen percent on taxable supplies.",
                "doc_type": "pdf_chunk",
                "source": "handbook.pdf",
            }
        ]
        on = self._sparse_only_retriever(payloads).search("taxable supplies percent", top_k=1)
        off = self._sparse_only_retriever(payloads).search("capital gains disposal", top_k=1)
        self.assertTrue(on and off, "both queries must reach the fake client")
        self.assertGreater(on[0]["score_lexical"], off[0]["score_lexical"])

    def test_the_stamp_survives_into_the_abstention_decision(self) -> None:
        """End-to-end within the retriever+guard pair: an off-topic question over
        this corpus must come back as abstain, an on-topic one must not."""
        from app.guardrails import OutputGuard

        payloads = [
            {
                "chunk_id": "c1",
                "text": "Value added tax is charged at eighteen percent on taxable supplies.",
                "doc_type": "pdf_chunk",
                "source": "handbook.pdf",
            }
        ]
        on_hits = self._sparse_only_retriever(payloads).search("taxable supplies rate", top_k=1)
        off_hits = self._sparse_only_retriever(payloads).search("write a poem about cats", top_k=1)
        self.assertFalse(OutputGuard.should_abstain(on_hits))
        if off_hits:  # an out-of-vocabulary query may return nothing at all
            self.assertTrue(OutputGuard.should_abstain(off_hits))

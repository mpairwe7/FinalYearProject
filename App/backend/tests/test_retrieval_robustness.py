"""Retrieval robustness: score comparability, dedup, and query caching.

The defect these pin down is scale mixing. A cross-encoder logit runs
roughly -10..10; an RRF score is 1/(60+rank) ≈ 0.016. Code that falls
back from one to the other and then *compares* the results is not
ranking, it is asserting that any reranked hit beats any RRF hit —
including ones the cross-encoder scored as irrelevant.
"""

from __future__ import annotations

import unittest

from app.corrective_rag import _improved, _ranking_key
from app.retriever import _dedupe_candidates, _shingles, hit_relevance


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

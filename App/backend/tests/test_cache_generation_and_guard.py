"""The semantic cache may only serve an answer that is still the right one (G277).

Four ways it served a confident, cited, wrong answer:

* **Version-blind similarity tier.** The exact tier keyed on the index stamp, the
  similarity tier did not: after a reindex the exact key missed, the very same
  question matched itself at cosine 1.0 and the old corpus's answer came back
  for the rest of the hour.
* **Tombstones and fiscal years.** A passage staff withdraw leaves retrieval at
  once, but not the answers cached from it; "the current rate" asked either
  side of 1 July is two different questions.
* **A two-shared-terms guard.** "What is the VAT rate?" took the answer cached
  for "... on exports?", PAYE took VAT's threshold, one fiscal year took
  another's, and a negated question took the affirmative answer.
* **Redis similarity lookups never worked.** The scan matched the exact tier's
  string keys, ``HGETALL`` raised WRONGTYPE on the first one and the handler
  reported a miss.

Plus the outage rule: an answer served from keyword fallback while Qdrant is
configured but unreachable is not stored.
"""

from __future__ import annotations

import fnmatch
import unittest
from typing import Any
from unittest import mock

import numpy as np
from app import cache as cache_mod
from app.cache import (
    RedisSemanticCache,
    SemanticCache,
    _cache_queries_compatible,
    cache_generation,
    reset_index_stamp,
)


class _SameVectorModel:
    """Every text embeds identically, so only the guard and the generation can refuse a hit."""

    def encode(self, text: str, normalize_embeddings: bool = True) -> np.ndarray:
        return np.array([1.0, 0.0], dtype=np.float32)


class _Stamps:
    """Patch the three inputs of :func:`cache_generation` for one test."""

    def __init__(self, test: unittest.TestCase, *, index: str = "idxA", tombstones: str = "", fy: str = "FY2026-27"):
        self.values = {"index": index, "tombstones": tombstones, "fy": fy}
        for name, target in (
            ("index", "app.cache._read_index_stamp"),
            ("tombstones", "app.cache._read_tombstone_stamp"),
            ("fy", "app.cache._read_fiscal_year"),
        ):
            patcher = mock.patch(target, side_effect=lambda name=name: self.values[name])
            patcher.start()
            test.addCleanup(patcher.stop)
        reset_index_stamp()
        test.addCleanup(reset_index_stamp)

    def change(self, **values: str) -> None:
        self.values.update(values)
        reset_index_stamp()


class CompatibilityGuardTests(unittest.TestCase):
    def test_paraphrases_of_one_question_are_compatible(self) -> None:
        for asked, cached in (
            ("How do I register for a TIN?", "What are the steps to register for a TIN in Uganda?"),
            ("VAT rate please", "What is the VAT rate?"),
            ("What is the current VAT rate?", "What is the standard VAT rate?"),
            ("What are the VAT rates?", "what is the vat rate"),
        ):
            with self.subTest(asked=asked):
                self.assertTrue(_cache_queries_compatible(asked, cached))

    def test_a_narrowing_qualifier_is_a_different_question(self) -> None:
        """The exports answer is the zero rate; the plain question wants 18%."""
        self.assertFalse(_cache_queries_compatible("What is the VAT rate?", "What is the VAT rate on exports?"))
        self.assertFalse(_cache_queries_compatible("What is the VAT rate on exports?", "What is the VAT rate?"))

    def test_a_different_tax_head_is_a_different_question(self) -> None:
        self.assertFalse(
            _cache_queries_compatible(
                "What is the PAYE registration threshold?", "What is the VAT registration threshold?"
            )
        )

    def test_a_different_fiscal_year_or_amount_is_a_different_question(self) -> None:
        self.assertFalse(_cache_queries_compatible("VAT rate for FY2023-24", "VAT rate for FY2025-26"))
        self.assertFalse(_cache_queries_compatible("VAT on UGX 1,500,000", "VAT on UGX 2,000,000"))

    def test_negation_is_a_different_question(self) -> None:
        self.assertFalse(_cache_queries_compatible("Is a TIN not required to import?", "Is a TIN required to import?"))
        self.assertFalse(_cache_queries_compatible("Why can't I file my return?", "Why can I file my return?"))
        self.assertTrue(_cache_queries_compatible("Is a TIN not required?", "Is a TIN not required?"))

    def test_nothing_to_compare_is_not_a_match(self) -> None:
        self.assertFalse(_cache_queries_compatible("what is the", "what is the"))
        self.assertFalse(_cache_queries_compatible("", "What is the VAT rate?"))


class MemorySimilarityTierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stamps = _Stamps(self)
        self.cache = SemanticCache(dense_model=_SameVectorModel())
        # Stored under one phrasing, asked under another, so the exact tier
        # misses and every assertion below is about the similarity tier.
        self.cache.put("What is the VAT rate?", {"reply": "18%", "locale": "en"})
        self.asked = "VAT rate please"

    def test_a_paraphrase_hits_in_the_same_generation(self) -> None:
        self.assertEqual(self.cache.get(self.asked, "en"), {"reply": "18%", "locale": "en"})

    def test_a_reindex_retires_similarity_entries_too(self) -> None:
        self.stamps.change(index="idxB")
        self.assertIsNone(self.cache.get(self.asked, "en"))
        self.assertIsNone(self.cache.get("What is the VAT rate?", "en"), "nor the identical question")

    def test_a_new_tombstone_retires_entries(self) -> None:
        self.stamps.change(tombstones="t-7f3a")
        self.assertIsNone(self.cache.get(self.asked, "en"))

    def test_a_new_fiscal_year_retires_entries(self) -> None:
        self.stamps.change(fy="FY2027-28")
        self.assertIsNone(self.cache.get(self.asked, "en"))

    def test_a_guard_refusal_is_a_miss_and_is_counted(self) -> None:
        with mock.patch.object(cache_mod.metrics, "inc") as inc:
            self.assertIsNone(self.cache.get("What is the VAT rate on exports?", "en"))
        inc.assert_called_with(
            "semantic_cache_lookups_total", labels={"backend": "memory", "outcome": "guard_rejected"}
        )

    def test_a_compatible_entry_wins_over_a_closer_refused_one(self) -> None:
        cache = SemanticCache(dense_model=_SameVectorModel())
        cache.put("What is the VAT rate on exports?", {"reply": "0%", "locale": "en"})
        cache.put("What is the VAT rate?", {"reply": "18%", "locale": "en"})
        self.assertEqual(cache.get("VAT rate please", "en")["reply"], "18%")


class GenerationTests(unittest.TestCase):
    def test_generation_names_index_tombstones_and_fiscal_year(self) -> None:
        stamps = _Stamps(self, index="idx1", tombstones="tomb1", fy="FY2026-27")
        self.assertEqual(cache_generation(), "idx1.tomb1.FY2026-27")
        stamps.change(tombstones="")
        self.assertEqual(cache_generation(), "idx1..FY2026-27")

    def test_tombstone_stamp_digests_active_ids_and_ignores_the_flag_off(self) -> None:
        from app import database as db

        rows = [{"id": "tomb_b"}, {"id": "tomb_a"}]
        with mock.patch.object(db, "get_active_corpus_tombstones", return_value=rows), mock.patch(
            "app.flags.flags.is_enabled", return_value=True
        ):
            first = cache_mod._read_tombstone_stamp()
        with mock.patch.object(db, "get_active_corpus_tombstones", return_value=list(reversed(rows))), mock.patch(
            "app.flags.flags.is_enabled", return_value=True
        ):
            self.assertEqual(cache_mod._read_tombstone_stamp(), first, "order-independent")
        self.assertTrue(first)
        with mock.patch.object(db, "get_active_corpus_tombstones", return_value=rows), mock.patch(
            "app.flags.flags.is_enabled", return_value=False
        ):
            self.assertEqual(cache_mod._read_tombstone_stamp(), "", "retrieval applies none")

    def test_exact_tier_keys_on_the_generation(self) -> None:
        stamps = _Stamps(self)
        cache = SemanticCache()  # exact tier only
        cache.put("What is the VAT rate?", {"reply": "18%", "locale": "en"})
        self.assertIsNotNone(cache.get("What is the VAT rate?", "en"))
        stamps.change(tombstones="t-new")
        self.assertIsNone(cache.get("What is the VAT rate?", "en"))


class _FakeRedis:
    """The slice of redis-py the cache uses, with Redis's type rule enforced."""

    class ResponseError(Exception):
        pass

    def __init__(self) -> None:
        self.strings: dict[str, bytes] = {}
        self.hashes: dict[str, dict[bytes, bytes]] = {}
        self.hgetall_calls = 0

    @staticmethod
    def _key(key: Any) -> str:
        return key.decode() if isinstance(key, bytes) else str(key)

    def get(self, key: Any) -> bytes | None:
        return self.strings.get(self._key(key))

    def set(self, key: Any, value: bytes, ex: int | None = None) -> None:
        self.strings[self._key(key)] = value

    def hset(self, key: Any, mapping: dict[str, Any]) -> None:
        self.hashes[self._key(key)] = {
            k.encode(): (v if isinstance(v, bytes) else str(v).encode()) for k, v in mapping.items()
        }

    def expire(self, key: Any, seconds: int) -> None:
        return None

    def hgetall(self, key: Any) -> dict[bytes, bytes]:
        self.hgetall_calls += 1
        name = self._key(key)
        if name in self.strings:
            raise self.ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value")
        return dict(self.hashes.get(name, {}))

    def scan_iter(self, match: str = "*", count: int = 10):
        for name in [*self.strings, *self.hashes]:
            if fnmatch.fnmatchcase(name, match):
                yield name.encode()

    def pipeline(self, transaction: bool = True) -> _FakeRedis._Pipeline:
        return self._Pipeline(self)

    class _Pipeline:
        def __init__(self, client: _FakeRedis) -> None:
            self._client = client
            self._ops: list[tuple[str, tuple, dict]] = []

        def __getattr__(self, name: str):
            def queue(*args: Any, **kwargs: Any) -> _FakeRedis._Pipeline:
                self._ops.append((name, args, kwargs))
                return self

            return queue

        def execute(self, raise_on_error: bool = True) -> list[Any]:
            results: list[Any] = []
            for name, args, kwargs in self._ops:
                try:
                    results.append(getattr(self._client, name)(*args, **kwargs))
                except Exception as exc:
                    if raise_on_error:
                        raise
                    results.append(exc)
            return results


def _redis_cache(client: _FakeRedis) -> RedisSemanticCache:
    cache = RedisSemanticCache.__new__(RedisSemanticCache)
    cache._dense_model = _SameVectorModel()
    cache._lock = mock.MagicMock()
    cache._client = client
    cache._stats = {"hits": 0, "misses": 0, "evictions": 0}
    return cache


class RedisSimilarityTierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stamps = _Stamps(self)
        self.client = _FakeRedis()
        self.cache = _redis_cache(self.client)
        self.cache.put("What is the VAT rate?", {"reply": "18%", "locale": "en"})

    def test_exact_string_keys_no_longer_break_similarity_lookups(self) -> None:
        self.assertTrue(self.client.strings, "put() wrote an exact-tier string key")
        self.assertEqual(self.cache.get("VAT rate please", "en"), {"reply": "18%", "locale": "en"})

    def test_the_scan_reads_only_this_tenant_locale_and_generation(self) -> None:
        self.cache.put("What is the VAT rate?", {"reply": "Omusolo 18%", "locale": "lg"})
        self.cache.put("What is the VAT rate?", {"reply": "18% for B", "locale": "en"}, tenant_id="tenant-b")
        self.client.hgetall_calls = 0
        self.assertEqual(self.cache.get("VAT rate please", "en")["reply"], "18%")
        self.assertEqual(self.client.hgetall_calls, 1)

    def test_a_reindex_retires_redis_similarity_entries(self) -> None:
        self.stamps.change(index="idxB")
        self.assertIsNone(self.cache.get("VAT rate please", "en"))

    def test_the_guard_applies_on_redis_too(self) -> None:
        self.assertIsNone(self.cache.get("What is the VAT rate on exports?", "en"))


class DegradedRetrievalTests(unittest.TestCase):
    def _retriever(self, *, ready: bool, client: object | None = object()):
        from app.retriever import HybridRetriever

        retriever = HybridRetriever()
        retriever._ready = ready
        retriever._client = client
        return retriever

    def test_configured_but_unavailable_qdrant_is_degraded(self) -> None:
        with mock.patch("app.retriever.QDRANT_ENABLED", True):
            self.assertTrue(self._retriever(ready=False, client=None).serving_degraded())

    def test_a_keyword_only_deployment_is_not_degraded(self) -> None:
        with mock.patch("app.retriever.QDRANT_ENABLED", False):
            self.assertFalse(self._retriever(ready=False, client=None).serving_degraded())

    def test_an_open_circuit_is_degraded_and_a_closed_one_is_not(self) -> None:
        retriever = self._retriever(ready=True)
        self.assertFalse(retriever.serving_degraded())
        for _ in range(3):
            retriever._circuit.record_failure()
        self.assertTrue(retriever.serving_degraded())

    def test_the_service_probe_tolerates_doubles(self) -> None:
        from app.service import _retrieval_degraded

        self.assertFalse(_retrieval_degraded(object()))
        self.assertFalse(_retrieval_degraded(mock.Mock(serving_degraded=mock.Mock(side_effect=RuntimeError))))
        self.assertTrue(_retrieval_degraded(mock.Mock(serving_degraded=mock.Mock(return_value=True))))


if __name__ == "__main__":
    unittest.main()

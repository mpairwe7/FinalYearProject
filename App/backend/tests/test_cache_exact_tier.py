"""Tests for the model-free exact tier of the response cache.

Both cache backends used to return ``None`` from ``get`` the moment no dense
model was present, and ``service.py`` only calls ``set_model`` when the retriever
has one. Every deployment shipping without torch — Crane Cloud, the HF Space —
therefore ran with a cache that missed on every single request and never reused
anything. The exact tier is what makes it work there, so these tests all
construct the cache with **no** model.
"""

from __future__ import annotations

import unittest

from unittest import mock

from app.cache import SemanticCache, exact_cache_key, reset_index_stamp


class ExactCacheKeyTests(unittest.TestCase):
    def test_case_punctuation_and_whitespace_collapse(self) -> None:
        canonical = exact_cache_key("What is the VAT rate?")
        for variant in (
            "what is the vat rate",
            "WHAT IS THE VAT RATE?!",
            "  What   is  the VAT   rate ?  ",
        ):
            self.assertEqual(exact_cache_key(variant), canonical, variant)

    def test_locale_is_part_of_the_key(self) -> None:
        self.assertNotEqual(
            exact_cache_key("What is the VAT rate?", "en"),
            exact_cache_key("What is the VAT rate?", "lg"),
        )

    def test_different_questions_do_not_collide(self) -> None:
        self.assertNotEqual(
            exact_cache_key("What is the VAT rate?"),
            exact_cache_key("What is the PAYE rate?"),
        )

    def test_empty_and_none_are_handled(self) -> None:
        # The key gained an index-stamp field in front (see
        # CacheKeyIndexStampTests), so assert the tail rather than the whole
        # string — the stamp depends on which corpus the index was built from.
        self.assertTrue(exact_cache_key("").endswith("en\x1f"))
        self.assertEqual(exact_cache_key(None), exact_cache_key(""))  # type: ignore[arg-type]


class ExactTierWithoutAnEmbedderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cache = SemanticCache()  # no dense model, as in the no-torch image
        self.assertIsNone(self.cache._dense_model)

    def test_a_repeated_question_is_served_from_cache(self) -> None:
        self.assertIsNone(self.cache.get("What is the VAT rate?", "en"))
        self.cache.put("What is the VAT rate?", {"reply": "18 percent", "locale": "en"})
        self.assertEqual(
            self.cache.get("What is the VAT rate?", "en"),
            {"reply": "18 percent", "locale": "en"},
        )

    def test_normalised_variants_hit_the_same_entry(self) -> None:
        self.cache.put("What is the VAT rate?", {"reply": "18 percent", "locale": "en"})
        for variant in ("what is the vat rate", "  What is the VAT rate ?  "):
            self.assertIsNotNone(self.cache.get(variant, "en"), variant)

    def test_a_different_locale_does_not_hit(self) -> None:
        """An English answer must not be served for a Luganda question."""
        self.cache.put("What is the VAT rate?", {"reply": "18 percent", "locale": "en"})
        self.assertIsNone(self.cache.get("What is the VAT rate?", "lg"))

    def test_a_different_question_does_not_hit(self) -> None:
        self.cache.put("What is the VAT rate?", {"reply": "18 percent", "locale": "en"})
        self.assertIsNone(self.cache.get("What is the PAYE rate?", "en"))

    def test_the_entry_locale_comes_from_the_response(self) -> None:
        """put() has no locale argument, so it must key on the response's own
        locale or a Luganda answer would be filed under English."""
        self.cache.put("Omusolo gwa VAT guli gwa?", {"reply": "18%", "locale": "lg"})
        self.assertIsNotNone(self.cache.get("Omusolo gwa VAT guli gwa?", "lg"))
        self.assertIsNone(self.cache.get("Omusolo gwa VAT guli gwa?", "en"))

    def test_hits_and_misses_are_counted(self) -> None:
        self.cache.get("cold", "en")
        self.cache.put("warm", {"reply": "x", "locale": "en"})
        self.cache.get("warm", "en")
        stats = self.cache.stats
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["size"], 1)

    def test_expired_exact_entries_are_evicted(self) -> None:
        import app.cache as cache_module

        original = cache_module.CACHE_TTL_SECONDS
        cache_module.CACHE_TTL_SECONDS = 0
        try:
            self.cache.put("What is the VAT rate?", {"reply": "18 percent", "locale": "en"})
            self.assertIsNone(self.cache.get("What is the VAT rate?", "en"))
        finally:
            cache_module.CACHE_TTL_SECONDS = original

    def test_the_exact_tier_respects_the_size_cap(self) -> None:
        import app.cache as cache_module

        original = cache_module.CACHE_MAX_SIZE
        cache_module.CACHE_MAX_SIZE = 3
        try:
            for i in range(6):
                self.cache.put(f"question number {i}", {"reply": str(i), "locale": "en"})
            self.assertLessEqual(len(self.cache._exact), 3)
            self.assertGreater(self.cache.stats["evictions"], 0)
        finally:
            cache_module.CACHE_MAX_SIZE = original

    def test_tenant_isolation_in_exact_cache(self) -> None:
        self.cache.put("What is the VAT rate?", {"reply": "Tenant A reply", "locale": "en"}, tenant_id="tenant_a")
        self.assertEqual(
            self.cache.get("What is the VAT rate?", "en", tenant_id="tenant_a"),
            {"reply": "Tenant A reply", "locale": "en"},
        )
        self.assertIsNone(self.cache.get("What is the VAT rate?", "en", tenant_id="tenant_b"))
        self.assertIsNone(self.cache.get("What is the VAT rate?", "en"))


if __name__ == "__main__":
    unittest.main()


# Two real index corpus hashes, truncated the way the cache key truncates them.
# They are content digests of a public corpus, not credentials — detect-secrets
# only sees the entropy.
_STAMP_OLD = "317bf6d66b1f"  # pragma: allowlist secret
_STAMP_NEW = "82e84d56eb80"  # pragma: allowlist secret


class CacheKeyIndexStampTests(unittest.TestCase):
    """A cached answer is only valid for the corpus it was retrieved from.

    The key was query + locale, and nothing cleared Redis on a reindex, so every
    entry written before a corpus fix stayed readable for the rest of its hour.
    Ship the fix, rebuild the index, and the assistant keeps serving what the old
    index produced — the corrected passage looks like it never landed.
    """

    def setUp(self) -> None:
        reset_index_stamp()
        self.addCleanup(reset_index_stamp)

    @staticmethod
    def _key_with_stamp(stamp: str, query: str = "What taxes apply to private schools?") -> str:
        reset_index_stamp()
        with mock.patch("app.cache._read_index_stamp", return_value=stamp):
            return exact_cache_key(query)

    def test_a_reindex_makes_the_previous_entries_unreachable(self) -> None:
        before = self._key_with_stamp(_STAMP_OLD)
        after = self._key_with_stamp(_STAMP_NEW)
        self.assertNotEqual(before, after)

    def test_the_same_corpus_still_reuses_one_entry(self) -> None:
        """The stamp must not defeat the reuse the exact tier exists for."""
        reset_index_stamp()
        with mock.patch("app.cache._read_index_stamp", return_value=_STAMP_NEW):
            self.assertEqual(
                exact_cache_key("What taxes apply to private schools?"),
                exact_cache_key("  WHAT taxes   apply to PRIVATE schools?! "),
            )

    def test_locale_still_separates_entries(self) -> None:
        reset_index_stamp()
        with mock.patch("app.cache._read_index_stamp", return_value=_STAMP_NEW):
            self.assertNotEqual(exact_cache_key("What is VAT?", "en"), exact_cache_key("What is VAT?", "lg"))

    def test_an_unreadable_snapshot_does_not_break_lookups(self) -> None:
        """No index stamp is a degraded cache, not a failed request."""
        reset_index_stamp()
        with mock.patch("app.cache._read_index_stamp", return_value=""):
            self.assertTrue(exact_cache_key("What is VAT?").endswith("what is vat"))


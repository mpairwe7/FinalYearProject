"""Model routing: English to Gemini, the Ugandan languages to Sunbird.

Sunbird holds the native TTS voices, the ASR models and the only translation
covering lug/nyn/ach/teo/lgg. Sending those locales to a general model would
answer in a language it approximates rather than speaks, and would bypass the
tier that exists for them — so the guard refuses before any network call rather
than relying on every call site to remember.

English runs gemini-3.7-flash with gemini-3.5-flash-lite behind it. Both were
benchmarked on the retrieval-judge task: every flash variant scored identically,
so the pair is chosen on latency — 3.5-flash-lite measured 1.5s median / 1.7s
max, the fastest of four.

Two fallbacks operate at different layers and are tested separately, because the
first version only had the transport one and a withdrawn model (HTTP 404) sailed
straight past it:

  host  — the AI Gateway lives on Cloudflare, which the HF Space cannot reach at
          all; generativelanguage.googleapis.com is the second route.
  model — a model can be withdrawn, rate-limited or overloaded, which arrives as
          an HTTP status, not a connection error.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers import gateway as gw  # noqa: E402


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "https://example.invalid"))


class TestUgandanLocalesStayOnSunbird(unittest.TestCase):
    def test_the_guard_rejects_every_sunbird_locale(self):
        for locale in ("lg", "nyn", "ach", "teo", "lgg", "sw"):
            with self.subTest(locale=locale):
                self.assertFalse(gw.gemini_allowed_for(locale))

    def test_english_and_an_unset_locale_are_allowed(self):
        for locale in ("en", "en-UG", None, ""):
            with self.subTest(locale=locale):
                self.assertTrue(gw.gemini_allowed_for(locale))

    def test_a_regional_suffix_does_not_smuggle_a_locale_through(self):
        for locale in ("lg-UG", "lg_UG", "LG", " lg "):
            with self.subTest(locale=locale):
                self.assertFalse(gw.gemini_allowed_for(locale))

    def test_generation_is_refused_before_any_network_call(self):
        with patch("app.providers.gateway._post_json") as post:
            with self.assertRaises(RuntimeError):
                gw.gemini_generate("hello", locale="lg")
            post.assert_not_called()


class TestEnglishModelSelection(unittest.TestCase):
    def test_the_primary_is_used_when_no_model_is_named(self):
        seen: list[str] = []

        def capture(url, headers, payload):
            seen.append(url)
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

        with patch("app.providers.gateway._post_json", side_effect=capture):
            gw.gemini_generate("hi", locale="en")
        self.assertIn(gw.GEMINI_PRIMARY_MODEL, seen[0])

    def test_an_explicit_model_wins_over_the_primary(self):
        seen: list[str] = []

        def capture(url, headers, payload):
            seen.append(url)
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

        with patch("app.providers.gateway._post_json", side_effect=capture):
            gw.gemini_generate("hi", locale="en", model="gemini-3.5-flash-lite")
        self.assertIn("gemini-3.5-flash-lite", seen[0])


class TestHostFallback(unittest.TestCase):
    """The gateway is unreachable from the HF Space; Google's own host is not."""

    def test_a_connection_failure_moves_to_the_google_host(self):
        seen: list[str] = []

        def flaky(url, headers, payload):
            seen.append(url)
            if "cloudflare" in url:
                raise httpx.ConnectError("SSL: UNEXPECTED_EOF_WHILE_READING")
            return {"candidates": [{"content": {"parts": [{"text": "recovered"}]}}]}

        with patch("app.providers.gateway._post_json", side_effect=flaky):
            self.assertEqual(gw.gemini_generate("hi", locale="en"), "recovered")
        self.assertIn("cloudflare", seen[0])
        self.assertIn("generativelanguage.googleapis.com", seen[1])


class TestModelFallback(unittest.TestCase):
    """A withdrawn or overloaded model returns an HTTP status, not a connection
    error — which the transport fallback alone never saw."""

    def test_a_withdrawn_model_falls_back_to_the_lite_model(self):
        seen: list[str] = []

        def by_model(url, headers, payload):
            seen.append(url)
            if gw.GEMINI_FALLBACK_MODEL in url:
                return {"candidates": [{"content": {"parts": [{"text": "lite"}]}}]}
            raise httpx.HTTPStatusError("404", request=None, response=_response(404))

        with patch("app.providers.gateway._post_json", side_effect=by_model):
            self.assertEqual(gw.gemini_generate("hi", locale="en"), "lite")
        self.assertTrue(any(gw.GEMINI_FALLBACK_MODEL in u for u in seen))

    def test_rate_limit_and_capacity_also_fall_back(self):
        for status in (429, 503):
            with self.subTest(status=status):

                def by_status(url, headers, payload, _s=status):
                    if gw.GEMINI_FALLBACK_MODEL in url:
                        return {"candidates": [{"content": {"parts": [{"text": "lite"}]}}]}
                    raise httpx.HTTPStatusError("x", request=None, response=_response(_s))

                with patch("app.providers.gateway._post_json", side_effect=by_status):
                    self.assertEqual(gw.gemini_generate("hi", locale="en"), "lite")

    def test_a_bad_request_is_not_retried_on_another_model(self):
        """400/401/403 describe the request; another model repeats them."""
        for status in (400, 401, 403):
            with self.subTest(status=status):
                calls: list[str] = []

                def always(url, headers, payload, _s=status):
                    calls.append(url)
                    raise httpx.HTTPStatusError("x", request=None, response=_response(_s))

                with patch("app.providers.gateway._post_json", side_effect=always):
                    with self.assertRaises(httpx.HTTPStatusError):
                        gw.gemini_generate("hi", locale="en")
                self.assertEqual(len(calls), 1, "a request error must not be retried")

    def test_the_fallback_model_is_not_retried_against_itself(self):
        calls: list[str] = []

        def always_404(url, headers, payload):
            calls.append(url)
            raise httpx.HTTPStatusError("404", request=None, response=_response(404))

        with patch("app.providers.gateway._post_json", side_effect=always_404):
            with self.assertRaises(httpx.HTTPStatusError):
                gw.gemini_generate("hi", locale="en", model=gw.GEMINI_FALLBACK_MODEL)
        # two hosts for the one model, and no recursion beyond that
        self.assertEqual(len(calls), 2)


class TestTheOutputBudgetFitsAThinkingModel(unittest.TestCase):
    """512 tokens looks generous for an FAQ answer and is not.

    gemini-3.x flash are thinking models: reasoning tokens are billed against
    maxOutputTokens BEFORE any answer token is emitted. In production every
    Gemini reply came back truncated ("truncated (41 chars)", "(84 chars)"),
    failed _looks_truncated, and sent the chain through all three Workers AI
    models against a Cloudflare host this deployment cannot reach — so a good
    answer was thrown away at the cost of a ~25s round trip.

    Measured live on the same prompt: 512 -> 337 chars, truncated; 2048 -> 455
    chars, complete.
    """

    def test_the_budget_is_large_enough_for_reasoning_tokens(self):
        from app import service as svc

        self.assertGreaterEqual(
            svc.GEMINI_MAX_OUTPUT_TOKENS,
            1024,
            "reasoning tokens come out of this budget before any answer text",
        )

    def test_the_chat_call_uses_the_budget_rather_than_a_literal(self):
        import os

        from app import service as svc

        with patch.dict(os.environ, {"LLM_FALLBACK_BACKEND": "gemini"}), patch.object(
            svc.flags, "is_enabled", return_value=True
        ), patch("app.providers.config.is_gemini_configured", return_value=True), patch(
            "app.providers.budget.try_consume_gemini_call", return_value=True
        ), patch(
            "app.providers.gateway.gemini_generate", return_value="a complete answer."
        ) as gen:
            svc._llm_cloud_fallback("q", [{"text": "p"}], None, "en")
        self.assertEqual(
            gen.call_args.kwargs.get("max_tokens"), svc.GEMINI_MAX_OUTPUT_TOKENS
        )


class TestTheGuardCoversTheRealChatPath(unittest.TestCase):
    """The guard defaults `locale` to None, so a call site that forgets to pass
    it is silently ALLOWED. `_llm_cloud_fallback` is the path that serves
    production chat, and it forgot — the gateway tests above all passed while
    Luganda still reached Gemini in the one place it mattered.
    """

    def _run(self, locale: str):
        import os

        from app import service as svc

        with patch.dict(os.environ, {"LLM_FALLBACK_BACKEND": "gemini"}), patch.object(
            svc.flags, "is_enabled", return_value=True
        ), patch("app.providers.config.is_gemini_configured", return_value=True), patch(
            "app.providers.budget.try_consume_gemini_call", return_value=True
        ) as budget, patch(
            "app.providers.gateway.gemini_generate", return_value="generated"
        ) as gen:
            svc._llm_cloud_fallback(
                "How do I file a return?", [{"text": "passage"}], None, locale
            )
        return gen, budget

    def test_a_luganda_turn_never_reaches_gemini(self):
        gen, budget = self._run("lg")
        gen.assert_not_called()
        budget.assert_not_called()  # and spends none of the free-tier allowance

    def test_an_english_turn_still_reaches_gemini_with_its_locale(self):
        gen, _ = self._run("en")
        gen.assert_called_once()
        self.assertEqual(gen.call_args.kwargs.get("locale"), "en")


if __name__ == "__main__":
    unittest.main()

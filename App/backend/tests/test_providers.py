"""Unit tests for the cloud-provider fallback layer (no real network or keys).

Verifies: SecretStr masking, configured-gating, the AI Gateway two-credential
header pattern (and that no key lands in a URL query string), Vectorize hit
reshaping, and the free-tier budget guards.
"""

from __future__ import annotations

import os
import unittest
import unittest.mock as mock

from app.providers import budget, config, gateway, relay_client, vectorize

_KEYS = {
    "CLOUDFLARE_ACCOUNT_ID": "acct123",
    "CLOUDFLARE_API_TOKEN": "cf-token-secret",
    "CF_AIG_GATEWAY": "ura-gw",
    "CF_AIG_TOKEN": "aig-token-secret",
    "GEMINI_API_KEY": "AIza-secret",
    "VECTORIZE_INDEX": "ura-kb-bge-m3",
}


def _with_keys() -> None:
    os.environ.update(_KEYS)
    config.get_cloud_settings.cache_clear()


def _clear_keys() -> None:
    for k in _KEYS:
        os.environ.pop(k, None)
    config.get_cloud_settings.cache_clear()


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status = payload, status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload
        self.calls: list[dict] = []

    def post(self, url, headers=None, json=None, content=None, timeout=None):
        self.calls.append(
            {"url": url, "headers": headers or {}, "json": json, "content": content, "timeout": timeout}
        )
        return _Resp(self._payload)


class ConfigTest(unittest.TestCase):
    def tearDown(self):
        _clear_keys()

    def test_unconfigured_by_default(self):
        _clear_keys()
        self.assertFalse(config.is_cloudflare_configured())
        self.assertFalse(config.is_gemini_configured())
        self.assertFalse(config.is_vectorize_configured())

    def test_configured_with_keys(self):
        _with_keys()
        self.assertTrue(config.is_cloudflare_configured())
        self.assertTrue(config.is_gemini_configured())
        self.assertTrue(config.is_vectorize_configured())

    def test_secret_masked_but_readable(self):
        _with_keys()
        s = config.get_cloud_settings()
        self.assertNotIn("cf-token-secret", repr(s))
        self.assertNotIn("cf-token-secret", str(s))
        self.assertEqual(s.cloudflare_api_token.get_secret_value(), "cf-token-secret")


class GatewayTest(unittest.TestCase):
    def setUp(self):
        _with_keys()

    def tearDown(self):
        _clear_keys()
        gateway._client = None

    def test_embed_parses_and_two_credential_headers(self):
        fake = _FakeClient({"result": {"data": [[0.1] * 1024]}, "success": True})
        with mock.patch.object(gateway, "_get_client", return_value=fake):
            vecs = gateway.workers_ai_embed(["hello"])
        self.assertEqual(len(vecs), 1)
        self.assertEqual(len(vecs[0]), 1024)
        hdr = fake.calls[0]["headers"]
        self.assertEqual(hdr["Authorization"], "Bearer cf-token-secret")
        self.assertEqual(hdr["cf-aig-authorization"], "Bearer aig-token-secret")
        self.assertNotIn("cf-token-secret", fake.calls[0]["url"])  # never in query string
        self.assertIn("workers-ai/@cf/baai/bge-m3", fake.calls[0]["url"])

    def test_gemini_uses_goog_header_not_query(self):
        fake = _FakeClient({"candidates": [{"content": {"parts": [{"text": "Olutalo"}]}}]})
        with mock.patch.object(gateway, "_get_client", return_value=fake):
            out = gateway.gemini_generate("Translate hi", system="to Luganda")
        self.assertEqual(out, "Olutalo")
        hdr = fake.calls[0]["headers"]
        self.assertEqual(hdr["x-goog-api-key"], "AIza-secret")
        self.assertEqual(hdr["cf-aig-authorization"], "Bearer aig-token-secret")
        self.assertNotIn("AIza-secret", fake.calls[0]["url"])
        self.assertIn("google-ai-studio", fake.calls[0]["url"])

    def test_chat_parses_response(self):
        fake = _FakeClient({"result": {"response": " 18 percent "}})
        with mock.patch.object(gateway, "_get_client", return_value=fake):
            out = gateway.workers_ai_chat([{"role": "user", "content": "vat?"}])
        self.assertEqual(out, "18 percent")


class VectorizeTest(unittest.TestCase):
    def setUp(self):
        _with_keys()

    def tearDown(self):
        _clear_keys()
        gateway._client = None

    def test_reshapes_matches_to_hit_dict(self):
        payload = {
            "result": {
                "matches": [
                    {
                        "id": "c1",
                        "score": 0.91,
                        "metadata": {
                            "text": "VAT is 18%",
                            "source": "vat.pdf",
                            "page": 2,
                            "section": "S1",
                            "tag": "vat",
                        },
                    }
                ]
            }
        }
        fake = _FakeClient(payload)
        with mock.patch.object(gateway, "_get_client", return_value=fake):
            hits = vectorize.vectorize_query([0.1] * 1024, top_k=4)
        self.assertEqual(len(hits), 1)
        h = hits[0]
        self.assertEqual(h["id"], "c1")
        self.assertEqual(h["text"], "VAT is 18%")
        self.assertEqual(h["source"], "vat.pdf")
        self.assertAlmostEqual(h["score"], 0.91)
        # direct Vectorize call uses the CF token only (no gateway header needed)
        self.assertIn("vectorize/v2/indexes/ura-kb-bge-m3/query", fake.calls[0]["url"])


class RelayClientTest(unittest.TestCase):
    """Client side of the Cloudflare relay: builds requests against
    ``cf_relay_base_url`` using ``cf_relay_secret``, distinct from the real
    Cloudflare credentials."""

    def setUp(self):
        os.environ["CF_RELAY_BASE_URL"] = "https://relay.example.internal"
        os.environ["CF_RELAY_SECRET"] = "relay-secret-xyz"
        config.get_cloud_settings.cache_clear()

    def tearDown(self):
        os.environ.pop("CF_RELAY_BASE_URL", None)
        os.environ.pop("CF_RELAY_SECRET", None)
        config.get_cloud_settings.cache_clear()
        relay_client._client = None

    def test_relay_workers_ai_embed_request_shape(self):
        fake = _FakeClient({"vectors": [[0.1] * 1024]})
        with mock.patch.object(relay_client, "_get_client", return_value=fake):
            vecs = relay_client.relay_workers_ai_embed(["hello"])
        self.assertEqual(len(vecs), 1)
        call = fake.calls[0]
        self.assertEqual(call["url"], "https://relay.example.internal/internal/cf-relay/workers-ai-embed")
        self.assertEqual(call["headers"]["Authorization"], "Bearer relay-secret-xyz")
        self.assertEqual(call["json"], {"texts": ["hello"]})  # no model — see relay_client docstring

    def test_relay_vectorize_query_request_shape(self):
        fake = _FakeClient({"hits": [{"id": "c1"}]})
        with mock.patch.object(relay_client, "_get_client", return_value=fake):
            hits = relay_client.relay_vectorize_query([0.1, 0.2], 5, {"tag": {"$eq": "vat"}})
        self.assertEqual(hits, [{"id": "c1"}])
        call = fake.calls[0]
        self.assertEqual(call["url"], "https://relay.example.internal/internal/cf-relay/vectorize-query")
        self.assertEqual(call["headers"]["Authorization"], "Bearer relay-secret-xyz")
        self.assertEqual(
            call["json"], {"vector": [0.1, 0.2], "top_k": 5, "vector_filter": {"tag": {"$eq": "vat"}}}
        )

    def test_relay_workers_ai_chat_request_shape(self):
        from app.providers import routing

        fake = _FakeClient({"text": "Double the VAT due."})
        with mock.patch.object(relay_client, "_get_client", return_value=fake):
            text = relay_client.relay_workers_ai_chat(
                [{"role": "user", "content": "hi"}],
                routing.CF_LLM_MODEL,
                max_tokens=512,
                temperature=0.2,
            )
        self.assertEqual(text, "Double the VAT due.")
        call = fake.calls[0]
        self.assertEqual(call["url"], "https://relay.example.internal/internal/cf-relay/workers-ai-chat")
        self.assertEqual(call["headers"]["Authorization"], "Bearer relay-secret-xyz")
        self.assertEqual(
            call["json"],
            {
                "messages": [{"role": "user", "content": "hi"}],
                "model_slot": "primary",  # never the raw model id — see CFRelayChatRequest
                "max_tokens": 512,
                "temperature": 0.2,
            },
        )
        # Chat gets its own per-call timeout override, not the client's default.
        self.assertEqual(call["timeout"], relay_client._CHAT_HTTP_TIMEOUT)

    def test_relay_workers_ai_chat_rejects_unknown_model(self):
        with self.assertRaises(ValueError):
            relay_client.relay_workers_ai_chat(
                [{"role": "user", "content": "hi"}],
                "@cf/not-a-configured-model",
                max_tokens=512,
                temperature=0.2,
            )


class RelayRoutingTest(unittest.TestCase):
    """When ``cf_relay_base_url`` is configured, the provider functions
    delegate to the relay client instead of calling Cloudflare directly."""

    def setUp(self):
        _with_keys()
        os.environ["CF_RELAY_BASE_URL"] = "https://relay.example.internal"
        os.environ["CF_RELAY_SECRET"] = "relay-secret-xyz"
        config.get_cloud_settings.cache_clear()

    def tearDown(self):
        os.environ.pop("CF_RELAY_BASE_URL", None)
        os.environ.pop("CF_RELAY_SECRET", None)
        _clear_keys()

    def test_vectorize_query_uses_relay_when_configured(self):
        with mock.patch.object(
            relay_client, "relay_vectorize_query", return_value=[{"id": "c1"}]
        ) as mocked, mock.patch.object(gateway, "_get_client") as direct_client:
            hits = vectorize.vectorize_query([0.1] * 1024, top_k=4)
        self.assertEqual(hits, [{"id": "c1"}])
        mocked.assert_called_once_with([0.1] * 1024, 4, None)
        direct_client.assert_not_called()  # never touches Cloudflare directly

    def test_workers_ai_embed_uses_relay_when_configured(self):
        with mock.patch.object(
            relay_client, "relay_workers_ai_embed", return_value=[[0.1] * 1024]
        ) as mocked, mock.patch.object(gateway, "_get_client") as direct_client:
            vecs = gateway.workers_ai_embed(["hello"])
        self.assertEqual(vecs, [[0.1] * 1024])
        mocked.assert_called_once_with(["hello"])
        direct_client.assert_not_called()

    def test_workers_ai_chat_uses_relay_when_configured(self):
        with mock.patch.object(
            relay_client, "relay_workers_ai_chat", return_value="Double the VAT due."
        ) as mocked, mock.patch.object(gateway, "_get_client") as direct_client:
            text = gateway.workers_ai_chat(
                [{"role": "user", "content": "hi"}],
                "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                max_tokens=512,
                temperature=0.2,
            )
        self.assertEqual(text, "Double the VAT due.")
        mocked.assert_called_once_with(
            [{"role": "user", "content": "hi"}],
            "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
            max_tokens=512,
            temperature=0.2,
        )
        direct_client.assert_not_called()


class BudgetTest(unittest.TestCase):
    def setUp(self):
        os.environ["CF_NEURON_DAILY_BUDGET"] = "3"
        os.environ["GEMINI_RPM"] = "2"
        config.get_cloud_settings.cache_clear()
        budget._redis = None
        budget._redis_tried = True  # force in-process counters
        budget._local_neurons.clear()
        budget._local_gemini.clear()

    def tearDown(self):
        os.environ.pop("CF_NEURON_DAILY_BUDGET", None)
        os.environ.pop("GEMINI_RPM", None)
        config.get_cloud_settings.cache_clear()
        budget._redis_tried = False

    def test_neuron_budget_caps(self):
        self.assertTrue(budget.try_consume_neurons(2))
        self.assertTrue(budget.try_consume_neurons(1))
        self.assertFalse(budget.try_consume_neurons(1))  # 4 > 3

    def test_gemini_rpm_caps_and_rolls_over(self):
        t = 1000.0
        self.assertTrue(budget.try_consume_gemini_call(now=t))
        self.assertTrue(budget.try_consume_gemini_call(now=t))
        self.assertFalse(budget.try_consume_gemini_call(now=t))  # 3rd in the minute
        self.assertTrue(budget.try_consume_gemini_call(now=t + 61))  # next minute

    def test_neuron_budget_resets_at_utc_midnight(self):
        import calendar

        before = calendar.timegm((2026, 6, 10, 23, 59, 59, 0, 0, 0))
        self.assertTrue(budget.try_consume_neurons(3, now=before))
        self.assertFalse(budget.try_consume_neurons(1, now=before))  # exhausted
        after = before + 2  # 2026-06-11 00:00:01 UTC — fresh daily window
        self.assertTrue(budget.try_consume_neurons(3, now=after))

    def test_redis_error_falls_back_to_local_counters(self):
        class _BrokenRedis:
            def incrby(self, *a, **k):
                raise ConnectionError("redis gone")

            def incr(self, *a, **k):
                raise ConnectionError("redis gone")

        budget._redis = _BrokenRedis()
        try:
            # Both guards must degrade to the in-process counters, not raise.
            self.assertTrue(budget.try_consume_neurons(2))
            self.assertFalse(budget.try_consume_neurons(2))  # 4 > 3 locally
            self.assertTrue(budget.try_consume_gemini_call(now=1000.0))
        finally:
            budget._redis = None

    def test_neuron_budget_is_thread_safe(self):
        import threading

        os.environ["CF_NEURON_DAILY_BUDGET"] = "16"
        config.get_cloud_settings.cache_clear()
        results: list[bool] = []
        lock = threading.Lock()

        def worker():
            ok = budget.try_consume_neurons(1, now=2000.0)
            with lock:
                results.append(ok)

        threads = [threading.Thread(target=worker) for _ in range(32)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(results), 16)  # exactly the budget, no over-grant


class R2Test(unittest.TestCase):
    """providers.r2 — S3-compatible object storage used for bm25_state.json
    durability / offline bundles / TTS cache. Must be a safe no-op when
    unconfigured and swallow client errors into None/False."""

    _R2_KEYS = {
        "R2_ACCOUNT_ID": "r2acct",
        "R2_ACCESS_KEY_ID": "r2-access-key",
        "R2_SECRET_ACCESS_KEY": "r2-secret-key",
        "R2_BUCKET": "ura-chatbot-test",
    }

    def setUp(self):
        from app.providers import r2

        self.r2 = r2

    def tearDown(self):
        for k in self._R2_KEYS:
            os.environ.pop(k, None)
        config.get_cloud_settings.cache_clear()
        self.r2._client.cache_clear()

    def _configure(self):
        os.environ.update(self._R2_KEYS)
        config.get_cloud_settings.cache_clear()

    def test_unconfigured_is_a_safe_noop(self):
        config.get_cloud_settings.cache_clear()
        with mock.patch.object(self.r2, "_client") as client:
            self.assertIsNone(self.r2.get_object("bm25_state.json"))
            self.assertFalse(self.r2.put_object("bm25_state.json", b"x"))
            self.assertFalse(self.r2.object_exists("bm25_state.json"))
        client.assert_not_called()

    def test_get_object_reads_body(self):
        self._configure()
        body = mock.Mock()
        body.read.return_value = b"bm25-bytes"
        fake = mock.Mock()
        fake.get_object.return_value = {"Body": body}
        with mock.patch.object(self.r2, "_client", return_value=fake):
            self.assertEqual(self.r2.get_object("bm25_state.json"), b"bm25-bytes")
        fake.get_object.assert_called_once_with(Bucket="ura-chatbot-test", Key="bm25_state.json")

    def test_get_object_error_returns_none(self):
        self._configure()
        fake = mock.Mock()
        fake.get_object.side_effect = RuntimeError("NoSuchKey")
        with mock.patch.object(self.r2, "_client", return_value=fake):
            self.assertIsNone(self.r2.get_object("missing.json"))

    def test_put_object_uploads_with_content_type(self):
        self._configure()
        fake = mock.Mock()
        with mock.patch.object(self.r2, "_client", return_value=fake):
            self.assertTrue(self.r2.put_object("tts/abc.mp3", b"audio", content_type="audio/mpeg"))
        fake.put_object.assert_called_once_with(
            Bucket="ura-chatbot-test", Key="tts/abc.mp3", Body=b"audio", ContentType="audio/mpeg"
        )

    def test_put_object_error_returns_false(self):
        self._configure()
        fake = mock.Mock()
        fake.put_object.side_effect = RuntimeError("AccessDenied")
        with mock.patch.object(self.r2, "_client", return_value=fake):
            self.assertFalse(self.r2.put_object("k", b"x"))

    def test_object_exists_via_head(self):
        self._configure()
        fake = mock.Mock()
        with mock.patch.object(self.r2, "_client", return_value=fake):
            self.assertTrue(self.r2.object_exists("bundle.zip"))
        fake.head_object.assert_called_once_with(Bucket="ura-chatbot-test", Key="bundle.zip")

    def test_object_exists_false_on_404(self):
        self._configure()
        fake = mock.Mock()
        fake.head_object.side_effect = RuntimeError("404")
        with mock.patch.object(self.r2, "_client", return_value=fake):
            self.assertFalse(self.r2.object_exists("nope.zip"))


class RetrieverVectorizeModeTest(unittest.TestCase):
    """The retriever restores dense search via Workers AI + Vectorize when
    Qdrant is off (the Crane Cloud degraded state)."""

    def setUp(self):
        _with_keys()

    def tearDown(self):
        _clear_keys()
        gateway._client = None

    def test_vectorize_mode_initialises_and_searches(self):
        from app import retriever as R

        r = R.HybridRetriever()
        with mock.patch.object(R, "QDRANT_ENABLED", False), mock.patch.object(
            R, "DENSE_FALLBACK_BACKEND", "workers_ai"
        ):
            ready = r.initialize()
            self.assertTrue(ready)
            self.assertTrue(r._vectorize_mode)
            self.assertTrue(r.is_ready)

            vhits = [
                {"id": "c1", "text": "VAT rate is 18 percent", "source": "vat.pdf",
                 "page": 1, "section": "S", "tag": "vat", "score": 0.9},
                {"id": "c2", "text": "TIN is a taxpayer id number", "source": "tin.pdf",
                 "page": 2, "section": "S", "tag": "tin", "score": 0.8},
            ]
            with mock.patch.object(gateway, "workers_ai_embed", return_value=[[0.1] * 1024]), \
                 mock.patch("app.providers.vectorize.vectorize_query", return_value=vhits):
                hits = r.search("what is the vat rate", top_k=2)

        self.assertEqual(len(hits), 2)
        self.assertIn("score_rrf", hits[0])
        # VAT chunk wins on dense rank 0 + lexical overlap (vat, rate)
        self.assertEqual(hits[0]["id"], "c1")
        self.assertEqual(hits[0]["text"], "VAT rate is 18 percent")
        self.assertEqual(hits[0]["chunk_id"], "c1")

    def test_unset_flag_auto_enables_the_fallback_when_cloudflare_is_configured(self):
        """Unset means AUTO. It used to mean OFF, which left a deployment holding
        valid Vectorize credentials on keyword-only search whenever Qdrant was
        unavailable — the opposite of the intent."""
        from app import retriever as R

        r = R.HybridRetriever()
        with mock.patch.object(R, "QDRANT_ENABLED", False), mock.patch.object(
            R, "DENSE_FALLBACK_BACKEND", ""
        ):
            self.assertTrue(r.initialize())
            self.assertTrue(r._vectorize_mode)
            self.assertEqual(r.backend, "vectorize")

    def test_fallback_can_be_explicitly_disabled(self):
        from app import retriever as R

        for value in ("none", "off", "disabled"):
            r = R.HybridRetriever()
            with mock.patch.object(R, "QDRANT_ENABLED", False), mock.patch.object(
                R, "DENSE_FALLBACK_BACKEND", value
            ):
                self.assertFalse(r.initialize(), value)
                self.assertFalse(r._vectorize_mode, value)
                self.assertEqual(r.backend, "keyword")

    def test_fallback_stays_off_when_cloudflare_is_not_configured(self):
        from app import retriever as R

        _clear_keys()
        r = R.HybridRetriever()
        with mock.patch.object(R, "QDRANT_ENABLED", False), mock.patch.object(
            R, "DENSE_FALLBACK_BACKEND", ""
        ):
            self.assertFalse(r.initialize())
            self.assertFalse(r._vectorize_mode)

    def _vectorize_retriever(self):
        from app import retriever as R

        r = R.HybridRetriever()
        with mock.patch.object(R, "QDRANT_ENABLED", False), mock.patch.object(
            R, "DENSE_FALLBACK_BACKEND", "workers_ai"
        ):
            r.initialize()
        return r

    def test_vectorize_breaker_open_skips_dense_fallback(self):
        from app.providers import breakers

        r = self._vectorize_retriever()
        with mock.patch.object(breakers.VECTORIZE_BREAKER, "allow_request", return_value=False), \
             mock.patch.object(gateway, "workers_ai_embed") as emb:
            hits = r._search_vectorize("vat rate", 4, 20, None)
        self.assertEqual(hits, [])
        emb.assert_not_called()  # never spend a network call against an open circuit

    def test_vectorize_budget_exhausted_skips_dense_fallback(self):
        from app.providers import breakers

        r = self._vectorize_retriever()
        with mock.patch.object(breakers.VECTORIZE_BREAKER, "allow_request", return_value=True), \
             mock.patch("app.providers.budget.try_consume_neurons", return_value=False), \
             mock.patch.object(gateway, "workers_ai_embed") as emb:
            hits = r._search_vectorize("vat rate", 4, 20, None)
        self.assertEqual(hits, [])
        emb.assert_not_called()

    def test_vectorize_gateway_error_records_breaker_failure(self):
        from app.providers import breakers

        r = self._vectorize_retriever()
        with mock.patch.object(breakers.VECTORIZE_BREAKER, "allow_request", return_value=True), \
             mock.patch.object(breakers.VECTORIZE_BREAKER, "record_failure") as rf, \
             mock.patch("app.providers.budget.try_consume_neurons", return_value=True), \
             mock.patch.object(gateway, "workers_ai_embed", side_effect=RuntimeError("HTTP 500")):
            hits = r._search_vectorize("vat rate", 4, 20, None)
        self.assertEqual(hits, [])
        rf.assert_called_once()


class LLMFallbackTest(unittest.TestCase):
    """service._llm_cloud_fallback fires only when configured + flag on."""

    def setUp(self):
        _with_keys()
        budget._redis = None
        budget._redis_tried = True  # force in-process budget
        budget._local_gemini.clear()
        budget._local_neurons.clear()
        # Some tests below let a "truncated" Gemini reply fall through to a
        # real (unmocked) Workers AI call, matching production's chain
        # behaviour; force both breakers healthy first so an unrelated
        # earlier test's failures can't leave them OPEN here (see
        # LLMRoutingOrderTest, which does the same).
        from app.providers import breakers

        breakers.GEMINI_BREAKER.record_success()
        breakers.CF_LLM_BREAKER.record_success()

    def tearDown(self):
        _clear_keys()
        budget._redis_tried = False
        gateway._client = None

    def test_fallback_uses_gemini_when_configured(self):
        from app import service

        with mock.patch.object(service.flags, "is_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"LLM_FALLBACK_BACKEND": "gemini"}), \
             mock.patch.object(gateway, "gemini_generate", return_value="VAT is 18%. [1]") as gg:
            out = service._llm_cloud_fallback("vat rate?", [{"text": "VAT is 18%"}], None, "en")
        self.assertEqual(out, "VAT is 18%. [1]")
        gg.assert_called_once()

    def test_fallback_disabled_when_flag_off(self):
        from app import service

        with mock.patch.object(service.flags, "is_enabled", return_value=False):
            self.assertEqual(
                service._llm_cloud_fallback("vat?", [{"text": "x"}], None, "en"), ""
            )

    def test_empty_primary_reply_triggers_cloud_fallback(self):
        """A swallowed-error empty reply from the primary backend must route to
        the cloud fallback, not return "" (regression: vLLM HTTP failure →
        _vllm_generate returns "" → _call_llm_with_deadline recorded success)."""
        from app import service

        with mock.patch.object(service._LLM_CIRCUIT, "allow_request", return_value=True), \
             mock.patch.object(service.llm_module, "generate", return_value=""), \
             mock.patch.object(service, "_llm_cloud_fallback", return_value="CLOUD ANSWER") as cf:
            out = service._call_llm_with_deadline("vat?", [{"text": "x"}], None, "en")
        self.assertEqual(out, "CLOUD ANSWER")
        cf.assert_called_once()

    def test_nonempty_primary_reply_skips_fallback(self):
        from app import service

        with mock.patch.object(service._LLM_CIRCUIT, "allow_request", return_value=True), \
             mock.patch.object(service.llm_module, "generate", return_value="real answer [1]"), \
             mock.patch.object(service, "_llm_cloud_fallback", return_value="CLOUD") as cf:
            out = service._call_llm_with_deadline("vat?", [{"text": "x"}], None, "en")
        self.assertEqual(out, "real answer [1]")
        cf.assert_not_called()

    def test_fallback_disabled_without_backend_env(self):
        from app import service

        os.environ.pop("LLM_FALLBACK_BACKEND", None)
        with mock.patch.object(service.flags, "is_enabled", return_value=True):
            self.assertEqual(
                service._llm_cloud_fallback("vat?", [{"text": "x"}], None, "en"), ""
            )

    def test_stream_fallback_yields_chunks(self):
        from app import service

        with mock.patch.object(service.flags, "is_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"LLM_FALLBACK_BACKEND": "gemini"}), \
             mock.patch.object(gateway, "gemini_generate", return_value="VAT is eighteen percent."):
            chunks = list(service._stream_cloud_fallback("vat?", [{"text": "VAT"}], None, "en"))
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks).strip(), "VAT is eighteen percent.")

    def test_gemini_truncated_reply_falls_through_to_workers_ai(self):
        """A short, punctuation-less Gemini reply (early-stop artifact) must
        not win outright — the chain should still try Workers AI for a
        complete answer (regression test for the Crane Cloud truncation
        bug: '...The Electronic' with nothing after it)."""
        from app import service

        with mock.patch.object(service.flags, "is_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"LLM_FALLBACK_BACKEND": "gemini"}), \
             mock.patch.object(gateway, "gemini_generate", return_value="The Electronic") as gg, \
             mock.patch.object(
                 gateway,
                 "workers_ai_chat",
                 return_value="EFRIS is mandatory for VAT-registered taxpayers. [1]",
             ) as wac:
            out = service._llm_cloud_fallback(
                "what is efris?", [{"text": "EFRIS info"}], None, "en"
            )
        self.assertEqual(out, "EFRIS is mandatory for VAT-registered taxpayers. [1]")
        gg.assert_called_once()
        wac.assert_called_once()

    def test_workers_ai_chain_tries_next_model_on_truncated_reply(self):
        from app import service
        from app.providers import routing

        with mock.patch.object(service.flags, "is_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"LLM_FALLBACK_BACKEND": "workers_ai"}), \
             mock.patch.object(
                 gateway,
                 "workers_ai_chat",
                 side_effect=["Short", "The full VAT rate is eighteen percent. [1]"],
             ) as wac:
            out = service._llm_cloud_fallback(
                "vat rate?", [{"text": "VAT is 18%"}], None, "en"
            )
        self.assertEqual(out, "The full VAT rate is eighteen percent. [1]")
        self.assertEqual(wac.call_count, 2)
        self.assertEqual(wac.call_args_list[0].kwargs["model"], routing.CF_LLM_MODEL)
        self.assertEqual(wac.call_args_list[1].kwargs["model"], routing.CF_LLM_FALLBACK_MODEL)

    def test_workers_ai_chain_returns_best_effort_when_all_truncated(self):
        """Every backend in the chain coming back short is still strictly
        better than propagating "" (which drops the caller to the raw
        extractive answer) — the first non-empty candidate is kept as a
        floor, so this path is never worse than before the truncation
        check existed."""
        from app import service

        with mock.patch.object(service.flags, "is_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"LLM_FALLBACK_BACKEND": "workers_ai"}), \
             mock.patch.object(gateway, "workers_ai_chat", return_value="Short"):
            out = service._llm_cloud_fallback(
                "vat rate?", [{"text": "VAT is 18%"}], None, "en"
            )
        self.assertEqual(out, "Short")

    def test_local_truncated_reply_upgrades_via_cloud_fallback(self):
        from app import service

        with mock.patch.object(service._LLM_CIRCUIT, "allow_request", return_value=True), \
             mock.patch.object(service.llm_module, "generate", return_value="The Electronic"), \
             mock.patch.object(
                 service,
                 "_llm_cloud_fallback",
                 return_value="The Electronic Fiscal Receipting and Invoicing System "
                 "(EFRIS) is mandatory for VAT-registered taxpayers. [1]",
             ) as cf:
            out = service._call_llm_with_deadline("what is efris?", [{"text": "x"}], None, "en")
        self.assertTrue(out.startswith("The Electronic Fiscal"))
        cf.assert_called_once()

    def test_local_truncated_reply_keeps_local_when_cloud_also_short(self):
        """If the cloud chain can't do better (disabled/empty), keep the
        original truncated-but-present local reply rather than discarding a
        usable answer — same floor as before this check existed."""
        from app import service

        with mock.patch.object(service._LLM_CIRCUIT, "allow_request", return_value=True), \
             mock.patch.object(service.llm_module, "generate", return_value="The Electronic"), \
             mock.patch.object(service, "_llm_cloud_fallback", return_value="") as cf:
            out = service._call_llm_with_deadline("what is efris?", [{"text": "x"}], None, "en")
        self.assertEqual(out, "The Electronic")
        cf.assert_called_once()

    def test_local_complete_short_reply_skips_fallback(self):
        """A short but properly terminated reply is a legitimate terse
        answer, not a truncation — must not trigger the cloud round-trip."""
        from app import service

        with mock.patch.object(service._LLM_CIRCUIT, "allow_request", return_value=True), \
             mock.patch.object(service.llm_module, "generate", return_value="VAT is 18%."), \
             mock.patch.object(service, "_llm_cloud_fallback", return_value="CLOUD") as cf:
            out = service._call_llm_with_deadline("vat rate?", [{"text": "x"}], None, "en")
        self.assertEqual(out, "VAT is 18%.")
        cf.assert_not_called()

    def test_deadline_already_past_skips_entire_chain(self):
        """A request that enters the cloud chain with no budget left must not
        attempt Gemini or any Workers AI model — regression test for Crane
        Cloud's /v1/chat hanging to a uniform ~51s then 504ing under load:
        a request with no time left should fail fast, not still try every
        hop's own ~30s timeout on top of an already-exhausted budget."""
        from app import service

        with mock.patch.object(service.flags, "is_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"LLM_FALLBACK_BACKEND": "gemini"}), \
             mock.patch.object(gateway, "gemini_generate") as gg, \
             mock.patch.object(gateway, "workers_ai_chat") as wac:
            out = service._llm_cloud_fallback(
                "vat rate?",
                [{"text": "VAT is 18%"}],
                None,
                "en",
                deadline=service.time.monotonic() - 1,
            )
        self.assertEqual(out, "")
        gg.assert_not_called()
        wac.assert_not_called()

    def test_deadline_exhausted_between_gemini_and_workers_ai_stops_chain(self):
        """Budget is re-checked before each hop, not just once at entry — a
        Gemini attempt that itself eats the remaining budget must stop the
        chain from also trying every Workers AI model afterward."""
        from app import service

        with mock.patch.object(service.flags, "is_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"LLM_FALLBACK_BACKEND": "gemini"}), \
             mock.patch.object(gateway, "gemini_generate", return_value="Short") as gg, \
             mock.patch.object(gateway, "workers_ai_chat") as wac, \
             mock.patch.object(service.time, "monotonic", side_effect=[100.0, 200.0]):
            out = service._llm_cloud_fallback(
                "vat rate?", [{"text": "VAT is 18%"}], None, "en", deadline=150.0
            )
        self.assertEqual(out, "Short")  # best-effort floor from the Gemini attempt
        gg.assert_called_once()
        wac.assert_not_called()

    def test_deadline_not_exhausted_behaves_as_before(self):
        """A generous deadline must not change existing behaviour — same
        assertion as test_fallback_uses_gemini_when_configured, plus an
        explicit far-future deadline."""
        from app import service

        with mock.patch.object(service.flags, "is_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"LLM_FALLBACK_BACKEND": "gemini"}), \
             mock.patch.object(gateway, "gemini_generate", return_value="VAT is 18%. [1]") as gg:
            out = service._llm_cloud_fallback(
                "vat rate?",
                [{"text": "VAT is 18%"}],
                None,
                "en",
                deadline=service.time.monotonic() + 100,
            )
        self.assertEqual(out, "VAT is 18%. [1]")
        gg.assert_called_once()

    def test_local_then_cloud_shares_one_budget_across_local_and_cloud(self):
        """_local_llm_then_cloud must compute the chain-wide deadline once,
        before the local attempt, and pass it through to the cloud fallback
        — not give the cloud chain a fresh full budget on top of whatever
        the local attempt already spent."""
        from app import service

        with mock.patch.object(service._LLM_CIRCUIT, "allow_request", return_value=True), \
             mock.patch.object(service.llm_module, "generate", return_value=""), \
             mock.patch.object(service, "_llm_cloud_fallback", return_value="CLOUD") as cf:
            before = service.time.monotonic()
            service._call_llm_with_deadline("vat?", [{"text": "x"}], None, "en")
            after = service.time.monotonic()
        cf.assert_called_once()
        deadline = cf.call_args.kwargs["deadline"]
        self.assertIsNotNone(deadline)
        # Must land within [now, now + LLM_TOTAL_BUDGET_SECONDS], not None
        # (unbounded) and not some unrelated/huge value.
        self.assertGreaterEqual(deadline, before)
        self.assertLessEqual(deadline, after + service.LLM_TOTAL_BUDGET_SECONDS)


class LooksTruncatedHeuristicTest(unittest.TestCase):
    def test_short_without_terminal_punctuation_is_truncated(self):
        from app import service

        self.assertTrue(service._looks_truncated("The Electronic"))

    def test_short_with_terminal_punctuation_is_not_truncated(self):
        from app import service

        self.assertFalse(service._looks_truncated("VAT is 18%."))

    def test_short_ending_in_citation_bracket_is_not_truncated(self):
        from app import service

        self.assertFalse(service._looks_truncated("See section 5 [1]"))

    def test_empty_or_blank_text_is_not_truncated(self):
        from app import service

        self.assertFalse(service._looks_truncated(""))
        self.assertFalse(service._looks_truncated("   "))

    def test_long_text_without_terminal_punctuation_is_still_flagged(self):
        """Regression: an empathy preamble sentence (the model's own, per
        tone_hint) can push a genuinely truncated reply past any reasonable
        length exemption — e.g. observed live: "I understand you're under
        time pressure! ... the fastest path to your answer:\\n\\nFor" (85
        chars, cut off mid-word). Length alone must not exempt a reply from
        the truncation check."""
        from app import service

        self.assertTrue(service._looks_truncated("x" * 80))
        self.assertTrue(
            service._looks_truncated(
                "I understand you're under time pressure! Here's the fastest "
                "path to your answer:\n\nFor"
            )
        )
        self.assertTrue(
            service._looks_truncated(
                "Please don't worry, we're here to help you understand.\n\nA customs"
            )
        )

    def test_long_complete_text_is_not_flagged(self):
        from app import service

        self.assertFalse(
            service._looks_truncated(
                "VAT is charged at 18% on most goods and services supplied in "
                "Uganda, and registration is mandatory once annual taxable "
                "turnover exceeds UGX 150 million. [1]"
            )
        )


class StreamFallbackTest(unittest.TestCase):
    """Every primary-failure shape of ``stream_llm_tokens`` must reroute to
    ``_stream_cloud_fallback`` (PR #98 parity for the SSE/WS path): empty
    stream, raised stream, breaker OPEN, and LLM-unavailable."""

    def test_empty_stream_triggers_cloud_fallback(self):
        from app import service

        with mock.patch.object(service.llm_module, "is_available", return_value=True), \
             mock.patch.object(service._LLM_CIRCUIT, "allow_request", return_value=True), \
             mock.patch.object(service._LLM_CIRCUIT, "record_failure") as rf, \
             mock.patch.object(service.llm_module, "generate_stream", return_value=iter([])), \
             mock.patch.object(service, "_stream_cloud_fallback", return_value=iter(["CLOUD ", "ANSWER"])) as cf:
            out = list(service.stream_llm_tokens("vat?", [{"text": "x"}], None, "en"))
        self.assertEqual("".join(out), "CLOUD ANSWER")
        rf.assert_called_once()  # an always-empty worker must eventually trip the breaker
        cf.assert_called_once()

    def test_stream_exception_triggers_cloud_fallback(self):
        from app import service

        with mock.patch.object(service.llm_module, "is_available", return_value=True), \
             mock.patch.object(service._LLM_CIRCUIT, "allow_request", return_value=True), \
             mock.patch.object(service._LLM_CIRCUIT, "record_failure") as rf, \
             mock.patch.object(service.llm_module, "generate_stream", side_effect=RuntimeError("boom")), \
             mock.patch.object(service, "_stream_cloud_fallback", return_value=iter(["CLOUD"])) as cf:
            out = list(service.stream_llm_tokens("vat?", [{"text": "x"}], None, "en"))
        self.assertEqual(out, ["CLOUD"])
        rf.assert_called_once()
        cf.assert_called_once()

    def test_breaker_open_streams_cloud_fallback_without_primary(self):
        from app import service

        with mock.patch.object(service.llm_module, "is_available", return_value=True), \
             mock.patch.object(service._LLM_CIRCUIT, "allow_request", return_value=False), \
             mock.patch.object(service.llm_module, "generate_stream") as gs, \
             mock.patch.object(service, "_stream_cloud_fallback", return_value=iter(["CLOUD"])) as cf:
            out = list(service.stream_llm_tokens("vat?", [{"text": "x"}], None, "en"))
        self.assertEqual(out, ["CLOUD"])
        gs.assert_not_called()
        cf.assert_called_once()

    def test_llm_unavailable_streams_cloud_fallback(self):
        from app import service

        with mock.patch.object(service.llm_module, "is_available", return_value=False), \
             mock.patch.object(service.llm_module, "generate_stream") as gs, \
             mock.patch.object(service, "_stream_cloud_fallback", return_value=iter(["CLOUD"])) as cf:
            out = list(service.stream_llm_tokens("vat?", [{"text": "x"}], None, "en"))
        self.assertEqual(out, ["CLOUD"])
        gs.assert_not_called()
        cf.assert_called_once()


class AgenticDeadlineContractTest(unittest.TestCase):
    """``_call_llm_agentic`` soft-failure contract: empty result dict + breaker
    bookkeeping, so the caller can run the plain chain (which carries the
    cloud fallback)."""

    def test_breaker_open_returns_empty_without_calling_llm(self):
        from app import service

        with mock.patch.object(service._LLM_CIRCUIT, "allow_request", return_value=False), \
             mock.patch.object(service.llm_module, "generate_with_tools") as gwt:
            out = service._call_llm_agentic("q", [{"text": "x"}], None, "en")
        self.assertEqual(out["text"], "")
        self.assertEqual(out["tool_calls"], [])
        gwt.assert_not_called()

    def test_empty_text_records_breaker_failure(self):
        from app import service

        empty = {"text": "", "tool_calls": [], "iterations": 1, "truncated": False}
        with mock.patch.object(service._LLM_CIRCUIT, "allow_request", return_value=True), \
             mock.patch.object(service._LLM_CIRCUIT, "record_failure") as rf, \
             mock.patch.object(service.llm_module, "generate_with_tools", return_value=empty):
            out = service._call_llm_agentic("q", [{"text": "x"}], None, "en")
        self.assertEqual(out["text"], "")
        rf.assert_called_once()

    def test_exception_records_breaker_failure_and_returns_empty(self):
        from app import service

        with mock.patch.object(service._LLM_CIRCUIT, "allow_request", return_value=True), \
             mock.patch.object(service._LLM_CIRCUIT, "record_failure") as rf, \
             mock.patch.object(service.llm_module, "generate_with_tools", side_effect=RuntimeError("boom")):
            out = service._call_llm_agentic("q", [{"text": "x"}], None, "en")
        self.assertEqual(out["text"], "")
        self.assertEqual(out["tool_calls"], [])
        rf.assert_called_once()


class AgenticCloudFallbackChainTest(unittest.TestCase):
    """Regression for the sync REST path: an empty agentic reply must run the
    plain LLM chain (``_call_llm_with_deadline`` → cloud fallback) before
    degrading to the extractive best-hit answer — the same chain the
    streaming path gets by falling through to ``stream_llm_tokens``."""

    _FAQ_ROW = {
        "question": "What is the standard VAT rate?",
        "answer": "The standard VAT rate in Uganda is 18%.",
        "source": "vat.csv",
        "tag": "vat",
        "_overlap": 3,
    }

    @classmethod
    def setUpClass(cls):
        from app import database as db

        db.init_db()
        from app import service

        cls.model = service.ChatModel()
        cls.model._llm_available = True

    def _generate(self, message: str, agentic_result: dict):
        from app import service
        from app.flags import flags

        flags.set("tool_use", True)
        self.addCleanup(flags.clear, "tool_use")
        approve = {
            "decision": "approve",
            "final_decision": "approve",
            "applied_revision": False,
            "reasons": [],
            "confidence_band": "high",
            "revised_reply": "",
        }
        with mock.patch.object(service, "_call_llm_agentic", return_value=agentic_result) as ag, \
             mock.patch.object(
                 service, "_call_llm_with_deadline",
                 return_value="Cloud answer: the standard VAT rate in Uganda is 18% [1].",
             ) as fb, \
             mock.patch.object(service, "_simple_search", return_value=[dict(self._FAQ_ROW)]), \
             mock.patch.object(service, "needs_clarification", return_value=""), \
             mock.patch.object(service, "verify_claims", return_value={"decision": "approve", "score": 1.0}), \
             mock.patch.object(service.ChatModel, "_deterministic_procedure_reply", return_value=("", False)), \
             mock.patch.object(service.ChatModel, "_priority_faq_hits", return_value=[]), \
             mock.patch.object(service.ChatModel, "_evaluate_response_judge", return_value=approve), \
             mock.patch.object(self.model._output_guard, "should_abstain", return_value=False), \
             mock.patch.object(self.model._cache, "get", return_value=None), \
             mock.patch.object(self.model._cache, "put"):
            out = self.model.generate(message=message)
        return out, ag, fb

    def test_empty_agentic_reply_runs_plain_llm_chain(self):
        out, ag, fb = self._generate(
            # NB: must not match the deterministic fast paths (rate lookup /
            # calculator / TIN) — this test exercises the agentic LLM chain.
            "What documents do I need when importing a vehicle?",
            {"text": "", "tool_calls": [], "iterations": 0, "truncated": False},
        )
        ag.assert_called_once()
        fb.assert_called_once()
        self.assertIn("Cloud answer", out["reply"])

    def test_nonempty_agentic_reply_skips_plain_chain(self):
        out, ag, fb = self._generate(
            "How is rental income taxed in Uganda?",
            {
                "text": "Agentic answer: rental income is taxed at 12% [1].",
                "tool_calls": [],
                "iterations": 1,
                "truncated": False,
            },
        )
        ag.assert_called_once()
        fb.assert_not_called()
        self.assertIn("Agentic answer", out["reply"])

    def test_llm_unavailable_with_cloud_ready_still_generates(self):
        """P3-1: with no local LLM at all, a configured cloud fallback must
        keep the generation step alive instead of degrading to FAQ extracts.
        The agentic branch stays off (tool calling is local-only)."""
        from app import service

        self.model._llm_available = False
        self.addCleanup(setattr, self.model, "_llm_available", True)
        with mock.patch.object(service, "_cloud_llm_ready", return_value=True):
            out, ag, fb = self._generate(
                "What withholding tax applies to imports in Uganda?",
                {"text": "", "tool_calls": [], "iterations": 0, "truncated": False},
            )
        ag.assert_not_called()  # agentic requires the local model
        fb.assert_called_once()
        self.assertIn("Cloud answer", out["reply"])


class CloudLlmReadyTest(unittest.TestCase):
    """service._cloud_llm_ready — the availability-gate widener: True only
    when the flag-gated cloud LLM tier could serve a reply on its own."""

    def tearDown(self):
        _clear_keys()
        os.environ.pop("LLM_FALLBACK_BACKEND", None)

    def test_false_when_flag_off(self):
        from app import service

        _with_keys()
        os.environ["LLM_FALLBACK_BACKEND"] = "gemini"
        with mock.patch.object(service.flags, "is_enabled", return_value=False):
            self.assertFalse(service._cloud_llm_ready())

    def test_false_without_backend_env(self):
        from app import service

        _with_keys()
        os.environ.pop("LLM_FALLBACK_BACKEND", None)
        with mock.patch.object(service.flags, "is_enabled", return_value=True):
            self.assertFalse(service._cloud_llm_ready())

    def test_false_when_unconfigured(self):
        from app import service

        _clear_keys()
        os.environ["LLM_FALLBACK_BACKEND"] = "gemini"
        with mock.patch.object(service.flags, "is_enabled", return_value=True):
            self.assertFalse(service._cloud_llm_ready())

    def test_true_for_configured_gemini(self):
        from app import service

        _with_keys()
        os.environ["LLM_FALLBACK_BACKEND"] = "gemini"
        with mock.patch.object(service.flags, "is_enabled", return_value=True):
            self.assertTrue(service._cloud_llm_ready())

    def test_true_for_configured_workers_ai(self):
        from app import service

        _with_keys()
        os.environ["LLM_FALLBACK_BACKEND"] = "workers_ai"
        with mock.patch.object(service.flags, "is_enabled", return_value=True):
            self.assertTrue(service._cloud_llm_ready())


class CfWhisperSTTTest(unittest.TestCase):
    """SpeechModel._cf_whisper_transcribe — Workers AI Whisper tier ⑤
    (flag/backend/config/breaker/budget-gated)."""

    def setUp(self):
        _with_keys()
        budget._redis = None
        budget._redis_tried = True
        budget._local_neurons.clear()
        from app.providers import breakers

        breakers.CF_STT_BREAKER.record_success()  # force CLOSED for isolation

    def tearDown(self):
        _clear_keys()
        budget._redis_tried = False
        gateway._client = None

    @staticmethod
    def _speech_model():
        from app.speech_service import SpeechModel

        return SpeechModel.__new__(SpeechModel)  # skip heavy __init__

    def test_transcribes_wav_when_configured(self):
        import numpy as np

        from app.speech_service import SpeechModel

        sm = self._speech_model()
        with mock.patch("app.flags.flags.is_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"STT_FALLBACK_BACKEND": "workers_ai"}), \
             mock.patch.object(SpeechModel, "_decode_audio_bytes",
                               return_value=np.zeros(1600, dtype="float32")), \
             mock.patch.object(gateway, "workers_ai_stt", return_value={"text": " webale "}) as stt:
            out = sm._cf_whisper_transcribe(b"\x00" * 320, 16000, "lg")
        self.assertEqual(out, "webale")
        stt.assert_called_once()
        self.assertTrue(stt.call_args.args[0].startswith(b"RIFF"))  # PCM16 WAV envelope

    def test_disabled_when_flag_off(self):
        sm = self._speech_model()
        with mock.patch("app.flags.flags.is_enabled", return_value=False), \
             mock.patch.object(gateway, "workers_ai_stt") as stt:
            self.assertEqual(sm._cf_whisper_transcribe(b"x", 16000, "en"), "")
        stt.assert_not_called()

    def test_disabled_without_backend_env(self):
        sm = self._speech_model()
        os.environ.pop("STT_FALLBACK_BACKEND", None)
        with mock.patch("app.flags.flags.is_enabled", return_value=True), \
             mock.patch.object(gateway, "workers_ai_stt") as stt:
            self.assertEqual(sm._cf_whisper_transcribe(b"x", 16000, "en"), "")
        stt.assert_not_called()

    def test_budget_exhausted_returns_empty(self):
        sm = self._speech_model()
        with mock.patch("app.flags.flags.is_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"STT_FALLBACK_BACKEND": "workers_ai"}), \
             mock.patch("app.providers.budget.try_consume_neurons", return_value=False), \
             mock.patch.object(gateway, "workers_ai_stt") as stt:
            self.assertEqual(sm._cf_whisper_transcribe(b"x", 16000, "en"), "")
        stt.assert_not_called()

    def test_gateway_error_records_breaker_failure(self):
        import numpy as np

        from app.providers import breakers
        from app.speech_service import SpeechModel

        sm = self._speech_model()
        with mock.patch("app.flags.flags.is_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"STT_FALLBACK_BACKEND": "workers_ai"}), \
             mock.patch.object(SpeechModel, "_decode_audio_bytes",
                               return_value=np.zeros(1600, dtype="float32")), \
             mock.patch.object(breakers.CF_STT_BREAKER, "record_failure") as rf, \
             mock.patch.object(gateway, "workers_ai_stt", side_effect=RuntimeError("HTTP 429")):
            self.assertEqual(sm._cf_whisper_transcribe(b"\x00" * 320, 16000, "en"), "")
        rf.assert_called_once()


class CfWorkersTtsTest(unittest.TestCase):
    """SpeechModel._cf_workers_ai_tts — Workers AI TTS for English
    (flag/backend/config/breaker/budget-gated, with a resilience fallback model)."""

    def setUp(self):
        _with_keys()
        budget._redis = None
        budget._redis_tried = True
        budget._local_neurons.clear()
        from app.providers import breakers

        breakers.CF_TTS_BREAKER.record_success()  # force CLOSED for isolation

    def tearDown(self):
        _clear_keys()
        budget._redis_tried = False
        gateway._client = None

    @staticmethod
    def _speech_model():
        from app.speech_service import SpeechModel

        return SpeechModel.__new__(SpeechModel)  # skip heavy __init__

    def test_synthesizes_when_configured(self):
        sm = self._speech_model()
        with mock.patch("app.flags.flags.is_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"TTS_FALLBACK_BACKEND": "workers_ai"}), \
             mock.patch.object(gateway, "workers_ai_tts",
                               return_value={"audio": b"ID3audio", "fmt": "mp3", "sample_rate": 24000}) as tts:
            out = sm._cf_workers_ai_tts("hello world", None, "en")
        self.assertIsNotNone(out)
        self.assertEqual(out.backend, "cf_workers_ai")
        self.assertEqual(out.audio, b"ID3audio")
        tts.assert_called_once()
        self.assertEqual(tts.call_args.kwargs.get("model"), "@cf/myshell-ai/melotts")  # default primary

    def test_disabled_when_flag_off(self):
        sm = self._speech_model()
        with mock.patch("app.flags.flags.is_enabled", return_value=False), \
             mock.patch.object(gateway, "workers_ai_tts") as tts:
            self.assertIsNone(sm._cf_workers_ai_tts("hi", None, "en"))
        tts.assert_not_called()

    def test_disabled_without_backend_env(self):
        sm = self._speech_model()
        os.environ.pop("TTS_FALLBACK_BACKEND", None)
        with mock.patch("app.flags.flags.is_enabled", return_value=True), \
             mock.patch.object(gateway, "workers_ai_tts") as tts:
            self.assertIsNone(sm._cf_workers_ai_tts("hi", None, "en"))
        tts.assert_not_called()

    def test_budget_exhausted_returns_none(self):
        sm = self._speech_model()
        with mock.patch("app.flags.flags.is_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"TTS_FALLBACK_BACKEND": "workers_ai"}), \
             mock.patch("app.providers.budget.try_consume_neurons", return_value=False), \
             mock.patch.object(gateway, "workers_ai_tts") as tts:
            self.assertIsNone(sm._cf_workers_ai_tts("hi", None, "en"))
        tts.assert_not_called()

    def test_resilience_fallback_to_second_model(self):
        sm = self._speech_model()
        calls = []

        def fake_tts(text, model=None, lang="en"):
            calls.append(model)
            if model == "@cf/myshell-ai/melotts":
                raise RuntimeError("HTTP 500")
            return {"audio": b"ID3second", "fmt": "mp3", "sample_rate": 24000}

        with mock.patch("app.flags.flags.is_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"TTS_FALLBACK_BACKEND": "workers_ai"}), \
             mock.patch.object(gateway, "workers_ai_tts", side_effect=fake_tts):
            out = sm._cf_workers_ai_tts("hi", None, "en")
        self.assertIsNotNone(out)
        self.assertEqual(out.audio, b"ID3second")
        self.assertEqual(out.voice, "@cf/deepgram/aura-2-en")  # TTS_FALLBACK_MODEL_2
        self.assertEqual(calls, ["@cf/myshell-ai/melotts", "@cf/deepgram/aura-2-en"])

    def test_all_models_fail_records_breaker_failure(self):
        from app.providers import breakers

        sm = self._speech_model()
        with mock.patch("app.flags.flags.is_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"TTS_FALLBACK_BACKEND": "workers_ai"}), \
             mock.patch.object(breakers.CF_TTS_BREAKER, "record_failure") as rf, \
             mock.patch.object(gateway, "workers_ai_tts", side_effect=RuntimeError("HTTP 429")):
            self.assertIsNone(sm._cf_workers_ai_tts("hi", None, "en"))
        self.assertEqual(rf.call_count, 2)  # both configured models tried + failed


class CfGatewayDispatchTest(unittest.TestCase):
    """gateway.workers_ai_stt / workers_ai_tts request+response shaping per model."""

    def setUp(self):
        _with_keys()

    def tearDown(self):
        _clear_keys()
        gateway._client = None

    def test_stt_turbo_uses_base64_json(self):
        import base64 as _b64

        fake = _FakeClient({"result": {"text": "hi", "transcription_info": {"language": "en"}}})
        with mock.patch.object(gateway, "_get_client", return_value=fake):
            out = gateway.workers_ai_stt(b"RAWAUDIO", model="@cf/openai/whisper-large-v3-turbo")
        self.assertEqual(out["text"], "hi")
        self.assertEqual(out["language"], "en")  # pulled from transcription_info
        self.assertIsNone(fake.calls[-1]["content"])
        self.assertEqual(fake.calls[-1]["json"], {"audio": _b64.b64encode(b"RAWAUDIO").decode("ascii")})

    def test_stt_original_whisper_uses_raw_bytes(self):
        fake = _FakeClient({"result": {"text": "hi"}})
        with mock.patch.object(gateway, "_get_client", return_value=fake):
            out = gateway.workers_ai_stt(b"RAWAUDIO", model="@cf/openai/whisper")
        self.assertEqual(out["text"], "hi")
        self.assertEqual(fake.calls[-1]["content"], b"RAWAUDIO")  # raw bytes body
        self.assertIsNone(fake.calls[-1]["json"])

    def test_tts_melotts_returns_wav(self):
        import base64 as _b64

        wav = b"RIFF" + b"\x00" * 40
        fake = _FakeClient({"result": {"audio": _b64.b64encode(wav).decode("ascii")}})
        with mock.patch.object(gateway, "_get_client", return_value=fake):
            out = gateway.workers_ai_tts("hello", model="@cf/myshell-ai/melotts", lang="en")
        self.assertEqual(out["audio"], wav)
        self.assertEqual(out["fmt"], "wav")
        self.assertEqual(fake.calls[-1]["json"], {"prompt": "hello", "lang": "en"})

    def test_tts_aura_returns_binary(self):
        class _BinResp:
            content = b"ID3binary-mp3-bytes"

            def raise_for_status(self):
                pass

        class _BinClient:
            def __init__(self):
                self.calls = []

            def post(self, url, headers=None, json=None, content=None):
                self.calls.append({"json": json})
                return _BinResp()

        fake = _BinClient()
        with mock.patch.object(gateway, "_get_client", return_value=fake):
            out = gateway.workers_ai_tts("hello", model="@cf/deepgram/aura-2-en")
        self.assertEqual(out["audio"], b"ID3binary-mp3-bytes")
        self.assertEqual(out["fmt"], "mp3")
        self.assertEqual(fake.calls[-1]["json"], {"text": "hello"})

    def test_cf_whisper_transcribe_passes_configured_model(self):
        import numpy as np

        import app.speech_service as ss
        from app.providers import breakers
        from app.speech_service import SpeechModel

        budget._redis = None
        budget._redis_tried = True
        budget._local_neurons.clear()
        breakers.CF_STT_BREAKER.record_success()
        sm = SpeechModel.__new__(SpeechModel)
        with mock.patch("app.flags.flags.is_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"STT_FALLBACK_BACKEND": "workers_ai"}), \
             mock.patch.object(ss, "STT_FALLBACK_MODEL", "@cf/openai/whisper-large-v3-turbo"), \
             mock.patch.object(SpeechModel, "_decode_audio_bytes",
                               return_value=np.zeros(1600, dtype="float32")), \
             mock.patch.object(gateway, "workers_ai_stt", return_value={"text": "x"}) as stt:
            sm._cf_whisper_transcribe(b"\x00" * 320, 16000, "en")
        self.assertEqual(stt.call_args.kwargs.get("model"), "@cf/openai/whisper-large-v3-turbo")
        budget._redis_tried = False


@unittest.skipUnless(os.getenv("CF_LIVE_TEST") == "1", "set CF_LIVE_TEST=1 + real CF keys to run")
class CfLiveTest(unittest.TestCase):
    """Live Cloudflare Workers AI STT+TTS round-trip via the real AI Gateway.

    Requires the real CLOUDFLARE_*/CF_AIG_* env vars (e.g. load .env). Run:
        CF_LIVE_TEST=1 python -m pytest backend/tests/test_providers.py -k CfLive -v
    """

    def test_tts_then_stt_roundtrip(self):
        gateway._client = None
        tts_model = os.getenv("TTS_FALLBACK_MODEL", "@cf/myshell-ai/melotts")
        stt_model = os.getenv("STT_FALLBACK_MODEL", "@cf/openai/whisper-large-v3-turbo")
        out = gateway.workers_ai_tts(
            "The standard VAT rate in Uganda is eighteen percent.", model=tts_model, lang="en"
        )
        self.assertTrue(out["audio"])
        self.assertIn(out["fmt"], ("wav", "mp3"))
        stt = gateway.workers_ai_stt(out["audio"], model=stt_model)
        self.assertTrue((stt.get("text") or "").strip(), "live STT returned empty transcript")


class ModelRoutingPolicyTest(unittest.TestCase):
    """providers.routing — policy constants + usage/fallback logging."""

    def test_default_model_ids(self):
        from app.providers import routing
        self.assertEqual(routing.CF_LLM_MODEL, "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
        self.assertEqual(
            routing.CF_LLM_FALLBACK_MODEL, "@cf/mistralai/mistral-small-3.1-24b-instruct"
        )
        self.assertEqual(
            routing.CF_LLM_FALLBACK_MODEL_2, "@cf/meta/llama-4-scout-17b-16e-instruct"
        )
        self.assertEqual(routing.CF_LLM_FAST_MODEL, "@cf/meta/llama-3.1-8b-instruct-fp8")

    def test_log_helpers_increment_metrics(self):
        from app.providers import routing
        with mock.patch.object(routing.metrics, "inc") as inc:
            routing.log_model_use("llm", "gemini_flash")
            routing.log_fallback("translate", "gemini_flash", "cf_workers_ai", "gemini_unavailable")
        self.assertEqual(
            inc.call_args_list[0],
            mock.call("model_usage_total", labels={"task": "llm", "model": "gemini_flash"}),
        )
        self.assertEqual(
            inc.call_args_list[1],
            mock.call("model_fallback_total", labels={
                "task": "translate", "from": "gemini_flash", "to": "cf_workers_ai",
                "reason": "gemini_unavailable"}),
        )


class LLMRoutingOrderTest(unittest.TestCase):
    """service._llm_cloud_fallback — Gemini -> CF Llama-3.3-70B -> Mistral Small 3.1."""

    def setUp(self):
        _with_keys()
        budget._redis = None
        budget._redis_tried = True
        budget._local_gemini.clear()
        budget._local_neurons.clear()
        from app.providers import breakers
        breakers.GEMINI_BREAKER.record_success()
        breakers.CF_LLM_BREAKER.record_success()

    def tearDown(self):
        _clear_keys()
        budget._redis_tried = False
        gateway._client = None

    def test_gemini_is_primary(self):
        from app import service
        with mock.patch.object(service.flags, "is_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"LLM_FALLBACK_BACKEND": "gemini"}), \
             mock.patch.object(gateway, "gemini_generate", return_value="ans [1]") as gg, \
             mock.patch.object(gateway, "workers_ai_chat") as wc:
            out = service._llm_cloud_fallback("q", [{"text": "ctx"}], None, "en")
        self.assertEqual(out, "ans [1]")
        gg.assert_called_once()
        wc.assert_not_called()  # Gemini served; CF not reached

    def test_cf_llama_70b_then_mistral_fallback(self):
        from app import service
        from app.providers import routing
        calls = []

        def chat(messages, model=None, **kw):
            calls.append(model)
            if model == routing.CF_LLM_MODEL:
                raise RuntimeError("HTTP 500")
            return "ans via mistral [1]"

        with mock.patch.object(service.flags, "is_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"LLM_FALLBACK_BACKEND": "workers_ai"}), \
             mock.patch.object(gateway, "workers_ai_chat", side_effect=chat):
            out = service._llm_cloud_fallback("q", [{"text": "ctx"}], None, "en")
        self.assertEqual(out, "ans via mistral [1]")
        self.assertEqual(calls, [routing.CF_LLM_MODEL, routing.CF_LLM_FALLBACK_MODEL])


class HybridCloudPrimaryRoutingTest(unittest.TestCase):
    """service._prefer_cloud_primary + cloud-primary dispatch in _call_llm_with_deadline.

    Cloud-primary is opt-in (LLM_PRIMARY_BACKEND=workers_ai); Ugandan locales stay
    local; the local model is the universal fallback.
    """

    def test_prefer_cloud_primary_opt_in_and_locale(self):
        from app import service

        with mock.patch.dict(os.environ), \
             mock.patch.object(service, "_cloud_llm_ready", return_value=True):
            # Default (opt-out) → local-first regardless of cloud readiness.
            os.environ.pop("LLM_PRIMARY_BACKEND", None)
            self.assertFalse(service._prefer_cloud_primary("en"))
            # Opt-in → cloud-primary for high-resource locales...
            os.environ["LLM_PRIMARY_BACKEND"] = "workers_ai"
            self.assertTrue(service._prefer_cloud_primary("en"))
            self.assertTrue(service._prefer_cloud_primary("sw"))
            # ...but Ugandan languages stay on the local LoRA-adapted model.
            for lg in ("lg", "nyn", "ach"):
                self.assertFalse(service._prefer_cloud_primary(lg))
        # Opt-in but cloud not ready → safe degrade to local-first.
        with mock.patch.dict(os.environ, {"LLM_PRIMARY_BACKEND": "workers_ai"}), \
             mock.patch.object(service, "_cloud_llm_ready", return_value=False):
            self.assertFalse(service._prefer_cloud_primary("en"))

    def test_cloud_primary_serves_before_local(self):
        from app import service

        with mock.patch.dict(os.environ, {"LLM_PRIMARY_BACKEND": "workers_ai"}), \
             mock.patch.object(service, "_cloud_llm_ready", return_value=True), \
             mock.patch.object(service, "_llm_cloud_fallback", return_value="CLOUD [1]") as cf, \
             mock.patch.object(service.llm_module, "generate", return_value="LOCAL") as gen:
            out = service._call_llm_with_deadline("vat?", [{"text": "x"}], None, "en")
        self.assertEqual(out, "CLOUD [1]")
        cf.assert_called_once()
        gen.assert_not_called()  # local never invoked when cloud-primary succeeds

    def test_cloud_primary_falls_back_to_local_when_cloud_empty(self):
        from app import service

        with mock.patch.dict(os.environ, {"LLM_PRIMARY_BACKEND": "workers_ai"}), \
             mock.patch.object(service, "_cloud_llm_ready", return_value=True), \
             mock.patch.object(service._LLM_CIRCUIT, "allow_request", return_value=True), \
             mock.patch.object(service, "_llm_cloud_fallback", return_value="") as cf, \
             mock.patch.object(service.llm_module, "generate", return_value="LOCAL [1]") as gen:
            out = service._call_llm_with_deadline("vat?", [{"text": "x"}], None, "en")
        self.assertEqual(out, "LOCAL [1]")
        cf.assert_called_once()  # cloud attempted once as primary...
        gen.assert_called_once()  # ...then local served as the fallback

    def test_ugandan_locale_stays_local_primary(self):
        from app import service

        with mock.patch.dict(os.environ, {"LLM_PRIMARY_BACKEND": "workers_ai"}), \
             mock.patch.object(service, "_cloud_llm_ready", return_value=True), \
             mock.patch.object(service._LLM_CIRCUIT, "allow_request", return_value=True), \
             mock.patch.object(service, "_llm_cloud_fallback", return_value="CLOUD") as cf, \
             mock.patch.object(service.llm_module, "generate", return_value="LOCAL LG [1]") as gen:
            out = service._call_llm_with_deadline("q", [{"text": "x"}], None, "lg")
        self.assertEqual(out, "LOCAL LG [1]")
        gen.assert_called_once()
        cf.assert_not_called()  # cloud is only the fallback for Ugandan locales


class TranslateRoutingOrderTest(unittest.TestCase):
    """speech_service._do_translate — Gemini -> CF Llama -> Sunbird."""

    @staticmethod
    def _sm():
        from app.speech_service import SpeechModel
        sm = SpeechModel.__new__(SpeechModel)
        sm._chat_model = None
        sm._mt = None
        return sm

    def test_gemini_first(self):
        from app.speech_service import SpeechModel
        sm = self._sm()
        with mock.patch.object(SpeechModel, "_gemini_translate", return_value="Omusolo"), \
             mock.patch.object(SpeechModel, "_cf_llama_translate") as cf:
            res = sm._do_translate("VAT?", "en", "lg")
        self.assertEqual(res.backend, "gemini_flash")
        self.assertEqual(res.text, "Omusolo")
        cf.assert_not_called()

    def test_cf_llama_when_gemini_empty(self):
        from app.speech_service import SpeechModel
        sm = self._sm()
        with mock.patch.object(SpeechModel, "_gemini_translate", return_value=""), \
             mock.patch.object(SpeechModel, "_cf_llama_translate", return_value="Omusolo via CF"):
            res = sm._do_translate("VAT?", "en", "lg")
        self.assertEqual(res.backend, "cf_workers_ai")
        self.assertEqual(res.text, "Omusolo via CF")

    def test_sunbird_when_both_cloud_llms_empty(self):
        from app import sunbird as real_sunbird
        from app.speech_service import SpeechModel
        sm = self._sm()
        with mock.patch.object(SpeechModel, "_gemini_translate", return_value=""), \
             mock.patch.object(SpeechModel, "_cf_llama_translate", return_value=""), \
             mock.patch.object(real_sunbird, "is_available", return_value=True), \
             mock.patch.object(real_sunbird, "translate", return_value="Omusolo via Sunbird"):
            res = sm._do_translate("VAT?", "en", "lg")
        self.assertEqual(res.backend, "sunbird_cloud")
        self.assertEqual(res.text, "Omusolo via Sunbird")


class TranslationFallbackTest(unittest.TestCase):
    """SpeechModel._gemini_translate uses Gemini 2.5 Flash for Luganda."""

    def setUp(self):
        _with_keys()
        budget._redis = None
        budget._redis_tried = True
        budget._local_gemini.clear()

    def tearDown(self):
        _clear_keys()
        budget._redis_tried = False
        gateway._client = None

    def test_gemini_translate_when_configured(self):
        from app.speech_service import SpeechModel

        with mock.patch("app.flags.flags.is_enabled", return_value=True), \
             mock.patch.dict(os.environ, {"TRANSLATE_FALLBACK_BACKEND": "gemini"}), \
             mock.patch.object(gateway, "gemini_generate", return_value="Omusolo gwa VAT") as gg:
            out = SpeechModel._gemini_translate("VAT tax", "en", "lg")
        self.assertEqual(out, "Omusolo gwa VAT")
        gg.assert_called_once()
        self.assertIn("Luganda", gg.call_args.kwargs.get("system", ""))

    def test_gemini_translate_disabled_when_flag_off(self):
        from app.speech_service import SpeechModel

        with mock.patch("app.flags.flags.is_enabled", return_value=False):
            self.assertEqual(SpeechModel._gemini_translate("x", "en", "lg"), "")


class DeterministicProcedureReplyFormattingTest(unittest.TestCase):
    """The vetted procedural templates must render stepwise Markdown and must NOT
    embed inline citation markers — references reach the UI via the result's
    ``citations`` / ``sources`` (the grounded-context panel), not the prose.
    Regression for a TIN answer that came back as a comma run-on ending in a
    dangling "[2]" citation digit."""

    @classmethod
    def setUpClass(cls):
        from app import database as db

        db.init_db()
        from app import service

        cls.model = service.ChatModel()

    def _tin_reply(self) -> str:
        from app import retriever as R

        hits = [
            {
                "source": "ura_instant_tin_application_faqs.csv",
                "question": "How do I apply for an instant TIN?",
                "answer": (
                    "Go to ura.go.ug, click Get a TIN, choose Instant TIN, select "
                    "Individual, enter your NIN, confirm you are not a robot, and submit."
                ),
                "text": "instant TIN application steps",
            },
        ]
        citations = R.HybridRetriever.build_citations(hits)
        reply, curated = self.model._deterministic_procedure_reply(
            "how do I register for a TIN", hits, citations
        )
        # The TIN template is fully hand-vetted → faithful by construction.
        self.assertTrue(curated)
        return reply

    def test_tin_reply_is_stepwise_markdown(self):
        reply = self._tin_reply()
        # Numbered steps, one per line — not a single comma-separated run-on.
        self.assertIn("1. Go to ura.go.ug", reply)
        self.assertIn("7. Submit", reply)
        self.assertNotIn("click Get a TIN, choose Instant TIN", reply)

    def test_tin_reply_has_no_inline_citation_marker(self):
        import re

        reply = self._tin_reply()
        # References live in the grounded-context panel, never inline in the prose.
        self.assertIsNone(re.search(r"\[\d+\]", reply))
        # And the answer must not end in a dangling digit.
        self.assertIsNone(re.search(r"\d\s*$", reply.strip()))

    def _return_reply(self) -> str:
        from app import retriever as R

        hits = [
            {
                "source": "ura_processes_systems_faqs.csv",
                "question": "How do I file a return?",
                "answer": (
                    "Login ura.go.ug with TIN/password → e-services → e-returns → select "
                    "return type and download template; enable macros, fill without renaming "
                    "or copy/paste; validate to generate upload file; back to e-returns upload "
                    "file with return period and captcha; submit; e-acknowledgment is issued "
                    "(also emailed/portal)."
                ),
                "text": "return filing steps",
            },
        ]
        citations = R.HybridRetriever.build_citations(hits)
        reply, curated = self.model._deterministic_procedure_reply(
            "how do I file my annual tax return", hits, citations
        )
        # Assembled from retrieved hits → scored against them, not assumed 1.0.
        self.assertFalse(curated)
        return reply

    def test_return_filing_reply_is_stepwise(self):
        import re

        reply = self._return_reply()
        # The ';'-delimited run-on becomes a numbered list (→ navigation stays inline).
        self.assertIn("1. Login ura.go.ug", reply)
        self.assertIn("5. Submit", reply)
        self.assertNotIn("; enable macros", reply)
        # No inline citation markers — references stay in the grounded-context panel.
        self.assertIsNone(re.search(r"\[\d+\]", reply))

    def test_format_procedure_steps_paragraph_fallback(self):
        # Non-procedural (no ';') text is left as a single paragraph, not a list.
        from app import service

        out = service.ChatModel._format_procedure_steps("A single grounded sentence.", "Lead:")
        self.assertEqual(out, "Lead: A single grounded sentence.")
        self.assertNotIn("1.", out)

    def test_grounded_revision_cleans_pdf_artifacts(self):
        # The "revise" fallback dumps raw retrieved chunks. PDF-extraction noise
        # (omitted-image blocks, page footers, orphan page numbers) must be
        # stripped, and an inline numbered list rendered as a real list.
        from app import service

        raw = (
            "Background to Taxation ==> picture [348 x 505] intentionally omitted "
            "CompulsoryPublicWorks(1991)----- End of picture text -----"
            "** A Guide to Taxation in Uganda | Sixth Edition 10 1 "
            "Taxes in Uganda are centrally assessed and collected by the Uganda Revenue "
            "Authority (URA), headed by a Commissioner General, under: 1. Customs Tariff "
            "Act, Cap. 337; 2. East African Customs Management Act; 3. Excise Tariff Act, "
            "Cap. 338; 5. 2"
        )
        out = service.ChatModel._build_grounded_revision(
            [{"source": "taxation_guide.pdf", "text": raw}], [], "explain taxation in uganda"
        )
        self.assertTrue(
            out.startswith("Here's the most relevant guidance I found in official URA sources:")
        )
        for noise in ("intentionally omitted", "End of picture text", "Sixth Edition", "picture ["):
            self.assertNotIn(noise, out)
        # inline numbered list is split onto its own lines (renders as a list)
        self.assertIn("\n1. Customs Tariff Act", out)
        self.assertIn("\n2. East African", out)
        # the truncation/page-number artifact "5. 2" must not survive
        self.assertNotIn("5. 2", out)


class LugandaTranslationRoutingTest(unittest.TestCase):
    """Luganda (lg↔en) translation must try Sunbird's Luganda-native NLLB BEFORE
    Gemini; other languages keep Gemini-first. Sunbird remains a fallback for all."""

    def _bare_model(self):
        # _do_translate only needs _mt / _chat_model; skip the heavy __init__.
        from app.speech_service import SpeechModel

        m = object.__new__(SpeechModel)
        m._mt = None
        m._chat_model = None
        return m

    def test_luganda_prefers_sunbird_over_gemini(self):
        from app import speech_service

        m = self._bare_model()
        with mock.patch.object(speech_service.SpeechModel, "_gemini_translate", return_value="GEMINI"), \
             mock.patch.object(speech_service.SpeechModel, "_cf_llama_translate", return_value=""), \
             mock.patch("app.sunbird.is_available", return_value=True), \
             mock.patch("app.sunbird.translate", return_value="SUNBIRD"):
            for src, tgt in (("lg", "en"), ("en", "lg")):
                res = m._do_translate("hello", src, tgt)
                self.assertEqual(res.backend, "sunbird_cloud", f"{src}->{tgt}")
                self.assertEqual(res.text, "SUNBIRD")

    def test_non_luganda_keeps_gemini_first(self):
        from app import speech_service

        m = self._bare_model()
        with mock.patch.object(speech_service.SpeechModel, "_gemini_translate", return_value="GEMINI"), \
             mock.patch.object(speech_service.SpeechModel, "_cf_llama_translate", return_value=""), \
             mock.patch("app.sunbird.is_available", return_value=True), \
             mock.patch("app.sunbird.translate", return_value="SUNBIRD"):
            res = m._do_translate("hello", "en", "fr")
            self.assertEqual(res.backend, "gemini_flash")

    def test_luganda_falls_back_to_gemini_when_sunbird_unavailable(self):
        from app import speech_service

        m = self._bare_model()
        with mock.patch.object(speech_service.SpeechModel, "_gemini_translate", return_value="GEMINI"), \
             mock.patch.object(speech_service.SpeechModel, "_cf_llama_translate", return_value=""), \
             mock.patch("app.sunbird.is_available", return_value=False):
            res = m._do_translate("hello", "lg", "en")
            self.assertEqual(res.backend, "gemini_flash")
            self.assertEqual(res.text, "GEMINI")


if __name__ == "__main__":
    unittest.main()

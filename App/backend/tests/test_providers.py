"""Unit tests for the cloud-provider fallback layer (no real network or keys).

Verifies: SecretStr masking, configured-gating, the AI Gateway two-credential
header pattern (and that no key lands in a URL query string), Vectorize hit
reshaping, and the free-tier budget guards.
"""

from __future__ import annotations

import os
import unittest
import unittest.mock as mock

from app.providers import budget, config, gateway, vectorize

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

    def post(self, url, headers=None, json=None, content=None):
        self.calls.append({"url": url, "headers": headers or {}, "json": json, "content": content})
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

    def test_vectorize_mode_off_without_flag(self):
        from app import retriever as R

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
             mock.patch.object(gateway, "gemini_generate", return_value="VAT is eighteen percent"):
            chunks = list(service._stream_cloud_fallback("vat?", [{"text": "VAT"}], None, "en"))
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks).strip(), "VAT is eighteen percent")


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
             mock.patch.object(service.ChatModel, "_deterministic_procedure_reply", return_value=""), \
             mock.patch.object(service.ChatModel, "_priority_faq_hits", return_value=[]), \
             mock.patch.object(service.ChatModel, "_evaluate_response_judge", return_value=approve), \
             mock.patch.object(self.model._output_guard, "should_abstain", return_value=False), \
             mock.patch.object(self.model._cache, "get", return_value=None), \
             mock.patch.object(self.model._cache, "put"):
            out = self.model.generate(message=message)
        return out, ag, fb

    def test_empty_agentic_reply_runs_plain_llm_chain(self):
        out, ag, fb = self._generate(
            "What is the standard VAT rate in Uganda?",
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


if __name__ == "__main__":
    unittest.main()

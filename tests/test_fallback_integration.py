"""Endpoint-level integration tests for the cloud LLM fallback chain.

Drives the real FastAPI app (`/v1/chat` and `/v1/chat/stream`) with a REAL
``ChatModel`` — not the stubbed model the API-surface suite uses — and the
primary LLM mocked DOWN, asserting the Cloudflare/Gemini fallback answer
reaches the HTTP/SSE surface. This is the chain that keeps the Crane Cloud
profile answering when the vLLM endpoint is unreachable:

    HTTP → ChatModel.generate / run_chat_turn → (empty primary | no local LLM)
         → _call_llm_with_deadline / stream_llm_tokens → _llm_cloud_fallback
         → providers.gateway (mocked Gemini) → HTTP reply / SSE tokens

Retrieval and the post-generation guard pipeline are pinned to deterministic
stubs — they have their own suites; what is under test here is the routing
from transport to fallback tier and back.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "App" / "backend"))

os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("CACHE_BACKEND", "memory")
os.environ.setdefault("ANALYTICS_BACKEND", "sqlite")
os.environ.setdefault("OTEL_ENABLED", "false")
os.environ.setdefault("QDRANT_URL", "http://127.0.0.1:1")
os.environ.setdefault("QDRANT_ENABLED", "false")
os.environ.setdefault("SPEECH_ENABLED", "false")
# NB: the analytics DB stays on the default cwd `data_store/` path — a tmpdir
# override would land on tmpfs, where SQLite WAL fails with "database is locked".

from fastapi.testclient import TestClient  # noqa: E402

from App.backend.app import database  # noqa: E402
from App.backend.app import service as service_module  # noqa: E402
from App.backend.app.flags import flags  # noqa: E402
from App.backend.app.main import app  # noqa: E402
from App.backend.app.providers import breakers, budget, gateway  # noqa: E402
from App.backend.app.providers import config as cloud_config  # noqa: E402

CLOUD_REPLY = "According to the cloud fallback, the standard VAT rate in Uganda is 18% [1]."

_FAQ_ROW = {
    "question": "What is the standard VAT rate?",
    "answer": "The standard VAT rate in Uganda is 18%.",
    "source": "vat.csv",
    "tag": "vat",
    "_overlap": 3,
}

_APPROVE_JUDGE = {
    "decision": "approve",
    "final_decision": "approve",
    "applied_revision": False,
    "reasons": [],
    "confidence_band": "high",
    "revised_reply": "",
}


def _passthrough_guard(model, **kwargs):
    return {
        "reply": kwargs["reply"],
        "faithfulness": 0.9,
        "escalate": False,
        "escalation_reason": "",
        "response_judge": _APPROVE_JUDGE,
        "handoff": None,
        "ticket_id": "",
        "revised": False,
        "claim_report": {"decision": "approve"},
    }


@pytest.fixture(scope="module")
def client():
    database.init_db()
    real_model = service_module.ChatModel()
    app.state.model = real_model
    app.state.speech = types.SimpleNamespace(enabled=False)
    # No `with` block: skips the lifespan (model download / prod gate).
    return TestClient(app)


@pytest.fixture()
def cloud_fallback_env():
    """Configure the Gemini fallback tier with fake creds + fresh budget/breakers."""
    env = {
        "CLOUDFLARE_ACCOUNT_ID": "acct-it",
        "CLOUDFLARE_API_TOKEN": "cf-it-token",
        "CF_AIG_GATEWAY": "ura-gw",
        "CF_AIG_TOKEN": "aig-it-token",
        "GEMINI_API_KEY": "AIza-it",
        "LLM_FALLBACK_BACKEND": "gemini",
    }
    os.environ.update(env)
    cloud_config.get_cloud_settings.cache_clear()
    flags.set("cloudflare_fallback", True)
    budget._redis = None
    budget._redis_tried = True  # force in-process counters
    budget._local_gemini.clear()
    breakers.GEMINI_BREAKER.record_success()
    service_module._LLM_CIRCUIT.record_success()
    yield
    flags.clear("cloudflare_fallback")
    for key in env:
        os.environ.pop(key, None)
    cloud_config.get_cloud_settings.cache_clear()
    budget._redis_tried = False


@pytest.fixture()
def deterministic_pipeline(client):
    """Pin retrieval + guards so only the LLM/fallback routing is variable."""
    model = app.state.model
    with mock.patch.object(service_module, "_simple_search", return_value=[dict(_FAQ_ROW)]), \
         mock.patch.object(service_module, "needs_clarification", return_value=""), \
         mock.patch.object(service_module, "verify_claims",
                           return_value={"decision": "approve", "score": 1.0}), \
         mock.patch.object(service_module, "_apply_output_guards", _passthrough_guard), \
         mock.patch.object(service_module.ChatModel, "_deterministic_procedure_reply",
                           return_value=("", False)), \
         mock.patch.object(service_module.ChatModel, "_priority_faq_hits", return_value=[]), \
         mock.patch.object(service_module.ChatModel, "_evaluate_response_judge",
                           return_value=dict(_APPROVE_JUDGE)), \
         mock.patch.object(model._output_guard, "should_abstain", return_value=False), \
         mock.patch.object(model._cache, "get", return_value=None), \
         mock.patch.object(model._cache, "put"):
        yield model


class TestChatEndpointFallback:
    def test_empty_primary_reply_serves_cloud_answer_over_http(
        self, client, cloud_fallback_env, deterministic_pipeline
    ):
        model = deterministic_pipeline
        model._llm_available = True
        with mock.patch.object(service_module.llm_module, "generate", return_value=""), \
             mock.patch.object(gateway, "gemini_generate", return_value=CLOUD_REPLY) as gg:
            resp = client.post("/v1/chat", json={"message": "What is the standard VAT rate?"})

        assert resp.status_code == 200
        assert "cloud fallback" in resp.json()["reply"]
        gg.assert_called_once()

    def test_llm_unavailable_still_serves_cloud_answer(
        self, client, cloud_fallback_env, deterministic_pipeline
    ):
        """An LLM-less deployment (is_available() False) must still engage the
        configured cloud tier instead of degrading to FAQ extracts."""
        model = deterministic_pipeline
        model._llm_available = False
        try:
            with mock.patch.object(service_module.llm_module, "is_available",
                                   return_value=False), \
                 mock.patch.object(service_module.llm_module, "generate", return_value=""), \
                 mock.patch.object(gateway, "gemini_generate", return_value=CLOUD_REPLY) as gg:
                resp = client.post(
                    "/v1/chat", json={"message": "Which VAT rate applies to imports?"}
                )
        finally:
            model._llm_available = True

        assert resp.status_code == 200
        assert "cloud fallback" in resp.json()["reply"]
        gg.assert_called_once()

    def test_unconfigured_fallback_degrades_to_best_hit(
        self, client, deterministic_pipeline
    ):
        """Without the flag/creds the old behaviour stands: extractive answer."""
        model = deterministic_pipeline
        model._llm_available = True
        with mock.patch.object(service_module.llm_module, "generate", return_value=""), \
             mock.patch.object(gateway, "gemini_generate") as gg:
            resp = client.post("/v1/chat", json={"message": "What is the standard VAT rate?"})

        assert resp.status_code == 200
        reply = resp.json()["reply"]
        assert "cloud fallback" not in reply
        assert "18%" in reply  # best-hit FAQ extract
        gg.assert_not_called()


class TestChatStreamEndpointFallback:
    def test_empty_primary_stream_serves_cloud_tokens_over_sse(
        self, client, cloud_fallback_env, deterministic_pipeline
    ):
        model = deterministic_pipeline
        model._llm_available = True
        with mock.patch.object(service_module.llm_module, "is_available", return_value=True), \
             mock.patch.object(service_module.llm_module, "generate_stream",
                               return_value=iter([])), \
             mock.patch.object(gateway, "gemini_generate", return_value=CLOUD_REPLY) as gg:
            with client.stream(
                "POST", "/v1/chat/stream", json={"message": "What is the standard VAT rate?"}
            ) as resp:
                assert resp.status_code == 200
                body = "".join(resp.iter_text())

        assert "event: token" in body
        assert "cloud fallback" in body
        gg.assert_called_once()

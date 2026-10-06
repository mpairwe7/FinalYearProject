"""End-to-end: the answer language over HTTP, the way the web client asks.

Drives the real FastAPI app (``/v1/chat`` and ``/v1/chat/stream``) with a real
``ChatModel``. The browser sends ``locale: "en"`` until the taxpayer touches
the language picker; from 2026-10-03 to the fix this test pins, the backend
took that default as a choice and answered every Luganda and Swahili question
in English. Retrieval and translation are pinned to stubs — what is under test
is the language decision and its continuity across stored turns.
"""

from __future__ import annotations

import json
import os
import sys
import types
import uuid
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

from app import database  # noqa: E402
from app import service as service_module  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# The load suite's own questions (tests/load/ngrok_multilang_suite.py).
LG_TIN = "Nnyinza ntya okwewandiisa okufuna TIN?"
SW_TIN = "Ninawezaje kujisajili kupata namba ya TIN?"

_FAQ_ROW = {
    "question": "How do I register for a TIN?",
    "answer": "Register for a TIN online on the URA portal.",
    "source": "tin.csv",
    "tag": "tin",
    "_overlap": 3,
}


@pytest.fixture(scope="module")
def client():
    database.init_db()
    app.state.model = service_module.ChatModel()
    app.state.speech = types.SimpleNamespace(enabled=False)
    return TestClient(app)


@pytest.fixture
def pinned(client):
    """Deterministic retrieval; translation is the identity."""
    model = app.state.model
    with mock.patch.object(service_module, "_simple_search", return_value=[dict(_FAQ_ROW)]), \
         mock.patch.object(service_module, "needs_clarification", return_value=""), \
         mock.patch.object(service_module, "english_retrieval_query", side_effect=lambda text, _loc: text), \
         mock.patch.object(service_module, "localize_reply", side_effect=lambda reply, _loc: reply), \
         mock.patch.object(service_module.ChatModel, "_maybe_handle_workflow", return_value=None), \
         mock.patch.object(service_module.ChatModel, "_priority_faq_hits", return_value=[]), \
         mock.patch.object(model._cache, "get", return_value=None), \
         mock.patch.object(model._cache, "put"):
        yield model


def _sse_payloads(body: str, event: str) -> list[dict]:
    out: list[dict] = []
    current = ""
    for line in body.splitlines():
        if line.startswith("event:"):
            current = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and current == event:
            try:
                out.append(json.loads(line.split(":", 1)[1].strip()))
            except json.JSONDecodeError:
                continue
    return out


def _stream(client: TestClient, payload: dict) -> list[dict]:
    with client.stream("POST", "/v1/chat/stream", json=payload) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    frames = _sse_payloads(body, "metadata")
    assert frames, body[:400]
    return frames


class TestStreamLanguage:
    def test_default_english_picker_answers_luganda_in_luganda(self, client, pinned):
        meta = _stream(client, {"message": LG_TIN, "locale": "en", "locale_explicit": False})[0]
        assert meta["locale"] == "lg"
        assert meta["locale_source"] == "detected"

    def test_client_without_the_flag_does_not_take_en_as_a_choice(self, client, pinned):
        meta = _stream(client, {"message": SW_TIN, "locale": "en"})[0]
        assert meta["locale"] == "sw"

    def test_picked_english_is_honoured(self, client, pinned):
        meta = _stream(client, {"message": LG_TIN, "locale": "en", "locale_explicit": True})[0]
        assert meta["locale"] == "en"
        assert meta["locale_source"] == "client_explicit"


class TestRestLanguageContinuity:
    def test_thread_language_carries_until_a_clear_switch(self, client, pinned):
        conversation_id = uuid.uuid4().hex
        headers = {"X-Session-ID": f"it-{uuid.uuid4().hex}"}

        def ask(message: str, locale: str) -> dict:
            resp = client.post(
                "/v1/chat",
                json={
                    "message": message,
                    "conversation_id": conversation_id,
                    "locale": locale,
                    "locale_explicit": False,
                },
                headers=headers,
            )
            assert resp.status_code == 200
            return resp.json()

        first = ask(LG_TIN, "en")
        assert (first["locale"], first["locale_source"]) == ("lg", "detected")

        # The client now sends the language it was answered in, as a hint.
        follow_up = ask("What about 150m?", "lg")
        assert (follow_up["locale"], follow_up["locale_source"]) == ("lg", "continuity")

        switched = ask("What documents do I need for the application?", "lg")
        assert (switched["locale"], switched["locale_source"]) == ("en", "switched")

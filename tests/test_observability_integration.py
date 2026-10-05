"""End-to-end: every transport's turn is audited and counted.

Real FastAPI app, real ``ChatModel``, the LLM mocked to a fixed answer. A
``/v1/chat`` turn and a ``/v1/chat/stream`` turn must each leave one
``generate`` row in the audit ledger — channel named, reply digest equal to
the text the client received — and both must show up on ``/metrics`` read
with the Prometheus scrape token. Before this change the streamed turn (the
web client's default) left no audit row at all (gap G103).
"""

from __future__ import annotations

import hashlib
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

from fastapi.testclient import TestClient  # noqa: E402

from app import database  # noqa: E402
from app import service as service_module  # noqa: E402
from app.analytics import metrics  # noqa: E402
from app.flags import flags  # noqa: E402
from app.main import app  # noqa: E402

REPLY = "The standard VAT rate in Uganda is 18% [1]."
METRICS_TOKEN = "integration-scrape-token-0123456789abcdef"  # pragma: allowlist secret

_FAQ_ROW = {
    "question": "How is VAT charged on local supplies?",
    "answer": "The standard VAT rate in Uganda is 18%.",
    "source": "vat.csv",
    "tag": "vat",
    "_overlap": 3,
}
_APPROVE = {
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
        "response_judge": _APPROVE,
        "handoff": None,
        "ticket_id": "",
        "revised": False,
        "claim_report": {"decision": "approve"},
    }


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _audit_rows(conversation_id: str) -> list[dict]:
    rows = database.query_all(
        "SELECT payload FROM audit_events WHERE event_type = 'generate' ORDER BY seq DESC LIMIT 50"
    )
    payloads = [json.loads(row["payload"]) for row in rows]
    return [p for p in payloads if p.get("conversation_id") == conversation_id]


@pytest.fixture(scope="module")
def client():
    database.init_db()
    app.state.model = service_module.ChatModel()
    app.state.speech = types.SimpleNamespace(enabled=False)
    return TestClient(app)


@pytest.fixture()
def pipeline(client):
    model = app.state.model
    model._llm_available = True
    flags.set("audit_ledger", True)
    with mock.patch.object(service_module, "_simple_search", return_value=[dict(_FAQ_ROW)]), \
         mock.patch.object(service_module, "needs_clarification", return_value=""), \
         mock.patch.object(service_module, "verify_claims", return_value={"decision": "approve", "score": 1.0}), \
         mock.patch.object(service_module, "_apply_output_guards", _passthrough_guard), \
         mock.patch.object(service_module.ChatModel, "_deterministic_procedure_reply", return_value=("", False)), \
         mock.patch.object(service_module.ChatModel, "_priority_faq_hits", return_value=[]), \
         mock.patch.object(service_module.ChatModel, "_evaluate_response_judge", return_value=dict(_APPROVE)), \
         mock.patch.object(service_module, "detect_language", return_value="en"), \
         mock.patch.object(model._output_guard, "should_abstain", return_value=False), \
         mock.patch.object(model._cache, "get", return_value=None), \
         mock.patch.object(model._cache, "put"), \
         mock.patch.object(service_module.llm_module, "is_available", return_value=True), \
         mock.patch.object(service_module.llm_module, "generate", return_value=REPLY), \
         mock.patch.object(service_module.llm_module, "generate_stream",
                           side_effect=lambda *a, **k: iter(["The standard VAT rate ", "in Uganda is 18% [1]."])):
        yield model
    flags.clear("audit_ledger")


def test_rest_turn_is_audited_with_the_served_reply(client, pipeline):
    conversation_id = f"obs-rest-{uuid.uuid4().hex[:10]}"
    key = 'chat_turns_total{channel="rest",outcome="answered"}'
    before = metrics.snapshot()["counters"].get(key, 0)
    resp = client.post("/v1/chat", json={"message": "How is VAT charged on local supplies?",
                                         "conversation_id": conversation_id})
    assert resp.status_code == 200
    assert metrics.snapshot()["counters"].get(key, 0) - before == 1
    rows = _audit_rows(conversation_id)
    assert len(rows) == 1, rows
    assert rows[0]["channel"] == "rest"
    assert rows[0]["reply_sha256"] == _sha(resp.json()["reply"])
    assert rows[0]["schema"] == 2


def test_streamed_turn_is_audited_and_counted(client, pipeline):
    conversation_id = f"obs-sse-{uuid.uuid4().hex[:10]}"
    with client.stream("POST", "/v1/chat/stream", json={"message": "How is VAT charged on local supplies?",
                                                        "conversation_id": conversation_id}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    tokens = [line[len("data: "):] for line in body.splitlines() if line.startswith("data: ")]
    assert "event: done" in body
    rows = _audit_rows(conversation_id)
    assert len(rows) == 1, f"streamed turn left {len(rows)} audit rows"
    assert rows[0]["channel"] == "sse"
    assert rows[0]["reply_sha256"] != _sha("")
    assert any("18%" in token for token in tokens)

    with mock.patch.dict(os.environ, {"METRICS_TOKEN": METRICS_TOKEN}):
        scrape = client.get("/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN}"})
    assert scrape.status_code == 200
    assert 'ura_chat_turns_total{channel="sse",outcome="answered"}' in scrape.text

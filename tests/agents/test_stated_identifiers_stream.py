"""A streamed turn keeps the TIN the taxpayer typed (G143, G154).

G143 exempted the identifiers a taxpayer states from output redaction, through a
turn-scoped ContextVar. The streaming core set it only inside the retrieval
thread, whose context copy never reaches the rest of the turn, so on the path
the web client uses a lookup the taxpayer asked for read "EFRIS holds … for TIN
[REDACTED_UG_TIN]". ``run_chat_turn`` now sets it in the request's own context,
which the turn's threads and the agentic stream inherit.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app import service as service_module
from app.guardrails import OutputGuard


def test_a_streamed_turn_keeps_the_tin_the_taxpayer_typed(monkeypatch) -> None:
    async def core(_model: Any, **_kwargs: Any):
        # The core redacts in worker threads and on the event loop alike.
        in_thread = await asyncio.to_thread(
            OutputGuard.redact_pii, "EFRIS holds this for TIN 1000000001; it also lists 1001234567."
        )
        yield ("token", in_thread)
        yield ("token", OutputGuard.redact_pii(" Again: 1000000001."))

    monkeypatch.setattr(service_module, "_run_chat_turn_events", core)

    async def collect() -> str:
        text = []
        async for event, payload in service_module.run_chat_turn(
            object(), message="What does EFRIS hold for TIN 1000000001?"
        ):
            if event == "token":
                text.append(payload)
        return "".join(text)

    text = asyncio.run(collect())

    assert text.count("1000000001") == 2
    # Only what the taxpayer typed is exempt: another TIN is still redacted.
    assert "1001234567" not in text
    assert "[REDACTED_UG_TIN]" in text


# The adapters each run their own DLP filter over the stream; each must be
# handed the stated identifiers, or removing them there would pass every test.
_STATED = "What does EFRIS hold for TIN 1000000001?"


async def _core_repeating_tins(_model: Any, **_kwargs: Any):
    yield ("metadata", {"sources": [], "retrieval_mode": "agentic"})
    yield ("token", "EFRIS holds this for TIN 1000000001; ")
    yield ("token", "it also lists 1001234567.")
    yield ("grounding", {"faithfulness_score": 1.0, "escalation_required": False})
    yield ("done", "")


def test_the_sse_adapter_keeps_the_stated_tin_and_redacts_another(monkeypatch) -> None:
    from unittest import mock

    from app.main import app
    from starlette.testclient import TestClient

    monkeypatch.setattr(service_module, "run_chat_turn", _core_repeating_tins)
    app.state.model = mock.MagicMock(name="chat_model")

    response = TestClient(app).post("/v1/chat/stream", json={"message": _STATED})

    assert response.status_code == 200
    assert "1000000001" in response.text
    assert "1001234567" not in response.text
    assert "[REDACTED_UG_TIN]" in response.text


def test_the_websocket_adapter_keeps_the_stated_tin_and_redacts_another(monkeypatch) -> None:
    import json
    from unittest import mock

    from app import chat_ws_v2
    from app.main import app
    from starlette.testclient import TestClient

    monkeypatch.setenv("FLAG_WS_CHAT", "true")
    monkeypatch.setattr(chat_ws_v2.service_module, "run_chat_turn", _core_repeating_tins)
    app.state.model = mock.MagicMock(name="chat_model")

    deltas: list[str] = []
    with TestClient(app).websocket_connect("/v2/chat/stream") as ws:
        ws.send_text(json.dumps({"type": "session_start"}))
        assert json.loads(ws.receive_text())["type"] == "session_ready"
        ws.send_text(json.dumps({"type": "response.create", "input": _STATED}))
        while True:
            frame = json.loads(ws.receive_text())
            if frame["type"] == "response.token":
                deltas.append(frame["delta"])
            if frame["type"] in ("response.done", "response.error"):
                break
        ws.send_text(json.dumps({"type": "session_end"}))

    text = "".join(deltas)
    assert "1000000001" in text
    assert "1001234567" not in text
    assert "[REDACTED_UG_TIN]" in text

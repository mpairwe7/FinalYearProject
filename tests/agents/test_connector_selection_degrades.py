"""A selected connector that cannot be used is left out of the turn, not fatal to it.

A taxpayer picks a service once and keeps asking. Before this, a service that
stopped answering — or that the account could no longer use after signing out
— made ``/v1/chat/stream`` answer 403, the client's retry on ``/v1/chat``
answered 403 too, and every later message in that conversation read "could not
reach the URA knowledge base" until the taxpayer found the chip and removed it.

These go through the real routes: the selection is resolved by the endpoint,
the model is only ever handed the usable services, and the client is told
which ones were left out and why.
"""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

import pytest
from app import service as service_module
from app.flags import flags
from app.main import app
from app.mcp import MCPClient
from app.mcp.transport import InProcessTransport
from starlette.testclient import TestClient


class _Server:
    """A bound server that answers ``tools/list`` with one reviewed read."""

    def __init__(self, namespace: str, tool: str) -> None:
        self.namespace = namespace
        self.descriptor = {
            "name": tool,
            "description": "A reviewed read operation",
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
            "_meta": {
                "ug.go.ura.chatbot/risk": "low",
                "ug.go.ura.chatbot/requiredScopes": [],
                "ug.go.ura.chatbot/requiresConfirmation": False,
            },
        }

    def list_tools(self) -> list[dict[str, Any]]:
        return [self.descriptor]

    def describe(self, name: str) -> dict[str, Any] | None:
        return self.descriptor if name == self.descriptor["name"] else None


class _Down(_Server):
    def list_tools(self) -> list[dict[str, Any]]:
        raise ConnectionError("connection refused")


@pytest.fixture
def connectors(monkeypatch: pytest.MonkeyPatch):
    """EFRIS answering, Payments down; chat connectors on for this test only."""
    client = MCPClient(
        {
            "core": InProcessTransport(),
            "efris": _Server("efris", "efris_taxpayer_status"),
            "payment_system": _Down("payment_system", "payment_view_status"),
        }
    )
    monkeypatch.setattr("app.mcp.get_client", lambda: client)
    monkeypatch.delenv("APP_ENV", raising=False)
    flags.set("enterprise_connectors", True)
    yield client
    flags.clear("enterprise_connectors")


def _model() -> mock.MagicMock:
    model = mock.MagicMock(name="chat_model")
    model.generate.return_value = {"reply": "Answered.", "sources": [], "conversation_id": "conv-degrade"}
    model.classify.return_value = {"predictions": [], "processing_time_ms": 1.0}
    return model


PAYMENTS_DOWN = {"namespace": "payment_system", "label": "Payments", "reason": "unavailable"}


def test_rest_turn_is_answered_without_the_service_that_is_down(connectors):
    model = _model()
    app.state.model = model
    response = TestClient(app).post(
        "/v1/chat",
        json={"message": "Is my TIN registered?", "connector_namespaces": ["efris", "payment_system"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Answered."
    assert body["connector_notices"] == [PAYMENTS_DOWN]
    # The model is only ever handed what may be used.
    assert model.generate.call_args.kwargs["connector_namespaces"] == ["efris"]


def test_stream_turn_is_answered_and_names_what_was_left_out(connectors, monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, Any] = {}

    async def run_chat_turn(_model, **kwargs):
        seen.update(kwargs)
        yield ("metadata", {"sources": [], "conversation_id": "conv-degrade"})
        yield ("token", "Answered.")
        yield ("done", "")

    monkeypatch.setattr(service_module, "run_chat_turn", run_chat_turn)
    app.state.model = _model()
    response = TestClient(app).post(
        "/v1/chat/stream",
        json={"message": "Is my TIN registered?", "connector_namespaces": ["payment_system", "efris"]},
    )

    assert response.status_code == 200
    frames = [block for block in response.text.replace("\r\n", "\n").split("\n\n") if block.strip()]
    metadata = next(frame for frame in frames if frame.startswith("event: metadata"))
    payload = json.loads(metadata.split("data: ", 1)[1])
    assert payload["connector_notices"] == [PAYMENTS_DOWN]
    assert payload["turn_id"]
    assert seen["connector_namespaces"] == ["efris"]
    assert "Answered." in response.text


def test_switching_connectors_off_does_not_fail_a_conversation_that_had_one(connectors):
    flags.set("enterprise_connectors", False)
    model = _model()
    app.state.model = model

    response = TestClient(app).post(
        "/v1/chat", json={"message": "What is VAT?", "connector_namespaces": ["efris"]}
    )

    assert response.status_code == 200
    assert response.json()["connector_notices"] == [
        {"namespace": "efris", "label": "EFRIS", "reason": "disabled"}
    ]
    assert model.generate.call_args.kwargs["connector_namespaces"] == []


def test_a_turn_without_a_selection_carries_no_notices(connectors):
    app.state.model = _model()
    response = TestClient(app).post("/v1/chat", json={"message": "What is VAT?"})

    assert response.status_code == 200
    assert response.json()["connector_notices"] == []

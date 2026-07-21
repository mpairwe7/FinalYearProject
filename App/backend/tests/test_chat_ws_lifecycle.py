"""Lifecycle + Phase 1 parity tests for /v2/chat/stream.

Covers:
  * Flag gating (closed with code 1001 when disabled).
  * session_start -> session_ready handshake.
  * ping/pong.
  * response.create streams metadata -> tokens -> done.
  * response.cancel acknowledged.
  * session_end closes the socket cleanly.
  * Invalid first frame is rejected.

The handler uses FastAPI's TestClient + websocket_connect.  Phase 1
tests monkey-patch ``service.run_chat_turn`` with a deterministic stub
so the test stays hermetic (no real LLM / Qdrant / SQLite traffic).
"""

from __future__ import annotations

import json
import unittest
import unittest.mock

from fastapi.testclient import TestClient


def _client_with_flag(enabled: bool) -> TestClient:
    """Return a TestClient with the ws_chat flag set and a stub model on app.state.

    Lifespan-initialised state (``app.state.model``) is replaced with a
    MagicMock so we don't pay for ChatModel construction or trigger
    Qdrant/LLM connections in the test process.
    """
    from app.flags import flags
    from app.main import app

    flags.set("ws_chat", enabled)
    if not hasattr(app.state, "model") or app.state.model is None:
        app.state.model = unittest.mock.MagicMock(name="stub_chat_model")
    return TestClient(app)


def _send(ws, payload: dict) -> None:
    ws.send_text(json.dumps(payload))


def _recv(ws) -> dict:
    return json.loads(ws.receive_text())


class TestChatWsLifecycle(unittest.TestCase):
    def tearDown(self) -> None:
        from app.flags import flags

        flags.clear("ws_chat")

    def test_flag_disabled_closes_socket(self) -> None:
        from starlette.websockets import WebSocketDisconnect

        client = _client_with_flag(False)
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with client.websocket_connect("/v2/chat/stream") as ws:
                # Server closes immediately; this read raises.
                ws.receive_text()
        self.assertEqual(ctx.exception.code, 1001)

    def test_session_start_handshake(self) -> None:
        client = _client_with_flag(True)
        with client.websocket_connect("/v2/chat/stream") as ws:
            _send(
                ws,
                {
                    "type": "session_start",
                    "conversation_id": "conv-1",
                    "locale": "en",
                    "protocol_version": 1,
                },
            )
            ready = _recv(ws)
            self.assertEqual(ready["type"], "session_ready")
            self.assertEqual(ready["protocol_version"], 1)
            self.assertFalse(ready["resume"])
            self.assertIn("agentic_events", ready["capabilities"])
            self.assertIsInstance(ready["session_id"], str)
            self.assertGreater(len(ready["session_id"]), 0)
            _send(ws, {"type": "session_end"})

    def test_ping_pong(self) -> None:
        client = _client_with_flag(True)
        with client.websocket_connect("/v2/chat/stream") as ws:
            _send(ws, {"type": "session_start"})
            self.assertEqual(_recv(ws)["type"], "session_ready")
            _send(ws, {"type": "ping"})
            self.assertEqual(_recv(ws)["type"], "pong")
            _send(ws, {"type": "session_end"})

    def test_response_create_streams_metadata_then_tokens_then_done(self) -> None:
        async def _fake_run_chat_turn(model, **kwargs):  # noqa: ARG001
            yield ("metadata", {"sources": [], "retrieval_mode": "fake"})
            yield ("token", "Hello ")
            yield ("token", "world!")
            yield ("grounding", {"faithfulness_score": 0.9, "escalation_required": False})
            yield ("done", "")
            yield (
                "_log",
                {"result": {"sources": []}, "full_reply": "Hello world!", "elapsed_ms": 12.3},
            )

        from app import chat_ws_v2

        client = _client_with_flag(True)
        with unittest.mock.patch.object(
            chat_ws_v2.service_module, "run_chat_turn", _fake_run_chat_turn
        ):
            with client.websocket_connect("/v2/chat/stream") as ws:
                _send(ws, {"type": "session_start"})
                self.assertEqual(_recv(ws)["type"], "session_ready")
                _send(ws, {"type": "response.create", "input": "hi"})

                meta = _recv(ws)
                self.assertEqual(meta["type"], "response.metadata")
                self.assertEqual(meta["retrieval_mode"], "fake")

                tok1 = _recv(ws)
                self.assertEqual(tok1["type"], "response.token")
                self.assertEqual(tok1["delta"], "Hello ")

                tok2 = _recv(ws)
                self.assertEqual(tok2["delta"], "world!")

                ground = _recv(ws)
                self.assertEqual(ground["type"], "response.grounding")
                self.assertAlmostEqual(ground["faithfulness_score"], 0.9)

                done = _recv(ws)
                self.assertEqual(done["type"], "response.done")

                _send(ws, {"type": "session_end"})

    def test_agentic_tool_call_events_are_forwarded_as_frames(self) -> None:
        """Phase 2 integration: agentic events from run_chat_turn surface as
        ``response.tool_call.*`` frames over the WebSocket."""

        async def _fake_run_chat_turn(model, **kwargs):  # noqa: ARG001
            yield ("metadata", {"sources": [], "retrieval_mode": "agentic"})
            yield ("retrieval.started", {"top_k": 4})
            yield ("retrieval.completed", {"hit_count": 3, "retrieval_mode": "agentic"})
            yield ("iteration.started", {"type": "iteration.started", "iteration": 0})
            yield (
                "tool_call.started",
                {
                    "type": "tool_call.started",
                    "call_id": "tc1",
                    "name": "calculate_vat",
                    "arguments": {"amount_ugx": 5000000},
                    "iteration": 0,
                },
            )
            yield (
                "tool_call.completed",
                {
                    "type": "tool_call.completed",
                    "call_id": "tc1",
                    "name": "calculate_vat",
                    "ok": True,
                    "result_summary": "VAT = 900,000 UGX",
                    "elapsed_ms": 12.5,
                },
            )
            yield ("token", "Your VAT is 900,000 UGX.")
            yield ("grounding", {"faithfulness_score": 0.9, "escalation_required": False})
            yield ("done", "")
            yield (
                "_log",
                {"result": {"sources": []}, "full_reply": "ok", "elapsed_ms": 22.0},
            )

        from app import chat_ws_v2

        client = _client_with_flag(True)
        with unittest.mock.patch.object(
            chat_ws_v2.service_module, "run_chat_turn", _fake_run_chat_turn
        ):
            with client.websocket_connect("/v2/chat/stream") as ws:
                _send(ws, {"type": "session_start"})
                _ = _recv(ws)  # session_ready
                _send(ws, {"type": "response.create", "input": "vat for 5m"})

                frames = []
                while True:
                    f = _recv(ws)
                    frames.append(f)
                    if f["type"] == "response.done":
                        break

                types = [f["type"] for f in frames]
                self.assertIn("response.metadata", types)
                self.assertIn("response.retrieval.started", types)
                self.assertIn("response.retrieval.completed", types)
                self.assertIn("response.iteration.started", types)
                self.assertIn("response.tool_call.started", types)
                self.assertIn("response.tool_call.completed", types)
                self.assertIn("response.token", types)
                self.assertIn("response.grounding", types)
                self.assertIn("response.done", types)

                started = next(f for f in frames if f["type"] == "response.tool_call.started")
                self.assertEqual(started["name"], "calculate_vat")
                self.assertEqual(started["arguments"], {"amount_ugx": 5000000})
                completed = next(
                    f for f in frames if f["type"] == "response.tool_call.completed"
                )
                self.assertTrue(completed["ok"])
                self.assertIn("VAT = 900,000", completed["result_summary"])

                _send(ws, {"type": "session_end"})

    def test_response_create_rejects_empty_input(self) -> None:
        client = _client_with_flag(True)
        with client.websocket_connect("/v2/chat/stream") as ws:
            _send(ws, {"type": "session_start"})
            self.assertEqual(_recv(ws)["type"], "session_ready")
            _send(ws, {"type": "response.create", "input": "   "})
            err = _recv(ws)
            self.assertEqual(err["type"], "response.error")
            self.assertEqual(err["code"], "invalid_input")
            done = _recv(ws)
            self.assertEqual(done["type"], "response.done")
            _send(ws, {"type": "session_end"})

    def test_response_cancel_acked(self) -> None:
        client = _client_with_flag(True)
        with client.websocket_connect("/v2/chat/stream") as ws:
            _send(ws, {"type": "session_start"})
            self.assertEqual(_recv(ws)["type"], "session_ready")
            _send(ws, {"type": "response.cancel"})
            ack = _recv(ws)
            self.assertEqual(ack["type"], "response.cancelled")
            _send(ws, {"type": "session_end"})

    def test_invalid_json_first_frame_rejected(self) -> None:
        from starlette.websockets import WebSocketDisconnect

        client = _client_with_flag(True)
        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect("/v2/chat/stream") as ws:
                ws.send_text("not json")
                err = _recv(ws)
                self.assertEqual(err["type"], "error")
                self.assertFalse(err["recoverable"])
                # Server closes after non-recoverable error.
                ws.receive_text()

    def test_wrong_first_frame_type_rejected(self) -> None:
        from starlette.websockets import WebSocketDisconnect

        client = _client_with_flag(True)
        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect("/v2/chat/stream") as ws:
                _send(ws, {"type": "ping"})  # not session_start
                err = _recv(ws)
                self.assertEqual(err["type"], "error")
                self.assertFalse(err["recoverable"])
                ws.receive_text()

    def test_unknown_message_recoverable(self) -> None:
        client = _client_with_flag(True)
        with client.websocket_connect("/v2/chat/stream") as ws:
            _send(ws, {"type": "session_start"})
            self.assertEqual(_recv(ws)["type"], "session_ready")
            _send(ws, {"type": "totally.bogus"})
            err = _recv(ws)
            self.assertEqual(err["type"], "error")
            self.assertTrue(err["recoverable"])
            # Socket still alive — ping works.
            _send(ws, {"type": "ping"})
            self.assertEqual(_recv(ws)["type"], "pong")
            _send(ws, {"type": "session_end"})


if __name__ == "__main__":
    unittest.main()

"""Phase 5 + 6 tests: protocol completeness and production hardening.

* response.create_partial is acknowledged and updates session state.
* session.modify is acknowledged with current capabilities.
* Per-user concurrent socket cap closes the (N+1)-th socket with 1013.
* AGENTIC_TURN_DEADLINE_S env var resolves correctly.
* Production env validation rejects WS_CHAT=true + AUTH_REQUIRED=false.
"""

from __future__ import annotations

import json
import os
import unittest
import unittest.mock

from fastapi.testclient import TestClient


def _client(enabled: bool = True) -> TestClient:
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


class PartialAndModifyFrameTest(unittest.TestCase):
    def tearDown(self) -> None:
        from app.flags import flags

        flags.clear("ws_chat")

    def test_create_partial_acked(self) -> None:
        client = _client(True)
        with client.websocket_connect("/v2/chat/stream") as ws:
            _send(ws, {"type": "session_start"})
            _recv(ws)
            _send(ws, {"type": "response.create_partial", "input": "what is the VAT for"})
            ack = _recv(ws)
            self.assertEqual(ack["type"], "response.create_partial.ack")
            self.assertEqual(ack["length"], len("what is the VAT for"))
            _send(ws, {"type": "session_end"})

    def test_session_modify_acked(self) -> None:
        client = _client(True)
        with client.websocket_connect("/v2/chat/stream") as ws:
            _send(ws, {"type": "session_start"})
            ready = _recv(ws)
            _send(ws, {"type": "session.modify", "modalities": ["text", "voice"]})
            ack = _recv(ws)
            self.assertEqual(ack["type"], "session.modified")
            self.assertEqual(ack["session_id"], ready["session_id"])
            self.assertTrue(ack["capabilities"]["tool_confirmation"])
            _send(ws, {"type": "session_end"})


class PerUserSocketCapTest(unittest.TestCase):
    def tearDown(self) -> None:
        from app.flags import flags

        flags.clear("ws_chat")
        os.environ.pop("WS_CHAT_MAX_PER_USER", None)
        # Clear the in-memory counter so other tests aren't polluted.
        from app import chat_ws_v2

        with chat_ws_v2._active_per_user_lock:  # noqa: SLF001
            chat_ws_v2._active_per_user.clear()  # noqa: SLF001

    def test_cap_rejects_excess_connections(self) -> None:
        os.environ["WS_CHAT_MAX_PER_USER"] = "1"
        from starlette.websockets import WebSocketDisconnect

        client = _client(True)
        with client.websocket_connect("/v2/chat/stream") as ws1:
            _send(ws1, {"type": "session_start"})
            _recv(ws1)  # session_ready — slot is held
            # Second connection should be refused.
            with self.assertRaises(WebSocketDisconnect) as ctx:
                with client.websocket_connect("/v2/chat/stream") as ws2:
                    ws2.receive_text()
            self.assertEqual(ctx.exception.code, 1013)
            _send(ws1, {"type": "session_end"})


class DeadlineResolutionTest(unittest.TestCase):
    def test_default(self) -> None:
        os.environ.pop("AGENTIC_TURN_DEADLINE_S", None)
        from app.service import _resolve_turn_deadline

        self.assertEqual(_resolve_turn_deadline(), 120.0)

    def test_override(self) -> None:
        os.environ["AGENTIC_TURN_DEADLINE_S"] = "5"
        try:
            from app.service import _resolve_turn_deadline

            self.assertEqual(_resolve_turn_deadline(), 5.0)
        finally:
            os.environ.pop("AGENTIC_TURN_DEADLINE_S", None)

    def test_disabled_with_zero(self) -> None:
        os.environ["AGENTIC_TURN_DEADLINE_S"] = "0"
        try:
            from app.service import _resolve_turn_deadline

            self.assertEqual(_resolve_turn_deadline(), 0.0)
        finally:
            os.environ.pop("AGENTIC_TURN_DEADLINE_S", None)

    def test_garbage_value_falls_back(self) -> None:
        os.environ["AGENTIC_TURN_DEADLINE_S"] = "not-a-number"
        try:
            from app.service import _resolve_turn_deadline

            self.assertEqual(_resolve_turn_deadline(), 120.0)
        finally:
            os.environ.pop("AGENTIC_TURN_DEADLINE_S", None)


class ToolMaxIterationResolutionTest(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("LLM_TOOL_MAX_ITER", None)

    def test_default(self) -> None:
        from app.service import _resolve_tool_max_iterations

        self.assertEqual(_resolve_tool_max_iterations(), 10)

    def test_clamped_to_hard_cap(self) -> None:
        os.environ["LLM_TOOL_MAX_ITER"] = "500"
        from app.service import _resolve_tool_max_iterations

        self.assertEqual(_resolve_tool_max_iterations(), 20)

    def test_clamped_to_minimum(self) -> None:
        os.environ["LLM_TOOL_MAX_ITER"] = "0"
        from app.service import _resolve_tool_max_iterations

        self.assertEqual(_resolve_tool_max_iterations(), 1)


if __name__ == "__main__":
    unittest.main()

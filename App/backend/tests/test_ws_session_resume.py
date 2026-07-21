"""Phase 3 tests: WS connection-local state and resume.

* WsChatSession.append_turn rolls oldest pairs once over the cap.
* Across two response.create turns on the same socket, the second turn
  does NOT touch the DB history fetch — it sees the cached history.
* previous_response_id triggers a resume attempt and sets ``resume``
  in the session_ready capability payload.
"""

from __future__ import annotations

import json
import unittest
import unittest.mock

from fastapi.testclient import TestClient


def _client_with_flag(enabled: bool) -> TestClient:
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


class WsChatSessionUnitTest(unittest.TestCase):
    def test_history_cache_caps_at_max(self) -> None:
        from app.chat_ws_v2 import _HISTORY_CACHE_MAX, WsChatSession

        s = WsChatSession(
            session_id="s",
            conversation_id="c",
            user_id="",
            tenant_id="default",
            locale="en",
        )
        for i in range(_HISTORY_CACHE_MAX):
            s.append_turn(f"u{i}", f"a{i}")
        # Each turn adds 2 entries; the cap trims back to the most recent.
        self.assertEqual(len(s.history), _HISTORY_CACHE_MAX)
        # The most recent entry should be the assistant message from the
        # last turn appended.
        self.assertEqual(s.history[-1]["role"], "assistant")

    def test_resume_with_no_conversation_id_returns_false(self) -> None:
        from app.chat_ws_v2 import WsChatSession

        s = WsChatSession(
            session_id="s",
            conversation_id="",
            user_id="",
            tenant_id="default",
            locale="en",
        )
        self.assertFalse(s.try_resume("resp_123"))
        self.assertFalse(s.resumed)
        self.assertTrue(s.resume_attempted)


class WsSessionStateIntegrationTest(unittest.TestCase):
    def tearDown(self) -> None:
        from app.flags import flags

        flags.clear("ws_chat")

    def test_second_turn_uses_in_memory_history(self) -> None:
        """Two response.create turns on the same socket should pass a
        non-empty history override to run_chat_turn on the second one."""

        call_args: list[dict] = []

        async def _fake_run_chat_turn(model, **kwargs):  # noqa: ARG001
            call_args.append(kwargs)
            yield ("metadata", {"sources": [], "retrieval_mode": "fake"})
            yield ("token", "OK")
            yield ("done", "")
            yield (
                "_log",
                {
                    "result": {"sources": [], "conversation_id": "conv-X"},
                    "full_reply": "OK",
                    "elapsed_ms": 1.0,
                },
            )

        from app import chat_ws_v2

        client = _client_with_flag(True)
        with unittest.mock.patch.object(
            chat_ws_v2.service_module, "run_chat_turn", _fake_run_chat_turn
        ):
            with client.websocket_connect("/v2/chat/stream") as ws:
                _send(ws, {"type": "session_start", "conversation_id": "conv-X"})
                ready = _recv(ws)
                self.assertEqual(ready["type"], "session_ready")
                self.assertTrue(ready["capabilities"]["session_resume"])

                _send(ws, {"type": "response.create", "input": "first"})
                # Drain frames until done
                while _recv(ws)["type"] != "response.done":
                    pass

                _send(ws, {"type": "response.create", "input": "second"})
                while _recv(ws)["type"] != "response.done":
                    pass

                _send(ws, {"type": "session_end"})

        self.assertEqual(len(call_args), 2)
        # First turn has no history override.
        self.assertIsNone(call_args[0].get("conversation_history_override"))
        # Second turn has the first turn's history cached in memory.
        override = call_args[1].get("conversation_history_override")
        self.assertIsNotNone(override)
        roles = [t["role"] for t in override]
        self.assertEqual(roles, ["user", "assistant"])
        self.assertEqual(override[0]["content"], "first")
        self.assertEqual(override[1]["content"], "OK")

    def test_session_ready_advertises_capabilities(self) -> None:
        from app.flags import flags

        flags.set("ws_chat", True)
        flags.set("tool_use", True)
        try:
            client = _client_with_flag(True)
            with client.websocket_connect("/v2/chat/stream") as ws:
                _send(ws, {"type": "session_start"})
                ready = _recv(ws)
                self.assertTrue(ready["capabilities"]["agentic_events"])
                self.assertTrue(ready["capabilities"]["session_resume"])
                _send(ws, {"type": "session_end"})
        finally:
            flags.clear("tool_use")


if __name__ == "__main__":
    unittest.main()

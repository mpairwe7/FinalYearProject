"""Phase 4 tests for inline tool-call confirmation (HITL).

Covers:
  * confirm_tokens.sign + verify round-trip & tamper detection
  * Token bound to session_id and call_id
  * Token expiry
  * WS handler: tool_call.confirmation_required event includes a token
  * WS handler: tool_call.confirm with approve invokes the tool with
    submit=True and forwards the result as response.tool_call.confirmed
  * WS handler: tool_call.confirm with reject does NOT invoke the tool
  * WS handler: replayed confirmations are rejected
"""

from __future__ import annotations

import json
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


class ConfirmTokenUnitTest(unittest.TestCase):
    def test_round_trip(self) -> None:
        from app.confirm_tokens import sign, verify

        token = sign(
            call_id="call_0_0",
            tool_name="submit_vat_return",
            idempotency_key="ik_abc",
            session_id="sess_xyz",
        )
        payload = verify(token, expected_call_id="call_0_0", expected_session_id="sess_xyz")
        self.assertIsNotNone(payload)
        self.assertEqual(payload["call_id"], "call_0_0")
        self.assertEqual(payload["tool_name"], "submit_vat_return")
        self.assertEqual(payload["idempotency_key"], "ik_abc")

    def test_tampered_token_rejected(self) -> None:
        from app.confirm_tokens import sign, verify

        token = sign(
            call_id="c", tool_name="t", idempotency_key="k", session_id="s"
        )
        tampered = token[:-4] + ("AAAA" if token[-4:] != "AAAA" else "BBBB")
        self.assertIsNone(verify(tampered))

    def test_wrong_session_id_rejected(self) -> None:
        from app.confirm_tokens import sign, verify

        token = sign(
            call_id="c", tool_name="t", idempotency_key="k", session_id="s_legit"
        )
        # Same call_id but a different session — should fail binding check.
        self.assertIsNone(verify(token, expected_session_id="s_other"))

    def test_expired_token_rejected(self) -> None:
        from app.confirm_tokens import sign, verify

        token = sign(
            call_id="c",
            tool_name="t",
            idempotency_key="k",
            session_id="s",
            ttl_seconds=60,  # min value
        )
        with unittest.mock.patch("time.time", return_value=10**12):
            self.assertIsNone(verify(token))

    def test_malformed_token_rejected(self) -> None:
        from app.confirm_tokens import verify

        self.assertIsNone(verify("not.a.real.token"))
        self.assertIsNone(verify(""))
        self.assertIsNone(verify(None))  # type: ignore[arg-type]


class WsConfirmFlowTest(unittest.TestCase):
    def tearDown(self) -> None:
        from app.flags import flags

        flags.clear("ws_chat")

    @staticmethod
    def _proposal_run_chat_turn():
        """Return a fake run_chat_turn that surfaces a confirmation_required."""

        async def _gen(model, **kwargs):  # noqa: ARG001
            yield ("metadata", {"sources": [], "retrieval_mode": "agentic"})
            yield (
                "tool_call.confirmation_required",
                {
                    "type": "tool_call.confirmation_required",
                    "call_id": "tc_phase4",
                    "name": "submit_vat_return",
                    "proposal": {
                        "action_type": "submit_vat_return",
                        "period": "2026-Q3",
                        "amount_ugx": 900000,
                        "idempotency_key": "ik_phase4",
                    },
                    "idempotency_key": "ik_phase4",
                },
            )
            yield ("token", "Awaiting your approval.")
            yield ("done", "")
            yield (
                "_log",
                {"result": {"sources": []}, "full_reply": "Awaiting your approval.", "elapsed_ms": 1.0},
            )

        return _gen

    def test_approve_invokes_tool_with_submit_true(self) -> None:
        from app import chat_ws_v2

        invoked: dict = {}

        class _StubRegistry:
            @staticmethod
            def call(name, args):
                invoked["name"] = name
                invoked["args"] = args
                return {"ok": True, "submitted": True, "receipt_id": "URA-REC-001"}

        client = _client(True)
        with unittest.mock.patch.object(
            chat_ws_v2.service_module, "run_chat_turn", self._proposal_run_chat_turn()
        ), unittest.mock.patch.dict(
            "sys.modules", {"app.tools": unittest.mock.MagicMock(ToolRegistry=_StubRegistry)}
        ):
            with client.websocket_connect("/v2/chat/stream") as ws:
                _send(ws, {"type": "session_start"})
                _recv(ws)  # session_ready
                _send(ws, {"type": "response.create", "input": "submit my Q3 VAT"})

                # Drain until done, capturing the confirmation_required frame.
                confirm_token = None
                while True:
                    f = _recv(ws)
                    if f["type"] == "response.tool_call.confirmation_required":
                        confirm_token = f["confirm_token"]
                        self.assertEqual(f["call_id"], "tc_phase4")
                        self.assertEqual(f["idempotency_key"], "ik_phase4")
                    if f["type"] == "response.done":
                        break
                self.assertIsNotNone(confirm_token)

                _send(
                    ws,
                    {
                        "type": "tool_call.confirm",
                        "confirm_token": confirm_token,
                        "call_id": "tc_phase4",
                        "idempotency_key": "ik_phase4",
                        "decision": "approve",
                    },
                )
                ack = _recv(ws)
                self.assertEqual(ack["type"], "response.tool_call.confirmed")
                self.assertEqual(ack["call_id"], "tc_phase4")
                self.assertTrue(ack["result"]["submitted"])

                _send(ws, {"type": "session_end"})

        self.assertEqual(invoked["name"], "submit_vat_return")
        self.assertTrue(invoked["args"]["submit"])
        self.assertEqual(invoked["args"]["idempotency_key"], "ik_phase4")

    def test_reject_does_not_invoke_tool(self) -> None:
        from app import chat_ws_v2

        called = []

        class _StubRegistry:
            @staticmethod
            def call(name, args):
                called.append((name, args))
                return {"ok": True}

        client = _client(True)
        with unittest.mock.patch.object(
            chat_ws_v2.service_module, "run_chat_turn", self._proposal_run_chat_turn()
        ), unittest.mock.patch.dict(
            "sys.modules", {"app.tools": unittest.mock.MagicMock(ToolRegistry=_StubRegistry)}
        ):
            with client.websocket_connect("/v2/chat/stream") as ws:
                _send(ws, {"type": "session_start"})
                _recv(ws)
                _send(ws, {"type": "response.create", "input": "submit Q3"})
                token = None
                while True:
                    f = _recv(ws)
                    if f["type"] == "response.tool_call.confirmation_required":
                        token = f["confirm_token"]
                    if f["type"] == "response.done":
                        break
                _send(
                    ws,
                    {
                        "type": "tool_call.confirm",
                        "confirm_token": token,
                        "call_id": "tc_phase4",
                        "idempotency_key": "ik_phase4",
                        "decision": "reject",
                    },
                )
                ack = _recv(ws)
                self.assertEqual(ack["type"], "response.tool_call.rejected")
                _send(ws, {"type": "session_end"})

        self.assertEqual(called, [])

    def test_replayed_confirmation_rejected(self) -> None:
        from app import chat_ws_v2

        class _StubRegistry:
            @staticmethod
            def call(name, args):
                return {"ok": True, "submitted": True}

        client = _client(True)
        with unittest.mock.patch.object(
            chat_ws_v2.service_module, "run_chat_turn", self._proposal_run_chat_turn()
        ), unittest.mock.patch.dict(
            "sys.modules", {"app.tools": unittest.mock.MagicMock(ToolRegistry=_StubRegistry)}
        ):
            with client.websocket_connect("/v2/chat/stream") as ws:
                _send(ws, {"type": "session_start"})
                _recv(ws)
                _send(ws, {"type": "response.create", "input": "submit Q3"})
                token = None
                while True:
                    f = _recv(ws)
                    if f["type"] == "response.tool_call.confirmation_required":
                        token = f["confirm_token"]
                    if f["type"] == "response.done":
                        break
                _send(
                    ws,
                    {
                        "type": "tool_call.confirm",
                        "confirm_token": token,
                        "call_id": "tc_phase4",
                        "idempotency_key": "ik_phase4",
                        "decision": "approve",
                    },
                )
                ack = _recv(ws)
                self.assertEqual(ack["type"], "response.tool_call.confirmed")

                # Replay: same token, same call_id
                _send(
                    ws,
                    {
                        "type": "tool_call.confirm",
                        "confirm_token": token,
                        "call_id": "tc_phase4",
                        "idempotency_key": "ik_phase4",
                        "decision": "approve",
                    },
                )
                replay = _recv(ws)
                self.assertEqual(replay["type"], "response.tool_call.confirm_failed")
                self.assertIn("consumed", replay["reason"])
                _send(ws, {"type": "session_end"})


if __name__ == "__main__":
    unittest.main()

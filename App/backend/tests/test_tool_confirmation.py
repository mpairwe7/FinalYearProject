"""Phase 4 tests for inline tool-call confirmation (HITL).

Covers:
  * confirm_tokens.sign + verify round-trip & tamper detection
  * Token bound to session_id and call_id
  * Token expiry
  * WS handler: tool_call.confirmation_required event includes a token
  * WS handler: tool_call.confirm with approve routes the submit through the
    MCP policy boundary (NOT ToolRegistry directly) and forwards the result
  * WS handler: tool_call.confirm with reject does NOT invoke the tool
  * WS handler: replayed confirmations are rejected
  * P0-1: a critical-tier submit is denied at confirm time when the
    authenticated principal lacks the role/consent — and allowed when it
    has them (the policy boundary is re-checked at submit, not just at
    proposal time).
"""

from __future__ import annotations

import json
import types
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


class _StubMCPClient:
    """Records call_tool invocations so tests can assert the submit went
    through the MCP policy boundary with the right principal + flags."""

    def __init__(self, result: dict | None = None, ok: bool = True) -> None:
        self.calls: list[dict] = []
        self._result = result or {"ok": True, "submitted": True, "receipt_id": "URA-REC-001"}
        self._ok = ok

    def call_tool(self, name, arguments, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append({"name": name, "arguments": arguments, "kwargs": kwargs})
        return types.SimpleNamespace(result=self._result, ok=self._ok)


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
    def _proposal_run_chat_turn(
        *,
        tool_name: str = "submit_vat_return",
        proposal: dict | None = None,
        idem: str = "ik_phase4",
        call_id: str = "tc_phase4",
    ):
        """Return a fake run_chat_turn that surfaces a confirmation_required."""
        proposal = proposal or {
            "action_type": "submit_vat_return",
            "period": "2026-Q3",
            "amount_ugx": 900000,
            "idempotency_key": idem,
        }

        async def _gen(model, **kwargs):  # noqa: ARG001
            yield ("metadata", {"sources": [], "retrieval_mode": "agentic"})
            yield (
                "tool_call.confirmation_required",
                {
                    "type": "tool_call.confirmation_required",
                    "call_id": call_id,
                    "name": tool_name,
                    "proposal": proposal,
                    "idempotency_key": idem,
                },
            )
            yield ("token", "Awaiting your approval.")
            yield ("done", "")
            yield (
                "_log",
                {"result": {"sources": []}, "full_reply": "Awaiting your approval.", "elapsed_ms": 1.0},
            )

        return _gen

    @staticmethod
    def _drain_for_token(ws) -> str | None:
        token = None
        while True:
            f = _recv(ws)
            if f["type"] == "response.tool_call.confirmation_required":
                token = f["confirm_token"]
            if f["type"] == "response.done":
                break
        return token

    def test_approve_routes_submit_through_policy(self) -> None:
        from app import chat_ws_v2

        stub = _StubMCPClient()
        client = _client(True)
        with unittest.mock.patch.object(
            chat_ws_v2.service_module, "run_chat_turn", self._proposal_run_chat_turn()
        ), unittest.mock.patch("app.mcp.get_client", return_value=stub):
            with client.websocket_connect("/v2/chat/stream") as ws:
                _send(ws, {"type": "session_start"})
                _recv(ws)  # session_ready
                _send(ws, {"type": "response.create", "input": "submit my Q3 VAT"})
                confirm_token = self._drain_for_token(ws)
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

        # The submit went through MCPClient.call_tool (policy boundary), NOT
        # ToolRegistry directly, and carried confirmed=True + the principal.
        self.assertEqual(len(stub.calls), 1)
        rec = stub.calls[0]
        self.assertEqual(rec["name"], "submit_vat_return")
        self.assertTrue(rec["arguments"]["submit"])
        self.assertEqual(rec["arguments"]["idempotency_key"], "ik_phase4")
        self.assertTrue(rec["kwargs"]["confirmed"])
        self.assertEqual(rec["kwargs"]["user_role"], "public")
        self.assertEqual(rec["kwargs"]["idempotency_key"], "ik_phase4")

    def test_reject_does_not_invoke_tool(self) -> None:
        from app import chat_ws_v2

        stub = _StubMCPClient()
        client = _client(True)
        with unittest.mock.patch.object(
            chat_ws_v2.service_module, "run_chat_turn", self._proposal_run_chat_turn()
        ), unittest.mock.patch("app.mcp.get_client", return_value=stub):
            with client.websocket_connect("/v2/chat/stream") as ws:
                _send(ws, {"type": "session_start"})
                _recv(ws)
                _send(ws, {"type": "response.create", "input": "submit Q3"})
                token = self._drain_for_token(ws)
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

        self.assertEqual(stub.calls, [])

    def test_replayed_confirmation_rejected(self) -> None:
        from app import chat_ws_v2

        stub = _StubMCPClient()
        client = _client(True)
        with unittest.mock.patch.object(
            chat_ws_v2.service_module, "run_chat_turn", self._proposal_run_chat_turn()
        ), unittest.mock.patch("app.mcp.get_client", return_value=stub):
            with client.websocket_connect("/v2/chat/stream") as ws:
                _send(ws, {"type": "session_start"})
                _recv(ws)
                _send(ws, {"type": "response.create", "input": "submit Q3"})
                token = self._drain_for_token(ws)
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

        # Only the first approve dispatched; the replay never reached the tool.
        self.assertEqual(len(stub.calls), 1)

    # ---- P0-1 regression: authorization re-checked at submit time --------
    @staticmethod
    def _critical_proposal_turn():
        return WsConfirmFlowTest._proposal_run_chat_turn(
            tool_name="ura_action_proposal",
            proposal={
                "action_type": "file_vat_return",
                "payload": {"period": "2026-Q3"},
                "idempotency_key": "ik_crit",
                "requires_confirmation": True,
            },
            idem="ik_crit",
            call_id="tc_crit",
        )

    def test_critical_tool_denied_when_principal_unauthorized(self) -> None:
        """An anonymous/public socket cannot drive a critical submit even with
        a valid confirmation token — the real ura_action_proposal tool is
        re-authorized through MCP policy and denied."""
        from app import chat_ws_v2
        from app.mcp import reset_client

        reset_client()  # use the real in-process client + tool registry
        client = _client(True)
        with unittest.mock.patch.object(
            chat_ws_v2.service_module, "run_chat_turn", self._critical_proposal_turn()
        ):
            with client.websocket_connect("/v2/chat/stream") as ws:  # no token → public
                _send(ws, {"type": "session_start"})
                _recv(ws)
                _send(ws, {"type": "response.create", "input": "file my VAT return"})
                token = self._drain_for_token(ws)
                self.assertIsNotNone(token)
                _send(
                    ws,
                    {
                        "type": "tool_call.confirm",
                        "confirm_token": token,
                        "call_id": "tc_crit",
                        "idempotency_key": "ik_crit",
                        "decision": "approve",
                    },
                )
                ack = _recv(ws)
                self.assertEqual(ack["type"], "response.tool_call.confirm_failed")
                self.assertIn("authorization denied", ack["reason"])
                _send(ws, {"type": "session_end"})

    def test_critical_tool_allowed_for_authorized_principal(self) -> None:
        """A staff principal holding the right consents passes the policy gate
        at submit (the tool itself fail-closes when the URA API is unset)."""
        from app import chat_ws_v2
        from app.auth.jwt_auth import make_dev_token
        from app.mcp import reset_client

        reset_client()
        jwt = make_dev_token(
            user_id="u_staff",
            role="ura_staff",
            granted_purposes=["ura_account_access", "ura_actions"],
        )
        client = _client(True)
        with unittest.mock.patch.object(
            chat_ws_v2.service_module, "run_chat_turn", self._critical_proposal_turn()
        ):
            with client.websocket_connect(
                "/v2/chat/stream", headers={"authorization": f"Bearer {jwt}"}
            ) as ws:
                _send(ws, {"type": "session_start"})
                _recv(ws)
                _send(ws, {"type": "response.create", "input": "file my VAT return"})
                token = self._drain_for_token(ws)
                self.assertIsNotNone(token)
                _send(
                    ws,
                    {
                        "type": "tool_call.confirm",
                        "confirm_token": token,
                        "call_id": "tc_crit",
                        "idempotency_key": "ik_crit",
                        "decision": "approve",
                    },
                )
                ack = _recv(ws)
                # Policy PASSED for this principal — the frame is `confirmed`,
                # not an authorization-denied confirm_failed.
                self.assertEqual(ack["type"], "response.tool_call.confirmed")
                _send(ws, {"type": "session_end"})


if __name__ == "__main__":
    unittest.main()

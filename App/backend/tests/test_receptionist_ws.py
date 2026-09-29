"""Tests for receptionist WebSocket endpoints and HTTP admin APIs."""

from __future__ import annotations

import json
import asyncio
import time
import unittest
import uuid
from unittest.mock import patch

from starlette.testclient import TestClient

from app import database as db
from app.auth.jwt_auth import make_dev_token
from app.flags import flags
from app.main import app
from app.receptionist.hub import CallEventHub, hub
from app.receptionist.state import CallRoom, CallState, registry
from app.receptionist.store import create_call, create_turn, init_receptionist_schema, update_call
from app.voice_consent import init_voice_consent_schema


def _listen_records(call_id: str) -> list[tuple[str, str, str]]:
    """(listener, role, transport) for each recorded listen-in on *call_id*."""
    rows = db.query_all(
        "SELECT user_id, metadata_json FROM voice_audit_log WHERE session_id = ? AND event_type = ?",
        (call_id, "staff_listened_call"),
    )
    out = []
    for row in rows:
        meta = json.loads(row["metadata_json"] or "{}")
        out.append((row["user_id"], meta.get("actor_role", ""), meta.get("transport", "")))
    return out


def _init_schema() -> None:
    """Every table these sockets write to: the voice audit log is created by the
    app lifespan, which TestClient(app) does not run, so without this the tests
    depended on an earlier test having started the app against the same DB."""
    db.init_db()
    init_receptionist_schema()
    init_voice_consent_schema()


class TestReceptionistWSAndHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _init_schema()
        cls.client = TestClient(app)

    def _auth_headers(self, role: str) -> dict[str, str]:
        token = make_dev_token(f"{role}_user", role=role)
        return {"Authorization": f"Bearer {token}"}

    def test_persisted_voice_flag_cannot_enable_production_socket(self):
        with patch.dict("os.environ", {"APP_ENV": "production"}), patch.object(flags, "is_enabled", return_value=True):
            from app.receptionist.ws import _voice_receptionist_enabled
            self.assertFalse(_voice_receptionist_enabled())

    def test_call_stream_flag_off_closes(self):
        with patch.object(flags, "is_enabled", side_effect=lambda name, **_kw: False if name == "voice_receptionist" else True):
            try:
                with self.client.websocket_connect("/v1/calls/stream") as ws:
                    ws.receive()
            except Exception:
                pass  # Closed by server with 1001

    def test_call_stream_consent_enforced(self):
        with patch.object(flags, "is_enabled", side_effect=lambda name, **_kw: True if name in ("voice_receptionist", "voice_consent") else False):
            with self.client.websocket_connect("/v1/calls/stream") as ws:
                # Send handshake with consent false
                ws.send_text(json.dumps({"type": "call_start", "locale": "en", "voice_consent_accepted": False}))
                msg = ws.receive_text()
                data = json.loads(msg)
                self.assertEqual(data.get("type"), "error")
                self.assertIn("consent required", data.get("detail", "").lower())

    def test_staff_stream_auditor_allowed_for_lobby_and_live(self):
        token = make_dev_token("auditor_1", role="ura_auditor")
        with patch.object(flags, "is_enabled", return_value=True), patch("app.receptionist.ws.is_ws_origin_allowed", return_value=True):
            # Auditor can connect to lobby stream
            with self.client.websocket_connect("/v1/admin/calls/stream") as ws:
                ws.send_text(json.dumps({"type": "authenticate", "access_token": token}))
                ws.send_text(json.dumps({"type": "ping"}))

    def test_officer_audio_auditor_refused(self):
        token = make_dev_token("auditor_1", role="ura_auditor")
        call_id = f"call_{uuid.uuid4().hex[:8]}"
        create_call(call_id, status="transferring")

        with patch.object(flags, "is_enabled", return_value=True), patch("app.receptionist.ws.is_ws_origin_allowed", return_value=True):
            # Auditor is refused on audio bridge (code 4403)
            with self.client.websocket_connect(f"/v1/admin/calls/{call_id}/audio") as ws:
                ws.send_text(json.dumps({"type": "authenticate", "access_token": token}))
                msg = ws.receive()
                self.assertEqual(msg["type"], "websocket.close")
                self.assertEqual(msg["code"], 4403)

    def test_lobby_event_carries_no_transcript(self):
        token = make_dev_token("staff_1", role="ura_staff")
        call_id = f"call_{uuid.uuid4().hex[:8]}"
        create_call(call_id, tenant_id="default")
        with patch.object(flags, "is_enabled", return_value=True), patch("app.receptionist.ws.is_ws_origin_allowed", return_value=True):
            with self.client.websocket_connect("/v1/admin/calls/stream") as ws:
                ws.send_text(json.dumps({"type": "authenticate", "access_token": token}))
                hub.publish_lobby(
                    "call.started",
                    {"call_id": call_id, "status": "ai", "topic": "VAT", "priority": "normal"},
                )
                msg = ws.receive_text()
                event = json.loads(msg)
                self.assertEqual(event.get("type"), "call.started")
                data = event.get("data", {})
                self.assertNotIn("text", data)
                self.assertNotIn("transcript", data)

    def test_http_admin_calls_endpoints(self):
        headers = self._auth_headers("ura_staff")
        call_id = f"call_{uuid.uuid4().hex[:8]}"
        create_call(call_id, status="ai", locale="en")
        create_turn(call_id, seq=1, speaker="caller", kind="utterance", text="How to pay VAT?")

        # 1. GET /v1/admin/calls
        r1 = self.client.get("/v1/admin/calls?status=all", headers=headers)
        self.assertEqual(r1.status_code, 200)
        self.assertIn("calls", r1.json())

        # 2. GET /v1/admin/calls/{call_id}
        r2 = self.client.get(f"/v1/admin/calls/{call_id}", headers=headers)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["call_id"], call_id)
        self.assertGreaterEqual(len(r2.json().get("turns", [])), 1)

        # 3. GET /v1/admin/calls/metrics
        r3 = self.client.get("/v1/admin/calls/metrics?days=7", headers=headers)
        self.assertEqual(r3.status_code, 200)
        self.assertIn("ai_only_completion_rate", r3.json())

        # 4. POST /v1/admin/calls/{call_id}/review
        r4 = self.client.post(
            f"/v1/admin/calls/{call_id}/review",
            json={"rating": 5, "note": "Great call"},
            headers=headers,
        )
        self.assertEqual(r4.status_code, 200)
        self.assertTrue(r4.json().get("ok"))

    def test_admin_call_detail_is_tenant_scoped(self):
        call_id = f"call_{uuid.uuid4().hex[:8]}"
        create_call(call_id, tenant_id="tenant-b", status="ai")
        token = make_dev_token("staff_a", tenant_id="tenant-a", role="ura_staff")
        with patch.object(flags, "is_enabled", side_effect=lambda name, **_kw: name in ("auth_required", "multi_tenant")):
            response = self.client.get(
                f"/v1/admin/calls/{call_id}", headers={"Authorization": f"Bearer {token}"}
            )
        self.assertEqual(response.status_code, 404)

    def test_staff_socket_requires_auth_message_not_query_token(self):
        token = make_dev_token("staff_1", role="ura_staff")
        with patch.object(flags, "is_enabled", return_value=True), patch("app.receptionist.ws.is_ws_origin_allowed", return_value=True):
            with self.client.websocket_connect(f"/v1/admin/calls/stream?token={token}") as ws:
                ws.send_text(json.dumps({"type": "ping"}))
                msg = ws.receive()
                self.assertEqual(msg["type"], "websocket.close")
                self.assertEqual(msg["code"], 4401)

    def test_staff_socket_rejects_disallowed_origin(self):
        with patch.object(flags, "is_enabled", return_value=True), patch("app.receptionist.ws.is_ws_origin_allowed", return_value=False):
            with self.assertRaises(Exception):
                with self.client.websocket_connect(
                    "/v1/admin/calls/stream", headers={"Origin": "https://untrusted.example"}
                ):
                    pass

    def test_supervisor_listen_in(self):
        call_id = f"call_{uuid.uuid4().hex[:8]}"
        create_call(call_id, status="bridged")
        update_call(call_id, officer_id="officer_alice")
        state = CallState(call_id=call_id, conversation_id=call_id, mode="bridged", officer_id="officer_alice")
        room = CallRoom(call_id=call_id, state=state)
        registry._rooms[call_id] = room
        try:
            token = make_dev_token("supervisor_1", role="ura_admin")
            with patch.object(flags, "is_enabled", return_value=True):
                with self.client.websocket_connect(f"/v1/admin/calls/{call_id}/audio?listen=true") as ws:
                    ws.send_text(json.dumps({"type": "authenticate", "access_token": token}))
                    ack = ws.receive_json()
                    self.assertEqual(ack.get("type"), "authenticated")
                    chunk = b"\x00\x01" * 160
                    room.broadcast_audio(chunk)
                    received = ws.receive_bytes()
                    self.assertEqual(received, chunk)
            self.assertEqual(_listen_records(call_id), [("supervisor_1", "ura_admin", "websocket")])
        finally:
            registry._rooms.pop(call_id, None)


class TestLiveKitListenIn(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _init_schema()
        cls.client = TestClient(app)

    def test_a_listener_is_removed_from_the_room_when_their_console_closes(self):
        from unittest.mock import AsyncMock

        from app.receptionist import livekit

        call_id = f"call_{uuid.uuid4().hex[:8]}"
        create_call(call_id, status="bridged")
        state = CallState(call_id=call_id, conversation_id=call_id, mode="bridged", officer_id="officer_alice")
        state.livekit_room = f"ura-{call_id}"
        room = CallRoom(call_id=call_id, state=state)
        registry._rooms[call_id] = room
        token = make_dev_token("supervisor_1", role="ura_admin")
        try:
            with (
                patch.object(flags, "is_enabled", return_value=True),
                patch.object(livekit, "enabled", return_value=True),
                patch.object(livekit, "mint_token", return_value="listen-jwt"),
                patch.object(livekit, "revoke_participant", new_callable=AsyncMock) as revoke,
                patch.dict("os.environ", {"LIVEKIT_URL": "wss://rtc.example.test"}),
            ):
                with self.client.websocket_connect(f"/v1/admin/calls/{call_id}/audio?listen=true") as ws:
                    ws.send_text(json.dumps({"type": "authenticate", "access_token": token}))
                    ready = ws.receive_json()
                    self.assertEqual((ready["type"], ready["can_publish"]), ("livekit_ready", False))
                    self.assertIn(ready["identity"], state.livekit_observer_identities)
                    state.mode = "ended"  # the call finishes; the listen loop exits
                    deadline = time.monotonic() + 5
                    while not revoke.await_count and time.monotonic() < deadline:
                        time.sleep(0.02)
            revoke.assert_awaited_once_with(state.livekit_room, ready["identity"])
            self.assertNotIn(ready["identity"], state.livekit_observer_identities)
            self.assertEqual(_listen_records(call_id), [("supervisor_1", "ura_admin", "livekit")])
        finally:
            registry._rooms.pop(call_id, None)


class TestLobbyTenantFanout(unittest.IsolatedAsyncioTestCase):
    async def test_call_events_only_reach_matching_tenant(self):
        _init_schema()
        call_id = f"call_fanout_{uuid.uuid4().hex[:8]}"
        create_call(call_id, tenant_id="tenant-a")
        event_hub = CallEventHub()
        loop = asyncio.get_running_loop()
        tenant_a = event_hub.subscribe_lobby(loop, tenant_id="tenant-a")
        tenant_b = event_hub.subscribe_lobby(loop, tenant_id="tenant-b")
        try:
            event_hub.publish_lobby("call.started", {"call_id": call_id})
            event = await asyncio.wait_for(tenant_a.get(), timeout=1)
            self.assertEqual(event["type"], "call.started")
            await asyncio.sleep(0)
            self.assertTrue(tenant_b.empty())
        finally:
            event_hub.unsubscribe_lobby(tenant_a)
            event_hub.unsubscribe_lobby(tenant_b)


class TestSocketTokenResolution(unittest.TestCase):
    def test_explicit_empty_first_message_token_does_not_fall_back_to_query(self):
        from types import SimpleNamespace

        from app.chat_ws_v2 import _resolve_ws_principal
        from app.auth.jwt_auth import JWTAuthError

        query_token = make_dev_token("staff_q", role="ura_staff")
        socket = SimpleNamespace(headers={}, query_params={"token": query_token})
        with self.assertRaises(JWTAuthError):
            _resolve_ws_principal(socket, required=True, token_override="")

    def test_first_message_token_resolves_staff_identity(self):
        from types import SimpleNamespace

        from app.chat_ws_v2 import _resolve_ws_principal

        token = make_dev_token("staff_frame", tenant_id="tenant-a", role="ura_staff")
        socket = SimpleNamespace(headers={}, query_params={})
        user_id, tenant_id, role, _purposes = _resolve_ws_principal(
            socket, required=True, token_override=token
        )
        self.assertEqual((user_id, tenant_id, role), ("staff_frame", "tenant-a", "ura_staff"))


class TestControlSocketHelpers(unittest.IsolatedAsyncioTestCase):
    """Malformed control frames are ignored; teardown always stops the media pipeline."""

    def test_only_json_objects_are_control_messages(self):
        from app.receptionist.ws import _control_message

        for raw in ("not json", "[1, 2]", '"hangup"', ""):
            self.assertIsNone(_control_message(raw))
        self.assertEqual(_control_message('{"type": "hangup"}'), {"type": "hangup"})

    async def test_a_runner_that_ignores_cancel_is_cancelled_after_the_wait(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app.receptionist import ws as ws_mod

        started = asyncio.Event()

        async def keeps_running() -> None:
            started.set()
            await asyncio.sleep(3600)

        runner = asyncio.create_task(keeps_running())
        await started.wait()
        task = SimpleNamespace(cancel=AsyncMock())
        with patch.object(ws_mod, "_PIPELINE_STOP_TIMEOUT_S", 0.05):
            await ws_mod._stop_pipeline(SimpleNamespace(call_id="call_x", pipeline_runner_task=runner), task)
        task.cancel.assert_awaited_once()
        with self.assertRaises(asyncio.CancelledError):
            await runner

    async def test_a_finished_runner_is_not_cancelled_again(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app.receptionist.ws import _stop_pipeline

        async def done() -> None:
            return None

        runner = asyncio.create_task(done())
        await runner
        task = SimpleNamespace(cancel=AsyncMock())
        await _stop_pipeline(SimpleNamespace(call_id="call_x", pipeline_runner_task=runner), task)
        task.cancel.assert_not_awaited()


class TestLiveKitCallControlLoop(unittest.TestCase):
    """The caller's control socket on a LiveKit call, driven through the real endpoint."""

    @classmethod
    def setUpClass(cls):
        _init_schema()
        cls.client = TestClient(app)

    def _run_call(self, frames: list[str]):
        """Start a LiveKit call, send *frames*, drop the socket; return what the pipeline saw."""
        import threading
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        import pytest

        pytest.importorskip(
            "pipecat.pipeline.runner",
            reason="LiveKit control-loop integration requires the receptionist extra",
        )

        from app.receptionist import livekit
        from app.receptionist import ws as ws_mod

        seen = SimpleNamespace(frames=[], cancelled=threading.Event(), running=threading.Event())

        class FakeTask:
            stop: asyncio.Event | None = None

            async def cancel(self) -> None:
                seen.cancelled.set()
                if self.stop is not None:
                    self.stop.set()

            async def queue_frame(self, frame) -> None:
                seen.frames.append(frame)

        class FakeRunner:
            async def run(self, task: FakeTask) -> None:
                task.stop = asyncio.Event()
                seen.running.set()
                await task.stop.wait()

        class FakeTransport:
            def event_handler(self, _name):
                return lambda fn: fn

            def get_participants(self):
                return []

        brain = SimpleNamespace(say_greeting=AsyncMock())
        with (
            patch.object(flags, "is_enabled", side_effect=lambda name, **_kw: name == "voice_receptionist"),
            patch.object(ws_mod, "is_ws_origin_allowed", return_value=True),
            patch.object(livekit, "enabled", return_value=True),
            patch.object(livekit, "mint_token", return_value="room-jwt"),
            patch.object(livekit, "close_room", new=AsyncMock()),
            patch.dict("os.environ", {"LIVEKIT_URL": "wss://rtc.example.test"}),
            patch.object(ws_mod, "build_call_pipeline", return_value=(FakeTask(), FakeTransport(), brain)),
            patch("pipecat.pipeline.runner.PipelineRunner", FakeRunner),
            patch.object(ws_mod.call_brief, "start"),
            patch.object(ws_mod.call_brief, "stop"),
            patch.object(ws_mod.risk, "start"),
            patch.object(ws_mod.risk, "stop"),
            patch.object(ws_mod, "generate_call_summary", new=MagicMock()),
        ):
            with self.client.websocket_connect("/v1/calls/stream") as sock:
                sock.send_text(json.dumps({"type": "call_start", "locale": "en", "voice_consent_accepted": True}))
                ready = sock.receive_json()
                self.assertEqual(ready["media"]["transport"], "livekit")
                self.assertTrue(seen.running.wait(5), "the media pipeline never started")
                for frame in frames:
                    sock.send_text(frame)
                deadline = time.monotonic() + 5
                while len(seen.frames) < sum('"set_language"' in f for f in frames) and time.monotonic() < deadline:
                    time.sleep(0.02)
                # Drop the control socket without a hangup, then wait for the
                # server's teardown to finish here: leaving the block cancels
                # the app task, which under load interrupted the teardown this
                # test exists to check (a flaky CancelledError).
                sock.close(1000)
                deadline = time.monotonic() + 10
                while registry.get(ready["call_id"]) is not None and time.monotonic() < deadline:
                    time.sleep(0.02)
        return seen

    def test_a_dropped_control_socket_stops_the_media_pipeline(self):
        seen = self._run_call([])
        self.assertTrue(seen.cancelled.is_set(), "the Pipecat task outlived the call")

    def test_a_malformed_control_frame_does_not_end_the_call(self):
        seen = self._run_call(["not json", "[1]", json.dumps({"type": "set_language", "language": "sw"})])
        self.assertEqual([frame.language for frame in seen.frames], ["sw"])
        self.assertTrue(seen.cancelled.is_set())

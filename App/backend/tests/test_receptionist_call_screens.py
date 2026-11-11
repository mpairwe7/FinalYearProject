"""Caller and officer screens, server side: what a browser is told, and when.

Regressions from phase 4 of the November 2026 call-receptionist review
(docs/plans/receptionist-assurance-2026-11-11.md, ADR-0042).
"""

from __future__ import annotations

import asyncio
import json
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from app import database as db
from app.receptionist import ws
from app.receptionist.hub import hub
from app.receptionist.state import CallRoom, CallState, registry
from app.receptionist.store import create_call, get_call, init_receptionist_schema


class RecordingSocket:
    """A browser socket that records what the server did to it, in order."""

    def __init__(self, inbound: list[str] | None = None, query: dict | None = None) -> None:
        self.events: list[tuple] = []
        self._inbound = list(inbound or [])
        self.headers = {"origin": "http://localhost:3300"}
        self.query_params = query or {}
        self.client = SimpleNamespace(host="127.0.0.1")

    async def accept(self) -> None:
        self.events.append(("accept",))

    async def close(self, code: int = 1000) -> None:
        self.events.append(("close", code))

    async def send_text(self, text: str) -> None:
        self.events.append(("send", json.loads(text)))

    async def send_bytes(self, data: bytes) -> None:
        self.events.append(("bytes", len(data)))

    async def receive_text(self) -> str:
        if self._inbound:
            return self._inbound.pop(0)
        await asyncio.sleep(3600)
        return ""

    async def receive(self) -> dict:
        return {"type": "websocket.receive", "text": await self.receive_text()}

    def kinds(self) -> list[str]:
        return [event[0] for event in self.events]

    def closed_with(self) -> int | None:
        codes = [event[1] for event in self.events if event[0] == "close"]
        return codes[0] if codes else None

    def errors(self) -> list[dict]:
        return [event[1] for event in self.events if event[0] == "send" and event[1].get("type") == "error"]


# ---------------------------------------------------------------------------
# A refusal the browser can read (G605)
# ---------------------------------------------------------------------------


class CallerRefusalTests(unittest.IsolatedAsyncioTestCase):
    """Closed before it was accepted, a socket reaches the browser as 1006: the reason was lost."""

    async def test_calls_switched_off_are_said_to_be_off(self):
        socket = RecordingSocket()
        with patch.object(ws, "_voice_receptionist_enabled", return_value=False), patch.object(
            ws, "_refusal_reason", return_value="off"
        ):
            await ws.call_stream_endpoint(socket)

        self.assertEqual(socket.kinds()[0], "accept")
        self.assertEqual(socket.closed_with(), 1001)
        self.assertEqual([e["code"] for e in socket.errors()], ["unavailable"])

    async def test_all_lines_busy_is_said_to_be_busy(self):
        socket = RecordingSocket()
        with (
            patch.object(ws, "_voice_receptionist_enabled", return_value=True),
            patch.object(ws, "is_ws_origin_allowed", return_value=True),
            patch.object(ws, "try_acquire", return_value=False),
        ):
            await ws.call_stream_endpoint(socket)

        self.assertEqual(socket.kinds()[0], "accept")
        self.assertEqual(socket.closed_with(), 1013)
        self.assertEqual([e["code"] for e in socket.errors()], ["busy"])

    async def test_a_page_from_another_site_is_still_refused_before_anything_is_accepted(self):
        socket = RecordingSocket()
        with (
            patch.object(ws, "_voice_receptionist_enabled", return_value=True),
            patch.object(ws, "is_ws_origin_allowed", return_value=False),
        ):
            await ws.call_stream_endpoint(socket)

        self.assertEqual(socket.events, [("close", 4403)])

    async def admitted(self, inbound: list[str], **patches) -> RecordingSocket:
        socket = RecordingSocket(inbound)
        with (
            patch.object(ws, "_voice_receptionist_enabled", return_value=True),
            patch.object(ws, "is_ws_origin_allowed", return_value=True),
            patch.object(ws, "try_acquire", return_value=True),
            patch.object(ws, "release", return_value=None),
        ):
            if "principal_error" in patches:
                with patch.object(ws, "_resolve_ws_principal", side_effect=patches["principal_error"]):
                    await asyncio.wait_for(ws.call_stream_endpoint(socket), timeout=5)
            else:
                await asyncio.wait_for(ws.call_stream_endpoint(socket), timeout=5)
        return socket

    async def test_every_error_the_caller_is_sent_carries_a_code_the_screen_can_translate(self):
        bad_start = await self.admitted(["not json"])
        signed_out = await self.admitted(
            [json.dumps({"type": "call_start", "access_token": "expired"})], principal_error=RuntimeError("expired")
        )

        self.assertEqual([e["code"] for e in bad_start.errors()], ["bad_request"])
        self.assertEqual([e["code"] for e in signed_out.errors()], ["signin"])
        self.assertEqual((bad_start.closed_with(), signed_out.closed_with()), (1003, 4401))


class StaffRefusalTests(unittest.IsolatedAsyncioTestCase):
    """The console hides its call layer on 1001, which it never received."""

    async def test_the_lobby_is_told_calls_are_off(self):
        socket = RecordingSocket()
        with patch.object(ws, "_voice_receptionist_enabled", return_value=False):
            await ws.staff_calls_stream_endpoint(socket, call_id=None)

        self.assertEqual((socket.kinds()[0], socket.closed_with()), ("accept", 1001))

    async def test_the_officer_audio_socket_is_told_calls_are_off(self):
        socket = RecordingSocket()
        with patch.object(ws, "_voice_receptionist_enabled", return_value=False):
            await ws.officer_audio_endpoint(socket, "call_x")

        self.assertEqual((socket.kinds()[0], socket.closed_with()), ("accept", 1001))

    async def test_staff_sockets_from_another_site_are_refused_before_accepting(self):
        for endpoint in (
            lambda s: ws.staff_calls_stream_endpoint(s, call_id=None),
            lambda s: ws.officer_audio_endpoint(s, "call_x"),
        ):
            socket = RecordingSocket()
            with (
                patch.object(ws, "_voice_receptionist_enabled", return_value=True),
                patch.object(ws, "is_ws_origin_allowed", return_value=False),
            ):
                await endpoint(socket)
            self.assertEqual(socket.events, [("close", 4403)])


# ---------------------------------------------------------------------------
# The lobby says when it is really open (G606)
# ---------------------------------------------------------------------------


class LobbyReadyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app import ws_concurrency

        ws_concurrency.reset()
        self.addCleanup(ws_concurrency.reset)

    async def test_a_signed_in_lobby_is_told_it_is_ready_before_any_event(self):
        socket = RecordingSocket([json.dumps({"type": "authenticate", "access_token": "t"})])
        with (
            patch.object(ws, "_voice_receptionist_enabled", return_value=True),
            patch.object(ws, "is_ws_origin_allowed", return_value=True),
            patch.object(ws, "_staff_session", return_value=("okello", "default", "ura_staff", None)),
        ):
            task = asyncio.create_task(ws.staff_calls_stream_endpoint(socket, call_id=None))
            for _ in range(50):
                await asyncio.sleep(0.01)
                if any(kind == "send" for kind in socket.kinds()):
                    break
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        sent = [event[1] for event in socket.events if event[0] == "send"]
        self.assertEqual(sent[:1], [{"type": "ready"}])

    async def test_a_refused_sign_in_is_never_told_it_is_ready(self):
        socket = RecordingSocket([json.dumps({"type": "authenticate", "access_token": "t"})])
        with (
            patch.object(ws, "_voice_receptionist_enabled", return_value=True),
            patch.object(ws, "is_ws_origin_allowed", return_value=True),
            patch.object(ws, "_staff_session", side_effect=RuntimeError("bad token")),
        ):
            await ws.staff_calls_stream_endpoint(socket, call_id=None)

        self.assertNotIn({"type": "ready"}, [event[1] for event in socket.events if event[0] == "send"])
        self.assertEqual(socket.closed_with(), 4401)


# ---------------------------------------------------------------------------
# The time limit is announced on every transport (G602)
# ---------------------------------------------------------------------------


class CallClockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_clock_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}", status="ai")
        self.caller = RecordingSocket()
        self.room = CallRoom(
            self.call_id, CallState(call_id=self.call_id, conversation_id=f"conv_{self.call_id}", mode="ai"),
            caller_ws=self.caller,
        )
        self.published: list[tuple[str, dict]] = []
        publish = patch.object(hub, "publish_call", side_effect=lambda cid, kind, data: self.published.append((kind, data)))
        publish.start()
        self.addCleanup(publish.stop)

    def warnings_to_caller(self) -> list[dict]:
        return [e[1] for e in self.caller.events if e[0] == "send" and e[1].get("type") == "duration_warning"]

    async def run_clock(self, *, ceiling: float, elapsed: float, wait: float = 1.0) -> None:
        self.room.state.started_at = time.time() - elapsed
        with patch.object(ws, "get_max_call_s", return_value=ceiling):
            await asyncio.wait_for(ws._call_clock(self.room), timeout=wait)

    async def test_the_caller_and_the_officers_are_told_a_minute_before_and_the_call_ends_at_the_limit(self):
        await self.run_clock(ceiling=120.0, elapsed=119.7)

        warnings = self.warnings_to_caller()
        self.assertEqual(len(warnings), 1)
        self.assertLessEqual(warnings[0]["seconds_remaining"], 60)
        self.assertIn("duration_warning", [kind for kind, _data in self.published])
        self.assertEqual(self.room.state.mode, "ended")
        self.assertEqual(get_call(self.call_id)["end_reason"], "max_call_duration")
        # The caller's screen is told the call ended rather than seeing the socket drop.
        self.assertIn({"type": "status", "status": "ended"}, [e[1] for e in self.caller.events if e[0] == "send"])

    async def test_a_short_ceiling_is_warned_at_half_its_length(self):
        await self.run_clock(ceiling=90.0, elapsed=89.7)

        warnings = self.warnings_to_caller()
        self.assertEqual(len(warnings), 1)
        self.assertLessEqual(warnings[0]["seconds_remaining"], 45)
        self.assertEqual(self.room.state.mode, "ended")

    async def test_a_caller_socket_that_stalls_cannot_hold_the_call_past_its_limit(self):
        class StalledSocket(RecordingSocket):
            async def send_text(self, text: str) -> None:
                await asyncio.sleep(3600)

        self.room.caller_ws = StalledSocket()
        await self.run_clock(ceiling=120.0, elapsed=119.7, wait=2.0)

        self.assertEqual(self.room.state.mode, "ended")
        self.assertEqual(get_call(self.call_id)["end_reason"], "max_call_duration")

    async def test_a_call_that_ended_first_is_left_as_it_ended(self):
        await self.room.end("caller_hangup")

        await self.run_clock(ceiling=120.0, elapsed=119.7)

        self.assertEqual(self.warnings_to_caller(), [])
        self.assertEqual(get_call(self.call_id)["end_reason"], "caller_hangup")


# ---------------------------------------------------------------------------
# A heartbeat cannot offer an officer who is still on a call (G610)
# ---------------------------------------------------------------------------


class PresenceHeartbeatGuardTests(unittest.IsolatedAsyncioTestCase):
    officer = "okello@ura.go.ug"

    async def asyncSetUp(self) -> None:
        from app.receptionist import presence

        db.init_db()
        init_receptionist_schema()
        self.presence = presence
        self.call_id = f"call_hb_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}", status="transferring")
        self.room = await registry.create(self.call_id, f"conv_{self.call_id}", tenant_id="default")
        self.room.state.mode = "transferring"
        presence.upsert_presence(self.officer, status="on_call", current_call_id=self.call_id)
        self.addCleanup(presence.upsert_presence, self.officer, status="offline")

    async def asyncTearDown(self) -> None:
        await registry.remove(self.call_id)

    def heartbeat(self, status: str) -> str:
        from starlette.testclient import TestClient

        from app.auth.jwt_auth import make_dev_token
        from app.main import app

        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {make_dev_token(self.officer, role='ura_staff')}"})
        response = client.put("/v1/admin/officers/me/presence", json={"status": status})
        self.assertEqual(response.status_code, 200, response.text)
        return self.presence.get_presence(self.officer)["raw_status"]

    async def test_an_officer_whose_media_is_reconnecting_is_not_offered_to_callers(self):
        self.room.state.reconnecting_officer = self.officer

        self.assertEqual(self.heartbeat("available"), "on_call")

    async def test_an_officer_holding_a_claim_is_not_offered_to_callers(self):
        self.room.state.claimed_by = self.officer

        self.assertEqual(self.heartbeat("available"), "on_call")

    async def test_an_officer_with_no_call_can_say_they_are_available(self):
        self.assertEqual(self.heartbeat("available"), "available")


# ---------------------------------------------------------------------------
# A claim is presence too (review of #717)
# ---------------------------------------------------------------------------


class ClaimPresenceTests(unittest.IsolatedAsyncioTestCase):
    officer = "okello@ura.go.ug"

    async def asyncSetUp(self) -> None:
        from app.receptionist import desk, presence

        db.init_db()
        init_receptionist_schema()
        self.desk, self.presence = desk, presence
        self.call_id = f"call_cp_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}", status="transferring")
        self.room = await registry.create(self.call_id, f"conv_{self.call_id}", tenant_id="default")
        self.room.state.mode = "transferring"
        lobby = patch.object(hub, "publish_lobby")
        lobby.start()
        self.addCleanup(lobby.stop)
        presence.upsert_presence(self.officer, status="busy")
        self.addCleanup(presence.upsert_presence, self.officer, status="offline")

    async def asyncTearDown(self) -> None:
        self.desk._cancel_claim_timer(self.room)
        await registry.remove(self.call_id)

    def status(self) -> tuple[str, str]:
        row = self.presence.get_presence(self.officer)
        return row["raw_status"], row.get("current_call_id") or ""

    async def test_an_officer_holding_a_claim_is_on_a_call(self):
        await self.desk.claim(self.call_id, self.officer)

        self.assertEqual(self.status(), ("on_call", self.call_id))

    async def test_giving_the_claim_back_restores_what_they_were(self):
        await self.desk.claim(self.call_id, self.officer)
        await self.desk.release(self.call_id, self.officer)

        self.assertEqual(self.status(), ("busy", ""))

    async def test_a_claim_that_lapses_restores_what_they_were(self):
        with patch.object(self.desk, "get_claim_timeout_s", return_value=0.05):
            await self.desk.claim(self.call_id, self.officer)
            await asyncio.sleep(0.2)

        self.assertEqual(self.status(), ("busy", ""))

    async def test_joining_the_call_keeps_what_they_were_before_the_claim(self):
        await self.desk.claim(self.call_id, self.officer)
        await self.desk.bridge(self.room, None, self.officer, speech_model=None)

        self.assertEqual(self.presence.get_presence(self.officer)["previous_status"], "busy")


if __name__ == "__main__":
    unittest.main()

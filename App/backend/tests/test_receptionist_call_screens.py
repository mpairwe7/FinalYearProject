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
from app.receptionist.store import create_call, get_call, init_receptionist_schema, update_call


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


class LateViewerTests(unittest.IsolatedAsyncioTestCase):
    """A console that opens a call after its time-limit warning still learns of it."""

    async def asyncSetUp(self) -> None:
        from app import ws_concurrency

        db.init_db()
        init_receptionist_schema()
        ws_concurrency.reset()
        self.addCleanup(ws_concurrency.reset)
        self.call_id = f"call_late_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}", status="ai")
        self.room = await registry.create(self.call_id, f"conv_{self.call_id}", tenant_id="default")
        self.addAsyncCleanup(registry.remove, self.call_id)

    async def snapshot(self) -> dict:
        socket = RecordingSocket([json.dumps({"type": "authenticate", "access_token": "t"})])
        with (
            patch.object(ws, "_voice_receptionist_enabled", return_value=True),
            patch.object(ws, "is_ws_origin_allowed", return_value=True),
            patch.object(ws, "_staff_session", return_value=("okello", "default", "ura_staff", None)),
        ):
            task = asyncio.create_task(ws.staff_calls_stream_endpoint(socket, call_id=self.call_id))
            for _ in range(100):
                await asyncio.sleep(0.01)
                if any(e[0] == "send" and e[1].get("type") == "snapshot" for e in socket.events):
                    break
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return next(e[1] for e in socket.events if e[0] == "send" and e[1].get("type") == "snapshot")

    async def test_the_snapshot_says_how_long_is_left_once_the_warning_has_gone_out(self):
        self.room.state.limit_at = time.time() + 42

        seconds = (await self.snapshot()).get("seconds_remaining")

        self.assertIsNotNone(seconds)
        self.assertTrue(40 <= seconds <= 42, seconds)

    async def test_the_snapshot_says_nothing_of_the_limit_before_the_warning(self):
        self.assertNotIn("seconds_remaining", await self.snapshot())

    async def test_a_console_on_a_worker_without_the_call_learns_the_limit_from_its_row(self):
        await registry.remove(self.call_id)  # the call is held by the other worker
        update_call(self.call_id, limit_at=time.time() + 42)

        seconds = (await self.snapshot()).get("seconds_remaining")

        self.assertIsNotNone(seconds)
        self.assertTrue(40 <= seconds <= 42, seconds)

    async def test_an_ended_call_has_no_time_left_to_report(self):
        await registry.remove(self.call_id)
        update_call(self.call_id, limit_at=time.time() + 42, status="ended")

        self.assertNotIn("seconds_remaining", await self.snapshot())


# ---------------------------------------------------------------------------
# The time limit is announced on every transport (G602)
# ---------------------------------------------------------------------------


real_wait_for = asyncio.wait_for


class CallClockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        self.setUp_room()
        self.published: list[tuple[str, dict]] = []
        publish = patch.object(hub, "publish_call", side_effect=lambda cid, kind, data: self.published.append((kind, data)))
        publish.start()
        self.addCleanup(publish.stop)

    def setUp_room(self) -> None:
        """A fresh call in the store and a fresh room for it, its caller recording what it is sent."""
        self.call_id = f"call_clock_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}", status="ai")
        self.caller = RecordingSocket()
        self.room = CallRoom(
            self.call_id, CallState(call_id=self.call_id, conversation_id=f"conv_{self.call_id}", mode="ai"),
            caller_ws=self.caller,
        )

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

    async def run_clock_on_a_controlled_clock(self, ceiling: float) -> tuple[list[float], list[tuple[float, str]]]:
        """Run the clock from the call's start with time moving only when it sleeps.

        Returns the delays it slept for and, for each live-stream event, the
        clock reading (seconds into the call) when it was published.
        """
        start = 1_000_000.0
        now = {"t": start}
        self.room.state.started_at = start
        slept: list[float] = []
        published: list[tuple[float, str]] = []
        real_sleep = asyncio.sleep

        async def sleep(delay: float, result=None):
            slept.append(delay)
            now["t"] += max(0.0, delay)
            await real_sleep(0)
            return result

        with patch.object(ws, "get_max_call_s", return_value=ceiling), \
                patch.object(ws, "time", SimpleNamespace(time=lambda: now["t"])), \
                patch.object(asyncio, "sleep", sleep), \
                patch.object(hub, "publish_call",
                             side_effect=lambda _cid, kind, _data: published.append((now["t"] - start, kind))):
            await real_wait_for(ws._call_clock(self.room), timeout=2.0)
        return slept, published

    async def test_the_warning_comes_a_minute_before_or_halfway_through_a_limit_of_a_minute(self):
        # "About a minute" is what the caller's screen says, in five languages:
        # every limit over a minute is warned exactly a minute before it.
        for ceiling, warned_at in ((900.0, 840.0), (120.0, 60.0), (90.0, 30.0), (60.0, 30.0), (30.0, 15.0)):
            with self.subTest(ceiling=ceiling):
                self.setUp_room()
                slept, published = await self.run_clock_on_a_controlled_clock(ceiling)

                # Warned at its moment; the end is published at the ceiling itself.
                self.assertEqual(published, [(warned_at, "duration_warning"), (ceiling, "status")])
                self.assertEqual(self.warnings_to_caller(),
                                 [{"type": "duration_warning", "seconds_remaining": int(ceiling - warned_at)}])
                # Then it sleeps to the ceiling, not past it, and ends the call there.
                self.assertEqual(slept[:2], [warned_at, ceiling - warned_at])
                # The deadline stays on the room and the call's row, for a
                # console that opens the call later, on either worker.
                self.assertEqual(self.room.state.limit_at, 1_000_000.0 + ceiling)
                self.assertEqual(get_call(self.call_id)["limit_at"], 1_000_000.0 + ceiling)
                self.assertEqual(self.room.state.mode, "ended")
                self.assertEqual(get_call(self.call_id)["end_reason"], "max_call_duration")

    async def test_a_caller_socket_that_stalls_cannot_hold_the_call_past_its_limit(self):
        class StalledSocket(RecordingSocket):
            async def send_text(self, text: str) -> None:
                await asyncio.sleep(3600)

        self.room.caller_ws = StalledSocket()
        await self.run_clock(ceiling=120.0, elapsed=119.7, wait=2.0)

        self.assertEqual(self.room.state.mode, "ended")
        self.assertEqual(get_call(self.call_id)["end_reason"], "max_call_duration")

    def caller_hanging_up_when_told(self):
        """A caller socket that hangs up as soon as it is told the call ended.

        The caller endpoint then ends the room as ``caller_hangup`` (its
        ``_finish_call``), possibly before whoever ended the call has recorded why.
        """
        room = self.room

        class HangsUpWhenTold(RecordingSocket):
            async def send_text(self, text: str) -> None:
                await super().send_text(text)
                if json.loads(text) == {"type": "status", "status": "ended"}:
                    await room.end("caller_hangup")

        return HangsUpWhenTold()

    async def test_a_caller_who_hangs_up_on_hearing_the_limit_is_ended_by_the_limit(self):
        self.room.caller_ws = self.caller_hanging_up_when_told()

        await self.run_clock(ceiling=120.0, elapsed=119.7)

        self.assertEqual(self.room.state.end_reason, "max_call_duration")
        self.assertEqual(get_call(self.call_id)["end_reason"], "max_call_duration")

    async def test_a_caller_who_hangs_up_on_being_hung_up_on_keeps_the_reason_they_were(self):
        from app.receptionist import desk

        self.room.caller_ws = self.caller_hanging_up_when_told()

        await desk.hang_up_caller(self.room, "officer_ended")

        self.assertEqual(get_call(self.call_id)["end_reason"], "officer_ended")

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
# A media pipeline that cannot start is refused like calls that are off
# ---------------------------------------------------------------------------


class PipelineFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_call_whose_media_cannot_start_is_told_so_and_closed_with_1001(self):
        from app import ws_concurrency

        db.init_db()
        init_receptionist_schema()
        ws_concurrency.reset()
        self.addCleanup(ws_concurrency.reset)
        lobby = patch.object(hub, "publish_lobby")
        lobby.start()
        self.addCleanup(lobby.stop)
        caller = RecordingSocket(
            inbound=[json.dumps({"type": "call_start", "locale": "en", "voice_consent_accepted": True})]
        )
        with (
            patch.object(ws, "_voice_receptionist_enabled", return_value=True),
            patch.object(ws.livekit, "enabled", return_value=True),
            patch.object(ws.livekit, "mint_token", return_value="caller-token"),
            patch.dict("os.environ", {"LIVEKIT_URL": "wss://rtc.example.test"}),
            patch.object(ws, "build_call_pipeline", side_effect=RuntimeError("no media server")),
            patch.object(ws, "generate_call_summary"),
        ):
            await asyncio.wait_for(ws.call_stream_endpoint(caller), timeout=5.0)

        self.assertEqual([e["code"] for e in caller.errors()], ["unavailable"])
        # Closed with the code the caller's screen reads as "unavailable", after
        # the error frame, rather than by the server with whatever it picks.
        self.assertEqual(caller.closed_with(), 1001)
        self.assertEqual(caller.kinds()[-1], "close")


# ---------------------------------------------------------------------------
# A claim is presence too (review of #717)
# ---------------------------------------------------------------------------


class ClaimPresenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app.receptionist import desk, presence

        # One officer per test: calls they held in other tests stay in the store.
        self.officer = f"okello_{uuid.uuid4().hex[:6]}@ura.go.ug"
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

    async def waiting_room(self) -> CallRoom:
        call_id = f"call_cp_{uuid.uuid4().hex[:8]}"
        create_call(call_id, conversation_id=f"conv_{call_id}", status="transferring")
        room = await registry.create(call_id, f"conv_{call_id}", tenant_id="default")
        room.state.mode = "transferring"

        async def drop() -> None:
            self.desk._cancel_claim_timer(room)
            await registry.remove(call_id)

        self.addAsyncCleanup(drop)
        return room

    async def test_a_claim_ending_on_one_call_leaves_the_officer_on_the_other(self):
        other = await self.waiting_room()
        await self.desk.claim(self.call_id, self.officer)
        await self.desk.claim(other.call_id, self.officer)

        await self.desk.release(self.call_id, self.officer)

        self.assertEqual(self.status(), ("on_call", other.call_id))

    async def test_giving_back_a_second_claim_keeps_an_officer_on_the_call_they_are_on(self):
        on_call = f"call_cp_{uuid.uuid4().hex[:8]}"
        create_call(on_call, conversation_id=f"conv_{on_call}", status="bridged")
        update_call(on_call, officer_id=self.officer)
        self.presence.set_on_call(self.officer, on_call)

        await self.desk.claim(self.call_id, self.officer)
        await self.desk.release(self.call_id, self.officer)

        self.assertEqual(self.status(), ("on_call", on_call))
        self.assertEqual(self.presence.get_presence(self.officer)["previous_status"], "busy")

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

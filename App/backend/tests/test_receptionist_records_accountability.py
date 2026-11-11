"""Records and accountability: what a call leaves behind is true, scoped and owned.

Regressions from phase 2 of the November 2026 call-receptionist review
(docs/plans/receptionist-assurance-2026-11-11.md, ADR-0040).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from app import database as db
from app.receptionist import desk, ws
from app.receptionist.hub import hub
from app.receptionist.state import registry
from app.receptionist.store import create_call, create_turn, get_call, init_receptionist_schema


class FakeCallerSocket:
    """The caller's browser: records what the server sends it."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    async def send_bytes(self, _data: bytes) -> None:
        return None


class FakeStaffSocket:
    """A staff browser socket: scripted inbound frames, recorded outbound ones."""

    def __init__(self, query: dict | None = None, inbound: list[str] | None = None) -> None:
        self._inbound = list(inbound or [json.dumps({"type": "authenticate", "access_token": "t"})])
        self.sent: list = []
        self.closed: int | None = None
        self.headers = {"origin": "http://localhost:3300"}
        self.query_params = query or {}
        self.client = SimpleNamespace(host="127.0.0.1")

    async def accept(self) -> None:
        return None

    async def receive_text(self) -> str:
        if self._inbound:
            return self._inbound.pop(0)
        await asyncio.sleep(3600)
        return ""

    async def receive(self) -> dict:
        return {"type": "websocket.receive", "text": await self.receive_text()}

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        if self.closed is None:
            self.closed = code

    def frames(self, kind: str) -> list[dict]:
        return [m for m in self.sent if isinstance(m, dict) and m.get("type") == kind]


class LiveCallCase(unittest.IsolatedAsyncioTestCase):
    """A call waiting in the officers' queue, on the real store."""

    tenant_id = "default"

    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_rec_{uuid.uuid4().hex[:8]}"
        create_call(
            self.call_id, conversation_id=f"conv_{self.call_id}", status="transferring", tenant_id=self.tenant_id
        )
        self.caller = FakeCallerSocket()
        self.room = await registry.create(
            self.call_id, f"conv_{self.call_id}", tenant_id=self.tenant_id, caller_ws=self.caller
        )
        self.room.state.mode = "transferring"
        self.lobby = patch.object(hub, "publish_lobby").start()
        self.addCleanup(patch.stopall)

    async def asyncTearDown(self) -> None:
        desk._cancel_claim_timer(self.room)
        desk._cancel_reconnect_timer(self.room)
        desk._cancel_reservation(self.room)
        await registry.remove(self.call_id)

    async def bridge(self, officer: str = "okello") -> None:
        await desk.claim(self.call_id, officer)
        await desk.bridge(self.room, None, officer, speech_model=None)

    def staff(self, user: str = "okello", role: str = "ura_staff", claims: dict | None = None):
        """Patches that let a fake socket through the staff endpoints as *user*."""
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(ws, "_voice_receptionist_enabled", return_value=True))
        stack.enter_context(
            patch.object(ws, "_resolve_ws_principal", return_value=(user, self.tenant_id, role, claims or {}))
        )
        stack.enter_context(patch.object(ws.livekit, "enabled", return_value=False))
        return stack


class ListenSocketTests(LiveCallCase):
    """A supervisor listening to a call is let go when the call ends."""

    async def listen(self) -> tuple[asyncio.Task, FakeStaffSocket]:
        socket = FakeStaffSocket({"listen": "true"})
        task = asyncio.create_task(ws.officer_audio_endpoint(socket, self.call_id))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if self.room.listeners:
                break
        return task, socket

    async def test_the_listener_is_released_when_the_call_ends(self):
        await self.bridge()
        with self.staff("auditor1", "ura_auditor"):
            task, _socket = await self.listen()
            self.assertEqual(len(self.room.listeners), 1)

            await self.room.end("caller_hangup")
            done, _pending = await asyncio.wait({task}, timeout=2.0)

        self.assertTrue(done, "the listen socket is still blocked after the call ended")
        self.assertEqual(self.room.listeners, [])

    async def test_cancelling_a_listener_cancels_it(self):
        await self.bridge()
        with self.staff("auditor1", "ura_auditor"):
            task, _socket = await self.listen()
            task.cancel()
            await asyncio.wait({task}, timeout=2.0)

        # A `return` inside `finally` turned the cancellation into a normal
        # return, so a shutdown could not tell the handler had been stopped.
        self.assertTrue(task.cancelled())
        self.assertEqual(self.room.listeners, [])

    async def test_a_listener_still_hears_the_call(self):
        await self.bridge()
        with self.staff("auditor1", "ura_auditor"):
            task, socket = await self.listen()
            self.room.broadcast_audio(b"\x01\x02" * 80)
            await asyncio.sleep(0.05)
            await self.room.end("caller_hangup")
            await asyncio.wait({task}, timeout=2.0)

        self.assertIn(b"\x01\x02" * 80, socket.sent)


class LiveTranscriptTests(LiveCallCase):
    """An officer watching a call sees every turn, including one written as they open it."""

    async def test_a_turn_written_while_the_transcript_opens_is_not_lost(self):
        socket = FakeStaffSocket()
        real_snapshot = ws.get_call_with_turns

        def snapshot_then_a_turn(call_id, tenant_id=None):
            snapshot = real_snapshot(call_id, tenant_id=tenant_id)
            # The caller speaks after the snapshot was read and before the
            # socket would have subscribed.
            self.room.state.turn_seq += 1
            turn = create_turn(
                call_id=call_id, seq=self.room.state.turn_seq, speaker="caller", kind="utterance",
                text="I need help with my VAT return",
            )
            hub.publish_call(call_id, "turn", turn)
            return snapshot

        with self.staff(), patch.object(ws, "get_call_with_turns", side_effect=snapshot_then_a_turn):
            task = asyncio.create_task(ws.staff_calls_stream_endpoint(socket, call_id=self.call_id))
            await asyncio.sleep(0.2)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        shown = [t["text"] for m in socket.frames("snapshot") for t in m["turns"]]
        shown += [m["data"]["text"] for m in socket.frames("turn")]
        self.assertEqual(shown, ["I need help with my VAT return"])

    async def test_a_turn_already_in_the_snapshot_is_not_shown_twice(self):
        socket = FakeStaffSocket()
        real_snapshot = ws.get_call_with_turns

        def a_turn_then_snapshot(call_id, tenant_id=None):
            # Written, and published, just before the snapshot is read: it is
            # in the snapshot and also waiting in the subscription's queue.
            self.room.state.turn_seq += 1
            turn = create_turn(
                call_id=call_id, seq=self.room.state.turn_seq, speaker="caller", kind="utterance",
                text="Where do I file my PAYE return?",
            )
            hub.publish_call(call_id, "turn", turn)
            return real_snapshot(call_id, tenant_id=tenant_id)

        with self.staff(), patch.object(ws, "get_call_with_turns", side_effect=a_turn_then_snapshot):
            task = asyncio.create_task(ws.staff_calls_stream_endpoint(socket, call_id=self.call_id))
            await asyncio.sleep(0.2)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        shown = [t["text"] for m in socket.frames("snapshot") for t in m["turns"]]
        shown += [m["data"]["text"] for m in socket.frames("turn")]
        self.assertEqual(shown, ["Where do I file my PAYE return?"])


class WrapUpOwnershipTests(LiveCallCase):
    """A call's wrap-up is written by the officers who handled it, once they are off it."""

    async def end_call(self) -> None:
        await self.room.end("caller_hangup")

    async def refused(self, officer: str, **kwargs) -> desk.DeskError:
        with self.assertRaises(desk.DeskError) as refused:
            await desk.wrapup(self.call_id, officer, outcome="resolved", note="Closed", **kwargs)
        return refused.exception

    async def test_an_officer_who_was_never_on_the_call_cannot_wrap_it_up(self):
        await self.bridge("okello")
        await self.end_call()

        error = await self.refused("mallory")

        self.assertEqual(error.status, 403)
        self.assertEqual(get_call(self.call_id)["outcome"], "")

    async def test_the_officer_on_a_call_ends_it_before_wrapping_up(self):
        await self.bridge("okello")

        error = await self.refused("okello")

        self.assertEqual(error.status, 409)
        self.assertEqual((self.room.state.mode, get_call(self.call_id)["outcome"]), ("bridged", ""))

    async def test_a_call_still_waiting_for_its_first_officer_is_nobodys_to_wrap_up(self):
        error = await self.refused("okello")

        self.assertEqual(error.status, 403)
        self.assertIsNone(get_call(self.call_id)["wrapup_at"])

    async def test_a_refused_wrap_up_does_not_touch_the_ticket(self):
        ticket = db.create_ticket("taxpayer-1", "Need my refund status")
        from app.receptionist.store import update_call

        update_call(self.call_id, ticket_id=ticket["id"])
        await self.bridge("okello")
        await self.end_call()

        await self.refused("mallory", ticket_action="resolve")

        self.assertNotEqual(db.get_ticket(ticket["id"])["status"], "resolved")

    async def test_the_officer_who_handled_it_wraps_it_up_after_it_ends(self):
        await self.bridge("okello")
        await self.end_call()

        result = await desk.wrapup(self.call_id, "okello", outcome="resolved", note="Explained the VAT return")

        self.assertTrue(result["wrapped_up"])
        row = get_call(self.call_id)
        self.assertEqual((row["outcome"], row["wrapup_note"]), ("resolved", "Explained the VAT return"))

    async def test_an_officer_who_passed_the_call_on_wraps_up_while_the_caller_waits(self):
        await self.bridge("okello")
        await desk.transfer(self.call_id, "okello", team="disputes")

        result = await desk.wrapup(self.call_id, "okello", outcome="referred", note="Passed to disputes")

        self.assertTrue(result["wrapped_up"])

    async def both_officers_handle_it(self) -> None:
        await self.bridge("okello")
        await desk.transfer(self.call_id, "okello", team="disputes")
        await self.bridge("nakato")
        await self.end_call()

    async def test_each_officer_on_a_transferred_call_keeps_their_own_note(self):
        await self.both_officers_handle_it()

        await desk.wrapup(self.call_id, "okello", outcome="referred", note="Passed to disputes", officer_name="Officer Okello")
        await desk.wrapup(self.call_id, "nakato", outcome="resolved", note="Objection lodged", officer_name="Officer Nakato")

        row = get_call(self.call_id)
        notes = {w["officer_id"]: (w["outcome"], w["note"], w["officer_name"]) for w in row["wrapups"]}
        self.assertEqual(notes, {
            "okello": ("referred", "Passed to disputes", "Officer Okello"),
            "nakato": ("resolved", "Objection lodged", "Officer Nakato"),
        })

    async def test_the_calls_outcome_is_the_last_officers_whoever_saves_first(self):
        await self.both_officers_handle_it()

        await desk.wrapup(self.call_id, "nakato", outcome="resolved", note="Objection lodged")
        await desk.wrapup(self.call_id, "okello", outcome="referred", note="Passed to disputes")

        row = get_call(self.call_id)
        self.assertEqual((row["outcome"], row["wrapup_note"]), ("resolved", "Objection lodged"))

    async def test_saving_again_replaces_that_officers_note_and_no_one_elses(self):
        await self.both_officers_handle_it()
        await desk.wrapup(self.call_id, "okello", outcome="referred", note="Passed to disputes")
        await desk.wrapup(self.call_id, "nakato", outcome="resolved", note="Objection lodged")

        await desk.wrapup(self.call_id, "nakato", outcome="follow_up", note="Objection lodged; documents owed")

        row = get_call(self.call_id)
        self.assertEqual(
            sorted((w["officer_id"], w["note"]) for w in row["wrapups"]),
            [("nakato", "Objection lodged; documents owed"), ("okello", "Passed to disputes")],
        )
        self.assertEqual(row["outcome"], "follow_up")

    async def test_a_call_no_officer_reached_can_be_wrapped_up_by_any_officer(self):
        await self.end_call()

        result = await desk.wrapup(self.call_id, "okello", outcome="callback", note="Caller left the queue; will ring back")

        self.assertTrue(result["wrapped_up"])

    async def test_a_ticket_made_at_wrap_up_is_made_once_when_the_save_is_retried(self):
        from app.receptionist.store import update_call as real_update

        await self.bridge("okello")
        await self.end_call()
        calls = {"n": 0}

        def fail_the_wrap_up_once(call_id, **fields):
            # The ticket is linked first; the wrap-up itself then fails once.
            if "wrapup_at" in fields:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("database is locked")
            return real_update(call_id, **fields)

        with patch("app.flags.flags.is_enabled", side_effect=lambda name, **_kw: name == "ticket_queue"):
            with patch.object(desk, "update_call", side_effect=fail_the_wrap_up_once):
                with self.assertRaises(desk.DeskError) as failed:
                    await desk.wrapup(self.call_id, "okello", outcome="follow_up", note="Needs a letter", ticket_action="create")
                self.assertEqual(failed.exception.status, 503)
                await desk.wrapup(self.call_id, "okello", outcome="follow_up", note="Needs a letter", ticket_action="create")

        row = get_call(self.call_id)
        self.assertTrue(row["ticket_id"])
        made = [t for t in db.list_tickets(limit=200) if t.get("conversation_id") == f"conv_{self.call_id}"]
        self.assertEqual(len(made), 1)

    async def test_a_note_longer_than_the_limit_is_refused(self):
        await self.bridge("okello")
        await self.end_call()

        with self.assertRaises(desk.DeskError) as refused:
            await desk.wrapup(self.call_id, "okello", outcome="resolved", note="x" * 4001)

        self.assertEqual(refused.exception.status, 400)


class TransferInputTests(LiveCallCase):
    """What an officer's console may send when passing a call on."""

    async def test_a_call_is_not_passed_to_a_team_that_does_not_exist(self):
        await self.bridge("okello")

        with self.assertRaises(desk.DeskError) as refused:
            await desk.transfer(self.call_id, "okello", team="'; DROP TABLE voice_calls; --")

        self.assertEqual(refused.exception.status, 400)
        self.assertEqual((self.room.state.mode, self.room.state.officer_id), ("bridged", "okello"))

    async def test_a_transfer_note_longer_than_the_limit_is_refused(self):
        await self.bridge("okello")

        with self.assertRaises(desk.DeskError) as refused:
            await desk.transfer(self.call_id, "okello", team="", note="x" * 4001)

        self.assertEqual(refused.exception.status, 400)
        self.assertEqual(self.room.state.mode, "bridged")

    async def test_a_known_team_is_accepted(self):
        from app.escalation_notify import known_teams

        await self.bridge("okello")

        result = await desk.transfer(self.call_id, "okello", team=known_teams()[0])

        self.assertTrue(result["transferred"])


class CallbackRecordTests(LiveCallCase):
    """An owed callback is completed once, by one officer."""

    async def owe_a_callback(self) -> None:
        from app.receptionist.store import update_call

        await self.room.end("caller_hangup")
        update_call(self.call_id, needs_callback=1, callback_reason="caller_left_waiting")

    async def test_the_first_officer_to_ring_back_is_the_one_recorded(self):
        await self.owe_a_callback()

        await desk.callback_done(self.call_id, "okello", note="Rang back, explained the refund")
        with self.assertRaises(desk.DeskError) as second:
            await desk.callback_done(self.call_id, "nakato", note="Also rang")

        self.assertEqual(second.exception.status, 409)
        row = get_call(self.call_id)
        self.assertEqual(row["callback_done_by"], "okello")
        self.assertNotIn("Also rang", row["officer_note"])

    async def test_a_call_that_owes_no_callback_is_not_given_one(self):
        await self.room.end("caller_hangup")

        with self.assertRaises(desk.DeskError) as refused:
            await desk.callback_done(self.call_id, "okello")

        self.assertEqual(refused.exception.status, 409)
        self.assertIsNone(get_call(self.call_id)["callback_done_at"])


class PresenceTenancyTests(unittest.IsolatedAsyncioTestCase):
    """An officer's presence is seen, counted and announced inside their tenant only."""

    async def asyncSetUp(self) -> None:
        from app.receptionist import presence

        db.init_db()
        init_receptionist_schema()
        self.presence = presence
        tag = uuid.uuid4().hex[:6]
        self.kla, self.gulu = f"okello-{tag}", f"akello-{tag}"
        self.tenant_a, self.tenant_b = f"tenant-kla-{tag}", f"tenant-gulu-{tag}"
        presence.upsert_presence(self.kla, tenant_id=self.tenant_a, display_name="Officer Okello", status="available")
        presence.upsert_presence(self.gulu, tenant_id=self.tenant_b, display_name="Officer Akello", status="available")
        self.addCleanup(presence.upsert_presence, self.kla, status="offline")
        self.addCleanup(presence.upsert_presence, self.gulu, status="offline")

    def ids(self, officers: list[dict]) -> set[str]:
        return {o["user_id"] for o in officers} & {self.kla, self.gulu}

    async def test_the_board_shows_only_the_tenants_own_officers(self):
        board = self.presence.get_presence_board(tenant_id=self.tenant_a)

        self.assertEqual(self.ids(board["officers"]), {self.kla})

    async def test_with_tenancy_off_the_board_shows_everyone(self):
        board = self.presence.get_presence_board()

        self.assertEqual(self.ids(board["officers"]), {self.kla, self.gulu})

    async def test_another_tenants_officer_does_not_make_a_call_look_staffed(self):
        self.presence.upsert_presence(self.kla, status="offline")

        self.assertEqual(self.presence.count_active_officers(tenant_id=self.tenant_a), 0)
        self.assertEqual(self.presence.count_active_officers(tenant_id=self.tenant_b), 1)

    async def test_a_presence_change_is_announced_to_the_tenants_own_consoles(self):
        loop = asyncio.get_running_loop()
        mine = hub.subscribe_lobby(loop, tenant_id=self.tenant_a)
        theirs = hub.subscribe_lobby(loop, tenant_id=self.tenant_b)
        self.addCleanup(hub.unsubscribe_lobby, mine)
        self.addCleanup(hub.unsubscribe_lobby, theirs)

        self.presence.upsert_presence(self.kla, status="away")
        await asyncio.sleep(0.05)

        self.assertEqual([e["data"]["user_id"] for e in _drain(mine)], [self.kla])
        self.assertEqual(_drain(theirs), [])

    async def test_an_event_that_names_no_tenant_reaches_no_tenants_console(self):
        loop = asyncio.get_running_loop()
        scoped = hub.subscribe_lobby(loop, tenant_id=self.tenant_a)
        unscoped = hub.subscribe_lobby(loop)
        self.addCleanup(hub.unsubscribe_lobby, scoped)
        self.addCleanup(hub.unsubscribe_lobby, unscoped)

        hub.publish_lobby("notification.arrived", {"user_id": "taxpayer-9", "deadline": "VAT return"})
        await asyncio.sleep(0.05)

        self.assertEqual(_drain(scoped), [])
        self.assertEqual([e["type"] for e in _drain(unscoped)], ["notification.arrived"])


def _drain(queue: asyncio.Queue) -> list[dict]:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


class PresenceRouteTests(LiveCallCase):
    """What an officer's console may say about the officer."""

    def client(self, email: str = "okello@ura.go.ug"):
        from starlette.testclient import TestClient

        from app.auth.jwt_auth import make_dev_token
        from app.main import app

        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {make_dev_token(email, role='ura_staff')}"})
        return client

    async def test_values_the_desk_does_not_know_are_refused(self):
        client = self.client()

        for body in ({"teams": 5}, {"status": "supervisor"}, {"languages": ["xx"]}, {"teams": ["not_a_team"]},
                     {"display_name": "x" * 200}):
            with self.subTest(body=body):
                self.assertEqual(client.put("/v1/admin/officers/me/presence", json=body).status_code, 422)

    async def test_a_plain_availability_update_is_accepted(self):
        response = self.client().put("/v1/admin/officers/me/presence", json={"status": "away", "languages": ["en", "lg"]})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "away")

    async def test_a_heartbeat_cannot_mark_an_officer_free_while_they_are_on_a_call(self):
        await self.bridge("okello@ura.go.ug")

        response = self.client().put("/v1/admin/officers/me/presence", json={"status": "available"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(desk.presence.get_presence("okello@ura.go.ug")["status"], "on_call")

    async def test_a_call_is_not_passed_to_an_officer_of_another_tenant(self):
        await self.bridge("okello")
        desk.presence.upsert_presence("akello", tenant_id="tenant-gulu", display_name="Officer Akello", status="available")
        self.addCleanup(desk.presence.upsert_presence, "akello", status="offline")

        with patch.object(desk, "tenant_enabled", return_value=True):
            with self.assertRaises(desk.DeskError) as refused:
                await desk.transfer(self.call_id, "okello", target_officer_id="akello")

        self.assertEqual(refused.exception.status, 409)
        self.assertEqual(self.room.state.mode, "bridged")


def only(*enabled: str):
    """A flag reader with just *enabled* on."""
    return lambda name, **_kw: name in enabled


class BrainCase(unittest.IsolatedAsyncioTestCase):
    """One call in the AI's hands, with a chat model that answers a TIN question."""

    PENDING = {
        "session_id": "from-generate", "user_id": "taxpayer_1", "tenant_id": "default",
        "tool_calls": [], "tool_iterations": 0, "agent_route": "rag", "usage": {"total_tokens": 42},
    }

    async def asyncSetUp(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from app.receptionist.brain import UraReceptionistBrain
        from app.receptionist.state import CallRoom, CallState

        staffed = patch("app.receptionist.transfer.is_staff_available", return_value=True)
        staffed.start()
        self.addCleanup(staffed.stop)
        flags_on = patch("app.receptionist.brain.flags.is_enabled", side_effect=only("ticket_queue"))
        flags_on.start()
        self.addCleanup(flags_on.stop)
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_aud_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}")
        self.state = CallState(
            call_id=self.call_id, conversation_id=f"conv_{self.call_id}", user_id="taxpayer_1", mode="ai"
        )
        self.room = CallRoom(call_id=self.call_id, state=self.state)
        self.chat_model = MagicMock()
        self.chat_model.generate.return_value = {
            "reply": "You can register for a TIN using the URA portal.",
            "sources": ["URA Portal Guide"],
            "faithfulness_score": 0.95,
            "retrieval_mode": "hybrid",
            "_pending_audit": dict(self.PENDING),
        }
        self.chat_model._build_handoff_packet.return_value = {"priority": "normal"}
        self.chat_model._maybe_create_ticket.return_value = "TICK-TEST-123"
        self.brain = UraReceptionistBrain(room=self.room, chat_model=self.chat_model)
        self.brain.push_frame = AsyncMock()

    async def asyncTearDown(self) -> None:
        for task in (self.state.transfer_timer_task, self.state.review_timer_task):
            if task is not None and not task.done():
                task.cancel()

    def voiced(self) -> list[str]:
        return [
            getattr(c.args[0], "text", "")
            for c in self.brain.push_frame.call_args_list
            if c.args[0].__class__.__name__ == "LLMTextFrame"
        ]

    async def hear(self, text: str) -> None:
        from app.receptionist.brain import LLMContextFrame

        await self.brain.process_frame(LLMContextFrame(context=text))

    def rows(self) -> list[dict]:
        """The audit rows written for this call's turns, as record_turn_audit was asked to write them."""
        return [dict(c.kwargs) for c in self.chat_model.record_turn_audit.call_args_list]


class AuditTrailTests(BrainCase):
    """Every turn the AI handles on a call writes one audit row, of what the caller was told."""

    async def test_a_crisis_turn_is_recorded(self):
        from app.receptionist.phrases import phrase

        await self.hear("I want to kill myself")

        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["channel"], rows[0]["message"]), ("call", "I want to kill myself"))
        self.assertEqual(rows[0]["result"]["retrieval_mode"], "crisis_support")
        self.assertIn(phrase("crisis_support", "en"), rows[0]["result"]["reply"])
        self.assertEqual((rows[0]["session_id"], rows[0]["user_id"], rows[0]["tenant_id"]),
                         (self.call_id, "taxpayer_1", "default"))

    async def test_a_request_for_an_officer_is_recorded_as_a_handoff(self):
        await self.hear("Officer please")

        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["result"]["escalation_required"])
        self.assertIn("connecting you to a URA officer", rows[0]["result"]["reply"])
        self.chat_model.generate.assert_not_called()

    async def test_an_answered_turn_is_recorded_once_with_what_the_caller_heard(self):
        await self.hear("How do I register for a TIN?")

        self.assertTrue(self.chat_model.generate.call_args.kwargs["defer_audit"])
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result"]["reply"], " ".join(self.voiced()))
        self.assertEqual((rows[0]["result"]["retrieval_mode"], rows[0]["agent_route"]), ("hybrid", "rag"))
        self.assertEqual(rows[0]["usage"], {"total_tokens": 42})
        self.assertEqual(rows[0]["session_id"], self.call_id)
        self.assertNotIn("_pending_audit", rows[0]["result"])
        self.assertNotIn("_disposition", rows[0]["result"])

    async def test_a_turn_that_timed_out_is_recorded_as_deadline_exceeded(self):
        import time as _time

        self.chat_model.generate.side_effect = lambda **_kw: _time.sleep(0.4) or {"reply": "late"}
        with (
            patch("app.receptionist.brain.GENERATION_TIMEOUT_S", 0.05),
            patch("app.receptionist.brain.get_filler_after_ms", return_value=10),
        ):
            await self.hear("How do I register for a TIN?")

        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result"]["_disposition"], "deadline_exceeded")
        self.assertNotIn("late", rows[0]["result"]["reply"])

    async def test_a_turn_that_failed_is_recorded_as_failed(self):
        self.chat_model.generate.side_effect = RuntimeError("retriever down")

        await self.hear("How do I register for a TIN?")

        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result"]["_disposition"], "failed")

    async def test_a_turn_the_caller_talked_over_is_recorded_as_cancelled(self):
        release = asyncio.Event()
        loop = asyncio.get_running_loop()

        def slow(**_kw):
            asyncio.run_coroutine_threadsafe(release.wait(), loop).result(timeout=5)
            return {"reply": "The VAT rate is 18 percent.", "_pending_audit": dict(self.PENDING)}

        self.chat_model.generate.side_effect = slow
        answering = asyncio.create_task(self.hear("What is the VAT rate?"))
        await asyncio.sleep(0.05)
        self.state.generation_id += 1  # the caller spoke over it
        release.set()
        await answering

        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result"]["_disposition"], "cancelled")
        self.assertEqual(rows[0]["result"]["reply"], "")

    async def test_a_luganda_turn_is_recorded_in_luganda_as_it_was_heard(self):
        self.state.locale = "lg"
        self.chat_model._maybe_handle_fast_paths.return_value = None
        luganda = "Osobola okwewandiisa ku mukutu ogwo leero."
        with (
            patch.object(self.brain, "_synthesize_luganda_reply", return_value=luganda),
            patch.object(self.brain, "_extract_english_tax_query", return_value="How do I register for a TIN?"),
        ):
            await self.hear("Nyinza ntya okwewandiisa okufuna TIN?")

        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result"]["reply_locale"], "lg")
        self.assertIn(luganda, rows[0]["result"]["reply"])
        self.assertNotIn("You can register", rows[0]["result"]["reply"])

    async def test_a_turn_while_an_officer_has_the_call_is_not_an_ai_turn(self):
        self.state.mode = "bridged"

        await self.hear("Thank you officer, I understand now")

        self.assertEqual(self.rows(), [])

    async def test_a_turn_the_ai_handled_itself_is_counted_once(self):
        with patch("app.receptionist.brain.record_chat_turn") as counted:
            await self.hear("I want to kill myself")

        self.assertEqual(counted.call_count, 1)
        self.assertEqual(counted.call_args.kwargs["channel"], "call")

    async def test_an_audit_write_that_fails_does_not_cost_the_caller_their_answer(self):
        self.chat_model.record_turn_audit.side_effect = RuntimeError("ledger down")

        await self.hear("How do I register for a TIN?")

        self.assertIn("portal", " ".join(self.voiced()))


class DeferredAuditTests(unittest.TestCase):
    """The real chat model hands its audit row back when a transport asks to write it itself."""

    @classmethod
    def setUpClass(cls) -> None:
        from app.service import ChatModel

        db.init_db()
        cls.model = ChatModel()

    def ask(self, **kwargs) -> tuple[dict, int]:
        with patch.object(self.model, "record_turn_audit") as written:
            result = self.model.generate(
                message="What is the standard VAT rate in Uganda?",
                session_id=f"sess_{uuid.uuid4().hex[:8]}",
                channel="call",
                **kwargs,
            )
        return result, written.call_count

    def test_a_deferred_turn_writes_no_row_and_carries_its_audit_fields(self):
        result, rows_written = self.ask(defer_audit=True)

        self.assertEqual(rows_written, 0)
        self.assertEqual(
            sorted(result["_pending_audit"]),
            ["agent_route", "session_id", "tenant_id", "tool_calls", "tool_iterations", "usage", "user_id"],
        )

    def test_every_other_turn_writes_its_row_as_before(self):
        result, rows_written = self.ask()

        self.assertEqual(rows_written, 1)
        self.assertNotIn("_pending_audit", result)


class LugandaCalculatorTests(BrainCase):
    """A calculator answer on a Luganda call is given in Luganda."""

    async def test_the_answer_is_put_into_luganda_off_the_event_loop(self):
        import threading

        loop_thread = threading.current_thread()
        seen: list[bool] = []

        def localize(text: str, locale: str) -> str:
            seen.append(threading.current_thread() is loop_thread)
            return "Omusolo gwa VAT guli shilingi 90,000 ku 500,000."

        self.state.locale = "lg"
        self.chat_model._maybe_handle_fast_paths.return_value = {
            "reply": "VAT calculation. Net amount: UGX 500,000. VAT at 18%: UGX 90,000.",
            "retrieval_mode": "calculator",
        }
        self.chat_model._localize_reply.side_effect = localize

        result = await self.brain._generate_luganda_answer("Omusolo gwa VAT ku 500,000 guba mmeka?")

        self.assertEqual(seen, [False])
        self.assertEqual(result["reply"], "Omusolo gwa VAT guli shilingi 90,000 ku 500,000.")
        self.assertEqual(result["locale"], "lg")

    async def test_english_is_served_when_it_cannot_be_put_into_luganda(self):
        self.state.locale = "lg"
        english = "VAT calculation. Net amount: UGX 500,000. VAT at 18%: UGX 90,000."
        self.chat_model._maybe_handle_fast_paths.return_value = {"reply": english, "retrieval_mode": "calculator"}
        self.chat_model._localize_reply.side_effect = RuntimeError("translation down")

        result = await self.brain._generate_luganda_answer("Omusolo gwa VAT ku 500,000 guba mmeka?")

        self.assertEqual(result["reply"], english)


class GenerationCapTests(BrainCase):
    """A burst of turns does not start a model run for each of them."""

    async def test_at_most_two_generations_run_at_once_and_the_last_question_is_answered(self):
        import threading
        import time as _time

        from app.receptionist.serializer import CallTextTurnFrame

        lock = threading.Lock()
        running = {"now": 0, "peak": 0, "started": 0}

        def generate(**kwargs):
            with lock:
                running["now"] += 1
                running["started"] += 1
                running["peak"] = max(running["peak"], running["now"])
            _time.sleep(0.15)
            with lock:
                running["now"] -= 1
            return {"reply": f"Answer to: {kwargs['message']}", "sources": ["Guide"]}

        self.chat_model.generate.side_effect = generate
        turns = []
        for n in range(1, 7):
            turns.append(asyncio.create_task(
                self.brain.process_frame(CallTextTurnFrame(text=f"Tell me about tax topic number {n}"))
            ))
            await asyncio.sleep(0.02)
        await asyncio.gather(*turns)

        self.assertLessEqual(running["peak"], 2)
        self.assertLessEqual(running["started"], 3)
        self.assertIn("topic number 6", " ".join(self.voiced()))


class CallSummaryTicketTests(unittest.TestCase):
    """The end-of-call summary is a record of the call, not an officer's action on its ticket."""

    def setUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_sum_{uuid.uuid4().hex[:8]}"
        self.ticket = db.create_ticket("taxpayer-7", "I was charged a penalty I do not owe")
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}", status="ended")
        from app.receptionist.store import update_call

        update_call(self.call_id, ticket_id=self.ticket["id"], transferred=1, needs_callback=1)
        create_turn(self.call_id, 1, "caller", "utterance", "I was charged a penalty I do not owe")
        create_turn(self.call_id, 2, "assistant", "handoff", "I'm connecting you to a URA officer, please hold.")

    def summarise(self) -> dict:
        from app.receptionist import summary

        with patch.object(summary, "_summarise_local", return_value=None):
            return summary.generate_call_summary(self.call_id)

    def test_a_ticket_no_officer_has_touched_is_still_awaiting_its_first_response(self):
        self.summarise()

        self.assertFalse(db.get_ticket(self.ticket["id"]).get("first_response_at"))

    def test_an_officers_note_on_the_ticket_is_not_replaced(self):
        db.update_ticket(self.ticket["id"], staff_note="OFFICER NOTE: promised a callback Friday 10am")

        self.summarise()

        self.assertEqual(db.get_ticket(self.ticket["id"])["staff_note"], "OFFICER NOTE: promised a callback Friday 10am")

    def test_the_summary_is_still_kept_on_the_call(self):
        self.summarise()

        self.assertEqual(get_call(self.call_id)["summary"]["resolution"], "transferred")


class FallbackSummaryTests(unittest.TestCase):
    """Without a model, a call is only said to be answered when a tax answer was given."""

    def resolution(self, turns: list[dict]) -> str:
        from app.receptionist.summary import _fallback_summary

        return _fallback_summary(turns, {"status": "ended", "transferred": 0, "end_reason": "caller_hangup"})["resolution"]

    def test_a_crisis_only_call_is_not_called_answered(self):
        from app.receptionist.phrases import phrase

        for locale in ("en", "lg", "sw", "nyn", "ach"):
            with self.subTest(locale=locale):
                turns = [
                    {"speaker": "caller", "kind": "utterance", "text": "I want to kill myself"},
                    {"speaker": "assistant", "kind": "answer",
                     "text": f"{phrase('crisis_support', locale)} {phrase('crisis_offer', locale)}"},
                ]
                self.assertNotEqual(self.resolution(turns), "answered")

    def test_a_call_with_a_tax_answer_is_answered(self):
        turns = [
            {"speaker": "caller", "kind": "utterance", "text": "What is the VAT rate?"},
            {"speaker": "assistant", "kind": "answer", "text": "The standard VAT rate is 18 percent."},
        ]

        self.assertEqual(self.resolution(turns), "answered")


class LugandaSynthesisTests(BrainCase):
    """The Luganda the caller hears carries the checked answer's figures and no others."""

    async def synthesised(self, luganda: str) -> dict:
        self.state.locale = "lg"
        self.chat_model._maybe_handle_fast_paths.return_value = None
        self.chat_model.generate.return_value = {"reply": "The VAT rate is 18%.", "sources": ["VAT Act"]}
        self.chat_model._localize_reply.side_effect = lambda text, _locale: "Omusolo gwa VAT guli 18%."
        with (
            patch.object(self.brain, "_synthesize_luganda_reply", return_value=luganda),
            patch.object(self.brain, "_extract_english_tax_query", return_value="What is the VAT rate?"),
        ):
            return await self.brain._generate_luganda_answer("Omusolo gwa VAT guli mmeka?")

    async def test_a_synthesis_that_adds_a_figure_is_replaced_by_the_guarded_translation(self):
        result = await self.synthesised("Omusolo gwa VAT guli 18%. Waliwo n'ekibonerezo kya 2% buli mwezi ku UGX 200,000.")

        self.assertEqual(result["reply"], "Omusolo gwa VAT guli 18%.")

    async def test_a_synthesis_with_the_same_figures_is_kept(self):
        result = await self.synthesised("Omusolo gwa VAT mu Uganda guli 18%.")

        self.assertEqual(result["reply"], "Omusolo gwa VAT mu Uganda guli 18%.")


class StaffSessionTests(LiveCallCase):
    """A staff socket lasts as long as the session that opened it, and no longer."""

    async def asyncSetUp(self) -> None:
        from app import ws_concurrency

        await super().asyncSetUp()
        ws_concurrency.reset()
        self.addCleanup(ws_concurrency.reset)

    @staticmethod
    def expiring(seconds: float) -> dict:
        import time as _time

        return {"exp": _time.time() + seconds}

    async def test_a_listener_is_cut_off_when_their_session_expires(self):
        await self.bridge()
        socket = FakeStaffSocket({"listen": "true"})
        with self.staff("auditor1", "ura_auditor", claims=self.expiring(0.3)):
            task = asyncio.create_task(ws.officer_audio_endpoint(socket, self.call_id))
            done, _pending = await asyncio.wait({task}, timeout=3.0)

        self.assertTrue(done, "the listen socket outlived its session")
        self.assertEqual(socket.closed, 4401)
        self.assertEqual(self.room.listeners, [])
        self.assertEqual(self.room.state.mode, "bridged")  # the call itself goes on

    async def test_an_officers_audio_socket_closes_when_their_session_expires(self):
        await desk.claim(self.call_id, "okello")
        socket = FakeStaffSocket()
        with self.staff("okello", claims=self.expiring(0.3)):
            task = asyncio.create_task(ws.officer_audio_endpoint(socket, self.call_id))
            done, _pending = await asyncio.wait({task}, timeout=3.0)

        self.assertTrue(done, "the officer's audio socket outlived its session")
        self.assertEqual(socket.closed, 4401)
        # The caller is not dropped: the officer has the reconnect grace to sign in again.
        self.assertEqual((self.room.state.mode, self.room.state.reconnecting_officer), ("transferring", "okello"))

    async def test_a_session_with_time_left_keeps_its_socket(self):
        await self.bridge()
        socket = FakeStaffSocket({"listen": "true"})
        with self.staff("auditor1", "ura_auditor", claims=self.expiring(600)):
            task = asyncio.create_task(ws.officer_audio_endpoint(socket, self.call_id))
            await asyncio.sleep(0.7)
            still_listening = not task.done()
            await self.room.end("caller_hangup")
            await asyncio.wait({task}, timeout=2.0)

        self.assertTrue(still_listening)
        self.assertNotEqual(socket.closed, 4401)


class StaffAdmissionTests(LiveCallCase):
    """Staff sockets are admitted under a cap, like callers are."""

    async def asyncSetUp(self) -> None:
        from app import ws_concurrency

        await super().asyncSetUp()
        self.slots = ws_concurrency
        ws_concurrency.reset()
        self.addCleanup(ws_concurrency.reset)

    async def open_lobby(self) -> tuple[asyncio.Task, FakeStaffSocket]:
        socket = FakeStaffSocket()
        task = asyncio.create_task(ws.staff_calls_stream_endpoint(socket, call_id=None))
        await asyncio.sleep(0.05)
        return task, socket

    async def test_one_officer_cannot_hold_streams_without_limit(self):
        with self.staff("okello"), patch.object(ws, "STAFF_STREAMS_PER_USER", 2):
            first = await self.open_lobby()
            second = await self.open_lobby()
            third_task, third = await self.open_lobby()
            await asyncio.wait({third_task}, timeout=1.0)

            self.assertEqual(third.closed, 1013)
            self.assertIsNone(first[1].closed)
            self.assertIsNone(second[1].closed)
            for task, _socket in (first, second):
                task.cancel()
            await asyncio.gather(first[0], second[0], return_exceptions=True)

        self.assertEqual(self.slots.active("call_staff_stream"), 0)

    async def test_a_refused_sign_in_gives_its_slot_back(self):
        socket = FakeStaffSocket(inbound=[json.dumps({"type": "not-authenticate"})])
        with patch.object(ws, "_voice_receptionist_enabled", return_value=True):
            await ws.staff_calls_stream_endpoint(socket, call_id=None)

        self.assertEqual(socket.closed, 4401)
        self.assertEqual(self.slots.active("call_staff_stream"), 0)

    async def test_a_call_has_a_limited_number_of_listeners(self):
        await self.bridge()
        tasks = []
        with patch.object(ws, "LISTENERS_PER_CALL", 2):
            for n in range(3):
                socket = FakeStaffSocket({"listen": "true"})
                with self.staff(f"auditor{n}", "ura_auditor"):
                    tasks.append((asyncio.create_task(ws.officer_audio_endpoint(socket, self.call_id)), socket))
                    await asyncio.sleep(0.05)
            await asyncio.wait({tasks[2][0]}, timeout=1.0)

            self.assertEqual(tasks[2][1].closed, 1013)
            self.assertEqual(len(self.room.listeners), 2)
            await self.room.end("caller_hangup")
            await asyncio.wait({t for t, _s in tasks}, timeout=2.0)

        self.assertEqual(self.slots.active("call_staff_audio"), 0)


class OfficerTranscriptionTests(unittest.IsolatedAsyncioTestCase):
    """An officer's speech is transcribed by one worker with a bounded backlog."""

    def leg(self, transcribe):
        from app.receptionist.officer import OfficerLeg
        from app.receptionist.state import CallRoom, CallState

        room = CallRoom("call_stt", CallState(call_id="call_stt", conversation_id="conv_stt", mode="bridged"))
        speech = SimpleNamespace(transcribe=transcribe)
        return OfficerLeg(ws=FakeStaffSocket(), officer_id="okello", officer_name="Officer Okello",
                          room=room, speech_model=speech)

    async def test_one_utterance_is_transcribed_at_a_time_and_the_backlog_is_bounded(self):
        import threading
        import time as _time

        lock = threading.Lock()
        seen = {"now": 0, "peak": 0, "calls": 0}

        def transcribe(*_args, **_kwargs):
            with lock:
                seen["now"] += 1
                seen["calls"] += 1
                seen["peak"] = max(seen["peak"], seen["now"])
            _time.sleep(0.05)
            with lock:
                seen["now"] -= 1
            return SimpleNamespace(text="", latency_s=0.05)

        leg = self.leg(transcribe)
        for _ in range(20):
            leg.queue_transcription(b"\x00\x01" * 1600)
        await asyncio.sleep(0.6)
        await leg.close()

        self.assertEqual(seen["peak"], 1)
        self.assertLessEqual(seen["calls"], leg.TRANSCRIPTION_BACKLOG + 1)

    async def test_closing_the_leg_stops_its_transcription(self):
        import time as _time

        leg = self.leg(lambda *_a, **_k: _time.sleep(0.05) or SimpleNamespace(text="", latency_s=0.05))
        leg.queue_transcription(b"\x00\x01" * 1600)
        await asyncio.sleep(0.01)

        await leg.close()
        await asyncio.sleep(0.1)

        self.assertTrue(leg._transcriber is None or leg._transcriber.done())


class LanguageStateTests(BrainCase):
    """The call's language has one owner: what the brain detects, the language policy knows."""

    def attach_router(self):
        from unittest.mock import AsyncMock, MagicMock

        from app.receptionist.language import LanguagePolicy, PolicyConfig
        from app.receptionist.router import LanguageRouter

        outlet = MagicMock()
        outlet.send = AsyncMock()
        policy = LanguagePolicy(active="en", config=PolicyConfig())
        router = LanguageRouter(self.room, policy, MagicMock(), outlet=outlet, brain=self.brain)
        router.interrupt = AsyncMock(side_effect=router.switch_passed)
        # As multilingual.build_multilingual_pipeline wires them.
        self.brain.turn_claimed = router.claims_turn
        self.brain.on_language_change = policy.adopt
        return router

    async def type_turn(self, text: str) -> None:
        from app.receptionist.serializer import CallTextTurnFrame

        await self.brain.process_frame(CallTextTurnFrame(text=text))

    async def test_a_typed_turn_in_another_language_moves_the_policy_with_the_call(self):
        router = self.attach_router()

        await self.type_turn("Nsaba okumanya ku VAT yange")

        self.assertEqual(self.state.locale, "lg")
        self.assertEqual((router.policy.active, router.policy.locked), ("lg", True))

    async def test_the_on_screen_choice_then_moves_the_call_back(self):
        router = self.attach_router()
        await self.type_turn("Nsaba okumanya ku VAT yange")

        await router.on_override("en")
        for task in list(router._reasks):
            await task

        self.assertEqual(self.state.locale, "en")

    async def test_the_brain_applies_an_on_screen_choice_when_no_router_is_attached(self):
        from app.receptionist.phrases import phrase
        from app.receptionist.serializer import SetLanguageFrame

        await self.brain.process_frame(SetLanguageFrame(language="sw"))

        self.assertEqual((self.state.locale, self.state.language_source), ("sw", "override"))
        self.assertIn(phrase("switched", "sw"), self.voiced())

    async def test_with_a_router_the_on_screen_choice_is_left_to_it(self):
        from app.receptionist.serializer import SetLanguageFrame

        self.attach_router()
        frame = SetLanguageFrame(language="sw")

        await self.brain.process_frame(frame)

        self.assertEqual(self.state.locale, "en")
        self.assertIn(frame, [c.args[0] for c in self.brain.push_frame.call_args_list])


class RetentionPurgeTests(unittest.TestCase):
    """On Postgres, a missing voice table does not undo the purge of resolved tickets."""

    def run_cleanup(self, missing: tuple[str, ...]):
        from app import postgres

        log: list[str] = []

        class Cursor:
            rowcount = 3

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def execute(self, sql, _params=None):
                table = sql.split("FROM", 1)[-1].split("SET", 1)[0].split()[0] if "FROM" in sql else "workflow_sessions"
                if table in missing:
                    log.append(f"error:{table}")
                    raise RuntimeError(f'relation "{table}" does not exist')
                log.append(f"delete:{table}" if "DELETE" in sql else f"update:{table}")

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def cursor(self):
                return Cursor()

            def commit(self):
                log.append("commit")

            def rollback(self):
                log.append("rollback")

        pool = SimpleNamespace(connection=lambda: Connection())
        with (
            patch.object(postgres, "_get_pool", return_value=pool),
            patch("app.database.module_retention", return_value={}),
        ):
            deleted = postgres.cleanup_expired_data()
        return deleted, log

    def test_resolved_tickets_are_purged_when_the_voice_tables_do_not_exist(self):
        deleted, log = self.run_cleanup(missing=("voice_call_turns", "voice_calls"))

        self.assertEqual(deleted["tickets"], 3)
        purge = log.index("delete:tickets")
        self.assertEqual(log[purge + 1], "commit", "the ticket purge was not committed on its own")

    def test_the_voice_tables_are_purged_when_they_exist(self):
        deleted, log = self.run_cleanup(missing=())

        self.assertEqual((deleted["voice_call_turns"], deleted["voice_calls"]), (3, 3))
        self.assertNotIn("rollback", log)

    def test_a_ticket_purge_that_fails_does_not_stop_the_voice_purge(self):
        deleted, _log = self.run_cleanup(missing=("tickets",))

        self.assertEqual(deleted["tickets"], 0)
        self.assertEqual(deleted["voice_call_turns"], 3)


if __name__ == "__main__":
    unittest.main()

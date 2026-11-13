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


def signed_staff_token(user: str, role: str, *, expires_in: float, tenant: str = "default") -> str:
    """A token the development verifier accepts: really signed, with a real expiry."""
    import time as _time

    from app.auth.dependencies import _get_verifier
    from app.auth.jwt_auth import _b64url_encode, _hs256_sign

    verifier = _get_verifier()
    now = _time.time()
    claims = {
        "sub": user,
        "tenant_id": tenant,
        "iss": verifier.issuer or "ura-chatbot-dev",
        "aud": verifier.audience or "ura-chatbot",
        "iat": int(now),
        "nbf": int(now) - 5,
        "exp": now + expires_in,
        "realm_access": {"roles": [role]},
    }
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url_encode(json.dumps(claims).encode())
    return f"{header}.{payload}.{_hs256_sign(header, payload, verifier.dev_secret)}"


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
            patch.object(
                ws, "_staff_session", return_value=(user, self.tenant_id, role, (claims or {}).get("exp"), None)
            )
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
        from app.receptionist.store import merge_wrapup as real_merge

        await self.bridge("okello")
        await self.end_call()
        calls = {"n": 0}

        def fail_the_wrap_up_once(*args, **kwargs):
            # The ticket is linked first; the wrap-up itself then fails once.
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("database is locked")
            return real_merge(*args, **kwargs)

        with patch("app.flags.flags.is_enabled", side_effect=lambda name, **_kw: name == "ticket_queue"):
            with patch.object(desk, "merge_wrapup", side_effect=fail_the_wrap_up_once):
                with self.assertRaises(desk.DeskError) as failed:
                    await desk.wrapup(self.call_id, "okello", outcome="follow_up", note="Needs a letter", ticket_action="create")
                self.assertEqual(failed.exception.status, 503)
                # It says what did happen, rather than that nothing did.
                self.assertIn("ticket was updated", failed.exception.detail)
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

    async def test_the_expiry_comes_from_the_token_the_verifier_checked(self):
        # The doubles in this class hand the endpoint an expiry. What sign-in
        # really returned in that position was the token's consent purposes,
        # so no expiry was ever read and no staff socket ever expired. This
        # one signs a real token and patches nothing about sign-in.
        await self.bridge()
        token = signed_staff_token("auditor1", "ura_auditor", expires_in=1.0)
        socket = FakeStaffSocket(
            {"listen": "true"}, inbound=[json.dumps({"type": "authenticate", "access_token": token})]
        )
        with (
            patch.object(ws, "_voice_receptionist_enabled", return_value=True),
            patch.object(ws.livekit, "enabled", return_value=False),
        ):
            task = asyncio.create_task(ws.officer_audio_endpoint(socket, self.call_id))
            done, _pending = await asyncio.wait({task}, timeout=5.0)
            if not done:
                task.cancel()

        self.assertTrue(done, "a listen socket opened with a real token outlived that token")
        self.assertEqual(socket.closed, 4401)

    async def test_the_lobby_closes_when_a_real_token_expires(self):
        token = signed_staff_token("okello", "ura_staff", expires_in=1.0)
        socket = FakeStaffSocket(inbound=[json.dumps({"type": "authenticate", "access_token": token})])
        with patch.object(ws, "_voice_receptionist_enabled", return_value=True):
            task = asyncio.create_task(ws.staff_calls_stream_endpoint(socket, call_id=None))
            done, _pending = await asyncio.wait({task}, timeout=5.0)
            if not done:
                task.cancel()

        self.assertTrue(done, "the lobby socket outlived its token")
        self.assertEqual(socket.closed, 4401)

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


# ---------------------------------------------------------------------------
# Found in review of this change
# ---------------------------------------------------------------------------

LUGANDA_QUESTION = "Nyinza ntya okwewandiisa okufuna TIN?"


class ConfiguredLanguageTests(BrainCase):
    """The brain moves a call only into a language this deployment takes calls in."""

    async def test_a_typed_turn_in_a_language_calls_are_not_held_in_leaves_the_call_where_it_is(self):
        from app.receptionist.serializer import CallTextTurnFrame

        with patch.dict("os.environ", {"RECEPTIONIST_LANGUAGES": "en,sw"}):
            await self.brain.process_frame(CallTextTurnFrame(text="Nsaba okumanya ku VAT yange"))

        self.assertEqual(self.state.locale, "en")

    async def test_an_on_screen_choice_of_such_a_language_is_not_taken(self):
        from app.receptionist.serializer import SetLanguageFrame

        with patch.dict("os.environ", {"RECEPTIONIST_LANGUAGES": "en,sw"}):
            await self.brain.process_frame(SetLanguageFrame(language="lg"))

        self.assertEqual(self.state.locale, "en")


class ReplyLanguageTests(BrainCase):
    """The audit row names the language the reply was in, which is not always the call's."""

    async def test_english_served_on_a_luganda_call_is_recorded_as_english(self):
        self.state.locale = "lg"
        self.chat_model._maybe_handle_fast_paths.return_value = None
        self.chat_model._localize_reply.side_effect = RuntimeError("translation down")
        with (
            patch.object(self.brain, "_synthesize_luganda_reply", return_value=""),
            patch.object(self.brain, "_extract_english_tax_query", return_value="How do I register for a TIN?"),
        ):
            await self.hear(LUGANDA_QUESTION)

        row = self.rows()[0]["result"]
        self.assertEqual((row["locale"], row["reply_locale"]), ("lg", "en"))

    async def test_a_reply_the_service_could_not_translate_keeps_the_language_it_reported(self):
        self.state.locale = "sw"
        self.state.language_source = "override"
        self.chat_model.generate.return_value = {**self.chat_model.generate.return_value, "reply_locale": "en"}

        await self.hear("How do I register for a TIN?")

        row = self.rows()[0]["result"]
        self.assertEqual((row["locale"], row["reply_locale"]), ("sw", "en"))


class GenerationSlotTests(unittest.IsolatedAsyncioTestCase):
    """A model-run slot is given back once, when the work that took it has really stopped."""

    def slot(self):
        from app.receptionist.brain import _GenerationSlot

        self.semaphore = asyncio.Semaphore(0)
        return _GenerationSlot(self.semaphore, asyncio.get_running_loop())

    def given_back(self) -> int:
        return self.semaphore._value

    async def test_a_step_still_running_holds_the_slot_after_the_turn_stops_waiting(self):
        import threading

        gate = threading.Event()
        slot = self.slot()
        step = asyncio.create_task(slot.run(gate.wait, 5))
        await asyncio.sleep(0.1)

        step.cancel()  # the turn timed out, or a newer turn replaced it
        with contextlib.suppress(asyncio.CancelledError):
            await step
        slot.finish()
        self.assertEqual(self.given_back(), 0, "the slot was freed while its thread was still running")

        gate.set()
        await asyncio.sleep(0.2)
        self.assertEqual(self.given_back(), 1)

    async def test_a_step_that_never_started_does_not_keep_the_slot(self):
        slot = self.slot()
        ran: list[bool] = []
        step = asyncio.create_task(slot.run(ran.append, True))
        step.cancel()  # before the coroutine, let alone its thread, has started
        with contextlib.suppress(asyncio.CancelledError):
            await step

        slot.finish()

        self.assertEqual((ran, self.given_back()), ([], 1))

    async def test_a_worker_that_starts_after_its_slot_was_given_back_does_no_work(self):
        # The executor had taken the job but the thread had not yet counted
        # itself when the turn gave up: the slot is free, so the work must not
        # run without one. (Nobody is waiting for it any more either.)
        slot = self.slot()
        ran: list[bool] = []
        handed_to_executor: list = []

        async def thread_not_yet_running(work):
            handed_to_executor.append(work)
            await asyncio.sleep(3600)

        with patch("app.receptionist.brain.asyncio.to_thread", thread_not_yet_running):
            step = asyncio.create_task(slot.run(ran.append, True))
            await asyncio.sleep(0.01)
            step.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await step
        slot.finish()
        self.assertEqual(self.given_back(), 1)

        await asyncio.to_thread(handed_to_executor[0])  # the thread gets going only now
        await asyncio.sleep(0.05)

        self.assertEqual(ran, [], "work ran after its slot had been given back")
        self.assertEqual(self.given_back(), 1)

    async def test_a_job_still_queued_for_a_thread_when_the_turn_gives_up_frees_the_slot(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor

        loop = asyncio.get_running_loop()
        only_thread = ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(only_thread)
        busy = threading.Event()
        self.addCleanup(busy.set)
        blocker = loop.run_in_executor(None, busy.wait, 5)  # the pool's one thread is taken
        slot = self.slot()
        ran: list[bool] = []
        step = asyncio.create_task(slot.run(ran.append, True))
        await asyncio.sleep(0.05)  # the job is queued behind the blocker

        step.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await step
        slot.finish()
        self.assertEqual(self.given_back(), 1)

        busy.set()
        await blocker
        await asyncio.sleep(0.05)
        self.assertEqual((ran, self.given_back()), ([], 1))

    async def test_the_slot_is_given_back_once(self):
        slot = self.slot()
        await slot.run(lambda: None)

        slot.finish()
        slot.finish()
        await asyncio.sleep(0.05)

        self.assertEqual(self.given_back(), 1)


class GenerationSlotUseTests(BrainCase):
    async def test_an_abandoned_luganda_run_keeps_its_slot_until_its_step_ends(self):
        import threading

        from app.receptionist.brain import MAX_GENERATIONS_IN_FLIGHT

        gate = threading.Event()
        self.addCleanup(gate.set)
        self.state.locale = "lg"
        self.chat_model._maybe_handle_fast_paths.side_effect = lambda **_kw: gate.wait(5) and None
        turn = asyncio.create_task(self.hear(LUGANDA_QUESTION))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if self.chat_model._maybe_handle_fast_paths.called:
                break
        self.assertTrue(self.chat_model._maybe_handle_fast_paths.called)

        self.state.generation_id += 1  # a newer turn replaced this one
        self.brain._active_gen_task.cancel()
        await asyncio.wait({turn}, timeout=3.0)

        self.assertEqual(self.brain._generation_slots._value, MAX_GENERATIONS_IN_FLIGHT - 1)
        gate.set()
        await asyncio.sleep(0.3)
        self.assertEqual(self.brain._generation_slots._value, MAX_GENERATIONS_IN_FLIGHT)

    async def test_a_question_is_answered_in_the_language_it_was_asked_in(self):
        from app.receptionist import brain as brain_module

        real_run = brain_module._GenerationSlot.run

        async def worker_starts_late(slot, fn, *args, **kwargs):
            self.state.locale = "sw"  # a newer turn moved the call before this worker started
            return await real_run(slot, fn, *args, **kwargs)

        with patch.object(brain_module._GenerationSlot, "run", worker_starts_late):
            await self.hear("How do I register for a TIN?")

        self.assertEqual(self.chat_model.generate.call_args.kwargs["locale"], "en")


class WrapUpReviewTests(LiveCallCase):
    """Wrap-up cases the first version of this change got wrong."""

    def no_room_here(self):
        """This process has no room for the call: another worker has it, or its process died."""
        return patch.object(desk.registry, "get", return_value=None)

    async def refused(self, officer: str, **kwargs) -> desk.DeskError:
        with self.assertRaises(desk.DeskError) as refused:
            await desk.wrapup(self.call_id, officer, outcome="resolved", note="Closed", **kwargs)
        return refused.exception

    async def test_the_officer_on_a_call_another_worker_holds_is_still_on_it(self):
        await self.bridge("okello")

        with self.no_room_here():
            error = await self.refused("okello")

        self.assertEqual(error.status, 409)

    async def test_a_waiting_call_another_worker_holds_is_not_open_to_any_officer(self):
        with self.no_room_here():
            error = await self.refused("mallory")

        self.assertEqual(error.status, 403)

    async def test_its_ticket_is_not_resolved_while_the_caller_waits_on_another_worker(self):
        from app.receptionist.store import update_call

        ticket = db.create_ticket("caller_requested", "I was charged a penalty I do not owe")
        update_call(self.call_id, ticket_id=ticket["id"])
        await self.bridge("okello")
        await desk.transfer(self.call_id, "okello", team="disputes")

        with self.no_room_here(), patch("app.flags.flags.is_enabled", side_effect=only("ticket_queue")):
            error = await self.refused("okello", ticket_action="resolve")

        self.assertEqual(error.status, 409)
        self.assertNotEqual(db.get_ticket(ticket["id"])["status"], "resolved")

    def test_a_wrap_up_saved_between_another_officers_read_and_write_is_kept(self):
        from app.receptionist import store

        def entry(officer: str) -> dict:
            return {"officer_id": officer, "officer_name": "", "outcome": "resolved", "note": f"{officer}'s note", "at": 1.0}

        raced = {"done": False}

        def nakato_saves_first(_row: dict) -> dict:
            # Called after okello's read of the row and before okello's write.
            if not raced["done"]:
                raced["done"] = True
                store.merge_wrapup(self.call_id, entry("nakato"), lambda _r: {})
            return {}

        store.merge_wrapup(self.call_id, entry("okello"), nakato_saves_first)

        self.assertEqual(sorted(w["officer_id"] for w in get_call(self.call_id)["wrapups"]), ["nakato", "okello"])

    async def link_fails(self, withdrawal: bool | None = None):
        """Wrap up with a new ticket whose link to the call cannot be written."""
        from app.receptionist.store import update_call as real_update

        await self.bridge("okello")
        await self.room.end("caller_hangup")

        def refuse_the_link(call_id, **fields):
            if "ticket_id" in fields:
                raise RuntimeError("database is locked")
            return real_update(call_id, **fields)

        patches = [
            patch("app.flags.flags.is_enabled", side_effect=only("ticket_queue")),
            patch.object(desk, "update_call", side_effect=refuse_the_link),
        ]
        if withdrawal is not None:
            patches.append(patch.object(db, "update_ticket", return_value=withdrawal))
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            with self.assertRaises(desk.DeskError):
                await desk.wrapup(self.call_id, "okello", outcome="follow_up", note="Needs a letter", ticket_action="create")
        return [t for t in db.list_tickets(limit=200) if t.get("conversation_id") == f"conv_{self.call_id}"]

    async def test_a_ticket_withdrawn_by_the_system_is_not_an_officers_first_response(self):
        made = await self.link_fails()

        self.assertEqual([t["status"] for t in made], ["wontfix"])
        self.assertFalse(made[0].get("first_response_at"))

    async def test_a_ticket_that_could_not_be_withdrawn_is_reported_as_an_orphan(self):
        with self.assertLogs("app.receptionist.desk", level="ERROR") as logs:
            await self.link_fails(withdrawal=False)

        self.assertTrue(any("orphan" in line for line in logs.output))

    async def resolve_with_a_ticket_write_that(self, *, exists: bool) -> desk.DeskError:
        """Wrap up as resolved while the ticket's own write reports failure (it returns False, it does not raise)."""
        from app.receptionist.store import update_call

        ticket = db.create_ticket("caller_requested", "I was charged a penalty I do not owe")
        update_call(self.call_id, ticket_id=ticket["id"])
        await self.bridge("okello")
        await self.room.end("caller_hangup")
        with (
            patch("app.flags.flags.is_enabled", side_effect=only("ticket_queue")),
            patch.object(db, "update_ticket", return_value=False),
            patch.object(db, "get_ticket", return_value=ticket if exists else None),
        ):
            return await self.refused("okello", ticket_action="resolve")

    async def test_a_call_is_not_recorded_resolved_when_its_ticket_could_not_be(self):
        error = await self.resolve_with_a_ticket_write_that(exists=True)

        self.assertEqual(error.status, 503)
        row = get_call(self.call_id)
        self.assertEqual((row["outcome"], row["wrapups"]), ("", []))

    async def test_a_ticket_that_no_longer_exists_is_said_to_be_gone(self):
        error = await self.resolve_with_a_ticket_write_that(exists=False)

        self.assertEqual(error.status, 409)
        self.assertEqual(get_call(self.call_id)["wrapups"], [])

    async def test_a_callback_the_officer_asked_for_is_dropped_when_they_change_the_outcome(self):
        await self.bridge("okello")
        await self.room.end("caller_hangup")
        await desk.wrapup(self.call_id, "okello", outcome="callback", note="Will ring back at two")
        self.assertEqual(get_call(self.call_id)["needs_callback"], 1)

        await desk.wrapup(self.call_id, "okello", outcome="resolved", note="Rang back; settled")

        row = get_call(self.call_id)
        self.assertEqual((row["needs_callback"], row["callback_reason"]), (0, ""))

    async def test_a_callback_owed_for_another_reason_stays_owed(self):
        from app.receptionist.store import update_call

        await self.bridge("okello")
        await self.room.end("caller_hangup")
        update_call(self.call_id, needs_callback=1, callback_reason="transfer_timeout")

        await desk.wrapup(self.call_id, "okello", outcome="resolved", note="Explained the notice")

        row = get_call(self.call_id)
        self.assertEqual((row["needs_callback"], row["callback_reason"]), (1, "transfer_timeout"))


class StaleEndTests(LiveCallCase):
    """A row with no room here is ended as stale only when no other process can be holding the call."""

    WORKERS = ("WORKERS", "UVICORN_WORKERS", "WEB_CONCURRENCY", "VOICE_RECEPTIONIST_REPLICAS")

    def deployment(self, **declared: str):
        """The worker and replica counts this deployment declares, and no room for the call here."""
        import os

        stack = contextlib.ExitStack()
        stack.enter_context(patch.dict("os.environ", declared))
        for name in self.WORKERS:
            if name not in declared:
                os.environ.pop(name, None)
        stack.enter_context(patch.object(desk.registry, "get", return_value=None))
        return stack

    async def test_a_call_another_worker_may_hold_is_not_ended_from_here(self):
        await self.bridge("okello")

        with self.deployment(UVICORN_WORKERS="2"):
            with self.assertRaises(desk.DeskError) as refused:
                await desk.end(self.call_id, "okello", speech_model=None)

        self.assertEqual(refused.exception.status, 409)
        self.assertEqual(get_call(self.call_id)["status"], "bridged")

    async def test_nor_when_the_deployment_does_not_say_how_many_processes_it_runs(self):
        await self.bridge("okello")

        with self.deployment():
            with self.assertRaises(desk.DeskError) as refused:
                await desk.end(self.call_id, "okello", speech_model=None)

        self.assertEqual(refused.exception.status, 409)

    async def test_the_only_process_ends_a_row_no_room_stands_behind(self):
        await self.bridge("okello")

        with self.deployment(UVICORN_WORKERS="1", VOICE_RECEPTIONIST_REPLICAS="1"):
            result = await desk.end(self.call_id, "okello", speech_model=None)

        self.assertTrue(result["stale"])
        self.assertEqual(get_call(self.call_id)["end_reason"], "officer_terminated_stale")

    async def test_a_row_too_old_to_be_a_live_call_is_ended_wherever_it_is_asked(self):
        import time as _time

        old = f"call_old_{uuid.uuid4().hex[:8]}"
        create_call(old, conversation_id=f"conv_{old}", status="bridged", started_at=_time.time() - 7200)

        with self.deployment(UVICORN_WORKERS="2"):
            result = await desk.end(old, "okello", speech_model=None)

        self.assertTrue(result["stale"])


class OfficerLastWordsTests(unittest.IsolatedAsyncioTestCase):
    """What an officer said just before leaving a call stays in its transcript."""

    def setUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_last_{uuid.uuid4().hex[:8]}"
        publish = patch.object(hub, "publish_call")
        publish.start()
        self.addCleanup(publish.stop)

    def leg(self, transcribe):
        from app.receptionist.officer import OfficerLeg
        from app.receptionist.state import CallRoom, CallState

        room = CallRoom(self.call_id, CallState(call_id=self.call_id, conversation_id="conv_last", mode="bridged"))
        return OfficerLeg(ws=FakeStaffSocket(), officer_id="okello", officer_name="Officer Okello",
                          room=room, speech_model=SimpleNamespace(transcribe=transcribe))

    def officer_turns(self) -> list[str]:
        from app.receptionist.store import list_turns

        return [t["text"] for t in list_turns(self.call_id) if t["speaker"] == "officer"]

    async def test_utterances_waiting_when_the_officer_leaves_are_still_transcribed(self):
        import time as _time

        def transcribe(*_args, **_kwargs):
            _time.sleep(0.1)
            return SimpleNamespace(text="Your objection is lodged.", latency_s=0.1)

        leg = self.leg(transcribe)
        leg.queue_transcription(b"\x00\x01" * 1600)
        leg.queue_transcription(b"\x00\x01" * 1600)
        await asyncio.sleep(0.01)

        await leg.close()
        await asyncio.sleep(0.6)

        self.assertEqual(self.officer_turns(), ["Your objection is lodged."] * 2)

    def slow_leg(self):
        import time as _time

        def transcribe(*_args, **_kwargs):
            _time.sleep(0.2)
            return SimpleNamespace(text="Your objection is lodged.", latency_s=0.2)

        leg = self.leg(transcribe)
        leg.queue_transcription(b"\x00\x01" * 1600)
        return leg

    async def test_the_wrap_up_draft_waits_for_what_the_officer_last_said(self):
        leg = self.slow_leg()
        await asyncio.sleep(0.01)
        await leg.close()
        seen: list[list[str]] = []

        with patch.object(desk, "draft_wrapup_note", side_effect=lambda _id: seen.append(self.officer_turns()) or ""):
            await desk._save_draft_wrapup(self.call_id)

        self.assertEqual(seen, [["Your objection is lodged."]])

    async def test_the_call_summary_waits_for_what_the_officer_last_said(self):
        leg = self.slow_leg()
        await asyncio.sleep(0.01)
        await leg.close()
        seen: list[list[str]] = []

        with patch.object(ws, "generate_call_summary", side_effect=lambda _id: seen.append(self.officer_turns()) or {}):
            await ws._summarise_when_transcribed(self.call_id)

        self.assertEqual(seen, [["Your objection is lodged."]])

    async def test_a_call_with_nothing_left_to_transcribe_is_not_made_to_wait(self):
        from app.receptionist import officer

        started = asyncio.get_running_loop().time()
        await officer.transcriptions_settled(self.call_id)

        self.assertLess(asyncio.get_running_loop().time() - started, 0.1)

    async def test_nothing_more_is_taken_once_the_leg_is_closed(self):
        leg = self.leg(lambda *_a, **_k: SimpleNamespace(text="late", latency_s=0.0))
        await leg.close()

        leg.queue_transcription(b"\x00\x01" * 1600)
        await asyncio.sleep(0.1)

        self.assertEqual(self.officer_turns(), [])

    async def test_a_transcription_that_hangs_is_given_up_on(self):
        import time as _time

        from app.receptionist import officer

        leg = self.leg(lambda *_a, **_k: _time.sleep(1.0) or SimpleNamespace(text="too late", latency_s=1.0))
        leg.queue_transcription(b"\x00\x01" * 1600)
        await asyncio.sleep(0.01)

        with patch.object(officer, "OFFICER_DRAIN_S", 0.1):
            await leg.close()
            await asyncio.sleep(0.4)

        self.assertTrue(leg._transcriber.done())
        await asyncio.sleep(0.8)  # let the abandoned thread finish before the loop closes


class ScheduleTenancyTests(unittest.TestCase):
    """An officer on shift keeps the desk open for their own tenant's taxpayers only."""

    def setUp(self) -> None:
        from app.receptionist import presence

        db.init_db()
        init_receptionist_schema()
        tag = uuid.uuid4().hex[:6]
        self.officer, self.own, self.other = f"akello-{tag}", f"tenant-gulu-{tag}", f"tenant-kla-{tag}"
        presence.upsert_presence(self.officer, tenant_id=self.own, display_name="Officer Akello", status="available")
        self.addCleanup(presence.upsert_presence, self.officer, status="offline")

    def online(self, tenant_id: str) -> bool:
        from app.escalation_policy import evaluate_cxp_schedule

        with patch("app.tenancy.tenant_enabled", return_value=True):
            return evaluate_cxp_schedule(check_presence=True, tenant_id=tenant_id).live_officer_online

    def test_their_own_tenant_sees_them(self):
        self.assertTrue(self.online(self.own))

    def test_another_tenant_does_not(self):
        self.assertFalse(self.online(self.other))


class SummaryNoteRaceTests(CallSummaryTicketTests):
    def test_a_note_an_officer_saves_while_the_summary_is_written_is_kept(self):
        real_get = db.get_ticket
        raced = {"done": False}

        def officer_writes_after_the_read(ticket_id: str):
            ticket = real_get(ticket_id)
            if not raced["done"]:
                raced["done"] = True
                db.update_ticket(ticket_id, staff_note="OFFICER NOTE: rang the taxpayer at ten")
            return ticket  # as read before the officer's save: no note yet

        with patch.object(db, "get_ticket", side_effect=officer_writes_after_the_read):
            self.summarise()

        self.assertEqual(real_get(self.ticket["id"])["staff_note"], "OFFICER NOTE: rang the taxpayer at ten")

    def test_a_ticket_with_no_note_gets_the_summary(self):
        self.summarise()

        self.assertIn("Call Summary", db.get_ticket(self.ticket["id"])["staff_note"])


class SlowStaffSocket(FakeStaffSocket):
    """A socket whose sends take a moment, as a real one's do."""

    async def send_text(self, text: str) -> None:
        await asyncio.sleep(0.05)
        await super().send_text(text)


class LeavingStaffSocket(FakeStaffSocket):
    """A listener whose browser closes shortly after signing in."""

    async def receive(self) -> dict:
        await asyncio.sleep(0.1)
        return {"type": "websocket.disconnect", "code": 1001}


class ListenerPlaceTests(LiveCallCase):
    async def asyncSetUp(self) -> None:
        from app import ws_concurrency

        await super().asyncSetUp()
        ws_concurrency.reset()
        self.addCleanup(ws_concurrency.reset)

    async def test_two_listeners_arriving_together_cannot_both_take_the_last_place(self):
        await self.bridge()
        first, second = SlowStaffSocket({"listen": "true"}), SlowStaffSocket({"listen": "true"})
        with patch.object(ws, "LISTENERS_PER_CALL", 1), self.staff("auditor1", "ura_auditor"):
            tasks = [asyncio.create_task(ws.officer_audio_endpoint(s, self.call_id)) for s in (first, second)]
            await asyncio.sleep(0.4)
            listening = len(self.room.listeners)
            refused = sorted(str(s.closed) for s in (first, second))
            await self.room.end("caller_hangup")
            await asyncio.wait(tasks, timeout=3.0)

        self.assertEqual(listening, 1)
        self.assertEqual(refused, ["1013", "None"])

    async def test_a_listener_who_leaves_during_a_silence_gives_up_their_place(self):
        await self.bridge()
        socket = LeavingStaffSocket({"listen": "true"})
        with self.staff("auditor1", "ura_auditor"):
            task = asyncio.create_task(ws.officer_audio_endpoint(socket, self.call_id))
            done, _pending = await asyncio.wait({task}, timeout=3.0)
            still_bridged = self.room.state.mode == "bridged"
            listeners = list(self.room.listeners)
            if not done:
                await self.room.end("caller_hangup")
                await asyncio.wait({task}, timeout=3.0)

        self.assertTrue(still_bridged)
        self.assertTrue(done, "a listener who had left kept their place until audio resumed")
        self.assertEqual(listeners, [])


if __name__ == "__main__":
    unittest.main()

"""An officer owes the wrap-up of a call that ended under them, however it ended (G610, ADR-0045).

A call can end on the server while the officer is on it (its time limit, a
media failure, a hang-up message) or during their reconnect grace. Only a
caller's hang-up on a bridged call moved the officer to ``wrap_up``; the
others left them ``on_call`` on a dead call, or marked them ``available``.
Their console's socket then closed on an error, so it went idle and its next
heartbeat said ``available``: the officer was offered to callers with the
wrap-up unsaved. These tests hold the three places that keep it owed: the
call's teardown (``ws._finish_call`` and the reconnect grace in ``desk``),
the store's ``owed_wrapup``, and the presence heartbeat.
"""

from __future__ import annotations

import asyncio
import time
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from app import database as db
from app.receptionist import desk, presence, ws
from app.receptionist.hub import hub
from app.receptionist.state import CallRoom, CallState, registry
from app.receptionist.store import create_call, init_receptionist_schema, update_call


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class OwedWrapUpCase(unittest.IsolatedAsyncioTestCase):
    """The real store, with one officer of the test's own: calls handled in other tests stay in the store."""

    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        lobby = patch.object(hub, "publish_lobby")
        lobby.start()
        self.addCleanup(lobby.stop)
        self.officer = f"{_new_id('okello')}@ura.go.ug"
        self.addCleanup(presence.upsert_presence, self.officer, status="offline")

    def ended_call(self, *handlers: str, wrapped_by: tuple[str, ...] = (), ended_ago_s: float = 60.0) -> str:
        """A call that ended *ended_ago_s* ago, handled by *handlers*, wrapped up by *wrapped_by*."""
        call_id = _new_id("call_owed")
        now = time.time()
        create_call(call_id, conversation_id=f"conv_{call_id}", status="ended", started_at=now - ended_ago_s - 120)
        update_call(
            call_id,
            ended_at=now - ended_ago_s,
            end_reason="max_call_duration",
            officer_id=handlers[-1] if handlers else None,
            handled_by_json=list(handlers),
            wrapups_json=[
                {"officer_id": o, "officer_name": "", "outcome": "resolved", "note": "Explained it.", "at": now}
                for o in wrapped_by
            ],
        )
        return call_id

    def status(self, officer: str | None = None) -> tuple[str, str]:
        row = presence.get_presence(officer or self.officer) or {}
        return str(row.get("raw_status") or ""), str(row.get("current_call_id") or "")


# ---------------------------------------------------------------------------
# The call's teardown
# ---------------------------------------------------------------------------


class CallTeardownTests(OwedWrapUpCase):
    """``_finish_call`` moves the officer who had the call to wrap_up, however the call ended."""

    def room(self, mode: str, **state: object) -> CallRoom:
        call_id = _new_id("call_owed")
        create_call(call_id, conversation_id=f"conv_{call_id}", status="bridged")
        room = CallRoom(call_id, CallState(call_id=call_id, conversation_id=f"conv_{call_id}", mode=mode, **state))
        room.end = AsyncMock()
        return room

    async def finish(self, room: CallRoom) -> None:
        with (
            patch.object(ws.call_brief, "stop"),
            patch.object(ws.risk, "stop"),
            patch.object(ws, "review_settle"),
            patch.object(ws, "record_call_end_metrics"),
            patch.object(ws, "log_voice_event"),
            patch.object(ws.desk, "_save_draft_wrapup", AsyncMock()),
            patch.object(ws, "_summarise_when_transcribed", AsyncMock()),
        ):
            await ws._finish_call(room)
            await asyncio.sleep(0)

    async def test_a_call_ended_at_its_time_limit_moves_its_officer_to_wrap_up(self):
        # CallRoom.end ran first (the call's clock): mode `ended`, the officer still named.
        room = self.room("ended", officer_id=self.officer, end_reason="max_call_duration")
        presence.set_on_call(self.officer, room.call_id)

        await self.finish(room)

        self.assertEqual(self.status(), ("wrap_up", room.call_id))

    async def test_a_call_that_ends_in_the_reconnect_grace_leaves_the_officer_owing_its_wrap_up(self):
        room = self.room("transferring", officer_id=self.officer, reconnecting_officer=self.officer)
        presence.set_on_call(self.officer, room.call_id)

        await self.finish(room)

        # They handled the call: not `available` with its wrap-up unsaved.
        self.assertEqual(self.status(), ("wrap_up", room.call_id))

    async def test_an_officer_now_on_another_call_is_left_on_it(self):
        other = _new_id("call_other")
        room = self.room("bridged", officer_id=self.officer)
        presence.set_on_call(self.officer, other)

        await self.finish(room)

        self.assertEqual(self.status(), ("on_call", other))

    async def test_an_officer_who_has_saved_the_wrap_up_is_not_put_back_in_it(self):
        # They pressed End, then saved the wrap-up before the caller's side finished.
        room = self.room("ended", officer_id=self.officer, end_reason="officer_ended")
        presence.upsert_presence(self.officer, status="available", current_call_id="")

        await self.finish(room)

        self.assertEqual(self.status(), ("available", ""))


class ReconnectGraceTests(OwedWrapUpCase):
    """The reconnect grace ends with the call already over: the officer owes its wrap-up."""

    def room(self) -> CallRoom:
        call_id = _new_id("call_owed")
        create_call(call_id, conversation_id=f"conv_{call_id}", status="bridged")
        room = CallRoom(
            call_id,
            CallState(
                call_id=call_id,
                conversation_id=f"conv_{call_id}",
                mode="transferring",
                officer_id=self.officer,
                reconnecting_officer=self.officer,
                livekit_room=f"ura-{call_id}",
                livekit_officer_identity="old-media",
            ),
        )
        presence.set_on_call(self.officer, call_id)
        return room

    async def test_a_failed_media_revocation_ends_the_call_and_the_officer_owes_its_wrap_up(self):
        room = self.room()

        async def revocation_fails(r: CallRoom, _identity: str) -> bool:
            r.state.mode = "ended"  # revoke_media ends the call: media_revocation_failed
            return False

        with patch.object(desk, "revoke_media", revocation_fails):
            await desk._expire_reconnect_grace(room, self.officer, 0)

        self.assertEqual(self.status(), ("wrap_up", room.call_id))

    async def test_a_caller_who_hangs_up_during_the_revocation_leaves_the_officer_owing_its_wrap_up(self):
        room = self.room()

        async def caller_hangs_up(r: CallRoom, _identity: str) -> bool:
            r.state.mode = "ended"
            return True

        with patch.object(desk, "revoke_media", caller_hangs_up):
            await desk._expire_reconnect_grace(room, self.officer, 0)

        self.assertEqual(self.status(), ("wrap_up", room.call_id))


# ---------------------------------------------------------------------------
# The store: which call does an officer owe?
# ---------------------------------------------------------------------------


class OwedWrapUpStoreTests(OwedWrapUpCase):
    def owed(self, officer: str | None = None) -> str:
        from app.receptionist.store import owed_wrapup

        return owed_wrapup(officer or self.officer)

    async def test_an_ended_call_the_officer_handled_without_a_wrap_up_is_owed(self):
        call_id = self.ended_call(self.officer)

        self.assertEqual(self.owed(), call_id)

    async def test_each_officer_who_handled_the_call_owes_their_own(self):
        first = f"{_new_id('nakato')}@ura.go.ug"
        call_id = self.ended_call(first, self.officer, wrapped_by=(first,))

        self.assertEqual(self.owed(), call_id)
        self.assertEqual(self.owed(first), "")

    async def test_a_call_ended_before_the_look_back_is_not_owed(self):
        from app.receptionist.store import OWED_WRAPUP_LOOKBACK_S

        self.ended_call(self.officer, ended_ago_s=OWED_WRAPUP_LOOKBACK_S + 3600)

        self.assertEqual(self.owed(), "")

    async def test_the_latest_call_owed_comes_first(self):
        self.ended_call(self.officer, ended_ago_s=3600)
        latest = self.ended_call(self.officer, ended_ago_s=60)
        self.ended_call(self.officer, wrapped_by=(self.officer,), ended_ago_s=10)

        self.assertEqual(self.owed(), latest)

    async def test_a_call_still_in_progress_is_not_owed(self):
        call_id = _new_id("call_live")
        create_call(call_id, conversation_id=f"conv_{call_id}", status="bridged")
        update_call(call_id, officer_id=self.officer, handled_by_json=[self.officer])

        self.assertEqual(self.owed(), "")


# ---------------------------------------------------------------------------
# The heartbeat
# ---------------------------------------------------------------------------


class HeartbeatTests(OwedWrapUpCase):
    def client(self, officer: str | None = None):
        from app.auth.jwt_auth import make_dev_token
        from app.main import app
        from starlette.testclient import TestClient

        client = TestClient(app)
        token = make_dev_token(officer or self.officer, role="ura_staff")
        client.headers.update({"Authorization": f"Bearer {token}"})
        return client

    def heartbeat(self, status: str, officer: str | None = None) -> dict:
        response = self.client(officer).put("/v1/admin/officers/me/presence", json={"status": status})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_an_officer_who_owes_a_wrap_up_stays_in_it_and_is_told_which_call(self):
        call_id = self.ended_call(self.officer)

        body = self.heartbeat("available")

        self.assertEqual(self.status(), ("wrap_up", call_id))
        self.assertEqual(body["wrap_up_call_id"], call_id)

    async def test_saving_the_wrap_up_lets_the_officer_become_available(self):
        call_id = self.ended_call(self.officer)
        self.heartbeat("available")

        saved = self.client().post(
            f"/v1/admin/calls/{call_id}/wrapup", json={"outcome": "resolved", "note": "Explained the VAT deadline."}
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        body = self.heartbeat("available")

        self.assertEqual(self.status()[0], "available")
        self.assertEqual(body["wrap_up_call_id"], "")

    async def test_an_officer_who_owes_a_wrap_up_can_still_step_away_or_sign_off(self):
        call_id = self.ended_call(self.officer)

        for status in ("busy", "away", "offline"):
            with self.subTest(status=status):
                body = self.heartbeat(status)
                self.assertEqual(self.status()[0], status)
                self.assertEqual(body["wrap_up_call_id"], call_id)

    async def test_a_second_officer_owes_their_own_wrap_up(self):
        first = f"{_new_id('nakato')}@ura.go.ug"
        self.addCleanup(presence.upsert_presence, first, status="offline")
        call_id = self.ended_call(first, self.officer, wrapped_by=(first,))

        self.heartbeat("available")
        self.heartbeat("available", officer=first)

        self.assertEqual(self.status(), ("wrap_up", call_id))
        self.assertEqual(self.status(first)[0], "available")

    async def test_a_claim_on_a_waiting_call_still_comes_first(self):
        self.ended_call(self.officer)
        waiting = _new_id("call_waiting")
        create_call(waiting, conversation_id=f"conv_{waiting}", status="transferring")
        room = await registry.create(waiting, f"conv_{waiting}", tenant_id="default")
        self.addAsyncCleanup(registry.remove, waiting)
        room.state.mode = "transferring"
        room.state.claimed_by = self.officer
        presence.set_on_call(self.officer, waiting)

        self.heartbeat("available")

        self.assertEqual(self.status(), ("on_call", waiting))


if __name__ == "__main__":
    unittest.main()

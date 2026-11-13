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

    def ended_call(
        self,
        *handlers: str,
        wrapped_by: tuple[str, ...] = (),
        ended_ago_s: float = 60.0,
        tenant_id: str = "default",
        lasted_s: float = 120.0,
    ) -> str:
        """A call of *tenant_id* that ended *ended_ago_s* ago, handled by *handlers*, wrapped up by *wrapped_by*."""
        call_id = _new_id("call_owed")
        now = time.time()
        create_call(
            call_id,
            conversation_id=f"conv_{call_id}",
            tenant_id=tenant_id,
            status="ended",
            started_at=now - ended_ago_s - lasted_s,
        )
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

    def live_call(
        self,
        status: str,
        *handlers: str,
        officer_id: str | None = None,
        claimed_by: str = "",
        started_ago_s: float = 120.0,
    ) -> str:
        """A call still in progress (*status*), handled by *handlers*; its row names *officer_id* and *claimed_by*."""
        call_id = _new_id("call_live")
        create_call(
            call_id,
            conversation_id=f"conv_{call_id}",
            tenant_id="default",
            status=status,
            started_at=time.time() - started_ago_s,
        )
        update_call(call_id, officer_id=officer_id, claimed_by=claimed_by, handled_by_json=list(handlers))
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

    async def test_a_caller_who_asks_for_a_callback_during_the_revocation_leaves_the_officer_owing_its_wrap_up(self):
        # The wait ends as a callback while the media is revoked: the AI has
        # the caller again, and the call goes on without the officer.
        room = self.room()

        async def caller_asks_for_a_callback(r: CallRoom, _identity: str) -> bool:
            r.state.mode = "ai"  # close_transfer_on_timeout
            return True

        with patch.object(desk, "revoke_media", caller_asks_for_a_callback):
            await desk._expire_reconnect_grace(room, self.officer, 0)

        self.assertEqual(self.status(), ("wrap_up", room.call_id))

    async def test_a_grace_that_lapses_on_a_call_still_running_leaves_the_officer_owing_its_wrap_up(self):
        # The caller is still on the line and the call goes back to the queue.
        # The officer handled it: they were marked available with its wrap-up
        # unsaved, and offered to the next caller (G610, ADR-0047).
        room = self.room()
        room.state.claimed_by = self.officer  # a bridged call keeps its claimant
        update_call(room.call_id, officer_id=self.officer, claimed_by=self.officer, handled_by_json=[self.officer])

        with patch.object(desk, "revoke_media", AsyncMock(return_value=True)):
            await desk._expire_reconnect_grace(room, self.officer, 0)

        self.assertEqual(room.state.mode, "transferring")  # the caller waits for the next officer
        self.assertEqual(self.status(), ("wrap_up", room.call_id))


# ---------------------------------------------------------------------------
# The store: which call does an officer owe?
# ---------------------------------------------------------------------------


class OwedWrapUpStoreTests(OwedWrapUpCase):
    def owed(self, officer: str | None = None, tenant_id: str | None = "default") -> str:
        from app.receptionist.store import owed_wrapup

        return owed_wrapup(officer or self.officer, tenant_id)

    async def test_an_ended_call_the_officer_handled_without_a_wrap_up_is_owed(self):
        call_id = self.ended_call(self.officer)

        self.assertEqual(self.owed(), call_id)

    async def test_each_officer_who_handled_the_call_owes_their_own(self):
        first = f"{_new_id('nakato')}@ura.go.ug"
        call_id = self.ended_call(first, self.officer, wrapped_by=(first,))

        self.assertEqual(self.owed(), call_id)
        self.assertEqual(self.owed(first), "")

    async def test_a_long_call_that_ended_recently_is_owed(self):
        # The look-back runs from the end: a call that began more than a day
        # ago (RECEPTIONIST_MAX_CALL_S has no upper bound) but ended a minute
        # ago is still owed.
        from unittest import mock

        with mock.patch.dict("os.environ", {"RECEPTIONIST_MAX_CALL_S": str(30 * 3600)}):
            call_id = self.ended_call(self.officer, ended_ago_s=60.0, lasted_s=26 * 3600)
            self.assertEqual(self.owed(), call_id)

    async def test_a_call_ended_before_the_look_back_is_not_owed(self):
        from app.receptionist.store import OWED_WRAPUP_LOOKBACK_S

        self.ended_call(self.officer, ended_ago_s=OWED_WRAPUP_LOOKBACK_S + 3600)

        self.assertEqual(self.owed(), "")

    async def test_the_latest_call_owed_comes_first(self):
        self.ended_call(self.officer, ended_ago_s=3600)
        latest = self.ended_call(self.officer, ended_ago_s=60)
        self.ended_call(self.officer, wrapped_by=(self.officer,), ended_ago_s=10)

        self.assertEqual(self.owed(), latest)

    async def test_a_call_in_another_tenant_is_not_owed(self):
        # The heartbeat asks for the officer's own tenant, the one their presence is written in.
        self.ended_call(self.officer, tenant_id="another-tenant")

        self.assertEqual(self.owed(), "")
        self.assertNotEqual(self.owed(tenant_id="another-tenant"), "")

    async def test_with_multi_tenancy_off_the_tenant_is_not_a_filter(self):
        # As get_call and list_calls: the desk passes no tenant when
        # multi_tenant is off, and a call's own tenant then does not hide it.
        call_id = self.ended_call(self.officer, tenant_id="another-tenant")

        self.assertEqual(self.owed(tenant_id=None), call_id)

    async def test_a_call_still_in_progress_is_not_owed(self):
        call_id = _new_id("call_live")
        create_call(call_id, conversation_id=f"conv_{call_id}", status="bridged")
        update_call(call_id, officer_id=self.officer, handled_by_json=[self.officer])

        self.assertEqual(self.owed(), "")

    # A call the officer has left while it still runs (G610, ADR-0047). A
    # transfer and the end of their reconnect grace clear the row's officer;
    # the next officer to join replaces it.

    async def test_a_live_call_the_officer_passed_on_is_owed(self):
        call_id = self.live_call("transferring", self.officer, officer_id=None)

        self.assertEqual(self.owed(), call_id)

    async def test_a_live_call_now_bridged_to_another_officer_is_owed(self):
        nakato = f"{_new_id('nakato')}@ura.go.ug"
        call_id = self.live_call("bridged", self.officer, nakato, officer_id=nakato)

        self.assertEqual(self.owed(), call_id)
        self.assertEqual(self.owed(nakato), "")  # on the call: not theirs to wrap up yet

    async def test_a_live_call_back_with_the_ai_is_owed(self):
        # Nobody took it in time: the AI has the caller again, owed a callback.
        call_id = self.live_call("ai", self.officer, officer_id=None)

        self.assertEqual(self.owed(), call_id)

    async def test_a_call_in_the_officers_reconnect_grace_is_not_owed(self):
        # The row stays the officer's while the call waits for them to rejoin.
        self.live_call("bridged", self.officer, officer_id=self.officer)

        self.assertEqual(self.owed(), "")

    async def test_a_live_call_the_officer_has_claimed_again_is_not_owed(self):
        self.live_call("transferring", self.officer, officer_id=None, claimed_by=self.officer)

        self.assertEqual(self.owed(), "")

    async def test_a_live_call_already_wrapped_up_is_not_owed(self):
        call_id = self.live_call("transferring", self.officer, officer_id=None)
        update_call(call_id, wrapups_json=[
            {"officer_id": self.officer, "officer_name": "", "outcome": "referred", "note": "Passed to VAT.",
             "at": time.time()},
        ])

        self.assertEqual(self.owed(), "")

    async def test_a_live_call_older_than_the_look_back_and_longest_call_is_not_owed(self):
        # A row a dead process left marked live is not owed for ever.
        from app.receptionist.config import get_max_call_s
        from app.receptionist.store import OWED_WRAPUP_LOOKBACK_S

        self.live_call(
            "transferring", self.officer, started_ago_s=OWED_WRAPUP_LOOKBACK_S + get_max_call_s() + 3600
        )

        self.assertEqual(self.owed(), "")


# ---------------------------------------------------------------------------
# The heartbeat
# ---------------------------------------------------------------------------


class HeartbeatCase(OwedWrapUpCase):
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


class HeartbeatTests(HeartbeatCase):
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


class LeftWhileLiveTests(HeartbeatCase):
    """A call the officer left while it still runs owes its wrap-up too (G610, ADR-0047).

    After a transfer, or the end of their reconnect grace, the call goes on
    without them. Only a call that had ended was owed, so a console reloaded
    before its wrap-up was saved reported ``available`` and the officer was
    offered to callers, and could be named in a transfer.
    """

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.call_id = _new_id("call_left")
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}", status="transferring", tenant_id="default")
        self.room = await registry.create(self.call_id, f"conv_{self.call_id}", tenant_id="default")
        self.room.state.mode = "transferring"
        self.addAsyncCleanup(registry.remove, self.call_id)
        self.addAsyncCleanup(self.stop_timers)
        draft = patch.object(desk, "_save_draft_wrapup", AsyncMock())
        draft.start()
        self.addCleanup(draft.stop)

    async def stop_timers(self) -> None:
        desk._cancel_claim_timer(self.room)
        desk._cancel_reconnect_timer(self.room)
        desk._cancel_reservation(self.room)

    async def bridged(self, officer: str | None = None) -> str:
        officer = officer or self.officer
        await desk.claim(self.call_id, officer)
        await desk.bridge(self.room, None, officer, speech_model=None)
        return officer

    async def in_grace(self) -> None:
        """Bridged, then their media dropped: the call waits for them to rejoin."""
        await self.bridged()
        with patch.object(desk, "get_officer_reconnect_grace_s", return_value=3600.0):
            await desk.handle_officer_disconnect(self.room, self.officer, speech_model=None)
        self.assertEqual(self.room.state.reconnecting_officer, self.officer)

    async def wrap_up(self, officer: str | None = None, **kwargs) -> dict:
        return await desk.wrapup(self.call_id, officer or self.officer, "referred", "Passed to the VAT team.", **kwargs)

    async def test_a_reload_after_passing_a_call_on_keeps_the_officer_in_wrap_up(self):
        await self.bridged()
        await desk.transfer(self.call_id, self.officer)
        self.assertEqual(self.status(), ("wrap_up", self.call_id))

        body = self.heartbeat("available")  # the console was reloaded

        self.assertEqual(self.status(), ("wrap_up", self.call_id))
        self.assertEqual(body["wrap_up_call_id"], self.call_id)
        # So nobody can name them in a transfer meanwhile.
        with self.assertRaises(desk.DeskError):
            desk._available_officer_name(self.officer)

    async def test_the_officer_may_still_wrap_up_a_call_they_passed_on(self):
        await self.bridged()
        await desk.transfer(self.call_id, self.officer)

        saved = await self.wrap_up(ticket_action=None)
        body = self.heartbeat("available")

        self.assertTrue(saved["wrapped_up"])
        self.assertEqual(self.status()[0], "available")
        self.assertEqual(body["wrap_up_call_id"], "")

    async def test_a_grace_that_lapses_while_the_call_runs_leaves_the_officer_in_wrap_up(self):
        await self.in_grace()

        await desk._expire_reconnect_grace(self.room, self.officer, 0)
        body = self.heartbeat("available")

        self.assertEqual(self.room.state.mode, "transferring")
        self.assertEqual(self.status(), ("wrap_up", self.call_id))
        self.assertEqual(body["wrap_up_call_id"], self.call_id)

    async def test_an_officer_in_their_reconnect_grace_cannot_wrap_the_call_up(self):
        # They may rejoin it: a wrap-up saved now made them available while
        # the call was still held for them.
        await self.in_grace()

        with self.assertRaises(desk.DeskError) as refused:
            await self.wrap_up()

        self.assertEqual(refused.exception.status, 409)
        self.assertEqual(self.status(), ("on_call", self.call_id))

    async def test_an_officer_who_has_claimed_the_call_cannot_wrap_it_up(self):
        # They passed it on, then took it again before joining.
        await self.bridged()
        await desk.transfer(self.call_id, self.officer)
        await desk.claim(self.call_id, self.officer)

        with self.assertRaises(desk.DeskError) as refused:
            await self.wrap_up()

        self.assertEqual(refused.exception.status, 409)
        self.assertEqual(self.status(), ("on_call", self.call_id))

    async def test_a_claim_recorded_only_on_the_row_still_refuses_the_wrap_up(self):
        # Another process holds the call: no room here, the row is what is known.
        await registry.remove(self.call_id)
        update_call(self.call_id, status="transferring", claimed_by=self.officer, handled_by_json=[self.officer])

        with self.assertRaises(desk.DeskError) as refused:
            await self.wrap_up()

        self.assertEqual(refused.exception.status, 409)


class HeartbeatRaceTests(HeartbeatCase):
    """A write to presence that lands while the heartbeat decides is not overwritten (ADR-0045).

    The heartbeat runs on a worker thread and does not take the desk's lock; a
    claim runs on the event loop. Here the write lands inside ``owed_wrapup``:
    after the heartbeat's checks, before its own write.
    """

    def lands_during_the_heartbeat(self, write):
        from app.receptionist import store

        real = store.owed_wrapup
        landed: list[bool] = []

        def owed_then_written(*args, **kwargs):
            answer = real(*args, **kwargs)
            if not landed:
                landed.append(True)
                write()
            return answer

        return patch.object(store, "owed_wrapup", side_effect=owed_then_written)

    def claim(self, call_id: str):
        return self.lands_during_the_heartbeat(lambda: presence.set_on_call(self.officer, call_id))

    async def test_a_claim_is_not_overwritten_by_an_owed_wrap_up(self):
        self.ended_call(self.officer)
        presence.upsert_presence(self.officer, status="available", current_call_id="")
        claimed = _new_id("call_claimed")

        with self.claim(claimed):
            body = self.heartbeat("available")

        self.assertEqual(self.status(), ("on_call", claimed))
        self.assertEqual(body["status"], "on_call")
        self.assertEqual(body["current_call_id"], claimed)
        self.assertEqual(body["wrap_up_call_id"], "")

    async def test_a_claim_is_not_overwritten_by_available(self):
        presence.upsert_presence(self.officer, status="available", current_call_id="")
        claimed = _new_id("call_claimed")

        with self.claim(claimed):
            body = self.heartbeat("available")

        self.assertEqual(self.status(), ("on_call", claimed))
        self.assertEqual(body["status"], "on_call")
        self.assertEqual(body["wrap_up_call_id"], "")

    async def test_a_claim_on_an_officer_with_no_presence_yet_is_not_overwritten(self):
        claimed = _new_id("call_claimed")

        with self.claim(claimed):
            body = self.heartbeat("available")

        self.assertEqual(self.status(), ("on_call", claimed))
        self.assertEqual(body["status"], "on_call")

    async def test_a_wrap_up_owed_meanwhile_is_answered_and_the_officer_counts_as_seen(self):
        # Their tab was hidden: no heartbeat for five minutes, so they show offline.
        call_id = self.ended_call(self.officer)
        presence.set_on_call(self.officer, call_id)
        db.execute("UPDATE officer_presence SET last_seen = ? WHERE user_id = ?", (time.time() - 300, self.officer))
        sent_at = time.time()

        with self.lands_during_the_heartbeat(lambda: presence.owe_wrap_up(self.officer, call_id)):
            body = self.heartbeat("available")

        self.assertEqual(self.status(), ("wrap_up", call_id))
        self.assertEqual(body["status"], "wrap_up")
        self.assertEqual(body["wrap_up_call_id"], call_id)
        self.assertGreaterEqual((presence.get_presence(self.officer) or {}).get("last_seen", 0), sent_at)

    async def test_a_heartbeat_nothing_races_still_writes(self):
        presence.upsert_presence(self.officer, status="busy", current_call_id="")
        before = (presence.get_presence(self.officer) or {})["last_seen"]

        body = self.heartbeat("available")

        self.assertEqual(self.status(), ("available", ""))
        self.assertEqual(body["status"], "available")
        self.assertEqual(body["wrap_up_call_id"], "")
        self.assertGreater((presence.get_presence(self.officer) or {})["last_seen"], before)

    async def test_an_owed_wrap_up_still_replaces_available_when_nothing_races(self):
        call_id = self.ended_call(self.officer)
        presence.upsert_presence(self.officer, status="available", current_call_id="")

        body = self.heartbeat("available")

        self.assertEqual(self.status(), ("wrap_up", call_id))
        self.assertEqual(body["wrap_up_call_id"], call_id)


if __name__ == "__main__":
    unittest.main()

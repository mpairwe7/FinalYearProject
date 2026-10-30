"""Fault injection for call teardown, media isolation and evidence integrity."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.receptionist import desk
from app.receptionist.state import CallRoom, CallState


class AssuranceTests(unittest.IsolatedAsyncioTestCase):
    def room(self):
        return CallRoom(
            "call_assurance",
            CallState(
                call_id="call_assurance",
                conversation_id="conv_assurance",
                mode="transferring",
                reconnecting_officer="old-officer",
                livekit_room="ura-call_assurance",
                livekit_officer_identity="old-media",
            ),
        )

    async def test_failed_revoke_ends_media_room(self):
        room = self.room()
        room.end = AsyncMock()
        with patch.object(
            desk.livekit, "revoke_participant", AsyncMock(side_effect=RuntimeError("offline"))
        ):
            result = await desk.revoke_media(room, "old-media")
        self.assertFalse(result)
        room.end.assert_awaited_once_with("media_revocation_failed")

    async def test_grace_does_not_release_claim_before_revocation(self):
        room = self.room()
        started = asyncio.Event()
        unblock = asyncio.Event()

        async def revoke(*args):
            started.set()
            await unblock.wait()
            return False

        with (
            patch.object(desk, "revoke_media", revoke),
            patch.object(desk, "_release_locked", AsyncMock()) as release,
        ):
            expiry = asyncio.create_task(desk._expire_reconnect_grace(room, "old-officer", 0))
            await asyncio.wait_for(started.wait(), 1)
            try:
                release.assert_not_awaited()
            finally:
                unblock.set()
                await expiry
            release.assert_not_awaited()

    async def test_ending_from_a_timer_does_not_cancel_its_own_teardown(self):
        room = self.room()
        room.state.transfer_timer_task = asyncio.current_task()
        room.officer = type("Leg", (), {"close": AsyncMock()})()
        with (
            patch("app.receptionist.state.update_call"),
            patch.object(desk.livekit, "close_room", AsyncMock()) as close,
        ):
            await room.end("timeout")
            await asyncio.sleep(0)
        close.assert_awaited_once_with("ura-call_assurance")

    async def test_ending_from_the_review_timer_does_not_cancel_teardown(self):
        room = self.room()
        room.state.review_timer_task = asyncio.current_task()
        with (
            patch("app.receptionist.state.update_call"),
            patch.object(desk.livekit, "close_room", AsyncMock()) as close,
        ):
            await room.end("review_timeout")
            await asyncio.sleep(0)
        close.assert_awaited_once()

    def test_empty_draft_does_not_invent_resolution(self):
        with (
            patch.object(desk, "get_call", return_value={"call_id": "call_assurance"}),
            patch.object(desk, "list_turns", return_value=[]),
        ):
            note = desk.draft_wrapup_note("call_assurance")
        self.assertNotIn("confirmed understanding", note)
        self.assertIn("not verified", note)

    def test_any_officer_utterance_is_not_evidence_of_statutory_guidance(self):
        with (
            patch.object(desk, "get_call", return_value={"call_id": "call_assurance"}),
            patch.object(
                desk,
                "list_turns",
                return_value=[
                    {"speaker": "officer", "kind": "utterance", "text": "Can you hear me?"}
                ],
            ),
        ):
            note = desk.draft_wrapup_note("call_assurance")
        self.assertNotIn("provided statutory guidance", note)

    async def test_reporting_failures_do_not_skip_room_end_or_other_reporting(self):
        from app.receptionist import ws

        room = self.room()
        room.end = AsyncMock()
        with (
            patch.object(ws.call_brief, "stop", side_effect=RuntimeError("monitor")),
            patch.object(ws.risk, "stop"),
            patch.object(ws, "update_call", side_effect=RuntimeError("database")),
            patch.object(ws, "review_settle", side_effect=RuntimeError("review")),
            patch.object(ws, "record_call_end_metrics", side_effect=RuntimeError("metrics")),
            patch.object(ws, "log_voice_event", side_effect=RuntimeError("audit")),
            patch.object(ws.hub, "publish_lobby") as publish,
            patch.object(ws.asyncio, "to_thread", AsyncMock()),
        ):
            await ws._finish_call(room)
            await asyncio.sleep(0)
        room.end.assert_awaited_once()
        publish.assert_called_once()

    async def test_wrapup_ticket_keeps_call_tenant_and_conversation(self):
        from app import database as db
        from app.flags import flags

        call = {
            "call_id": "call_assurance",
            "tenant_id": "tenant-b",
            "conversation_id": "conv-b",
            "locale": "lg",
        }
        with (
            patch.object(desk, "get_call", return_value=call),
            patch.object(flags, "is_enabled", return_value=True),
            patch.object(db, "create_ticket", return_value={"id": "ticket-b"}) as create,
            patch.object(desk, "update_call"),
            patch.object(desk.presence, "finish_wrap_up"),
            patch.object(desk.hub, "publish_lobby"),
        ):
            await desk.wrapup(
                "call_assurance", "officer", "follow_up", "Needs help", ticket_action="create"
            )
        self.assertEqual(create.call_args.kwargs["tenant_id"], "tenant-b")
        self.assertEqual(create.call_args.kwargs["conversation_id"], "conv-b")
        self.assertEqual(create.call_args.kwargs["locale"], "lg")

    async def test_disabled_queue_refuses_ticket_action_before_any_write(self):
        from app.flags import flags

        with (
            patch.object(desk, "get_call", return_value={"call_id": "call_assurance"}),
            patch.object(flags, "is_enabled", return_value=False),
            patch.object(desk, "update_call") as update,
        ):
            with self.assertRaises(desk.DeskError) as error:
                await desk.wrapup(
                    "call_assurance", "officer", "follow_up", "Needs help", ticket_action="create"
                )
        self.assertEqual(error.exception.status, 409)
        update.assert_not_called()

    async def test_failed_socket_accept_releases_admission_slot(self):
        from types import SimpleNamespace
        from app.receptionist import ws

        socket = SimpleNamespace(
            headers={},
            client=SimpleNamespace(host="test"),
            accept=AsyncMock(side_effect=RuntimeError("connection lost")),
        )
        with (
            patch.object(ws, "_voice_receptionist_enabled", return_value=True),
            patch.object(ws, "is_ws_origin_allowed", return_value=True),
            patch.object(ws, "try_acquire", return_value=True),
            patch.object(ws, "release") as release,
        ):
            await ws.call_stream_endpoint(socket)
        release.assert_called_once_with("call", "anon::test")

    async def test_pipeline_cancel_request_itself_has_a_deadline(self):
        from types import SimpleNamespace
        from app.receptionist import ws

        runner = asyncio.create_task(asyncio.sleep(3600))
        task = SimpleNamespace(cancel=AsyncMock(side_effect=lambda: None))

        async def stuck():
            await asyncio.sleep(3600)

        task.cancel = stuck
        with patch.object(ws, "_PIPELINE_STOP_TIMEOUT_S", 0.01):
            await asyncio.wait_for(
                ws._stop_pipeline(
                    SimpleNamespace(call_id="test", pipeline_runner_task=runner), task
                ),
                0.2,
            )
        with self.assertRaises(asyncio.CancelledError):
            await runner

    async def test_finalizer_failure_still_removes_room_and_admission_slot(self):
        from types import SimpleNamespace
        from app.receptionist import ws

        room = self.room()
        socket = SimpleNamespace(
            headers={},
            client=SimpleNamespace(host="test"),
            accept=AsyncMock(),
            receive_text=AsyncMock(return_value='{"type":"call_start"}'),
        )
        with (
            patch.object(ws, "_voice_receptionist_enabled", return_value=True),
            patch.object(ws.flags, "is_enabled", return_value=False),
            patch.object(ws, "is_ws_origin_allowed", return_value=True),
            patch.object(ws, "try_acquire", return_value=True),
            patch.object(
                ws, "_resolve_ws_principal", return_value=("", "default", "anonymous", None)
            ),
            patch.object(ws, "chat_carryover", return_value=None),
            patch.object(ws.livekit, "enabled", return_value=False),
            patch.object(ws.registry, "create", AsyncMock(return_value=room)),
            patch.object(ws, "create_call", side_effect=RuntimeError("database down")),
            patch.object(ws, "_finish_call", AsyncMock(side_effect=RuntimeError("cleanup failed"))),
            patch.object(ws.registry, "remove", AsyncMock()) as remove,
            patch.object(ws, "release") as release,
        ):
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                await ws.call_stream_endpoint(socket)
        remove.assert_awaited_once()
        release.assert_called_once_with("call", "anon::test")

    def test_already_departed_participant_not_found_does_not_fail_revocation(self):
        from app.receptionist import livekit

        class NotFoundError(Exception):
            code = "not_found"

        self.assertTrue(livekit._is_participant_absent_error(NotFoundError("participant does not exist")))
        self.assertFalse(livekit._is_participant_absent_error(RuntimeError("service unavailable")))

    async def test_caller_hangup_during_revocation_still_restores_officer_presence(self):
        room = self.room()

        async def revoke_and_hangup(r, _ident):
            r.state.mode = "ended"
            return True

        with (
            patch.object(desk, "revoke_media", revoke_and_hangup),
            patch.object(desk.presence, "finish_wrap_up") as finish_wrap,
        ):
            await desk._expire_reconnect_grace(room, "old-officer", 0)
        finish_wrap.assert_called_once_with("old-officer")
        self.assertIsNone(room.state.reconnecting_officer)

    async def test_caller_hangup_on_bridged_call_moves_officer_to_wrap_up(self):
        from app.receptionist import ws

        room = self.room()
        room.state.mode = "bridged"
        room.state.officer_id = "officer-active"
        room.state.reconnecting_officer = None
        room.end = AsyncMock()
        with (
            patch.object(ws.call_brief, "stop"),
            patch.object(ws.risk, "stop"),
            patch.object(ws, "review_settle"),
            patch.object(ws, "record_call_end_metrics"),
            patch.object(ws, "log_voice_event"),
            patch.object(ws.hub, "publish_lobby"),
            patch.object(ws.desk.presence, "set_wrap_up") as set_wrap,
            patch.object(ws.desk, "_save_draft_wrapup", AsyncMock()),
            patch.object(ws.asyncio, "to_thread", AsyncMock()),
        ):
            await ws._finish_call(room)
            await asyncio.sleep(0)
        set_wrap.assert_called_once_with("officer-active", call_id="call_assurance")

    async def test_wrapup_ticket_failure_does_not_commit_wrapup_before_ticket(self):
        from app import database as db
        from app.flags import flags

        with (
            patch.object(desk, "get_call", return_value={"call_id": "call_assurance"}),
            patch.object(flags, "is_enabled", return_value=True),
            patch.object(db, "create_ticket", side_effect=RuntimeError("db down")),
            patch.object(desk, "update_call") as update,
        ):
            with self.assertRaises(desk.DeskError) as err:
                await desk.wrapup(
                    "call_assurance", "officer", "follow_up", "Needs help", ticket_action="create"
                )
        self.assertEqual(err.exception.status, 503)
        update.assert_not_called()

    def test_conditional_draft_wrapup_does_not_overwrite_submitted_officer_wrapup(self):
        from app import database as db
        from app.receptionist.store import (
            create_call,
            get_call,
            init_receptionist_schema,
            save_draft_wrapup_if_empty,
            update_call,
        )

        db.init_db()
        init_receptionist_schema()
        create_call("call_draft_race", status="ended")
        update_call("call_draft_race", outcome="resolved", wrapup_note="Officer verified note", wrapup_at=100.0)
        wrote = save_draft_wrapup_if_empty("call_draft_race", "Generated draft note")
        self.assertFalse(wrote)
        self.assertEqual(get_call("call_draft_race")["wrapup_note"], "Officer verified note")

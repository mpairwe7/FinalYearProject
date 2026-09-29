"""The shared transfer path: what an officer's queue learns when the AI hands a call over.

The brain goes through ``receptionist/transfer.py``; these tests pin what reaches the staff lobby
and the ``voice_calls`` row, and what a timeout leaves behind.
"""

from __future__ import annotations

import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch


from app import database as db
from app.flags import flags
from app.receptionist import transfer as transfer_mod
from app.receptionist.state import CallRoom, CallState
from app.receptionist.store import (
    _CALL_DESK_COLUMNS,
    _ensure_columns,
    create_call,
    get_call,
    init_receptionist_schema,
    list_turns,
)
from app.receptionist.transfer import close_transfer_on_timeout, open_transfer

#: Everything the lobby may carry about a waiting call — metadata, never content.
LOBBY_KEYS = {
    "call_id", "status", "started_at", "reason", "ticket_ref", "topic", "priority",
    "language", "waiting_since", "target_team", "attempt",
}


def queue_on(name: str, **_kw) -> bool:
    return name == "ticket_queue"


class TransferTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_xfer_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}", user_id="taxpayer_1")
        self.state = CallState(call_id=self.call_id, conversation_id=f"conv_{self.call_id}",
                               user_id="taxpayer_1", mode="ai")
        self.room = CallRoom(call_id=self.call_id, state=self.state)
        self.chat_model = MagicMock()
        self.chat_model._build_handoff_packet.return_value = {
            "topic": "objection_or_dispute", "priority": "high", "summary": "Disputes an assessment",
            "recent_context": [{"role": "user", "content": "the transcript"}],
        }
        self.chat_model._maybe_create_ticket.return_value = "TICK-XFER-1"
        self.lobby = patch.object(transfer_mod.hub, "publish_lobby").start()
        self.addCleanup(patch.stopall)

    def lobby_events(self, event: str) -> list[dict]:
        return [c.args[1] for c in self.lobby.call_args_list if c.args[0] == event]


class OpenTransferTests(TransferTestCase):
    async def test_the_packet_s_topic_and_priority_reach_the_lobby_and_the_row(self):
        self.state.locale = "lg"
        tid, status_event = open_transfer(self.room, self.chat_model, "caller_requested")

        self.assertEqual((tid, status_event["status"]), ("TICK-XFER-1", "transferring"))
        [event] = self.lobby_events("call.transfer_requested")
        self.assertEqual(
            {k: event[k] for k in ("topic", "priority", "language", "ticket_ref", "attempt", "target_team")},
            {"topic": "objection_or_dispute", "priority": "high", "language": "lg",
             "ticket_ref": "TICK-XFER-1", "attempt": 1, "target_team": ""},
        )
        row = get_call(self.call_id)
        self.assertEqual((row["status"], row["topic"], row["priority"]), ("transferring", "objection_or_dispute", "high"))
        self.assertEqual(row["transfer_requested_at"], event["waiting_since"])
        self.assertEqual(self.state.transfer_requested_at, event["waiting_since"])

    async def test_the_lobby_hears_metadata_only(self):
        open_transfer(self.room, self.chat_model, "caller_requested")
        [event] = self.lobby_events("call.transfer_requested")
        self.assertLessEqual(set(event), LOBBY_KEYS)
        self.assertNotIn("the transcript", repr(event))
        self.assertNotIn("Disputes an assessment", repr(event))

    async def test_without_a_packet_the_call_is_general_and_normal(self):
        open_transfer(self.room, None, "caller_requested")
        [event] = self.lobby_events("call.transfer_requested")
        self.assertEqual((event["topic"], event["priority"]), ("general_tax_support", "normal"))

    async def test_a_priority_outside_the_scale_reads_as_normal(self):
        self.chat_model._build_handoff_packet.return_value = {"topic": "customs", "priority": "ASAP!"}
        open_transfer(self.room, self.chat_model, "caller_requested")
        self.assertEqual(get_call(self.call_id)["priority"], "normal")

    async def test_a_ticket_the_engine_already_has_keeps_its_packet(self):
        # The cascaded brain hands over the ticket and packet the chat turn made.
        open_transfer(self.room, self.chat_model, "rule_triggered", ticket_id="TICK-CHAT-9",
                      handoff={"topic": "registration", "priority": "urgent"})
        self.chat_model._maybe_create_ticket.assert_not_called()
        [event] = self.lobby_events("call.transfer_requested")
        self.assertEqual((event["ticket_ref"], event["topic"], event["priority"]),
                         ("TICK-CHAT-9", "registration", "urgent"))

    async def test_each_time_in_the_queue_is_a_new_attempt(self):
        open_transfer(self.room, self.chat_model, "caller_requested")
        self.state.mode = "ai"
        open_transfer(self.room, self.chat_model, "caller_requested", target_team="customs")
        attempts = [(e["attempt"], e["target_team"]) for e in self.lobby_events("call.transfer_requested")]
        self.assertEqual(attempts, [(1, ""), (2, "customs")])
        self.assertEqual(get_call(self.call_id)["target_team"], "customs")


class TimeoutTests(TransferTestCase):
    def waiting(self) -> None:
        open_transfer(self.room, self.chat_model, "caller_requested")
        self.lobby.reset_mock()

    def assert_owed_a_callback(self) -> None:
        row = get_call(self.call_id)
        self.assertEqual((row["status"], row["needs_callback"], row["callback_reason"]),
                         ("ai", 1, "no_officer_available"))
        self.assertEqual(self.lobby_events("call.transfer_timed_out"),
                         [{"call_id": self.call_id, "ticket_ref": "TICK-XFER-1"}])
        self.assertEqual(self.state.mode, "ai")

    async def test_nobody_answering_leaves_a_callback(self):
        self.waiting()
        status_event = close_transfer_on_timeout(self.room, "TICK-XFER-1")
        self.assertEqual(status_event, {"type": "status", "status": "ai", "ticket_ref": "TICK-XFER-1"})
        self.assert_owed_a_callback()

    async def test_a_call_an_officer_took_does_not_time_out(self):
        self.waiting()
        self.state.mode = "bridged"
        self.assertIsNone(close_transfer_on_timeout(self.room, "TICK-XFER-1"))
        self.assertEqual(get_call(self.call_id)["needs_callback"], 0)
        self.assertEqual(self.lobby_events("call.transfer_timed_out"), [])

    async def test_the_cascaded_engine_times_out_through_it(self):
        from app.receptionist.brain import UraReceptionistBrain

        brain = UraReceptionistBrain(room=self.room, chat_model=self.chat_model)
        brain.push_frame = AsyncMock()
        self.waiting()
        await brain._transfer_timeout_countdown(0, "TICK-XFER-1")
        self.assert_owed_a_callback()
        self.assertTrue(any("TICK-XFER-1" in t["text"] for t in list_turns(self.call_id)))  # its own line


class BrainTransferTests(TransferTestCase):
    """What the brain's transfer puts in the officers' queue."""

    async def cascaded_transfer(self) -> None:
        from app.receptionist.brain import UraReceptionistBrain

        brain = UraReceptionistBrain(room=self.room, chat_model=self.chat_model)
        brain.push_frame = AsyncMock()
        await brain._transfer("caller_requested")
        if self.state.transfer_timer_task is not None:
            self.state.transfer_timer_task.cancel()

    async def test_the_cascaded_engine_queues_the_packet_s_topic_and_language(self):
        self.state.locale = "sw"
        with patch.object(flags, "is_enabled", side_effect=queue_on):
            await self.cascaded_transfer()
        [event] = self.lobby_events("call.transfer_requested")
        self.assertEqual((event["topic"], event["priority"], event["language"]), ("objection_or_dispute", "high", "sw"))

    async def test_no_call_is_queued_without_the_ticket_queue(self):
        with patch.object(flags, "is_enabled", return_value=False):
            await self.cascaded_transfer()
        self.chat_model._maybe_create_ticket.assert_not_called()
        self.assertEqual(self.lobby_events("call.transfer_requested"), [])
        self.assertNotEqual(get_call(self.call_id)["status"], "transferring")
        self.assertEqual(self.state.transfer_attempts, 0)


class SchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        db.init_db()
        init_receptionist_schema()

    def test_voice_calls_has_every_call_desk_column(self):
        names = {r["name"] for r in db.query_all("PRAGMA table_info(voice_calls)")}
        self.assertLessEqual(set(_CALL_DESK_COLUMNS), names)

    def test_columns_are_added_to_an_existing_table_once(self):
        table = f"scratch_migrate_{uuid.uuid4().hex[:8]}"
        db.execute_script(f"CREATE TABLE {table} (call_id TEXT PRIMARY KEY);")  # noqa: S608 - test identifier
        self.addCleanup(db.execute_script, f"DROP TABLE IF EXISTS {table};")
        db.execute(f"INSERT INTO {table} (call_id) VALUES (?)", ("old",))  # noqa: S608
        columns = {"topic": "TEXT NOT NULL DEFAULT ''", "needs_callback": "INTEGER NOT NULL DEFAULT 0"}
        _ensure_columns(table, columns)
        _ensure_columns(table, columns)  # a second start changes nothing
        row = db.query_one(f"SELECT * FROM {table} WHERE call_id = ?", ("old",))  # noqa: S608
        self.assertEqual((row["topic"], row["needs_callback"]), ("", 0))


if __name__ == "__main__":
    unittest.main()

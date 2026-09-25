"""Tests for officer actions on live calls (Call Desk Phase 2)."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from app import database as db
from app.auth.jwt_auth import make_dev_token
from app.main import app
from app.receptionist import desk, presence
from app.receptionist.hub import hub
from app.receptionist.state import CallRoom, CallState, registry
from app.receptionist.store import create_call, get_call, init_receptionist_schema, update_call
from fastapi.testclient import TestClient


class TestOfficerActions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db.init_db()
        init_receptionist_schema()
        db.execute("DELETE FROM officer_presence")
        db.execute("DELETE FROM voice_calls")
        db.execute("DELETE FROM voice_call_turns")
        registry._rooms.clear()
        hub._lobby.clear()

    async def _make_bridged_room(self, call_id: str = "call_action_1", officer_id: str = "officer_okello"):
        create_call(call_id, conversation_id=call_id, status="ai", user_id="user_taxpayer_1")
        state = CallState(call_id=call_id, conversation_id=call_id, mode="bridged", officer_id=officer_id, officer_name="Officer Okello", locale="en")
        room = CallRoom(call_id=call_id, state=state)
        room.officer = MagicMock()
        room.officer.on_hold = False
        room.officer.close = AsyncMock()
        registry._rooms[call_id] = room
        update_call(call_id, status="bridged", officer_id=officer_id)
        presence.set_on_call(officer_id, call_id)
        return room

    async def test_hold_and_resume_accounting(self):
        room = await self._make_bridged_room("call_hold_1", "officer_okello")

        # Put on hold
        res = await desk.hold("call_hold_1", "officer_okello", on=True)
        self.assertTrue(res["hold"])
        self.assertTrue(room.state.on_hold)
        self.assertIsNotNone(room.state.hold_started_at)
        self.assertTrue(room.officer.on_hold)

        # Calling hold again while already on hold is idempotent
        res_dup = await desk.hold("call_hold_1", "officer_okello", on=True)
        self.assertTrue(res_dup["hold"])

        # Resume from hold
        await asyncio.sleep(0.05)
        res_off = await desk.hold("call_hold_1", "officer_okello", on=False)
        self.assertFalse(res_off["hold"])
        self.assertFalse(room.state.on_hold)
        self.assertIsNone(room.state.hold_started_at)
        self.assertGreater(room.state.hold_total_s, 0.0)
        self.assertFalse(room.officer.on_hold)

        call_db = get_call("call_hold_1")
        self.assertGreater(call_db["hold_total_s"], 0.0)
        self.assertIsNone(call_db["hold_started_at"])

    async def test_transfer_requeues_and_moves_presence(self):
        room = await self._make_bridged_room("call_xfer_1", "officer_nakato")
        room.state.ticket_id = "TIC-TEST-123"

        res = await desk.transfer(
            "call_xfer_1",
            "officer_nakato",
            team="disputes",
            note="Taxpayer requests senior dispute auditor",
        )
        self.assertTrue(res["transferred"])
        self.assertEqual(res["target_team"], "disputes")

        # Room state should be transferring
        self.assertEqual(room.state.mode, "transferring")
        self.assertEqual(room.state.transfer_reason, "officer_transfer")
        self.assertEqual(room.state.target_team, "disputes")
        self.assertEqual(room.state.transfer_attempts, 1)
        self.assertIsNone(room.officer)

        # Officer presence should be wrap_up
        p = presence.get_presence("officer_nakato")
        self.assertEqual(p["status"], "wrap_up")

        # DB row updated
        call_db = get_call("call_xfer_1")
        self.assertEqual(call_db["status"], "transferring")
        self.assertEqual(call_db["target_team"], "disputes")

    async def test_wrapup_saves_outcome_updates_ticket_and_restores_presence(self):
        create_call("call_wrap_1", status="ended", user_id="taxpayer_99")
        ticket = db.create_ticket("taxpayer_99", "Need refund status")
        db.update_ticket(ticket["id"], status="assigned", assignee="officer_musoke")
        update_call("call_wrap_1", ticket_id=ticket["id"])
        presence.upsert_presence("officer_musoke", display_name="Officer Musoke", status="wrap_up")

        res = await desk.wrapup(
            "call_wrap_1",
            "officer_musoke",
            outcome="resolved",
            note="Verified refund processed on 14 Sept",
            ticket_action="resolve",
            rating=5,
            rating_note="Clear call",
        )
        self.assertTrue(res["wrapped_up"])
        self.assertEqual(res["outcome"], "resolved")

        # Call in DB should have outcome and wrapup note
        call_db = get_call("call_wrap_1")
        self.assertEqual(call_db["outcome"], "resolved")
        self.assertEqual(call_db["wrapup_note"], "Verified refund processed on 14 Sept")
        self.assertEqual(call_db["officer_rating"], 5)

        # Ticket should be resolved
        t = db.get_ticket(ticket["id"])
        self.assertEqual(t["status"], "resolved")

        # Officer presence should be restored to available
        p = presence.get_presence("officer_musoke")
        self.assertEqual(p["status"], "available")

    async def test_wrapup_with_callback_outcome_sets_flag(self):
        create_call("call_cb_out", status="ended", user_id="taxpayer_cb")
        presence.upsert_presence("officer_cb", status="wrap_up")

        await desk.wrapup(
            "call_cb_out",
            "officer_cb",
            outcome="callback",
            note="Will check ledger and call back tomorrow",
        )
        call_db = get_call("call_cb_out")
        self.assertEqual(call_db["needs_callback"], 1)
        self.assertEqual(call_db["callback_reason"], "officer_outcome")

    async def test_callback_done(self):
        create_call("call_done_1", status="ended")
        update_call("call_done_1", needs_callback=1, callback_reason="no_officer_available")

        res = await desk.callback_done("call_done_1", "officer_kigozi", note="Called back, issue solved")
        self.assertTrue(res["ok"])

        call_db = get_call("call_done_1")
        self.assertEqual(call_db["needs_callback"], 0)
        self.assertEqual(call_db["callback_done_by"], "officer_kigozi")
        self.assertIsNotNone(call_db["callback_done_at"])
        self.assertIn("Called back, issue solved", call_db["officer_note"])

    async def test_auditor_cannot_perform_actions(self):
        await self._make_bridged_room("call_audit_1", "officer_legit")
        client = TestClient(app)
        auditor_token = make_dev_token("auditor_bob", role="ura_auditor")
        headers = {"Authorization": f"Bearer {auditor_token}"}

        res_hold = client.post("/v1/admin/calls/call_audit_1/hold", headers=headers, json={"on": True})
        self.assertEqual(res_hold.status_code, 403)

        res_xfer = client.post("/v1/admin/calls/call_audit_1/transfer", headers=headers, json={"team": "disputes"})
        self.assertEqual(res_xfer.status_code, 403)

        res_wrap = client.post("/v1/admin/calls/call_audit_1/wrapup", headers=headers, json={"outcome": "resolved", "note": "ok"})
        self.assertEqual(res_wrap.status_code, 403)

        res_cb = client.post("/v1/admin/calls/call_audit_1/callback-done", headers=headers, json={"note": "ok"})
        self.assertEqual(res_cb.status_code, 403)

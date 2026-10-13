"""Tests for officer actions on live calls (Call Desk Phase 2)."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
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
        db.execute("DELETE FROM service_ratings WHERE call_id LIKE 'call_csat%'")
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

    async def test_livekit_transfer_revokes_old_officer_before_requeue(self):
        room = await self._make_bridged_room("call_livekit_xfer", "officer_nakato")
        room.state.livekit_room = "ura-call_livekit_xfer"
        room.state.livekit_officer_identity = "officer-old"
        revoke = AsyncMock()

        with patch.object(desk.livekit, "revoke_participant", revoke):
            await desk.transfer("call_livekit_xfer", "officer_nakato", team="disputes")

        revoke.assert_awaited_once_with("ura-call_livekit_xfer", "officer-old")
        self.assertEqual(room.state.livekit_officer_identity, "")
        self.assertEqual(room.state.mode, "transferring")

    async def test_livekit_transfer_fails_closed_when_revocation_fails(self):
        room = await self._make_bridged_room("call_livekit_xfer_fail", "officer_nakato")
        room.state.livekit_room = "ura-call_livekit_xfer_fail"
        room.state.livekit_officer_identity = "officer-old"
        revoke = AsyncMock(side_effect=RuntimeError("API down"))

        with (
            patch.object(desk.livekit, "revoke_participant", revoke),
            pytest.raises(desk.DeskError) as raised,
        ):
            await desk.transfer("call_livekit_xfer_fail", "officer_nakato", team="disputes")

        self.assertEqual(raised.value.status, 503)
        self.assertEqual(room.state.mode, "bridged")
        self.assertEqual(room.state.livekit_officer_identity, "officer-old")
        self.assertIsNotNone(room.officer)

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

    # --- The caller's satisfaction, recorded by the officer at wrap-up ----------

    def _satisfaction(self, call_id: str):
        from app import service_ratings

        return service_ratings.find("call_officer_csat", call_id=call_id)

    async def test_wrapup_records_what_the_caller_said_about_the_service(self):
        create_call("call_csat_1", status="ended", user_id="taxpayer_c1", locale="lg")
        update_call("call_csat_1", target_team="domestic_taxes", topic="vat_refund")
        res = await desk.wrapup(
            "call_csat_1", "officer_nakato", outcome="resolved", note="Refund explained",
            caller_satisfaction=2, caller_satisfaction_status="answered", officer_name="Officer Nakato",
        )
        self.assertEqual(res["caller_satisfaction"], {"status": "answered", "score": 2, "recorded": True})
        row = self._satisfaction("call_csat_1")
        self.assertEqual((row["status"], row["score"], row["sentiment"]), ("answered", 2, "negative"))
        self.assertEqual((row["channel"], row["subject"]), ("call", "officer"))
        self.assertEqual((row["officer_id"], row["officer_name"], row["team"]), ("officer_nakato", "Officer Nakato", "domestic_taxes"))
        self.assertEqual((row["topic"], row["locale"], row["user_id"]), ("vat_refund", "lg", "taxpayer_c1"))
        # The officer's rating of the AI is a separate, optional thing.
        self.assertIsNone(get_call("call_csat_1")["officer_rating"])

    async def test_a_caller_who_did_not_say_is_recorded_as_no_answer(self):
        create_call("call_csat_2", status="ended")
        await desk.wrapup("call_csat_2", "officer_x", outcome="resolved", note="Done",
                          caller_satisfaction_status="not_said")
        row = self._satisfaction("call_csat_2")
        self.assertEqual((row["status"], row["score"]), ("no_answer", None))

    async def test_an_invalid_satisfaction_is_refused_before_anything_is_saved(self):
        create_call("call_csat_3", status="ended")
        for score, status in ((0, "answered"), (6, None), ("4", None), (True, None), (3, "not_said"), (None, "maybe")):
            with self.subTest(score=score, status=status), self.assertRaises(desk.DeskError) as raised:
                await desk.wrapup("call_csat_3", "officer_x", outcome="resolved", note="Done",
                                  caller_satisfaction=score, caller_satisfaction_status=status)
            self.assertEqual(raised.exception.status, 400)
        self.assertIsNone(get_call("call_csat_3")["wrapup_at"])
        self.assertIsNone(self._satisfaction("call_csat_3"))

    async def test_a_wrapup_without_it_records_nothing_and_a_second_keeps_the_first(self):
        create_call("call_csat_4", status="ended")
        await desk.wrapup("call_csat_4", "officer_x", outcome="resolved", note="Done")
        self.assertIsNone(self._satisfaction("call_csat_4"))
        await desk.wrapup("call_csat_4", "officer_x", outcome="resolved", note="Done", caller_satisfaction=5)
        res = await desk.wrapup("call_csat_4", "officer_x", outcome="resolved", note="Again", caller_satisfaction=1)
        self.assertFalse(res["caller_satisfaction"]["recorded"])
        self.assertEqual(self._satisfaction("call_csat_4")["score"], 5)

    async def test_the_wrapup_route_records_it_under_the_officers_name(self):
        await self._make_bridged_room("call_csat_route", "officer_route")
        client = TestClient(app)
        officer = {"Authorization": f"Bearer {make_dev_token('nakato@ura.go.ug', role='ura_staff')}"}
        bad = client.post("/v1/admin/calls/call_csat_route/wrapup", headers=officer,
                          json={"outcome": "resolved", "note": "ok", "caller_satisfaction": 9})
        self.assertEqual(bad.status_code, 400)
        res = client.post("/v1/admin/calls/call_csat_route/wrapup", headers=officer,
                          json={"outcome": "resolved", "note": "ok", "caller_satisfaction": 4,
                                "caller_satisfaction_status": "answered"})
        self.assertEqual(res.status_code, 200, res.text)
        row = self._satisfaction("call_csat_route")
        self.assertEqual(row["score"], 4)
        self.assertTrue(row["officer_name"].startswith("Officer "), row["officer_name"])

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

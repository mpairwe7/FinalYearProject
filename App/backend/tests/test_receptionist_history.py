"""Tests for Call Desk history, search, and caller history (Call Desk Phase 2)."""

from __future__ import annotations

import time
import unittest

from app import database as db
from app.auth.jwt_auth import make_dev_token
from app.main import app
from app.receptionist.store import (
    create_call,
    create_turn,
    get_caller_history,
    init_receptionist_schema,
    list_calls,
    update_call,
)
from fastapi.testclient import TestClient


class TestReceptionistHistory(unittest.TestCase):
    def setUp(self):
        db.init_db()
        init_receptionist_schema()
        db.execute("DELETE FROM voice_calls")
        db.execute("DELETE FROM voice_call_turns")

    def test_filter_by_outcome_language_topic(self):
        t0 = time.time()
        create_call("hist_1", status="ended", locale="lg", started_at=t0 - 100)
        update_call("hist_1", outcome="resolved", topic="vat_refund")

        create_call("hist_2", status="ended", locale="sw", started_at=t0 - 50)
        update_call("hist_2", outcome="callback", topic="tin_reg", needs_callback=1)

        create_call("hist_3", status="ai", locale="en", started_at=t0)
        update_call("hist_3", topic="customs")

        # By language
        calls_lg = list_calls(language="lg")
        self.assertEqual(len(calls_lg), 1)
        self.assertEqual(calls_lg[0]["call_id"], "hist_1")

        # By outcome
        calls_res = list_calls(outcome="resolved")
        self.assertEqual(len(calls_res), 1)
        self.assertEqual(calls_res[0]["call_id"], "hist_1")

        # By needs_callback
        calls_cb = list_calls(needs_callback=True)
        self.assertEqual(len(calls_cb), 1)
        self.assertEqual(calls_cb[0]["call_id"], "hist_2")

        # By topic
        calls_top = list_calls(topic="customs")
        self.assertEqual(len(calls_top), 1)
        self.assertEqual(calls_top[0]["call_id"], "hist_3")

    def test_search_q_over_turns_and_summary(self):
        t0 = time.time()
        create_call("search_turn", status="ended", started_at=t0 - 100)
        create_turn("search_turn", seq=1, speaker="caller", kind="utterance", text="I need help with my withholding tax return")

        create_call("search_sum", status="ended", started_at=t0 - 50)
        update_call("search_sum", summary_json={"summary": "Taxpayer asked about customs exemption on solar panels"})

        create_call("search_other", status="ended", started_at=t0)
        create_turn("search_other", seq=1, speaker="caller", kind="utterance", text="What is the rental income threshold?")

        # Match in turn text
        res_withholding = list_calls(q="withholding")
        self.assertEqual(len(res_withholding), 1)
        self.assertEqual(res_withholding[0]["call_id"], "search_turn")

        # Match in summary
        res_solar = list_calls(q="solar panels")
        self.assertEqual(len(res_solar), 1)
        self.assertEqual(res_solar[0]["call_id"], "search_sum")

    def test_pagination_and_total(self):
        t0 = time.time()
        for i in range(15):
            create_call(f"page_call_{i}", status="ended", started_at=t0 - (15 - i))

        calls_p1, total = list_calls(status="ended", limit=5, offset=0, return_total=True)
        self.assertEqual(total, 15)
        self.assertEqual(len(calls_p1), 5)

        calls_p2, total2 = list_calls(status="ended", limit=5, offset=5, return_total=True)
        self.assertEqual(total2, 15)
        self.assertEqual(len(calls_p2), 5)
        self.assertNotEqual(calls_p1[0]["call_id"], calls_p2[0]["call_id"])

    def test_caller_history_authenticated_user(self):
        t0 = time.time()
        # Same user has 3 calls
        create_call("user_c1", user_id="taxpayer_kato", started_at=t0 - 200)
        create_call("user_c2", user_id="taxpayer_kato", started_at=t0 - 100)
        create_call("user_c3", user_id="taxpayer_kato", started_at=t0)

        # And 1 ticket
        ticket = db.create_ticket(reason="e-tax inquiry", user_query="Need assistance with e-tax registration", user_id="taxpayer_kato")

        # Check caller history for call user_c3 (should return user_c1, user_c2 and ticket)
        hist = get_caller_history("user_c3")
        self.assertFalse(hist["anonymous"])
        self.assertEqual(hist["user_id"], "taxpayer_kato")
        call_ids = [c["call_id"] for c in hist["calls"]]
        self.assertIn("user_c1", call_ids)
        self.assertIn("user_c2", call_ids)
        self.assertNotIn("user_c3", call_ids)
        ticket_ids = [t["id"] for t in hist["tickets"]]
        self.assertIn(ticket["id"], ticket_ids)

    def test_caller_history_anonymous_user(self):
        create_call("anon_call_1", user_id="anon::12345")
        hist = get_caller_history("anon_call_1")
        self.assertTrue(hist["anonymous"])
        self.assertEqual(hist["calls"], [])
        self.assertEqual(hist["tickets"], [])

    def test_http_endpoint_list_calls_with_filters(self):
        create_call("api_hist_1", status="ended", locale="lg")
        update_call("api_hist_1", outcome="resolved")

        client = TestClient(app)
        token = make_dev_token("staff_user", role="ura_staff")
        headers = {"Authorization": f"Bearer {token}"}

        res = client.get("/v1/admin/calls?outcome=resolved&language=lg", headers=headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("calls", data)
        self.assertIn("total", data)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["calls"][0]["call_id"], "api_hist_1")

    def test_http_endpoint_caller_history(self):
        create_call("api_c1", user_id="taxpayer_sam", status="ended")
        create_call("api_c2", user_id="taxpayer_sam", status="ai")

        client = TestClient(app)
        token = make_dev_token("staff_user", role="ura_staff")
        headers = {"Authorization": f"Bearer {token}"}

        res = client.get("/v1/admin/calls/api_c2/caller-history", headers=headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data["anonymous"])
        self.assertEqual(data["user_id"], "taxpayer_sam")
        self.assertEqual(len(data["calls"]), 1)
        self.assertEqual(data["calls"][0]["call_id"], "api_c1")

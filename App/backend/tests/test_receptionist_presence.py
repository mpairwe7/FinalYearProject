"""Tests for staff officer presence (Call Desk Phase 2)."""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from app import database as db
from app.auth.jwt_auth import make_dev_token
from app.main import app
from app.receptionist import presence
from app.receptionist.hub import hub
from app.receptionist.store import init_receptionist_schema
from fastapi.testclient import TestClient


class TestOfficerPresence(unittest.TestCase):
    def setUp(self):
        db.init_db()
        init_receptionist_schema()
        db.execute("DELETE FROM officer_presence")
        hub._lobby.clear()

    def test_upsert_and_get_presence(self):
        p = presence.upsert_presence(
            "user_nakato",
            display_name="Officer Nakato",
            status="available",
            languages=["en", "lg"],
            teams=["taxpayer_accounts"],
        )
        self.assertEqual(p["user_id"], "user_nakato")
        self.assertEqual(p["display_name"], "Officer Nakato")
        self.assertEqual(p["status"], "available")
        self.assertEqual(p["languages"], ["en", "lg"])
        self.assertEqual(p["teams"], ["taxpayer_accounts"])

        fetched = presence.get_presence("user_nakato")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["display_name"], "Officer Nakato")
        self.assertEqual(fetched["status"], "available")

    def test_ttl_computed_offline(self):
        presence.upsert_presence("user_okello", display_name="Officer Okello", status="available")
        # Artificially age last_seen by 100 seconds
        old_time = time.time() - 100.0
        db.execute("UPDATE officer_presence SET last_seen = ? WHERE user_id = ?", (old_time, "user_okello"))

        fetched = presence.get_presence("user_okello")
        self.assertEqual(fetched["status"], "offline")
        self.assertEqual(fetched["raw_status"], "available")

    def test_lobby_event_emitted_only_on_change(self):
        published = []
        with patch("app.receptionist.presence.hub.publish_lobby", side_effect=lambda ev, data: published.append((ev, data))):
            # First insert: creates record -> event emitted
            presence.upsert_presence("user_event_test", display_name="Officer Test", status="available")
            self.assertEqual(len(published), 1)
            self.assertEqual(published[0][0], "officer.presence")
            self.assertEqual(published[0][1]["user_id"], "user_event_test")
            self.assertEqual(published[0][1]["status"], "available")

            # Heartbeat: same status and display_name -> NO new event
            presence.upsert_presence("user_event_test", display_name="Officer Test", status="available")
            self.assertEqual(len(published), 1)

            # Status change: busy -> event emitted
            presence.upsert_presence("user_event_test", status="busy")
            self.assertEqual(len(published), 2)
            self.assertEqual(published[1][1]["status"], "busy")

    def test_automatic_transitions(self):
        presence.upsert_presence("user_flow", display_name="Officer Flow", status="available")
        presence.set_on_call("user_flow", "call_123")
        self.assertEqual(presence.get_presence("user_flow")["status"], "on_call")

        presence.set_wrap_up("user_flow", "call_123")
        self.assertEqual(presence.get_presence("user_flow")["status"], "wrap_up")

        presence.finish_wrap_up("user_flow")
        self.assertEqual(presence.get_presence("user_flow")["status"], "available")

    def test_presence_board_endpoint(self):
        presence.upsert_presence("user_board", display_name="Officer Board", status="available")
        client = TestClient(app)
        token = make_dev_token("user_board", role="ura_staff")
        headers = {"Authorization": f"Bearer {token}"}

        res = client.get("/v1/admin/officers/presence", headers=headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("officers", data)
        self.assertIn("teams", data)
        officers = {o["user_id"]: o for o in data["officers"]}
        self.assertIn("user_board", officers)

    def test_update_my_presence_endpoint(self):
        client = TestClient(app)
        token = make_dev_token("user_me", role="ura_staff")
        headers = {"Authorization": f"Bearer {token}"}

        res = client.put(
            "/v1/admin/officers/me/presence",
            headers=headers,
            json={"status": "away", "display_name": "Officer Me", "languages": ["en", "sw"]},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "away")
        self.assertEqual(data["languages"], ["en", "sw"])

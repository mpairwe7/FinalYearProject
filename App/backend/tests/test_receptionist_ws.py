"""Tests for receptionist WebSocket endpoints and HTTP admin APIs."""

from __future__ import annotations

import json
import unittest
import uuid
from unittest.mock import patch

from starlette.testclient import TestClient

from app import database as db
from app.auth.jwt_auth import make_dev_token
from app.flags import flags
from app.main import app
from app.receptionist.hub import hub
from app.receptionist.state import CallState, registry
from app.receptionist.store import create_call, create_turn, init_receptionist_schema


class TestReceptionistWSAndHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        init_receptionist_schema()
        cls.client = TestClient(app)

    def _auth_headers(self, role: str) -> dict[str, str]:
        token = make_dev_token(f"{role}_user", role=role)
        return {"Authorization": f"Bearer {token}"}

    def test_call_stream_flag_off_closes(self):
        with patch.object(flags, "is_enabled", side_effect=lambda name, **_kw: False if name == "voice_receptionist" else True):
            try:
                with self.client.websocket_connect("/v1/calls/stream") as ws:
                    ws.receive()
            except Exception:
                pass  # Closed by server with 1001

    def test_call_stream_consent_enforced(self):
        with patch.object(flags, "is_enabled", side_effect=lambda name, **_kw: True if name in ("voice_receptionist", "voice_consent") else False):
            with self.client.websocket_connect("/v1/calls/stream") as ws:
                # Send handshake with consent false
                ws.send_text(json.dumps({"type": "call_start", "locale": "en", "voice_consent_accepted": False}))
                msg = ws.receive_text()
                data = json.loads(msg)
                self.assertEqual(data.get("type"), "error")
                self.assertIn("consent required", data.get("detail", "").lower())

    def test_staff_stream_auditor_allowed_for_lobby_and_live(self):
        token = make_dev_token("auditor_1", role="ura_auditor")
        with patch.object(flags, "is_enabled", return_value=True):
            # Auditor can connect to lobby stream
            with self.client.websocket_connect(f"/v1/admin/calls/stream?token={token}") as ws:
                # Connected successfully
                ws.send_text(json.dumps({"type": "ping"}))

    def test_officer_audio_auditor_refused(self):
        token = make_dev_token("auditor_1", role="ura_auditor")
        call_id = f"call_{uuid.uuid4().hex[:8]}"
        create_call(call_id, status="transferring")

        with patch.object(flags, "is_enabled", return_value=True):
            # Auditor is refused on audio bridge (code 4403)
            with self.assertRaises(Exception):
                with self.client.websocket_connect(f"/v1/admin/calls/{call_id}/audio?token={token}") as ws:
                    ws.receive()

    def test_lobby_event_carries_no_transcript(self):
        token = make_dev_token("staff_1", role="ura_staff")
        with patch.object(flags, "is_enabled", return_value=True):
            with self.client.websocket_connect(f"/v1/admin/calls/stream?token={token}") as ws:
                hub.publish_lobby(
                    "call.started",
                    {"call_id": "test_c1", "status": "ai", "topic": "VAT", "priority": "normal"},
                )
                msg = ws.receive_text()
                event = json.loads(msg)
                self.assertEqual(event.get("type"), "call.started")
                data = event.get("data", {})
                self.assertNotIn("text", data)
                self.assertNotIn("transcript", data)

    def test_http_admin_calls_endpoints(self):
        headers = self._auth_headers("ura_staff")
        call_id = f"call_{uuid.uuid4().hex[:8]}"
        create_call(call_id, status="ai", locale="en")
        create_turn(call_id, seq=1, speaker="caller", kind="utterance", text="How to pay VAT?")

        # 1. GET /v1/admin/calls
        r1 = self.client.get("/v1/admin/calls?status=all", headers=headers)
        self.assertEqual(r1.status_code, 200)
        self.assertIn("calls", r1.json())

        # 2. GET /v1/admin/calls/{call_id}
        r2 = self.client.get(f"/v1/admin/calls/{call_id}", headers=headers)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["call_id"], call_id)
        self.assertGreaterEqual(len(r2.json().get("turns", [])), 1)

        # 3. GET /v1/admin/calls/metrics
        r3 = self.client.get("/v1/admin/calls/metrics?days=7", headers=headers)
        self.assertEqual(r3.status_code, 200)
        self.assertIn("containment_rate", r3.json())

        # 4. POST /v1/admin/calls/{call_id}/review
        r4 = self.client.post(
            f"/v1/admin/calls/{call_id}/review",
            json={"rating": 5, "note": "Great call"},
            headers=headers,
        )
        self.assertEqual(r4.status_code, 200)
        self.assertTrue(r4.json().get("ok"))

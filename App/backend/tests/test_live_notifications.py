"""Unit tests for the live notification engine & transactional outbox (G14)."""

import os
import unittest
from unittest.mock import MagicMock, patch

from app import database as db
from app.notify import (
    configured_providers,
    dispatch,
    drain_outbox,
    is_live,
    retry_notification,
    send_notification_item,
    send_test_notification,
    _send_email_live,
    _send_sms_live,
    _send_webhook_live,
)
from app.reminders import Reminder


class LiveNotificationTests(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_live_detection_toggle(self):
        with patch.dict(os.environ, {"NOTIFICATION_LIVE": "false", "RESEND_API_KEY": "", "SMTP_HOST": "", "AFRICASTALKING_API_KEY": "", "TWILIO_ACCOUNT_SID": "", "WEBHOOK_NOTIFY_URL": ""}, clear=False):
            self.assertFalse(is_live())

        with patch.dict(os.environ, {"NOTIFICATION_LIVE": "true"}):
            self.assertTrue(is_live())

    def test_configured_providers_reporting(self):
        with patch.dict(os.environ, {"RESEND_API_KEY": "re_test_key_123", "AFRICASTALKING_API_KEY": "at_key", "AFRICASTALKING_USERNAME": "sandbox"}):  # pragma: allowlist secret
            provs = configured_providers()
            self.assertTrue(provs["email"]["configured"])
            self.assertEqual(provs["email"]["backend"], "resend")
            self.assertTrue(provs["sms"]["configured"])
            self.assertEqual(provs["sms"]["backend"], "africastalking")
            self.assertTrue(provs["in_app"]["configured"])

    def test_send_email_live_sandbox_and_resend(self):
        # Sandbox delivery
        res = _send_email_live("taxpayer@example.com", "VAT Due", "Pay VAT")
        self.assertTrue(res["ok"])
        self.assertIn("tx_email_", res["provider_msg_id"])
        self.assertEqual(res["status"], "delivered")

        # Mocked Resend delivery
        with patch.dict(os.environ, {"RESEND_API_KEY": "re_live_key"}):  # pragma: allowlist secret
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"id": "resend_msg_001"}
            with patch("httpx.post", return_value=mock_resp):
                res_resend = _send_email_live("test@ura.go.ug", "Subject", "Body")
                self.assertTrue(res_resend["ok"])
                self.assertEqual(res_resend["provider_msg_id"], "resend_msg_001")

    def test_send_sms_live_sandbox_and_africastalking(self):
        # Sandbox delivery
        res = _send_sms_live("+256701234567", "Hello from URA")
        self.assertTrue(res["ok"])
        self.assertIn("tx_sms_", res["provider_msg_id"])

        # Mocked Africa's Talking delivery
        with patch.dict(os.environ, {"AFRICASTALKING_API_KEY": "at_secret", "AFRICASTALKING_USERNAME": "sandbox"}):  # pragma: allowlist secret
            mock_resp = MagicMock()
            mock_resp.status_code = 201
            mock_resp.json.return_value = {"SMSMessageData": {"Recipients": [{"messageId": "AT_MSG_999"}]}}
            with patch("httpx.post", return_value=mock_resp):
                res_at = _send_sms_live("+256701234567", "Reminder message")
                self.assertTrue(res_at["ok"])
                self.assertEqual(res_at["provider_msg_id"], "AT_MSG_999")

    def test_send_webhook_hmac_signature(self):
        with patch.dict(os.environ, {"WEBHOOK_NOTIFY_URL": "https://hooks.ura.go.ug/test", "WEBHOOK_SECRET": "secret-key"}):  # pragma: allowlist secret
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            with patch("httpx.post", return_value=mock_resp) as mock_post:
                res = _send_webhook_live("https://hooks.ura.go.ug/test", {"event": "vat_deadline", "days": 3})
                self.assertTrue(res["ok"])
                self.assertTrue(mock_post.called)
                headers = mock_post.call_args[1]["headers"]
                self.assertIn("X-URA-Signature", headers)
                self.assertTrue(headers["X-URA-Signature"].startswith("sha256="))

    def test_outbox_enqueue_and_drain(self):
        # Enqueue item
        item = db.enqueue_notification(
            user_id="user-vat-001",
            channel="email",
            payload={"recipient": "user@ura.go.ug", "message": "Filing deadline tomorrow"},
            provider="live_sandbox",
            status="queued",
        )
        self.assertEqual(item["status"], "queued")
        nid = item["id"]

        # Drain outbox
        drain_res = drain_outbox(limit=10)
        self.assertTrue(drain_res["ok"])
        self.assertGreaterEqual(drain_res["processed"], 1)

        # Confirm status updated in db
        refreshed = db.get_notification_by_id(nid)
        self.assertIsNotNone(refreshed)
        self.assertEqual(refreshed["status"], "delivered")
        self.assertIsNotNone(refreshed.get("sent_at"))

    def test_send_test_notification_and_retry(self):
        res = send_test_notification(
            channel="email",
            recipient="officer@ura.go.ug",
            message="Test broadcast",
            subject="Test Ping",
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "delivered")
        nid = res["id"]

        # Retry notification
        retry_res = retry_notification(nid)
        self.assertTrue(retry_res["ok"])
        self.assertEqual(retry_res["status"], "delivered")


if __name__ == "__main__":
    unittest.main()

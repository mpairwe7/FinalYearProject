"""A live chat between a taxpayer and the officer who takes their case.

The flow the audit depends on: an officer takes the case (the taxpayer's chat
switches to them and the AI pauses), they exchange messages, the officer ends
the chat, the taxpayer is asked to rate it — once — and is handed back to the
assistant. These tests pin the thread and its order, the chat state the
taxpayer's chat follows, who may end a chat (officers only), that an ended
chat cannot be silently reopened, the rating, and that the taxpayer's erasure
reaches the thread and the rating.
"""

from __future__ import annotations

import os
import unittest
import unittest.mock as mock
import uuid

os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("SPEECH_ENABLED", "false")
os.environ.setdefault("QDRANT_ENABLED", "false")
os.environ.setdefault("ANALYTICS_BACKEND", "sqlite")
os.environ.setdefault("OTEL_ENABLED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import database as db  # noqa: E402
from app import officer_chat, service_ratings  # noqa: E402
from app.auth.jwt_auth import make_dev_token  # noqa: E402
from app.main import app  # noqa: E402


def setUpModule() -> None:
    db.init_db()


def _bearer(user: str, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_dev_token(user, role=role)}"}


OFFICER = _bearer("nakato@ura.go.ug", "ura_staff")
ADMIN = _bearer("chat-admin", "ura_admin")
AUDITOR = _bearer("chat-auditor", "ura_auditor")


class _Chat(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.taxpayer = f"taxpayer-{uuid.uuid4().hex[:8]}"
        self.ticket = db.create_ticket(
            reason="The VAT answer does not match my assessment notice.",
            user_query="Why was I assessed VAT twice?",
            conversation_id=f"conv-{uuid.uuid4().hex[:8]}",
            user_id=self.taxpayer,
            team="domestic_taxes",
            **self.ticket_extra(),
        )
        self.tid = self.ticket["id"]

    def ticket_extra(self) -> dict:
        return {}

    def take_case(self) -> None:
        r = self.client.patch(
            f"/v1/admin/tickets/{self.tid}", json={"assignee": "nakato@ura.go.ug", "status": "assigned"}, headers=OFFICER
        )
        self.assertEqual(r.status_code, 200, r.text)

    def officer_says(self, text: str) -> None:
        r = self.client.patch(f"/v1/admin/tickets/{self.tid}", json={"officer_reply": text}, headers=OFFICER)
        self.assertEqual(r.status_code, 200, r.text)

    def taxpayer_says(self, text: str):
        return self.client.post(f"/v1/escalate/{self.tid}/reply", json={"message": text})

    def thread(self, after_seq: int = 0) -> dict:
        r = self.client.get(f"/v1/escalate/{self.tid}/messages?after_seq={after_seq}")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def end(self, **body):
        return self.client.post(f"/v1/admin/tickets/{self.tid}/end-chat", json=body, headers=OFFICER)


class TaxpayerThreadTests(_Chat):
    def test_the_chat_waits_then_follows_the_officer_who_takes_the_case(self):
        body = self.thread()
        self.assertEqual((body["chat_state"], body["messages"]), ("waiting", []))
        self.take_case()
        body = self.thread()
        self.assertEqual(body["chat_state"], "officer")
        self.assertTrue(body["officer_name"].startswith("Officer "), body["officer_name"])

    def test_messages_arrive_in_order_and_after_seq_returns_only_newer_ones(self):
        self.take_case()
        self.officer_says("I can see the duplicate assessment. Which period?")
        r = self.taxpayer_says("March 2026.")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["seq"], 2)
        self.officer_says("Thank you — I have cancelled the second one.")
        body = self.thread()
        self.assertEqual([m["sender"] for m in body["messages"]], ["officer", "taxpayer", "officer"])
        self.assertEqual([m["seq"] for m in body["messages"]], [1, 2, 3])
        self.assertEqual(body["last_seq"], 3)
        newer = self.thread(after_seq=2)
        self.assertEqual([m["text"] for m in newer["messages"]], ["Thank you — I have cancelled the second one."])
        self.assertEqual(self.thread(after_seq=3)["messages"], [])

    def test_reading_the_officers_message_delivers_it(self):
        self.take_case()
        self.officer_says("Your refund was approved.")
        self.assertFalse(db.get_ticket(self.tid)["reply_delivered_at"])
        self.thread()
        self.assertTrue(db.get_ticket(self.tid)["reply_delivered_at"])

    def test_the_lookup_is_by_exact_id_only(self):
        self.assertEqual(self.client.get(f"/v1/escalate/{uuid.uuid4()}/messages").status_code, 404)
        # A reference or a search term is not an id: no fuzzy fallback here.
        self.assertEqual(self.client.get(f"/v1/escalate/TIC-{self.tid[:8].upper()}/messages").status_code, 422)

    def test_an_officers_message_reaches_the_taxpayer_in_their_language(self):
        db.update_ticket(self.tid, locale="lg")
        self.take_case()
        with mock.patch("app.service.localize_reply", return_value="Ssente zo zakkirizibwa."):
            self.officer_says("Your refund was approved.")
        msg = self.thread()["messages"][0]
        self.assertEqual(msg["text"], "Ssente zo zakkirizibwa.")
        staff = self.client.get(f"/v1/admin/tickets/{self.tid}", headers=OFFICER).json()
        self.assertEqual(staff["messages"][0]["text"], "Your refund was approved.")
        self.assertEqual(staff["messages"][0]["text_localized"], "Ssente zo zakkirizibwa.")

    def test_the_officer_and_the_auditor_read_the_thread_on_the_case(self):
        self.take_case()
        self.officer_says("Which period?")
        self.taxpayer_says("March 2026.")
        for who in (OFFICER, AUDITOR, ADMIN):
            body = self.client.get(f"/v1/admin/tickets/{self.tid}", headers=who).json()
            self.assertEqual([m["sender"] for m in body["messages"]], ["officer", "taxpayer"])
            self.assertEqual(body["chat"]["state"], "officer")
            self.assertIsNone(body["chat"]["rating"])


class CallTicketTests(_Chat):
    def ticket_extra(self) -> dict:
        return {"modality": "voice"}

    def test_a_calls_ticket_has_no_chat(self):
        self.assertEqual(self.thread()["chat_state"], "none")
        self.assertEqual(self.end().status_code, 409)


class EndChatTests(_Chat):
    def test_the_officer_ends_the_chat_and_the_taxpayer_is_asked_to_rate_it(self):
        self.take_case()
        with mock.patch("app.ticket_events.publish") as publish:
            r = self.end(closing_message="Glad I could help. Goodbye.", outcome="resolved")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["chat_state"], "ended")
        self.assertEqual(db.get_ticket(self.tid)["status"], "resolved")
        events = [c.args[0]["event"] for c in publish.call_args_list if c.args]
        self.assertIn("escalation.chat_ended", events)
        body = self.thread()
        self.assertEqual(body["chat_state"], "ended")
        self.assertEqual([m["sender"] for m in body["messages"]], ["officer", "system"])
        self.assertEqual(body["messages"][0]["text"], "Glad I could help. Goodbye.")
        self.assertEqual(body["messages"][-1]["text"], officer_chat.ENDED_MARKER)
        self.assertEqual(body["csat"], {"requested": True, "submitted": False})
        rating = service_ratings.find("chat_officer_csat", ticket_id=self.tid)
        self.assertEqual(rating["status"], "pending")
        self.assertTrue(rating["officer_name"].startswith("Officer "))
        self.assertEqual(rating["user_id"], self.taxpayer)

    def test_closing_without_resolving_is_recorded_as_closed(self):
        self.take_case()
        self.assertEqual(self.end(outcome="wontfix").status_code, 200)
        self.assertEqual(db.get_ticket(self.tid)["status"], "wontfix")
        self.assertEqual(self.thread()["chat_state"], "ended")

    def test_resolving_from_the_status_control_ends_the_chat_the_same_way(self):
        self.take_case()
        r = self.client.patch(
            f"/v1/admin/tickets/{self.tid}", json={"officer_reply": "All sorted.", "status": "resolved"}, headers=OFFICER
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["chat_state"], "ended")
        body = self.thread()
        self.assertEqual([m["sender"] for m in body["messages"]], ["officer", "system"])
        self.assertTrue(body["csat"]["requested"])
        self.assertEqual(db.get_ticket(self.tid)["officer_reply"], "All sorted.")

    def test_only_officers_end_a_chat(self):
        self.take_case()
        for who in (ADMIN, AUDITOR):
            r = self.client.post(f"/v1/admin/tickets/{self.tid}/end-chat", json={}, headers=who)
            self.assertEqual(r.status_code, 403, r.text)
        self.assertIn(self.client.post(f"/v1/admin/tickets/{self.tid}/end-chat", json={}).status_code, (401, 403, 503))
        self.assertEqual(self.thread()["chat_state"], "officer")

    def test_an_ended_chat_cannot_be_ended_again_or_reopened_by_a_message(self):
        self.take_case()
        self.assertEqual(self.end().status_code, 200)
        self.assertEqual(self.end().status_code, 409)
        r = self.taxpayer_says("One more thing…")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(db.get_ticket(self.tid)["status"], "resolved")

    def test_an_unknown_ticket_or_outcome_is_refused(self):
        r = self.client.post(f"/v1/admin/tickets/{uuid.uuid4()}/end-chat", json={}, headers=OFFICER)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.end(outcome="deleted").status_code, 422)

    def test_a_reopened_chat_is_not_rated_twice(self):
        self.take_case()
        self.end()
        self.client.patch(f"/v1/admin/tickets/{self.tid}", json={"status": "assigned"}, headers=OFFICER)
        self.assertEqual(self.thread()["chat_state"], "officer")
        self.assertEqual(self.end().status_code, 200)
        rows = db.query_all("SELECT id FROM service_ratings WHERE ticket_id = ?", (self.tid,))
        self.assertEqual(len(rows), 1)


class ChatRatingTests(_Chat):
    def rate(self, score, comment: str = ""):
        return self.client.post(f"/v1/escalate/{self.tid}/csat", json={"score": score, "comment": comment})

    def test_a_chat_is_rated_only_after_it_ends_and_only_once(self):
        self.take_case()
        self.assertEqual(self.rate(4).status_code, 409)
        self.end()
        r = self.rate(2, comment="Took too long. Call me on 0772123456.")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "answered")
        self.assertEqual(self.rate(5).status_code, 409)
        rating = service_ratings.find("chat_officer_csat", ticket_id=self.tid)
        self.assertEqual((rating["status"], rating["score"], rating["sentiment"]), ("answered", 2, "negative"))
        self.assertNotIn("0772123456", rating["comment"])
        self.assertEqual(self.thread()["csat"], {"requested": True, "submitted": True})
        staff = self.client.get(f"/v1/admin/tickets/{self.tid}", headers=AUDITOR).json()
        self.assertEqual(staff["chat"]["rating"]["score"], 2)

    def test_the_taxpayer_may_skip_the_rating(self):
        self.take_case()
        self.end()
        r = self.rate(None, comment="ignored")
        self.assertEqual(r.json()["status"], "skipped")
        rating = service_ratings.find("chat_officer_csat", ticket_id=self.tid)
        self.assertEqual((rating["status"], rating["score"], rating["comment"]), ("skipped", None, ""))

    def test_a_score_is_one_to_five(self):
        self.take_case()
        self.end()
        self.assertEqual(self.rate(6).status_code, 422)
        self.assertEqual(self.rate(0).status_code, 422)


class PrivacyTests(_Chat):
    def test_the_taxpayers_export_and_erasure_reach_the_thread_and_the_rating(self):
        self.take_case()
        self.officer_says("Which period?")
        self.taxpayer_says("March 2026.")
        self.end()
        self.client.post(f"/v1/escalate/{self.tid}/csat", json={"score": 5})
        exported = db.export_user_data(self.taxpayer, external_id=self.taxpayer)
        self.assertEqual([m["sender"] for m in exported["ticket_messages"]], ["officer", "taxpayer", "system"])
        self.assertEqual([r["score"] for r in exported["service_ratings"]], [5])
        counts = db.delete_user_cascade(self.taxpayer, external_id=self.taxpayer)
        self.assertEqual(counts["ticket_messages"], 3)
        self.assertEqual(counts["service_ratings"], 1)
        self.assertEqual(officer_chat.list_messages(self.tid), [])
        self.assertIsNone(service_ratings.find("chat_officer_csat", ticket_id=self.tid))


if __name__ == "__main__":
    unittest.main()

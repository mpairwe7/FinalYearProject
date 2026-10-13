"""The quality audit's "Needs review" queue, and the decisions recorded on it.

One queue across the four streams, holding only what calls for a person to
look: an AI chat that went wrong or was never found helpful, or a 👎 with a
comment; an officer chat or call rated 1 or 2; a caller who said the AI did
not help. An administrator or auditor marks an item Reviewed or Action needed
with a note — an audit note that changes no case, so an auditor may make it,
and each one is on the audit ledger. Officers do not audit their own service.
"""

from __future__ import annotations

import os
import time
import unittest
import uuid
from unittest.mock import patch

os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("SPEECH_ENABLED", "false")
os.environ.setdefault("QDRANT_ENABLED", "false")
os.environ.setdefault("ANALYTICS_BACKEND", "sqlite")
os.environ.setdefault("OTEL_ENABLED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import audit_reviews  # noqa: E402
from app import database as db  # noqa: E402
from app import service_ratings  # noqa: E402
from app.auth.jwt_auth import make_dev_token  # noqa: E402
from app.main import app  # noqa: E402


def setUpModule() -> None:
    db.init_db()


def _bearer(user: str, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_dev_token(user, role=role)}"}


ADMIN = _bearer("qa-admin@ura.go.ug", "ura_admin")
AUDITOR = _bearer("qa-auditor@ura.go.ug", "ura_auditor")
OFFICER = _bearer("qa-officer@ura.go.ug", "ura_staff")


def _conversation(ratings: list[str], *, comment: str = "") -> str:
    """A rated conversation: one feedback row per answer, in order."""
    conv = f"conv-nr-{uuid.uuid4().hex[:10]}"
    for n, rating in enumerate(ratings, start=1):
        db.save_feedback(
            message_id=f"m-{conv}-{n}",
            rating=rating,
            comment=comment if (rating == "down" and n == len(ratings)) else "",
            user_query=f"Question {n} about VAT",
            bot_reply=f"Answer {n}",
            conversation_id=conv,
            turn_id=f"t-{conv}-{n}",
            turn_index=n,
            topic_tag="vat",
            locale="en",
        )
    return conv


def _rating(source: str, *, score: int | None = None, sentiment: str = "", officer: str = "Officer Okello") -> dict:
    case = uuid.uuid4().hex[:12]
    keys = {"ticket_id": f"tkt-{case}"} if source == "chat_officer_csat" else {"call_id": f"call-{case}"}
    service_ratings.request(
        source, channel="call" if "call" in source else "chat", subject="ai" if "ai" in source else "officer",
        officer_id="off-x", officer_name=officer, topic="vat", **keys,
    )
    service_ratings.answer(source, status="answered", score=score, sentiment=sentiment, comment="Not good.", **keys)
    return service_ratings.find(source, **keys)


def _queue(**params) -> dict:
    return audit_reviews.needs_review(1, status=params.pop("status", "all"), limit=100, **params)


def _ids(queue: dict) -> set[str]:
    return {i["item_id"] for i in queue["items"]}


class QueueTests(unittest.TestCase):
    def test_it_holds_the_ai_chats_that_need_a_look_and_nothing_else(self):
        went_wrong = _conversation(["up", "up", "down"])
        never = _conversation(["down", "down"])
        fine = _conversation(["up", "up"])
        recovered = _conversation(["down", "up"])
        ids = _ids(_queue(stream="ai_chat"))
        self.assertTrue({went_wrong, never}.issubset(ids))
        self.assertNotIn(fine, ids)
        self.assertNotIn(recovered, ids)  # recovered, and nobody wrote anything
        item = next(i for i in _queue(stream="ai_chat")["items"] if i["item_id"] == went_wrong)
        self.assertEqual(item["title"], "AI chat went wrong at answer 3")
        self.assertEqual(item["quote"], "Question 3 about VAT")
        self.assertEqual(item["link"], {"tab": "ai-chat", "param": "conversation", "value": went_wrong})
        # A 👎 with a comment is in the queue whatever the pattern, quoting the comment.
        thumbs = _conversation(["up", "down", "up", "down"], comment="Still wrong")
        item = next(i for i in _queue(stream="ai_chat")["items"] if i["item_id"] == thumbs)
        self.assertEqual(item["comment"], "Still wrong")

    def test_it_holds_the_low_ratings_of_every_other_stream(self):
        chat_low = _rating("chat_officer_csat", score=2)
        chat_ok = _rating("chat_officer_csat", score=4)
        call_low = _rating("call_officer_csat", score=1, officer="Officer Nakato")
        ai_low = _rating("call_ai_review_transfer", sentiment="negative")
        ai_ok = _rating("call_ai_review_end", sentiment="positive")
        items = {i["item_id"]: i for i in _queue()["items"]}
        self.assertTrue({chat_low["id"], call_low["id"], ai_low["id"]}.issubset(items))
        self.assertNotIn(chat_ok["id"], items)
        self.assertNotIn(ai_ok["id"], items)
        self.assertEqual(items[chat_low["id"]]["title"], "Officer chat rated 2/5 — Officer Okello")
        self.assertEqual(items[chat_low["id"]]["link"], {"tab": "officer-chat", "param": "ticket", "value": chat_low["ticket_id"]})
        self.assertEqual(items[call_low["id"]]["title"], "Caller satisfaction 1/5 — Officer Nakato")
        self.assertEqual(items[call_low["id"]]["link"]["tab"], "officer-calls")
        self.assertEqual(items[ai_low["id"]]["title"], "Caller said the AI didn't help (while being transferred)")
        self.assertEqual(items[ai_low["id"]]["stream"], "ai_call")

    def test_a_decision_moves_an_item_out_of_open_and_reopening_brings_it_back(self):
        low = _rating("call_officer_csat", score=2)
        self.assertIn(low["id"], _ids(_queue(status="open")))
        audit_reviews.set_review("rating", low["id"], status="action_needed", note="Coach on refunds", reviewer_name="Auditor A")
        self.assertNotIn(low["id"], _ids(_queue(status="open")))
        decided = next(i for i in _queue(status="action_needed")["items"] if i["item_id"] == low["id"])
        self.assertEqual((decided["review"]["status"], decided["review"]["note"]), ("action_needed", "Coach on refunds"))
        audit_reviews.set_review("rating", low["id"], status="reviewed", note="Done", reviewer_name="Auditor B")
        self.assertEqual(audit_reviews.get_review("rating", low["id"])["reviewer_name"], "Auditor B")
        self.assertIsNone(audit_reviews.set_review("rating", low["id"], status="open"))
        self.assertIn(low["id"], _ids(_queue(status="open")))

    def test_the_note_is_redacted(self):
        low = _rating("chat_officer_csat", score=1)
        review = audit_reviews.set_review("rating", low["id"], status="reviewed", note="Caller on 0772123456 was upset")
        self.assertNotIn("0772123456", review["note"])

    def test_decisions_on_items_that_are_gone_are_purged(self):
        low = _rating("chat_officer_csat", score=1)
        audit_reviews.set_review("rating", low["id"], status="reviewed")
        db.execute("DELETE FROM service_ratings WHERE id = ?", (low["id"],))
        stale = _rating("chat_officer_csat", score=2)
        audit_reviews.set_review("rating", stale["id"], status="reviewed")
        db.execute("UPDATE audit_reviews SET updated_at = ? WHERE item_id = ?", (time.time() - 200 * 86400, stale["id"]))
        audit_reviews.purge()
        self.assertIsNone(audit_reviews.get_review("rating", low["id"]))
        self.assertIsNone(audit_reviews.get_review("rating", stale["id"]))


class QueueRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_administrators_and_auditors_read_the_queue_and_officers_do_not(self):
        conv = _conversation(["up", "down"])
        for who in (ADMIN, AUDITOR):
            r = self.client.get("/v1/admin/quality/needs-review?days=1&status=open&stream=ai_chat&limit=100", headers=who)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIn(conv, [i["item_id"] for i in r.json()["items"]])
            self.assertIn("open", r.json()["counts"])
        self.assertEqual(self.client.get("/v1/admin/quality/needs-review", headers=OFFICER).status_code, 403)
        self.assertIn(self.client.get("/v1/admin/quality/needs-review").status_code, (401, 403, 503))

    def test_a_bad_filter_is_422(self):
        self.assertEqual(self.client.get("/v1/admin/quality/needs-review?status=done", headers=ADMIN).status_code, 422)
        self.assertEqual(self.client.get("/v1/admin/quality/needs-review?stream=everything", headers=ADMIN).status_code, 422)

    def test_the_summary_counts_what_is_waiting(self):
        _rating("chat_officer_csat", score=1)
        body = self.client.get("/v1/admin/quality/summary?days=1", headers=ADMIN).json()
        self.assertGreaterEqual(body["needs_review"]["open"], 1)
        self.assertIn("action_needed", body["needs_review"])


class ReviewRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def put(self, item_type: str, item_id: str, body: dict, who=ADMIN):
        return self.client.put(f"/v1/admin/quality/reviews/{item_type}/{item_id}", json=body, headers=who)

    def test_an_auditor_marks_an_item_and_it_is_on_the_ledger(self):
        conv = _conversation(["up", "up", "down"])
        with patch("app.main._audit_staff_action") as audited:
            r = self.put("ai_chat", conv, {"status": "action_needed", "note": "Sole proprietors can register"}, AUDITOR)
        self.assertEqual(r.status_code, 200, r.text)
        review = r.json()["review"]
        self.assertEqual(review["status"], "action_needed")
        self.assertEqual(review["reviewer_name"], "qa-auditor@ura.go.ug")  # a person, not "Officer …"
        audited.assert_called_once()
        event, payload = audited.call_args.args[1], audited.call_args.args[2]
        self.assertEqual(event, "staff.quality_reviewed")
        self.assertEqual(payload, {"item_type": "ai_chat", "item_id": conv, "status": "action_needed", "note_chars": 29})

    def test_an_administrator_reviews_and_reopens(self):
        low = _rating("call_officer_csat", score=2)
        self.assertEqual(self.put("rating", low["id"], {"status": "reviewed"}).status_code, 200)
        r = self.put("rating", low["id"], {"status": "open"})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["review"])

    def test_officers_do_not_review_their_own_service(self):
        low = _rating("chat_officer_csat", score=1)
        self.assertEqual(self.put("rating", low["id"], {"status": "reviewed"}, OFFICER).status_code, 403)
        self.assertIsNone(audit_reviews.get_review("rating", low["id"]))

    def test_an_unknown_item_type_status_or_item_is_refused(self):
        self.assertEqual(self.put("ticket", "abc", {"status": "reviewed"}).status_code, 422)
        self.assertEqual(self.put("rating", "abc", {"status": "approved"}).status_code, 422)
        self.assertEqual(self.put("rating", uuid.uuid4().hex, {"status": "reviewed"}).status_code, 404)
        self.assertEqual(self.put("ai_chat", "conv-nobody", {"status": "reviewed"}).status_code, 404)

    def test_an_unlinked_rating_is_reviewed_by_its_key(self):
        mid = f"m-unlinked-{uuid.uuid4().hex[:8]}"
        db.save_feedback(message_id=mid, rating="down", comment="wrong", user_query="q", bot_reply="a")
        self.assertEqual(self.put("ai_chat", f"msg:{mid}", {"status": "reviewed"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()

"""The administrator's audit of the AI's chat answers: where a conversation went wrong.

A 👍 then a 👎 on the follow-up is the case the audit exists for: the
conversation started well and broke at the second answer. These tests pin the
pattern and break point (``quality_audit.classify``), the link from a rating
to the answer it rates (``turn_id`` from the chat response, copied from the
conversation log), anonymous ratings, and who may read the audit.
"""

from __future__ import annotations

import os
import time
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
from app import quality_audit as qa  # noqa: E402
from app.auth.jwt_auth import make_dev_token  # noqa: E402
from app.main import app  # noqa: E402


def setUpModule() -> None:
    db.init_db()


def _bearer(user: str, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_dev_token(user, role=role)}"}


ADMIN = _bearer("audit-admin", "ura_admin")
AUDITOR = _bearer("audit-auditor", "ura_auditor")
OFFICER = _bearer("audit-officer", "ura_staff")


class ClassifyTests(unittest.TestCase):
    def test_a_helpful_answer_then_an_unhelpful_follow_up_went_wrong_at_the_follow_up(self):
        self.assertEqual(qa.classify(["up", "down"]), ("went_wrong", 1))

    def test_it_breaks_at_the_first_unhelpful_answer_after_a_helpful_one(self):
        self.assertEqual(qa.classify(["down", "up", "up", "down", "down"]), ("went_wrong", 3))

    def test_a_conversation_that_ends_helpful_recovered(self):
        self.assertEqual(qa.classify(["up", "down", "up"]), ("recovered", 1))
        self.assertEqual(qa.classify(["down", "up"]), ("recovered", 0))

    def test_never_helpful_breaks_at_its_first_answer(self):
        self.assertEqual(qa.classify(["down"]), ("never_helpful", 0))
        self.assertEqual(qa.classify(["down", "down"]), ("never_helpful", 0))

    def test_all_helpful_has_no_break(self):
        self.assertEqual(qa.classify(["up"]), ("all_helpful", None))
        self.assertEqual(qa.classify(["up", "up", "up"]), ("all_helpful", None))
        self.assertEqual(qa.classify([]), ("all_helpful", None))


class SummaryTests(unittest.TestCase):
    def rows(self) -> list[dict]:
        now = time.time()

        def r(conv: str, n: int, rating: str, **extra) -> dict:
            return {
                "id": f"{conv}-{n}",
                "message_id": f"m-{conv}-{n}",
                "conversation_id": conv,
                "turn_id": f"t-{conv}-{n}",
                "turn_index": n,
                "rating": rating,
                "user_query": f"question {n}",
                "created_at": now - 100 + n,
                **extra,
            }

        return [
            r("c1", 1, "up", topic_tag="vat"),
            r("c1", 2, "down", topic_tag="vat", comment="wrong rate"),
            r("c2", 1, "up", topic_tag="paye"),
            r("c2", 2, "up", topic_tag="paye"),
            r("c3", 1, "down", topic_tag="vat"),
        ]

    def test_follow_up_answers_are_measured_apart_from_first_answers(self):
        s = qa.summarise(self.rows())
        positions = {p["position"]: p for p in s["by_position"]}
        self.assertEqual((positions["1"]["rated"], positions["1"]["helpful_pct"]), (3, 66.7))
        self.assertEqual((positions["2"]["rated"], positions["2"]["helpful_pct"]), (2, 50.0))
        self.assertEqual(positions["5+"]["rated"], 0)

    def test_patterns_and_where_conversations_broke(self):
        s = qa.summarise(self.rows())
        self.assertEqual(s["patterns"], {"went_wrong": 1, "never_helpful": 1, "recovered": 0, "all_helpful": 1})
        self.assertEqual(s["median_break_answer"], 2)
        self.assertEqual((s["rated_answers"], s["helpful"], s["helpful_pct"]), (5, 3, 60.0))
        self.assertEqual(s["comments"], 1)
        # The topic with the most unhelpful answers comes first.
        self.assertEqual(s["by_topic"][0]["key"], "vat")

    def test_rating_one_answer_twice_counts_once_the_latest(self):
        rows = self.rows()
        again = {**rows[1], "id": "c1-2-again", "rating": "up", "created_at": rows[1]["created_at"] + 5}
        kept = qa.latest_per_answer([*rows, again])
        self.assertEqual(len(kept), 5)
        self.assertEqual(qa.conversation_row("c1", [r for r in kept if r["conversation_id"] == "c1"])["pattern"],
                         "all_helpful")

    def test_a_rating_from_before_the_link_is_its_own_answer(self):
        legacy = {"id": "old", "message_id": "client-123", "rating": "down", "created_at": time.time()}
        groups = qa.group_conversations([legacy])
        row = qa.conversation_row(*next(iter(groups.items())))
        self.assertFalse(row["linked"])
        self.assertIsNone(row["conversation_id"])


class _Api(unittest.TestCase):
    def setUp(self) -> None:
        model = mock.MagicMock(name="stub_chat_model")
        model.classify.return_value = {"predictions": [], "processing_time_ms": 1.0}
        app.state.model = model
        app.state.speech = None
        self.client = TestClient(app)
        self.conv = f"conv-audit-{uuid.uuid4().hex[:10]}"

    def log_turn(self, question: str, reply: str, *, sources: str = '["vat-act.pdf"]') -> str:
        turn_id = uuid.uuid4().hex
        db.log_conversation(
            session_id="chat-session",
            conversation_id=self.conv,
            user_message=question,
            bot_reply=reply,
            sources=sources,
            confidence=0.7,
            topic_tag="vat",
            locale="en",
            row_id=turn_id,
        )
        time.sleep(0.002)  # distinct created_at, so answer order is unambiguous
        return turn_id

    def rate(self, turn_id: str, rating: str, *, headers: dict | None = None, **body) -> dict:
        payload = {
            "message_id": f"m-{uuid.uuid4().hex[:10]}",
            "rating": rating,
            "conversation_id": self.conv,
            "turn_id": turn_id,
            "user_query": "what the client says it asked",
            "bot_reply": "what the client says it was told",
            **body,
        }
        r = self.client.post("/v1/feedback", json=payload, headers=headers or {})
        self.assertEqual(r.status_code, 200, r.text)
        return {**r.json(), "message_id": payload["message_id"]}

    def stored(self, feedback_id: str) -> dict:
        return db.query_one("SELECT * FROM feedback WHERE id = ?", (feedback_id,)) or {}


class FeedbackLinkTests(_Api):
    def test_the_chat_response_names_the_row_its_answer_is_logged_under(self):
        app.state.model.generate.return_value = {
            "reply": "The standard VAT rate is 18%.",
            "sources": ["vat.pdf"],
            "citations": [],
            "retrieval_mode": "hybrid",
            "conversation_id": self.conv,
            "locale": "en",
        }
        r = self.client.post("/v1/chat", json={"message": "What is the VAT rate?", "conversation_id": self.conv})
        self.assertEqual(r.status_code, 200, r.text)
        turn_id = r.json()["turn_id"]
        row = db.query_one("SELECT conversation_id, user_message FROM conversations WHERE id = ?", (turn_id,))
        self.assertEqual(row["conversation_id"], self.conv)

    def test_an_anonymous_rating_is_kept_without_a_rater_and_linked_to_its_answer(self):
        self.log_turn("What is the VAT rate?", "18%.")
        second = self.log_turn("Does it apply to exports?", "Exports are zero-rated.")
        fb = self.stored(self.rate(second, "down")["id"])
        self.assertEqual(fb["user_id"], "")
        self.assertEqual((fb["conversation_id"], fb["turn_id"], fb["turn_index"]), (self.conv, second, 2))
        # The logged question and reply, not the client's copy of them.
        self.assertEqual((fb["user_query"], fb["bot_reply"]), ("Does it apply to exports?", "Exports are zero-rated."))
        self.assertEqual((fb["topic_tag"], fb["locale"], fb["sources_json"]), ("vat", "en", '["vat-act.pdf"]'))

    def test_a_turn_from_another_conversation_is_not_linked(self):
        turn = self.log_turn("What is the VAT rate?", "18%.")
        fb = self.stored(self.rate(turn, "up", conversation_id="conv-somebody-else")["id"])
        self.assertEqual(fb["turn_id"], "")
        self.assertEqual(fb["user_query"], "what the client says it asked")

    def test_a_signed_in_rater_without_analytics_consent_is_not_recorded(self):
        turn = self.log_turn("What is the VAT rate?", "18%.")
        headers = _bearer(f"taxpayer-{uuid.uuid4().hex[:8]}", "verified_taxpayer")
        fb = self.stored(self.rate(turn, "up", headers=headers)["id"])
        self.assertEqual(fb["user_id"], "")

    def test_an_anonymous_rating_takes_one_comment(self):
        turn = self.log_turn("What is the VAT rate?", "18%.")
        mid = self.rate(turn, "down")["message_id"]
        first = self.client.patch(f"/v1/feedback/{mid}/comment", json={"comment": "it was 16% last year?"})
        self.assertEqual(first.status_code, 200, first.text)
        again = self.client.patch(f"/v1/feedback/{mid}/comment", json={"comment": "changed my mind"})
        self.assertEqual(again.status_code, 404)


class AuditRouteTests(_Api):
    def went_wrong_at_the_third_answer(self) -> list[str]:
        turns = [
            self.log_turn("How do I register for VAT?", "Apply on the eTax portal."),
            self.log_turn("What documents do I need?", "Your TIN and certificate of incorporation."),
            self.log_turn("What if I am a sole proprietor?", "Sole proprietors cannot register."),
        ]
        self.rate(turns[0], "up")
        self.rate(turns[1], "up")
        self.rate(turns[2], "down", comment="That is wrong.")
        return turns

    def test_the_audit_finds_where_the_conversation_went_wrong(self):
        self.went_wrong_at_the_third_answer()
        r = self.client.get("/v1/admin/quality/ai-chat?days=1&pattern=went_wrong&limit=100", headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        mine = next(c for c in r.json()["page"]["conversations"] if c["conversation_id"] == self.conv)
        self.assertEqual(mine["pattern"], "went_wrong")
        self.assertEqual(mine["break_answer"], 3)
        self.assertEqual(mine["break_question"], "What if I am a sole proprietor?")
        self.assertTrue(mine["helpful_before_break"])
        self.assertEqual([x["rating"] for x in mine["ratings"]], ["up", "up", "down"])
        summary = r.json()["summary"]
        self.assertGreaterEqual(summary["patterns"]["went_wrong"], 1)
        self.assertEqual([p["position"] for p in summary["by_position"]], ["1", "2", "3", "4", "5+"])

    def test_the_timeline_marks_the_break_point_answer_by_answer(self):
        self.went_wrong_at_the_third_answer()
        r = self.client.get(f"/v1/admin/quality/ai-chat/{self.conv}", headers=AUDITOR)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual((body["pattern"], body["break_answer"], body["transcript_complete"]), ("went_wrong", 3, True))
        self.assertEqual([a["answer"] for a in body["answers"]], [1, 2, 3])
        self.assertEqual([a["is_break"] for a in body["answers"]], [False, False, True])
        self.assertEqual(body["answers"][2]["comment"], "That is wrong.")
        self.assertEqual(body["answers"][0]["sources"], ["vat-act.pdf"])

    def test_after_the_transcript_expires_the_rated_answers_remain(self):
        self.went_wrong_at_the_third_answer()
        db.execute("DELETE FROM conversations WHERE conversation_id = ?", (self.conv,))
        body = self.client.get(f"/v1/admin/quality/ai-chat/{self.conv}", headers=ADMIN).json()
        self.assertFalse(body["transcript_complete"])
        self.assertEqual([a["question"] for a in body["answers"]][-1], "What if I am a sole proprietor?")
        self.assertEqual(body["break_answer"], 3)

    def test_the_summary_reports_this_period_and_the_one_before(self):
        self.went_wrong_at_the_third_answer()
        body = self.client.get("/v1/admin/quality/summary?days=7", headers=ADMIN).json()
        ai = body["ai_chat"]
        self.assertGreaterEqual(ai["rated_answers"], 3)
        self.assertIn("helpful_pct", ai["previous"])

    def test_only_administrators_and_auditors_read_the_audit(self):
        self.went_wrong_at_the_third_answer()
        for path in ("/v1/admin/quality/summary", "/v1/admin/quality/ai-chat", f"/v1/admin/quality/ai-chat/{self.conv}"):
            self.assertIn(self.client.get(path).status_code, (401, 403, 503), path)
            # Officers give the service; auditing it is not their job.
            self.assertEqual(self.client.get(path, headers=OFFICER).status_code, 403, path)
            self.assertEqual(self.client.get(path, headers=AUDITOR).status_code, 200, path)

    def test_an_unknown_conversation_is_404_and_a_bad_filter_422(self):
        self.assertEqual(self.client.get("/v1/admin/quality/ai-chat/conv-nobody", headers=ADMIN).status_code, 404)
        self.assertEqual(self.client.get("/v1/admin/quality/ai-chat?pattern=awful", headers=ADMIN).status_code, 422)


if __name__ == "__main__":
    unittest.main()

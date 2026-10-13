"""Satisfaction ratings of the service a taxpayer got, as the administrator audits them.

One table holds every stream (officer chats now; calls in the next phase).
A row is written when the rating is *asked for*, so the response rate is a
count. These tests pin the figures the audit shows — average, CSAT % (the
share of 4–5), response rate, low ratings, the per-officer table — that a
rating is asked once and answered once, and who may read the audit.
"""

from __future__ import annotations

import os
import time
import unittest
import uuid

os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("SPEECH_ENABLED", "false")
os.environ.setdefault("QDRANT_ENABLED", "false")
os.environ.setdefault("ANALYTICS_BACKEND", "sqlite")
os.environ.setdefault("OTEL_ENABLED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import database as db  # noqa: E402
from app import service_ratings as sr  # noqa: E402
from app.auth.jwt_auth import make_dev_token  # noqa: E402
from app.main import app  # noqa: E402


def setUpModule() -> None:
    db.init_db()


def _bearer(user: str, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_dev_token(user, role=role)}"}


ADMIN = _bearer("ratings-admin", "ura_admin")
AUDITOR = _bearer("ratings-auditor", "ura_auditor")
OFFICER = _bearer("ratings-officer", "ura_staff")


def _row(score=None, status="answered", officer="off-1", name="Officer Nakato", created_at=None):
    return {
        "status": status,
        "score": score,
        "sentiment": sr.sentiment_for_score(score),
        "officer_id": officer,
        "officer_name": name,
        "created_at": created_at or time.time(),
    }


class SummariseTests(unittest.TestCase):
    def test_average_csat_response_rate_and_low_ratings(self):
        rows = [_row(5), _row(4), _row(2), _row(1), _row(status="skipped"), _row(status="pending")]
        s = sr.summarise(rows)
        self.assertEqual((s["asked"], s["answered"], s["skipped"], s["pending"]), (6, 4, 1, 1))
        self.assertEqual(s["response_rate"], 66.7)
        self.assertEqual(s["average"], 3.0)
        self.assertEqual(s["csat_pct"], 50.0)  # two of four scored 4 or 5
        self.assertEqual(s["low"], 2)
        self.assertEqual(s["distribution"], {"1": 1, "2": 1, "3": 0, "4": 1, "5": 1})
        self.assertEqual(s["sentiment"]["negative"], 2)

    def test_the_officer_table_leads_with_the_lowest_average(self):
        rows = [
            _row(5, officer="a", name="Officer A"),
            _row(4, officer="a", name="Officer A"),
            _row(2, officer="b", name="Officer B"),
            _row(status="skipped", officer="b", name="Officer B"),
        ]
        table = sr.summarise(rows)["by_officer"]
        self.assertEqual([o["officer_name"] for o in table], ["Officer B", "Officer A"])
        self.assertEqual((table[0]["handled"], table[0]["rated"], table[0]["average"], table[0]["low"]), (2, 1, 2.0, 1))
        self.assertEqual(table[1]["csat_pct"], 100.0)

    def test_nothing_rated_is_no_figure_not_zero(self):
        s = sr.summarise([_row(status="pending")])
        self.assertIsNone(s["average"])
        self.assertIsNone(s["csat_pct"])
        self.assertEqual(s["response_rate"], 0.0)

    def test_sentiment_bands(self):
        self.assertEqual([sr.sentiment_for_score(n) for n in (1, 2, 3, 4, 5)],
                         ["negative", "negative", "neutral", "positive", "positive"])
        self.assertEqual(sr.sentiment_for_score(None), "")


class StoreTests(unittest.TestCase):
    def ask(self, **extra):
        ticket = f"t-{uuid.uuid4().hex}"
        sr.request("chat_officer_csat", ticket_id=ticket, channel="chat", subject="officer", officer_id="off-x", **extra)
        return ticket

    def test_a_rating_is_asked_once_per_case(self):
        ticket = self.ask()
        again = sr.request("chat_officer_csat", ticket_id=ticket, channel="chat", subject="officer")
        self.assertEqual(again["officer_id"], "off-x")
        self.assertEqual(len(db.query_all("SELECT id FROM service_ratings WHERE ticket_id = ?", (ticket,))), 1)

    def test_it_is_answered_once(self):
        ticket = self.ask()
        self.assertTrue(sr.answer("chat_officer_csat", ticket_id=ticket, status="answered", score=4))
        self.assertFalse(sr.answer("chat_officer_csat", ticket_id=ticket, status="answered", score=1))
        self.assertEqual(sr.find("chat_officer_csat", ticket_id=ticket)["score"], 4)

    def test_an_unknown_source_status_or_score_is_refused(self):
        with self.assertRaises(ValueError):
            sr.request("chat_ai_vibes", ticket_id="x", channel="chat", subject="ai")
        ticket = self.ask()
        with self.assertRaises(ValueError):
            sr.answer("chat_officer_csat", ticket_id=ticket, status="pending")
        with self.assertRaises(ValueError):
            sr.answer("chat_officer_csat", ticket_id=ticket, status="answered", score=7)

    def test_the_audit_filters_and_never_names_the_taxpayer(self):
        low = self.ask(user_id="taxpayer-1")
        sr.answer("chat_officer_csat", ticket_id=low, status="answered", score=1, comment="Rude.")
        page = sr.audit("chat_officer_csat", 1, score=1, limit=100)
        mine = next(r for r in page["ratings"] if r["ticket_id"] == low)
        self.assertEqual(mine["comment"], "Rude.")
        self.assertNotIn("user_id", mine)
        self.assertTrue(all(r["score"] == 1 for r in page["ratings"]))
        # Pending ratings count as asked but are not listed.
        pending = self.ask()
        listed = sr.audit("chat_officer_csat", 1, limit=100)
        self.assertNotIn(pending, [r["ticket_id"] for r in listed["ratings"]])

    def test_ratings_from_before_the_period_are_the_previous_figures(self):
        old = f"t-{uuid.uuid4().hex}"
        sr.request("chat_officer_csat", ticket_id=old, channel="chat", subject="officer",
                   now=time.time() - 3 * 86400, tenant_id="ratings-prev")
        sr.answer("chat_officer_csat", ticket_id=old, status="answered", score=5)
        h = sr.headline("chat_officer_csat", 2, tenant_id="ratings-prev")
        self.assertEqual((h["asked"], h["previous"]["asked"], h["previous"]["average"]), (0, 1, 5.0))

    def test_retention_purges_old_ratings(self):
        old = f"t-{uuid.uuid4().hex}"
        sr.request("chat_officer_csat", ticket_id=old, channel="chat", subject="officer", now=time.time() - 200 * 86400)
        self.assertGreaterEqual(sr.purge_expired(90), 1)
        self.assertIsNone(sr.find("chat_officer_csat", ticket_id=old))


class RatingsRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_administrators_and_auditors_read_the_ratings(self):
        ticket = f"t-{uuid.uuid4().hex}"
        sr.request("chat_officer_csat", ticket_id=ticket, channel="chat", subject="officer",
                   officer_id="off-r", officer_name="Officer Route")
        sr.answer("chat_officer_csat", ticket_id=ticket, status="answered", score=2)
        for who in (ADMIN, AUDITOR):
            r = self.client.get("/v1/admin/quality/ratings?source=chat_officer_csat&days=7&limit=100", headers=who)
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["source"], "chat_officer_csat")
            self.assertIn(ticket, [x["ticket_id"] for x in body["ratings"]])
            self.assertIn("Officer Route", [o["officer_name"] for o in body["summary"]["by_officer"]])
        r = self.client.get("/v1/admin/quality/ratings?officer=off-r&score=2", headers=ADMIN)
        self.assertEqual([x["ticket_id"] for x in r.json()["ratings"]], [ticket])

    def test_officers_do_not_read_their_own_audit(self):
        self.assertEqual(self.client.get("/v1/admin/quality/ratings", headers=OFFICER).status_code, 403)
        self.assertIn(self.client.get("/v1/admin/quality/ratings").status_code, (401, 403, 503))

    def test_a_bad_source_or_score_is_422(self):
        self.assertEqual(self.client.get("/v1/admin/quality/ratings?source=everything", headers=ADMIN).status_code, 422)
        self.assertEqual(self.client.get("/v1/admin/quality/ratings?score=9", headers=ADMIN).status_code, 422)

    def test_the_summary_carries_officer_chat_satisfaction(self):
        body = self.client.get("/v1/admin/quality/summary?days=7", headers=ADMIN).json()
        self.assertIn("officer_chat", body)
        for key in ("asked", "answered", "response_rate", "average", "csat_pct", "low", "previous"):
            self.assertIn(key, body["officer_chat"])


if __name__ == "__main__":
    unittest.main()

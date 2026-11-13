"""Replacing a live answer override is recorded as a replacement (G637).

An override is keyed on the normalized question, so saving "What is the VAT
registration threshold?" silently replaced the answer stored for "what is the
vat registration threshold". The ledger recorded only the row id and whether it
was enabled, so nobody could tell an answer had been replaced, or what it said.
"""

from __future__ import annotations

import hashlib
import unittest
import uuid
from unittest import mock

from fastapi.testclient import TestClient

from app import database as db
from app.auth.jwt_auth import make_dev_token
from app.main import app


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class OverrideReplacementAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        db.init_db()
        self.client = TestClient(app)
        self.headers = {"Authorization": f"Bearer {make_dev_token('admin_g637', role='ura_admin')}"}
        self.question = f"What is the VAT registration threshold {uuid.uuid4().hex[:6]}?"

    def _put(self, query: str, reply: str):
        with mock.patch("app.main._audit_staff_action") as audit:
            res = self.client.put("/v1/admin/overrides", json={"query": query, "reply": reply}, headers=self.headers)
        self.assertEqual(res.status_code, 200, res.text)
        return [c.args[2] for c in audit.call_args_list if c.args[1] == "staff.override_saved"][0]

    def test_a_new_override_is_not_a_replacement(self) -> None:
        event = self._put(self.question, "UGX 300,000,000 a year.")
        self.assertFalse(event["replaced"])
        self.assertEqual(event["reply_sha256"], _sha("UGX 300,000,000 a year."))
        self.assertEqual(event["previous_reply_sha256"], "")

    def test_saving_over_the_same_normalized_question_records_what_it_replaced(self) -> None:
        self._put(self.question, "UGX 150,000,000 a year.")
        event = self._put(self.question.upper().replace("?", " ?"), "UGX 300,000,000 a year.")
        self.assertTrue(event["replaced"])
        self.assertEqual(event["previous_reply_sha256"], _sha("UGX 150,000,000 a year."))
        self.assertEqual(event["reply_sha256"], _sha("UGX 300,000,000 a year."))

    def test_the_replaced_reply_is_read_with_the_write_not_before_it(self) -> None:
        """CodeRabbit on PR 730: the route read the old reply in a call of its own
        before writing, so a write that landed in between was recorded as never
        having existed. The store now returns what the write itself replaced."""
        self._put(self.question, "UGX 150,000,000 a year.")
        # A stale read, as a concurrent request's would be: the store holds 150m.
        with mock.patch.object(db, "get_answer_override", return_value={"reply": "UGX 100,000,000 a year."}):
            event = self._put(self.question, "UGX 300,000,000 a year.")
        self.assertEqual(event["previous_reply_sha256"], _sha("UGX 150,000,000 a year."))

    def test_the_store_returns_the_reply_it_replaced(self) -> None:
        key = f"g637 store {uuid.uuid4().hex[:6]}"
        self.assertEqual(db.upsert_answer_override(key, "first")["previous_reply"], "")
        self.assertEqual(db.upsert_answer_override(key, "second")["previous_reply"], "first")

    def test_the_api_response_carries_no_previous_reply(self) -> None:
        self._put(self.question, "UGX 150,000,000 a year.")
        res = self.client.put(
            "/v1/admin/overrides", json={"query": self.question, "reply": "UGX 300,000,000 a year."}, headers=self.headers
        )
        self.assertNotIn("previous_reply", res.json())


class ConcurrentCreationTests(unittest.TestCase):
    """CodeRabbit on PR 730: two first saves of the same question at once."""

    def setUp(self) -> None:
        db.init_db()

    def test_two_first_saves_on_sqlite_both_land_and_one_replaces_the_other(self) -> None:
        import threading

        key = f"g637 race {uuid.uuid4().hex[:6]}"
        barrier = threading.Barrier(2)
        results: dict[str, dict] = {}
        errors: list[BaseException] = []

        def save(reply: str) -> None:
            try:
                barrier.wait(timeout=5)
                results[reply] = db.upsert_answer_override(key, reply)
            except BaseException as exc:  # noqa: BLE001 - reported below
                errors.append(exc)

        threads = [threading.Thread(target=save, args=(r,)) for r in ("first", "second")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        self.assertEqual(errors, [])
        previous = sorted(r["previous_reply"] for r in results.values())
        # One write created the row; the other replaced exactly what it wrote.
        self.assertEqual(previous[0], "")
        self.assertIn(previous[1], {"first", "second"})
        self.assertEqual(db.get_answer_override(key)["reply"], ({"first", "second"} - {previous[1]}).pop())

    def test_postgres_retries_a_first_save_that_lost_the_race(self) -> None:
        """No row to lock: the losing INSERT hits the unique key. It is retried
        once, when the locked read finds the winner's row and updates it."""
        from app import postgres as pg

        class UniqueViolation(Exception):
            sqlstate = "23505"

        state = {"inserts": 0, "rows": {}}

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=()):
                self.last = None
                if sql.lstrip().startswith("SELECT id, reply FROM answer_overrides"):
                    row = state["rows"].get(params[0])
                    self.last = (row["id"], row["reply"]) if row else None
                elif sql.lstrip().startswith("INSERT INTO answer_overrides"):
                    state["inserts"] += 1
                    if state["inserts"] == 1:
                        # Another request created the key a moment ago.
                        state["rows"][params[1]] = {"id": "winner", "reply": "theirs"}
                        raise UniqueViolation("duplicate key value violates unique constraint")
                    state["rows"][params[1]] = {"id": params[0], "reply": params[2]}
                elif sql.lstrip().startswith("UPDATE answer_overrides"):
                    for row in state["rows"].values():
                        if row["id"] == params[-1]:
                            row["reply"] = params[0]

            def fetchone(self):
                return self.last

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return Cursor()

            def commit(self):
                pass

        class Pool:
            def connection(self):
                return Conn()

        with mock.patch.object(pg, "_get_pool", return_value=Pool()):
            row = pg.upsert_answer_override("g637 pg race", "mine")
        self.assertEqual(row["previous_reply"], "theirs")
        self.assertEqual(row["id"], "winner")
        self.assertEqual(state["rows"]["g637 pg race"]["reply"], "mine")


if __name__ == "__main__":
    unittest.main()

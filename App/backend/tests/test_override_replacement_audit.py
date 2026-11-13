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


if __name__ == "__main__":
    unittest.main()

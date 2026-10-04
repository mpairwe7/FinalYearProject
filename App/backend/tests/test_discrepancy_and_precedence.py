"""Tests for the Conversational Bug Reporting, Solution B (Tombstones), and Solution C (Precedences)."""

import os
import unittest
from unittest import mock
from fastapi.testclient import TestClient

from app import database as db
from app.auth.jwt_auth import make_dev_token
from app.discrepancy_detector import (
    detect_discrepancy,
    format_discrepancy_acknowledgement,
)
from app.flags import flags
from app.llm import _inject_statutory_precedences
from app.main import app
from app.retriever import _filter_tombstoned_candidates


class TestDiscrepancyDetector(unittest.TestCase):
    def test_detect_rate_dispute(self):
        res = detect_discrepancy(
            "No, that is incorrect, VAT is 18%, not 16%",
            "The standard VAT rate in Uganda is 16%.",
        )
        self.assertTrue(res.is_dispute)
        self.assertEqual(res.discrepancy_type, "incorrect_rate")
        self.assertIn("16%", res.bot_statement)
        self.assertIn("18%", res.user_correction)

    def test_detect_amendment_dispute(self):
        res = detect_discrepancy(
            "Under the 2023 Amendment Act, the registration threshold was raised to 150m UGX.",
            "The threshold is 50,000,000 UGX under the original schedule.",
        )
        self.assertTrue(res.is_dispute)
        self.assertEqual(res.discrepancy_type, "outdated_law")
        self.assertEqual(res.suggested_priority, "high")

    def test_multilingual_disputes(self):
        # Luganda
        res_lg = detect_discrepancy(
            "Nedda, omusolo gwa VAT guli 18% ssi 16%",
            "Omusolo gwa VAT guli 16%.",
        )
        self.assertTrue(res_lg.is_dispute)

        # Swahili
        res_sw = detect_discrepancy(
            "Hapana, kiwango cha VAT ni 18% siyo 16%",
            "Kiwango cha VAT ni 16%.",
        )
        self.assertTrue(res_sw.is_dispute)

    def test_ignore_clarifications(self):
        res = detect_discrepancy(
            "No, I meant for an NGO or non-profit organisation",
            "Corporation tax for standard resident entities is 30%.",
        )
        self.assertFalse(res.is_dispute)

        res2 = detect_discrepancy(
            "No, that is not what I asked, I need import duty for cars",
            "Individual income tax brackets range up to 40%.",
        )
        self.assertFalse(res2.is_dispute)

    def test_ignore_emotional_complaints(self):
        res = detect_discrepancy(
            "This tax rate is completely unfair, ridiculous, and crazy!",
            "Commercial rent withholding tax is 15%.",
        )
        self.assertFalse(res.is_dispute)

    def test_format_acknowledgement(self):
        msg_en = format_discrepancy_acknowledgement("kb_abcdef123456", locale="en")
        self.assertIn("#KB-ef123456", msg_en)
        self.assertIn("Knowledge Discrepancy Report", msg_en)

        msg_lg = format_discrepancy_acknowledgement("kb_abcdef123456", locale="lg")
        self.assertIn("#KB-ef123456", msg_lg)
        self.assertIn("alipoota", msg_lg)


class TestDatabaseAndDeduplication(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_create_and_deduplicate_discrepancy(self):
        bot_statement = "The WHT rate on professional fees is 15%."
        user_correction = "Actually, the withholding tax on professional fees was reduced to 6% in 2023."

        rep1 = db.create_or_increment_discrepancy(
            bot_statement,
            user_correction,
            conversation_id="conv_1",
            priority="normal",
        )
        self.assertTrue(rep1["id"].startswith("kb_"))
        self.assertFalse(rep1["reused_existing"])
        self.assertEqual(rep1["frequency_count"], 1)

        # Second user reports the exact same discrepancy
        rep2 = db.create_or_increment_discrepancy(
            bot_statement,
            user_correction,
            conversation_id="conv_2",
            priority="normal",
        )
        self.assertEqual(rep2["id"], rep1["id"])
        self.assertTrue(rep2["reused_existing"])
        self.assertEqual(rep2["frequency_count"], 2)

        # Third user reports -> bumps priority to high
        rep3 = db.create_or_increment_discrepancy(
            bot_statement,
            user_correction,
            conversation_id="conv_3",
            priority="normal",
        )
        self.assertEqual(rep3["frequency_count"], 3)
        self.assertEqual(rep3["priority"], "high")

        # Verify retrieval and status update
        fetched = db.get_discrepancy(rep1["id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["frequency_count"], 3)

        ok = db.update_discrepancy_status(rep1["id"], "verified", admin_note="Confirmed with legal")
        self.assertTrue(ok)
        fetched_after = db.get_discrepancy(rep1["id"])
        self.assertEqual(fetched_after["status"], "verified")
        self.assertEqual(fetched_after["admin_note"], "Confirmed with legal")


class TestSolutionBAndC(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_solution_b_tombstone_filtering(self):
        # Add a tombstone for a specific outdated chunk and a source
        t1 = db.add_corpus_tombstone("outdated_vat_guide_2018.pdf", chunk_id="chunk_bad_42", reason="Superseded")
        self.assertTrue(t1["id"].startswith("tomb_"))

        candidates = [
            {"id": "c1", "source": "current_act_2023.pdf", "chunk_id": "chunk_good_1"},
            {"id": "c2", "source": "outdated_vat_guide_2018.pdf", "chunk_id": "chunk_bad_42"},
            {"id": "c3", "source": "income_tax.pdf", "chunk_id": "chunk_good_2"},
        ]

        filtered = _filter_tombstoned_candidates(candidates)
        # c2 must be dropped
        chunk_ids = [c["chunk_id"] for c in filtered]
        self.assertIn("chunk_good_1", chunk_ids)
        self.assertIn("chunk_good_2", chunk_ids)
        self.assertNotIn("chunk_bad_42", chunk_ids)

        # Cleanup
        db.delete_corpus_tombstone(t1["id"])

    def test_solution_c_active_precedence_prompt_injection(self):
        # Register a statutory precedence rule
        p1 = db.add_statutory_precedence(
            topic="VAT Registration Threshold",
            rule_statement="As per the 2023 Amendment Act, the mandatory threshold is 150,000,000 UGX.",
            statute_reference="VAT Amendment Act 2023, Sec 7",
            enabled=True,
        )
        self.assertTrue(p1["id"].startswith("prec_"))

        base_prompt = "You are the URA Intelligent Assistant."
        injected = _inject_statutory_precedences(base_prompt)

        self.assertIn("## Verified Statutory Precedences (Authoritative Corrections)", injected)
        self.assertIn("STRICTLY SUPERSEDE", injected)
        self.assertIn("VAT Registration Threshold", injected)
        self.assertIn("150,000,000 UGX", injected)
        self.assertIn("VAT Amendment Act 2023, Sec 7", injected)

        # Cleanup
        db.delete_statutory_precedence(p1["id"])


class TestAdminEndpointsE2E(unittest.TestCase):
    def setUp(self):
        db.init_db()
        self.client = TestClient(app)
        self.admin_token = make_dev_token("admin_test_user", role="ura_admin")
        self.auth_headers = {"Authorization": f"Bearer {self.admin_token}"}

    def test_admin_discrepancy_verification_workflow(self):
        # 1. Create a discrepancy report
        rep = db.create_or_increment_discrepancy(
            bot_statement="WHT is 15%",
            user_correction="WHT is 6% under 2023 amendment",
            cited_sources=["old_wht.pdf"],
        )
        rep_id = rep["id"]

        # 2. List discrepancies
        res_list = self.client.get("/v1/admin/discrepancies", headers=self.auth_headers)
        self.assertEqual(res_list.status_code, 200)
        data = res_list.json()
        self.assertIn("discrepancies", data)
        self.assertIn("stats", data)

        # 3. Get single discrepancy
        res_get = self.client.get(f"/v1/admin/discrepancies/{rep_id}", headers=self.auth_headers)
        self.assertEqual(res_get.status_code, 200)
        self.assertEqual(res_get.json()["id"], rep_id)

        # 4. Verify & Hotfix (creates override, adds precedence, tombstones source)
        verify_payload = {
            "admin_note": "Legally verified against 2023 gazette",
            "create_override": True,
            "override_query": "wht rate on professional fees",
            "override_reply": "Withholding tax on professional fees is 6% as per the 2023 Amendment Act.",
            "precedence_topic": "Withholding Tax Professional Fees",
            "precedence_rule": "Withholding tax on professional fees is 6% (not 15%).",
            "statute_reference": "Income Tax Amendment 2023",
            "tombstone_sources": ["old_wht.pdf"],
        }
        res_verify = self.client.post(
            f"/v1/admin/discrepancies/{rep_id}/verify",
            headers=self.auth_headers,
            json=verify_payload,
        )
        self.assertEqual(res_verify.status_code, 200)
        verify_data = res_verify.json()
        self.assertTrue(verify_data["ok"])
        self.assertEqual(verify_data["status"], "verified")
        self.assertTrue(verify_data["override_id"])

        # 5. Check active precedences and tombstones were registered
        precs = self.client.get("/v1/admin/precedences", headers=self.auth_headers).json()
        self.assertTrue(any(p["topic"] == "Withholding Tax Professional Fees" for p in precs["precedences"]))

        tombs = self.client.get("/v1/admin/tombstones", headers=self.auth_headers).json()
        self.assertTrue(any("old_wht.pdf" in t["source_uri"] for t in tombs["tombstones"]))

        # 6. Test dismiss endpoint on another report
        rep2 = db.create_or_increment_discrepancy(
            bot_statement="Income tax must be paid",
            user_correction="Income tax is 0% for everyone",
        )
        res_dismiss = self.client.post(
            f"/v1/admin/discrepancies/{rep2['id']}/dismiss",
            headers=self.auth_headers,
            json={"admin_note": "User misconception"},
        )
        self.assertEqual(res_dismiss.status_code, 200)
        self.assertEqual(res_dismiss.json()["status"], "dismissed")


if __name__ == "__main__":
    unittest.main()

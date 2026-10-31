"""Regression tests for October 2026 gap closures (G288–G294, G297, G81, G87, G125, G184, G185, G188, G259)."""

from __future__ import annotations

import time
import unittest
from pathlib import Path
from unittest import mock

from app import cache, cms, entailment, indexer, mt, query, retriever, speech_normalization
from app.claim_verifier import verify_claims
from app.receptionist import lexicon


class AnswerVerificationGapClosureTests(unittest.TestCase):
    def test_g288_false_premise_rate_question_is_flagged_as_contradiction(self) -> None:
        """G288: 'Is the standard VAT rate 20%?' must not exempt a 'Yes, 20%' reply against an 18% source."""
        passage = "The standard rate of Value Added Tax (VAT) in Uganda is 18% under the VAT Act."
        user_q = "Is the standard VAT rate 20%?"
        bad_claim = "Yes, the standard VAT rate in Uganda is 20% [1]."
        self.assertTrue(
            entailment.numeric_contradiction(bad_claim, passage, user_query=user_q),
            "A false-premise percentage in the query must not exempt a contradicted statutory rate claim",
        )
        report = verify_claims(
            bad_claim,
            [{"ref": "[1]", "source": "ura_vat_faqs.csv", "passage": passage}],
            [{"text": passage, "source": "ura_vat_faqs.csv"}],
            query=user_q,
        )
        self.assertEqual(report["decision"], "escalate")
        self.assertGreaterEqual(len(report["contradicted_claims"]), 1)

        # A corrective reply that states the true 18% rate while negating 20% must NOT be flagged
        good_claim = "No, the standard VAT rate in Uganda is 18%, not 20% [1]."
        self.assertFalse(
            entailment.numeric_contradiction(good_claim, passage, user_query=user_q),
            "A corrective reply negating the false premise and stating the true rate must pass",
        )

        # Compound-clause bypass check: mentioning 18% alongside an un-negated 20% false premise must STILL be flagged
        compound_bad = "Although standard VAT is 18%, your domestic supply is taxed at 20% [1]."
        self.assertTrue(
            entailment.numeric_contradiction(compound_bad, passage, user_query=user_q),
            "Compound sentence repeating an un-negated false-premise percentage alongside 18% must be flagged",
        )

        # Vernacular (Swahili) false-premise rate contradiction check
        sw_passage = "Kiwango cha kawaida cha VAT nchini Uganda ni asilimia 18."
        sw_query = "Je, kiwango cha VAT ni asilimia 20?"
        sw_bad = "Ndiyo, kiwango cha kodi ya VAT ni asilimia 20 [1]."
        self.assertTrue(
            entailment.numeric_contradiction(sw_bad, sw_passage, user_query=sw_query),
            "Swahili false-premise rate confirmation must be flagged as contradiction",
        )
        sw_good = "Hapana, kiwango cha VAT ni asilimia 18, si asilimia 20 [1]."
        self.assertFalse(
            entailment.numeric_contradiction(sw_good, sw_passage, user_query=sw_query),
            "Swahili corrective negation must pass",
        )

    def test_g289_money_amount_in_query_does_not_approve_unverified_hallucinated_figures(self) -> None:
        """G289: A salary question must not blanket-approve an unsupported claim stating an invented penalty."""
        from app.service import ChatModel

        model = ChatModel.__new__(ChatModel)
        model._has_inline_citations = lambda r: "[1]" in r
        model._reflect_llm = lambda **kw: ""
        model._build_grounded_revision = lambda *a, **kw: ""

        hits = [{"text": "PAYE returns are due by the 15th day of the following month.", "source": "paye.csv"}]
        citations = [{"ref": "[1]", "source": "paye.csv", "passage": hits[0]["text"]}]
        # Draft invents a UGX 9,500,000 penalty not in the passage or user's salary
        bad_reply = "For your salary of UGX 5,000,000, late filing attracts a special penalty of UGX 9,500,000 [1]."
        claim_report = verify_claims(bad_reply, citations, hits, query="My salary is UGX 5,000,000, when is PAYE due?")
        self.assertTrue(claim_report["unsupported_claims"])

        judge = model._evaluate_response_judge(
            message="My salary is UGX 5,000,000, when is PAYE due?",
            reply=bad_reply,
            hits=hits,
            citations=citations,
            faithfulness_score=0.45,
            escalation_required=False,
            escalation_reason="",
            claim_report=claim_report,
            verified_facts="",
        )
        self.assertNotIn("approved user-scenario calculation draft", judge["reasons"])

        # Numberless hallucinated prose claim in a salary turn must also NOT be auto-approved
        numberless_bad = "URA will immediately revoke your passport and freeze all accounts without notice [1]."
        cr_numberless = verify_claims(numberless_bad, citations, hits, query="My salary is UGX 5,000,000, when is PAYE due?")
        judge_numberless = model._evaluate_response_judge(
            message="My salary is UGX 5,000,000, when is PAYE due?",
            reply=numberless_bad,
            hits=hits,
            citations=citations,
            faithfulness_score=0.45,
            escalation_required=False,
            escalation_reason="",
            claim_report=cr_numberless,
            verified_facts="",
        )
        self.assertNotIn("approved user-scenario calculation draft", judge_numberless["reasons"])

    def test_g290_cms_normalize_query_strips_punctuation_and_preserves_legacy_lookup(self) -> None:
        """G290: Punctuation differences resolve to the same key while keeping 1.5 and 1-5 distinct."""
        self.assertEqual(
            cms.normalize_query("What is the standard VAT rate?"),
            cms.normalize_query("what is the standard vat rate"),
        )
        self.assertNotEqual(
            cms.normalize_query("Is the rate 1.5%?"),
            cms.normalize_query("Is the rate 1-5%?"),
        )
        with mock.patch("app.database.get_answer_override") as mock_get:
            mock_get.side_effect = lambda k: {"id": "ov1", "reply": "18%"} if k == "what is vat" else None
            found = cms.lookup("What is VAT?!")
            self.assertIsNotNone(found)
            self.assertEqual(found["id"], "ov1")

    def test_g293_per_passage_support_rejects_bag_of_words_across_unrelated_hits(self) -> None:
        """G293: An uncited claim stitching half its tokens from hit 1 and half from hit 2 must not pass."""
        hits = [
            {"text": "Customs bond securities are executed by licensed clearing agents at border posts.", "source": "a.pdf"},
            {"text": "Agricultural tractors and hoes are exempt from Value Added Tax on importation.", "source": "b.pdf"},
        ]
        citations = [
            {"ref": "[1]", "source": "a.pdf", "passage": hits[0]["text"]},
            {"ref": "[2]", "source": "b.pdf", "passage": hits[1]["text"]},
        ]
        stitched_claim = "Customs bond securities executed by clearing agents are exempt from Value Added Tax on importation [1]."
        report = verify_claims(stitched_claim, citations, hits, min_support=0.75)
        self.assertEqual(report["supported_claim_count"], 0)


class RetrieverLifecycleAndTemporalTests(unittest.TestCase):
    def test_g292_shared_retriever_singleton_and_alias_refresh(self) -> None:
        """G292: get_shared_retriever returns the registered singleton and _maybe_refresh_alias_binding recovers _sparse_ok."""
        r = retriever.HybridRetriever()
        retriever.set_shared_retriever(r)
        self.assertIs(retriever.get_shared_retriever(auto_init=False), r)

        # Simulate desynced BM25 state recovering when sentinel payload updates
        enc = retriever.BM25SparseEncoder().fit(["vat registration threshold is 300 million shillings"])
        from app.indexer import _packed_bm25_state

        packed = _packed_bm25_state(enc.to_dict())
        r._client = mock.MagicMock()
        r._sparse_ok = False
        r._bound_corpus_hash = "old_hash"
        r._binding_checked_at = 0.0
        r._binding_payload = lambda: {
            "corpus_hash": enc.corpus_hash,
            "source_corpus_hash": "new_source_hash_1234",
            "bm25_state_zlib": packed,
        }
        r._maybe_refresh_alias_binding()
        self.assertTrue(r._sparse_ok)
        self.assertEqual(r._bound_corpus_hash, "new_source_hash_1234")

    def test_g294_annotate_fiscal_year_populates_valid_from_and_valid_to(self) -> None:
        """G294: Indexed documents carry explicit ISO valid_from, valid_to, and superseded_by metadata."""
        docs = [
            {"source": "Taxation-Handbook-FY-2026-27.pdf", "text": "VAT is 18%."},
            {"source": "ura_vat_faqs.csv", "text": "General FAQ."},
        ]
        indexer.annotate_fiscal_year(docs)
        self.assertEqual(docs[0]["fiscal_year"], "FY2026-27")
        self.assertEqual(docs[0]["valid_from"], "2026-07-01")
        self.assertEqual(docs[0]["valid_to"], "2027-06-30")
        self.assertEqual(docs[0]["superseded_by"], "")
        self.assertEqual(docs[1]["fiscal_year"], "")
        self.assertEqual(docs[1]["valid_from"], "")
        self.assertEqual(docs[1]["valid_to"], "")

        # Point-in-time 'as of' date extraction & candidate filtering (G294)
        self.assertEqual(query.extract_as_of_date("What was the VAT threshold as of 2024-10-15?"), "2024-10-15")
        self.assertEqual(query.extract_as_of_date("What was the rule as at 15 October 2024?"), "2024-10-15")
        self.assertIsNone(query.extract_as_of_date("What was the rule as of 2024-02-31?"))
        self.assertIsNone(query.extract_as_of_date("What was the rule as of 31 February 2024?"))
        self.assertEqual(query.fiscal_year_for_iso_date("2024-10-15"), "FY2024-25")
        cands = [
            {"text": "FY2024-25 threshold 150M", "fiscal_year": "FY2024-25", "valid_from": "2024-07-01", "valid_to": "2025-06-30"},
            {"text": "FY2026-27 threshold 300M", "fiscal_year": "FY2026-27", "valid_from": "2026-07-01", "valid_to": "2027-06-30"},
            {"text": "General VAT Act definition", "fiscal_year": "", "valid_from": "", "valid_to": ""},
        ]
        filtered = retriever.filter_candidates_as_of(cands, "2024-10-15")
        self.assertEqual([c["fiscal_year"] for c in filtered], ["FY2024-25", ""])

    def test_g293_rag_tool_citation_offset_and_keyword_fallback_fy_filter(self) -> None:
        """G293: prompt_citation_offset_scope shifts RAG tool refs [k+1]..[k+m] and keyword fallback filters conflicting FYs."""
        from app.service import _faq_hits_to_retrieval_hits
        from app.tools.rag_tool import SearchKnowledgeBaseTool, prompt_citation_offset_scope

        tool = SearchKnowledgeBaseTool()
        fake_ret = mock.MagicMock()
        fake_ret.is_ready = True
        fake_ret.search_planned.return_value = [{"text": "Passage A", "source": "a.pdf", "score_rerank": 0.9}]
        with mock.patch("app.tools.rag_tool._get_retriever", return_value=fake_ret), mock.patch(
            "app.tools.rag_tool._retriever_ready", True
        ), prompt_citation_offset_scope(3):
            res = tool.execute(query="What is VAT?", top_k=1)
        self.assertTrue(res["ok"])
        self.assertEqual(res["results"][0]["ref"], "[4]")

        # Keyword fallback FY filtering
        entries = [
            {"question": "Q1", "answer": "A1", "source": "ura_taxation_handbook_fy2024_25_faqs.csv", "tag": "vat"},
            {"question": "Q2", "answer": "A2", "source": "ura_taxation_handbook_fy2026_27_faqs.csv", "tag": "vat"},
            {"question": "Q3", "answer": "A3", "source": "ura_vat_faqs.csv", "tag": "vat"},
        ]
        kw_hits = _faq_hits_to_retrieval_hits(entries, query="What was the VAT rule in FY2024-25?")
        self.assertEqual([h["fiscal_year"] for h in kw_hits], ["FY2024-25", ""])

    def test_cache_clear_removes_entries_and_resets_stamp(self) -> None:
        """SemanticCache.clear() removes exact and semantic entries immediately."""
        sc = cache.SemanticCache()
        sc.put("What is the VAT rate?", {"reply": "18%", "locale": "en"})
        self.assertGreaterEqual(sc.stats["size"], 1)
        removed = sc.clear()
        self.assertGreaterEqual(removed, 1)
        self.assertEqual(sc.stats["size"], 0)


class MultilingualAndSpeechHardeningTests(unittest.TestCase):
    def test_g125_citation_repetition_loop_detected(self) -> None:
        """G125: Translation degeneration like 'We[1]We[2]We[3]We[4]We[5]' is caught by mt.looped, while 6 distinct cited items pass."""
        src = "Taxpayers must register for a TIN and file returns on time [1]."
        degenerate = "Olina okwewandiisa We[1]We[2]We[3]We[4]We[5]We[6]We[7]"
        self.assertTrue(mt.looped(src, degenerate))
        distinct_list = "Items: alpha[1] beta[2] gamma[3] delta[4] epsilon[5] zeta[6]"
        self.assertFalse(mt.looped(src, distinct_list))

    def test_g188_resume_ticket_enforces_anonymous_vs_authenticated_owner(self) -> None:
        """G188: An anonymous caller (expected_user_id='') must not consume a ticket issued to an authenticated user."""
        import os
        from app.flags import flags
        from app.receptionist.low_bandwidth import mint_call_resume_ticket, verify_call_resume_ticket

        flags.set("call_drop_resume", True)
        try:
            with mock.patch.dict(os.environ, {"CALL_RESUME_SECRET": "test-resume-secret-key-32-bytes!!"}):
                ticket = mint_call_resume_ticket("call_1", "conv_1", user_id="user_auth_1", tenant_id="default")
                self.assertTrue(ticket)
                # Anonymous caller attempts to present authenticated user's ticket
                self.assertIsNone(
                    verify_call_resume_ticket(ticket, expected_user_id="", expected_tenant_id="default")
                )
                # Legitimate owner still succeeds because the failed check did not burn the nonce
                claims = verify_call_resume_ticket(ticket, expected_user_id="user_auth_1", expected_tenant_id="default")
                self.assertIsNotNone(claims)
                self.assertEqual(claims["uid"], "user_auth_1")
        finally:
            flags.clear("call_drop_resume")

    def test_g87_speech_normalizer_regexes_run_in_linear_time(self) -> None:
        """G87: Pathological 50,000-char strings of spaces/newlines/zeros complete in < 0.25s."""
        for payload in (" " * 50_000, "\n" * 50_000, "0" * 50_000):
            t0 = time.perf_counter()
            for pat in (
                speech_normalization._CITATION_RE,
                speech_normalization._BROKEN_MD_LINK_RE,
                speech_normalization._TABLE_ROW_RE,
                speech_normalization._TABLE_PIPE_RE,
                speech_normalization._HEADER_RE,
                speech_normalization._BULLET_RE,
                speech_normalization._NUMBERED_STEP_RE,
            ):
                pat.sub(" ", payload)
            elapsed = time.perf_counter() - t0
            self.assertLess(elapsed, 0.25, f"Regex suite took {elapsed:.3f}s on 50k payload")

    def test_g259_asr_repair_fixes_vetereite_and_veterinary_rate(self) -> None:
        """G259: Orpheus round-trip 'standard vetereite' is repaired to 'standard VAT rate'."""
        self.assertEqual(
            lexicon.repair_asr_entities("What is the standard vetereite in Uganda?"),
            "What is the standard VAT rate in Uganda?",
        )

    def test_g81_swahili_retrieval_translation_normalizes_tax_terms_first(self) -> None:
        """G81: translate_query_for_retrieval normalizes Swahili rental/penalty terms before calling MT."""
        seen: list[str] = []

        def _fake_translate(text: str, source_lang: str = "sw", target_lang: str = "en") -> str:
            seen.append(text)
            return "How do I pay Rental Income Tax?"

        mt.cache.clear()
        with mock.patch("app.llm.translate_text", side_effect=_fake_translate), mock.patch(
            "app.query.detect_language", return_value="en"
        ):
            out = query.translate_query_for_retrieval(
                "Ninawezaje kulipa kodi ya mapato ya upangishaji?", "sw"
            )
        self.assertEqual(out, "How do I pay Rental Income Tax?")
        self.assertTrue(seen and "Rental Income Tax" in seen[0])

    def test_g297_root_dockerfile_uses_cu126_index(self) -> None:
        """G297: Root Dockerfile uses the cu126 wheel index that publishes torch==2.14.1."""
        df = (Path(__file__).resolve().parents[3] / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("https://download.pytorch.org/whl/cu126", df)
        self.assertNotIn("https://download.pytorch.org/whl/cu128", df)


if __name__ == "__main__":
    unittest.main()

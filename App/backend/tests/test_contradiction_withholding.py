"""A figure that contradicts its own cited passage is not printed.

Reported as "the model is still hallucinating". The pipeline already CAUGHT
this class of error: ``entailment.numeric_contradiction`` is a deliberately
high-precision check — a percentage the cited passage does not state, or, for
rule-shaped sentences only, an amount it does not state — and the response
judge already escalated on it and raised a ticket.

What none of that did was stop showing the figure. A taxpayer acts on the
number, not on the amber banner above it, so on a revenue authority's
assistant a detected contradiction that is still printed is the same as an
undetected one.

The line this draws matters as much as the behaviour. *Contradicted* claims
are withheld; merely *unsupported* ones are not. An unsupported claim is one a
lexical verifier could not confirm — paraphrase, a synonym, a figure the
passage expresses differently — which happens constantly and legitimately.
Withholding those would silence most correct answers.
"""

from __future__ import annotations

import unittest
import unittest.mock as mock

from app import service
from app.text_signals import CONTRADICTED_CLAIM_REPLY


class WithholdContradictedTest(unittest.TestCase):
    ANSWER = "Value Added Tax is charged at 20% on taxable supplies. [1]"

    def test_a_contradicted_figure_is_replaced(self):
        report = {
            "decision": "escalate",
            "contradicted_claims": [{"text": "Value Added Tax is charged at 20%"}],
            "unsupported_claims": [{"text": "Value Added Tax is charged at 20%"}],
        }
        reply, withheld = service.withhold_if_contradicted(self.ANSWER, report)
        self.assertTrue(withheld)
        self.assertEqual(reply, CONTRADICTED_CLAIM_REPLY)
        # The wrong figure must not survive anywhere in what is sent.
        self.assertNotIn("20%", reply)

    def test_the_replacement_says_what_happened(self):
        """Not "I could not find anything" — retrieval worked, generation went
        wrong, and telling the taxpayer the first is a false statement about
        their question."""
        reply, _ = service.withhold_if_contradicted(
            self.ANSWER, {"contradicted_claims": [{"text": "x"}]}
        )
        self.assertIn("disagreed with the URA documents", reply)
        self.assertIn("0800 117 000", reply)

    def test_merely_unsupported_claims_are_left_alone(self):
        """The common, legitimate case: a lexical verifier that could not
        confirm a paraphrase. Withholding these would silence most correct
        answers."""
        report = {
            "decision": "revise",
            "contradicted_claims": [],
            "unsupported_claims": [{"text": "You may register online."}],
            "uncited_claims": [{"text": "You may register online."}],
        }
        reply, withheld = service.withhold_if_contradicted(self.ANSWER, report)
        self.assertFalse(withheld)
        self.assertEqual(reply, self.ANSWER)

    def test_no_report_and_no_reply_are_both_no_ops(self):
        self.assertEqual(service.withhold_if_contradicted(self.ANSWER, None), (self.ANSWER, False))
        self.assertEqual(service.withhold_if_contradicted("", {"contradicted_claims": [{}]}), ("", False))

    def test_it_can_be_turned_off(self):
        with mock.patch.object(service, "WITHHOLD_CONTRADICTED_CLAIMS", False):
            reply, withheld = service.withhold_if_contradicted(
                self.ANSWER, {"contradicted_claims": [{"text": "x"}]}
            )
        self.assertFalse(withheld)
        self.assertEqual(reply, self.ANSWER)


class StreamingGuardTest(unittest.TestCase):
    """_apply_output_guards is shared by the token and agentic branches, so
    withholding there covers both."""

    def _guard(self, claim_report):
        model = mock.MagicMock()
        model._evaluate_response_judge.return_value = {"decision": "approve", "reasons": []}
        model._build_handoff_packet.return_value = {"topic": "vat", "priority": "normal"}
        model._maybe_create_ticket.return_value = "ticket-1"
        output_guard = mock.MagicMock()
        output_guard.should_escalate.return_value = (False, "")
        with mock.patch.object(service, "verify_claims", return_value=claim_report), \
             mock.patch.object(service.HybridRetriever, "compute_faithfulness", return_value=0.9):
            return service._apply_output_guards(
                model,
                message="What is the VAT rate?",
                reply="Value Added Tax is charged at 20% on taxable supplies. [1]",
                hits=[{"text": "VAT is charged at 18%.", "source": "vat.csv"}],
                citations=[{"ref": "[1]", "source": "vat.csv", "passage": "VAT is charged at 18%."}],
                conversation_history=[],
                session_id="s1",
                conversation_id="c1",
                output_guard=output_guard,
            )

    def test_a_contradiction_withholds_escalates_and_drops_the_score(self):
        out = self._guard(
            {"decision": "escalate", "contradicted_claims": [{"text": "VAT is 20%"}]}
        )
        self.assertEqual(out["reply"], CONTRADICTED_CLAIM_REPLY)
        self.assertTrue(out["revised"])
        self.assertTrue(out["escalate"])
        # No faithfulness score: the number scored 0.9 described text that is
        # no longer being sent, and reporting it would put a "well grounded"
        # badge on a withholding notice.
        self.assertIsNone(out["faithfulness"])
        self.assertTrue(out["response_judge"]["withheld_contradicted"])
        # An officer must be waiting — withholding without a handoff leaves the
        # taxpayer with nothing at all.
        self.assertTrue(out["ticket_id"])

    def test_a_clean_answer_passes_through(self):
        out = self._guard({"decision": "approve", "contradicted_claims": []})
        self.assertIn("20%", out["reply"])
        self.assertFalse(out["escalate"])
        self.assertEqual(out["faithfulness"], 0.9)


class AnswerLanguageDirectiveTest(unittest.TestCase):
    """The prompt used to instruct the model to do the one thing this
    architecture exists to avoid.

    Rule 10 said "if the user writes in Luganda … respond in the same
    language", unconditionally, in the system prompt — where nothing could see
    that the CPU deployments and the vLLM backend load no locale adapter. The
    base model asked for Luganda anyway produced a degenerate repetition loop
    rather than sentences. Meanwhile the message builder quietly declined to
    reinforce it, so two instructions pointed opposite ways and the language of
    the question broke the tie.
    """

    def _language_section(self, locale, can_generate):
        from app import llm

        with mock.patch.object(llm, "can_generate_in_locale", return_value=can_generate):
            messages = llm._build_messages(
                "VAT eri ki?",
                [{"source": "vat.csv", "text": "VAT is 18%"}],
                locale=locale,
            )
        return messages[-1]["content"]

    def test_no_adapter_means_english_and_says_why(self):
        content = self._language_section("lg", False)
        self.assertIn("Write the answer in English", content)
        self.assertIn("do not translate it yourself", content)

    def test_an_adapter_means_the_locale_is_named(self):
        content = self._language_section("lg", True)
        self.assertIn("Write the answer in lg.", content)

    def test_the_system_prompt_no_longer_decides_the_language_by_itself(self):
        from app import llm

        self.assertNotIn("respond in the same language", llm.SYSTEM_PROMPT.lower())
        self.assertIn("## Answer language", self._language_section("en", True))


if __name__ == "__main__":
    unittest.main()

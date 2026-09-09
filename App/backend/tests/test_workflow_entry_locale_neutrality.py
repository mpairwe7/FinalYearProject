"""G39: a question must not be captured as a task, in any language.

``docs/GAPS_AND_AGENTIC_ROADMAP.md`` §2.10 measured it: 80 of 186 live probes
were answered with a guided-flow slot prompt instead of an answer, and *all* of
them were Luganda or Kiswahili. Coverage in both languages sat 27 points below
English, and this was the largest single cause.

The mechanism is a pair of asymmetries in ``_maybe_handle_workflow``.
``WorkflowRegistry.match_trigger`` is tried against the query's English
translation as well as the taxpayer's own words, so a local-language question
reaches the English trigger phrases. The escape that keeps an informational
question out of a flow — ``_INFORMATIONAL_WORKFLOW_QUERY_RE`` — is an English
word list covering "how do I", "what are the steps", "procedure". A translation
that renders the question as "Can I file a nil return?" matches a trigger and
none of the escape, so the trigger fires and the escape cannot.

G38 fixed the mirror image of this on the way *out* of a flow and established
the predicate that carries across locales: ``_reads_as_question``, an English
interrogative opener **or** a trailing "?" on three words or more. This pins
the entrance using the same one.
"""

from __future__ import annotations

import unittest
import uuid

from app import service


class WorkflowEntryLocaleNeutralityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = service.ChatModel()

    def _entered(self, message: str, rewritten: str, tag: str) -> bool:
        """Whether this turn is captured into a guided flow.

        ``rewritten`` is the English canonical form the service builds for a
        local-language question — the text that reaches ``match_trigger`` and
        makes the capture possible in the first place.
        """
        # A fresh thread per call: `_maybe_handle_workflow` persists a session
        # for any flow it starts, and a reused id makes the next call take the
        # continue-an-existing-flow branch instead of the entry decision under
        # test.
        result = self.model._maybe_handle_workflow(
            message=message,
            rewritten=rewritten,
            thread_id=f"test-{tag}-{uuid.uuid4()}",
            locale="en",
        )
        return result is not None

    # -- the defect ---------------------------------------------------------
    def test_a_luganda_question_is_not_captured_as_a_task(self) -> None:
        self.assertFalse(
            self._entered("Nsobola okuwaayo nil return?", "Can I file a nil return?", "lg-return")
        )

    def test_a_kiswahili_question_is_not_captured_as_a_task(self) -> None:
        self.assertFalse(
            self._entered("Je, ninaweza kulipa kodi yangu leo?", "Can I pay my tax today?", "sw-pay")
        )

    def test_a_question_form_the_english_word_list_misses_still_escapes(self) -> None:
        """"Must I…" is a question and appears in no escape list."""
        self.assertFalse(
            self._entered("Nnina okukola objection?", "Must I file an objection?", "lg-objection")
        )

    # -- what must keep working --------------------------------------------
    def test_a_bare_task_statement_still_starts_a_flow(self) -> None:
        self.assertTrue(self._entered("File my return", "File my return", "task"))

    def test_an_explicit_request_to_start_a_flow_still_starts_one(self) -> None:
        self.assertTrue(
            self._entered("Help me file my return", "Help me file my return", "explicit")
        )

    def test_an_english_informational_question_still_escapes(self) -> None:
        self.assertFalse(
            self._entered(
                "What are the steps to file a return?",
                "What are the steps to file a return?",
                "en-info",
            )
        )

    # -- the predicate itself ----------------------------------------------
    def test_the_word_count_floor_keeps_a_hedged_slot_answer_with_the_validator(self) -> None:
        """G38's floor. A one-word "individual?" is uncertainty, not a question."""
        self.assertFalse(service._reads_as_question("individual?"))

    def test_a_local_language_question_with_no_english_opener_reads_as_one(self) -> None:
        self.assertTrue(service._reads_as_question("Ssente mmeka ez'omusolo gwa VAT?"))
        self.assertTrue(service._reads_as_question("Je, ninaweza kulipa kodi yangu leo?"))


if __name__ == "__main__":
    unittest.main()

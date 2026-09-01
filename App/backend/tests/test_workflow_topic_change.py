"""A question asked mid-flow must be answered, not eaten as a slot value.

A guided workflow owns its thread until it completes or the user types one of
six cancel words, and ``_maybe_handle_workflow`` returns before retrieval runs.
So while a flow was open the corpus was unreachable, and any question the
taxpayer asked was fed to the slot validator instead.

From a measured PAYE journey (results/tax_education_accuracy_local_gpu.json,
topic coherence 33%):

    turn 1  "I hired 3 staff with salaries of 1.5m, 2.5m and 5.0m UGX.
             What are my PAYE duties?"        -> opens the flow
    turn 3  "What is the penalty if I pay on the 20th instead of the 15th?"
                                              -> "Please give me one..."

The fix diverts only when the message reads as a question *and* the pending
slot cannot accept it, so a mistyped answer still re-asks rather than silently
abandoning the flow.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from app.service import ChatModel
from app.workflows.loader import load_workflow
from app.workflows.registry import WorkflowRegistry

FLOWS_DIR = Path(__file__).resolve().parents[1] / "app" / "workflows" / "flows"

_MID_FLOW_QUESTIONS = [
    "What is the penalty if I pay on the 20th instead of the 15th?",
    "When is the monthly deadline to pay PAYE to URA?",
    "How much is the late filing fine?",
]


class WorkflowTopicChangeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        WorkflowRegistry._workflows.clear()
        for path in sorted(FLOWS_DIR.glob("*.yaml")):
            WorkflowRegistry.register(load_workflow(path))
        cls.model = ChatModel()

    def _session_awaiting_a_slot(self, workflow_id: str):
        session = WorkflowRegistry.create_session(workflow_id)
        self.assertIsNotNone(session, f"{workflow_id} should be registered")
        WorkflowRegistry.advance(session, "")
        step = WorkflowRegistry.pending_step(session)
        self.assertIsNotNone(step, f"{workflow_id} should be waiting on a step")
        self.assertTrue(step.slot, f"{workflow_id} should be waiting on a slot")
        return session, step

    def test_pending_step_does_not_advance_the_session(self) -> None:
        session, _ = self._session_awaiting_a_slot("tin_registration")
        before = session.current_step_idx
        WorkflowRegistry.pending_step(session)
        WorkflowRegistry.pending_step(session)
        self.assertEqual(session.current_step_idx, before)

    def test_question_diverts_out_of_an_enum_slot(self) -> None:
        session, _ = self._session_awaiting_a_slot("tin_registration")
        for question in _MID_FLOW_QUESTIONS:
            with self.subTest(question=question):
                self.assertTrue(
                    self.model._workflow_input_changes_subject(session, question)
                )

    def test_question_diverts_out_of_a_free_text_slot(self) -> None:
        # Free text is the most common validator in the shipped flows and
        # accepts anything, so validation cannot be the arbiter there.
        session, step = self._session_awaiting_a_slot("payment_assistance")
        self.assertEqual(step.validator.strip(), "text")
        for question in _MID_FLOW_QUESTIONS:
            with self.subTest(question=question):
                self.assertTrue(
                    self.model._workflow_input_changes_subject(session, question)
                )

    def test_valid_slot_answer_is_not_diverted(self) -> None:
        session, _ = self._session_awaiting_a_slot("tin_registration")
        for answer in ("individual", "company", "ngo"):
            with self.subTest(answer=answer):
                self.assertFalse(
                    self.model._workflow_input_changes_subject(session, answer)
                )

    def test_unrecognised_answer_re_asks_instead_of_diverting(self) -> None:
        # The flow must not be abandoned just because an answer was not
        # understood — that is the validator's job, and it re-asks.
        session, _ = self._session_awaiting_a_slot("tin_registration")
        for answer in ("sole trader", "a shop", "dunno"):
            with self.subTest(answer=answer):
                self.assertFalse(
                    self.model._workflow_input_changes_subject(session, answer)
                )

    def test_free_text_slot_still_accepts_an_ordinary_answer(self) -> None:
        session, _ = self._session_awaiting_a_slot("payment_assistance")
        for answer in ("vat", "PRN12345678", "corporation tax"):
            with self.subTest(answer=answer):
                self.assertFalse(
                    self.model._workflow_input_changes_subject(session, answer)
                )

    def test_local_language_questions_can_leave_a_flow(self) -> None:
        """The escape must not be English-only.

        Measured against the live stack: 80 of 186 taxpayer-voice probes came
        back as workflow slot prompts, all of them Luganda or Kiswahili. None
        of those questions opens with an English question word, so an
        English-only detector strands exactly the users this system exists to
        serve. A trailing "?" is the signal that carries across languages;
        the repository deliberately refuses to invent local-language trigger
        vocabulary (see app.agents.patterns), so it must not be required here.
        """
        local_questions = [
            "Kiki kye nnina okukola nga nfunye TIN?",
            "Omusolo gwa VAT gw'ameka mu Uganda?",
            "Nifanye nini kama sina namba ya kitambulisho cha taifa?",
            "Kiwango cha kodi ya VAT nchini Uganda ni asilimia ngapi?",
        ]
        for workflow_id in ("tin_registration", "payment_assistance"):
            session, _ = self._session_awaiting_a_slot(workflow_id)
            for question in local_questions:
                with self.subTest(workflow=workflow_id, question=question):
                    self.assertTrue(
                        self.model._workflow_input_changes_subject(session, question)
                    )

    def test_a_hedged_one_word_answer_still_reaches_the_validator(self) -> None:
        # The word-count floor is what separates "individual?" from a real
        # question; without it the trailing "?" rule would divert an uncertain
        # slot answer and abandon the flow.
        session, _ = self._session_awaiting_a_slot("tin_registration")
        for answer in ("individual?", "company?", "ngo?"):
            with self.subTest(answer=answer):
                self.assertFalse(
                    self.model._workflow_input_changes_subject(session, answer)
                )

    def test_cancel_words_are_left_to_the_cancel_path(self) -> None:
        # Cancellation is handled before this check and ends the flow with its
        # own reply; diverting them here would skip that.
        session, _ = self._session_awaiting_a_slot("tin_registration")
        for word in ("cancel", "stop", "quit"):
            with self.subTest(word=word):
                self.assertFalse(
                    self.model._workflow_input_changes_subject(session, word)
                )


if __name__ == "__main__":
    unittest.main()

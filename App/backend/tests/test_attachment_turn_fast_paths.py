"""What a turn's document means for its instant paths (ADR-0068 part 2).

The HF Space's build script made the REST path skip every fast path on a turn with an attachment.
That also skipped the decline for another country's taxes, and the streaming path, which the web
app uses, was never patched, so the two routes disagreed (G698).
"""

from __future__ import annotations

import ast
import types
import unittest
import uuid
from pathlib import Path

from app.db import database as db
from app.rag import pipeline
from app.rag.pipeline import attachment_role

PIPELINE = Path(pipeline.__file__)
READABLE = [types.SimpleNamespace(text="Gross salary UGX 1,200,000")]
UNREADABLE = [types.SimpleNamespace(text="   ")]


class AttachmentRoleTest(unittest.TestCase):
    def test_no_document_or_one_with_no_text_is_no_document(self) -> None:
        """A scan with no extracted text cannot hold a missing figure, so the taxpayer is asked."""
        for attachments in (None, [], UNREADABLE):
            self.assertEqual(attachment_role(attachments, "What is the PAYE on this payslip?"), "none")

    def test_a_message_that_points_at_the_document_makes_it_the_subject(self) -> None:
        for message in (
            "What is the VAT rate on this invoice?",
            "What is the PAYE on the salary in this payslip?",
            "Is the attached notice of assessment correct?",
            "Calculate the tax due on my uploaded bank statement",
            "Ankara hii ina VAT kiasi gani?",
            "Ekiwandiiko kino kigamba ki ku musolo?",
        ):
            with self.subTest(message=message):
                self.assertEqual(attachment_role(READABLE, message), "subject")

    def test_a_question_that_stands_on_its_own_leaves_the_document_beside_it(self) -> None:
        for message in (
            "What is the VAT rate in Uganda?",
            "What is the PAYE on UGX 500,000 a month?",
            "What is the tax on income above 10 million this year?",
            "How much PAYE should I pay on my salary?",
        ):
            with self.subTest(message=message):
                self.assertEqual(attachment_role(READABLE, message), "beside")

    def test_either_form_of_the_message_can_point_at_it(self) -> None:
        self.assertEqual(attachment_role(READABLE, "Ssente meka?", "How much is on this receipt?"), "subject")


class AttachmentTurnFastPathsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        db.init_db()  # the paths that ask write a workflow row; the file must pass when run alone
        cls.model = pipeline.ChatModel()

    def _turn(self, message: str, *, attachment: str) -> dict | None:
        return self.model._maybe_handle_fast_paths(
            message=message, rewritten=message, thread_id=f"t-{uuid.uuid4().hex[:8]}", locale="en", attachment=attachment
        )

    def test_another_countrys_tax_is_declined_whatever_the_document_is(self) -> None:
        for attachment in ("beside", "subject"):
            result = self._turn("What is the VAT rate in Kenya?", attachment=attachment)
            self.assertEqual((result or {}).get("retrieval_mode"), "out_of_jurisdiction")

    def test_a_question_with_its_own_figures_still_gets_the_exact_answer(self) -> None:
        result = self._turn("What is the PAYE on UGX 500,000 a month?", attachment="beside")
        self.assertEqual((result or {}).get("retrieval_mode"), "calculator")
        self.assertIn("38,250", result["reply"])

    def test_a_rate_question_is_still_answered_from_the_table(self) -> None:
        result = self._turn("What is the VAT rate in Uganda?", attachment="beside")
        self.assertEqual((result or {}).get("retrieval_mode"), "calculator")

    def test_a_question_about_the_document_is_left_to_the_answer_that_reads_it(self) -> None:
        """The rate table would say 18% to "What is the VAT rate on this invoice?", whatever the invoice says."""
        for question in ("What is the VAT rate on this invoice?", "What is the PAYE on UGX 500,000 a month?"):
            self.assertIsNotNone(self._turn(question, attachment="beside"))
            self.assertIsNone(self._turn(question, attachment="subject"))

    def test_a_calculation_with_a_figure_missing_is_not_asked_for(self) -> None:
        question = "How much PAYE should I pay?"
        asked = self._turn(question, attachment="none")
        self.assertIsNotNone(asked)
        self.assertIsNone(self._turn(question, attachment="beside"), "the salary may be in the document")

    def test_the_tin_clarification_question_is_not_asked(self) -> None:
        question = "How do I register for a TIN?"
        self.assertIsNotNone(self._turn(question, attachment="none"))
        self.assertIsNone(self._turn(question, attachment="beside"))

    def test_a_skipped_question_opens_no_guided_flow(self) -> None:
        thread = f"t-{uuid.uuid4().hex[:8]}"
        skipped = self.model._maybe_handle_fast_paths(
            message="Do I need to register for VAT?", rewritten="Do I need to register for VAT?", thread_id=thread, locale="en", attachment="beside"
        )
        self.assertIsNone(skipped)
        self.assertIsNone(db.get_workflow_session(pipeline._state_conversation_id(thread)))


def test_both_chat_paths_pass_the_role_they_worked_out_for_the_turn() -> None:
    """Six calls, three on each route. Each passes the name `attachment`, never a constant, and
    each route binds that name from `attachment_role(attachments, ...)`."""
    tree = ast.parse(PIPELINE.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_maybe_handle_fast_paths"
    ]
    assert len(calls) == 6
    for call in calls:
        passed = [keyword.value for keyword in call.keywords if keyword.arg == "attachment"]
        assert len(passed) == 1 and isinstance(passed[0], ast.Name) and passed[0].id == "attachment", ast.unparse(call)[:80]
    bound = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "attachment" for target in node.targets)
    ]
    assert len(bound) == 2
    for node in bound:
        assert isinstance(node.value, ast.Call) and getattr(node.value.func, "id", "") == "attachment_role"
        assert isinstance(node.value.args[0], ast.Name) and node.value.args[0].id == "attachments"

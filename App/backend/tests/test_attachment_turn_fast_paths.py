"""Beside a readable document a fast path may answer but may not ask (ADR-0068 part 2).

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
from app.rag.pipeline import has_readable_document

PIPELINE = Path(pipeline.__file__)


class ReadableDocumentTest(unittest.TestCase):
    def test_a_document_with_text_could_hold_the_missing_figure(self) -> None:
        self.assertTrue(has_readable_document([types.SimpleNamespace(text="Gross salary UGX 1,200,000")]))
        self.assertTrue(has_readable_document([types.SimpleNamespace(text=" "), types.SimpleNamespace(text="VAT 18%")]))

    def test_no_document_or_one_with_no_text_cannot(self) -> None:
        """A scan with no extracted text made every asking path stay quiet, and the taxpayer was
        left with nothing to answer. It counts as no document."""
        for attachments in (None, [], [types.SimpleNamespace(text="   ")], [types.SimpleNamespace(text=None)]):
            self.assertFalse(has_readable_document(attachments))


class AttachmentTurnFastPathsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        db.init_db()  # the paths that ask write a workflow row; the file must pass when run alone
        cls.model = pipeline.ChatModel()

    def _turn(self, message: str, *, beside_document: bool, thread: str | None = None) -> dict | None:
        return self.model._maybe_handle_fast_paths(
            message=message,
            rewritten=message,
            thread_id=thread or f"t-{uuid.uuid4().hex[:8]}",
            locale="en",
            beside_document=beside_document,
        )

    def test_another_countrys_tax_is_still_declined(self) -> None:
        result = self._turn("What is the VAT rate in Kenya?", beside_document=True)
        self.assertEqual((result or {}).get("retrieval_mode"), "out_of_jurisdiction")

    def test_a_question_with_its_own_figures_still_gets_the_exact_answer(self) -> None:
        """Also when it mentions a document. A version that sent every message naming one to the
        slower answer was failed by an independent run of the web app's route."""
        for question in (
            "What is the PAYE on UGX 500,000 a month?",
            "I have attached my payslip. What is the PAYE on UGX 500,000 a month?",
            "What is the PAYE on UGX 500,000 a month? That is what my contract says.",
        ):
            with self.subTest(question=question):
                result = self._turn(question, beside_document=True)
                self.assertEqual((result or {}).get("retrieval_mode"), "calculator")
                self.assertIn("38,250", result["reply"])

    def test_a_rate_question_is_still_answered_from_the_table(self) -> None:
        for question in ("What is the VAT rate in Uganda?", "What is the VAT rate? See attached.", "What is the VAT rate on this invoice?"):
            with self.subTest(question=question):
                beside, alone = self._turn(question, beside_document=True), self._turn(question, beside_document=False)
                self.assertEqual((beside or {}).get("retrieval_mode"), "calculator")
                self.assertEqual(beside["reply"], alone["reply"])

    def test_a_calculation_with_a_figure_missing_is_not_asked_for(self) -> None:
        question = "How much PAYE should I pay?"
        self.assertIsNotNone(self._turn(question, beside_document=False))
        self.assertIsNone(self._turn(question, beside_document=True), "the salary may be in the document")

    def test_the_tin_clarification_question_is_not_asked(self) -> None:
        question = "How do I register for a TIN?"
        self.assertIsNotNone(self._turn(question, beside_document=False))
        self.assertIsNone(self._turn(question, beside_document=True))

    def test_a_bare_amount_is_not_met_with_the_menu(self) -> None:
        self.assertIsNotNone(self._turn("500,000", beside_document=False))
        self.assertIsNone(self._turn("500,000", beside_document=True))

    def test_a_skipped_question_opens_no_guided_flow(self) -> None:
        thread = f"t-{uuid.uuid4().hex[:8]}"
        self.assertIsNone(self._turn("Do I need to register for VAT?", beside_document=True, thread=thread))
        self.assertIsNone(db.get_workflow_session(pipeline._state_conversation_id(thread)))


def test_both_chat_paths_pass_what_they_worked_out_for_the_turn() -> None:
    """Six calls, three on each route. Each passes the name `beside_document`, never a constant,
    and each route binds that name from `has_readable_document(attachments)`."""
    tree = ast.parse(PIPELINE.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_maybe_handle_fast_paths"
    ]
    assert len(calls) == 6
    for call in calls:
        passed = [keyword.value for keyword in call.keywords if keyword.arg == "beside_document"]
        assert len(passed) == 1 and isinstance(passed[0], ast.Name) and passed[0].id == "beside_document", ast.unparse(call)[:80]
    bound = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "beside_document" for target in node.targets)
    ]
    assert len(bound) == 2
    for node in bound:
        assert isinstance(node.value, ast.Call) and getattr(node.value.func, "id", "") == "has_readable_document"
        assert [getattr(arg, "id", "") for arg in node.value.args] == ["attachments"]

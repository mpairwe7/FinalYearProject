"""On a turn that carries a document, a fast path may answer but may not ask (ADR-0068 part 2).

The HF Space's build script made the REST path skip every fast path on such a turn. That also
skipped the decline for another country's taxes, and the streaming path, which the web app uses,
was never patched, so the two routes disagreed (G698).
"""

from __future__ import annotations

import ast
import unittest
import uuid
from pathlib import Path

from app.rag import pipeline

PIPELINE = Path(pipeline.__file__)


class AttachmentTurnFastPathsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = pipeline.ChatModel()

    def _turn(self, message: str, *, has_attachment: bool) -> dict | None:
        return self.model._maybe_handle_fast_paths(
            message=message, rewritten=message, thread_id=f"t-{uuid.uuid4().hex[:8]}", locale="en", has_attachment=has_attachment
        )

    def test_another_countrys_tax_is_still_declined(self) -> None:
        result = self._turn("What is the VAT rate in Kenya?", has_attachment=True)
        self.assertEqual((result or {}).get("retrieval_mode"), "out_of_jurisdiction")

    def test_a_question_with_its_own_figures_still_gets_the_exact_answer(self) -> None:
        result = self._turn("What is the PAYE on UGX 500,000 a month?", has_attachment=True)
        self.assertEqual((result or {}).get("retrieval_mode"), "calculator")
        self.assertIn("38,250", result["reply"])

    def test_a_rate_question_is_still_answered_from_the_table(self) -> None:
        result = self._turn("What is the VAT rate in Uganda?", has_attachment=True)
        self.assertEqual((result or {}).get("retrieval_mode"), "calculator")
        self.assertIn("18%", result["reply"])

    def test_a_calculation_with_a_figure_missing_is_not_asked_for(self) -> None:
        question = "How much PAYE should I pay?"
        asked = self._turn(question, has_attachment=False)
        self.assertIsNotNone(asked, "without a document the calculator asks for the salary")
        self.assertIsNone(self._turn(question, has_attachment=True), "the salary may be in the document")

    def test_the_tin_clarification_question_is_not_asked(self) -> None:
        question = "How do I register for a TIN?"
        self.assertIsNotNone(self._turn(question, has_attachment=False))
        self.assertIsNone(self._turn(question, has_attachment=True))

    def test_a_skipped_question_opens_no_guided_flow(self) -> None:
        thread = f"t-{uuid.uuid4().hex[:8]}"
        self.model._maybe_handle_fast_paths(
            message="Do I need to register for VAT?", rewritten="Do I need to register for VAT?", thread_id=thread, locale="en", has_attachment=True
        )
        from app.db import database as db

        self.assertIsNone(db.get_workflow_session(pipeline._state_conversation_id(thread)))


def test_both_chat_paths_say_whether_the_turn_carries_a_document() -> None:
    """The script patched `generate()` only, so SSE and REST disagreed. Every call passes the flag."""
    tree = ast.parse(PIPELINE.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_maybe_handle_fast_paths"
    ]
    assert len(calls) >= 6
    assert all(any(keyword.arg == "has_attachment" for keyword in call.keywords) for call in calls)

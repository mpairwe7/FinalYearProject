"""Streaming parity for deterministic procedural replies.

The REST path has always short-circuited TIN/return-filing questions through
``_deterministic_procedure_reply`` with faithfulness 1.0. The streaming path
(SSE/WS) used to re-synthesise the same curated KB answer via the LLM and
token-overlap score it — so the identical question earned a lower score in
the chat UI. These tests pin the ported fast path:

  * ``generate_retrieval_only`` returns a ``_short_circuit`` result carrying
    the curated score and judge verdict;
  * ``run_chat_turn`` emits it as one bundled payload (metadata + single
    token frame) without entering the LLM stream.
"""

from __future__ import annotations

import asyncio
import types
import unittest
import unittest.mock as mock

_TIN_HIT = {
    "source": "ura_instant_tin_application_faqs.csv",
    "question": "How do I apply for an instant TIN?",
    "answer": (
        "Go to ura.go.ug, click Get a TIN, choose Instant TIN, select "
        "Individual, enter your NIN, confirm you are not a robot, and submit."
    ),
    "text": "instant TIN application steps",
    "score_rrf": 0.9,
}


class GenerateRetrievalOnlyDeterministicTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import database as db

        db.init_db()
        from app import service

        cls.model = service.ChatModel()

    def test_tin_query_short_circuits_with_curated_score(self) -> None:
        from app import service

        with mock.patch.object(
            service.ChatModel, "_priority_faq_hits", return_value=[dict(_TIN_HIT)]
        ), mock.patch.object(
            service, "_simple_search", return_value=[]
        ), mock.patch.object(
            service, "needs_clarification", return_value=""
        ), mock.patch.object(
            service.ChatModel, "_maybe_handle_workflow", return_value=None
        ), mock.patch.object(
            self.model._cache, "get", return_value=None
        ), mock.patch.object(
            self.model._cache, "put"
        ) as cache_put:
            # Typed as individual so the deterministic template answers
            # directly (untyped asks now clarify individual vs organisation
            # first — pinned in test_hf_quality_fixes.py).
            out = self.model.generate_retrieval_only(
                message="how do I register for a TIN as an individual"
            )

        self.assertTrue(out.get("_short_circuit"))
        self.assertEqual(out["faithfulness_score"], 1.0)
        judge = out["response_judge"]
        self.assertEqual(judge["decision"], "approve")
        self.assertEqual(judge["confidence_band"], "high")
        self.assertIn("curated deterministic template", judge["reasons"])
        self.assertEqual(out["retrieval_mode"], "faq_priority")
        self.assertIn("1. Go to ura.go.ug", out["reply"])
        self.assertTrue(out["_hits"])
        # Cached for future turns — without the internal underscore keys.
        cache_put.assert_called_once()
        cached = cache_put.call_args.args[1]
        self.assertFalse([k for k in cached if k.startswith("_")])

    def test_workflow_prompt_gets_empathy_prefix_when_distressed(self) -> None:
        from app import service
        from app.text_signals import empathy_ack

        workflow_stub = {
            "reply": "Are you registering as an **individual** or a **company**?",
            "retrieval_mode": "workflow",
            "faithfulness_score": None,
        }
        with mock.patch.object(
            service.ChatModel, "_maybe_handle_workflow", return_value=dict(workflow_stub)
        ):
            distressed = self.model.generate_retrieval_only(
                message="I'm so frustrated, I still can't register for a TIN!!"
            )
        with mock.patch.object(
            service.ChatModel, "_maybe_handle_workflow", return_value=dict(workflow_stub)
        ):
            calm = self.model.generate_retrieval_only(
                message="I would like to register for a TIN"
            )

        self.assertTrue(
            distressed["reply"].startswith(empathy_ack("frustration")), distressed["reply"]
        )
        self.assertEqual(calm["reply"], workflow_stub["reply"])


class _ShortCircuitFakeModel:
    """Stand-in model returning a deterministic short-circuit result."""

    name = "test-model"

    def __init__(self) -> None:
        self._cache = types.SimpleNamespace(
            put=lambda *a, **k: None, get=lambda *a, **k: None
        )

    def generate_retrieval_only(self, **kwargs):
        return {
            "reply": "To register for an **instant TIN**:\n\n1. Go to ura.go.ug",
            "sources": ["ura_instant_tin_application_faqs.csv"],
            "citations": [],
            "faithfulness_score": 1.0,
            "retrieval_mode": "faq_priority",
            "model": self.name,
            "conversation_id": "conv1",
            "locale": "en",
            "escalation_required": False,
            "escalation_reason": "",
            "agent_role": "rag_answerer",
            "handoff": None,
            "response_judge": {
                "decision": "approve",
                "final_decision": "approve",
                "applied_revision": False,
                "reasons": ["curated deterministic template"],
                "confidence_band": "high",
            },
            "next_actions": [],
            "ticket_id": "",
            "_hits": [dict(_TIN_HIT)],
            "_history": [],
            "_rewritten": kwargs.get("message", ""),
            "_short_circuit": True,
        }


class RunChatTurnShortCircuitTest(unittest.TestCase):
    def test_deterministic_result_streams_as_single_bundle(self) -> None:
        from app import service

        model = _ShortCircuitFakeModel()

        async def _collect():
            out = []
            async for ev in service.run_chat_turn(
                model,
                message="how do I register for a TIN",
                conversation_id=None,
                top_k=4,
                locale="en",
                session_id="s",
                request_id="r",
                user_id=None,
                tenant_id="default",
            ):
                out.append(ev)
            return out

        def _must_not_stream(**kwargs):
            raise AssertionError("LLM stream must not run for deterministic replies")

        with mock.patch.object(
            service.llm_module, "is_available", return_value=True
        ), mock.patch.object(service, "stream_llm_tokens", _must_not_stream):
            events = _run_async(_collect())

        kinds = [t for (t, _) in events]
        self.assertEqual(kinds.count("token"), 1)
        self.assertIn("done", kinds)
        self.assertIn("_log", kinds)

        metadata = [p for (t, p) in events if t == "metadata"][0]
        self.assertEqual(metadata["faithfulness_score"], 1.0)
        self.assertEqual(
            metadata["response_judge"]["confidence_band"], "high"
        )

        token = [p for (t, p) in events if t == "token"][0]
        self.assertIn("1. Go to ura.go.ug", token)


def _run_async(coro):
    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()

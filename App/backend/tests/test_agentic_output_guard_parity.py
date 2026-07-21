"""P0-3 regression: the agentic (tool-use) branch and the token-streaming
branch of ``run_chat_turn`` must enforce the SAME post-generation guard
pipeline (response judge + claim verification + grounded revision +
escalation), not just a bare faithfulness score.

Two layers:
  * Unit: ``_apply_output_guards`` actually runs claim verification and feeds
    the report into the response judge.
  * Integration: both ``tool_use`` on and off route the finished reply through
    ``_apply_output_guards`` and emit a grounding event carrying the judge.
"""

from __future__ import annotations

import asyncio
import types
import unittest
import unittest.mock as mock


class _GuardFakeModel:
    """Minimal stand-in exposing just what ``_apply_output_guards`` calls."""

    def __init__(self) -> None:
        self.judge_calls: list[dict] = []

    def _evaluate_response_judge(self, **kwargs):
        self.judge_calls.append(kwargs)
        return {
            "decision": "approve",
            "final_decision": "approve",
            "reasons": [],
            "confidence_band": "high",
            "revised_reply": "",
        }

    def _build_handoff_packet(self, **kwargs):
        return {"priority": "normal"}

    def _maybe_create_ticket(self, **kwargs):
        return ""


class ApplyOutputGuardsUnitTest(unittest.TestCase):
    def test_runs_claim_verification_and_feeds_judge(self) -> None:
        from app import service
        from app.guardrails import OutputGuard

        model = _GuardFakeModel()
        report = {"decision": "approve", "score": 1.0, "claim_count": 1}

        with mock.patch.object(service, "verify_claims", return_value=report) as vc:
            guard = service._apply_output_guards(
                model,
                message="what is the VAT rate?",
                reply="The standard VAT rate is 18% [1].",
                hits=[{"text": "The VAT standard rate is 18%.", "score_rerank": 0.8}],
                citations=[{"marker": "1", "text": "The VAT standard rate is 18%."}],
                conversation_history=None,
                session_id=None,
                conversation_id="c1",
                output_guard=OutputGuard(),
            )

        # Claim verification ran and its report was passed into the judge.
        vc.assert_called_once()
        self.assertEqual(len(model.judge_calls), 1)
        self.assertEqual(model.judge_calls[0]["claim_report"], report)
        self.assertEqual(guard["response_judge"]["claim_verification"], report)
        self.assertFalse(guard["revised"])
        self.assertEqual(guard["reply"], "The standard VAT rate is 18% [1].")


class _FakeModel:
    """Stand-in chat model for driving run_chat_turn end-to-end."""

    name = "test-model"

    def __init__(self) -> None:
        self._cache = types.SimpleNamespace(
            put=lambda *a, **k: None, get=lambda *a, **k: None
        )

    def generate_retrieval_only(self, **kwargs):
        return {
            "_hits": [
                {"text": "VAT standard rate is 18%.", "source": "vat.pdf", "score_rerank": 0.8}
            ],
            "retrieval_mode": "hybrid",
            "sources": ["vat.pdf"],
            "citations": [{"marker": "1", "source": "vat.pdf", "text": "VAT standard rate is 18%."}],
            "_history": [],
            "_rewritten": kwargs.get("message", ""),
            "_personalization_context": "",
            "conversation_id": "conv1",
            "agent_role": "rag_answerer",
            "reply": "VAT is 18% [1].",
            "model": "test-model",
            "locale": kwargs.get("locale", "en"),
            "next_actions": [],
        }


class GuardParityIntegrationTest(unittest.TestCase):
    def tearDown(self) -> None:
        from app.flags import flags

        flags.clear("tool_use")

    @staticmethod
    def _spy_guard(calls):
        def _spy(model, **kwargs):  # noqa: ANN001
            calls.append(kwargs)
            return {
                "reply": kwargs["reply"],
                "faithfulness": 0.9,
                "escalate": False,
                "escalation_reason": "",
                "response_judge": {
                    "decision": "approve",
                    "final_decision": "approve",
                    "claim_verification": {"decision": "approve"},
                },
                "handoff": None,
                "ticket_id": "",
                "revised": False,
                "claim_report": {"decision": "approve"},
            }

        return _spy

    def _run(self, model):
        from app import service

        base = dict(
            message="what is vat?",
            conversation_id=None,
            top_k=4,
            locale="en",
            session_id="s",
            request_id="r",
            user_id=None,
            tenant_id="default",
        )

        async def _collect():
            out = []
            async for ev in service.run_chat_turn(model, **base):
                out.append(ev)
            return out

        return asyncio.run(_collect())

    def test_both_branches_invoke_unified_guard(self) -> None:
        from app import service
        from app.flags import flags

        calls: list[dict] = []
        model = _FakeModel()

        async def _fake_agentic(**kwargs):  # noqa: ANN003
            yield ("token", "VAT is 18% [1].")
            yield ("_full_reply", "VAT is 18% [1].")

        def _fake_stream(**kwargs):  # noqa: ANN003
            yield "VAT is 18% [1]."

        with mock.patch.object(service, "_apply_output_guards", self._spy_guard(calls)), \
                mock.patch.object(service.llm_module, "is_available", return_value=True):
            # Agentic branch (tool_use ON)
            flags.set("tool_use", True)
            with mock.patch.object(service, "_stream_agentic_turn", _fake_agentic):
                ev_agentic = self._run(model)
            flags.clear("tool_use")
            # Token-streaming branch (tool_use OFF)
            with mock.patch.object(service, "stream_llm_tokens", _fake_stream):
                ev_stream = self._run(model)

        # The unified guard was invoked once on each path.
        self.assertEqual(len(calls), 2)
        # Both paths emit a grounding event carrying the response judge.
        for events in (ev_agentic, ev_stream):
            grounding = [p for (t, p) in events if t == "grounding"]
            self.assertTrue(grounding, "expected a grounding event")
            self.assertIn("response_judge", grounding[0])
            self.assertIsNotNone(grounding[0]["response_judge"])


if __name__ == "__main__":
    unittest.main()

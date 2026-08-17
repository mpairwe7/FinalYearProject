"""EI parity for the extractive fallback (every LLM tier unavailable).

The tone_hint only adapts replies that reach a model. When generation falls
back to the best-hit extractive answer — local LLM off/failed AND cloud
fallback unavailable — the reply must still open with the same empathy
acknowledgment the deterministic branches use, on the REST path and on both
streaming fallback emissions. The semantic cache must keep the neutral copy
so a calm user never inherits someone else's empathy opener.
"""

from __future__ import annotations

import asyncio
import types
import unittest
import unittest.mock as mock

from app.text_signals import empathy_ack

_VAT_HIT = {
    "source": "ura_taxation_handbook_faqs.csv",
    "question": "What is the VAT registration threshold?",
    "answer": (
        "You must register for VAT when your annual taxable turnover exceeds "
        "UGX 150 million. Apply on ura.go.ug under e-Services."
    ),
    "text": (
        "Question: What is the VAT registration threshold?\n"
        "Answer: You must register for VAT when your annual taxable turnover "
        "exceeds UGX 150 million. Apply on ura.go.ug under e-Services."
    ),
    "score_rrf": 0.9,
}

_ALL_ACKS = tuple(empathy_ack(k) for k in ("frustration", "anxiety", "urgency"))


def _extractive_seams(model):
    """Patch generate() past the fast paths so it reaches the extractive branch."""
    from app import service

    return (
        # False for everything except semantic_cache: this test's own point is
        # that the cache keeps the neutral copy (see module docstring), so a
        # blanket False here — which used to be harmless — now also disables
        # the cache.put() gate service.py added (`flags.is_enabled("semantic_cache")`),
        # silently turning off the exact behavior these tests assert on.
        mock.patch.object(
            service.flags, "is_enabled", side_effect=lambda name, **kw: name == "semantic_cache"
        ),
        mock.patch.object(
            service.ChatModel, "_priority_faq_hits", return_value=[dict(_VAT_HIT)]
        ),
        mock.patch.object(service, "_simple_search", return_value=[]),
        mock.patch.object(service, "needs_clarification", return_value=""),
        mock.patch.object(service.ChatModel, "_maybe_handle_fast_paths", return_value=None),
        mock.patch.object(
            service.ChatModel,
            "_deterministic_procedure_reply",
            return_value=("", False),
        ),
        mock.patch.object(
            service.ChatModel,
            "_evaluate_response_judge",
            return_value={
                "decision": "approve",
                "final_decision": "approve",
                "applied_revision": False,
                "reasons": [],
                "confidence_band": "high",
            },
        ),
        mock.patch.object(model._cache, "get", return_value=None),
        mock.patch.object(model._cache, "put"),
    )


class ExtractiveFallbackEmpathyRestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import database as db

        db.init_db()
        from app import service

        cls.model = service.ChatModel()

    def _generate(self, message: str):
        patches = _extractive_seams(self.model)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[
            5
        ], patches[6], patches[7], patches[8] as cache_put:
            return self.model.generate(message=message), cache_put

    def test_distressed_extractive_reply_opens_with_ack(self) -> None:
        result, cache_put = self._generate(
            "I'm really worried about the VAT threshold, please help me"
        )
        self.assertTrue(
            result["reply"].startswith(empathy_ack("anxiety")), result["reply"][:80]
        )
        # Scored on the neutral copy; the ack is courtesy and never dilutes.
        self.assertEqual(result["faithfulness_score"], 1.0)
        # The cache stores the neutral copy — no empathy opener.
        cache_put.assert_called_once()
        cached = cache_put.call_args.args[1]
        self.assertFalse(
            any(cached["reply"].startswith(a) for a in _ALL_ACKS), cached["reply"][:80]
        )

    def test_calm_extractive_reply_is_unprefixed(self) -> None:
        result, _ = self._generate("What is the VAT registration threshold?")
        self.assertFalse(
            any(result["reply"].startswith(a) for a in _ALL_ACKS), result["reply"][:80]
        )

    def test_llm_empty_reply_falls_back_with_ack(self) -> None:
        from app import service

        with mock.patch.object(service, "_cloud_llm_ready", return_value=True), \
                mock.patch.object(service, "_call_llm_with_deadline", return_value=""):
            result, cache_put = self._generate(
                "My VAT deadline is tomorrow and I fear a penalty — what is the threshold?"
            )
        self.assertTrue(
            result["reply"].startswith(empathy_ack("urgency")), result["reply"][:80]
        )
        cached = cache_put.call_args.args[1]
        self.assertFalse(any(cached["reply"].startswith(a) for a in _ALL_ACKS))


class _ExtractiveFakeModel:
    """Stand-in returning the non-short-circuit payload generate_retrieval_only builds."""

    name = "test-model"

    def __init__(self, distress: str = "") -> None:
        self._distress = distress
        self._cache = types.SimpleNamespace(
            put=lambda *a, **k: None, get=lambda *a, **k: None
        )

    def generate_retrieval_only(self, **kwargs):
        return {
            "reply": _VAT_HIT["answer"],
            "sources": [_VAT_HIT["source"]],
            "citations": [],
            "faithfulness_score": None,
            "retrieval_mode": "keyword",
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
                "reasons": [],
                "confidence_band": "high",
            },
            "next_actions": [],
            "ticket_id": "",
            "_hits": [dict(_VAT_HIT)],
            "_history": [],
            "_rewritten": kwargs.get("message", ""),
            "_personalization_context": "",
            "_tone_hint": "",
            "_distress": self._distress,
        }


def _collect_turn(model):
    from app import service

    async def _run():
        out = []
        async for ev in service.run_chat_turn(
            model,
            message="what is the VAT threshold",
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

    return asyncio.run(_run())


class StreamFallbackEmpathyTest(unittest.TestCase):
    def test_no_llm_branch_prefixes_ack(self) -> None:
        from app import service

        with mock.patch.object(
            service.llm_module, "is_available", return_value=False
        ), mock.patch.object(service, "_cloud_llm_ready", return_value=False):
            events = _collect_turn(_ExtractiveFakeModel(distress="frustration"))

        token = [p for (t, p) in events if t == "token"][0]
        self.assertTrue(token.startswith(empathy_ack("frustration")), token[:80])

    def test_no_llm_branch_calm_is_unprefixed(self) -> None:
        from app import service

        with mock.patch.object(
            service.llm_module, "is_available", return_value=False
        ), mock.patch.object(service, "_cloud_llm_ready", return_value=False):
            events = _collect_turn(_ExtractiveFakeModel(distress=""))

        token = [p for (t, p) in events if t == "token"][0]
        self.assertFalse(any(token.startswith(a) for a in _ALL_ACKS), token[:80])

    def test_empty_stream_fallback_prefixes_ack(self) -> None:
        from app import service

        def _empty_stream(**kwargs):
            return iter(())

        with mock.patch.object(
            service.llm_module, "is_available", return_value=True
        ), mock.patch.object(service, "stream_llm_tokens", _empty_stream):
            events = _collect_turn(_ExtractiveFakeModel(distress="anxiety"))

        token = [p for (t, p) in events if t == "token"][0]
        self.assertTrue(token.startswith(empathy_ack("anxiety")), token[:80])
        log = [p for (t, p) in events if t == "_log"][0]
        self.assertTrue(log["full_reply"].startswith(empathy_ack("anxiety")))


if __name__ == "__main__":
    unittest.main()

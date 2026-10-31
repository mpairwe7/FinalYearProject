"""A taxpayer can tell what they are reading, and an auditor can tell who wrote it.

With every model tier down the service answered with a raw passage and said nothing,
so a fallback looked like a considered answer to the question asked (OWASP ASI09).
And ``result["model"]`` names the configured model whichever backend ran, so the
ledger could not show whether an answer came from a local model, the reasoner or a
cloud provider, which also decides whether the question left Uganda (DPPA 2019 s.19).
"""

from __future__ import annotations

import asyncio
import types
import uuid
from typing import Any

import pytest
from app import service as service_module
from app.audit import turns
from app.flags import flags
from app.guardrails import OutputGuard
from app.providers import breakers
from app.providers.routing import ModelTier
from app.service import ChatModel

HIT = {"text": "The standard VAT rate in Uganda is 18 percent.", "source": "vat.pdf", "score_rerank": 0.9}
CITATION = {"marker": "1", "ref": "[1]", "source": "vat.pdf", "text": HIT["text"]}
UNAVAILABLE = service_module.DEGRADED_DISCLOSURES["generation_unavailable"]


class Model:
    name = "test-model"

    def __init__(self) -> None:
        self._cache = types.SimpleNamespace(put=lambda *a, **k: None, get=lambda *a, **k: None)
        self.audited: list[dict[str, Any]] = []

    def generate_retrieval_only(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "_hits": [HIT],
            "retrieval_mode": "hybrid",
            "sources": ["vat.pdf"],
            "citations": [CITATION],
            "_history": [],
            "_rewritten": kwargs.get("message", ""),
            "_personalization_context": "",
            "conversation_id": "conv-1",
            "agent_role": "rag_answerer",
            "reply": HIT["text"],
            "model": "test-model",
            "locale": "en",
            "next_actions": [],
        }

    def record_turn_audit(self, **kwargs: Any) -> bool:
        self.audited.append(kwargs)
        return True

    def _evaluate_response_judge(self, **_kwargs: Any) -> dict[str, Any]:
        return {"decision": "approve", "final_decision": "approve", "reasons": [], "confidence_band": "high"}

    def _build_handoff_packet(self, **_kwargs: Any) -> dict[str, Any]:
        return {"priority": "normal"}

    def _maybe_create_ticket(self, **_kwargs: Any) -> str:
        return ""


def run_turn(model: Model, **overrides: Any) -> list[tuple[str, Any]]:
    arguments = {
        "message": "What is the VAT rate?",
        "conversation_id": "conv-1",
        "top_k": 4,
        "locale": "en",
        "session_id": "s",
        "request_id": "r",
        "user_id": "",
        "tenant_id": "default",
    }

    async def collect() -> list[tuple[str, Any]]:
        return [e async for e in service_module.run_chat_turn(model, channel="sse", **{**arguments, **overrides})]

    return asyncio.run(collect())


@pytest.fixture
def stream(monkeypatch):
    def install(*words: str) -> None:
        monkeypatch.setattr(service_module, "stream_llm_tokens", lambda *_a, **_k: iter(words))

    monkeypatch.setattr(service_module.llm_module, "is_available", lambda: True)
    monkeypatch.setattr(service_module.llm_module, "can_generate_in_locale", lambda _locale: True)
    monkeypatch.setattr(service_module, "_answer_in_english", lambda text: text)
    monkeypatch.setattr(service_module, "verify_claims", lambda *_a, **_k: {"decision": "approve"})
    monkeypatch.setattr(OutputGuard, "should_abstain", lambda _self, _hits, locale="en": False)
    return install


class TestAFallbackSaysSo:
    def test_on_the_stream(self, stream) -> None:
        stream()
        model = Model()
        events = run_turn(model)
        text = "".join(payload for kind, payload in events if kind == "token")
        assert HIT["text"] in text
        assert UNAVAILABLE in text
        assert model.audited[0]["result"]["_generation"]["degraded"] == ["generation_unavailable"]

    def test_a_generated_answer_does_not(self, stream) -> None:
        stream("The standard VAT rate is 18 percent. [1]")
        model = Model()
        events = run_turn(model)
        assert UNAVAILABLE not in "".join(str(payload) for kind, payload in events if kind in ("token", "revision"))
        assert model.audited[0]["result"]["_generation"]["degraded"] == []

    def test_a_stream_that_broke_off_says_so(self, stream, monkeypatch) -> None:
        def breaks(**_kwargs: Any):
            yield "You must register first. "
            raise ConnectionError("worker died")

        monkeypatch.setattr(service_module.llm_module, "generate_stream", breaks)
        monkeypatch.setattr(
            service_module,
            "stream_llm_tokens",
            lambda *_a, **_k: service_module._stream_local_then_cloud("q", [HIT], None, "en"),
        )
        service_module._LLM_CIRCUIT.record_success()
        events = run_turn(Model())
        text = "".join(str(payload) for kind, payload in events if kind in ("token", "revision"))
        assert service_module.DEGRADED_DISCLOSURES["stream_interrupted"] in text

    def test_a_cut_off_stream_gets_no_second_answer_appended(self, monkeypatch) -> None:
        def breaks(**_kwargs: Any):
            yield "First half"
            raise ConnectionError("worker died")

        monkeypatch.setattr(service_module.llm_module, "generate_stream", breaks)
        monkeypatch.setattr(service_module.llm_module, "is_available", lambda: True)
        monkeypatch.setattr(service_module, "_stream_cloud_fallback", lambda *_a, **_k: iter(["Second answer"]))
        service_module._LLM_CIRCUIT.record_success()

        tokens = list(service_module._stream_local_then_cloud("q", [], None, "en"))

        assert tokens == ["First half"]


@pytest.fixture(scope="module")
def chat_model() -> ChatModel:
    return ChatModel()


class TestOnRest:
    def test_a_failed_generation_is_disclosed_in_the_reply(self, chat_model, clean_flags, tmp_db, monkeypatch) -> None:
        flags.set("agentic_mode", False)
        monkeypatch.setattr(service_module, "_cloud_llm_ready", lambda: True)
        monkeypatch.setattr(service_module, "_call_llm_with_deadline", lambda **_kw: "")
        monkeypatch.setattr(chat_model, "_llm_available", True)
        hits = [{**HIT, "doc_type": "faq", "answer": HIT["text"]}]
        monkeypatch.setattr(service_module, "_simple_search", lambda *_a, **_k: [])
        monkeypatch.setattr(service_module, "_faq_hits_to_retrieval_hits", lambda *_a, **_k: hits)
        monkeypatch.setattr(OutputGuard, "should_abstain", lambda _self, _hits, locale="en": False)
        result = chat_model.generate("Tell me about keeping records for a small shop", session_id=uuid.uuid4().hex)
        assert result["retrieval_mode"] not in ("abstained", "calculator", "rate_lookup"), result["retrieval_mode"]
        assert result["_generation"]["degraded"] == ["generation_unavailable"]
        assert UNAVAILABLE in result["reply"]

    def test_the_audit_helper_adds_it_on_every_exit(self) -> None:
        model = ChatModel.__new__(ChatModel)
        token = service_module._TURN_GENERATION.set(
            {**service_module._new_generation_record(), "degraded": ["tool_loop_truncated"]}
        )
        try:
            result = {"reply": "Partly checked.", "retrieval_mode": "agentic"}
            model._audit_turn(message="q", result=result, session_id=None, trace_ctx={})
        finally:
            service_module._TURN_GENERATION.reset(token)
        assert service_module.DEGRADED_DISCLOSURES["tool_loop_truncated"] in result["reply"]
        assert result["_generation"]["degraded"] == ["tool_loop_truncated"]


class TestADegradedAnswerIsNotKept:
    """A cached fallback would outlive its outage, and a cache hit carries no disclosure."""

    GROUNDED = "The standard VAT rate in Uganda is 18 percent. [1]"

    def test_on_the_stream(self, stream, monkeypatch) -> None:
        # The same grounded sentence as the control below, so that being cut off is
        # the only reason it is not stored.
        def breaks(**_kwargs: Any):
            yield self.GROUNDED
            raise ConnectionError("worker died")

        monkeypatch.setattr(service_module.llm_module, "generate_stream", breaks)
        monkeypatch.setattr(
            service_module,
            "stream_llm_tokens",
            lambda *_a, **_k: service_module._stream_local_then_cloud("q", [HIT], None, "en"),
        )
        monkeypatch.setattr(service_module.flags, "is_enabled", lambda name, **_kw: name == "semantic_cache")
        service_module._LLM_CIRCUIT.record_success()
        model = Model()
        stored: list[Any] = []
        model._cache.put = lambda *a, **_k: stored.append(a)
        original = model.generate_retrieval_only
        model.generate_retrieval_only = lambda **kw: {**original(**kw), "_cache_allowed": True}

        run_turn(model)

        assert stored == []

    def test_a_healthy_answer_still_is(self, stream, monkeypatch) -> None:
        stream(self.GROUNDED)
        monkeypatch.setattr(service_module.flags, "is_enabled", lambda name, **_kw: name == "semantic_cache")
        model = Model()
        stored: list[Any] = []
        model._cache.put = lambda *a, **_k: stored.append(a)
        original = model.generate_retrieval_only
        model.generate_retrieval_only = lambda **kw: {**original(**kw), "_cache_allowed": True}

        run_turn(model)

        assert len(stored) == 1

    def test_on_rest(self, chat_model, clean_flags, tmp_db, monkeypatch) -> None:
        flags.set("agentic_mode", False)
        flags.set("semantic_cache", True)
        monkeypatch.setattr(service_module, "_cloud_llm_ready", lambda: True)
        monkeypatch.setattr(service_module, "_call_llm_with_deadline", lambda **_kw: "")
        monkeypatch.setattr(chat_model, "_llm_available", True)
        monkeypatch.setattr(service_module, "_simple_search", lambda *_a, **_k: [])
        monkeypatch.setattr(
            service_module,
            "_faq_hits_to_retrieval_hits",
            lambda *_a, **_k: [{**HIT, "doc_type": "faq", "answer": HIT["text"]}],
        )
        monkeypatch.setattr(OutputGuard, "should_abstain", lambda _self, _hits, locale="en": False)
        stored: list[Any] = []
        monkeypatch.setattr(chat_model._cache, "put", lambda *a, **_k: stored.append(a))

        chat_model.generate("Tell me about keeping records for a small shop", session_id=uuid.uuid4().hex)

        assert stored == []


class TestAGraphFallback:
    def test_says_so_on_the_stream_too(self, chat_model, clean_flags, tmp_db, monkeypatch) -> None:
        from app.agents.graphs.state import AgentGraphState, GraphOutcome

        final = AgentGraphState(
            query="q", reply=HIT["text"], outcome=GraphOutcome.ANSWERED,
            hits=[HIT], citations=[CITATION], sources=["vat.pdf"], retrieval_mode="hybrid",
        )

        class Graph:
            def run(self, state: AgentGraphState, thread_id: str = "") -> AgentGraphState:
                service_module._note_generation(degraded="generation_unavailable")
                return final

        flags.set("langgraph", True)
        flags.set("agentic_mode", True)
        monkeypatch.setattr("app.agents.graphs.main_graph.build_main_graph", lambda: Graph())
        token = service_module._TURN_GENERATION.set(service_module._new_generation_record())
        try:
            result = chat_model.generate_retrieval_only(
                "Tell me about keeping records for a small shop", session_id=uuid.uuid4().hex
            )
        finally:
            service_module._TURN_GENERATION.reset(token)
        assert UNAVAILABLE in result["reply"]


class TestTheRowNamesWhatAnswered:
    @pytest.fixture
    def record(self):
        token = service_module._TURN_GENERATION.set(service_module._new_generation_record())
        yield service_module._TURN_GENERATION.get()
        service_module._TURN_GENERATION.reset(token)

    def test_a_cloud_answer(self, record, monkeypatch) -> None:
        monkeypatch.setenv("LLM_FALLBACK_BACKEND", "gemini")
        monkeypatch.setattr(flags, "is_enabled", lambda name, **_kw: name == "cloudflare_fallback")
        monkeypatch.setattr("app.production_readiness.is_sovereign_mode", lambda: False)
        monkeypatch.setattr("app.providers.gateway.gemini_allowed_for", lambda _l: True)
        monkeypatch.setattr("app.providers.config.is_gemini_configured", lambda: True)
        monkeypatch.setattr("app.providers.budget.try_consume_gemini_call", lambda: True)
        monkeypatch.setattr("app.providers.gateway.gemini_generate", lambda *_a, **_k: "A complete answer.")
        breakers.GEMINI_BREAKER.record_success()

        service_module._llm_cloud_fallback("q", [], None, "en")

        assert record["backend"] == "gemini"
        assert record["model"] == "gemini_flash"

    def test_a_local_answer(self, record, monkeypatch) -> None:
        monkeypatch.setattr(service_module.llm_module, "generate", lambda **_kw: "A complete answer.")
        monkeypatch.setattr(service_module.llm_module, "get_last_generation_meta", lambda: {"finish_reason": "stop"})
        service_module._LLM_CIRCUIT.record_success()

        service_module._local_llm_then_cloud("q", [], None, "en", tier=ModelTier.T1)

        assert record["backend"] == "local"
        assert record["tier"] == ModelTier.T1.value
        assert record["model"]

    def test_a_fallback_is_recorded(self, record, monkeypatch) -> None:
        class Reasoner:
            model = "reasoner-model"
            timeout_s = 5.0

        reasoner = Reasoner()
        monkeypatch.setattr(service_module, "_is_non_t1_endpoint", lambda ep: ep is reasoner)
        monkeypatch.setattr(service_module, "get_endpoint", lambda _tier: None)
        monkeypatch.setattr(
            service_module.llm_module,
            "generate",
            lambda **kw: "" if kw.get("endpoint") is reasoner else "T1 answered.",
        )
        monkeypatch.setattr(service_module.llm_module, "get_last_generation_meta", lambda: {"finish_reason": "stop"})
        service_module._LLM_CIRCUIT.record_success()
        service_module._REASONER_CIRCUIT.record_success()

        service_module._local_llm_then_cloud("q", [], None, "en", endpoint=reasoner, tier=ModelTier.T3)

        assert record["fallbacks"] == ["reasoner_empty"]

    def test_outside_a_turn_nothing_is_recorded(self) -> None:
        service_module._note_generation(backend="local", degraded="stream_interrupted")

    def test_it_reaches_the_ledger(self) -> None:
        payload = turns.build_turn_payload(
            message="q",
            result={"reply": "r", "_generation": {"backend": "gemini", "model": "gemini_flash"}},
            channel="sse",
        )
        assert payload["generation"]["backend"] == "gemini"

    def test_the_row_still_holds_no_text(self) -> None:
        payload = turns.build_turn_payload(
            message="my tin is 1000000001",
            result={"reply": "private reply", "_generation": {"backend": "local", "degraded": []}},
            channel="rest",
        )
        assert "1000000001" not in str(payload)
        assert "private reply" not in str(payload)

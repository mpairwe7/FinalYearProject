"""Every turn ends on the record, however it ended.

The ledger's job is to answer "what did the service tell this taxpayer?". A streamed
turn wrote its row only from the final ``_log`` frame, so the turns an auditor most
needs were the ones with none: past the deadline, the client gone, or an exception
after tokens had already gone out. The deadline path also sent no terminal frame, so
a WebSocket client heard nothing further, and the web client could only report a
broken stream.

The pipeline attestation had a similar hole. It added Stage 6 itself when the turn
recorded none, so model text served with no output guard attested ``ok``.
"""

from __future__ import annotations

import asyncio
import threading
import time
import types
from typing import Any
from unittest import mock

import pytest
from app import service as service_module
from app.audit import turns
from app.guardrails import OutputGuard
from app.turn_pipeline import attest_turn

HIT = {"text": "The standard VAT rate in Uganda is 18 percent.", "source": "vat.pdf", "score_rerank": 0.9}
CITATION = {"marker": "1", "ref": "[1]", "source": "vat.pdf", "text": HIT["text"]}


class Model:
    name = "test-model"

    def __init__(self, retrieval: Any = None) -> None:
        self._cache = types.SimpleNamespace(put=lambda *a, **k: None, get=lambda *a, **k: None)
        self.audited: list[dict[str, Any]] = []
        self.done = threading.Event()
        self._retrieval = retrieval

    def generate_retrieval_only(self, **kwargs: Any) -> dict[str, Any]:
        if self._retrieval is not None:
            return self._retrieval(**kwargs)
        service_module._mark_stage("input_guard", time.perf_counter())
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
            "reply": "",
            "model": "test-model",
            "locale": "en",
            "next_actions": [],
        }

    def record_turn_audit(self, **kwargs: Any) -> bool:
        self.audited.append(kwargs)
        self.done.set()
        return True

    def _evaluate_response_judge(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "decision": "approve",
            "final_decision": "approve",
            "reasons": [],
            "confidence_band": "high",
            "revised_reply": "",
        }

    def _build_handoff_packet(self, **_kwargs: Any) -> dict[str, Any]:
        return {"priority": "normal"}

    def _maybe_create_ticket(self, **_kwargs: Any) -> str:
        return ""


ARGUMENTS = {
    "message": "What is the VAT rate?",
    "conversation_id": "conv-1",
    "top_k": 4,
    "locale": "en",
    "session_id": "s",
    "request_id": "r",
    "user_id": "",
    "tenant_id": "default",
}


def turn(model: Model, **overrides: Any) -> Any:
    return service_module.run_chat_turn(model, channel="sse", **{**ARGUMENTS, **overrides})


@pytest.fixture
def stream(monkeypatch):
    """Give the turn a model that streams *words*, *pause* seconds apart."""

    def install(words: list[str], pause: float = 0.0) -> None:
        def tokens(*_args: Any, **_kwargs: Any):
            for word in words:
                if pause:
                    time.sleep(pause)
                yield word

        monkeypatch.setattr(service_module, "stream_llm_tokens", tokens)

    monkeypatch.setattr(service_module.llm_module, "is_available", lambda: True)
    monkeypatch.setattr(service_module.llm_module, "can_generate_in_locale", lambda _locale: True)
    monkeypatch.setattr(service_module, "_answer_in_english", lambda text: text)
    monkeypatch.setattr(OutputGuard, "should_abstain", lambda _self, _hits, locale="en": False)
    return install


def _audited(model: Model) -> dict[str, Any]:
    assert model.done.wait(2.0), "the turn left no audit row"
    assert len(model.audited) == 1
    return model.audited[0]


async def _collect(generator: Any) -> list[tuple[str, Any]]:
    return [event async for event in generator]


class TestTheTaxpayerIsTold:
    def test_a_turn_past_its_deadline_says_so_in_text_and_finishes(self, stream) -> None:
        # Text, not an error frame: the web client drops an error frame's message
        # and shows a generic failure, and it discards nothing it received as a token.
        stream(["The ", "rate ", "is ", "18%. "], pause=0.15)
        model = Model()

        events = asyncio.run(_collect(turn(model, turn_deadline_s=0.2)))

        kinds = [kind for kind, _ in events]
        assert kinds[-2:] == ["done", "_log"]
        assert "deadline_exceeded" in kinds
        assert "error" not in kinds
        assert any("0800 117 000" in str(payload) for kind, payload in events if kind == "token")


class TestEveryTurnIsAudited:
    def test_a_finished_turn_once_and_as_completed(self, stream) -> None:
        stream(["The VAT rate is 18 percent. [1]"])
        model = Model()
        asyncio.run(_collect(turn(model)))
        row = _audited(model)
        assert row["result"]["_disposition"] == "completed"

    def test_past_the_deadline(self, stream) -> None:
        stream(["The ", "rate ", "is ", "18%. "], pause=0.15)
        model = Model()
        asyncio.run(_collect(turn(model, turn_deadline_s=0.2)))
        assert _audited(model)["result"]["_disposition"] == "deadline_exceeded"

    def test_a_client_that_left(self, stream) -> None:
        stream(["The ", "rate ", "is ", "18%. "], pause=0.05)
        polls = {"n": 0}

        def still_there() -> bool:
            polls["n"] += 1
            return polls["n"] < 3

        model = Model()
        asyncio.run(_collect(turn(model, should_continue=still_there)))
        assert _audited(model)["result"]["_disposition"] == "cancelled"

    def test_a_stream_closed_after_text_went_out_records_that_text(self, stream) -> None:
        stream(["The VAT rate is 18 percent. ", "Register online. "], pause=0.05)
        model = Model()

        async def run() -> None:
            generator = turn(model)
            async for kind, _payload in generator:
                if kind == "token":
                    await generator.aclose()
                    break

        asyncio.run(run())
        row = _audited(model)
        assert row["result"]["_disposition"] == "cancelled"
        assert "The VAT rate is 18 percent" in row["result"]["reply"]

    def test_a_turn_that_failed(self) -> None:
        def down(**_kwargs: Any) -> dict[str, Any]:
            raise ConnectionError("database unreachable")

        model = Model(retrieval=down)
        asyncio.run(_collect(turn(model)))
        assert _audited(model)["result"]["_disposition"] == "failed"

    def test_only_a_completed_turn_writes_to_memory(self, stream) -> None:
        stream(["The ", "rate ", "is ", "18%. "], pause=0.15)
        model = Model()
        model._persist_personalization_turn = mock.Mock()
        result = model.generate_retrieval_only(message="q")

        def consenting(**_kwargs: Any) -> dict[str, Any]:
            return {**result, "_memory_consent": True}

        model._retrieval = consenting
        asyncio.run(_collect(turn(model, turn_deadline_s=0.2)))
        _audited(model)
        model._persist_personalization_turn.assert_not_called()

    def test_the_row_carries_the_disposition(self) -> None:
        payload = turns.build_turn_payload(
            message="q", result={"reply": "r", "_disposition": "deadline_exceeded"}, channel="sse"
        )
        assert payload["disposition"] == "deadline_exceeded"

    def test_a_row_with_no_disposition_is_a_completed_turn(self) -> None:
        payload = turns.build_turn_payload(message="q", result={"reply": "r"}, channel="rest")
        assert payload["disposition"] == "completed"

    def test_a_cancelled_turn_has_its_own_outcome(self, stream) -> None:
        from app.analytics import metrics

        stream(["The ", "rate ", "is ", "18%. "], pause=0.05)
        polls = {"n": 0}

        def still_there() -> bool:
            polls["n"] += 1
            return polls["n"] < 3

        key = 'chat_turns_total{channel="sse",outcome="cancelled"}'
        before = metrics.snapshot()["counters"].get(key, 0)
        model = Model()
        asyncio.run(_collect(turn(model, should_continue=still_there)))
        _audited(model)
        assert metrics.snapshot()["counters"].get(key, 0) - before == 1


class TestAttestationIsEvidence:
    def test_model_text_with_no_output_guard_fails(self) -> None:
        verdict = attest_turn({"input_guard": 1.0, "hybrid_search": 5.0, "llm_generate": 900.0})
        assert verdict["ok"] is False
        assert verdict["missing_mandatory_guards"] == ["stage_6_output_guard_and_audit"]

    def test_a_streamed_answer_with_no_output_guard_fails(self) -> None:
        verdict = attest_turn({"input_guard": 1.0, "llm_stream": 900.0}, transport="sse")
        assert verdict["ok"] is False

    def test_the_graph_with_no_output_guard_fails(self) -> None:
        verdict = attest_turn({"input_guard": 1.0, "langgraph_execution": 900.0})
        assert verdict["ok"] is False

    def test_with_a_guard_it_passes(self) -> None:
        verdict = attest_turn({"input_guard": 1.0, "llm_generate": 900.0, "output_guard": 2.0})
        assert verdict["ok"] is True
        assert verdict["guard_basis"] == "verified"

    def test_a_deterministic_exit_owes_no_output_guard(self) -> None:
        verdict = attest_turn({"input_guard": 1.0, "calculator_router": 1.0})
        assert verdict["ok"] is True
        assert verdict["guard_basis"] == "no_model_text"

    def test_a_canned_reply_after_a_model_call_owes_no_output_guard(self) -> None:
        # The tool loop ran, produced nothing usable, and the turn abstained.
        verdict = attest_turn(
            {"input_guard": 1.0, "llm_agentic": 900.0, "abstention_check": 1.0},
            served_model_text=False,
        )
        assert verdict["ok"] is True

    def test_a_streamed_turn_is_attested_in_its_audit_row(self, stream) -> None:
        stream(["The VAT rate is 18 percent. [1]"])
        model = Model()
        asyncio.run(_collect(turn(model)))
        attestation = _audited(model)["result"]["_attestation"]
        assert attestation["transport"] == "sse"
        assert attestation["guard_basis"] == "verified"
        assert attestation["ok"] is True

    def test_the_real_streaming_pipeline_records_its_guards(self, tmp_db, clean_flags, monkeypatch, stream) -> None:
        # Against the real retrieval path, not the stand-in above: a stage that
        # stopped being recorded would otherwise pass unnoticed.
        from app.flags import flags
        from app.service import ChatModel

        flags.set("agentic_mode", False)
        stream(["The VAT rate is 18 percent. [1]"])
        model = ChatModel()
        monkeypatch.setattr(model, "record_turn_audit", lambda **kwargs: captured.update(kwargs) or True)
        captured: dict[str, Any] = {}
        asyncio.run(_collect(turn(model, message="What is the standard VAT rate?")))
        attestation = captured["result"].get("_attestation") or {}
        assert attestation.get("transport") == "sse"
        assert "stage_1_input_guard" not in attestation.get("missing_mandatory_guards", ["stage_1_input_guard"])

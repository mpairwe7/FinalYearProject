"""A dependency that fails must degrade the service, not silently end it.

The primitives existed; what failed was how they compose. A half-open breaker lets
one probe through and waits to hear how it went, so any path that took the probe
and never reported wedged the breaker for the life of the process: the free-tier
budget refusing after the breaker had said yes, the reasoner falling back to T1 and
finding the probe already taken, a client disconnecting mid-stream. Nothing logged
an error, and every later turn quietly got a raw passage.

These tests drive the real call paths with the real breakers.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import itertools
import threading
import time
from typing import Any
from unittest import mock

import pytest
from app import llm
from app import service as service_module
from app.agents.loop_control import ToolCallBudget
from app.analytics import metrics
from app.flags import flags
from app.providers import breakers
from app.providers.routing import ModelTier
from app.resilience import CircuitBreaker, CircuitState, admit


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock():
    fake = Clock()
    with mock.patch("app.resilience.time.monotonic", fake):
        yield fake


@pytest.fixture(autouse=True)
def _closed_breakers():
    live = (
        service_module._LLM_CIRCUIT,
        service_module._REASONER_CIRCUIT,
        breakers.GEMINI_BREAKER,
        breakers.CF_LLM_BREAKER,
    )
    for breaker in live:
        breaker.record_success()
    yield
    for breaker in live:
        breaker.record_success()


def _half_open(breaker: CircuitBreaker) -> None:
    """Trip *breaker* and let its back-off lapse, so the next caller is the probe."""
    for _ in range(breaker.failure_threshold):
        breaker.record_failure()
    breaker._opened_at -= breaker._current_timeout + 1
    assert breaker.state is CircuitState.HALF_OPEN


def _opened(clock: Clock, **kwargs: Any) -> CircuitBreaker:
    defaults = {"name": "t", "failure_threshold": 1, "reset_timeout": 10.0, "max_timeout": 40.0}
    breaker = CircuitBreaker(**{**defaults, **kwargs})
    breaker.record_failure()
    clock.t += 10.1
    return breaker


class TestTheProbeIsALease:
    def test_one_probe_at_a_time_within_the_lease(self, clock) -> None:
        breaker = _opened(clock, probe_timeout=60.0)
        assert breaker.allow_request() is True
        clock.t += 59.0
        assert breaker.allow_request() is False

    def test_a_probe_that_never_reports_does_not_wedge_the_breaker(self, clock) -> None:
        breaker = _opened(clock, probe_timeout=60.0)
        assert breaker.allow_request() is True
        clock.t += 61.0
        assert breaker.allow_request() is True

    def test_the_replacement_probe_can_close_it(self, clock) -> None:
        breaker = _opened(clock, probe_timeout=60.0)
        breaker.allow_request()
        clock.t += 61.0
        breaker.allow_request()
        breaker.record_success()
        assert breaker.state is CircuitState.CLOSED

    def test_a_lost_probe_is_counted(self, clock) -> None:
        breaker = _opened(clock, name="lease_probe_test", probe_timeout=60.0)
        key = 'circuit_breaker_probes_lost_total{breaker="lease_probe_test"}'
        before = metrics.snapshot()["counters"].get(key, 0)
        breaker.allow_request()
        clock.t += 61.0
        breaker.allow_request()
        assert metrics.snapshot()["counters"].get(key, 0) - before == 1

    def test_release_returns_the_probe_without_judging_the_dependency(self, clock) -> None:
        breaker = _opened(clock, probe_timeout=60.0)
        assert breaker.allow_request() is True
        breaker.release()
        assert breaker.state is CircuitState.HALF_OPEN
        assert breaker._current_timeout == 10.0
        assert breaker.allow_request() is True

    def test_release_on_a_closed_breaker_changes_nothing(self, clock) -> None:
        breaker = CircuitBreaker(name="t", failure_threshold=2)
        breaker.record_failure()
        breaker.release()
        breaker.record_failure()
        assert breaker.state is CircuitState.OPEN

    def test_failures_arriving_late_do_not_extend_the_open_window(self, clock) -> None:
        # Calls already in flight when the breaker opens fail one after another.
        # Each used to restart the clock, so recovery kept moving away.
        breaker = CircuitBreaker(name="t", failure_threshold=1, reset_timeout=10.0)
        breaker.record_failure()
        clock.t += 9.0
        breaker.record_failure()
        clock.t += 1.1
        assert breaker.state is CircuitState.HALF_OPEN

    def test_state_changes_are_visible(self, clock) -> None:
        breaker = CircuitBreaker(name="visible_test", failure_threshold=1)
        key = 'circuit_breaker_transitions_total{breaker="visible_test",to="open"}'
        before = metrics.snapshot()["counters"].get(key, 0)
        breaker.record_failure()
        assert metrics.snapshot()["counters"].get(key, 0) - before == 1
        assert metrics.snapshot()["gauges"].get('circuit_breaker_state{breaker="visible_test"}') == 2


class TestAdmit:
    def test_takes_the_slot_and_the_budget(self, clock) -> None:
        breaker = _opened(clock)
        assert admit(breaker, lambda: True) is True
        assert breaker._half_open_probe_in_flight is True

    def test_gives_the_slot_back_when_the_budget_refuses(self, clock) -> None:
        breaker = _opened(clock)
        assert admit(breaker, lambda: False) is False
        assert breaker._half_open_probe_in_flight is False

    def test_spends_no_budget_when_the_breaker_refuses(self, clock) -> None:
        breaker = _opened(clock, probe_timeout=60.0)
        breaker.allow_request()
        spent: list[int] = []
        assert admit(breaker, lambda: spent.append(1) or True) is False
        assert spent == []


def _local_generate(monkeypatch, reply_for) -> list[Any]:
    """Route local generation to *reply_for(endpoint)*; returns the endpoints tried."""
    seen: list[Any] = []

    def fake(**kwargs):
        endpoint = kwargs.get("endpoint")
        seen.append(endpoint)
        return reply_for(endpoint)

    monkeypatch.setattr(service_module.llm_module, "generate", fake)
    monkeypatch.setattr(service_module.llm_module, "get_last_generation_meta", lambda: {"finish_reason": "stop"})
    return seen


class Reasoner:
    base_url = "http://reasoner:8012/v1"
    model = "reasoner-model"
    timeout_s = 0.5


@pytest.fixture
def reasoner(monkeypatch):
    endpoint = Reasoner()
    t1 = mock.Mock(base_url="http://t1:8011/v1", model="t1-model")
    monkeypatch.setattr(service_module, "get_endpoint", lambda _tier: t1)
    monkeypatch.setattr(service_module, "_is_non_t1_endpoint", lambda ep: ep is endpoint)
    return endpoint


class TestNoCallPathWedgesAProviderBreaker:
    def test_a_budget_refusal_during_the_gemini_probe(self, monkeypatch) -> None:
        monkeypatch.setenv("LLM_FALLBACK_BACKEND", "gemini")
        monkeypatch.setattr(flags, "is_enabled", lambda name, **_kw: name == "cloudflare_fallback")
        monkeypatch.setattr("app.production_readiness.is_sovereign_mode", lambda: False)
        monkeypatch.setattr("app.providers.gateway.gemini_allowed_for", lambda _l: True)
        monkeypatch.setattr("app.providers.gateway.cloud_generation_allowed_for", lambda _l: False)
        monkeypatch.setattr("app.providers.config.is_gemini_configured", lambda: True)
        monkeypatch.setattr("app.providers.budget.try_consume_gemini_call", lambda: False)
        _half_open(breakers.GEMINI_BREAKER)

        service_module._llm_cloud_fallback("q", [], None, "en")

        assert breakers.GEMINI_BREAKER._half_open_probe_in_flight is False

    def test_a_budget_refusal_during_the_workers_ai_probe(self, monkeypatch) -> None:
        monkeypatch.setenv("LLM_FALLBACK_BACKEND", "workers_ai")
        monkeypatch.setattr(flags, "is_enabled", lambda name, **_kw: name == "cloudflare_fallback")
        monkeypatch.setattr("app.production_readiness.is_sovereign_mode", lambda: False)
        monkeypatch.setattr("app.providers.gateway.cloud_generation_allowed_for", lambda _l: True)
        monkeypatch.setattr("app.providers.config.is_cloudflare_configured", lambda: True)
        monkeypatch.setattr("app.providers.budget.try_consume_neurons", lambda _n: False)
        monkeypatch.setattr(service_module.llm_module, "_build_messages", lambda **_kw: [])
        _half_open(breakers.CF_LLM_BREAKER)

        service_module._llm_cloud_fallback("q", [], None, "en")

        assert breakers.CF_LLM_BREAKER._half_open_probe_in_flight is False

    def test_a_reasoner_failure_during_the_t1_probe(self, monkeypatch, reasoner) -> None:
        # The reasoner took the shared probe, failed, and fell back to T1, which
        # found the probe taken: no answer, nothing reported, breaker stuck.
        _local_generate(monkeypatch, lambda ep: "" if ep is reasoner else "T1 answered.")
        _half_open(service_module._LLM_CIRCUIT)

        reply = service_module._local_llm_then_cloud(
            "q", [], None, "en", endpoint=reasoner, tier=ModelTier.T3
        )

        assert reply == "T1 answered."
        assert service_module._LLM_CIRCUIT.state is CircuitState.CLOSED

    def test_a_client_leaving_mid_stream_during_the_probe(self, monkeypatch) -> None:
        def stream(**_kwargs):
            yield "first"
            yield "second"

        monkeypatch.setattr(service_module.llm_module, "generate_stream", stream)
        monkeypatch.setattr(service_module.llm_module, "is_available", lambda: True)
        _half_open(service_module._LLM_CIRCUIT)

        tokens = service_module._stream_local_then_cloud("q", [], None, "en")
        assert next(tokens) == "first"
        tokens.close()

        assert service_module._LLM_CIRCUIT._half_open_probe_in_flight is False
        assert service_module._LLM_CIRCUIT.allow_request() is True


class TestALeavingClientIsNotAnOutage:
    def test_cancelling_before_the_first_token_is_not_a_model_failure(self, monkeypatch) -> None:
        def stream(**_kwargs):
            yield "never shown"

        cloud = mock.Mock(side_effect=lambda *a, **k: iter(["cloud answer"]))
        monkeypatch.setattr(service_module.llm_module, "generate_stream", stream)
        monkeypatch.setattr(service_module.llm_module, "is_available", lambda: True)
        monkeypatch.setattr(service_module, "_stream_cloud_fallback", cloud)
        cancelled = threading.Event()
        cancelled.set()

        with mock.patch.object(service_module._LLM_CIRCUIT, "record_failure") as failed:
            out = list(service_module._stream_local_then_cloud("q", [], None, "en", cancel_event=cancelled))

        assert out == []
        failed.assert_not_called()
        cloud.assert_not_called()


class TestTheReasonerHasItsOwnBreaker:
    def test_a_down_reasoner_stops_costing_every_turn_its_deadline(self, monkeypatch, reasoner) -> None:
        seen = _local_generate(monkeypatch, lambda ep: "" if ep is reasoner else "T1 answered.")
        threshold = service_module._REASONER_CIRCUIT.failure_threshold

        for _ in range(threshold):
            service_module._local_llm_then_cloud("q", [], None, "en", endpoint=reasoner, tier=ModelTier.T3)
        seen.clear()
        reply = service_module._local_llm_then_cloud("q", [], None, "en", endpoint=reasoner, tier=ModelTier.T3)

        assert reply == "T1 answered."
        assert reasoner not in seen, "an open reasoner circuit goes straight to T1"

    def test_a_reasoner_outage_leaves_t1_closed(self, monkeypatch, reasoner) -> None:
        _local_generate(monkeypatch, lambda ep: "" if ep is reasoner else "T1 answered.")
        for _ in range(service_module._REASONER_CIRCUIT.failure_threshold + 1):
            service_module._local_llm_then_cloud("q", [], None, "en", endpoint=reasoner, tier=ModelTier.T3)
        assert service_module._REASONER_CIRCUIT.state is CircuitState.OPEN
        assert service_module._LLM_CIRCUIT.state is CircuitState.CLOSED

    def test_a_t1_outage_leaves_the_reasoner_reachable(self, monkeypatch, reasoner) -> None:
        _local_generate(monkeypatch, lambda ep: "Reasoner answered." if ep is reasoner else "")
        for _ in range(service_module._LLM_CIRCUIT.failure_threshold):
            service_module._local_llm_then_cloud("q", [], None, "en")
        assert service_module._LLM_CIRCUIT.state is CircuitState.OPEN
        reply = service_module._local_llm_then_cloud("q", [], None, "en", endpoint=reasoner, tier=ModelTier.T3)
        assert reply == "Reasoner answered."

    def test_the_stream_also_skips_an_open_reasoner(self, monkeypatch, reasoner) -> None:
        seen: list[Any] = []

        def stream(**kwargs):
            seen.append(kwargs.get("endpoint"))
            yield "T1 token."

        monkeypatch.setattr(service_module.llm_module, "generate_stream", stream)
        monkeypatch.setattr(service_module.llm_module, "is_available", lambda: True)
        for _ in range(service_module._REASONER_CIRCUIT.failure_threshold):
            service_module._REASONER_CIRCUIT.record_failure()

        out = list(
            service_module._stream_local_then_cloud("q", [], None, "en", endpoint=reasoner, tier=ModelTier.T3)
        )

        assert out == ["T1 token."]
        assert reasoner not in seen


@pytest.fixture
def tool_loop(monkeypatch):
    """The real tool loop with a scripted model and the real local tools."""
    monkeypatch.setattr(llm, "LLM_BACKEND", "vllm")
    monkeypatch.setattr(llm, "_vllm_ready", lambda: True)
    monkeypatch.setattr(llm, "_select_tools_for_query", lambda _query, names, **_options: names)
    monkeypatch.setattr(llm, "_fit_tools_to_window", lambda _m, specs, **_kw: specs)

    def script(rounds: list[list[dict[str, Any]]]) -> list[Any]:
        calls: list[Any] = []

        def model(messages, *, tools=None, **_kwargs):
            calls.append(len(messages))
            planned = rounds[len(calls) - 1] if len(calls) <= len(rounds) else []
            return {"content": "" if planned else "Done.", "tool_calls": planned}

        monkeypatch.setattr(llm, "_vllm_chat_completion", model)
        return calls

    return script


VAT = {"name": "calculate_vat", "arguments": {"amount": 1000000}}


class TestTheAgentStopsWhenTheTurnDoes:
    def test_a_cancelled_loop_starts_no_further_round(self, tool_loop) -> None:
        rounds = tool_loop([[VAT], [VAT]])
        cancel = threading.Event()

        def stop_after_first_tool(event: dict[str, Any]) -> None:
            if event["type"] == "tool_call.completed":
                cancel.set()

        result = llm.generate_with_tools(
            "VAT on 1,000,000?",
            tool_names=["calculate_vat"],
            cancel_event=cancel,
            event_callback=stop_after_first_tool,
        )

        assert len(rounds) == 1
        assert result["cancelled"] is True

    def test_a_cancelled_loop_dispatches_nothing_it_had_not_started(self, tool_loop, monkeypatch) -> None:
        tool_loop([[VAT]])
        cancel = threading.Event()
        cancel.set()
        client = mock.Mock()
        monkeypatch.setattr("app.mcp.get_client", lambda: client)
        client.available_for.return_value = ["calculate_vat"]
        client.remote_tool_descriptors.return_value = []

        result = llm.generate_with_tools("VAT?", tool_names=["calculate_vat"], cancel_event=cancel)

        client.call_tool.assert_not_called()
        assert result["cancelled"] is True

    def test_a_timed_out_run_is_told_to_stop(self, monkeypatch) -> None:
        stopped = threading.Event()

        def slow_loop(**kwargs):
            # Stands in for a loop between rounds; returns once it has been told.
            stopped.set() if kwargs["cancel_event"].wait(2.0) else None
            return {"text": "", "tool_calls": [], "iterations": 0, "truncated": False}

        monkeypatch.setattr(service_module.llm_module, "generate_with_tools", slow_loop)

        result = service_module._call_llm_agentic("q", [], None, "en", deadline_s=0.05)

        assert result["text"] == ""
        assert stopped.wait(1.0), "the abandoned run kept going"

    def test_a_cancelled_turn_cancels_the_run(self, monkeypatch) -> None:
        stopped = threading.Event()

        def slow_loop(**kwargs):
            stopped.set() if kwargs["cancel_event"].wait(2.0) else None
            return {"text": "", "tool_calls": [], "iterations": 0, "truncated": False}

        monkeypatch.setattr(service_module.llm_module, "generate_with_tools", slow_loop)
        turn_cancel = threading.Event()
        timer = threading.Timer(0.05, turn_cancel.set)
        timer.start()
        service_module._call_llm_agentic("q", [], None, "en", deadline_s=2.0, cancel_event=turn_cancel)
        timer.join()

        assert stopped.is_set()

    def test_the_streamed_agent_notices_the_client_has_gone(self, monkeypatch) -> None:
        monkeypatch.setattr(
            service_module.llm_module,
            "generate_with_tools",
            lambda **kw: kw["cancel_event"].wait(5.0) and {"text": "", "tool_calls": []},
        )

        async def run() -> float:
            cancel = threading.Event()
            polls = {"n": 0}

            async def should_stop() -> bool:
                polls["n"] += 1
                if polls["n"] >= 2:
                    cancel.set()
                    return True
                return False

            started = time.perf_counter()
            async for _ in service_module._stream_agentic_turn(
                rewritten_query="q", hits=[], conversation_history=None, locale="en",
                personalization_context="", tenant_id="default", user_id="", user_role="public",
                granted_purposes=[], cancel_event=cancel, _output_guard=mock.Mock(),
                should_stop=should_stop,
            ):
                pass
            return time.perf_counter() - started

        assert asyncio.run(run()) < 2.0


class TestParallelToolCalls:
    def test_the_same_call_twice_in_one_round_runs_once(self, tool_loop, monkeypatch) -> None:
        tool_loop([[VAT, dict(VAT)], []])
        dispatched: list[str] = []
        real = service_module.llm_module

        from app.mcp import get_client

        client = get_client()
        original = client.call_tool

        def counting(name, arguments, **kwargs):
            dispatched.append(name)
            return original(name, arguments, **kwargs)

        monkeypatch.setattr(client, "call_tool", counting)
        real.generate_with_tools("VAT on 1,000,000?", tool_names=["calculate_vat"])

        assert dispatched == ["calculate_vat"]

    def test_a_duplicate_of_a_failed_action_does_not_report_success(self, tool_loop, monkeypatch) -> None:
        # Side effects run one at a time, and the round is recorded after both, so the
        # duplicate is admitted before anyone knows how the original went.
        from app.mcp import get_client

        same = {"name": "escalate_to_human", "arguments": {"reason": "a"}}
        tool_loop([[same, dict(same)], []])
        monkeypatch.setattr(
            get_client(), "call_tool", lambda *_a, **_k: mock.Mock(result={"ok": False, "error": "queue down"}, ok=False)
        )
        events: list[dict[str, Any]] = []

        result = llm.generate_with_tools(
            "I need a person",
            tool_names=["escalate_to_human"],
            user_id="u1",
            user_role="verified_taxpayer",
            event_callback=events.append,
        )

        outcomes = [call["result"] for call in result["tool_calls"]]
        assert [outcome["ok"] for outcome in outcomes] == [False, False]
        assert [event["ok"] for event in events if event["type"] == "tool_call.completed"] == [False, False]
        assert outcomes[1]["error"] == "queue down"
        assert outcomes[1]["repeated_call"] is True

    def test_a_duplicate_that_was_admitted_first_still_gets_the_result(self, tool_loop, monkeypatch) -> None:
        # In a parallel round, the second identical call can reach the budget first.
        from app.mcp import get_client

        tool_loop([[VAT, dict(VAT)], []])
        monkeypatch.setattr(
            get_client(), "call_tool", lambda *_a, **_k: mock.Mock(result={"ok": False, "error": "offline"}, ok=False)
        )
        real_admit = ToolCallBudget.admit
        order = itertools.count()
        second_admitted = threading.Event()

        def first_caller_admitted_last(self, name, arguments=None, *, iteration=0):
            if next(order) == 0:
                second_admitted.wait(1.0)
                return real_admit(self, name, arguments, iteration=iteration)
            decision = real_admit(self, name, arguments, iteration=iteration)
            second_admitted.set()
            return decision

        monkeypatch.setattr(ToolCallBudget, "admit", first_caller_admitted_last)

        result = llm.generate_with_tools("VAT on 1,000,000?", tool_names=["calculate_vat"])

        assert [call["result"]["error"] for call in result["tool_calls"]] == ["offline", "offline"]

    def test_a_duplicate_is_not_a_success_before_the_original_returns(self) -> None:
        budget = ToolCallBudget()
        budget.admit("lookup", {"tin": "1"}, iteration=0)

        duplicate = budget.admit("lookup", {"tin": "1"}, iteration=0)

        assert duplicate.result["ok"] is False
        assert duplicate.result["repeated_call"] is True

    def test_a_duplicate_keeps_its_note_if_the_original_left_no_result(self) -> None:
        budget = ToolCallBudget()
        budget.admit("lookup", {"tin": "1"}, iteration=0)
        duplicate = budget.admit("lookup", {"tin": "1"}, iteration=0)
        budget.record("lookup", {"tin": "1"}, "not a dict")

        assert budget.settle("lookup", {"tin": "1"}, duplicate.result) == duplicate.result

    def test_tools_with_side_effects_never_overlap(self, tool_loop, monkeypatch) -> None:
        from app.mcp import get_client

        calls = [
            {"name": "escalate_to_human", "arguments": {"reason": "a"}},
            {"name": "escalate_to_human", "arguments": {"reason": "b"}},
        ]
        tool_loop([calls, []])
        client = get_client()
        active = {"now": 0, "peak": 0}
        guard = threading.Lock()

        def overlapping(name, arguments, **_kwargs):
            with guard:
                active["now"] += 1
                active["peak"] = max(active["peak"], active["now"])
            time.sleep(0.05)
            with guard:
                active["now"] -= 1
            return mock.Mock(result={"ok": True}, ok=True)

        monkeypatch.setattr(client, "call_tool", overlapping)
        llm.generate_with_tools(
            "I need a person", tool_names=["escalate_to_human"], user_id="u1", user_role="verified_taxpayer"
        )

        assert active["peak"] == 1

    def test_read_only_calls_still_run_in_parallel(self, tool_loop, monkeypatch) -> None:
        from app.mcp import get_client

        calls = [
            {"name": "calculate_vat", "arguments": {"amount": 1000}},
            {"name": "calculate_vat", "arguments": {"amount": 2000}},
        ]
        tool_loop([calls, []])
        client = get_client()
        barrier = threading.Barrier(2, timeout=2.0)

        def together(name, arguments, **_kwargs):
            barrier.wait()
            return mock.Mock(result={"ok": True}, ok=True)

        monkeypatch.setattr(client, "call_tool", together)
        llm.generate_with_tools("VAT?", tool_names=["calculate_vat"])

        # A failed wait is caught as a tool error, so look at the barrier itself.
        assert not barrier.broken

    def test_pool_threads_see_the_turn(self, tool_loop, monkeypatch) -> None:
        from app.mcp import get_client

        marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker", default="lost")
        marker.set("this turn")
        calls = [
            {"name": "calculate_vat", "arguments": {"amount": 1000}},
            {"name": "calculate_vat", "arguments": {"amount": 2000}},
        ]
        tool_loop([calls, []])
        client = get_client()
        seen: list[str] = []

        def recording(name, arguments, **_kwargs):
            seen.append(marker.get())
            return mock.Mock(result={"ok": True}, ok=True)

        monkeypatch.setattr(client, "call_tool", recording)
        llm.generate_with_tools("VAT?", tool_names=["calculate_vat"])

        assert seen == ["this turn", "this turn"]

    def test_the_budget_holds_under_contention(self) -> None:
        budget = ToolCallBudget(max_total_calls=8, max_calls_per_iteration=100, max_calls_per_tool=100)

        def spend(i: int) -> bool:
            return budget.admit("tool", {"n": i}, iteration=0).should_dispatch

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            admitted = sum(pool.map(spend, range(400)))

        assert admitted == 8
        assert budget.dispatched == 8


class _SlowModel:
    """A fake chat model whose reply is ready at once."""

    name = "test-model"

    def __init__(self, retrieval_seconds: float = 0.0) -> None:
        self._retrieval_seconds = retrieval_seconds

    def generate_retrieval_only(self, **kwargs):
        if self._retrieval_seconds:
            time.sleep(self._retrieval_seconds)
        return {
            "reply": "Hello",
            "retrieval_mode": "greeting",
            "conversation_id": "c1",
            "agent_role": "rag_answerer",
            "locale": kwargs.get("locale", "en"),
            "next_actions": [],
            "sources": [],
            "citations": [],
        }


def _turn(model: Any, **overrides: Any) -> Any:
    arguments = {
        "message": "hello",
        "conversation_id": "c1",
        "top_k": 4,
        "locale": "en",
        "session_id": "s",
        "request_id": "r",
        "user_id": "",
        "tenant_id": "default",
    }
    return service_module.run_chat_turn(model, channel="sse", **{**arguments, **overrides})


class TestTheEventLoopKeepsRunning:
    def test_a_slow_translation_does_not_stall_other_taxpayers(self, monkeypatch) -> None:
        def slow_localize(text: str, _locale: str) -> str:
            time.sleep(0.4)
            return text

        monkeypatch.setattr(service_module, "localize_reply", slow_localize)

        async def run() -> int:
            ticks = 0

            async def heartbeat() -> None:
                nonlocal ticks
                while True:
                    await asyncio.sleep(0.02)
                    ticks += 1

            beat = asyncio.create_task(heartbeat())
            async for _ in _turn(_SlowModel()):
                pass
            beat.cancel()
            return ticks

        assert asyncio.run(run()) >= 8, "the event loop was blocked by a translation"


class TestAWholeTurnHasADeadline:
    def test_a_hung_retrieval_ends_the_turn_on_time(self) -> None:
        async def run() -> tuple[list[tuple[str, Any]], float]:
            started = time.perf_counter()
            events = [event async for event in _turn(_SlowModel(retrieval_seconds=1.0), turn_deadline_s=0.1)]
            return events, time.perf_counter() - started

        events, elapsed = asyncio.run(run())
        assert elapsed < 0.8
        assert any(kind == "deadline_exceeded" for kind, _ in events)
        assert [kind for kind, _ in events][-2:] == ["done", "_log"]

    def test_a_generation_only_gets_what_is_left_of_the_turn(self, monkeypatch) -> None:
        seen: list[float] = []

        def fake_result(self, timeout=None):
            seen.append(timeout)
            return ("An answer.", {"finish_reason": "stop"})

        monkeypatch.setattr(concurrent.futures.Future, "result", fake_result)
        with service_module.turn_deadline(5.0):
            service_module._local_llm_then_cloud("q", [], None, "en", deadline_s=45.0)

        assert seen
        assert seen[0] <= 5.0

    def test_no_time_left_means_no_new_generation(self, monkeypatch) -> None:
        generate = mock.Mock(return_value="never")
        monkeypatch.setattr(service_module.llm_module, "generate", generate)
        with service_module.turn_deadline(0.0):
            reply = service_module._local_llm_then_cloud("q", [], None, "en", allow_cloud_fallback=False)
        assert reply == ""
        generate.assert_not_called()

    def test_outside_a_turn_nothing_is_clamped(self) -> None:
        assert service_module.remaining_turn_seconds(45.0) == 45.0

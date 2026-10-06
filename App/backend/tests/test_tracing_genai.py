"""GenAI telemetry (app.tracing): model calls, tools, evaluations, turn usage.

Token usage used to be the user's message counted in words under vLLM, and
the LLM/tool span helpers had no callers. These tests pin what a model call
now reports, with OpenTelemetry off (Prometheus only) and on (spans).
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("OTEL_ENABLED", "false")

from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

from app import tracing  # noqa: E402
from app.analytics import metrics  # noqa: E402

USAGE_PAYLOAD = {
    "model": "Sunbird/Sunflower-14B-FP8",
    "choices": [{"message": {"content": "18%"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1742, "completion_tokens": 58},
}


def _counter(key: str) -> float:
    return metrics.snapshot()["counters"].get(key, 0)


class LlmCallMetricsTest(unittest.TestCase):
    def test_provider_usage_is_counted_and_summed_for_the_turn(self) -> None:
        labels = 'model="m-test",operation="chat",provider="vllm",type="input"'
        before = _counter(f"llm_tokens_total{{{labels}}}")
        with tracing.turn_usage_scope() as usage:
            for _ in range(2):
                with tracing.llm_call("chat", "m-test", "vllm") as call:
                    call.response(USAGE_PAYLOAD)
        self.assertEqual(usage, {"input_tokens": 3484, "output_tokens": 116, "calls": 2})
        self.assertEqual(tracing.usage_summary(usage), {"input_tokens": 3484, "output_tokens": 116, "source": "provider"})
        self.assertEqual(_counter(f"llm_tokens_total{{{labels}}}") - before, 3484)

    def test_no_reported_usage_means_no_summary(self) -> None:
        with tracing.turn_usage_scope() as usage, tracing.llm_call("chat", "m-test", "vllm"):
            pass
        self.assertIsNone(tracing.usage_summary(usage))

    def test_failures_are_counted_and_reraised(self) -> None:
        key = 'llm_requests_total{model="m-fail",operation="chat",provider="vllm",status="error"}'
        before = _counter(key)
        with self.assertRaises(TimeoutError), tracing.llm_call("chat", "m-fail", "vllm"):
            raise TimeoutError("vLLM timed out")
        with tracing.llm_call("chat", "m-fail", "vllm") as call:
            call.fail("unreachable")
        self.assertEqual(_counter(key) - before, 2)

    def test_stream_records_time_to_first_token(self) -> None:
        with tracing.llm_call("chat", "m-stream", "vllm", streaming=True) as call:
            call.first_token()
            call.first_token()  # only the first counts
        hist = metrics.snapshot()["histograms"]
        self.assertIn('llm_time_to_first_token_seconds{model="m-stream",operation="chat",provider="vllm"}', hist)

    def test_tool_outcome_without_exception_counts_as_error(self) -> None:
        key = 'tool_calls_total{status="error",tool="calculate_paye"}'
        before = _counter(key)
        with tracing.trace_tool_call("calculate_paye") as outcome:
            outcome["ok"] = False
        self.assertEqual(_counter(key) - before, 1)


class ExecutorContextTest(unittest.TestCase):
    """Generation runs on a thread pool; its tokens and span must still belong to the turn.

    Found live on the GPU stack: a bare ``executor.submit`` dropped the turn's
    context, so the audit row recorded the judge's 918 input tokens and missed
    the 2,644 of the generation itself.
    """

    def test_generation_on_the_executor_counts_toward_the_turn(self) -> None:
        import unittest.mock as mock

        from app import service

        def fake_generate(**_kwargs):
            with tracing.llm_call("chat", "m-exec", "vllm") as call:
                call.usage(2644, 180)
            return "generated"

        with mock.patch.object(service.llm_module, "generate", side_effect=fake_generate), \
             mock.patch.object(service._LLM_CIRCUIT, "allow_request", return_value=True), \
             tracing.turn_usage_scope() as usage:
            reply = service._local_llm_then_cloud("q", [], None, "en", allow_cloud_fallback=False)
        self.assertEqual(reply, "generated")
        self.assertEqual(usage["input_tokens"], 2644)
        self.assertEqual(usage["calls"], 1)


class GeminiUsageTest(unittest.TestCase):
    def test_thinking_tokens_count_as_output(self) -> None:
        from app.providers.gateway import _gemini_output_tokens

        self.assertEqual(_gemini_output_tokens({"candidatesTokenCount": 120, "thoughtsTokenCount": 900}), 1020)
        self.assertEqual(_gemini_output_tokens({"candidatesTokenCount": 120}), 120)
        self.assertEqual(_gemini_output_tokens({"thoughtsTokenCount": 7}), 7)
        self.assertIsNone(_gemini_output_tokens({"promptTokenCount": 10}))


class SpanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self._saved = tracing._tracer
        tracing._tracer = provider.get_tracer("test")

    def tearDown(self) -> None:
        tracing._tracer = self._saved

    def test_genai_span_names_and_attributes(self) -> None:
        with tracing.trace_rag_pipeline("What is VAT?", request_id="req-1"):
            with tracing.llm_call("chat", "Sunflower-14B", "vllm") as call:
                call.response(USAGE_PAYLOAD)
            with tracing.trace_tool_call("calculate_paye", call_id="c-1"):
                pass
            tracing.record_evaluation("faithfulness", 0.87)
        spans = {span.name: span for span in self.exporter.get_finished_spans()}
        self.assertIn("invoke_agent ura-assistant", spans)
        chat = spans["chat Sunflower-14B"]
        self.assertEqual(chat.attributes["gen_ai.provider.name"], "vllm")
        self.assertEqual(chat.attributes["gen_ai.usage.input_tokens"], 1742)
        self.assertEqual(tuple(chat.attributes["gen_ai.response.finish_reasons"]), ("stop",))
        self.assertEqual(spans["execute_tool calculate_paye"].attributes["gen_ai.operation.name"], "execute_tool")
        turn = spans["invoke_agent ura-assistant"]
        self.assertEqual(turn.attributes["gen_ai.usage.input_tokens"], 1742)
        self.assertNotIn("gen_ai.system", turn.attributes)
        events = [e for e in turn.events if e.name == "gen_ai.evaluation.result"]
        self.assertEqual(events[0].attributes["gen_ai.evaluation.score.value"], 0.87)

    def test_streaming_span_is_not_made_current(self) -> None:
        from opentelemetry import trace

        with tracing.llm_call("chat", "m", "vllm", streaming=True):
            self.assertFalse(trace.get_current_span().get_span_context().is_valid)
        self.assertEqual([s.name for s in self.exporter.get_finished_spans()], ["chat m"])

    def test_traceresponse_only_for_a_real_span(self) -> None:
        self.assertEqual(tracing.current_traceresponse(), "")
        with tracing._tracer.start_as_current_span("server"):
            value = tracing.current_traceresponse()
        # Trace Context Level 2: flags carry "sampled" and may carry "random" (0x02).
        self.assertRegex(value, r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


if __name__ == "__main__":
    unittest.main()

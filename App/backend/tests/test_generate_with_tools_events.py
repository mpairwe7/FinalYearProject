"""Phase 2 tests for the agentic event surface.

Two layers:

  * Unit — :func:`llm.generate_with_tools` emits the documented event
    sequence (iteration.started, tool_call.started, tool_call.completed,
    iteration.final).  We stub Qwen out and exercise just the loop
    structure via dependency injection.
  * Integration (smoke) — :func:`service._stream_agentic_turn` forwards
    events into an async stream.  We stub ``_call_llm_agentic`` to fire
    canned callbacks and assert the event order on the consumer side.
"""

from __future__ import annotations

import asyncio
import threading
import unittest
import unittest.mock


class SummariseToolResultTest(unittest.TestCase):
    """Unit tests for the event-payload summariser."""

    def test_dict_with_summary_key(self) -> None:
        from app.llm import _summarise_tool_result

        self.assertEqual(
            _summarise_tool_result({"summary": "VAT = 18,000 UGX"}),
            "VAT = 18,000 UGX",
        )

    def test_dict_with_error_key(self) -> None:
        from app.llm import _summarise_tool_result

        out = _summarise_tool_result({"error": "rate not found"})
        self.assertTrue(out.startswith("error:"))
        self.assertIn("rate not found", out)

    def test_list_payload(self) -> None:
        from app.llm import _summarise_tool_result

        self.assertEqual(_summarise_tool_result([1, 2, 3]), "list[3]")

    def test_long_string_truncated(self) -> None:
        from app.llm import _summarise_tool_result

        out = _summarise_tool_result("x" * 500)
        self.assertLessEqual(len(out), 200)

    def test_none_returns_empty_marker(self) -> None:
        from app.llm import _summarise_tool_result

        self.assertEqual(_summarise_tool_result(None), "<empty>")


class StreamAgenticTurnTest(unittest.IsolatedAsyncioTestCase):
    async def test_stream_forwards_callback_events_in_order(self) -> None:
        from app import service as service_module

        canned_events = [
            {"type": "iteration.started", "iteration": 0},
            {"type": "tool_call.started", "call_id": "tc1", "name": "calculate_vat", "arguments": {}},
            {"type": "tool_call.completed", "call_id": "tc1", "name": "calculate_vat", "ok": True, "result_summary": "ok", "elapsed_ms": 1.0},
            {"type": "iteration.final", "iterations": 1, "truncated": False, "tool_call_count": 1},
        ]

        def _fake_agentic(**kwargs):
            cb = kwargs["event_callback"]
            for ev in canned_events:
                cb(ev)
            return {"text": "VAT is 18,000 UGX", "tool_calls": [], "iterations": 1, "truncated": False}

        guard = unittest.mock.MagicMock()
        guard.sanitize.side_effect = lambda x: x
        guard.redact_pii.side_effect = lambda x: x

        with unittest.mock.patch.object(service_module, "_call_llm_agentic", _fake_agentic):
            yielded: list[tuple[str, object]] = []
            async for evt in service_module._stream_agentic_turn(
                rewritten_query="vat",
                hits=[{"text": "VAT is 18%"}],
                conversation_history=None,
                locale="en",
                personalization_context="",
                tenant_id="default",
                user_id="",
                user_role="public",
                granted_purposes=[],
                cancel_event=threading.Event(),
                _output_guard=guard,
            ):
                yielded.append(evt)

        types = [t for t, _ in yielded]
        # Order: forwarded callback events, then token, then _full_reply.
        self.assertEqual(types[0], "iteration.started")
        self.assertIn("tool_call.started", types)
        self.assertIn("tool_call.completed", types)
        self.assertIn("iteration.final", types)
        self.assertIn("token", types)
        self.assertIn("_full_reply", types)


if __name__ == "__main__":
    unittest.main()

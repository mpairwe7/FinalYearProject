"""Tests for the Phase-B tool-call parser helpers in app.llm."""

from __future__ import annotations

import pytest

from app.llm import (
    TOOL_USE_PROMPT_SUFFIX,
    _parse_tool_calls,
    _strip_tool_calls,
)


# ---------------------------------------------------------------------------
# _parse_tool_calls
# ---------------------------------------------------------------------------
class TestParseToolCalls:
    def test_single_tool_call(self):
        s = '<tool_call>{"name": "calculate_vat", "arguments": {"amount": 5000}}</tool_call>'
        calls = _parse_tool_calls(s)
        assert len(calls) == 1
        assert calls[0]["name"] == "calculate_vat"
        assert calls[0]["arguments"] == {"amount": 5000}

    def test_parallel_tool_calls(self):
        s = (
            '<tool_call>{"name": "a", "arguments": {}}</tool_call>\n'
            '<tool_call>{"name": "b", "arguments": {"x": 1}}</tool_call>\n'
            '<tool_call>{"name": "c", "arguments": {"y": "hello"}}</tool_call>'
        )
        calls = _parse_tool_calls(s)
        assert [c["name"] for c in calls] == ["a", "b", "c"]
        assert calls[2]["arguments"] == {"y": "hello"}

    def test_empty_input(self):
        assert _parse_tool_calls("") == []
        assert _parse_tool_calls("Just some prose, no tool calls.") == []

    def test_malformed_json_silently_skipped(self):
        s = "<tool_call>{not valid json}</tool_call>"
        assert _parse_tool_calls(s) == []

    def test_malformed_mixed_with_valid(self):
        s = (
            "<tool_call>{\"name\":\"a\"}</tool_call>\n"
            "<tool_call>{garbage}</tool_call>\n"
            "<tool_call>{\"name\":\"b\",\"arguments\":{}}</tool_call>"
        )
        calls = _parse_tool_calls(s)
        # The first valid call has no arguments → args defaults to {}
        # The second malformed block is skipped
        # The third is preserved
        assert len(calls) == 2
        assert calls[0]["name"] == "a"
        assert calls[1]["name"] == "b"

    def test_string_encoded_arguments(self):
        """Some LLM variants emit `arguments` as a JSON-encoded string."""
        s = (
            '<tool_call>{"name": "calculate_paye", '
            '"arguments": "{\\"monthly_gross\\": 3000000}"}</tool_call>'
        )
        calls = _parse_tool_calls(s)
        assert len(calls) == 1
        assert calls[0]["arguments"] == {"monthly_gross": 3000000}

    def test_missing_name_skipped(self):
        s = '<tool_call>{"arguments": {"x": 1}}</tool_call>'
        assert _parse_tool_calls(s) == []

    def test_non_dict_top_level_skipped(self):
        s = '<tool_call>["not", "a", "dict"]</tool_call>'
        assert _parse_tool_calls(s) == []

    def test_non_dict_arguments_coerced_to_empty(self):
        s = '<tool_call>{"name": "a", "arguments": 42}</tool_call>'
        calls = _parse_tool_calls(s)
        assert calls[0]["arguments"] == {}

    def test_multiline_json_body(self):
        s = """<tool_call>
{
  "name": "calculate_customs_duty",
  "arguments": {
    "cif_value": 1000000,
    "duty_rate": 0.1
  }
}
</tool_call>"""
        calls = _parse_tool_calls(s)
        assert len(calls) == 1
        assert calls[0]["arguments"]["cif_value"] == 1_000_000


# ---------------------------------------------------------------------------
# _strip_tool_calls
# ---------------------------------------------------------------------------
class TestStripToolCalls:
    def test_removes_tool_call_blocks(self):
        s = "Hi <tool_call>{\"name\":\"a\"}</tool_call> there"
        assert "<tool_call>" not in _strip_tool_calls(s)
        assert "Hi" in _strip_tool_calls(s)
        assert "there" in _strip_tool_calls(s)

    def test_empty_after_only_call(self):
        s = '<tool_call>{"name": "a"}</tool_call>'
        assert _strip_tool_calls(s) == ""

    def test_preserves_prose_without_calls(self):
        prose = "The VAT rate in Uganda is 18%."
        assert _strip_tool_calls(prose) == prose

    def test_removes_multiple_blocks(self):
        s = "A <tool_call>{\"name\":\"a\"}</tool_call> B <tool_call>{\"name\":\"b\"}</tool_call> C"
        result = _strip_tool_calls(s)
        assert "<tool_call>" not in result
        assert all(c in result for c in ["A", "B", "C"])


# ---------------------------------------------------------------------------
# TOOL_USE_PROMPT_SUFFIX — the system-prompt extension
# ---------------------------------------------------------------------------
class TestSystemPromptSuffix:
    def test_references_knowledge_base_tool(self):
        """The suffix must teach the LLM to call search_ura_knowledge_base."""
        assert "search_ura_knowledge_base" in TOOL_USE_PROMPT_SUFFIX

    def test_forbids_guessing_rates(self):
        """Key rule: never fabricate numeric rates."""
        lower = TOOL_USE_PROMPT_SUFFIX.lower()
        assert any(word in lower for word in ("fabricate", "guess", "invent"))

    def test_explains_error_handling(self):
        """Tells the LLM how to handle tool errors."""
        assert "error" in TOOL_USE_PROMPT_SUFFIX.lower() or \
               "ok" in TOOL_USE_PROMPT_SUFFIX.lower()

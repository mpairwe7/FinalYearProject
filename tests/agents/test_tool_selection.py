"""Tool RAG on the live agentic path.

``ToolRAGSelector`` was only ever reachable from the graph orchestrator,
which nothing calls — so every agentic turn pasted all 18 registered
schemas (~4.5k tokens) into the prompt regardless of the query.  These
tests pin the wiring in ``llm._select_tools_for_query``: narrowed when
the flag is on, unchanged when it is off, and never empty.
"""

from __future__ import annotations

import pytest
from app.llm import _select_tools_for_query
from app.mcp.tool_rag import MANDATORY_RAILS


@pytest.fixture
def eligible(fresh_registry):
    from app.tools import ToolRegistry

    return sorted(ToolRegistry.names())


class TestToolSelection:
    def test_flag_off_exposes_every_eligible_tool(self, clean_flags, eligible):
        clean_flags.set("tool_rag", False)
        assert _select_tools_for_query("how much VAT on 100000", eligible) == eligible

    def test_flag_on_narrows_the_exposed_set(self, clean_flags, eligible):
        clean_flags.set("tool_rag", True)
        selected = _select_tools_for_query("how much VAT on 100000", eligible)
        assert len(selected) < len(eligible)
        assert "calculate_vat" in selected

    def test_the_safety_rails_survive_narrowing(self, clean_flags, eligible):
        clean_flags.set("tool_rag", True)
        selected = _select_tools_for_query("how much VAT on 100000", eligible)
        for rail in MANDATORY_RAILS:
            if rail in eligible:
                assert rail in selected

    def test_selection_never_returns_an_empty_tool_set(self, clean_flags, eligible):
        # An agent with no tools cannot act; falling back to all of them
        # is merely expensive.
        clean_flags.set("tool_rag", True)
        assert _select_tools_for_query("zzzz qqqq wwww", eligible)

    def test_an_empty_eligible_set_stays_empty(self, clean_flags):
        clean_flags.set("tool_rag", True)
        assert _select_tools_for_query("anything", []) == []

    def test_a_selector_failure_falls_back_rather_than_raising(
        self, clean_flags, eligible, monkeypatch
    ):
        clean_flags.set("tool_rag", True)

        def _boom(*args, **kwargs):
            raise RuntimeError("selector down")

        monkeypatch.setattr("app.mcp.tool_rag.ToolRAGSelector.select", _boom)
        assert _select_tools_for_query("how much VAT", eligible) == eligible

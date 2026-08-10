"""Tests for Phase 15 Lite — MCP client + Tool RAG."""

from __future__ import annotations

import hashlib

import pytest

from app.mcp import MCPCallResult, MCPClient, ToolRAGSelector, get_client
from app.mcp.client import reset_client
from app.tools import ToolRegistry


# ---------------------------------------------------------------------------
# MCPClient
# ---------------------------------------------------------------------------
class TestMCPClient:
    def test_singleton_identity(self, fresh_registry):
        reset_client()
        c1 = get_client()
        c2 = get_client()
        assert c1 is c2

    def test_list_tools_returns_openai_specs(self, fresh_registry):
        reset_client()
        client = get_client()
        tools = client.list_tools()
        assert len(tools) > 0
        # Every entry has the Qwen-compatible envelope
        for spec in tools:
            assert spec["type"] == "function"
            assert "name" in spec["function"]
            assert "description" in spec["function"]
            assert spec["function"]["parameters"]["type"] == "object"

    def test_list_tools_risk_filter(self, fresh_registry):
        reset_client()
        client = get_client()
        low_only = client.list_tools(allow_risk=["low"])
        names = [t["function"]["name"] for t in low_only]
        assert "calculate_vat" in names
        # escalate_to_human is medium risk — should be excluded
        assert "escalate_to_human" not in names

    def test_describe_tool_unknown(self, fresh_registry):
        reset_client()
        assert get_client().describe_tool("nonexistent") is None


class TestAvailableFor:
    def test_public_has_most_low_risk_tools(self, fresh_registry):
        reset_client()
        allowed = get_client().available_for(user_role="public")
        assert "calculate_vat" in allowed
        assert "calculate_paye" in allowed
        assert "search_ura_knowledge_base" in allowed

    def test_public_without_consent_blocks_escalation(self, fresh_registry):
        reset_client()
        allowed = get_client().available_for(
            user_role="public",
            granted_purposes=[],
        )
        assert "escalate_to_human" not in allowed

    def test_public_with_ticket_consent_sees_escalation(self, fresh_registry):
        reset_client()
        allowed = get_client().available_for(
            user_role="public",
            granted_purposes=["ticket_escalation"],
        )
        assert "escalate_to_human" in allowed

    def test_verified_taxpayer_sees_escalation_without_explicit_consent(self, fresh_registry):
        reset_client()
        allowed = get_client().available_for(
            user_role="verified_taxpayer",
            granted_purposes=[],
        )
        # non-public roles bypass the consent gate for escalate
        assert "escalate_to_human" in allowed

    def test_staff_sees_all_tools(self, fresh_registry):
        reset_client()
        allowed = get_client().available_for(
            user_role="ura_staff",
            granted_purposes=["ura_account_access"],
        )
        # Everything except the critical write tool, which additionally
        # needs `ura_actions` consent.  Derived from the registry rather
        # than a literal count, which went stale every time a tool landed.
        expected = {
            name
            for name in ToolRegistry.names()
            if ToolRegistry.get(name).schema.risk != "critical"
        }
        assert set(allowed) == expected
        assert "ura_action_proposal" not in allowed


class TestCallTool:
    def test_calculate_vat_roundtrip(self, fresh_registry):
        reset_client()
        result = get_client().call_tool(
            "calculate_vat",
            {"amount": 100_000, "direction": "add"},
            tenant_id="t1",
            user_id="alice",
        )
        assert isinstance(result, MCPCallResult)
        assert result.tool_name == "calculate_vat"
        assert result.tenant_id == "t1"
        assert result.user_id == "alice"
        assert result.ok is True
        assert result.result["vat"] == 18_000
        assert result.duration_ms >= 0
        assert len(result.call_id) == 36

    def test_unknown_tool_returns_structured_error(self, fresh_registry):
        reset_client()
        result = get_client().call_tool("nonexistent", {})
        assert result.ok is False
        assert "Unknown tool" in result.result["error"]

    def test_iteration_tracking(self, fresh_registry):
        reset_client()
        result = get_client().call_tool(
            "calculate_vat",
            {"amount": 100},
            iteration=3,
        )
        assert result.iteration == 3


class TestAuditDict:
    def test_hashes_are_sha256_hex(self, fresh_registry):
        reset_client()
        result = get_client().call_tool("calculate_vat", {"amount": 100})
        audit = result.to_audit_dict()
        assert "arguments_sha256" in audit
        assert "result_sha256" in audit
        assert len(audit["arguments_sha256"]) == 64
        assert len(audit["result_sha256"]) == 64

    def test_deterministic_arguments_hash(self, fresh_registry):
        reset_client()
        r1 = get_client().call_tool("calculate_vat", {"amount": 100, "direction": "add"})
        r2 = get_client().call_tool("calculate_vat", {"direction": "add", "amount": 100})
        a1 = r1.to_audit_dict()
        a2 = r2.to_audit_dict()
        # Same args in different key order → same hash (sort_keys=True)
        assert a1["arguments_sha256"] == a2["arguments_sha256"]

    def test_to_audit_dict_has_required_fields(self, fresh_registry):
        reset_client()
        result = get_client().call_tool(
            "calculate_vat",
            {"amount": 100},
            tenant_id="t1",
            user_id="alice",
        )
        audit = result.to_audit_dict()
        required = {
            "call_id", "tool_name", "tenant_id", "user_id",
            "risk_tier", "ok", "duration_ms", "iteration",
            "arguments_sha256", "result_sha256", "ts",
        }
        assert required.issubset(audit.keys())


# ---------------------------------------------------------------------------
# Tool RAG
# ---------------------------------------------------------------------------
class TestToolRAGScorer:
    def test_vat_query_selects_vat_calculator(self, fresh_registry):
        reset_client()
        eligible = get_client().available_for(user_role="public")
        selector = ToolRAGSelector()
        selection = selector.select(
            query="How much VAT should I pay?",
            eligible_names=eligible,
            k=3,
        )
        assert "calculate_vat" in selection.tool_names
        # And it should be ranked first (highest score)
        scores = selection.scores
        vat_score = scores.get("calculate_vat", 0)
        other_scores = [v for k, v in scores.items() if k != "calculate_vat" and not k.startswith("search_")]
        assert vat_score >= max(other_scores, default=0)

    def test_paye_query_selects_paye_calculator(self, fresh_registry):
        reset_client()
        eligible = get_client().available_for(user_role="public")
        selector = ToolRAGSelector()
        selection = selector.select(
            query="Calculate my PAYE on a 2M salary",
            eligible_names=eligible,
            k=3,
        )
        assert "calculate_paye" in selection.tool_names

    def test_deadline_query_selects_calendar(self, fresh_registry):
        reset_client()
        eligible = get_client().available_for(user_role="public")
        selector = ToolRAGSelector()
        selection = selector.select(
            query="When is my next filing deadline?",
            eligible_names=eligible,
            k=3,
        )
        assert "get_next_deadlines" in selection.tool_names

    def test_mandatory_rails_always_present(self, fresh_registry):
        reset_client()
        eligible = get_client().available_for(
            user_role="public",
            granted_purposes=["ticket_escalation"],
        )
        selector = ToolRAGSelector()
        selection = selector.select(
            query="completely unrelated gibberish xyz",
            eligible_names=eligible,
            k=3,
        )
        assert "search_ura_knowledge_base" in selection.tool_names

    def test_empty_query_returns_empty(self, fresh_registry):
        reset_client()
        selector = ToolRAGSelector()
        selection = selector.select(
            query="",
            eligible_names=["calculate_vat"],
            k=3,
        )
        assert len(selection) == 0

    def test_empty_eligible_returns_empty(self, fresh_registry):
        reset_client()
        selector = ToolRAGSelector()
        selection = selector.select(
            query="How much VAT?",
            eligible_names=[],
            k=3,
        )
        assert len(selection) == 0

    def test_no_match_falls_back_to_all(self, fresh_registry):
        reset_client()
        selector = ToolRAGSelector()
        # Alien tokens that match no name or description token
        selection = selector.select(
            query="xyzzy plugh frobnicate",
            eligible_names=["calculate_vat", "calculate_paye"],
            k=3,
        )
        # Fallback returns everything eligible so the LLM isn't empty-handed
        assert selection.fallback_reason != ""
        assert len(selection) >= 1

    def test_name_hits_outweigh_desc_hits(self, fresh_registry):
        """Query containing the tool's *name* should dominate a query
        that only matches description tokens."""
        reset_client()
        eligible = ["calculate_vat", "calculate_paye", "calculate_corporation_tax"]
        selector = ToolRAGSelector()
        # Query exactly matches calculate_vat's name token
        selection = selector.select(
            query="vat",
            eligible_names=eligible,
            k=3,
        )
        # calculate_vat's score should be strictly higher than others
        scores = selection.scores
        assert scores.get("calculate_vat", 0) > scores.get("calculate_paye", 0)

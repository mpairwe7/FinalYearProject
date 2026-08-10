"""Tests for the tool framework itself (registry, dispatch, schema)."""

from __future__ import annotations

import pytest

from app.tools import Tool, ToolRegistry, ToolSchema


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class _DummyTool(Tool):
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="_dummy_echo",
            description="Echo back the input value for tests.",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            risk="low",
        )

    def execute(self, value: str, **kwargs) -> dict:
        return {"ok": True, "echoed": value}


def test_registry_register_and_get(fresh_registry):
    tool = _DummyTool()
    fresh_registry.register(tool)
    assert fresh_registry.get("_dummy_echo") is tool
    assert "_dummy_echo" in fresh_registry.names()


def test_registry_returns_none_for_unknown(fresh_registry):
    assert fresh_registry.get("nonexistent") is None


def test_registry_call_unknown_tool_returns_structured_error(fresh_registry):
    result = fresh_registry.call("nonexistent", {})
    assert result["ok"] is False
    assert "Unknown tool" in result["error"]
    assert "available_tools" in result


def test_registry_call_captures_exceptions(fresh_registry):
    class _ExplodingTool(Tool):
        @property
        def schema(self) -> ToolSchema:
            return ToolSchema(name="_boom", description="Raises.")

        def execute(self, **kwargs) -> dict:
            raise RuntimeError("pretend failure")

    fresh_registry.register(_ExplodingTool())
    result = fresh_registry.call("_boom", {})
    assert result["ok"] is False
    assert "RuntimeError" in result["error"]


def test_registry_call_rejects_bad_args_cleanly(fresh_registry):
    class _StrictTool(Tool):
        @property
        def schema(self) -> ToolSchema:
            return ToolSchema(name="_strict", description="Needs exactly one int.")

        def execute(self, n: int) -> dict:
            return {"ok": True, "n": n}

    fresh_registry.register(_StrictTool())
    # Missing required arg → TypeError inside execute, captured as structured
    result = fresh_registry.call("_strict", {})
    assert result["ok"] is False
    assert "Invalid arguments" in result["error"]
    assert "expected" in result


def test_registry_clear_resets_state(fresh_registry):
    fresh_registry.clear()
    assert fresh_registry.names() == []
    assert fresh_registry.openai_specs() == []


def test_openai_spec_shape(fresh_registry):
    """Verify the Qwen2.5-compatible function-calling envelope."""
    fresh_registry.clear()
    fresh_registry.register(_DummyTool())
    specs = fresh_registry.openai_specs()
    assert len(specs) == 1
    spec = specs[0]
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "_dummy_echo"
    assert "description" in spec["function"]
    assert spec["function"]["parameters"]["type"] == "object"


def test_openai_specs_filter_by_risk(fresh_registry):
    """The risk whitelist should filter out ineligible tools."""
    class _MediumTool(Tool):
        @property
        def schema(self) -> ToolSchema:
            return ToolSchema(name="_med", description="Medium risk.", risk="medium")

        def execute(self) -> dict:
            return {"ok": True}

    class _CriticalTool(Tool):
        @property
        def schema(self) -> ToolSchema:
            return ToolSchema(name="_crit", description="Critical risk.", risk="critical")

        def execute(self) -> dict:
            return {"ok": True}

    fresh_registry.clear()
    fresh_registry.register(_DummyTool())          # low
    fresh_registry.register(_MediumTool())
    fresh_registry.register(_CriticalTool())

    low_only = fresh_registry.openai_specs(allow_risk=["low"])
    assert [s["function"]["name"] for s in low_only] == ["_dummy_echo"]

    low_medium = fresh_registry.openai_specs(allow_risk=["low", "medium"])
    names = sorted(s["function"]["name"] for s in low_medium)
    assert names == ["_dummy_echo", "_med"]


# ---------------------------------------------------------------------------
# Starter tools register on import
# ---------------------------------------------------------------------------
EXPECTED_STARTER_TOOLS = {
    "calculate_vat",
    "calculate_paye",
    "calculate_corporation_tax",
    "calculate_capital_gains",
    "calculate_customs_duty",
    "get_current_date",
    "get_next_deadlines",
    "lookup_rate",
    "list_available_rates",
    "search_ura_knowledge_base",
    "escalate_to_human",
}


def test_starter_tools_all_auto_register(fresh_registry):
    """After importing the tools package, all 11 canonical tools exist."""
    registered = set(fresh_registry.names())
    missing = EXPECTED_STARTER_TOOLS - registered
    assert not missing, f"missing tools: {missing}"


def test_every_starter_tool_has_valid_schema(fresh_registry):
    for name in EXPECTED_STARTER_TOOLS:
        tool = fresh_registry.get(name)
        assert tool is not None, f"{name} not registered"
        schema = tool.schema
        assert schema.name == name
        assert len(schema.description) > 20, f"{name}: description too short"
        assert schema.parameters["type"] == "object"
        assert schema.risk in ("low", "medium", "high", "critical")


def test_risk_tier_inventory(fresh_registry):
    """Phase A tools are low risk, Phase D escalate tool is medium."""
    low = [n for n in fresh_registry.names()
           if fresh_registry.get(n).schema.risk == "low"]
    medium = [n for n in fresh_registry.names()
              if fresh_registry.get(n).schema.risk == "medium"]
    assert "calculate_vat" in low
    assert "search_ura_knowledge_base" in low
    assert "escalate_to_human" in medium

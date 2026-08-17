"""Cross-phase integration tests.

These exercise the wiring between the tool framework, the Phase-B
llm module, the Phase-C supervisor, and the Phase-D ticket queue
— WITHOUT actually calling Qwen (the LLM path is stubbed via
monkeypatching).  Full live-LLM tests belong in a separate
integration suite gated by ``PYTEST_INTEGRATION=1``.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Feature flag wiring
# ---------------------------------------------------------------------------
class TestFeatureFlags:
    def test_phase_flags_registered(self, clean_flags):
        all_flags = clean_flags.all()
        assert "tool_use" in all_flags
        assert "agentic_mode" in all_flags
        assert "ticket_queue" in all_flags

    def test_phase_flag_defaults(self, clean_flags):
        """tool_use / tickets stay off; agentic_mode is on behind the routing gate."""
        assert clean_flags.is_enabled("tool_use") is False
        assert clean_flags.is_enabled("agentic_mode") is True
        assert clean_flags.is_enabled("ticket_queue") is False

    def test_flag_overrides_work(self, clean_flags):
        clean_flags.set("tool_use", True)
        assert clean_flags.is_enabled("tool_use") is True
        clean_flags.clear("tool_use")
        assert clean_flags.is_enabled("tool_use") is False


# ---------------------------------------------------------------------------
# Supervisor → Tool whitelist flow
# ---------------------------------------------------------------------------
class TestSupervisorToolWhitelist:
    def test_vat_route_whitelist_includes_vat_calc(self, fresh_registry):
        from app.agents import supervisor, AgentRoute
        d = supervisor.classify("How much VAT on 5 million?")
        assert d.route == AgentRoute.TOOLS

        # Every suggested tool should exist in the registry — no typos
        for name in d.suggested_tools:
            assert fresh_registry.get(name) is not None, f"{name} missing"
        assert "calculate_vat" in d.suggested_tools

    def test_customs_specialist_whitelist_includes_customs(self, fresh_registry):
        from app.agents import supervisor, AgentRoute
        d = supervisor.classify("Tariff code for finished goods")
        assert d.route == AgentRoute.CUSTOMS_SPECIALIST
        for name in d.suggested_tools:
            assert fresh_registry.get(name) is not None
        assert "calculate_customs_duty" in d.suggested_tools

    def test_rate_lookup_suggests_lookup_rate(self, fresh_registry):
        from app.agents import supervisor
        d = supervisor.classify("What is the current VAT rate?")
        assert "lookup_rate" in d.suggested_tools


# ---------------------------------------------------------------------------
# Supervisor escalation → ticket creation (round-trip through service hook)
# ---------------------------------------------------------------------------
class TestEscalationRoundTrip:
    def test_supervisor_reason_flows_into_ticket(self, tmp_db):
        """Emulates the service.py ESCALATE branch."""
        from app.agents import supervisor, AgentRoute

        decision = supervisor.classify("I want to dispute my assessment")
        assert decision.route == AgentRoute.ESCALATE

        ticket = tmp_db.create_ticket(
            reason=decision.reason,
            user_query="I want to dispute my assessment",
            bot_reply="",
            priority="high",
        )
        fetched = tmp_db.get_ticket(ticket["id"])
        assert fetched["reason"] == decision.reason
        assert fetched["priority"] == "high"
        assert fetched["status"] == "open"


# ---------------------------------------------------------------------------
# Full FastAPI app still assembles with the flags on
# ---------------------------------------------------------------------------
class TestAppAssembly:
    def test_app_loads_with_all_agentic_flags_on(self, monkeypatch):
        """The FastAPI app must still construct under every flag combo."""
        for flag in ("TOOL_USE", "AGENTIC_MODE", "TICKET_QUEUE"):
            monkeypatch.setenv(f"FLAG_{flag}", "true")

        # Import fresh so the flag env is re-read
        import importlib
        import app.flags
        importlib.reload(app.flags)

        from app.flags import flags
        assert flags.is_enabled("tool_use") is True
        assert flags.is_enabled("agentic_mode") is True
        assert flags.is_enabled("ticket_queue") is True

        # Full FastAPI app construction
        import app.main
        importlib.reload(app.main)
        routes = [r.path for r in app.main.app.routes if hasattr(r, "path")]
        # Admin endpoints are registered
        assert "/v1/admin/tickets" in routes
        assert "/v1/admin/tickets/stats" in routes

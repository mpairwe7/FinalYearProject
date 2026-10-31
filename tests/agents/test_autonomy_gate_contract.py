"""The autonomy gate decides from what a tool declares, and cannot be talked out of it.

Every defect pinned here was a control that looked like it held. The gate read a
tool's tier from a table of names, 14 of whose 24 entries are not tools, so
``payment_checkout_settle`` (declared high) ran as "low". A failed audit write
released a block. A record could say "proposed" about a call that had already
run. Each test states what must be true of the real registry, not of a fixture,
so a new tool cannot quietly fall outside the contract.
"""

from __future__ import annotations

import json
import uuid

import pytest
from app.audit import governance_v3 as gov
from app.flags import flags
from app.tools import Tool, ToolRegistry, ToolSchema

TIERS = ("low", "medium", "high", "critical")


def _tool(name: str, *, risk: str = "low", ran: list | None = None, boom: bool = False, **schema) -> Tool:
    """A registered tool that records whether its body actually ran."""

    class _Probe(Tool):
        @property
        def schema(self) -> ToolSchema:
            return ToolSchema(name=name, description="probe", risk=risk, **schema)

        def execute(self, **_kwargs):
            if ran is not None:
                ran.append(name)
            if boom:
                raise RuntimeError("connector exploded")
            return {"ok": True}

    tool = _Probe()
    ToolRegistry.register(tool)
    return tool


@pytest.fixture
def probe_name():
    name = f"probe_{uuid.uuid4().hex[:8]}"
    yield name
    ToolRegistry.unregister(name)


@pytest.fixture
def gates(clean_flags):
    flags.set("risk_tiered_autonomy_gates", True)
    gov.quality_circuit_breaker.reset()
    yield
    gov.quality_circuit_breaker.reset()


def _ledger_rows(db) -> list[dict]:
    rows = db._get_connection().execute(
        "SELECT payload FROM audit_events WHERE event_type = 'agent_action_v3' ORDER BY seq"
    ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


class TestTheGateReadsTheDeclaredTier:
    def test_no_registered_tool_is_rated_below_what_it_declares(self) -> None:
        under = [
            t.schema.name
            for t in ToolRegistry.all()
            if TIERS.index(gov.classify_action_risk(t.schema.name, declared_risk=t.schema.risk))
            < TIERS.index(t.schema.risk)
        ]
        assert under == []

    def test_a_declared_tier_is_not_second_guessed_by_a_word_in_the_name(self) -> None:
        # "objection" in the name made an informational lookup "high", which would
        # have blocked a taxpayer asking how to object.
        assert gov.classify_action_risk("objection_requirements", declared_risk="low") == "low"
        assert gov.classify_action_risk("efris_taxpayer_status", declared_risk="low") == "low"

    def test_a_table_entry_can_still_raise_a_declared_tier(self) -> None:
        assert gov.classify_action_risk("mutate_tax_liability_ledger", declared_risk="low") == "critical"

    def test_an_unrecognised_declared_tier_is_treated_as_the_strictest(self) -> None:
        assert gov.classify_action_risk("anything", declared_risk="experimental") == "critical"

    def test_a_name_nobody_rated_is_not_assumed_safe(self) -> None:
        assert gov.classify_action_risk("rotate_the_widgets") != "low"

    def test_the_names_the_gate_knows_still_classify_without_a_schema(self) -> None:
        assert gov.classify_action_risk("calculate_paye") == "low"
        assert gov.classify_action_risk("verify_tin") == "medium"
        assert gov.classify_action_risk("file_notice_of_objection") == "high"
        assert gov.classify_action_risk("disburse_treasury_funds") == "critical"

    @pytest.mark.parametrize(
        ("risk", "outcome"),
        [("high", "proposed_awaiting_officer"), ("critical", "hard_blocked")],
    )
    def test_a_tool_declaring_a_high_tier_does_not_run_on_autonomy(
        self, gates, probe_name, risk, outcome
    ) -> None:
        ran: list[str] = []
        _tool(probe_name, risk=risk, ran=ran)
        result = ToolRegistry.call(probe_name, {}, user_id="u1", caller_confirmed=True)
        assert ran == []
        assert result["ok"] is False
        assert result["autonomy_outcome"] == outcome


class TestTheHumanRouteStaysOpen:
    def test_a_taxpayer_can_always_reach_an_officer(self, gates, tmp_db) -> None:
        flags.set("ticket_queue", True)
        result = ToolRegistry.call("escalate_to_human", {"reason": "I need a person"}, user_id="u1")
        assert result["ok"] is True
        assert result["ticket_id"]

    def test_quality_throttling_does_not_close_it(self, gates, tmp_db) -> None:
        flags.set("ticket_queue", True)
        flags.set("quality_circuit_breaker", True)
        gov.evaluate_interaction_scorecard(session_id="s", reply_text="x", figure_fidelity_passed=False)
        assert gov.quality_circuit_breaker.is_throttled()
        result = ToolRegistry.call("escalate_to_human", {"reason": "the figure looks wrong"}, user_id="u1")
        assert result["ok"] is True

    def test_a_guardrail_failure_does_not_close_it(self) -> None:
        outcome, _ = gov.evaluate_autonomy_gate("escalate_to_human", guardrails_passed=False)
        assert outcome == "autonomous_executed"

    def test_an_informational_lookup_is_not_held_for_an_officer(self, gates) -> None:
        result = ToolRegistry.call("objection_requirements", {})
        assert result.get("autonomy_outcome") is None


class TestABlockIsNotConditionalOnItsRecord:
    def test_a_failed_audit_write_does_not_release_the_block(
        self, gates, probe_name, monkeypatch
    ) -> None:
        ran: list[str] = []
        _tool(probe_name, risk="critical", ran=ran)

        def _audit_down(**_kwargs):
            raise ConnectionError("ledger unreachable")

        monkeypatch.setattr(gov, "append_action_audit_v3", _audit_down)
        result = ToolRegistry.call(probe_name, {}, user_id="u1")
        assert ran == []
        assert result["ok"] is False
        assert result["autonomy_outcome"] == "hard_blocked"

    def test_a_gate_that_cannot_decide_has_not_allowed_a_write(
        self, gates, probe_name, monkeypatch
    ) -> None:
        ran: list[str] = []
        _tool(probe_name, risk="medium", ran=ran, read_only=False)

        def _gate_down(*_args, **_kwargs):
            raise RuntimeError("policy store unreachable")

        monkeypatch.setattr(gov, "evaluate_autonomy_gate", _gate_down)
        result = ToolRegistry.call(probe_name, {}, user_id="u1")
        assert ran == []
        assert result["ok"] is False
        assert result["autonomy_outcome"] == "failed_closed"

    def test_a_pure_calculation_survives_a_gate_fault(self, gates, monkeypatch) -> None:
        def _gate_down(*_args, **_kwargs):
            raise RuntimeError("policy store unreachable")

        monkeypatch.setattr(gov, "evaluate_autonomy_gate", _gate_down)
        result = ToolRegistry.call("get_current_date", {})
        assert result["ok"] is True

    def test_the_fault_is_counted(self, gates, probe_name, monkeypatch) -> None:
        from app.analytics import metrics

        _tool(probe_name, risk="high", read_only=False)

        def _gate_down(*_args, **_kwargs):
            raise RuntimeError("policy store unreachable")

        monkeypatch.setattr(gov, "evaluate_autonomy_gate", _gate_down)
        key = 'autonomy_gate_faults_total{stage="evaluate"}'
        before = metrics.snapshot()["counters"].get(key, 0)
        ToolRegistry.call(probe_name, {}, user_id="u1")
        assert metrics.snapshot()["counters"].get(key, 0) - before == 1


class TestTheRecordSaysWhatHappened:
    def test_a_call_that_ran_is_not_recorded_as_proposed(self, clean_flags, tmp_db) -> None:
        # Gates off, audit on: the call runs, and the audit layer used to re-run
        # the gate afterwards and file it as "proposed_awaiting_officer".
        flags.set("action_level_audit_v3", True)
        flags.set("ticket_queue", True)
        result = ToolRegistry.call("escalate_to_human", {"reason": "help"}, user_id="u1")
        assert result["ok"] is True
        row = _ledger_rows(tmp_db)[-1]
        assert row["execution_status"] == "success"
        assert row["autonomy_outcome"] == "autonomous_executed"
        assert row["resolution_status"] == "resolved"

    def test_a_decided_officer_approval_keeps_its_time(self, clean_flags, tmp_db) -> None:
        record, _receipt = gov.append_action_audit_v3(
            session_id="c1",
            tool_or_action_name="tin_registration_submit",
            intent_detected="tin_registration",
            intent_confidence=0.96,
            agent_role="tax_specialist",
            decision_trace=["gate:officer_approved"],
            caller_confirmed=True,
            officer_approved=True,
            approving_officer_id="officer-1",
            decided_outcome="officer_approved_executed",
        )

        assert record.approval_gate.approval_timestamp_utc

    def test_no_approval_time_is_invented(self, clean_flags, tmp_db) -> None:
        record, _receipt = gov.append_action_audit_v3(
            session_id="c1",
            tool_or_action_name="calculate_vat",
            intent_detected="vat",
            intent_confidence=0.96,
            agent_role="tax_specialist",
            decision_trace=["gate:low_risk"],
            decided_outcome="autonomous_executed",
        )

        assert record.approval_gate.approval_timestamp_utc is None

    def test_a_high_tier_call_that_ran_is_recorded_as_having_run(self, clean_flags, tmp_db, probe_name) -> None:
        # The human route is exempt from the gate, so it cannot show this: a second
        # opinion on a high-tier tool would file a call that ran as awaiting an officer.
        flags.set("action_level_audit_v3", True)
        ran: list[str] = []
        _tool(probe_name, risk="high", ran=ran)

        ToolRegistry.call(probe_name, {}, user_id="u1")

        assert ran == [probe_name]
        row = _ledger_rows(tmp_db)[-1]
        assert row["execution_status"] == "success"
        assert row["autonomy_outcome"] == "autonomous_executed"

    def test_the_record_carries_the_declared_tier(self, clean_flags, tmp_db, probe_name) -> None:
        flags.set("action_level_audit_v3", True)
        _tool(probe_name, risk="high")
        ToolRegistry.call(probe_name, {}, user_id="u1")
        assert _ledger_rows(tmp_db)[-1]["risk_tier"] == "high"

    def test_a_blocked_call_is_recorded_as_blocked(self, gates, tmp_db, probe_name) -> None:
        flags.set("action_level_audit_v3", True)
        _tool(probe_name, risk="critical")
        ToolRegistry.call(probe_name, {}, user_id="u1")
        row = _ledger_rows(tmp_db)[-1]
        assert row["execution_status"] == "blocked"
        assert row["autonomy_outcome"] == "hard_blocked"

    def test_a_tool_that_raised_is_recorded_as_an_error(self, clean_flags, tmp_db, probe_name) -> None:
        flags.set("action_level_audit_v3", True)
        _tool(probe_name, boom=True)
        result = ToolRegistry.call(probe_name, {}, user_id="u1")
        assert result["ok"] is False
        assert _ledger_rows(tmp_db)[-1]["execution_status"] == "error"

    def test_a_rejected_argument_is_recorded_as_an_error(self, clean_flags, tmp_db) -> None:
        flags.set("action_level_audit_v3", True)
        result = ToolRegistry.call("calculate_vat", {"nonsense": 1})
        assert result["ok"] is False
        assert _ledger_rows(tmp_db)[-1]["execution_status"] == "error"

    def test_a_decision_with_no_outcome_is_still_gated(self, clean_flags, tmp_db) -> None:
        # Callers recording an intended action, rather than one already decided,
        # keep the original behaviour: the gate is consulted for them.
        flags.set("action_level_audit_v3", True)
        record, _ = gov.append_action_audit_v3(
            session_id="s", tool_or_action_name="file_notice_of_objection", intent_detected="objection"
        )
        assert record.autonomy_outcome == "proposed_awaiting_officer"
        assert record.execution_status == "proposed"

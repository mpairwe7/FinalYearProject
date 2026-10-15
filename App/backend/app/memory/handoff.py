"""Standardized Agent Handoff Envelope (2026 Enterprise Standards).

Enables lossless, structured task handoffs between autonomous agents,
specialist subgraphs, and human contact-center officers.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class EvidenceCard:
    """Verified factual evidence backing the current conversation state."""

    source: str
    fact: str
    citation_index: int = 0
    confidence: float = 1.0


@dataclass
class HandoffGoal:
    """Primary intent and target resolution of the active thread."""

    primary_objective: str
    target_resolution: str
    status: str = "in_progress"  # "in_progress", "blocked_awaiting_documentation", "escalated", "resolved"


@dataclass
class RecommendedAction:
    """Action suggested by the agent for the next actor (officer or peer agent)."""

    action_type: str
    suggested_message: str
    required_inputs: list[str] = field(default_factory=list)


@dataclass
class SLAContext:
    """Service-level agreement timer and routing metadata for human or specialist handoffs."""

    priority: str = "normal"  # "low" | "normal" | "high" | "urgent"
    target_response_seconds: int = 120
    deadline_utc: float = 0.0
    breach_escalation_team: str = "tier2_domestic_taxes"


@dataclass
class ToolExecutionReceipt:
    """Deterministic tool execution receipt carried across specialist or officer handoffs."""

    tool_name: str
    inputs_summary: str
    deterministic_output: dict[str, Any] = field(default_factory=dict)
    verified_at_utc: float = field(default_factory=time.time)


@dataclass
class HandoffEnvelope:
    """Standardized payload for inter-agent and human handoffs."""

    handoff_id: str = field(default_factory=lambda: f"hnd-{uuid.uuid4().hex[:12]}")
    thread_id: str = ""
    tenant_id: str = "default"
    timestamp_utc: float = field(default_factory=time.time)
    source_agent: str = "supervisor"
    target_channel: str = "officer_desk"  # "officer_desk", "tax_specialist", "customs_specialist"
    goal: HandoffGoal = field(default_factory=lambda: HandoffGoal("", ""))
    taxpayer_tin: str = ""
    taxpayer_locale: str = "en"
    verified_evidence: list[EvidenceCard] = field(default_factory=list)
    unverified_assumptions: list[str] = field(default_factory=list)
    blockers_and_risks: list[str] = field(default_factory=list)
    next_action: RecommendedAction = field(default_factory=lambda: RecommendedAction("", ""))
    # v2.0 Continuous Relationship & Officer Co-Pilot extensions (October 2026)
    case_reference: str = ""
    source_channel: str = "web"  # "web" | "voice_call" | "voice_ws"
    caller_affect: str = "calm"  # "calm" | "confused" | "frustrated" | "urgent_distress"
    workflow_state_snapshot: dict[str, Any] = field(default_factory=dict)
    tool_receipts: list[ToolExecutionReceipt] = field(default_factory=list)
    sla: SLAContext = field(default_factory=SLAContext)
    english_transcript_digest: list[dict[str, str]] = field(default_factory=list)
    officer_macros: list[dict[str, str]] = field(default_factory=list)
    eq_guidance: dict[str, Any] = field(default_factory=dict)
    # Phase 34 (2026) — Enterprise Governance & Action-Level Auditability
    audit_event_ids: list[str] = field(default_factory=list)
    decision_trace: list[str] = field(default_factory=list)
    reversibility_summary: str = ""
    quality_scorecard_summary: dict[str, Any] = field(default_factory=dict)
    # Phase 35 (October 2026) — Zero-Repetition Slot Bank, Affect Trajectory & Background Tasks
    verified_slot_bank: dict[str, Any] = field(default_factory=dict)
    affect_trajectory: list[dict[str, Any]] = field(default_factory=list)
    pending_background_tasks: list[str] = field(default_factory=list)

    def zero_repetition_score(self) -> float:
        """Return 1.0 when next_action.required_inputs asks for zero already-verified slots."""
        req = [str(k).strip().lower() for k in (self.next_action.required_inputs or []) if str(k).strip()]
        if not req:
            return 1.0
        known = {str(k).strip().lower() for k, v in (self.verified_slot_bank or {}).items() if v not in (None, "")}
        if self.taxpayer_tin.strip():
            known.update({"tin", "taxpayer_tin", "taxpayer_id"})
        overlap = sum(1 for k in req if k in known)
        return round(max(0.0, 1.0 - (overlap / len(req))), 3)

    def completeness_ratio(self) -> float:
        """Compute structured handoff completeness score in [0.0, 1.0]."""
        checks = (
            bool(self.goal.primary_objective.strip()),
            bool(self.goal.target_resolution.strip()),
            bool(self.verified_evidence or self.tool_receipts),
            bool(self.next_action.action_type.strip()),
            bool(self.sla.target_response_seconds > 0),
            bool(self.taxpayer_locale.strip()),
        )
        return round(sum(1 for c in checks if c) / len(checks), 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "thread_id": self.thread_id,
            "tenant_id": self.tenant_id,
            "timestamp_utc": self.timestamp_utc,
            "source_agent": self.source_agent,
            "target_channel": self.target_channel,
            "goal": asdict(self.goal),
            "taxpayer_tin": self.taxpayer_tin,
            "taxpayer_locale": self.taxpayer_locale,
            "verified_evidence": [asdict(e) for e in self.verified_evidence],
            "unverified_assumptions": list(self.unverified_assumptions),
            "blockers_and_risks": list(self.blockers_and_risks),
            "next_action": asdict(self.next_action),
            "case_reference": self.case_reference,
            "source_channel": self.source_channel,
            "caller_affect": self.caller_affect,
            "workflow_state_snapshot": dict(self.workflow_state_snapshot),
            "tool_receipts": [asdict(r) for r in self.tool_receipts],
            "sla": asdict(self.sla),
            "english_transcript_digest": list(self.english_transcript_digest),
            "officer_macros": list(self.officer_macros),
            "eq_guidance": dict(self.eq_guidance),
            "audit_event_ids": list(self.audit_event_ids),
            "decision_trace": list(self.decision_trace),
            "reversibility_summary": self.reversibility_summary,
            "quality_scorecard_summary": dict(self.quality_scorecard_summary),
            "verified_slot_bank": dict(self.verified_slot_bank),
            "affect_trajectory": list(self.affect_trajectory),
            "pending_background_tasks": list(self.pending_background_tasks),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HandoffEnvelope:
        goal_data = data.get("goal", {})
        goal = HandoffGoal(
            primary_objective=str(goal_data.get("primary_objective", "")),
            target_resolution=str(goal_data.get("target_resolution", "")),
            status=str(goal_data.get("status", "in_progress")),
        )

        evidence_list = [
            EvidenceCard(
                source=str(e.get("source", "")),
                fact=str(e.get("fact", "")),
                citation_index=int(e.get("citation_index", 0)),
                confidence=float(e.get("confidence", 1.0)),
            )
            for e in data.get("verified_evidence", [])
            if isinstance(e, dict)
        ]

        action_data = data.get("next_action", {})
        next_action = RecommendedAction(
            action_type=str(action_data.get("action_type", "")),
            suggested_message=str(action_data.get("suggested_message", "")),
            required_inputs=list(action_data.get("required_inputs", [])),
        )

        receipts_list = [
            ToolExecutionReceipt(
                tool_name=str(r.get("tool_name", "")),
                inputs_summary=str(r.get("inputs_summary", "")),
                deterministic_output=dict(r.get("deterministic_output", {})),
                verified_at_utc=float(r.get("verified_at_utc", time.time())),
            )
            for r in data.get("tool_receipts", [])
            if isinstance(r, dict)
        ]

        sla_data = data.get("sla", {}) if isinstance(data.get("sla"), dict) else {}
        sla = SLAContext(
            priority=str(sla_data.get("priority", "normal")),
            target_response_seconds=int(sla_data.get("target_response_seconds", 120)),
            deadline_utc=float(sla_data.get("deadline_utc", 0.0)),
            breach_escalation_team=str(sla_data.get("breach_escalation_team", "tier2_domestic_taxes")),
        )

        return cls(
            handoff_id=str(data.get("handoff_id", "")),
            thread_id=str(data.get("thread_id", "")),
            tenant_id=str(data.get("tenant_id", "default")),
            timestamp_utc=float(data.get("timestamp_utc", time.time())),
            source_agent=str(data.get("source_agent", "supervisor")),
            target_channel=str(data.get("target_channel", "officer_desk")),
            goal=goal,
            taxpayer_tin=str(data.get("taxpayer_tin", "")),
            taxpayer_locale=str(data.get("taxpayer_locale", "en")),
            verified_evidence=evidence_list,
            unverified_assumptions=list(data.get("unverified_assumptions", [])),
            blockers_and_risks=list(data.get("blockers_and_risks", [])),
            next_action=next_action,
            case_reference=str(data.get("case_reference", "")),
            source_channel=str(data.get("source_channel", "web")),
            caller_affect=str(data.get("caller_affect", "calm")),
            workflow_state_snapshot=dict(data.get("workflow_state_snapshot", {})),
            tool_receipts=receipts_list,
            sla=sla,
            english_transcript_digest=list(data.get("english_transcript_digest", [])),
            officer_macros=list(data.get("officer_macros", [])),
            eq_guidance=dict(data.get("eq_guidance", {})),
            audit_event_ids=list(data.get("audit_event_ids", [])),
            decision_trace=list(data.get("decision_trace", [])),
            reversibility_summary=str(data.get("reversibility_summary", "")),
            quality_scorecard_summary=dict(data.get("quality_scorecard_summary", {})),
            verified_slot_bank=dict(data.get("verified_slot_bank", {})),
            affect_trajectory=list(data.get("affect_trajectory", [])),
            pending_background_tasks=list(data.get("pending_background_tasks", [])),
        )

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
        )

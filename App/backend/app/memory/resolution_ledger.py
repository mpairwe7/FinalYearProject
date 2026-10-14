"""Continuous Relationship Orchestrator & Longitudinal Resolution Ledger (2026).

Extends the 4-tier cognitive memory architecture with a consent-gated,
cross-channel ``ResolutionCase`` ledger so autonomous agents continue tracking
multi-step taxpayer goals across Voice Calls, Web Chat, and Officer Desks
even after a call disconnects or awaits documentation.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def get_resolution_case_ttl_seconds() -> int:
    """Retention window for active cross-channel resolution cases (default 168h / 7 days)."""
    try:
        hours = int(os.getenv("RESOLUTION_CASE_TTL_HOURS", "168"))
        return max(1, hours) * 3600
    except ValueError:
        return 168 * 3600


@dataclass
class ResolutionCase:
    """Longitudinal state of an ongoing taxpayer resolution journey."""

    case_id: str = field(default_factory=lambda: f"res-{uuid.uuid4().hex[:10]}")
    user_id: str = ""
    tenant_id: str = "default"
    thread_id: str = ""
    source_channel: str = "web"  # "web" | "voice_call" | "voice_ws"
    locale: str = "en"
    primary_objective: str = ""
    status: str = "in_progress"  # "in_progress" | "awaiting_user_action" | "in_progress_background" | "escalated_officer" | "resolved"
    completed_steps: list[str] = field(default_factory=list)
    pending_steps: list[str] = field(default_factory=list)
    handoff_envelope: dict[str, Any] = field(default_factory=dict)
    background_task_id: str = ""
    updated_at: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResolutionCase:
        now = time.time()
        return cls(
            case_id=str(data.get("case_id") or f"res-{uuid.uuid4().hex[:10]}"),
            user_id=str(data.get("user_id") or ""),
            tenant_id=str(data.get("tenant_id") or "default"),
            thread_id=str(data.get("thread_id") or ""),
            source_channel=str(data.get("source_channel") or "web"),
            locale=str(data.get("locale") or "en"),
            primary_objective=str(data.get("primary_objective") or ""),
            status=str(data.get("status") or "in_progress"),
            completed_steps=list(data.get("completed_steps") or []),
            pending_steps=list(data.get("pending_steps") or []),
            handoff_envelope=dict(data.get("handoff_envelope") or {}),
            background_task_id=str(data.get("background_task_id") or ""),
            updated_at=float(data.get("updated_at") or now),
            expires_at=float(data.get("expires_at") or (now + get_resolution_case_ttl_seconds())),
        )


def build_resumption_prompt(case: ResolutionCase, target_locale: str = "en") -> str:
    """Build a concise cross-channel resumption summary for prompts or voice greetings."""
    if not case.primary_objective or case.status == "resolved":
        return ""
    next_step = case.pending_steps[0] if case.pending_steps else "the next verification step"
    loc = (target_locale or case.locale or "en").lower()
    if loc == "lg":
        return (
            f"Ku mulundi ogwaggwa ({case.source_channel}), twali tukola ku: {case.primary_objective}. "
            f"Ekiddako: {next_step}."
        )
    if loc == "sw":
        return (
            f"Katika kipindi kilichopita ({case.source_channel}), tulikuwa tukishughulikia: "
            f"{case.primary_objective}. Hatua inayofuata: {next_step}."
        )
    return (
        f"Continuing active resolution case ({case.case_id} from {case.source_channel}): "
        f"Objective '{case.primary_objective}' [{case.status}]. Next pending step: {next_step}."
    )


class ResolutionLedger:
    """Consent-gated persistence for longitudinal taxpayer resolution cases."""

    def checkpoint(
        self,
        user_id: str,
        case: ResolutionCase,
        *,
        tenant_id: str = "default",
        asr_confidence: float | None = None,
        min_asr_confidence: float = 0.6,
    ) -> bool:
        """Persist *case* into shared working memory when consent and ASR confidence gates pass."""
        if not user_id:
            return False
        if asr_confidence is not None and asr_confidence < min_asr_confidence:
            logger.info(
                "Skipping resolution case checkpoint due to low ASR confidence (%.2f < %.2f)",
                float(asr_confidence),
                float(min_asr_confidence),
            )
            return False
        try:
            from .. import database as db

            tenant = tenant_id or case.tenant_id or "default"
            if not db.has_active_consent(user_id, "personalization", tenant_id=tenant):
                return False
            ttl = get_resolution_case_ttl_seconds()
            now = time.time()
            case.user_id = user_id
            case.tenant_id = tenant
            case.updated_at = now
            case.expires_at = now + ttl
            return db.upsert_working_memory(
                user_id,
                tenant,
                {
                    "active_resolution_case": case.to_dict(),
                    "last_topic": case.primary_objective or "general",
                },
                ttl_seconds=ttl,
            )
        except Exception:
            logger.debug("resolution_ledger: checkpoint failed", exc_info=True)
            return False

    def get_active_case(
        self,
        user_id: str,
        *,
        tenant_id: str = "default",
        current_channel: str = "",
    ) -> ResolutionCase | None:
        """Fetch an active (unexpired, non-resolved) ResolutionCase for *user_id*."""
        if not user_id:
            return None
        try:
            from .. import database as db

            tenant = tenant_id or "default"
            if not db.has_active_consent(user_id, "personalization", tenant_id=tenant):
                return None
            working = db.get_working_memory(user_id, tenant_id=tenant)
            if not isinstance(working, dict):
                return None
            raw = working.get("active_resolution_case")
            if not isinstance(raw, dict):
                return None
            case = ResolutionCase.from_dict(raw)
            if case.expires_at and case.expires_at <= time.time():
                return None
            if case.status == "resolved":
                return None
            if current_channel and case.source_channel and current_channel != case.source_channel:
                try:
                    from ..analytics import metrics

                    metrics.inc(
                        "relationship_resumption_total",
                        labels={
                            "from_channel": case.source_channel,
                            "to_channel": current_channel,
                            "status": "resumed",
                        },
                    )
                except Exception:
                    logger.debug("resolution_ledger: resumption metric failed", exc_info=True)
            return case
        except Exception:
            logger.debug("resolution_ledger: read failed", exc_info=True)
            return None

    def attach_background_task(
        self,
        user_id: str,
        task_id: str,
        *,
        tenant_id: str = "default",
        completed_step: str = "",
        evidence_note: str = "",
    ) -> ResolutionCase | None:
        """Link or complete an asynchronous MCP background task on an open resolution case."""
        case = self.get_active_case(user_id, tenant_id=tenant_id)
        if case is None:
            return None
        case.background_task_id = task_id
        if completed_step:
            if completed_step not in case.completed_steps:
                case.completed_steps.append(completed_step)
            case.pending_steps = [s for s in case.pending_steps if s != completed_step]
            if not case.pending_steps:
                case.status = "awaiting_user_action"
        else:
            case.status = "in_progress_background"
        if evidence_note and isinstance(case.handoff_envelope, dict):
            ev = list(case.handoff_envelope.get("verified_evidence") or [])
            ev.append(
                {
                    "source": f"mcp_task:{task_id}",
                    "fact": evidence_note,
                    "citation_index": 0,
                    "confidence": 1.0,
                }
            )
            case.handoff_envelope["verified_evidence"] = ev
        self.checkpoint(user_id, case, tenant_id=tenant_id)
        return case

    def mark_resolved(
        self,
        user_id: str,
        *,
        tenant_id: str = "default",
        channel: str = "web",
    ) -> bool:
        """Mark the user's active resolution case as resolved and emit telemetry."""
        case = self.get_active_case(user_id, tenant_id=tenant_id)
        if case is None:
            return False
        prev_channel = case.source_channel or channel
        case.status = "resolved"
        case.pending_steps = []
        ok = self.checkpoint(user_id, case, tenant_id=tenant_id)
        if ok:
            try:
                from ..analytics import metrics

                metrics.inc(
                    "relationship_resumption_total",
                    labels={
                        "from_channel": prev_channel,
                        "to_channel": channel,
                        "status": "completed",
                    },
                )
            except Exception:
                logger.debug("resolution_ledger: completion metric failed", exc_info=True)
        return ok

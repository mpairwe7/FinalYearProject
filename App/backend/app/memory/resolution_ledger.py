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


def get_max_concurrent_cases() -> int:
    """Maximum concurrent ResolutionCase entries per user across tax domains (default 5)."""
    try:
        return max(1, min(20, int(os.getenv("RESOLUTION_MAX_CONCURRENT_CASES", "5"))))
    except ValueError:
        return 5


def infer_domain_tag(text: str) -> str:
    """Infer a coarse tax domain tag from an objective or query string."""
    lower = (text or "").lower()
    if any(w in lower for w in ("customs", "duty", "import", "cif", "tariff", "ASYCUDA".lower(), "transit", "forodha")):
        return "customs"
    if any(w in lower for w in ("efris", "fdn", "fiscal", "invoice")):
        return "efris"
    if any(w in lower for w in ("objection", "dispute", "appeal", "tat", "remission", "waiver")):
        return "objection"
    if any(w in lower for w in ("tin", "register", "registration")):
        return "tin_registration"
    if any(w in lower for w in ("vat", "value added")):
        return "vat"
    if any(w in lower for w in ("paye", "salary", "employment")):
        return "paye"
    if any(w in lower for w in ("prn", "payment", "pay")):
        return "payment"
    return "general"


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
    domain_tag: str = ""
    status: str = "in_progress"  # "in_progress" | "awaiting_user_action" | "in_progress_background" | "escalated_officer" | "resolved"
    completed_steps: list[str] = field(default_factory=list)
    pending_steps: list[str] = field(default_factory=list)
    handoff_envelope: dict[str, Any] = field(default_factory=dict)
    verified_slot_bank: dict[str, Any] = field(default_factory=dict)
    background_task_id: str = ""
    updated_at: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        if not self.domain_tag:
            self.domain_tag = infer_domain_tag(self.primary_objective)
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResolutionCase:
        now = time.time()
        obj = str(data.get("primary_objective") or "")
        return cls(
            case_id=str(data.get("case_id") or f"res-{uuid.uuid4().hex[:10]}"),
            user_id=str(data.get("user_id") or ""),
            tenant_id=str(data.get("tenant_id") or "default"),
            thread_id=str(data.get("thread_id") or ""),
            source_channel=str(data.get("source_channel") or "web"),
            locale=str(data.get("locale") or "en"),
            primary_objective=obj,
            domain_tag=str(data.get("domain_tag") or infer_domain_tag(obj)),
            status=str(data.get("status") or "in_progress"),
            completed_steps=list(data.get("completed_steps") or []),
            pending_steps=list(data.get("pending_steps") or []),
            handoff_envelope=dict(data.get("handoff_envelope") or {}),
            verified_slot_bank=dict(data.get("verified_slot_bank") or {}),
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
            if not case.domain_tag:
                case.domain_tag = infer_domain_tag(case.primary_objective)
            case_dict = case.to_dict()

            existing = db.get_working_memory(user_id, tenant_id=tenant) or {}
            cases_map = dict(existing.get("active_resolution_cases") or {})
            cases_map[case.case_id] = case_dict
            # Prune resolved/expired or oldest beyond cap
            max_cases = get_max_concurrent_cases()
            active_items = [
                (cid, cdata)
                for cid, cdata in cases_map.items()
                if isinstance(cdata, dict)
                and float(cdata.get("expires_at") or (now + ttl)) > now
                and str(cdata.get("status") or "") != "resolved"
            ]
            active_items.sort(key=lambda item: float(item[1].get("updated_at") or 0.0), reverse=True)
            bounded_map = dict(active_items[:max_cases])

            return db.upsert_working_memory(
                user_id,
                tenant,
                {
                    "active_resolution_case": case_dict,
                    "active_resolution_cases": bounded_map,
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
        ok = self.checkpoint(user_id, case, tenant_id=tenant_id)
        if completed_step and ok:
            try:
                from .. import database as db
                from ..flags import flags

                if flags.is_enabled("proactive_resolution_continuation", subject=user_id):
                    summary_msg = build_resumption_prompt(case, target_locale=case.locale)
                    if evidence_note:
                        summary_msg = f"{summary_msg} ({evidence_note})".strip()
                    db.upsert_reminder_inbox(
                        user_id=user_id,
                        deadline_name=f"Resolution Update: {case.primary_objective[:48]}",
                        due_date=time.strftime("%Y-%m-%d", time.gmtime(time.time() + 86400)),
                        message=summary_msg or f"Background task {task_id} completed for {case.case_id}.",
                    )
            except Exception:
                logger.debug("resolution_ledger: proactive reminder creation skipped", exc_info=True)
        return case

    def store_verified_slots(
        self,
        user_id: str,
        slots: dict[str, Any],
        *,
        tenant_id: str = "default",
    ) -> bool:
        """Persist validated workflow/calculator slots in shared working memory for zero-effort prefill."""
        if not user_id or not slots:
            return False
        try:
            from .. import database as db

            tenant = tenant_id or "default"
            if not db.has_active_consent(user_id, "personalization", tenant_id=tenant):
                return False
            existing = db.get_working_memory(user_id, tenant_id=tenant) or {}
            bank = dict(existing.get("verified_slot_bank") or {})
            for k, v in slots.items():
                if k and v not in ("", None):
                    bank[str(k)] = v
            return db.upsert_working_memory(
                user_id,
                tenant,
                {"verified_slot_bank": bank},
                ttl_seconds=get_resolution_case_ttl_seconds(),
            )
        except Exception:
            logger.debug("resolution_ledger: store_verified_slots failed", exc_info=True)
            return False

    def get_verified_slots(
        self,
        user_id: str,
        *,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        """Read consented verified slots for zero-effort workflow prefill."""
        if not user_id:
            return {}
        try:
            from .. import database as db

            tenant = tenant_id or "default"
            if not db.has_active_consent(user_id, "personalization", tenant_id=tenant):
                return {}
            working = db.get_working_memory(user_id, tenant_id=tenant) or {}
            bank = working.get("verified_slot_bank")
            return dict(bank) if isinstance(bank, dict) else {}
        except Exception:
            logger.debug("resolution_ledger: get_verified_slots failed", exc_info=True)
            return {}

    def sync_offline_actions(
        self,
        user_id: str,
        actions: list[dict[str, Any]],
        *,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        """Sync queued PWA offline calculator receipts and workflow slots upon reconnection.

        Gated by ``FLAG_OFFLINE_ACTION_OUTBOX`` and active ``personalization`` consent.
        """
        from ..flags import flags

        if not user_id or not actions:
            return {"synced_count": 0, "slots_merged": {}}
        if not flags.is_enabled("offline_action_outbox", subject=user_id):
            return {"synced_count": 0, "slots_merged": {}, "skipped": "flag_disabled"}
        try:
            from .. import database as db

            tenant = tenant_id or "default"
            if not db.has_active_consent(user_id, "personalization", tenant_id=tenant):
                return {"synced_count": 0, "slots_merged": {}, "skipped": "consent_required"}

            existing_slots = self.get_verified_slots(user_id, tenant_id=tenant)
            existing_case = self.get_active_case(user_id, tenant_id=tenant)
            existing_steps = set(existing_case.completed_steps if existing_case is not None else [])

            merged_slots: dict[str, Any] = {}
            completed_notes: list[str] = []
            seen_notes = set(existing_steps)
            action_outcomes: list[tuple[bool, bool]] = []
            last_objective = ""
            for item in actions[:25]:
                if not isinstance(item, dict):
                    continue
                has_new_slot = False
                slots = item.get("slots")
                if isinstance(slots, dict):
                    for k, v in slots.items():
                        key = str(k) if k else ""
                        if key and v not in ("", None):
                            prev_val = merged_slots.get(key, existing_slots.get(key))
                            if prev_val != v:
                                has_new_slot = True
                            merged_slots[key] = v
                has_new_note = False
                tool = str(item.get("tool") or item.get("workflow_id") or "").strip()
                summary = str(item.get("summary") or "").strip()
                if tool or summary:
                    note = f"Offline {tool}: {summary}".strip(": ")
                    if note and note not in seen_notes:
                        seen_notes.add(note)
                        completed_notes.append(note)
                        has_new_note = True
                    if not last_objective:
                        last_objective = summary or f"Offline {tool}"
                if has_new_slot or has_new_note:
                    action_outcomes.append((has_new_slot, has_new_note))

            slots_ok = True
            if merged_slots:
                slots_ok = bool(self.store_verified_slots(user_id, merged_slots, tenant_id=tenant))

            checkpoint_ok = True
            if completed_notes:
                case = existing_case or ResolutionCase(
                    user_id=user_id,
                    tenant_id=tenant,
                    source_channel="offline_pwa",
                    primary_objective=last_objective or "Offline tax calculation follow-up",
                    status="awaiting_user_action",
                )
                for note in completed_notes:
                    if note not in case.completed_steps:
                        case.completed_steps.append(note)
                checkpoint_ok = bool(self.checkpoint(user_id, case, tenant_id=tenant))
                if checkpoint_ok:
                    try:
                        from ..analytics import metrics

                        metrics.inc(
                            "relationship_resumption_total",
                            labels={
                                "from_channel": "offline_pwa",
                                "to_channel": "web",
                                "status": "resumed",
                            },
                        )
                    except Exception as _exc:  # noqa: BLE001
                        _ = _exc

            applied_count = sum(
                1
                for needs_slots, needs_note in action_outcomes
                if (not needs_slots or slots_ok) and (not needs_note or checkpoint_ok)
            )
            all_ok = slots_ok and checkpoint_ok
            result: dict[str, Any] = {
                "ok": all_ok,
                "synced_count": applied_count,
                "slots_merged": merged_slots if slots_ok else {},
            }
            if not all_ok:
                result["partial"] = applied_count > 0
                result["error"] = "persistence_write_failed"
            return result
        except Exception:
            logger.debug("resolution_ledger: sync_offline_actions failed", exc_info=True)
            return {"synced_count": 0, "slots_merged": {}}

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

    def list_active_cases(
        self,
        user_id: str,
        *,
        tenant_id: str = "default",
    ) -> list[ResolutionCase]:
        """Return all unexpired, non-resolved concurrent ResolutionCases for *user_id*, newest first."""
        if not user_id:
            return []
        try:
            from .. import database as db

            tenant = tenant_id or "default"
            if not db.has_active_consent(user_id, "personalization", tenant_id=tenant):
                return []
            working = db.get_working_memory(user_id, tenant_id=tenant)
            if not isinstance(working, dict):
                return []
            now = time.time()
            results: dict[str, ResolutionCase] = {}
            raw_map = working.get("active_resolution_cases")
            if isinstance(raw_map, dict):
                for cid, cdata in raw_map.items():
                    if isinstance(cdata, dict):
                        c = ResolutionCase.from_dict(cdata)
                        if (not c.expires_at or c.expires_at > now) and c.status != "resolved":
                            results[c.case_id or str(cid)] = c
            raw_single = working.get("active_resolution_case")
            if isinstance(raw_single, dict):
                c_single = ResolutionCase.from_dict(raw_single)
                if (not c_single.expires_at or c_single.expires_at > now) and c_single.status != "resolved":
                    results[c_single.case_id] = c_single
            return sorted(results.values(), key=lambda x: x.updated_at, reverse=True)
        except Exception:
            logger.debug("resolution_ledger: list_active_cases failed", exc_info=True)
            return []

    def select_case_for_turn(
        self,
        user_id: str,
        *,
        query: str = "",
        domain_tag: str = "",
        tenant_id: str = "default",
        current_channel: str = "",
    ) -> ResolutionCase | None:
        """Select the best matching active ResolutionCase by domain_tag or query keywords."""
        cases = self.list_active_cases(user_id, tenant_id=tenant_id)
        if not cases:
            return None
        target_domain = (domain_tag or infer_domain_tag(query)).strip().lower()
        selected = cases[0]
        if target_domain and target_domain != "general":
            for c in cases:
                if (c.domain_tag or infer_domain_tag(c.primary_objective)) == target_domain:
                    selected = c
                    break
        if current_channel and selected.source_channel and current_channel != selected.source_channel:
            try:
                from ..analytics import metrics

                metrics.inc(
                    "relationship_resumption_total",
                    labels={
                        "from_channel": selected.source_channel,
                        "to_channel": current_channel,
                        "status": "resumed",
                    },
                )
            except Exception:
                logger.debug("resolution_ledger: select_case_for_turn metric skipped", exc_info=True)
        return selected

    def promote_anonymous_session(
        self,
        anonymous_session_id: str,
        user_id: str,
        *,
        conversation_id: str = "",
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        """Promote anonymous session slots and active workflow state into an authenticated user profile."""
        if not anonymous_session_id or not user_id or anonymous_session_id == user_id:
            return {"ok": False, "promoted_slots": 0, "workflow_migrated": False}
        try:
            from .. import database as db

            tenant = tenant_id or "default"
            if not db.has_active_consent(user_id, "personalization", tenant_id=tenant):
                return {
                    "ok": False,
                    "promoted_slots": 0,
                    "workflow_migrated": False,
                    "skipped": "consent_required",
                }

            anon_working = db.get_working_memory(anonymous_session_id, tenant_id=tenant) or {}
            anon_slots = anon_working.get("verified_slot_bank")
            promoted_slots = 0
            if (
                isinstance(anon_slots, dict)
                and anon_slots
                and self.store_verified_slots(user_id, anon_slots, tenant_id=tenant)
            ):
                promoted_slots = len(anon_slots)

            workflow_migrated = False
            if conversation_id:
                anon_key = db.conversation_state_key(
                    conversation_id,
                    session_id=anonymous_session_id,
                    user_id=None,
                    tenant_id=tenant,
                )
                auth_key = db.conversation_state_key(
                    conversation_id,
                    session_id=anonymous_session_id,
                    user_id=user_id,
                    tenant_id=tenant,
                )
                wf_row = db.get_workflow_session(anon_key, tenant_id=tenant)
                if isinstance(wf_row, dict) and wf_row.get("workflow_id"):
                    db.upsert_workflow_session(
                        auth_key,
                        workflow_id=str(wf_row["workflow_id"]),
                        current_step_idx=int(wf_row.get("current_step_idx") or 0),
                        slots=dict(wf_row.get("slots") or {}),
                        status=str(wf_row.get("status") or "active"),
                        tenant_id=tenant,
                    )
                    workflow_migrated = True
                    wf_slots = wf_row.get("slots")
                    if isinstance(wf_slots, dict) and wf_slots:
                        clean_wf_slots = {k: v for k, v in wf_slots.items() if not str(k).startswith("_")}
                        if clean_wf_slots and self.store_verified_slots(user_id, clean_wf_slots, tenant_id=tenant):
                            promoted_slots += len(clean_wf_slots)

            return {
                "ok": True,
                "promoted_slots": promoted_slots,
                "workflow_migrated": workflow_migrated,
            }
        except Exception:
            logger.debug("resolution_ledger: promote_anonymous_session failed", exc_info=True)
            return {"ok": False, "promoted_slots": 0, "workflow_migrated": False}

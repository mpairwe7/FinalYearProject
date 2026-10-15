"""Enterprise Governance, Action-Level Auditability v3 & Continuous QA Scorecard (Phase 34, 2026).

Implements:
1. Tamper-evident ``ActionAuditRecord`` (v3) with UTC + EAT (Africa/Kampala) timestamps,
   salted HMAC taxpayer attribution, intent confidence, full decision trace, guardrail
   evaluation trace, officer approval gate record, and reversibility contract.
2. Risk-Tiered Autonomy Matrix (``low``, ``medium``, ``high``, ``critical``) with
   explicit approval gates and compensation/reversal contracts.
3. 100% deterministic ``InteractionQualityScorecard`` across statutory accuracy, figure
   fidelity, policy adherence, multilingual empathy, resolution completeness, and
   handoff cleanliness.
4. Self-healing ``QualityCircuitBreaker`` that throttles Medium/High-risk autonomy when
   rolling quality or figure fidelity drops below threshold.
5. Human-readable ``DecisionReceipt`` for taxpayers, supervisors, and external auditors.
"""

from __future__ import annotations

import collections
import datetime as dt
import hashlib
import hmac
import json
import logging
import os
import threading
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..flags import flags
from .turns import provenance

logger = logging.getLogger(__name__)

POLICY_VERSION = "2026.10-phase34.v3"
_UTC_TZ = dt.timezone(dt.timedelta(0), name="UTC")
_EAT_TZ = dt.timezone(dt.timedelta(hours=3), name="EAT")

RiskTier = Literal["low", "medium", "high", "critical"]
AutonomyOutcome = Literal[
    "autonomous_executed",
    "soft_confirmed_executed",
    "proposed_awaiting_officer",
    "officer_approved_executed",
    "officer_rejected",
    "hard_blocked",
    "failed_closed",
]


class GuardrailEvaluationTrace(BaseModel):
    """Preventive and detective guardrail verdicts evaluated on an action."""

    policy_version: str = Field(default=POLICY_VERSION)
    prompt_injection_passed: bool = True
    pii_redaction_applied: bool = True
    tool_allowlist_passed: bool = True
    schema_validation_passed: bool = True
    figure_fidelity_passed: bool = True
    budget_remaining_calls: int = Field(default=3, ge=0)
    evaluated_rules: list[str] = Field(
        default_factory=lambda: [
            "OWASP-LLM01-INJECTION",
            "URA-FIG-FIDELITY-01",
            "URA-PII-REDACT-01",
            "URA-RISK-TIER-GATE-03",
        ]
    )


class ReversibilityContract(BaseModel):
    """Reversal or compensation path for consequential actions."""

    reversible: bool = True
    compensation_tool_or_procedure: str = Field(
        default="Idempotent read-only action; re-run with updated parameters."
    )
    reversal_window_hours: int | None = Field(default=None, ge=0)
    pre_state_digest_sha256: str = ""


class HumanApprovalGateRecord(BaseModel):
    """Evidence of caller confirmation challenge and/or live officer approval."""

    approval_required: bool = False
    caller_confirm_challenge_verified: bool = False
    officer_approval_obtained: bool = False
    approving_officer_id_hash: str = ""
    approval_timestamp_utc: str | None = None
    ticket_id: str | None = None


class ActionAuditRecord(BaseModel):
    """Immutable runtime evidence record written to AuditLedger for every consequential action."""

    schema_version: Literal[3] = 3
    audit_event_id: str = ""
    prev_row_hash: str = ""
    row_hash: str = ""

    timestamp_utc: str
    timestamp_eat: str
    tenant_id: str = "default"
    session_id: str
    channel: str = "rest"
    taxpayer_tin_hmac: str = ""

    intent_detected: str
    intent_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    agent_role: str = "tax_specialist"
    decision_trace: list[str] = Field(default_factory=list)
    human_readable_explanation: str = ""

    risk_tier: RiskTier = "low"
    tool_or_action_name: str
    parameters_redacted: dict[str, Any] = Field(default_factory=dict)
    parameters_sha256: str
    response_summary_redacted: dict[str, Any] = Field(default_factory=dict)
    response_sha256: str
    execution_status: Literal[
        "success", "error", "timeout", "blocked", "simulated_sandbox", "proposed"
    ] = "success"
    execution_latency_ms: float = Field(default=0.0, ge=0.0)

    guardrails: GuardrailEvaluationTrace = Field(default_factory=GuardrailEvaluationTrace)
    approval_gate: HumanApprovalGateRecord = Field(default_factory=HumanApprovalGateRecord)
    reversibility: ReversibilityContract = Field(default_factory=ReversibilityContract)
    provenance: dict[str, Any] = Field(default_factory=dict)

    autonomy_outcome: AutonomyOutcome = "autonomous_executed"
    resolution_status: Literal[
        "resolved", "awaiting_user_action", "escalated_to_officer", "abstained"
    ] = "resolved"


class InteractionQualityScorecard(BaseModel):
    """100% turn-level and session-level QA scorecard (Detective Guardrail Layer)."""

    scorecard_id: str
    audit_event_id: str = ""
    session_id: str
    timestamp_utc: str
    locale: str = "en"
    channel: str = "rest"

    statutory_accuracy_score: float = Field(..., ge=0.0, le=1.0)
    figure_fidelity_score: float = Field(..., ge=0.0, le=1.0)
    policy_adherence_score: float = Field(..., ge=0.0, le=1.0)
    language_and_empathy_score: float = Field(..., ge=0.0, le=1.0)
    resolution_completeness_score: float = Field(..., ge=0.0, le=1.0)
    handoff_cleanliness_score: float | None = Field(default=None, ge=0.0, le=1.0)

    composite_quality_score: float = Field(..., ge=0.0, le=1.0)
    anomaly_flags: list[str] = Field(default_factory=list)
    flagged_for_human_review: bool = False
    autonomy_throttle_triggered: bool = False


class DecisionReceipt(BaseModel):
    """Human-readable decision explanation for taxpayers, officers, and external auditors."""

    audit_event_id: str
    timestamp_eat: str
    risk_tier: RiskTier
    autonomy_outcome: AutonomyOutcome
    policy_version: str = POLICY_VERSION
    statutory_basis: list[str] = Field(default_factory=list)
    verified_inputs: list[str] = Field(default_factory=list)
    explanation: str
    reversibility_note: str


# ---------------------------------------------------------------------------
# Risk-Tier & Reversibility Mapping
# ---------------------------------------------------------------------------

_RISK_TIER_MAP: dict[str, tuple[RiskTier, str, int | None]] = {
    # Low-risk deterministic calculators & lookups
    "lookup_tax_rate": ("low", "Idempotent rate lookup; re-query with explicit fiscal_year.", None),
    "search_knowledge_base": ("low", "Read-only retrieval; re-query knowledge base.", None),
    "calculate_paye": ("low", "Pure statutory calculation; re-run with updated gross pay.", None),
    "calculate_vat": ("low", "Pure statutory calculation; re-run with updated taxable amount.", None),
    "calculate_withholding": ("low", "Pure statutory calculation; re-run with updated payment.", None),
    "calculate_rental_tax": ("low", "Pure statutory calculation; re-run with updated rent.", None),
    "calculate_corporation_tax": ("low", "Pure statutory calculation; re-run with updated income.", None),
    "calculate_excise_duty": ("low", "Pure statutory calculation; re-run with updated quantity/value.", None),
    "calculate_customs_duty": ("low", "Pure statutory calculation; re-run with updated CIF value.", None),
    "calculate_capital_gains": ("low", "Pure statutory calculation; re-run with updated cost base.", None),
    # Medium-risk status checks, PRN advice & guided slot prefill
    "verify_tin": ("medium", "Read-only TIN verification; clear verified_slot_bank via /v1/me.", 168),
    "check_prn_status": ("medium", "Read-only PRN status check; no ledger state modified.", 168),
    "generate_payment_advice": (
        "medium",
        "Unpaid PRN expires automatically after statutory window or can be cancelled before payment.",
        72,
    ),
    "sync_offline_actions": (
        "medium",
        "Merged slots and ResolutionCase steps can be reset via reset_workflow or /v1/me erasure.",
        168,
    ),
    # High-risk account/compliance proposals requiring human-in-the-loop approval
    "tin_registration_submit": (
        "high",
        "Draft registration remains uncommitted until officer approval; amendable under TPC Sec. 23.",
        720,
    ),
    "efris_submit_adjustment": (
        "high",
        "Credit/debit note adjustment requires officer approval and EFRIS reversal fiscal note.",
        336,
    ),
    "submit_return_amendment": (
        "high",
        "Return amendment requires officer review; further self-amendment governed by TPC Sec. 23.",
        720,
    ),
    "file_notice_of_objection": (
        "high",
        "Objection under TPC Sec. 24 (45-day window) may be withdrawn in writing before TAT referral.",
        1080,
    ),
    "request_penalty_remission": (
        "high",
        "Penalty remission application under TPC Sec. 40 requires Commissioner/officer review.",
        720,
    ),
    "recommend_tax_refund": (
        "high",
        "Refund recommendation requires audit verification and officer sign-off prior to credit.",
        720,
    ),
    "escalate_to_human": (
        "high",
        "Officer can release collision lock, reassign queue, or delegate follow-up back to AI.",
        168,
    ),
    # Critical actions (never autonomous)
    "mutate_tax_liability_ledger": (
        "critical",
        "Hard-blocked on AI channel; requires dual-control core e-Tax officer authorization.",
        0,
    ),
    "disburse_treasury_funds": (
        "critical",
        "Hard-blocked on AI channel; requires BoU/URA Treasury dual-control sign-off.",
        0,
    ),
}


def classify_action_risk(tool_or_action_name: str) -> RiskTier:
    """Return the canonical RiskTier for a URA tool or action name."""
    norm = (tool_or_action_name or "").strip().lower()
    if norm in _RISK_TIER_MAP:
        return _RISK_TIER_MAP[norm][0]
    if any(k in norm for k in ("disburse", "write_off", "delete_ledger", "mutate_liability")):
        return "critical"
    if any(k in norm for k in ("submit", "amend", "objection", "refund", "remission", "adjustment")):
        return "high"
    if any(k in norm for k in ("prn", "verify_tin", "prefill", "sync_offline", "status")):
        return "medium"
    return "low"


def get_reversibility_contract(
    tool_or_action_name: str,
    pre_state: dict[str, Any] | None = None,
) -> ReversibilityContract:
    """Build a structured ReversibilityContract for any tool or action."""
    norm = (tool_or_action_name or "").strip().lower()
    tier, proc, window_h = _RISK_TIER_MAP.get(
        norm,
        (
            classify_action_risk(norm),
            "Standard URA administrative review or workflow reset.",
            168,
        ),
    )
    pre_digest = ""
    if pre_state:
        pre_digest = hashlib.sha256(
            json.dumps(dict(pre_state), sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
    return ReversibilityContract(
        reversible=(tier != "critical"),
        compensation_tool_or_procedure=proc,
        reversal_window_hours=window_h,
        pre_state_digest_sha256=pre_digest,
    )


# ---------------------------------------------------------------------------
# Self-Healing Quality Circuit Breaker
# ---------------------------------------------------------------------------

class QualityCircuitBreaker:
    """Tracks rolling 100% quality scorecards and throttles autonomy when quality dips."""

    def __init__(self, window_size: int = 20, composite_floor: float = 0.88) -> None:
        self._window_size = max(5, window_size)
        self._composite_floor = composite_floor
        self._scores: collections.deque[float] = collections.deque(maxlen=self._window_size)
        self._figure_drift_events = 0
        self._lock = threading.Lock()

    def record(self, scorecard: InteractionQualityScorecard) -> bool:
        """Record a scorecard and return True if autonomy throttling is now active."""
        with self._lock:
            self._scores.append(scorecard.composite_quality_score)
            if scorecard.figure_fidelity_score < 1.0:
                self._figure_drift_events += 1
            return self._is_throttled_unlocked()

    def _is_throttled_unlocked(self) -> bool:
        if self._figure_drift_events > 0:
            return True
        if len(self._scores) >= 3:
            mean_score = sum(self._scores) / len(self._scores)
            if mean_score < self._composite_floor:
                return True
        return False

    def is_throttled(self, *, subject: str | None = None) -> bool:
        if not flags.is_enabled("quality_circuit_breaker", subject=subject):
            return False
        with self._lock:
            return self._is_throttled_unlocked()

    def reset(self) -> None:
        with self._lock:
            self._scores.clear()
            self._figure_drift_events = 0


quality_circuit_breaker = QualityCircuitBreaker()


# ---------------------------------------------------------------------------
# Risk-Tiered Autonomy Gate Evaluation
# ---------------------------------------------------------------------------

def evaluate_autonomy_gate(
    tool_or_action_name: str,
    *,
    intent_confidence: float = 1.0,
    caller_confirmed: bool = False,
    officer_approved: bool = False,
    approving_officer_id: str = "",
    ticket_id: str | None = None,
    guardrails_passed: bool = True,
    subject: str | None = None,
) -> tuple[AutonomyOutcome, HumanApprovalGateRecord]:
    """Evaluate whether an action may execute autonomously, needs soft confirmation, or requires officer sign-off."""
    tier = classify_action_risk(tool_or_action_name)
    now_utc = dt.datetime.now(_UTC_TZ).isoformat()
    officer_hash = (
        hashlib.sha256(approving_officer_id.encode()).hexdigest()[:32]
        if approving_officer_id
        else ""
    )

    if not guardrails_passed:
        return (
            "failed_closed",
            HumanApprovalGateRecord(
                approval_required=(tier in ("high", "critical")),
                caller_confirm_challenge_verified=caller_confirmed,
                officer_approval_obtained=officer_approved,
                approving_officer_id_hash=officer_hash,
                ticket_id=ticket_id,
            ),
        )

    if tier == "critical":
        return (
            "hard_blocked",
            HumanApprovalGateRecord(
                approval_required=True,
                caller_confirm_challenge_verified=caller_confirmed,
                officer_approval_obtained=False,
                ticket_id=ticket_id,
            ),
        )

    throttled = quality_circuit_breaker.is_throttled(subject=subject)

    if tier == "high" or (tier == "medium" and throttled):
        if caller_confirmed and officer_approved and approving_officer_id:
            return (
                "officer_approved_executed",
                HumanApprovalGateRecord(
                    approval_required=True,
                    caller_confirm_challenge_verified=True,
                    officer_approval_obtained=True,
                    approving_officer_id_hash=officer_hash,
                    approval_timestamp_utc=now_utc,
                    ticket_id=ticket_id,
                ),
            )
        return (
            "proposed_awaiting_officer",
            HumanApprovalGateRecord(
                approval_required=True,
                caller_confirm_challenge_verified=caller_confirmed,
                officer_approval_obtained=False,
                ticket_id=ticket_id,
            ),
        )

    if tier == "medium":
        if caller_confirmed or intent_confidence >= 0.80:
            return (
                "soft_confirmed_executed",
                HumanApprovalGateRecord(
                    approval_required=False,
                    caller_confirm_challenge_verified=caller_confirmed,
                    officer_approval_obtained=False,
                    ticket_id=ticket_id,
                ),
            )
        return (
            "proposed_awaiting_officer",
            HumanApprovalGateRecord(
                approval_required=True,
                caller_confirm_challenge_verified=False,
                officer_approval_obtained=False,
                ticket_id=ticket_id,
            ),
        )

    return (
        "autonomous_executed",
        HumanApprovalGateRecord(
            approval_required=False,
            caller_confirm_challenge_verified=caller_confirmed,
            officer_approval_obtained=officer_approved,
            approving_officer_id_hash=officer_hash,
            ticket_id=ticket_id,
        ),
    )


# ---------------------------------------------------------------------------
# 100% Continuous Quality Scorecard Evaluator
# ---------------------------------------------------------------------------

def evaluate_interaction_scorecard(
    *,
    session_id: str,
    locale: str = "en",
    channel: str = "rest",
    reply_text: str = "",
    sources: list[Any] | None = None,
    figure_fidelity_passed: bool = True,
    policy_adherence_passed: bool = True,
    distress_detected: bool = False,
    relief_cited: bool = False,
    sub_tasks_total: int = 1,
    sub_tasks_completed: int = 1,
    handoff_completeness_ratio: float | None = None,
    repeated_tool_failures: int = 0,
    audit_event_id: str = "",
    subject: str | None = None,
) -> InteractionQualityScorecard:
    """Compute a deterministic 100% turn-level QA scorecard and update the circuit breaker."""
    import uuid

    now_utc = dt.datetime.now(_UTC_TZ).isoformat()
    has_sources_or_calc = bool(sources) or ("ugx" in (reply_text or "").lower()) or ("%" in (reply_text or ""))
    statutory_score = 1.0 if (has_sources_or_calc and figure_fidelity_passed) else (0.85 if reply_text else 0.0)
    figure_score = 1.0 if figure_fidelity_passed else 0.0
    policy_score = 1.0 if policy_adherence_passed else 0.0

    if distress_detected:
        empathy_score = 1.0 if relief_cited else 0.65
    else:
        empathy_score = 1.0 if reply_text.strip() else 0.5

    completeness_score = (
        min(1.0, max(0.0, float(sub_tasks_completed) / float(max(1, sub_tasks_total))))
        if reply_text.strip()
        else 0.0
    )

    weights = [
        (statutory_score, 0.30),
        (figure_score, 0.30),
        (policy_score, 0.15),
        (empathy_score, 0.10),
        (completeness_score, 0.15),
    ]
    composite = round(sum(s * w for s, w in weights), 4)

    anomalies: list[str] = []
    if not figure_fidelity_passed:
        anomalies.append("NUMERIC_DRIFT_DETECTED")
    if not policy_adherence_passed:
        anomalies.append("POLICY_GATE_VIOLATION")
    if repeated_tool_failures >= 2:
        anomalies.append("REPEATED_TOOL_FAILURE")
    if distress_detected and not relief_cited:
        anomalies.append("MISSING_STATUTORY_RELIEF_ON_HARDSHIP")

    flagged = bool(anomalies) or composite < 0.88

    scorecard = InteractionQualityScorecard(
        scorecard_id=f"qa-{uuid.uuid4().hex[:12]}",
        audit_event_id=audit_event_id,
        session_id=session_id,
        timestamp_utc=now_utc,
        locale=locale or "en",
        channel=channel or "rest",
        statutory_accuracy_score=round(statutory_score, 4),
        figure_fidelity_score=round(figure_score, 4),
        policy_adherence_score=round(policy_score, 4),
        language_and_empathy_score=round(empathy_score, 4),
        resolution_completeness_score=round(completeness_score, 4),
        handoff_cleanliness_score=(
            round(handoff_completeness_ratio, 4)
            if handoff_completeness_ratio is not None
            else None
        ),
        composite_quality_score=composite,
        anomaly_flags=anomalies,
        flagged_for_human_review=flagged,
        autonomy_throttle_triggered=False,
    )

    throttled = quality_circuit_breaker.record(scorecard)
    if throttled and flags.is_enabled("quality_circuit_breaker", subject=subject):
        scorecard.autonomy_throttle_triggered = True

    return scorecard


# ---------------------------------------------------------------------------
# Ledger Append & Decision Receipt Generation
# ---------------------------------------------------------------------------

def _hash_taxpayer_identifier(raw_id: str, tenant_id: str = "default") -> str:
    if not raw_id:
        return ""
    salt = (os.getenv("AUDIT_TAXPAYER_HMAC_SALT") or os.getenv("AUTH_DEV_SECRET") or "ura-audit-salt").encode()
    msg = f"{tenant_id}:{raw_id.strip()}".encode()
    return hmac.new(salt, msg, hashlib.sha256).hexdigest()


def append_action_audit_v3(
    *,
    session_id: str,
    tool_or_action_name: str,
    intent_detected: str,
    intent_confidence: float = 1.0,
    agent_role: str = "tax_specialist",
    decision_trace: list[str] | None = None,
    parameters_redacted: dict[str, Any] | None = None,
    response_summary_redacted: dict[str, Any] | None = None,
    execution_status: Literal[
        "success", "error", "timeout", "blocked", "simulated_sandbox", "proposed"
    ] = "success",
    execution_latency_ms: float = 0.0,
    channel: str = "rest",
    user_id: str = "",
    tenant_id: str = "default",
    caller_confirmed: bool = False,
    officer_approved: bool = False,
    approving_officer_id: str = "",
    ticket_id: str | None = None,
    guardrails: GuardrailEvaluationTrace | None = None,
    pre_state: dict[str, Any] | None = None,
    statutory_basis: list[str] | None = None,
) -> tuple[ActionAuditRecord, DecisionReceipt]:
    """Construct, risk-gate, and append an ActionAuditRecord v3 to the tamper-evident AuditLedger."""
    now_utc_dt = dt.datetime.now(_UTC_TZ)
    now_eat_dt = now_utc_dt.astimezone(_EAT_TZ)

    guardrail_trace = guardrails or GuardrailEvaluationTrace()
    guardrails_ok = (
        guardrail_trace.prompt_injection_passed
        and guardrail_trace.tool_allowlist_passed
        and guardrail_trace.schema_validation_passed
        and guardrail_trace.figure_fidelity_passed
    )

    outcome, approval_rec = evaluate_autonomy_gate(
        tool_or_action_name,
        intent_confidence=intent_confidence,
        caller_confirmed=caller_confirmed,
        officer_approved=officer_approved,
        approving_officer_id=approving_officer_id,
        ticket_id=ticket_id,
        guardrails_passed=guardrails_ok,
        subject=user_id or None,
    )
    tier = classify_action_risk(tool_or_action_name)
    rev_contract = get_reversibility_contract(tool_or_action_name, pre_state=pre_state)

    params_dict = dict(parameters_redacted or {})
    resp_dict = dict(response_summary_redacted or {})
    params_sha = hashlib.sha256(
        json.dumps(params_dict, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    resp_sha = hashlib.sha256(
        json.dumps(resp_dict, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()

    if outcome in ("proposed_awaiting_officer", "hard_blocked", "failed_closed"):
        res_status: Literal[
            "resolved", "awaiting_user_action", "escalated_to_officer", "abstained"
        ] = "escalated_to_officer" if outcome == "proposed_awaiting_officer" else "abstained"
    else:
        res_status = "resolved"

    statutes = list(statutory_basis or ["Income Tax Act Cap 340 / Tax Procedures Code Act (FY2026-27)"])
    explanation = (
        f"Action '{tool_or_action_name}' classified as {tier.upper()} risk "
        f"(intent='{intent_detected}', confidence={intent_confidence:.2f}) -> outcome '{outcome}'. "
        f"Statutory basis: {', '.join(statutes)}."
    )

    prov = provenance()
    try:
        prov["active_flag_variants"] = json.dumps(
            flags.logged_variants(subject=user_id or None), sort_keys=True
        )
    except Exception:
        prov["active_flag_variants"] = "{}"

    record = ActionAuditRecord(
        timestamp_utc=now_utc_dt.isoformat(),
        timestamp_eat=now_eat_dt.isoformat(),
        tenant_id=tenant_id or "default",
        session_id=session_id,
        channel=channel,
        taxpayer_tin_hmac=_hash_taxpayer_identifier(user_id, tenant_id or "default"),
        intent_detected=intent_detected,
        intent_confidence=round(float(intent_confidence), 4),
        agent_role=agent_role,
        decision_trace=list(decision_trace or [f"intent:{intent_detected}", f"tool:{tool_or_action_name}"]),
        human_readable_explanation=explanation,
        risk_tier=tier,
        tool_or_action_name=tool_or_action_name,
        parameters_redacted=params_dict,
        parameters_sha256=params_sha,
        response_summary_redacted=resp_dict,
        response_sha256=resp_sha,
        execution_status=(
            "blocked"
            if outcome in ("hard_blocked", "failed_closed")
            else ("proposed" if outcome == "proposed_awaiting_officer" else execution_status)
        ),
        execution_latency_ms=round(float(execution_latency_ms), 2),
        guardrails=guardrail_trace,
        approval_gate=approval_rec,
        reversibility=rev_contract,
        provenance=prov,
        autonomy_outcome=outcome,
        resolution_status=res_status,
    )

    if flags.is_enabled("action_level_audit_v3", subject=user_id or None) and flags.is_enabled("audit_ledger"):
        try:
            from . import get_ledger

            evt = get_ledger().append(
                event_type="agent_action_v3",
                payload=record.model_dump(),
                tenant_id=str(tenant_id or "default")[:128],
                user_id=str(user_id or session_id or "")[:128],
            )
            record.audit_event_id = evt.event_id
            record.prev_row_hash = evt.prev_hash
            record.row_hash = evt.row_hash
        except Exception:
            logger.warning("Failed to append ActionAuditRecord v3 to AuditLedger", exc_info=True)

    receipt = DecisionReceipt(
        audit_event_id=record.audit_event_id or "unanchored",
        timestamp_eat=record.timestamp_eat,
        risk_tier=record.risk_tier,
        autonomy_outcome=record.autonomy_outcome,
        policy_version=POLICY_VERSION,
        statutory_basis=statutes,
        verified_inputs=sorted(str(k) for k in params_dict),
        explanation=record.human_readable_explanation,
        reversibility_note=rev_contract.compensation_tool_or_procedure,
    )
    return record, receipt

"""October 2026 Agentic Journey Copilot — 7 Customer-Experience Pillars.

Implements verified October 2026 standards and research for guided tax journeys:

1. **Pre-Flight Prerequisite DAG & Readiness Gate** (*TATA*, CMC 2026; *arXiv:2608.22266*):
   Evaluates statutory, document, and cross-MDA dependencies before or during a
   workflow so taxpayers never stall mid-form due to missing prerequisites.
2. **Declarative A2UI Form Co-Completion Surface** (Google *A2UI* / *AG-UI* 2026;
   OECD *No-Stop Shop*):
   Projects workflow slots into a structured, PII-masked form schedule preview
   mapped to official URA return/application boxes.
3. **Plan-Centric "Journey GPS" & Pushdown Goal Stack** (*Plover*, Potts & Sudhof
   *arXiv:2607.15193*, July 2026; *Goals as First-Class Abstractions*, AutomationXP 2026):
   Maintains a pushdown stack of parent/child goals and a persistent GPS state
   across multi-turn side-question digressions.
4. **Proactive Objective Inference & Confirmation Cards** (*Clarify User Expertise*,
   *arXiv:2608.22266*, Aug 2026):
   Maps natural-language taxpayer challenges into explicit URA objective
   hypotheses with 1-tap guided-mode confirmation actions.
5. **Just-In-Time Step-Up Authentication & Zero-Data-Loss Session Elevation**
   (IETF RFC 9470; W3C WCAG 2.2 AA SC 3.3.7 *Redundant Entry* & SC 3.3.8):
   Allows anonymous discovery and slot collection first, then triggers inline
   login/registration or access recovery while preserving 100% of entered slots.
6. **Multimodal "Snap-to-Fill" Document-to-Slot Grounding & Stall Scaffolding**:
   Binds analysed OCR/document facts directly into workflow slots and emits
   progressive field examples and helper actions when validation fails.
7. **Cross-Channel Resume Token**:
   Generates a deterministic, PII-free resume code (`URA-<FLOW>-S<N>-<HASH>`)
   for continuity across Web, Voice, and Officer Desk.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .loader import WorkflowDefinition, WorkflowStep
    from .registry import WorkflowSession, WorkflowTurn

GUIDE_PREFIX = "Guide me step by step through "


@dataclass(frozen=True)
class PrerequisiteSpec:
    """A single statutory or operational prerequisite in a workflow's readiness DAG."""

    id: str
    label: str
    slot: str = ""
    remediation_workflow: str = ""
    remediation_workflow_name: str = ""
    required_when: str = ""


@dataclass(frozen=True)
class FormFieldSpec:
    """Declarative A2UI form box mapping for a workflow slot."""

    box: str
    label: str
    slot: str


#: Curated prerequisite DAG per workflow id (Pillar 1).
WORKFLOW_PREREQUISITE_DAG: dict[str, tuple[PrerequisiteSpec, ...]] = {
    "return_filing": (
        PrerequisiteSpec(
            id="active_tin",
            label="Active URA TIN",
            slot="taxpayer_type",
            remediation_workflow="tin_registration",
            remediation_workflow_name="TIN Registration Guide",
        ),
        PrerequisiteSpec(
            id="supporting_schedules",
            label="Supporting schedules, EFRIS invoices, or payroll ledgers",
            slot="supporting_records_ready",
            remediation_workflow="audit_invoice_compliance",
            remediation_workflow_name="Tax Invoice & EFRIS Document Review",
        ),
        PrerequisiteSpec(
            id="portal_credentials",
            label="URA e-Tax portal sign-in access",
            slot="portal_access",
            remediation_workflow="tin_procedure_help",
            remediation_workflow_name="TIN & Portal Access Recovery",
        ),
    ),
    "tin_registration": (
        PrerequisiteSpec(
            id="applicant_category",
            label="Applicant entity classification (Individual, Company, or NGO)",
            slot="taxpayer_type",
        ),
        PrerequisiteSpec(
            id="identity_or_ursb_docs",
            label="Original NIRA National ID / Passport or URSB Incorporation Certificate",
            slot="documents_ready",
            remediation_workflow="mda_ntr_resolution",
            remediation_workflow_name="Cross-MDA & NTR Verification Guide",
        ),
    ),
    "payment_assistance": (
        PrerequisiteSpec(
            id="tax_head_identified",
            label="Tax head or liability type identified",
            slot="tax_type",
        ),
        PrerequisiteSpec(
            id="prn_or_assessment_ref",
            label="Payment Registration Number (PRN) or return reference",
            slot="reference_kind",
            remediation_workflow="return_filing",
            remediation_workflow_name="Return Filing",
        ),
        PrerequisiteSpec(
            id="confirmed_amount",
            label="Verified UGX amount due",
            slot="amount_known",
            remediation_workflow="calc_vat",
            remediation_workflow_name="VAT Calculator",
        ),
    ),
    "tax_clearance": (
        PrerequisiteSpec(
            id="active_tin_tcc",
            label="Active URA TIN & portal account",
            slot="tcc_purpose",
            remediation_workflow="tin_registration",
            remediation_workflow_name="TIN Registration Guide",
        ),
        PrerequisiteSpec(
            id="returns_up_to_date",
            label="All required tax returns filed to date",
            slot="compliance_status",
            remediation_workflow="return_filing",
            remediation_workflow_name="Return Filing",
        ),
    ),
    "customs_clearance": (
        PrerequisiteSpec(
            id="commercial_invoice_packing_list",
            label="Commercial invoice, bill of lading & packing list",
            slot="clearance_stage",
            remediation_workflow="calc_customs_duty",
            remediation_workflow_name="Customs Duty Calculator",
        ),
    ),
    "objection_or_dispute": (
        PrerequisiteSpec(
            id="assessment_notice",
            label="Official URA assessment notice & ground of objection (within 45 days)",
            slot="dispute_stage",
        ),
    ),
}

#: Declarative A2UI form box mappings for guided co-completion (Pillar 2).
WORKFLOW_FORM_SCHEMAS: dict[str, tuple[str, str, tuple[FormFieldSpec, ...]]] = {
    "return_filing": (
        "URA-ETAX-RET-01",
        "URA Return Filing Readiness Schedule",
        (
            FormFieldSpec("Box A1", "Taxpayer Category", "taxpayer_type"),
            FormFieldSpec("Box A2", "Tax Head / Return Type", "tax_type"),
            FormFieldSpec("Box B1", "Return Filing Period", "filing_period"),
            FormFieldSpec("Box C1", "Supporting Schedules Verified", "supporting_records_ready"),
            FormFieldSpec("Box D1", "e-Tax Portal Access Confirmed", "portal_access"),
        ),
    ),
    "tin_registration": (
        "URA-REG-DT-101",
        "URA TIN Application Preparation Checklist",
        (
            FormFieldSpec("Sec 1", "Applicant Classification", "taxpayer_type"),
            FormFieldSpec("Sec 2", "Identity / URSB Documents Ready", "documents_ready"),
        ),
    ),
    "payment_assistance": (
        "URA-PRN-PAY-01",
        "URA PRN Payment Preparation Slip",
        (
            FormFieldSpec("Line 1", "Tax Head / Obligation", "tax_type"),
            FormFieldSpec("Line 2", "Reference Basis (PRN / Return / Assessment)", "reference_kind"),
            FormFieldSpec("Line 3", "Liability Amount Confirmed", "amount_known"),
            FormFieldSpec("Line 4", "Expected Payment Amount (UGX)", "payment_amount"),
            FormFieldSpec("Line 5", "Selected Payment Channel", "payment_channel"),
        ),
    ),
}

#: Field-level stall scaffolding hints and examples when validation fails (Pillar 6).
_SLOT_SCAFFOLDING: dict[str, dict[str, Any]] = {
    "taxpayer_type": {
        "hint": "Choose whether this applies to one person ('individual'), a registered business ('company'), or a non-profit ('ngo').",
        "examples": ["individual", "company", "ngo"],
        "helper_actions": ["individual", "company", "ngo"],
    },
    "tax_type": {
        "hint": "Name the URA tax head for this return or payment (for example: VAT, PAYE, corporation tax, withholding tax, or stamp duty).",
        "examples": ["vat", "paye", "corporation tax", "withholding tax"],
        "helper_actions": ["vat", "paye", "corporation tax", "withholding tax"],
    },
    "filing_period": {
        "hint": "State the month, quarter, or financial year of the return (for example: 'September 2026' or 'FY2025-26').",
        "examples": ["September 2026", "Q3 2026", "FY2025-26"],
        "helper_actions": ["September 2026", "FY2025-26"],
    },
    "supporting_records_ready": {
        "hint": "Reply 'yes' if your invoices, payroll, or ledgers are ready, or 'no' if you still need to prepare them.",
        "examples": ["yes", "no"],
        "helper_actions": ["yes", "no"],
    },
    "portal_access": {
        "hint": "Reply 'yes' if you can sign in toura.go.ug right now, or 'no' if you forgot your password or cannot log in.",
        "examples": ["yes", "no"],
        "helper_actions": ["yes", "no"],
    },
    "documents_ready": {
        "hint": "Reply 'yes' if you have your NIRA National ID, Passport, or URSB registration certificate on hand, or 'no' if not yet.",
        "examples": ["yes", "no"],
        "helper_actions": ["yes", "no"],
    },
    "reference_kind": {
        "hint": "Select what reference you have: 'prn' (13-digit Payment Registration Number), 'return', 'assessment', or 'unknown'.",
        "examples": ["prn", "return", "assessment", "unknown"],
        "helper_actions": ["prn", "return", "assessment", "unknown"],
    },
    "payment_channel": {
        "hint": "Choose how you want to pay URA: 'mobile money' (MTN/Airtel), 'bank', 'online card', or 'other'.",
        "examples": ["mobile money", "bank", "online card"],
        "helper_actions": ["mobile money", "bank", "online card"],
    },
}

#: Natural-language situation patterns mapped to URA objective hypotheses (Pillar 4).
_OBJECTIVE_PATTERNS: tuple[tuple[re.Pattern[str], dict[str, str]], ...] = (
    (
        re.compile(
            r"\b(?:forgot\s+(?:my\s+)?(?:etax|ura|tin|portal)\s+password|cannot\s+log\s*in\s+to\s+(?:ura|etax|portal)|"
            r"locked\s+out\s+of\s+(?:ura|etax|portal)|recover\s+(?:my\s+)?(?:tin|portal)\s+account|"
            r"lost\s+(?:my\s+)?tin\s+(?:password|email|phone))\b",
            re.IGNORECASE,
        ),
        {
            "objective_id": "portal_access_recovery",
            "title": "Recover URA e-Tax Portal Access & TIN Credentials",
            "domain": "tin_registration",
            "workflow_id": "tin_procedure_help",
            "workflow_name": "TIN Registration & Account Help",
            "prerequisites_summary": "Registered TIN or NIRA NIN and active email/phone for OTP verification",
        },
    ),
    (
        re.compile(
            r"(?:\bdeducted\s+6\s*%|\bwithheld\s+6\s*%|\bclient\s+withheld\b|"
            r"\bpaid\s+(?:me\s+)?less\s+than\s+(?:my\s+)?invoice\b|"
            r"\bwht\s+credit\b|\bwithholding\s+tax\s+certificate\b)",
            re.IGNORECASE,
        ),
        {
            "objective_id": "wht_credit_and_filing",
            "title": "Verify 6% Withholding Tax Deduction & Claim Tax Credit",
            "domain": "returns",
            "workflow_id": "return_filing",
            "workflow_name": "Return Filing",
            "prerequisites_summary": "WHT Certificate from withholding agent, EFRIS invoice, and active e-Tax portal access",
        },
    ),
    (
        re.compile(
            r"\b(?:bid\s+for\s+a\s+tender|government\s+contract\s+clearance|apply\s+for\s+(?:a\s+)?tcc|"
            r"need\s+(?:a\s+)?tax\s+clearance|clearance\s+certificate\s+for\s+bidding)\b",
            re.IGNORECASE,
        ),
        {
            "objective_id": "tax_clearance_application",
            "title": "Apply for a URA Tax Clearance Certificate (TCC)",
            "domain": "tax_clearance",
            "workflow_id": "tax_clearance",
            "workflow_name": "Tax Clearance Certificate (TCC) Guide",
            "prerequisites_summary": "Active TIN, all periodic returns filed, and no unsettled tax arrears (or active payment plan)",
        },
    ),
    (
        re.compile(
            r"\b(?:goods\s+(?:stuck|held|detained)\s+at\s+(?:malaba|busia|entebbe|mombasa|customs|border)|"
            r"clear\s+(?:my\s+)?(?:container|shipment|cargo|import)|customs\s+release\s+order)\b",
            re.IGNORECASE,
        ),
        {
            "objective_id": "customs_cargo_clearance",
            "title": "Clear Imported Goods & Resolve Customs Hold (ASYCUDA / Single Window)",
            "domain": "customs",
            "workflow_id": "customs_clearance",
            "workflow_name": "Customs Clearance & Import Guide",
            "prerequisites_summary": "Commercial invoice, packing list, bill of lading/airway bill, and licensed clearing agent",
        },
    ),
    (
        re.compile(
            r"\b(?:disagree\s+with\s+(?:an?\s+)?assessment|unfair\s+tax\s+assessment|challenge\s+(?:a\s+)?ura\s+penalty|"
            r"lodge\s+an\s+objection|appeal\s+tax\s+decision)\b",
            re.IGNORECASE,
        ),
        {
            "objective_id": "tax_objection_dispute",
            "title": "Lodge a Formal Tax Objection Within the 45-Day Statutory Window",
            "domain": "objection",
            "workflow_id": "objection_or_dispute",
            "workflow_name": "Tax Objection & Dispute Guide",
            "prerequisites_summary": "Assessment reference number, written grounds of objection, and 30% payment of tax not in dispute (where applicable)",
        },
    ),
    (
        re.compile(
            r"\b(?:transfer\s+(?:car|vehicle)\s+(?:ownership|logbook)|bought\s+a\s+used\s+car|"
            r"first\s+time\s+vehicle\s+registration|number\s+plate\s+registration)\b",
            re.IGNORECASE,
        ),
        {
            "objective_id": "motor_vehicle_transfer",
            "title": "Complete Motor Vehicle Registration or Logbook Ownership Transfer",
            "domain": "motor_vehicle",
            "workflow_id": "motor_vehicle_registration",
            "workflow_name": "Motor Vehicle Registration & Transfer Guide",
            "prerequisites_summary": "Both buyer and seller TINs, original logbook, transfer form, and stamp duty / transfer fee PRN",
        },
    ),
    (
        re.compile(
            r"\b(?:opening\s+a\s+(?:shop|business|company|hardware|store)|starting\s+a\s+(?:new\s+)?business|"
            r"register\s+my\s+new\s+business\s+for\s+tax|first\s+time\s+taxpayer)\b",
            re.IGNORECASE,
        ),
        {
            "objective_id": "new_business_tax_onboarding",
            "title": "Onboard a New Business (TIN Registration, Presumptive/VAT Threshold & EFRIS)",
            "domain": "tin_registration",
            "workflow_id": "tin_registration",
            "workflow_name": "TIN Registration Guide",
            "prerequisites_summary": "NIRA National ID (for sole proprietors) or URSB Certificate of Incorporation (for companies)",
        },
    ),
)


def generate_resume_code(workflow_id: str, step_idx: int, slots: Mapping[str, Any]) -> str:
    """Return a deterministic, PII-free short token to resume a workflow across channels (Pillar 7).

    Hashes only the workflow id, step index, and *names* (never values) of filled
    slots so no personal or financial data can ever be inferred from the token.
    """
    prefix = re.sub(r"[^a-zA-Z0-9]", "", (workflow_id or "GEN")[:3]).upper() or "GEN"
    filled_keys = ",".join(sorted(k for k in slots if not k.startswith("_") and slots.get(k) not in ("", None)))
    digest = hashlib.sha256(f"{workflow_id}:{step_idx}:{filled_keys}".encode()).hexdigest()[:4].upper()
    return f"URA-{prefix}-S{step_idx + 1}-{digest}"


def evaluate_prerequisites(
    workflow_id: str,
    slots: Mapping[str, Any],
    *,
    case_facts: Any | None = None,
) -> list[dict[str, Any]]:
    """Evaluate the pre-flight and in-flight Prerequisite DAG for *workflow_id* (Pillar 1)."""
    specs = WORKFLOW_PREREQUISITE_DAG.get(workflow_id, ())
    if not specs:
        return []

    out: list[dict[str, Any]] = []
    for spec in specs:
        raw_val = slots.get(spec.slot) if spec.slot else None
        if raw_val in ("", None) and case_facts is not None and spec.slot:
            raw_val = getattr(case_facts, spec.slot, None)

        if raw_val is False or str(raw_val).strip().lower() in ("false", "no", "unknown"):
            status = "missing"
        elif raw_val not in ("", None):
            status = "satisfied"
        else:
            status = "pending"

        item: dict[str, Any] = {
            "id": spec.id,
            "label": spec.label,
            "status": status,
        }
        if spec.slot:
            item["slot"] = spec.slot
        if spec.remediation_workflow and status == "missing":
            item["remediation_workflow"] = spec.remediation_workflow
            if spec.remediation_workflow_name:
                item["remediation_action"] = f"{GUIDE_PREFIX}{spec.remediation_workflow_name}"
        out.append(item)
    return out


def build_form_draft_surface(
    workflow_id: str,
    workflow_name: str,
    slots: Mapping[str, Any],
    all_steps: list[dict[str, Any]],
    *,
    sensitive_slots: Set[str] = frozenset(),
) -> dict[str, Any]:
    """Build a declarative A2UI form co-completion surface without leaking sensitive values (Pillar 2)."""
    schema = WORKFLOW_FORM_SCHEMAS.get(workflow_id)
    fields: list[dict[str, Any]] = []

    if schema is not None:
        form_code, title, field_specs = schema
        for fspec in field_specs:
            val = slots.get(fspec.slot)
            is_filled = val not in ("", None)
            is_masked = fspec.slot in sensitive_slots
            if not is_filled:
                display_val = "Pending"
                status = "pending"
            elif is_masked:
                display_val = "•••• (Masked)"
                status = "filled"
            elif isinstance(val, bool):
                display_val = "Confirmed" if val else "Action required"
                status = "filled" if val else "attention_needed"
            else:
                display_val = "Recorded"
                status = "filled"
            fields.append(
                {
                    "box": fspec.box,
                    "label": fspec.label,
                    "slot": fspec.slot,
                    "status": status,
                    "display_value": display_val,
                    "masked": is_masked,
                }
            )
    else:
        form_code = f"URA-WF-{workflow_id.upper()[:8]}"
        title = f"{workflow_name} Preparation Checklist"
        for idx, st in enumerate(all_steps, start=1):
            st_status = st.get("status", "pending")
            fields.append(
                {
                    "box": f"Step {idx}",
                    "label": str(st.get("title") or st.get("id") or f"Step {idx}"),
                    "slot": str(st.get("id") or f"step_{idx}"),
                    "status": "filled" if st_status == "completed" else "pending",
                    "display_value": "Completed" if st_status == "completed" else "Pending",
                    "masked": False,
                }
            )

    total_fields = len(fields)
    filled_count = sum(1 for f in fields if f["status"] == "filled")
    completion_pct = round((filled_count / total_fields) * 100) if total_fields > 0 else 0
    attention_needed = any(f["status"] == "attention_needed" for f in fields)

    return {
        "protocol": "a2ui/v1",
        "surface_id": f"ura-form-{workflow_id}",
        "form_code": form_code,
        "title": title,
        "completion_pct": completion_pct,
        "filled_count": filled_count,
        "total_fields": total_fields,
        "ready_for_portal": filled_count == total_fields and total_fields > 0 and not attention_needed,
        "fields": fields,
    }


def evaluate_step_up_auth(
    wf: WorkflowDefinition,
    step: WorkflowStep | None,
    slots: Mapping[str, Any],
    *,
    is_authenticated: bool = False,
) -> dict[str, Any] | None:
    """Return an inline Step-Up Auth / Registration descriptor when portal access is needed (Pillar 5).

    Honours W3C WCAG 2.2 AA SC 3.3.7 (Redundant Entry) by guaranteeing that
    all filled slots in the anonymous session are preserved across login or
    registration handoff.
    """
    filled_non_internal = [k for k in slots if not k.startswith("_") and slots.get(k) not in ("", None)]
    portal_blocked = slots.get("portal_access") is False or str(slots.get("portal_access", "")).lower() == "false"
    docs_missing = slots.get("documents_ready") is False or str(slots.get("documents_ready", "")).lower() == "false"
    has_portal_action = bool(step and step.portal_action)

    if portal_blocked:
        return {
            "required": True,
            "mode": "recover_access",
            "reason": "URA e-Tax portal access is required to submit this declaration.",
            "login_url": "https://ura.go.ug",
            "remediation_workflow_id": "tin_procedure_help",
            "remediation_action": f"{GUIDE_PREFIX}TIN Registration & Account Help",
            "preserves_slots": True,
            "preserved_slot_count": len(filled_non_internal),
        }
    if docs_missing and wf.id == "tin_registration":
        return {
            "required": True,
            "mode": "prepare_prerequisites",
            "reason": "Original identification (NIRA NIN/Passport) or URSB registration documents are required before portal submission.",
            "login_url": "https://ura.go.ug",
            "remediation_workflow_id": "mda_ntr_resolution",
            "remediation_action": f"{GUIDE_PREFIX}Cross-MDA & NTR Verification Guide",
            "preserves_slots": True,
            "preserved_slot_count": len(filled_non_internal),
        }
    if (wf.requires_auth or has_portal_action) and not is_authenticated:
        return {
            "required": bool(wf.requires_auth),
            "mode": "login_or_register",
            "reason": "Sign in to your URA e-Tax account (or register for a TIN) to complete submission on the official portal.",
            "login_url": (step.portal_action.get("url") if step and step.portal_action else None)
            or "https://ura.go.ug",
            "register_workflow_id": "tin_registration",
            "register_action": f"{GUIDE_PREFIX}TIN Registration Guide",
            "preserves_slots": True,
            "preserved_slot_count": len(filled_non_internal),
        }
    return None


def elevate_anonymous_workflow_session(
    session: WorkflowSession,
    *,
    user_id: str,
    profile_slots: Mapping[str, Any] | None = None,
) -> WorkflowSession:
    """Elevate an anonymous WorkflowSession to an authenticated user with zero slot loss (WCAG 3.3.7).

    Existing answers given by the taxpayer during anonymous discovery always
    take precedence over generic profile defaults so nothing the taxpayer
    typed is overwritten or re-asked.
    """
    session.slots["_authenticated_user_id"] = user_id
    if profile_slots:
        for key, value in profile_slots.items():
            if value not in ("", None) and session.slots.get(key) in ("", None):
                session.slots[key] = value
    return session


def build_stall_scaffolding(
    workflow_id: str,
    step_id: str,
    slot_name: str,
    validator: str,
    failed_attempts: int,
    options: list[str] | None = None,
) -> dict[str, Any] | None:
    """Return progressive field-level examples and 1-tap helpers when a user stalls (Pillar 6)."""
    if failed_attempts <= 0:
        return None

    spec = _SLOT_SCAFFOLDING.get(slot_name, {})
    hint = str(
        spec.get("hint")
        or (
            f"Please select one of the listed options: {', '.join(options)}."
            if options
            else "Please provide a valid value for this step, or tap 'Cancel workflow' to exit."
        )
    )
    examples = list(spec.get("examples") or (options[:3] if options else []))
    helper_actions = list(spec.get("helper_actions") or (options[:4] if options else []))
    if failed_attempts >= 2 and "Talk to an officer" not in helper_actions:
        helper_actions.append("Talk to an officer")

    return {
        "step_id": step_id,
        "slot": slot_name,
        "failed_attempts": failed_attempts,
        "level": "escalated_assist" if failed_attempts >= 2 else "inline_example",
        "hint": hint,
        "examples": examples,
        "helper_actions": helper_actions,
    }


def bind_document_facts_to_workflow(
    session: WorkflowSession,
    doc_facts: Mapping[str, Any] | None,
) -> list[str]:
    """Auto-populate unfilled workflow slots from analysed OCR/document metadata (Pillar 6 'Snap-to-Fill').

    Returns the list of slot names that were pre-filled from the document.
    Never overwrites a slot the taxpayer has already answered directly.
    """
    if not doc_facts or not isinstance(doc_facts, Mapping):
        return []

    candidate_map: dict[str, Any] = {}
    doc_type = str(doc_facts.get("doc_type") or doc_facts.get("document_type") or "").lower()
    if any(k in doc_type for k in ("invoice", "efris", "receipt", "payroll", "ledger")):
        candidate_map["supporting_records_ready"] = True
    if any(k in doc_type for k in ("id", "nin", "passport", "ursb", "certificate")):
        candidate_map["documents_ready"] = True

    for slot_key in ("taxpayer_type", "tax_type", "filing_period", "reference_kind", "payment_channel"):
        raw = doc_facts.get(slot_key)
        if isinstance(raw, str) and raw.strip():
            candidate_map[slot_key] = raw.strip().lower()

    bound: list[str] = []
    for slot_name, val in candidate_map.items():
        if session.slots.get(slot_name) in ("", None):
            session.slots[slot_name] = val
            bound.append(slot_name)

    if bound:
        existing = list(session.slots.get("_snap_to_fill_fields") or [])
        for s in bound:
            if s not in existing:
                existing.append(s)
        session.slots["_snap_to_fill_fields"] = existing
    return bound


def build_journey_gps(
    wf: WorkflowDefinition,
    session: WorkflowSession,
    turn: WorkflowTurn | None,
    *,
    step_index: int,
    total_steps: int,
) -> dict[str, Any]:
    """Build the persistent Plan-Centric 'Journey GPS' state (*Plover* 2026, Pillar 3)."""
    completed_steps = max(0, step_index - 1) if not session.completed else total_steps
    completion_pct = round((completed_steps / total_steps) * 100) if total_steps > 0 else 0
    is_tangent = bool(turn and turn.is_tangent)
    parked_prompt = (turn.parked_step_prompt if turn and turn.parked_step_prompt else "") or (
        turn.question if turn and not is_tangent else ""
    )

    return {
        "workflow_id": wf.id,
        "active_goal": wf.name,
        "step_index": step_index,
        "total_steps": total_steps,
        "completion_pct": 100 if session.completed else completion_pct,
        "is_paused_for_tangent": is_tangent,
        "tangent_count": int(session.slots.get("_tangent_count") or 0),
        "parked_step_prompt": parked_prompt,
        "resume_action": f"Continue {wf.name}",
        "resume_code": generate_resume_code(wf.id, session.current_step_idx, session.slots),
        "parent_goals": [
            {"workflow_id": g.get("workflow_id", ""), "step_idx": g.get("step_idx", 0)}
            for g in getattr(session, "goal_stack", ())
        ],
    }


def infer_taxpayer_objective(
    message: str,
    rewritten: str = "",
) -> dict[str, Any] | None:
    """Infer a structured URA objective hypothesis from a situational problem description (Pillar 4)."""
    combined = f"{message or ''} {rewritten or ''}".strip()
    if not combined:
        return None

    for pattern, meta in _OBJECTIVE_PATTERNS:
        if pattern.search(combined):
            wf_name = meta["workflow_name"]
            return {
                "objective_id": meta["objective_id"],
                "title": meta["title"],
                "domain": meta["domain"],
                "confidence": 0.92,
                "workflow_id": meta["workflow_id"],
                "workflow_name": wf_name,
                "confirm_action": f"{GUIDE_PREFIX}{wf_name}",
                "prerequisites_summary": meta["prerequisites_summary"],
            }
    return None

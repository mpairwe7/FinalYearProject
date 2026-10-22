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
            remediation_workflow="tin_registration",
            remediation_workflow_name="TIN Registration Guide",
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
            remediation_workflow_name="Cross-MDA NTR & PRN Resolution",
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
            remediation_workflow="return_filing",
            remediation_workflow_name="Return Filing",
        ),
    ),
    "tax_clearance": (
        PrerequisiteSpec(
            id="active_tin_tcc",
            label="Applicant classification & active URA TIN",
            slot="taxpayer_type",
            remediation_workflow="tin_registration",
            remediation_workflow_name="TIN Registration Guide",
        ),
        PrerequisiteSpec(
            id="returns_up_to_date",
            label="All required tax returns filed to date",
            slot="returns_filed",
            remediation_workflow="return_filing",
            remediation_workflow_name="Return Filing",
        ),
        PrerequisiteSpec(
            id="tax_due_settled",
            label="All tax due paid or covered by URA instalment agreement",
            slot="tax_paid",
            remediation_workflow="payment_assistance",
            remediation_workflow_name="Payment Assistance",
        ),
    ),
    "customs_clearance": (
        PrerequisiteSpec(
            id="customs_valuation_evidence",
            label="Customs value, commercial invoice & supporting valuation details",
            slot="valuation_ready",
        ),
        PrerequisiteSpec(
            id="customs_supporting_documents",
            label="Supporting customs declaration, bill of lading & packing list",
            slot="documents_ready",
        ),
    ),
    "objection_or_dispute": (
        PrerequisiteSpec(
            id="objection_supporting_docs",
            label="Supporting assessment notices, correspondence & schedules",
            slot="documents_ready",
        ),
        PrerequisiteSpec(
            id="objection_45_day_window",
            label="Within the 45-day statutory objection deadline on the notice",
            slot="within_deadline",
        ),
    ),
    "motor_vehicle_registration": (
        PrerequisiteSpec(
            id="vehicle_registration_kind",
            label="Registration category (First registration or Digital number plate)",
            slot="registration_kind",
        ),
        PrerequisiteSpec(
            id="vehicle_owner_tin",
            label="Active URA TIN in the vehicle owner's name",
            slot="has_tin",
            remediation_workflow="tin_registration",
            remediation_workflow_name="TIN Registration Guide",
        ),
    ),
    "audit_invoice_compliance": (
        PrerequisiteSpec(
            id="supplier_tin_on_invoice",
            label="Supplier TIN visible on invoice",
            slot="supplier_tin_present",
            remediation_workflow="tin_registration",
            remediation_workflow_name="TIN Registration Guide",
        ),
        PrerequisiteSpec(
            id="efris_fdn_verification_code",
            label="18-digit EFRIS Fiscal Document Number (FDN) & QR/verification code",
            slot="has_efris_code",
        ),
    ),
    "mda_ntr_resolution": (
        PrerequisiteSpec(
            id="mda_service_selected",
            label="Target government MDA service identified",
            slot="mda_service",
        ),
        PrerequisiteSpec(
            id="ntr_prn_available",
            label="13-digit URA Non-Tax Revenue (NTR) PRN available",
            slot="has_prn",
            remediation_workflow="payment_assistance",
            remediation_workflow_name="Payment Assistance",
        ),
    ),
    "bwims_warehouse_guidance": (
        PrerequisiteSpec(
            id="bwims_request_goal",
            label="Bonded warehouse / BWIMS enquiry category identified",
            slot="request_goal",
        ),
        PrerequisiteSpec(
            id="bwims_operator_role",
            label="Customs stakeholder role (Importer, Clearing Agent, or Operator)",
            slot="user_role",
        ),
    ),
    "new_taxpayer_and_relief": (
        PrerequisiteSpec(
            id="onboarding_relief_need",
            label="Primary compliance or penalty-relief area selected",
            slot="taxpayer_need",
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
    "tax_clearance": (
        "URA-TCC-APP-01",
        "URA Tax Clearance Certificate (TCC) Readiness Schedule",
        (
            FormFieldSpec("Sec A", "Applicant Category", "taxpayer_type"),
            FormFieldSpec("Sec B", "TCC Certificate Type", "certificate_type"),
            FormFieldSpec("Sec C", "All Periodic Returns Filed", "returns_filed"),
            FormFieldSpec("Sec D", "Tax Due Settled / Instalment Plan", "tax_paid"),
        ),
    ),
    "customs_clearance": (
        "URA-ASY-CUS-01",
        "URA ASYCUDA Customs Clearance Schedule",
        (
            FormFieldSpec("Box 1", "Movement Type (Import / Export)", "movement_type"),
            FormFieldSpec("Box 2", "Customs Valuation Evidence Ready", "valuation_ready"),
            FormFieldSpec("Box 3", "Supporting Customs Documents Ready", "documents_ready"),
        ),
    ),
    "objection_or_dispute": (
        "URA-OBJ-TAT-01",
        "URA Statutory Objection Preparation Schedule",
        (
            FormFieldSpec("Sec 1", "Tax Head Under Objection", "tax_type"),
            FormFieldSpec("Sec 2", "Supporting Evidence Ready", "documents_ready"),
            FormFieldSpec("Sec 3", "Within Statutory 45-Day Window", "within_deadline"),
        ),
    ),
    "motor_vehicle_registration": (
        "URA-MVR-NIP-01",
        "URA Motor Vehicle & Digital Plate Schedule",
        (
            FormFieldSpec("Sec 1", "Registration Type", "registration_kind"),
            FormFieldSpec("Sec 2", "Owner URA TIN Confirmed", "has_tin"),
        ),
    ),
    "mda_ntr_resolution": (
        "URA-MDA-NTR-01",
        "Cross-MDA Non-Tax Revenue (NTR) Schedule",
        (
            FormFieldSpec("Line 1", "Government Agency / Service", "mda_service"),
            FormFieldSpec("Line 2", "Reported NTR / PRN Issue", "mda_issue_kind"),
            FormFieldSpec("Line 3", "13-Digit PRN Available", "has_prn"),
        ),
    ),
    "audit_invoice_compliance": (
        "URA-EFR-INV-01",
        "URA Tax Invoice & EFRIS Verification Schedule",
        (
            FormFieldSpec("Box 1", "Document Category", "doc_category"),
            FormFieldSpec("Box 2", "Supplier TIN Visible", "supplier_tin_present"),
            FormFieldSpec("Box 3", "Buyer TIN Visible", "buyer_tin_present"),
            FormFieldSpec("Box 4", "Net Chargeable Amount (UGX)", "net_amount"),
            FormFieldSpec("Box 5", "VAT Amount Shown (UGX)", "vat_amount"),
            FormFieldSpec("Box 6", "EFRIS FDN / QR Code Present", "has_efris_code"),
        ),
    ),
    "bwims_warehouse_guidance": (
        "URA-CUS-BWM-01",
        "URA Bonded Warehouse (BWIMS) Guidance Schedule",
        (
            FormFieldSpec("Sec 1", "Bonded Warehouse / BWIMS Goal", "request_goal"),
            FormFieldSpec("Sec 2", "Customs Stakeholder Role", "user_role"),
        ),
    ),
    "new_taxpayer_and_relief": (
        "URA-TPC-REL-01",
        "URA Taxpayer Onboarding & Statutory Relief Schedule",
        (
            FormFieldSpec("Sec 1", "Compliance or Relief Need", "taxpayer_need"),
            FormFieldSpec("Sec 2", "Audit Notice / Assessment Status", "has_audit_notice"),
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
            "workflow_id": "tin_registration",
            "workflow_name": "TIN Registration Guide",
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
            "workflow_name": "Tax Clearance Certificate",
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
            "workflow_name": "Customs Clearance",
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
            "workflow_name": "Objection or Dispute",
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
            "title": "Complete Motor Vehicle Registration or Digital Number Plate Fitment",
            "domain": "motor_vehicle",
            "workflow_id": "motor_vehicle_registration",
            "workflow_name": "Motor Vehicle Registration",
            "prerequisites_summary": "Active URA TIN in the vehicle owner's name, original logbook or import entry, and bank payment slip",
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


_RESUME_CODE_RE = re.compile(r"\bURA-([A-Z0-9]{2,4})-S(\d{1,2})-([A-F0-9]{4})\b", re.IGNORECASE)

#: One unique, stable resume-code prefix per workflow. Explicit rather than
#: derived from the id: the first three letters collide (``tin_procedure_help``
#: and ``tin_registration`` both gave ``TIN``; all nine calculators gave
#: ``CAL``, which no workflow mapped back to). The first eleven are the codes
#: already issued and must not change.
RESUME_CODE_PREFIXES: dict[str, str] = {
    "return_filing": "RET",
    "tin_registration": "TIN",
    "payment_assistance": "PAY",
    "tax_clearance": "TAX",
    "customs_clearance": "CUS",
    "objection_or_dispute": "OBJ",
    "motor_vehicle_registration": "MOT",
    "audit_invoice_compliance": "AUD",
    "mda_ntr_resolution": "MDA",
    "bwims_warehouse_guidance": "BWI",
    "new_taxpayer_and_relief": "NEW",
    "tin_procedure_help": "TPH",
    "calc_paye": "PYE",
    "calc_vat": "VAT",
    "calc_vat_registration": "VRG",
    "calc_withholding": "WHT",
    "calc_rental_tax": "RNT",
    "calc_corporation_tax": "CIT",
    "calc_capital_gains": "CGT",
    "calc_customs_duty": "CDE",
    "calc_excise_duty": "EXD",
}

_RESUME_PREFIX_TO_WORKFLOW: dict[str, str] = {code: wf for wf, code in RESUME_CODE_PREFIXES.items()}


def _resume_check(prefix: str, step_number: int) -> str:
    """Four-hex check over the visible part of a resume code.

    A typo or a made-up code-shaped string fails it, so it cannot pivot or
    cancel the flow a taxpayer is in. It is an integrity check, not an
    authenticator: a code carries no authority beyond naming a public
    workflow, so it needs no secret and holds no slot names or values.
    """
    return hashlib.sha256(f"ura-resume:{prefix}:{step_number}".encode()).hexdigest()[:4].upper()


def generate_resume_code(workflow_id: str, step_idx: int) -> str:
    """Return the cross-channel resume token for *workflow_id* at *step_idx* (Pillar 7).

    Empty for a workflow without a registered prefix, so the UI shows no code
    rather than one that cannot be parsed back.
    """
    prefix = RESUME_CODE_PREFIXES.get(workflow_id or "")
    if prefix is None:
        return ""
    step_number = max(0, int(step_idx)) + 1
    return f"URA-{prefix}-S{step_number}-{_resume_check(prefix, step_number)}"


def looks_like_resume_code(text: str) -> bool:
    """Whether *text* holds something shaped like a resume code, verified or not.

    A free-text step accepts anything, so without this a mistyped code would
    be stored as the answer (e.g. as the filing period).
    """
    return bool(text) and _RESUME_CODE_RE.search(text) is not None


def parse_resume_code(text: str) -> dict[str, Any] | None:
    """Parse and verify a cross-channel resume token (e.g. ``URA-RET-S2-…``) in *text* (Pillar 7).

    Returns ``None`` for an unknown prefix or a check that does not match, so
    only codes this service issued are routed.
    """
    if not text:
        return None
    for m in _RESUME_CODE_RE.finditer(text):
        prefix = m.group(1).upper()
        step_num = int(m.group(2))
        digest = m.group(3).upper()
        workflow_id = _RESUME_PREFIX_TO_WORKFLOW.get(prefix)
        if workflow_id is None or step_num < 1 or digest != _resume_check(prefix, step_num):
            continue
        return {
            "code": f"URA-{prefix}-S{step_num}-{digest}",
            "prefix": prefix,
            "workflow_id": workflow_id,
            "step_number": step_num,
            "step_idx": step_num - 1,
            "digest": digest,
        }
    return None


def is_prerequisite_remediation(parent_workflow_id: str, target_workflow_id: str) -> bool:
    """Return True if *target_workflow_id* is a declared prerequisite remediation for *parent_workflow_id*."""
    if not parent_workflow_id or not target_workflow_id or parent_workflow_id == target_workflow_id:
        return False
    specs = WORKFLOW_PREREQUISITE_DAG.get(parent_workflow_id, ())
    return any(s.remediation_workflow == target_workflow_id for s in specs)


#: Every slot a prerequisite reads; the only case facts a session may keep.
_PREREQUISITE_SLOTS: frozenset[str] = frozenset(
    spec.slot for specs in WORKFLOW_PREREQUISITE_DAG.values() for spec in specs if spec.slot
)


def prerequisite_case_facts(case_facts: Any | None) -> dict[str, Any]:
    """The part of *case_facts* that a prerequisite in the DAG can read.

    The workflow session is persisted with every turn, so storing whole
    ``CaseFacts`` there would copy salary, turnover and residency figures
    into durable storage for nothing (data minimisation). Accepts a
    ``CaseFacts``-like object with ``to_dict()`` or a mapping.
    """
    if case_facts is None:
        return {}
    if isinstance(case_facts, Mapping):
        source: Mapping[str, Any] = case_facts
    elif callable(getattr(case_facts, "to_dict", None)):
        raw = case_facts.to_dict()
        source = raw if isinstance(raw, Mapping) else {}
    else:
        return {}
    return {
        key: source[key]
        for key in sorted(_PREREQUISITE_SLOTS)
        if source.get(key) not in ("", None)
    }


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
            if isinstance(case_facts, Mapping):
                raw_val = case_facts.get(spec.slot)
            else:
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
            "remediation_workflow_id": "tin_registration",
            "remediation_action": f"{GUIDE_PREFIX}TIN Registration Guide",
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
            "remediation_action": f"{GUIDE_PREFIX}Cross-MDA NTR & PRN Resolution",
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
    doc_facts: Any | None,
) -> list[str]:
    """Auto-populate unfilled workflow slots from analysed OCR/document metadata (Pillar 6 'Snap-to-Fill').

    Accepts either a ``Mapping`` of document facts or a ``DocumentRecord``
    instance. Returns the list of slot names that were pre-filled from the
    document. Never overwrites a slot the taxpayer has already answered directly.
    """
    if doc_facts is None:
        return []

    if isinstance(doc_facts, Mapping):
        raw_map: Mapping[str, Any] = doc_facts
        doc_type = str(raw_map.get("doc_type") or raw_map.get("document_type") or "").lower()
        ocr_fields: Mapping[str, Any] = (
            raw_map.get("fields") if isinstance(raw_map.get("fields"), Mapping) else {}
        )
    else:
        doc_type = str(getattr(doc_facts, "doc_type", "") or "").lower()
        raw_fields = getattr(doc_facts, "fields", None)
        ocr_fields = raw_fields if isinstance(raw_fields, Mapping) else {}
        raw_map = ocr_fields

    if not doc_type and not raw_map and not ocr_fields:
        return []

    doc_tokens = {t for t in re.split(r"[^a-z0-9]+", doc_type) if t}
    candidate_map: dict[str, Any] = {}
    if doc_tokens & {"invoice", "efris", "receipt", "payroll", "ledger", "filing", "form", "assessment"}:
        candidate_map["supporting_records_ready"] = True
    if doc_tokens & {"id", "nin", "passport", "ursb", "certificate", "tin", "customs"}:
        candidate_map["documents_ready"] = True

    ocr_suggestions: dict[str, Any] = dict(session.slots.get("_ocr_suggestions") or {})
    if ocr_fields.get("efris_invoice_numbers"):
        ocr_suggestions["has_efris_code"] = True
    if ocr_fields.get("tins"):
        ocr_suggestions["supplier_tin_present"] = True
        ocr_suggestions["has_tin"] = True
    if ocr_fields.get("prns"):
        ocr_suggestions["has_prn"] = True
        ocr_suggestions["reference_kind"] = "prn"
    if ocr_fields.get("ugx_amounts"):
        ocr_suggestions["amount_known"] = True
    if ocr_suggestions:
        session.slots["_ocr_suggestions"] = ocr_suggestions

    tax_heads = ocr_fields.get("tax_heads")
    if isinstance(tax_heads, list) and tax_heads:
        first_head = str(tax_heads[0] or "").strip().lower()
        if first_head:
            candidate_map.setdefault("tax_type", first_head)

    for slot_key in ("taxpayer_type", "tax_type", "filing_period", "reference_kind", "payment_channel"):
        raw = raw_map.get(slot_key)
        if isinstance(raw, str) and raw.strip():
            candidate_map[slot_key] = raw.strip().lower()

    # Validate candidate slot values against the workflow's step validators when registered
    from .registry import WorkflowRegistry
    from .slots import validate_slot

    wf_def = WorkflowRegistry.get(session.workflow_id)
    step_validators: dict[str, str] = {}
    if wf_def is not None:
        for st in wf_def.steps:
            if st.slot and st.validator:
                step_validators[st.slot] = st.validator

    bound: list[str] = []
    for slot_name, val in candidate_map.items():
        if session.slots.get(slot_name) not in ("", None):
            continue
        validator = step_validators.get(slot_name, "")
        if validator and isinstance(val, str):
            ok, norm_val, _ = validate_slot(val, validator)
            if not ok:
                continue
            val = norm_val
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
        "resume_code": generate_resume_code(wf.id, session.current_step_idx),
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

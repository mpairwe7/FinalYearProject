"""Phase 42 — URA Portal Task Execution, Claude-Style Action Confirmation, Inline Chat Elicitation & Inbuilt Stuck-User DOM Co-Pilot.

Implements:
- 4-Tier Privacy, Statutory & Security Classification Matrix (`PortalTaskTier`, `PORTAL_TASK_CATALOG`)
- MCP 2026-07-28 Form-Mode Elicitation (`card_type="inline_form"`)
- Claude-Style Human-in-the-Loop Action Confirmation (`card_type="confirmation_required"`) signed with `mrtr.wrap_request_state` and bound to owner-scoped state keys (`ADR-0013`)
- Step-Up OIDC Authentication with `returnTo` continuation (`card_type="step_up_login"`, IETF RFC 9470 & WCAG 2.2 AA SC 3.3.7)
- TPCA Cap. 343 s. 75 (Official Secrecy) Subject-TIN Ownership enforcement & TPCA s. 16 Statutory Declaration boundary (`card_type="statutory_copilot_handoff"`)
- Live DOM Telemetry scrubbing and stuck-user recovery (`analyze_portal_context`)
"""

from __future__ import annotations

import enum
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import urllib.parse
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import database as db
from .mcp import mrtr
from .tools import ToolRegistry
from .tools.portal_navigator_data import (
    EXTERNAL_URA_PORTALS,
    export_companion_manifest,
    get_portal_dom_recipe,
    portal_requires_auth,
)

logger = logging.getLogger(__name__)


class PortalTaskTier(str, enum.Enum):
    """4-Tier Privacy, Statutory & Security Classification for URA Portal Tasks."""

    AUTONOMOUS_VERIFY = "AUTONOMOUS_VERIFY"
    DELEGATED_CONFIRM = "DELEGATED_CONFIRM"
    AUTHENTICATED_ACCOUNT_READ = "AUTHENTICATED_ACCOUNT_READ"
    STATUTORY_COPILOT_ONLY = "STATUTORY_COPILOT_ONLY"


class PortalTaskValidationError(ValueError):
    """Raised when `request_state` or `idempotency_key` is missing, malformed, or tampered (HTTP 400)."""


class PortalTaskOwnershipError(PermissionError):
    """Raised when `request_state` belongs to another session/user or violates TPCA s. 75 (HTTP 403)."""


@dataclass(frozen=True)
class PortalFieldSpec:
    """Declarative input control specification for inline chat form elicitation."""

    name: str
    label: str
    input_type: str = "text"  # "text" | "number" | "select" | "date" | "email" | "tel"
    required: bool = True
    placeholder: str = ""
    helper_text: str = ""
    options: tuple[dict[str, str], ...] = ()
    sensitive: bool = False
    default_value: str = ""

    def to_dict(self, *, value: Any = "", validation_error: str = "") -> dict[str, Any]:
        """Serialize this field specification with its current value and optional validation error."""
        val_str = str(value if value not in (None, "") else self.default_value)
        payload: dict[str, Any] = {
            "name": self.name,
            "label": self.label,
            "input_type": self.input_type,
            "required": self.required,
            "placeholder": self.placeholder,
            "helper_text": self.helper_text,
            "sensitive": self.sensitive,
            "value": val_str,
        }
        if self.options:
            payload["options"] = [dict(o) for o in self.options]
        if validation_error:
            payload["validation_error"] = validation_error
        return payload


@dataclass(frozen=True)
class PortalTaskSpec:
    """Catalog metadata and policy rules for a single URA portal task."""

    task_id: str
    tool_name: str
    portal_key: str
    title: str
    tier: PortalTaskTier
    requires_auth: bool
    required_consent: str | None
    fields: tuple[PortalFieldSpec, ...]
    verification_warning: str = (
        "Please carefully verify all information below before allowing the URA AI to execute this action on the portal."
    )
    statutory_citation: str = "Tax Procedures Code Act (TPCA) Cap. 343"
    description: str = ""


@dataclass
class PortalTaskIntent:
    """Detected portal task intent and any pre-extracted entities from user message/context."""

    task_id: str
    portal_key: str
    confidence: float
    extracted_fields: dict[str, Any] = field(default_factory=dict)
    is_guidance_or_portal_link_request: bool = False


@dataclass
class PortalCopilotOutcome:
    """Result of evaluating a chat turn for portal task execution or stuck-user assistance."""

    card_type: str
    payload: dict[str, Any]
    grounding_block: str
    deterministic_reply: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dictionary representation of this portal co-pilot outcome."""
        out = dict(self.payload)
        out["card_type"] = self.card_type
        return out


# ---------------------------------------------------------------------------
# Shared Field Option Catalogs
# ---------------------------------------------------------------------------

TAX_HEAD_OPTIONS: tuple[dict[str, str], ...] = (
    {"value": "VAT_STANDARD", "label": "Value Added Tax — VAT (0010)"},
    {"value": "CORPORATION_TAX", "label": "Income Tax / Corporation Tax (0011)"},
    {"value": "PAYE", "label": "Pay As You Earn — PAYE (0012)"},
    {"value": "WITHHOLDING_TAX", "label": "Withholding Tax — WHT (0013)"},
    {"value": "RENTAL_TAX", "label": "Rental Income Tax (0014)"},
    {"value": "LOCAL_EXCISE", "label": "Local Excise Duty (0020)"},
    {"value": "PASSPORT_FEE", "label": "Non-Tax Revenue / Passport / MDA Fee (0090)"},
)

PAYMENT_MODE_OPTIONS: tuple[dict[str, str], ...] = (
    {"value": "COMMERCIAL_BANK", "label": "Commercial Bank Counter / EFT (Stanbic, Centenary, Absa, DFCU)"},
    {"value": "MOBILE_MONEY", "label": "Mobile Money (*165# MTN MoMo / *185# Airtel Money)"},
    {"value": "VISA_MASTERCARD", "label": "Online Card Payment (VISA / MasterCard)"},
)

ENTITY_TYPE_OPTIONS: tuple[dict[str, str], ...] = (
    {"value": "NON_INDIVIDUAL_COMPANY", "label": "Private / Public Limited Company"},
    {"value": "PARTNERSHIP", "label": "Registered Partnership"},
    {"value": "TRUST_NGO", "label": "Trust / NGO / Association"},
)

DTS_PRODUCT_OPTIONS: tuple[dict[str, str], ...] = (
    {"value": "BOTTLED_WATER", "label": "Bottled Water (UGX 13 / stamp)"},
    {"value": "SODA", "label": "Non-Alcoholic Soda / Beverage (UGX 17 / stamp)"},
    {"value": "BEER", "label": "Beer (UGX 36 / stamp)"},
    {"value": "WINES", "label": "Wine (UGX 55 / stamp)"},
    {"value": "SPIRITS", "label": "Spirits (UGX 55 / stamp)"},
    {"value": "TOBACCO", "label": "Tobacco / Cigarettes (UGX 75 / stamp)"},
    {"value": "CEMENT", "label": "Cement (UGX 135 / stamp)"},
    {"value": "SUGAR", "label": "Sugar (UGX 100 / stamp)"},
    {"value": "COOKING_OIL", "label": "Cooking Oil (UGX 30 / stamp)"},
)

TCC_PURPOSE_OPTIONS: tuple[dict[str, str], ...] = (
    {"value": "BIDDING_TENDER", "label": "Public Procurement Bidding / Government Tender"},
    {"value": "LICENSE_RENEWAL", "label": "Trade / Regulatory License Renewal"},
    {"value": "WORK_PERMIT", "label": "Immigration / Work Permit Application"},
    {"value": "VEHICLE_TRANSFER", "label": "Motor Vehicle Ownership Transfer"},
)


# ---------------------------------------------------------------------------
# 4-Tier Portal Task Catalog
# ---------------------------------------------------------------------------

PORTAL_TASK_CATALOG: dict[str, PortalTaskSpec] = {
    # ── TIER 1: AUTONOMOUS_VERIFY (Public Read-Only Verification) ─────────
    "tin_search_verify": PortalTaskSpec(
        task_id="tin_search_verify",
        tool_name="tin_search_verify",
        portal_key="tin_registration",
        title="Search & Verify Taxpayer Identification Number (TIN)",
        tier=PortalTaskTier.AUTONOMOUS_VERIFY,
        requires_auth=False,
        required_consent=None,
        statutory_citation="TPCA Cap. 343 s. 4–5 (Taxpayer Registration)",
        description="Verify an active 10-digit URA TIN, 14-character NIN, or URSB registration number.",
        fields=(
            PortalFieldSpec(
                name="tin",
                label="10-Digit TIN, 14-Character NIN, or Registered Name",
                input_type="text",
                required=True,
                placeholder="100-XXX-XXXX",
                helper_text="Enter the 10-digit URA TIN (e.g. 100-XXX-XXXX) or 14-character NIRA NIN.",
            ),
        ),
    ),
    "payment_view_status": PortalTaskSpec(
        task_id="payment_view_status",
        tool_name="payment_view_status",
        portal_key="prn_payments",
        title="Verify PRN Payment & Bank Clearance Status",
        tier=PortalTaskTier.AUTONOMOUS_VERIFY,
        requires_auth=False,
        required_consent=None,
        statutory_citation="TPCA Cap. 343 s. 37 (Tax Payment & Receipts)",
        description="Check whether a Payment Registration Number (PRN) is pending, expired, or bank-cleared.",
        fields=(
            PortalFieldSpec(
                name="prn",
                label="Payment Registration Number (PRN)",
                input_type="text",
                required=True,
                placeholder="226000001001",
                helper_text="Enter your 10 to 13-digit URA PRN (e.g. 226000001001).",
            ),
        ),
    ),
    "dts_verify_stamp": PortalTaskSpec(
        task_id="dts_verify_stamp",
        tool_name="dts_verify_stamp",
        portal_key="dts",
        title="Verify Digital Tax Stamp (Kakasa Authenticity Check)",
        tier=PortalTaskTier.AUTONOMOUS_VERIFY,
        requires_auth=False,
        required_consent=None,
        statutory_citation="Tax Procedures Code (Tax Stamps) Regulations",
        description="Verify a physical or digital excise stamp serial code.",
        fields=(
            PortalFieldSpec(
                name="stamp_serial",
                label="Digital Tax Stamp Serial / QR Code",
                input_type="text",
                required=True,
                placeholder="UG-DTS-2026-000101",
                helper_text="Enter the Kakasa stamp serial printed on the product seal (e.g. UG-DTS-2026-000101).",
            ),
        ),
    ),
    "ursb_verify_business": PortalTaskSpec(
        task_id="ursb_verify_business",
        tool_name="ursb_verify_business",
        portal_key="tin_registration",
        title="Verify URSB Business Registration (BRN)",
        tier=PortalTaskTier.AUTONOMOUS_VERIFY,
        requires_auth=False,
        required_consent=None,
        statutory_citation="Companies Act & URSB-URA Integration Framework",
        description="Look up company incorporation status in the URSB registry.",
        fields=(
            PortalFieldSpec(
                name="ursb_brn",
                label="URSB Registration Number (BRN) or Company Name",
                input_type="text",
                required=True,
                placeholder="80020001234567",
                helper_text="Enter the URSB Business Registration Number or exact registered entity name.",
            ),
        ),
    ),
    "payment_verify_advance_tax": PortalTaskSpec(
        task_id="payment_verify_advance_tax",
        tool_name="payment_verify_advance_tax",
        portal_key="prn_payments",
        title="Verify Commercial Motor Vehicle Advance Income Tax",
        tier=PortalTaskTier.AUTONOMOUS_VERIFY,
        requires_auth=False,
        required_consent=None,
        statutory_citation="Income Tax Act Cap. 338 (Advance Tax on Commercial Vehicles)",
        description="Verify PSV or goods vehicle advance income tax compliance prior to TLB route licensing.",
        fields=(
            PortalFieldSpec(
                name="vehicle_registration_number",
                label="Motor Vehicle Registration Plate",
                input_type="text",
                required=True,
                placeholder="UBK 412A",
                helper_text="Enter the Ugandan vehicle registration number (e.g. UBK 412A).",
            ),
        ),
    ),
    # ── TIER 2: DELEGATED_CONFIRM (Write / Draft with Claude-Style Allow/Disallow) ──
    "payment_generate_prn": PortalTaskSpec(
        task_id="payment_generate_prn",
        tool_name="payment_generate_prn",
        portal_key="prn_payments",
        title="Generate Payment Registration Number (PRN)",
        tier=PortalTaskTier.DELEGATED_CONFIRM,
        requires_auth=False,
        required_consent="ura_account_access",
        statutory_citation="TPCA Cap. 343 s. 37 (Payment Registration Slip)",
        description="Generate a PRN payment voucher for tax or government NTR/MDA liabilities.",
        fields=(
            PortalFieldSpec(
                name="tin",
                label="10-Digit Taxpayer TIN",
                input_type="text",
                required=True,
                placeholder="100-XXX-XXXX",
                helper_text="Your 10-digit URA Taxpayer Identification Number.",
            ),
            PortalFieldSpec(
                name="tax_head",
                label="Tax Head / Revenue Type",
                input_type="select",
                required=True,
                options=TAX_HEAD_OPTIONS,
                default_value="VAT_STANDARD",
                helper_text="Select the tax regime or NTR fee category.",
            ),
            PortalFieldSpec(
                name="amount_ugx",
                label="Payment Amount (UGX)",
                input_type="number",
                required=True,
                placeholder="2500000",
                helper_text="Exact amount to pay in Uganda Shillings (numeric, > 0).",
            ),
            PortalFieldSpec(
                name="payment_mode",
                label="Payment Mode / Channel",
                input_type="select",
                required=True,
                options=PAYMENT_MODE_OPTIONS,
                default_value="COMMERCIAL_BANK",
                helper_text="Select Commercial Bank, Mobile Money (*165# / *185#), or VISA/MasterCard.",
            ),
            PortalFieldSpec(
                name="taxpayer_name",
                label="Taxpayer Legal Name (Optional — auto-resolved if omitted)",
                input_type="text",
                required=False,
                placeholder="Kampala Traders Ltd",
                default_value="Registered Taxpayer",
                helper_text="Full name of the paying individual or company.",
            ),
        ),
    ),
    "payment_reactivate_prn": PortalTaskSpec(
        task_id="payment_reactivate_prn",
        tool_name="payment_reactivate_prn",
        portal_key="prn_payments",
        title="Reactivate Expired Payment Registration Number (PRN)",
        tier=PortalTaskTier.DELEGATED_CONFIRM,
        requires_auth=False,
        required_consent="ura_account_access",
        statutory_citation="TPCA Cap. 343 s. 37 (PRN Validity Extension)",
        description="Extend the 21-day validity window on an expired unpaid PRN.",
        fields=(
            PortalFieldSpec(
                name="prn",
                label="Expired PRN Number",
                input_type="text",
                required=True,
                placeholder="226000001003",
                helper_text="Enter the 10 to 13-digit expired PRN you wish to reactivate.",
            ),
        ),
    ),
    "tin_apply_individual": PortalTaskSpec(
        task_id="tin_apply_individual",
        tool_name="tin_apply_individual",
        portal_key="tin_registration",
        title="Apply for Instant Individual TIN (NIRA NIN Integration)",
        tier=PortalTaskTier.DELEGATED_CONFIRM,
        requires_auth=False,
        required_consent="ura_account_access",
        statutory_citation="TPCA Cap. 343 s. 4 (Application for TIN)",
        description="Submit an individual TIN registration application using your NIRA National ID.",
        fields=(
            PortalFieldSpec(
                name="nin",
                label="14-Character National ID Number (NIN)",
                input_type="text",
                required=True,
                placeholder="CM-14-CHAR-NIN",
                helper_text="Enter your 14-character NIN exactly as printed on your National ID card.",
                sensitive=True,
            ),
            PortalFieldSpec(
                name="surname",
                label="Surname (Family Name)",
                input_type="text",
                required=True,
                placeholder="Mukasa",
                helper_text="Surname as registered with NIRA.",
            ),
            PortalFieldSpec(
                name="given_name",
                label="Given Name (First Name)",
                input_type="text",
                required=True,
                placeholder="Sarah",
                helper_text="Given name as registered with NIRA.",
            ),
            PortalFieldSpec(
                name="date_of_birth",
                label="Date of Birth",
                input_type="date",
                required=True,
                placeholder="1992-05-14",
                default_value="1992-01-01",
                helper_text="Format: YYYY-MM-DD.",
                sensitive=True,
            ),
            PortalFieldSpec(
                name="mobile_number",
                label="Mobile Telephone Number",
                input_type="tel",
                required=True,
                placeholder="+256772123456",
                helper_text="Active Ugandan mobile number for OTP and TIN notifications.",
                sensitive=True,
            ),
            PortalFieldSpec(
                name="email",
                label="Email Address",
                input_type="email",
                required=True,
                placeholder="sarah.mukasa@example.ug",
                helper_text="Email address to receive your TIN certificate.",
                sensitive=True,
            ),
            PortalFieldSpec(
                name="district",
                label="District of Residence",
                input_type="text",
                required=False,
                placeholder="Kampala",
                default_value="Kampala",
                helper_text="Current residential or business district.",
            ),
        ),
    ),
    "tin_apply_non_individual": PortalTaskSpec(
        task_id="tin_apply_non_individual",
        tool_name="tin_apply_non_individual",
        portal_key="tin_registration",
        title="Apply for Non-Individual / Corporate TIN (URSB Linked)",
        tier=PortalTaskTier.DELEGATED_CONFIRM,
        requires_auth=False,
        required_consent="ura_account_access",
        statutory_citation="TPCA Cap. 343 s. 4 & Companies Act",
        description="Register a corporate, partnership, or NGO TIN linked to URSB incorporation records.",
        fields=(
            PortalFieldSpec(
                name="ursb_brn",
                label="URSB Registration Number (BRN)",
                input_type="text",
                required=True,
                placeholder="80020001234567",
                helper_text="Official Certificate of Incorporation / BRN from URSB.",
            ),
            PortalFieldSpec(
                name="legal_name",
                label="Registered Business / Entity Legal Name",
                input_type="text",
                required=True,
                placeholder="Nile Agro Processors Ltd",
                helper_text="Exact legal name on the URSB Certificate of Incorporation.",
            ),
            PortalFieldSpec(
                name="entity_type",
                label="Entity Legal Category",
                input_type="select",
                required=True,
                options=ENTITY_TYPE_OPTIONS,
                default_value="NON_INDIVIDUAL_COMPANY",
                helper_text="Select Company, Partnership, or Trust/NGO.",
            ),
            PortalFieldSpec(
                name="mobile_number",
                label="Official Corporate Contact Phone",
                input_type="tel",
                required=True,
                placeholder="+256772123456",
                helper_text="Primary contact phone for corporate tax notifications.",
                sensitive=True,
            ),
            PortalFieldSpec(
                name="email",
                label="Official Corporate Email",
                input_type="email",
                required=True,
                placeholder="tax@nileagro.co.ug",
                helper_text="Official email address for corporate TIN issuance.",
                sensitive=True,
            ),
            PortalFieldSpec(
                name="district",
                label="Registered Office District",
                input_type="text",
                required=False,
                placeholder="Kampala",
                default_value="Kampala",
                helper_text="District where headquarters is located.",
            ),
        ),
    ),
    "dts_order_stamps": PortalTaskSpec(
        task_id="dts_order_stamps",
        tool_name="dts_order_stamps",
        portal_key="dts",
        title="Order Digital Tax Stamps (DTS)",
        tier=PortalTaskTier.DELEGATED_CONFIRM,
        requires_auth=True,
        required_consent="ura_account_access",
        statutory_citation="Excise Duty Act & Tax Stamps Regulations",
        description="Submit an order for Digital Tax Stamps and generate the corresponding stamp fee PRN.",
        fields=(
            PortalFieldSpec(
                name="tin",
                label="Manufacturer / Importer 10-Digit TIN",
                input_type="text",
                required=True,
                placeholder="100-XXX-XXXX",
                helper_text="Registered 10-digit URA TIN.",
            ),
            PortalFieldSpec(
                name="product_category",
                label="Gazetted Excise Product Category",
                input_type="select",
                required=True,
                options=DTS_PRODUCT_OPTIONS,
                default_value="BOTTLED_WATER",
                helper_text="Select the gazetted commodity requiring digital tax stamps.",
            ),
            PortalFieldSpec(
                name="quantity",
                label="Stamp Quantity to Order",
                input_type="number",
                required=True,
                placeholder="10000",
                helper_text="Total number of stamps to order (e.g. 10000).",
            ),
        ),
    ),
    "dts_activate_stamps": PortalTaskSpec(
        task_id="dts_activate_stamps",
        tool_name="dts_activate_stamps",
        portal_key="dts",
        title="Activate Digital Tax Stamp Batch on Production Line",
        tier=PortalTaskTier.DELEGATED_CONFIRM,
        requires_auth=True,
        required_consent="ura_account_access",
        statutory_citation="Tax Procedures Code (Tax Stamps) Regulations",
        description="Activate ordered stamp reels on an accredited factory packaging line.",
        fields=(
            PortalFieldSpec(
                name="order_id",
                label="DTS Stamp Order ID",
                input_type="text",
                required=True,
                placeholder="DTS-ORD-2026-001",
                helper_text="Order reference number issued when stamps were ordered.",
            ),
            PortalFieldSpec(
                name="line_id",
                label="Packaging Line Identifier",
                input_type="text",
                required=True,
                placeholder="LINE-KLA-01",
                default_value="LINE-KLA-01",
                helper_text="Accredited factory packaging line ID.",
            ),
            PortalFieldSpec(
                name="stamp_serial",
                label="Stamp Serial Number(s) (comma-separated)",
                input_type="text",
                required=True,
                placeholder="UG-DTS-2026-000105, UG-DTS-2026-000106",
                helper_text="Serial numbers of the stamp batch to commission.",
            ),
        ),
    ),
    "efris_stock_management": PortalTaskSpec(
        task_id="efris_stock_management",
        tool_name="efris_stock_management",
        portal_key="efris",
        title="Record EFRIS Inventory Stock-In",
        tier=PortalTaskTier.DELEGATED_CONFIRM,
        requires_auth=True,
        required_consent="ura_account_access",
        statutory_citation="TPCA Cap. 343 s. 73A (EFRIS Stock Management)",
        description="Add stock-in inventory for a commodity code in EFRIS.",
        fields=(
            PortalFieldSpec(
                name="tin",
                label="10-Digit Taxpayer TIN",
                input_type="text",
                required=True,
                placeholder="100-XXX-XXXX",
                helper_text="Registered seller 10-digit URA TIN.",
            ),
            PortalFieldSpec(
                name="commodity_code",
                label="UNSPSC Commodity Code",
                input_type="text",
                required=True,
                placeholder="50202301",
                helper_text="8-digit UNSPSC commodity classification code.",
            ),
            PortalFieldSpec(
                name="quantity",
                label="Quantity to Stock In",
                input_type="number",
                required=True,
                placeholder="500",
                helper_text="Positive quantity of units to add to EFRIS stock.",
            ),
            PortalFieldSpec(
                name="unit_cost",
                label="Unit Purchase Cost (UGX)",
                input_type="number",
                required=True,
                placeholder="1500",
                helper_text="Purchase cost per unit in UGX.",
            ),
        ),
    ),
    "ursb_register_business": PortalTaskSpec(
        task_id="ursb_register_business",
        tool_name="ursb_register_business",
        portal_key="tin_registration",
        title="Register Business Name / Entity with URSB",
        tier=PortalTaskTier.DELEGATED_CONFIRM,
        requires_auth=False,
        required_consent="ura_account_access",
        statutory_citation="Business Names Registration Act & Companies Act",
        description="Submit a business name or company registration application via the URSB-URA formalisation bridge.",
        fields=(
            PortalFieldSpec(
                name="legal_name",
                label="Proposed Business / Company Name",
                input_type="text",
                required=True,
                placeholder="Pearl Tech Solutions Ltd",
                helper_text="Proposed legal entity or business name.",
            ),
            PortalFieldSpec(
                name="nin",
                label="Primary Applicant / Director NIN",
                input_type="text",
                required=True,
                placeholder="CM-14-CHAR-NIN",
                helper_text="14-character NIRA NIN of the lead director or proprietor.",
                sensitive=True,
            ),
            PortalFieldSpec(
                name="district",
                label="Operational District",
                input_type="text",
                required=False,
                placeholder="Kampala",
                default_value="Kampala",
                helper_text="District of primary business operations.",
            ),
        ),
    ),
    # ── TIER 3: AUTHENTICATED_ACCOUNT_READ (Requires Login + TPCA s. 75 Secrecy) ──
    "ura_account_profile": PortalTaskSpec(
        task_id="ura_account_profile",
        tool_name="ura_account_profile",
        portal_key="e_services",
        title="View Authenticated Taxpayer Account Profile & Ledger Summary",
        tier=PortalTaskTier.AUTHENTICATED_ACCOUNT_READ,
        requires_auth=True,
        required_consent="ura_account_access",
        statutory_citation="TPCA Cap. 343 s. 75 (Official Secrecy)",
        description="Query your authenticated URA taxpayer account profile, registered tax heads, and balance.",
        fields=(
            PortalFieldSpec(
                name="tin",
                label="Your Authenticated 10-Digit TIN",
                input_type="text",
                required=True,
                placeholder="100-XXX-XXXX",
                helper_text="Must match your signed-in taxpayer profile under TPCA s. 75.",
            ),
        ),
    ),
    "tin_tax_obligations": PortalTaskSpec(
        task_id="tin_tax_obligations",
        tool_name="tin_tax_obligations",
        portal_key="e_services",
        title="Check or Register Taxpayer Filing Obligations & Deadlines",
        tier=PortalTaskTier.AUTHENTICATED_ACCOUNT_READ,
        requires_auth=True,
        required_consent="ura_account_access",
        statutory_citation="TPCA Cap. 343 s. 75 (Official Secrecy) & s. 16",
        description="Inspect or update registered tax head obligations on your authenticated TIN profile.",
        fields=(
            PortalFieldSpec(
                name="tin",
                label="Your Authenticated 10-Digit TIN",
                input_type="text",
                required=True,
                placeholder="100-XXX-XXXX",
                helper_text="Must match your signed-in taxpayer profile under TPCA s. 75.",
            ),
            PortalFieldSpec(
                name="tax_head",
                label="Tax Obligation Head",
                input_type="select",
                required=False,
                options=TAX_HEAD_OPTIONS,
                default_value="VAT_STANDARD",
                helper_text="Tax obligation head to check or activate.",
            ),
        ),
    ),
    "efris_taxpayer_status": PortalTaskSpec(
        task_id="efris_taxpayer_status",
        tool_name="efris_taxpayer_status",
        portal_key="efris",
        title="Check EFRIS Enrollment & Fiscal Device Readiness",
        tier=PortalTaskTier.AUTHENTICATED_ACCOUNT_READ,
        requires_auth=True,
        required_consent="ura_account_access",
        statutory_citation="TPCA Cap. 343 s. 73A & s. 75 (Official Secrecy)",
        description="Inspect your EFRIS enrollment status, fiscal devices, and offline sync compliance.",
        fields=(
            PortalFieldSpec(
                name="tin",
                label="Your Authenticated 10-Digit TIN",
                input_type="text",
                required=True,
                placeholder="100-XXX-XXXX",
                helper_text="Must match your signed-in taxpayer TIN.",
            ),
        ),
    ),
    "dts_taxpayer_status": PortalTaskSpec(
        task_id="dts_taxpayer_status",
        tool_name="dts_taxpayer_status",
        portal_key="dts",
        title="Check Digital Tax Stamps (DTS) Account & Quota Status",
        tier=PortalTaskTier.AUTHENTICATED_ACCOUNT_READ,
        requires_auth=True,
        required_consent="ura_account_access",
        statutory_citation="TPCA Cap. 343 s. 75 (Official Secrecy)",
        description="Inspect your DTS manufacturer/importer registration and packaging line status.",
        fields=(
            PortalFieldSpec(
                name="tin",
                label="Your Authenticated 10-Digit TIN",
                input_type="text",
                required=True,
                placeholder="100-XXX-XXXX",
                helper_text="Must match your signed-in taxpayer TIN.",
            ),
        ),
    ),
    "ursb_compliance_status": PortalTaskSpec(
        task_id="ursb_compliance_status",
        tool_name="ursb_compliance_status",
        portal_key="tin_registration",
        title="Check URSB Annual Return & TIN Readiness Status",
        tier=PortalTaskTier.AUTHENTICATED_ACCOUNT_READ,
        requires_auth=True,
        required_consent="ura_account_access",
        statutory_citation="Companies Act & TPCA Cap. 343 s. 75",
        description="Check URSB annual return compliance for your registered company.",
        fields=(
            PortalFieldSpec(
                name="ursb_brn",
                label="URSB Registration Number (BRN)",
                input_type="text",
                required=True,
                placeholder="URSB-CO-10001",
                helper_text="Enter your company's URSB registration number.",
            ),
        ),
    ),
    "bwims_consignment_status": PortalTaskSpec(
        task_id="bwims_consignment_status",
        tool_name="bwims_consignment_status",
        portal_key="bwims",
        title="Check Bonded Warehouse Consignment Status (BWIMS IM7)",
        tier=PortalTaskTier.AUTHENTICATED_ACCOUNT_READ,
        requires_auth=True,
        required_consent="ura_account_access",
        statutory_citation="EACCMA 2004 s. 42 & TPCA s. 75",
        description="Check storage days and overstay status for a customs bonded warehouse entry.",
        fields=(
            PortalFieldSpec(
                name="entry_number",
                label="Customs IM7 Warehousing Entry Number",
                input_type="text",
                required=True,
                placeholder="2026-ASY-IM7-88912",
                helper_text="Enter the ASYCUDA IM7 entry reference.",
            ),
        ),
    ),
    "bwims_warehouse_inventory": PortalTaskSpec(
        task_id="bwims_warehouse_inventory",
        tool_name="bwims_warehouse_inventory",
        portal_key="bwims",
        title="Check Customs Bonded Warehouse Inventory Balance",
        tier=PortalTaskTier.AUTHENTICATED_ACCOUNT_READ,
        requires_auth=True,
        required_consent="ura_account_access",
        statutory_citation="EACCMA 2004 s. 47–67 & TPCA s. 75",
        description="Inspect bonded consignments stored in a licensed customs warehouse.",
        fields=(
            PortalFieldSpec(
                name="warehouse_code",
                label="Licensed Customs Warehouse Code",
                input_type="text",
                required=True,
                placeholder="WH-KLA-001",
                default_value="WH-KLA-001",
                helper_text="Official warehouse code (e.g. WH-KLA-001).",
            ),
            PortalFieldSpec(
                name="tin",
                label="Importer 10-Digit TIN",
                input_type="text",
                required=False,
                placeholder="100-XXX-XXXX",
                helper_text="Your authenticated importer TIN.",
            ),
        ),
    ),
    # ── TIER 4: STATUTORY_COPILOT_ONLY (Stages A2UI + DOM Recipe; Taxpayer Final-Signs) ──
    "return_filing_submit": PortalTaskSpec(
        task_id="return_filing_submit",
        tool_name="ura_action_proposal",
        portal_key="return_filing",
        title="Stage Statutory Tax Return Filing (Co-Pilot Pre-Fill)",
        tier=PortalTaskTier.STATUTORY_COPILOT_ONLY,
        requires_auth=True,
        required_consent="ura_actions",
        statutory_citation="TPCA Cap. 343 s. 16 & s. 58–65 (Taxpayer Personal Declaration Liability)",
        description="Pre-populate your return schedule and portal fields up to the final statutory declaration.",
        fields=(
            PortalFieldSpec(
                name="tin",
                label="10-Digit Taxpayer TIN",
                input_type="text",
                required=False,
                placeholder="100-XXX-XXXX",
                helper_text="Your 10-digit URA TIN.",
            ),
            PortalFieldSpec(
                name="tax_head",
                label="Return Tax Type",
                input_type="select",
                required=False,
                options=TAX_HEAD_OPTIONS,
                default_value="VAT_STANDARD",
                helper_text="Select VAT, PAYE, or Income Tax return.",
            ),
            PortalFieldSpec(
                name="amount_ugx",
                label="Declared Net Tax Payable (UGX)",
                input_type="number",
                required=False,
                placeholder="1500000",
                helper_text="Net tax payable from your validated return schedule.",
            ),
        ),
    ),
    "objection_appeals_lodge": PortalTaskSpec(
        task_id="objection_appeals_lodge",
        tool_name="ura_action_proposal",
        portal_key="objection_appeals",
        title="Stage Formal Tax Assessment Objection (Section 24 TPCA Co-Pilot)",
        tier=PortalTaskTier.STATUTORY_COPILOT_ONLY,
        requires_auth=True,
        required_consent="ura_actions",
        statutory_citation="TPCA Cap. 343 s. 24 (45-Day Statutory Objection Window)",
        description="Pre-populate your Section 24 objection grounds and assessment reference on the portal.",
        fields=(
            PortalFieldSpec(
                name="assessment_ref",
                label="Assessment Registration Number (ARN)",
                input_type="text",
                required=False,
                placeholder="ASMT-2026-00912",
                helper_text="The assessment reference number being disputed.",
            ),
            PortalFieldSpec(
                name="grounds",
                label="Summary of Legal & Factual Grounds of Objection",
                input_type="text",
                required=False,
                placeholder="Input VAT invoices carry verified EFRIS FDNs under s. 28 VAT Act.",
                helper_text="Key grounds of objection to pre-fill on the portal form.",
            ),
        ),
    ),
    "tax_clearance_apply": PortalTaskSpec(
        task_id="tax_clearance_apply",
        tool_name="ura_action_proposal",
        portal_key="tax_clearance",
        title="Stage Tax Clearance Certificate (TCC) Application",
        tier=PortalTaskTier.STATUTORY_COPILOT_ONLY,
        requires_auth=True,
        required_consent="ura_actions",
        statutory_citation="TPCA Cap. 343 s. 43 (Tax Clearance Certificate Declaration)",
        description="Pre-populate your TCC application and run compliance pre-checks with the inbuilt AI Co-Pilot.",
        fields=(
            PortalFieldSpec(
                name="tin",
                label="10-Digit Taxpayer TIN",
                input_type="text",
                required=False,
                placeholder="100-XXX-XXXX",
                helper_text="Your 10-digit URA TIN.",
            ),
            PortalFieldSpec(
                name="tcc_purpose",
                label="TCC Statutory Purpose",
                input_type="select",
                required=False,
                options=TCC_PURPOSE_OPTIONS,
                default_value="BIDDING_TENDER",
                helper_text="Purpose for which the Tax Clearance Certificate is required.",
            ),
        ),
    ),
    "get_refund_claim": PortalTaskSpec(
        task_id="get_refund_claim",
        tool_name="ura_action_proposal",
        portal_key="get_refund",
        title="Stage Statutory Tax Refund Claim (Section 42 TPCA Co-Pilot)",
        tier=PortalTaskTier.STATUTORY_COPILOT_ONLY,
        requires_auth=True,
        required_consent="ura_actions",
        statutory_citation="TPCA Cap. 343 s. 42 & VAT Act s. 31 (Tax Refunds)",
        description="Pre-populate your tax refund claim and EFT bank verification steps on the portal.",
        fields=(
            PortalFieldSpec(
                name="tin",
                label="10-Digit Taxpayer TIN",
                input_type="text",
                required=False,
                placeholder="100-XXX-XXXX",
                helper_text="Your 10-digit URA TIN.",
            ),
            PortalFieldSpec(
                name="amount_ugx",
                label="Refund Claim Amount (UGX)",
                input_type="number",
                required=False,
                placeholder="5000000",
                helper_text="Amount of overpaid tax or input VAT credit claimed.",
            ),
        ),
    ),
    "efris_fiscal_invoice": PortalTaskSpec(
        task_id="efris_fiscal_invoice",
        tool_name="efris_fiscal_invoice",
        portal_key="efris",
        title="Stage EFRIS Fiscal Invoice Issuance (Co-Pilot Pre-Fill)",
        tier=PortalTaskTier.STATUTORY_COPILOT_ONLY,
        requires_auth=True,
        required_consent="ura_actions",
        statutory_citation="TPCA Cap. 343 s. 73A & s. 58 (False/Misleading Fiscal Receipt Liability)",
        description="Stage B2B/B2C fiscal invoice fields on the EFRIS portal for taxpayer final confirmation.",
        fields=(
            PortalFieldSpec(
                name="buyer_tin",
                label="Buyer 10-Digit TIN",
                input_type="text",
                required=False,
                placeholder="100-YYY-YYYY",
                helper_text="Purchasing business TIN.",
            ),
            PortalFieldSpec(
                name="commodity_code",
                label="UNSPSC Commodity Code",
                input_type="text",
                required=False,
                placeholder="50202301",
                helper_text="8-digit commodity classification code.",
            ),
        ),
    ),
    "efris_credit_note": PortalTaskSpec(
        task_id="efris_credit_note",
        tool_name="efris_credit_note",
        portal_key="efris",
        title="Stage EFRIS Credit Note Application (Co-Pilot Pre-Fill)",
        tier=PortalTaskTier.STATUTORY_COPILOT_ONLY,
        requires_auth=True,
        required_consent="ura_actions",
        statutory_citation="VAT Act Cap. 344 & TPCA s. 73A",
        description="Stage credit note adjustment fields against an original 20-digit FDN.",
        fields=(
            PortalFieldSpec(
                name="original_fdn",
                label="Original 20-Digit Fiscal Document Number (FDN)",
                input_type="text",
                required=False,
                placeholder="FDN-20-DIGITS",
                helper_text="20-digit FDN of the original EFRIS invoice.",
            ),
            PortalFieldSpec(
                name="amount_ugx",
                label="Adjusted Gross Amount (UGX)",
                input_type="number",
                required=False,
                placeholder="500000",
                helper_text="Gross amount to adjust.",
            ),
        ),
    ),
    "payment_checkout_settle": PortalTaskSpec(
        task_id="payment_checkout_settle",
        tool_name="payment_checkout_settle",
        portal_key="prn_payments",
        title="Stage Electronic PRN Payment Settlement",
        tier=PortalTaskTier.STATUTORY_COPILOT_ONLY,
        requires_auth=True,
        required_consent="ura_actions",
        statutory_citation="National Payment Systems Act & TPCA s. 37",
        description="Pre-fill your PRN and payment amount; taxpayer enters bank/MoMo PIN personally.",
        fields=(
            PortalFieldSpec(
                name="prn",
                label="12/13-Digit PRN to Settle",
                input_type="text",
                required=False,
                placeholder="226000001001",
                helper_text="Active Payment Registration Number.",
            ),
            PortalFieldSpec(
                name="amount_ugx",
                label="Amount to Settle (UGX)",
                input_type="number",
                required=False,
                placeholder="2500000",
                helper_text="Exact PRN amount in UGX.",
            ),
        ),
    ),
}


# Replay-safe bounded LRU idempotency & decline caches for Tier 2 / Tier 3 portal task executions,
# backed by the shared `workflow_sessions` database table across Uvicorn workers and guarded
# in-process by `_TASK_CACHE_LOCK` + `_IN_FLIGHT_TASK_KEYS` (OWASP ASI07 / LLM10).
_MAX_TASK_CACHE_ENTRIES = 2048
PORTAL_TASK_STATE_TTL_SECONDS = max(60, int(os.getenv("PORTAL_TASK_STATE_TTL_SECONDS", "900")))
_TASK_CACHE_LOCK = threading.Lock()
_EXECUTED_TASK_CACHE: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
_DECLINED_TASK_CACHE: OrderedDict[tuple[str, str], float] = OrderedDict()
_IN_FLIGHT_TASK_KEYS: set[tuple[str, str]] = set()
_SHARED_DECISION_KEYS: set[str] = set()
_CONVERSATION_ENTITY_MEMORY: OrderedDict[str, dict[str, Any]] = OrderedDict()


def _durable_task_cache_id(cache_key: tuple[str, str]) -> str:
    """Return a deterministic shared-store key for `(owner_key, idempotency_key)`."""
    digest = hashlib.sha256(f"{cache_key[0]}:{cache_key[1]}".encode()).hexdigest()[:40]
    return f"portal_task_decision:{digest}"


def _read_shared_task_decision(
    cache_key: tuple[str, str],
) -> tuple[str | None, dict[str, Any] | None]:
    """Read a previously executed or declined task decision from the shared database store."""
    try:
        row = db.get_workflow_session(_durable_task_cache_id(cache_key))
        if not row or row.get("workflow_id") != "portal_task_decision":
            return None, None
        updated_at = float(row.get("updated_at") or 0.0)
        if updated_at and (time.time() - updated_at) > PORTAL_TASK_STATE_TTL_SECONDS:
            return None, None
        status = str(row.get("status") or "")
        slots = row.get("slots")
        if status == "cancelled":
            return "declined", None
        if status == "completed" and isinstance(slots, dict) and slots:
            return "executed", dict(slots)
    except Exception:
        logger.debug("Shared portal task decision read skipped", exc_info=True)
    return None, None


def _write_shared_task_decision(
    cache_key: tuple[str, str],
    *,
    declined: bool,
    payload: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> None:
    """Persist an executed or declined task decision in the shared database store across workers."""
    try:
        cid = _durable_task_cache_id(cache_key)
        _SHARED_DECISION_KEYS.add(cid)
        db.upsert_workflow_session(
            cid,
            "portal_task_decision",
            1,
            dict(payload or {"declined": True}),
            status="cancelled" if declined else "completed",
            last_prompt=cache_key[1],
            user_id=str(user_id or ""),
        )
    except Exception:
        logger.debug("Shared portal task decision write skipped", exc_info=True)


def clear_portal_task_cache() -> None:
    """Clear in-memory and shared-store portal task idempotency caches (used in test fixtures)."""
    with _TASK_CACHE_LOCK:
        _EXECUTED_TASK_CACHE.clear()
        _DECLINED_TASK_CACHE.clear()
        _IN_FLIGHT_TASK_KEYS.clear()
        _CONVERSATION_ENTITY_MEMORY.clear()
        keys_to_reset = list(_SHARED_DECISION_KEYS)
        _SHARED_DECISION_KEYS.clear()
    for cid in keys_to_reset:
        try:
            db.upsert_workflow_session(cid, "portal_task_decision", 0, {}, status="active")
        except Exception:
            logger.debug("Shared portal task decision cleanup skipped", exc_info=True)


def classify_portal_task(task_id: str) -> PortalTaskSpec | None:
    """Return the `PortalTaskSpec` for *task_id*, or None if unknown."""
    return PORTAL_TASK_CATALOG.get(str(task_id or "").strip())


def mask_sensitive_value(field_name: str, value: Any) -> str:
    """Mask sensitive PII (NIN, phone, email, DOB) for display in confirmation cards."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    key = field_name.lower()
    if "nin" in key:
        if len(raw) >= 8:
            return f"{raw[:4]}{'*' * (len(raw) - 7)}{raw[-3:]}"
        return "****"
    if "mobile" in key or "phone" in key or "tel" in key:
        if len(raw) >= 7:
            return f"{raw[:5]}{'*' * (len(raw) - 8)}{raw[-3:]}"
        return "****"
    if "email" in key and "@" in raw:
        local, domain = raw.split("@", 1)
        if len(local) > 2:
            return f"{local[:2]}***{local[-1]}@{domain}"
        return f"***@{domain}"
    if "birth" in key or "dob" in key:
        if len(raw) >= 4:
            return f"{raw[:4]}-**-**"
        return "****-**-**"
    return raw


# ---------------------------------------------------------------------------
# Secret & Credential Scrubbing for DOM Telemetry (`portal_context`)
# ---------------------------------------------------------------------------

_SENSITIVE_FIELD_PATTERN = re.compile(
    r"(?:password|passwd|pwd|otp|pin|cvv|cvc|csc|secret|token|passcode|one[-_]?time[-_]?code|cc[-_]?csc)",
    re.IGNORECASE,
)


def sanitize_portal_context(portal_context: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a deep-sanitized copy of `portal_context` with all password/OTP/PIN/CVV fields stripped."""
    if not portal_context or not isinstance(portal_context, Mapping):
        return {}

    prohibited_selectors = set(
        export_companion_manifest().get("security_policy", {}).get("prohibited_selectors", [])
    )

    def _is_secret_key(k: str) -> bool:
        """Return True when field key or selector *k* names a secret or credential."""
        k_str = str(k or "").strip()
        if not k_str:
            return False
        if _SENSITIVE_FIELD_PATTERN.search(k_str):
            return True
        return any(sel.lower() in k_str.lower() for sel in prohibited_selectors)

    raw_visible = portal_context.get("visible_fields")
    clean_visible: dict[str, str] = {}
    scrubbed_keys: list[str] = []
    if isinstance(raw_visible, Mapping):
        for k, v in raw_visible.items():
            if _is_secret_key(str(k)):
                scrubbed_keys.append(str(k))
                continue
            clean_visible[str(k)] = str(v or "")[:240]

    raw_empty = portal_context.get("empty_required_fields")
    clean_empty: list[str] = []
    if isinstance(raw_empty, Sequence) and not isinstance(raw_empty, str | bytes):
        for item in raw_empty:
            if not _is_secret_key(str(item)):
                clean_empty.append(str(item))

    raw_errors = portal_context.get("validation_errors")
    clean_errors: list[dict[str, str]] = []
    if isinstance(raw_errors, Sequence) and not isinstance(raw_errors, str | bytes):
        for err in raw_errors:
            if isinstance(err, Mapping):
                f_name = str(err.get("field") or "")
                sel = str(err.get("selector") or "")
                if _is_secret_key(f_name) or _is_secret_key(sel):
                    continue
                clean_errors.append(
                    {
                        "field": f_name,
                        "message": str(err.get("message") or ""),
                        "selector": sel,
                    }
                )

    return {
        "portal_id": str(portal_context.get("portal_id") or "e_services").strip().lower(),
        "current_route": str(portal_context.get("current_route") or "")[:240],
        "active_step": str(portal_context.get("active_step") or "")[:160],
        "form_id": str(portal_context.get("form_id") or "")[:64],
        "visible_fields": clean_visible,
        "empty_required_fields": clean_empty,
        "validation_errors": clean_errors,
        "error_banner": str(portal_context.get("error_banner") or "")[:400],
        "stalled_duration_ms": int(portal_context.get("stalled_duration_ms") or 0),
        "last_user_action": str(portal_context.get("last_user_action") or "")[:120],
        "scrubbed_secret_fields": scrubbed_keys,
    }


# ---------------------------------------------------------------------------
# Entity Extraction from Natural-Language Prompts
# ---------------------------------------------------------------------------

_TIN_RE = re.compile(r"\b(1\d{9})\b")
_PRN_RE = re.compile(r"\b(2\d{9,12})\b")
_NIN_RE = re.compile(r"\b(C[MF][A-Z0-9]{12})\b", re.IGNORECASE)
_STAMP_RE = re.compile(r"\b(UG-DTS-\d{4}-\d{4,8})\b", re.IGNORECASE)
_BRN_RE = re.compile(r"\b(800\d{11}|URSB-[A-Z0-9-]+)\b", re.IGNORECASE)
_ENTRY_RE = re.compile(r"\b(\d{4}-ASY-IM[47]-\d+)\b", re.IGNORECASE)
_ASSESSMENT_REF_RE = re.compile(r"\b(ASMT-\d{4}-\d{3,8}|ASMT-\d{5,10})\b", re.IGNORECASE)
_FDN_RE = re.compile(r"\b(\d{20})\b")
_VEHICLE_PLATE_RE = re.compile(r"\b(U[A-Z]{1,2}\s?\d{3}[A-Z])\b", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_PHONE_RE = re.compile(r"(\+?256\s?7\d{8}|\b07\d{8}\b)")
_DOB_RE = re.compile(r"\b((?:19|20)\d{2}-\d{2}-\d{2})\b")
def _extract_amount_ugx(text: str) -> str:
    """Linear-time token scanner for UGX currency amounts (avoids ReDoS backtracking)."""
    tokens = text[:2000].replace(":", " ").replace("=", " ").split()
    currency_words = {"ugx", "shs", "shilling", "shillings", "amount"}
    for i, raw_tok in enumerate(tokens):
        low_tok = raw_tok.lower().strip(".,;()[]")
        if low_tok in currency_words:
            for candidate_idx in (i + 1, i + 2, i - 1):
                if 0 <= candidate_idx < len(tokens):
                    cand = tokens[candidate_idx].strip(".,;()[]").replace(",", "")
                    if cand.replace(".", "", 1).isdigit():
                        try:
                            val = float(cand)
                            if val > 0 and not (len(cand) == 10 and cand.startswith("1")):
                                return str(int(val))
                        except ValueError:
                            pass
    return ""


def _extract_quantity(text: str) -> str:
    """Linear-time token scanner for stamp/item quantities."""
    tokens = text[:2000].lower().split()
    unit_words = {"stamp", "stamps", "unit", "units", "reel", "reels", "bottle", "bottles"}
    for i, tok in enumerate(tokens):
        clean_u = tok.strip(".,;()[]")
        if clean_u in unit_words:
            for prev_idx in range(max(0, i - 4), i):
                cand = tokens[prev_idx].strip(".,;()[]").replace(",", "")
                if cand.isdigit() and 2 <= len(cand) <= 7:
                    return cand
    return ""


def extract_task_entities(message: str, portal_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Extract structured portal task entities from natural-language text and sanitized `portal_context`."""
    text = str(message or "")
    text_lower = text.lower()
    entities: dict[str, Any] = {}

    if portal_context and isinstance(portal_context, Mapping):
        sanitized = sanitize_portal_context(portal_context)
        for k, v in sanitized.get("visible_fields", {}).items():
            if v:
                entities[k] = v

    tin_m = _TIN_RE.search(text)
    if tin_m:
        entities["tin"] = tin_m.group(1)

    prn_m = _PRN_RE.search(text)
    if prn_m:
        entities["prn"] = prn_m.group(1)

    nin_m = _NIN_RE.search(text)
    if nin_m:
        entities["nin"] = nin_m.group(1).upper()

    stamp_m = _STAMP_RE.search(text)
    if stamp_m:
        entities["stamp_serial"] = stamp_m.group(1).upper()

    brn_m = _BRN_RE.search(text)
    if brn_m:
        entities["ursb_brn"] = brn_m.group(1).upper()

    entry_m = _ENTRY_RE.search(text)
    if entry_m:
        entities["entry_number"] = entry_m.group(1).upper()

    asmt_m = _ASSESSMENT_REF_RE.search(text)
    if asmt_m:
        entities["assessment_ref"] = asmt_m.group(1).upper()

    fdn_m = _FDN_RE.search(text)
    if fdn_m:
        entities["original_fdn"] = fdn_m.group(1)

    plate_m = _VEHICLE_PLATE_RE.search(text)
    if plate_m:
        entities["vehicle_registration_number"] = re.sub(r"\s+", " ", plate_m.group(1).upper())

    email_m = _EMAIL_RE.search(text)
    if email_m:
        entities["email"] = email_m.group(1)

    phone_m = _PHONE_RE.search(text)
    if phone_m:
        entities["mobile_number"] = re.sub(r"\s+", "", phone_m.group(1))

    dob_m = _DOB_RE.search(text)
    if dob_m:
        entities["date_of_birth"] = dob_m.group(1)

    amt_str = _extract_amount_ugx(text)
    if amt_str:
        entities["amount_ugx"] = amt_str

    # Tax head detection
    if re.search(r"\b(?:vat|value added tax|0010)\b", text_lower):
        entities.setdefault("tax_head", "VAT_STANDARD")
    elif re.search(r"\b(?:paye|pay as you earn|0012)\b", text_lower):
        entities.setdefault("tax_head", "PAYE")
    elif re.search(r"\b(?:withholding|wht|0013)\b", text_lower):
        entities.setdefault("tax_head", "WITHHOLDING_TAX")
    elif re.search(r"\b(?:rental|0014)\b", text_lower):
        entities.setdefault("tax_head", "RENTAL_TAX")
    elif re.search(r"\b(?:excise|0020)\b", text_lower):
        entities.setdefault("tax_head", "LOCAL_EXCISE")
    elif re.search(r"\b(?:passport|permit|ntr|ontr|0090)\b", text_lower):
        entities.setdefault("tax_head", "PASSPORT_FEE")
    elif re.search(r"\b(?:income tax|corporation tax|cit|0011)\b", text_lower):
        entities.setdefault("tax_head", "CORPORATION_TAX")

    # Payment mode detection
    if any(k in text_lower for k in ("mtn", "airtel", "mobile money", "momo", "*165#", "*185#")):
        entities.setdefault("payment_mode", "MOBILE_MONEY")
    elif any(k in text_lower for k in ("visa", "mastercard", "master card", "debit card", "credit card")):
        entities.setdefault("payment_mode", "VISA_MASTERCARD")
    elif any(k in text_lower for k in ("bank", "stanbic", "centenary", "absa", "dfcu", "equity", "kcb", "eft")):
        entities.setdefault("payment_mode", "COMMERCIAL_BANK")

    # DTS product category detection
    if "bottled water" in text_lower or ("water" in text_lower and "stamp" in text_lower):
        entities.setdefault("product_category", "BOTTLED_WATER")
    elif "beer" in text_lower:
        entities.setdefault("product_category", "BEER")
    elif "spirit" in text_lower:
        entities.setdefault("product_category", "SPIRITS")
    elif "soda" in text_lower:
        entities.setdefault("product_category", "SODA")
    elif "cement" in text_lower:
        entities.setdefault("product_category", "CEMENT")
    elif "sugar" in text_lower:
        entities.setdefault("product_category", "SUGAR")

    # Quantity extraction for stamps / stock
    qty_str = _extract_quantity(text)
    if qty_str:
        entities.setdefault("quantity", qty_str)

    return entities


def detect_portal_task_intent(
    message: str,
    portal_context: Mapping[str, Any] | None = None,
) -> PortalTaskIntent | None:
    """Detect whether the user is asking the AI to execute a URA portal task or open a portal screen."""
    text = str(message or "").strip()
    if not text:
        return None
    low = text.lower()
    extracted = extract_task_entities(text, portal_context=portal_context)

    is_how_to_question = any(
        low.startswith(prefix) or f" {prefix}" in f" {low}"
        for prefix in (
            "how do i ",
            "how can i ",
            "how to ",
            "what is the exact procedure",
            "what is the procedure",
            "what are the requirements",
            "what is required",
            "steps to ",
            "guide me step by step",
            "where do i ",
        )
    )

    def _intent(task_id: str, portal_key: str, confidence: float = 0.94) -> PortalTaskIntent:
        """Build a `PortalTaskIntent` with the extracted entities and how-to flag."""
        return PortalTaskIntent(
            task_id=task_id,
            portal_key=portal_key,
            confidence=confidence,
            extracted_fields=extracted,
            is_guidance_or_portal_link_request=is_how_to_question,
        )

    # 1. PRN Generation (Tier 2)
    if any(
        phrase in low
        for phrase in (
            "generate a prn",
            "generate prn",
            "generate a vat prn",
            "generate a paye prn",
            "generate an income tax prn",
            "create a prn",
            "create prn",
            "register a payment",
            "generate a payment slip",
            "generate payment slip",
            "get a prn for",
        )
    ) or (
        "prn" in low
        and any(w in low for w in ("generate", "create", "make", "issue", "get me"))
        and "reactivate" not in low
        and "expired" not in low
    ):
        return _intent("payment_generate_prn", "prn_payments", 0.95)

    # 2. PRN Reactivation (Tier 2)
    if any(
        phrase in low
        for phrase in (
            "reactivate prn",
            "reactivate my prn",
            "reactivate expired prn",
            "reactivate my expired prn",
            "renew expired prn",
        )
    ):
        return _intent("payment_reactivate_prn", "prn_payments", 0.95)

    # 3. PRN Payment Status Check (Tier 1)
    if any(
        phrase in low
        for phrase in (
            "check prn status",
            "check my prn status",
            "verify prn",
            "view payment status",
            "check payment status",
            "has my prn been paid",
        )
    ) or ("prn" in low and any(w in low for w in ("status", "check", "verify", "cleared"))):
        return _intent("payment_view_status", "prn_payments", 0.92)

    # 4. Verify TIN (Tier 1)
    if (
        any(
            phrase in low
            for phrase in (
                "verify tin",
                "search tin",
                "check tin",
                "lookup tin",
                "validate tin",
                "verify if tin",
                "check if tin",
            )
        )
        or (
            "tin" in low
            and bool(extracted.get("tin"))
            and any(w in low for w in ("verify", "validate", "lookup", "is active"))
        )
    ) and not any(w in low for w in ("obligation", "apply", "register", "tcc", "clearance", "prn", "return")):
        return _intent("tin_search_verify", "tin_registration", 0.93)

    # 5. Apply for Individual vs Non-Individual TIN (Tier 2)
    if any(
        phrase in low
        for phrase in (
            "apply for a company tin",
            "apply for company tin",
            "register a company tin",
            "apply for non-individual tin",
            "apply for a non-individual tin",
            "corporate tin application",
        )
    ):
        return _intent("tin_apply_non_individual", "tin_registration", 0.94)

    if any(
        phrase in low
        for phrase in (
            "apply for an individual tin",
            "apply for individual tin",
            "apply for a tin for me",
            "apply for my tin",
            "register for an individual tin",
            "get me an instant tin",
        )
    ):
        return _intent("tin_apply_individual", "tin_registration", 0.94)

    # 6. Digital Tax Stamps — Verify (Tier 1), Order (Tier 2), Activate (Tier 2), Status (Tier 3)
    if any(
        phrase in low
        for phrase in (
            "verify stamp",
            "verify digital tax stamp",
            "verify kakasa",
            "check stamp authenticity",
            "authenticate stamp",
        )
    ):
        return _intent("dts_verify_stamp", "dts", 0.93)

    if any(
        phrase in low
        for phrase in (
            "order digital tax stamps",
            "order tax stamps",
            "order stamps",
            "buy digital tax stamps",
        )
    ):
        return _intent("dts_order_stamps", "dts", 0.94)

    if any(
        phrase in low
        for phrase in (
            "activate stamps",
            "activate digital tax stamps",
            "activate stamp batch",
        )
    ):
        return _intent("dts_activate_stamps", "dts", 0.93)

    # 7. Tier 3 Authenticated Account Reads (TPCA s. 75)
    if any(
        phrase in low
        for phrase in (
            "check my tax obligations",
            "check tax obligations",
            "view my tax obligations",
            "my filing obligations",
            "tin_tax_obligations",
            "register vat obligation",
            "add tax obligation",
        )
    ) or ("obligations" in low and ("my" in low or "tin" in low or "check" in low or "query" in low)):
        return _intent("tin_tax_obligations", "e_services", 0.95)

    if any(
        phrase in low
        for phrase in (
            "check my ura account profile",
            "view my ura account",
            "my tax account balance",
            "check my tax ledger",
            "ura_account_profile",
        )
    ):
        return _intent("ura_account_profile", "e_services", 0.94)

    if any(
        phrase in low
        for phrase in (
            "check my efris status",
            "efris enrollment status",
            "efris_taxpayer_status",
        )
    ):
        return _intent("efris_taxpayer_status", "efris", 0.92)

    # 8. Tier 4 Statutory Boundary (Return Filing, Section 24 Objection, TCC, Refund, EFRIS Invoice)
    if any(
        phrase in low
        for phrase in (
            "submit my vat return",
            "file my vat return for me",
            "submit my paye return",
            "submit my income tax return",
            "file my tax return for me",
            "sign and submit my return",
            "return_filing_submit",
        )
    ) or (
        any(w in low for w in ("submit", "file", "declare"))
        and any(w in low for w in ("vat return", "paye return", "income tax return", "final return"))
        and not any(w in low for w in ("penalty for", "what is the deadline", "when is"))
    ):
        return _intent("return_filing_submit", "return_filing", 0.94)

    if any(
        phrase in low
        for phrase in (
            "lodge an objection for me",
            "lodge formal objection",
            "submit section 24 objection",
            "lodge my objection",
            "objection_appeals_lodge",
            "dispute this assessment",
            "dispute the assessment",
            "object to this assessment",
        )
    ) or (
        any(w in low for w in ("lodge", "submit", "file", "dispute"))
        and ("objection" in low or "assessment notice" in low)
        and not any(w in low for w in ("how many days", "what is the deadline"))
    ):
        return _intent("objection_appeals_lodge", "objection_appeals", 0.93)

    if any(
        phrase in low
        for phrase in (
            "apply for a tcc for me",
            "apply for tcc",
            "apply for tax clearance certificate",
            "submit tcc application",
            "tax_clearance_apply",
            "apply for the clearance certificate",
            "stage my tcc application",
        )
    ) or (
        any(w in low for w in ("tender", "bidding", "procurement"))
        and any(w in low for w in ("apply for", "get the certificate", "clearance certificate", "tcc"))
    ):
        return _intent("tax_clearance_apply", "tax_clearance", 0.92)

    if any(
        phrase in low
        for phrase in (
            "submit a tax refund claim",
            "claim my vat refund",
            "apply for tax refund for me",
            "get_refund_claim",
        )
    ):
        return _intent("get_refund_claim", "get_refund", 0.91)

    return None


# ---------------------------------------------------------------------------
# Field Validation & Subject-TIN Ownership (TPCA Cap. 343 s. 75)
# ---------------------------------------------------------------------------


def validate_task_fields(
    spec: PortalTaskSpec,
    submitted_fields: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str], dict[str, str]]:
    """Validate and normalize *submitted_fields* against *spec.fields*.

    Returns `(validated_params, missing_field_names, field_errors)`.
    """
    validated: dict[str, Any] = {}
    missing: list[str] = []
    errors: dict[str, str] = {}

    for f_spec in spec.fields:
        raw_val = submitted_fields.get(f_spec.name)
        if raw_val in (None, "") and f_spec.default_value:
            raw_val = f_spec.default_value
        val_str = str(raw_val or "").strip()

        if not val_str:
            if f_spec.required:
                missing.append(f_spec.name)
            continue

        # Field-specific format checks
        if f_spec.name in ("tin", "buyer_tin") and spec.task_id != "tin_search_verify":
            if not re.fullmatch(r"\d{10}", val_str):
                errors[f_spec.name] = "TIN must be an exact 10-digit number (e.g. 100-XXX-XXXX)."
                continue
        elif f_spec.name == "prn":
            if not re.fullmatch(r"\d{10,14}", val_str):
                errors[f_spec.name] = "PRN must be a 10 to 14-digit Payment Registration Number."
                continue
        elif f_spec.name == "nin":
            if not re.fullmatch(r"C[MF][A-Za-z0-9]{12}", val_str, re.IGNORECASE):
                errors[f_spec.name] = (
                    "NIN must be 14 alphanumeric characters starting with CM or CF (e.g. CM-14-CHAR-NIN)."
                )
                continue
            val_str = val_str.upper()
        elif f_spec.name == "mobile_number":
            normalized_phone = re.sub(r"[\s()-]+", "", val_str)
            if not re.fullmatch(r"(?:\+?256|0)7\d{8}", normalized_phone):
                errors[f_spec.name] = (
                    "Mobile number must be a valid Ugandan telephone number (e.g. +256772123456 or 0772123456)."
                )
                continue
            val_str = normalized_phone
        elif f_spec.name == "email":
            if not _EMAIL_RE.fullmatch(val_str):
                errors[f_spec.name] = "Please enter a valid email address (e.g. name@example.ug)."
                continue
        elif f_spec.name in ("amount_ugx", "quantity", "unit_cost"):
            cleaned = val_str.replace(",", "")
            try:
                num = float(cleaned)
                if not math.isfinite(num) or num <= 0:
                    errors[f_spec.name] = f"{f_spec.label} must be greater than zero."
                    continue
                if num > 1_000_000_000_000:
                    errors[f_spec.name] = f"{f_spec.label} exceeds the maximum single-transaction ceiling."
                    continue
                val_str = str(int(num)) if f_spec.name in ("amount_ugx", "quantity") else str(num)
            except ValueError:
                errors[f_spec.name] = f"{f_spec.label} must be a valid positive number."
                continue

        validated[f_spec.name] = val_str

    return validated, missing, errors


def extract_authenticated_tin(user_id: str | None, authenticated_tin: str | None = None) -> str:
    """Resolve the 10-digit TIN bound to the authenticated user identity, if any."""
    if authenticated_tin and re.fullmatch(r"\d{10}", str(authenticated_tin).strip()):
        return str(authenticated_tin).strip()
    uid = str(user_id or "").strip()
    if not uid:
        return ""
    m = re.search(r"\b(\d{10})\b", uid)
    return m.group(1) if m else ""


def check_subject_tin_ownership(
    spec: PortalTaskSpec,
    target_tin: str,
    *,
    user_id: str | None,
    authenticated_tin: str | None = None,
) -> tuple[bool, str]:
    """Enforce TPCA Cap. 343 s. 75 (Official Secrecy): User A cannot query User B's private obligations."""
    if spec.tier != PortalTaskTier.AUTHENTICATED_ACCOUNT_READ:
        return True, ""
    target = str(target_tin or "").strip()
    if not target:
        return True, ""
    owner_tin = extract_authenticated_tin(user_id, authenticated_tin)
    if owner_tin and target != owner_tin:
        return (
            False,
            f"Access refused under Section 75 of the Tax Procedures Code Act (TPCA) Cap. 343 (Official Secrecy): "
            f"your authenticated session is bound to TIN {owner_tin} and cannot query private tax obligations or "
            f"account records for a different taxpayer TIN ({target}).",
        )
    return True, ""


# ---------------------------------------------------------------------------
# Tool Execution Bridge
# ---------------------------------------------------------------------------


def _ensure_connector_tools_registered() -> None:
    """Ensure local connector simulator tools and URA account tools are loaded in ToolRegistry."""
    if os.getenv("APP_ENV", "development").lower() == "production":
        return
    if ToolRegistry.get("payment_generate_prn") is None:
        try:
            from .tools import plugins as _plugins  # noqa: F401
            from .tools import ura_account as _ura_account  # noqa: F401
            from .tools import ura_actions as _ura_actions  # noqa: F401
        except Exception:
            logger.debug("Optional connector tools could not be imported", exc_info=True)


def execute_portal_task(
    task_id: str,
    params: Mapping[str, Any],
    *,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Execute the underlying registered connector tool for *task_id* with normalized arguments."""
    _ensure_connector_tools_registered()
    spec = classify_portal_task(task_id)
    if spec is None:
        return {"ok": False, "error": f"Unknown portal task: {task_id}", "live": False, "mode": "simulation"}

    tool = ToolRegistry.get(spec.tool_name)
    p = dict(params)

    if tool is None:
        return {
            "ok": False,
            "error": f"Tool '{spec.tool_name}' is not available in this environment.",
            "live": False,
            "mode": "simulation",
        }

    # Map canonical PortalFieldSpec names to each tool's parameter schema
    if spec.task_id == "tin_search_verify":
        tool_args: dict[str, Any] = {"query": str(p.get("tin") or p.get("query") or "")}
    elif spec.task_id == "payment_view_status":
        tool_args = {"prn": str(p.get("prn") or "")}
    elif spec.task_id == "dts_verify_stamp":
        tool_args = {"stamp_code": str(p.get("stamp_serial") or p.get("stamp_code") or "")}
    elif spec.task_id == "ursb_verify_business":
        tool_args = {"query": str(p.get("ursb_brn") or p.get("query") or "")}
    elif spec.task_id == "payment_verify_advance_tax":
        tool_args = {"vehicle_registration_number": str(p.get("vehicle_registration_number") or "")}
    elif spec.task_id == "payment_generate_prn":
        tool_args = {
            "taxpayer_name": str(p.get("taxpayer_name") or "Registered Taxpayer"),
            "amount_ugx": float(p.get("amount_ugx") or 0),
            "taxpayer_tin": str(p.get("tin") or ""),
            "tax_head": str(p.get("tax_head") or "VAT_STANDARD"),
            "payment_channel": str(p.get("payment_mode") or "COMMERCIAL_BANK"),
        }
    elif spec.task_id == "payment_reactivate_prn":
        tool_args = {"prn": str(p.get("prn") or "")}
    elif spec.task_id == "tin_apply_individual":
        surname = str(p.get("surname") or "").strip()
        given = str(p.get("given_name") or "").strip()
        full_name = str(p.get("full_name") or f"{surname} {given}".strip() or "Applicant")
        tool_args = {
            "nin": str(p.get("nin") or ""),
            "full_name": full_name,
            "date_of_birth": str(p.get("date_of_birth") or "1992-01-01"),
            "mobile": str(p.get("mobile_number") or "+256700000000"),
            "email": str(p.get("email") or "taxpayer@example.ug"),
            "district": str(p.get("district") or "Kampala"),
        }
    elif spec.task_id == "tin_apply_non_individual":
        tool_args = {
            "ursb_registration_number": str(p.get("ursb_brn") or ""),
            "business_name": str(p.get("legal_name") or ""),
            "category": str(p.get("entity_type") or "NON_INDIVIDUAL_COMPANY"),
            "district": str(p.get("district") or "Kampala"),
            "mobile": str(p.get("mobile_number") or "+256700000000"),
            "email": str(p.get("email") or "info@company.co.ug"),
        }
    elif spec.task_id == "dts_order_stamps":
        tool_args = {
            "taxpayer_tin": str(p.get("tin") or ""),
            "product_category": str(p.get("product_category") or "BOTTLED_WATER"),
            "quantity": int(float(p.get("quantity") or 1000)),
        }
    elif spec.task_id == "dts_activate_stamps":
        serials = [s.strip() for s in str(p.get("stamp_serial") or "").split(",") if s.strip()]
        tool_args = {
            "action": "activate",
            "order_id": str(p.get("order_id") or ""),
            "line_id": str(p.get("line_id") or "LINE-KLA-01"),
            "stamp_serials": serials,
        }
    elif spec.task_id == "efris_stock_management":
        tool_args = {
            "action": "stock_in",
            "tin": str(p.get("tin") or ""),
            "commodity_code": str(p.get("commodity_code") or ""),
            "description": str(p.get("description") or "Portal Co-Pilot Stock-In"),
            "quantity": float(p.get("quantity") or 0),
            "unit_cost": float(p.get("unit_cost") or 0),
        }
    elif spec.task_id == "ursb_register_business":
        tool_args = {
            "business_name": str(p.get("legal_name") or ""),
            "applicant_nin": str(p.get("nin") or ""),
            "district": str(p.get("district") or "Kampala"),
        }
    elif spec.task_id == "ura_account_profile":
        tool_args = {"taxpayer_id": str(p.get("tin") or user_id or "")}
    elif spec.task_id == "tin_tax_obligations":
        tool_args = {
            "tin": str(p.get("tin") or ""),
            "tax_head": str(p.get("tax_head") or "VAT_STANDARD"),
        }
    elif spec.task_id in ("efris_taxpayer_status", "dts_taxpayer_status"):
        tool_args = {"tin": str(p.get("tin") or "")}
    elif spec.task_id == "ursb_compliance_status":
        tool_args = {"registration_number": str(p.get("ursb_brn") or "")}
    elif spec.task_id == "bwims_consignment_status":
        tool_args = {"entry_number": str(p.get("entry_number") or "")}
    elif spec.task_id == "bwims_warehouse_inventory":
        tool_args = {
            "warehouse_code": str(p.get("warehouse_code") or "WH-KLA-001"),
            "importer_tin": str(p.get("tin") or "") or None,
        }
    else:
        tool_args = dict(p)

    raw = ToolRegistry.call(
        spec.tool_name,
        tool_args,
        user_id=str(user_id or ""),
        caller_confirmed=True,
    )

    result_dict = dict(raw) if isinstance(raw, Mapping) else {"ok": True, "data": raw}
    result_dict.setdefault("live", False)
    result_dict["mode"] = "live" if result_dict.get("live") is True else "simulation"
    return result_dict


# ---------------------------------------------------------------------------
# 4-Tier Policy Gate & Card Builder
# ---------------------------------------------------------------------------


def build_portal_task_card(
    task_id: str,
    *,
    extracted_fields: Mapping[str, Any] | None = None,
    user_id: str | None = None,
    authenticated_tin: str | None = None,
    session_id: str | None = None,
    conversation_id: str | None = None,
    tenant_id: str = "default",
) -> dict[str, Any]:
    """Evaluate the 4-Tier Policy for *task_id* and build the declarative A2UI card payload."""
    spec = classify_portal_task(task_id)
    if spec is None:
        raise KeyError(f"Unknown portal task_id: {task_id}")

    conv_id = str(conversation_id or "default")
    owner_key = db.conversation_state_key(
        conv_id,
        session_id=session_id or None,
        user_id=user_id or None,
        tenant_id=tenant_id,
    )
    fields_map: dict[str, Any] = dict(extracted_fields or {})

    # Pre-fill TIN from authenticated user only when the caller didn't explicitly supply a different TIN
    auth_tin = extract_authenticated_tin(user_id, authenticated_tin)
    if auth_tin and "tin" not in fields_map:
        fields_map["tin"] = auth_tin

    copilot_portal_url = (
        f"/portal/{spec.portal_key}?intent={urllib.parse.quote(spec.task_id)}"
        f"&conversation_id={urllib.parse.quote(conv_id)}&copilot=1"
    )
    portal_info = EXTERNAL_URA_PORTALS.get(spec.portal_key, EXTERNAL_URA_PORTALS["e_services"])

    # ── Gate 1: Step-Up Authentication Check (RFC 9470 & WCAG 2.2 AA SC 3.3.7) ──
    if spec.requires_auth and not user_id:
        return_to_chat_url = (
            f"/?conversation_id={urllib.parse.quote(conv_id)}"
            f"&auth_resumed=1&pending_task={urllib.parse.quote(spec.task_id)}"
        )
        return_to_portal_url = copilot_portal_url
        signin_chat_href = f"/signin?continue=signin&returnTo={urllib.parse.quote(return_to_chat_url, safe='')}"
        signin_portal_href = f"/signin?continue=signin&returnTo={urllib.parse.quote(return_to_portal_url, safe='')}"

        return {
            "card_type": "step_up_login",
            "task_id": spec.task_id,
            "portal_key": spec.portal_key,
            "portal_name": portal_info["name"],
            "title": spec.title,
            "tier": spec.tier.value,
            "requires_auth": True,
            "statutory_citation": spec.statutory_citation,
            "login_reason": (
                f"Accessing {spec.title} requires signing in to your URA account under "
                f"{spec.statutory_citation}. Once your login is accepted, you will be brought "
                f"automatically back here with your progress preserved."
            ),
            "return_to_chat_url": return_to_chat_url,
            "return_to_portal_url": return_to_portal_url,
            "signin_chat_href": signin_chat_href,
            "signin_portal_href": signin_portal_href,
            "preserved_fields": {
                k: mask_sensitive_value(k, v) if any(f.name == k and f.sensitive for f in spec.fields) else v
                for k, v in fields_map.items()
            },
            "raw_preserved_fields": {
                k: v
                for k, v in fields_map.items()
                if not any(f.name == k and f.sensitive for f in spec.fields)
            },
            "copilot_portal_url": copilot_portal_url,
        }

    # ── Gate 1b: TPCA s. 75 Subject-TIN Ownership Check for Authenticated Tier 3 ──
    allowed, secrecy_error = check_subject_tin_ownership(
        spec,
        str(fields_map.get("tin") or ""),
        user_id=user_id,
        authenticated_tin=authenticated_tin,
    )
    if not allowed:
        return {
            "card_type": "access_denied",
            "task_id": spec.task_id,
            "portal_key": spec.portal_key,
            "title": spec.title,
            "tier": spec.tier.value,
            "statutory_citation": "Tax Procedures Code Act (TPCA) Cap. 343 s. 75 (Official Secrecy)",
            "error": secrecy_error,
            "copilot_portal_url": copilot_portal_url,
        }

    # ── Gate 2: Tier 4 Statutory Boundary (`STATUTORY_COPILOT_ONLY`) ──
    if spec.tier == PortalTaskTier.STATUTORY_COPILOT_ONLY:
        dom_actions = get_portal_dom_recipe(spec.portal_key, state=spec.task_id, entities=fields_map)
        autofill_url = f"{copilot_portal_url}&autofill=1"
        return {
            "card_type": "statutory_copilot_handoff",
            "task_id": spec.task_id,
            "portal_key": spec.portal_key,
            "portal_name": portal_info["name"],
            "title": spec.title,
            "tier": spec.tier.value,
            "requires_auth": spec.requires_auth,
            "statutory_citation": spec.statutory_citation,
            "verification_warning": (
                f"Under {spec.statutory_citation}, final statutory declarations and legal submissions must be "
                f"personally reviewed and signed by the taxpayer. The URA AI Co-Pilot has staged your fields and "
                f"DOM auto-fill recipe up to the final confirmation button."
            ),
            "staged_fields": dict(fields_map),
            "dom_actions": dom_actions,
            "copilot_portal_url": autofill_url,
            "open_portal_label": f"Open in {portal_info['name']} with AI Auto-Fill Ready",
        }

    # ── Gate 3: Validate Required Inputs -> Inline Chat Form Card (`inline_form`) ──
    validated_params, missing_fields, field_errors = validate_task_fields(spec, fields_map)
    if missing_fields or field_errors:
        submit_label = (
            "Verify Now"
            if spec.tier == PortalTaskTier.AUTONOMOUS_VERIFY
            else "Review & Verify Details"
        )
        return {
            "card_type": "inline_form",
            "task_id": spec.task_id,
            "portal_key": spec.portal_key,
            "portal_name": portal_info["name"],
            "title": spec.title,
            "tier": spec.tier.value,
            "requires_auth": spec.requires_auth,
            "statutory_citation": spec.statutory_citation,
            "description": spec.description,
            "missing_fields": missing_fields,
            "field_errors": field_errors,
            "fields": [
                f.to_dict(
                    value=fields_map.get(f.name, ""),
                    validation_error=field_errors.get(f.name, ""),
                )
                for f in spec.fields
            ],
            "submit_label": submit_label,
            "copilot_portal_url": copilot_portal_url,
        }

    # ── Gate 4: Tier 1 Autonomous Execution (`executed_result`) ──
    if spec.tier == PortalTaskTier.AUTONOMOUS_VERIFY:
        exec_res = execute_portal_task(spec.task_id, validated_params, user_id=user_id)
        dom_actions = get_portal_dom_recipe(spec.portal_key, state=spec.task_id, entities=validated_params)
        return {
            "card_type": "executed_result",
            "task_id": spec.task_id,
            "portal_key": spec.portal_key,
            "portal_name": portal_info["name"],
            "title": spec.title,
            "tier": spec.tier.value,
            "live": bool(exec_res.get("live", False)),
            "mode": str(exec_res.get("mode") or "simulation"),
            "result": exec_res,
            "dom_actions": dom_actions,
            "copilot_portal_url": copilot_portal_url,
        }

    # ── Gate 5: Tier 2 & Tier 3 Claude-Style Confirmation Card (`confirmation_required`) ──
    canonical_params = json.dumps(validated_params, sort_keys=True, separators=(",", ":"))
    params_sha256 = hashlib.sha256(canonical_params.encode("utf-8")).hexdigest()
    idempotency_key = "ura_task_" + hashlib.sha256(
        f"{owner_key}:{spec.task_id}:{canonical_params}".encode()
    ).hexdigest()[:32]

    issued_at = int(time.time())
    state_payload = {
        "task_id": spec.task_id,
        "portal_key": spec.portal_key,
        "tier": spec.tier.value,
        "params": validated_params,
        "params_sha256": params_sha256,
        "owner_key": owner_key,
        "user_id": str(user_id or ""),
        "issued_at": issued_at,
        "expires_at": issued_at + PORTAL_TASK_STATE_TTL_SECONDS,
        "idempotency_key": idempotency_key,
    }
    signed_request_state = mrtr.wrap_request_state(state_payload)

    display_fields: list[dict[str, Any]] = []
    for f in spec.fields:
        if f.name not in validated_params:
            continue
        raw_v = str(validated_params[f.name])
        display_fields.append(
            {
                "name": f.name,
                "label": f.label,
                "value": mask_sensitive_value(f.name, raw_v) if f.sensitive else raw_v,
                "raw_value": raw_v,
                "sensitive": f.sensitive,
            }
        )

    return {
        "card_type": "confirmation_required",
        "task_id": spec.task_id,
        "portal_key": spec.portal_key,
        "portal_name": portal_info["name"],
        "title": spec.title,
        "tier": spec.tier.value,
        "requires_auth": spec.requires_auth,
        "statutory_citation": spec.statutory_citation,
        "verification_prompt": spec.verification_warning,
        "reversibility_notice": (
            "Reversibility & Safeguard (WCAG 2.2 SC 3.3.4): You can edit any field before allowing execution, "
            "or cancel at any time. Unpaid PRNs expire automatically after 21 days."
        ),
        "display_fields": display_fields,
        "fields": [
            f.to_dict(value=validated_params.get(f.name, ""))
            for f in spec.fields
        ],
        "allow_label": "Allow & Execute",
        "disallow_label": "Disallow",
        "edit_label": "Edit Details",
        "request_state": signed_request_state,
        "idempotency_key": idempotency_key,
        "expires_in_seconds": PORTAL_TASK_STATE_TTL_SECONDS,
        "simulation_mode": True,
        "live": False,
        "copilot_portal_url": copilot_portal_url,
    }


# ---------------------------------------------------------------------------
# Confirmed Task Decision Handler (`POST /v1/portal/tasks/execute`)
# ---------------------------------------------------------------------------


def handle_portal_task_decision(
    decision: str,
    request_state: Mapping[str, Any],
    idempotency_key: str,
    *,
    owner_key: str,
    candidate_owner_keys: Sequence[str] = (),
    user_id: str | None = None,
    authenticated_tin: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Verify HMAC-signed `request_state`, enforce owner/session binding (`ADR-0013`), and execute or decline."""
    unwrapped = mrtr.unwrap_request_state(dict(request_state) if isinstance(request_state, Mapping) else None)
    if unwrapped is None:
        raise PortalTaskValidationError("Invalid or tampered request_state signature.")

    task_id = str(unwrapped.get("task_id") or "").strip()
    state_idem = str(unwrapped.get("idempotency_key") or "").strip()
    state_owner = str(unwrapped.get("owner_key") or "").strip()
    state_user = str(unwrapped.get("user_id") or "").strip()
    state_tier = str(unwrapped.get("tier") or "").strip()
    state_params_hash = str(unwrapped.get("params_sha256") or "").strip()
    params = unwrapped.get("params")

    if not task_id or not isinstance(params, Mapping):
        raise PortalTaskValidationError("Malformed request_state payload.")
    if state_idem != str(idempotency_key or "").strip():
        raise PortalTaskValidationError("idempotency_key does not match signed request_state.")

    # Temporal Anti-Replay check (OWASP ASI07)
    now_ts = int(time.time())
    try:
        issued_at = int(unwrapped.get("issued_at") or 0)
    except (TypeError, ValueError) as exc:
        raise PortalTaskValidationError("Invalid issued_at timestamp in request_state.") from exc
    if issued_at <= 0 or (now_ts - issued_at) > PORTAL_TASK_STATE_TTL_SECONDS or (issued_at - now_ts) > 60:
        raise PortalTaskValidationError(
            "Confirmation request_state has expired (exceeded 15-minute validity window). Please review and confirm again."
        )

    # Intent Capsule canonical parameter digest check (OWASP ASI01)
    canonical_params = json.dumps(dict(params), sort_keys=True, separators=(",", ":"))
    computed_hash = hashlib.sha256(canonical_params.encode("utf-8")).hexdigest()
    if state_params_hash and not hmac_compare(state_params_hash, computed_hash):
        raise PortalTaskValidationError("Intent capsule parameter digest mismatch in request_state.")

    allowed_owners = {owner_key, *candidate_owner_keys}
    if state_owner not in allowed_owners:
        raise PortalTaskOwnershipError(
            "Cross-session or cross-user request_state replay refused (ADR-0013 owner_key mismatch)."
        )
    if state_user and user_id and state_user != str(user_id):
        raise PortalTaskOwnershipError(
            "Signed request_state belongs to a different authenticated user."
        )

    spec = classify_portal_task(task_id)
    if spec is None:
        raise PortalTaskValidationError(f"Unknown portal task_id: {task_id}")
    if state_tier and state_tier != spec.tier.value:
        raise PortalTaskValidationError("Intent capsule policy tier mismatch.")

    conv_id = str(conversation_id or "default")
    copilot_portal_url = (
        f"/portal/{spec.portal_key}?intent={urllib.parse.quote(spec.task_id)}"
        f"&conversation_id={urllib.parse.quote(conv_id)}&copilot=1"
    )
    cache_key = (state_owner, state_idem)

    decision_clean = str(decision or "").strip().lower()
    if decision_clean in ("disallow", "decline", "cancel"):
        shared_state, _ = _read_shared_task_decision(cache_key)
        with _TASK_CACHE_LOCK:
            if cache_key not in _EXECUTED_TASK_CACHE and shared_state != "executed":
                _DECLINED_TASK_CACHE[cache_key] = time.time()
                while len(_DECLINED_TASK_CACHE) > _MAX_TASK_CACHE_ENTRIES:
                    _DECLINED_TASK_CACHE.popitem(last=False)
                _write_shared_task_decision(cache_key, declined=True, user_id=user_id)
        return {
            "ok": True,
            "status": "declined",
            "task_id": spec.task_id,
            "portal_key": spec.portal_key,
            "live": False,
            "mode": "simulation",
            "summary": "Action cancelled by taxpayer. No changes were sent to the URA portal.",
            "result": {"cancelled": True},
            "dom_actions": [],
            "copilot_portal_url": copilot_portal_url,
        }

    if decision_clean not in ("allow", "accept"):
        raise PortalTaskValidationError(f"Unsupported decision: {decision}")

    with _TASK_CACHE_LOCK:
        if cache_key in _DECLINED_TASK_CACHE:
            raise PortalTaskValidationError(
                "This action proposal was already declined by the taxpayer and cannot be replayed."
            )
        if cache_key in _EXECUTED_TASK_CACHE:
            _EXECUTED_TASK_CACHE.move_to_end(cache_key)
            cached = dict(_EXECUTED_TASK_CACHE[cache_key])
            cached["idempotent_replay"] = True
            return cached
        shared_state, shared_payload = _read_shared_task_decision(cache_key)
        if shared_state == "declined":
            _DECLINED_TASK_CACHE[cache_key] = time.time()
            raise PortalTaskValidationError(
                "This action proposal was already declined by the taxpayer and cannot be replayed."
            )
        if shared_state == "executed" and shared_payload:
            _EXECUTED_TASK_CACHE[cache_key] = dict(shared_payload)
            cached = dict(shared_payload)
            cached["idempotent_replay"] = True
            return cached
        if cache_key in _IN_FLIGHT_TASK_KEYS:
            raise PortalTaskValidationError("This action proposal is already being executed.")
        _IN_FLIGHT_TASK_KEYS.add(cache_key)

    try:
        # Re-verify 4-Tier Policy Gate at execution time
        if spec.requires_auth and not user_id:
            return {
                "ok": False,
                "status": "requires_auth",
                "task_id": spec.task_id,
                "portal_key": spec.portal_key,
                "live": False,
                "mode": "simulation",
                "summary": f"Authentication is required to execute {spec.title}.",
                "result": {},
                "dom_actions": [],
                "copilot_portal_url": copilot_portal_url,
            }

        allowed_tin, secrecy_err = check_subject_tin_ownership(
            spec,
            str(params.get("tin") or ""),
            user_id=user_id,
            authenticated_tin=authenticated_tin,
        )
        if not allowed_tin:
            raise PortalTaskOwnershipError(secrecy_err)

        exec_result = execute_portal_task(spec.task_id, params, user_id=user_id)
        ok = bool(exec_result.get("ok", True))
        live = bool(exec_result.get("live", False))
        mode = "live" if live else "simulation"
        dom_actions = get_portal_dom_recipe(spec.portal_key, state=spec.task_id, entities=dict(params))

        summary_msg = str(
            exec_result.get("message")
            or (
                f"Completed {spec.title} in simulation mode (PRN: {exec_result['prn']})."
                if exec_result.get("prn")
                else f"Completed {spec.title} ({mode} mode)."
            )
        )

        response_payload = {
            "ok": ok,
            "status": "executed" if ok else "error",
            "task_id": spec.task_id,
            "portal_key": spec.portal_key,
            "live": live,
            "mode": mode,
            "summary": summary_msg,
            "result": exec_result,
            "dom_actions": dom_actions,
            "copilot_portal_url": copilot_portal_url,
        }
        if ok:
            with _TASK_CACHE_LOCK:
                _EXECUTED_TASK_CACHE[cache_key] = dict(response_payload)
                while len(_EXECUTED_TASK_CACHE) > _MAX_TASK_CACHE_ENTRIES:
                    _EXECUTED_TASK_CACHE.popitem(last=False)
                _write_shared_task_decision(
                    cache_key,
                    declined=False,
                    payload=response_payload,
                    user_id=user_id,
                )
        return response_payload
    finally:
        with _TASK_CACHE_LOCK:
            _IN_FLIGHT_TASK_KEYS.discard(cache_key)


def hmac_compare(a: str, b: str) -> bool:
    """Compare two hex digests in constant time."""
    import hmac

    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ---------------------------------------------------------------------------
# Stuck-User Live DOM Telemetry Analyzer (`analyze_portal_context`)
# ---------------------------------------------------------------------------

_FIELD_SELECTOR_MAP: dict[str, str] = {
    "tax_head": "select#ddlTaxHead, select[name='taxHead'], select[name='tax_head']",
    "taxhead": "select#ddlTaxHead, select[name='taxHead'], select[name='tax_head']",
    "tin": "input#txtTIN, input[name='tin']",
    "amount": "input#txtAmount, input[name='amount'], input[name='amount_ugx']",
    "amount_ugx": "input#txtAmount, input[name='amount'], input[name='amount_ugx']",
    "payment_mode": "select#ddlPaymentMode, select[name='paymentMode'], select[name='payment_mode']",
    "paymentmode": "select#ddlPaymentMode, select[name='paymentMode'], select[name='payment_mode']",
    "nin": "input#txtNIN, input[name='nin']",
    "ursb_brn": "input#txtURSBBrn, input[name='ursb_brn']",
    "buyer_tin": "input#buyerTin, input[name='purchaserTin'], input[name='buyer_tin']",
    "commodity_code": "input#commodityCode, select#itemClassification, input[name='commodity_code']",
    "assessment_ref": "input#txtAssessmentRef, input[name='assessment_ref']",
    "grounds": "textarea#txtObjectionGrounds, textarea[name='grounds']",
    "tcc_purpose": "select#ddlTccPurpose, select[name='tcc_purpose']",
    "product_category": "select#ddlDtsCategory, select[name='product_category']",
    "quantity": "input#txtStampQuantity, input[name='quantity']",
    "chassis_vin": "input#txtChassisVin, input#txtSadNumber, input[name='chassis_vin']",
}


def analyze_portal_context(
    portal_context: Mapping[str, Any],
    *,
    user_id: str | None = None,
    conversation_id: str = "",
) -> dict[str, Any]:
    """Analyze live DOM telemetry from the Inbuilt Portal Co-Pilot or Browser Companion.

    Scrubs secrets, diagnoses why the taxpayer is stuck, and returns actionable
    `dom_actions` with `scroll_to=True` on off-screen errors or missing required fields.
    """
    sanitized = sanitize_portal_context(portal_context)
    portal_key = sanitized.get("portal_id") or "e_services"
    if portal_key not in EXTERNAL_URA_PORTALS and portal_key != "return_filing":
        portal_key = "e_services"

    portal_info = EXTERNAL_URA_PORTALS.get(
        "e_services" if portal_key == "return_filing" else portal_key,
        EXTERNAL_URA_PORTALS["e_services"],
    )
    visible_fields = sanitized.get("visible_fields", {})
    validation_errors = sanitized.get("validation_errors", [])
    empty_required = sanitized.get("empty_required_fields", [])
    error_banner = sanitized.get("error_banner", "")
    stalled_ms = int(sanitized.get("stalled_duration_ms") or 0)
    active_step = sanitized.get("active_step") or "Form Entry"

    # Build base DOM recipe bound with visible fields
    base_recipe = get_portal_dom_recipe(portal_key, state=error_banner or active_step, entities=visible_fields)
    dom_actions: list[dict[str, Any]] = []

    diagnosis_points: list[str] = []
    if error_banner:
        diagnosis_points.append(f"Portal Alert Banner: {error_banner}")

    # Prioritize explicit validation errors so the first failing field is scrolled into view and spotlighted
    for idx, err in enumerate(validation_errors):
        field_name = str(err.get("field") or "").strip()
        err_msg = str(err.get("message") or "Invalid or missing value").strip()
        selector = (
            str(err.get("selector") or "").strip()
            or _FIELD_SELECTOR_MAP.get(field_name.lower(), f"[name='{field_name}'], #{field_name}")
        )
        diagnosis_points.append(f"Field '{field_name}': {err_msg}")
        dom_actions.append(
            {
                "step_number": idx + 1,
                "element": f"Fix {field_name}",
                "selector": selector,
                "action_type": "spotlight",
                "fill_value": visible_fields.get(field_name),
                "scroll_to": True,
                "tooltip": f"{err_msg} — complete or correct this field first.",
                "security_gate": "safe",
            }
        )

    for req_field in empty_required:
        if any(req_field.lower() in a["element"].lower() for a in dom_actions):
            continue
        selector = _FIELD_SELECTOR_MAP.get(req_field.lower(), f"[name='{req_field}'], #{req_field}")
        diagnosis_points.append(f"Required field '{req_field}' is currently empty.")
        dom_actions.append(
            {
                "step_number": len(dom_actions) + 1,
                "element": f"Complete required field: {req_field}",
                "selector": selector,
                "action_type": "spotlight",
                "fill_value": None,
                "scroll_to": len(dom_actions) == 0,
                "tooltip": f"This mandatory field ({req_field}) is required before submitting.",
                "security_gate": "safe",
            }
        )

    # Append remaining recipe steps with sequential numbering
    for step in base_recipe:
        step_copy = dict(step)
        step_copy["step_number"] = len(dom_actions) + 1
        step_copy.setdefault("scroll_to", len(dom_actions) == 0)
        dom_actions.append(step_copy)

    if not diagnosis_points:
        if stalled_ms >= 15_000:
            diagnosis_points.append(
                f"Detected {stalled_ms // 1000}s pause on step '{active_step}'. Highlighting the next required input."
            )
        else:
            diagnosis_points.append(f"Ready to guide you through '{active_step}' on {portal_info['name']}.")

    conv_q = f"&conversation_id={urllib.parse.quote(conversation_id)}" if conversation_id else ""
    copilot_portal_url = f"/portal/{portal_key}?copilot=1{conv_q}"
    requires_auth = portal_requires_auth(portal_key)

    return {
        "card_type": "stuck_portal_assistance",
        "portal_key": portal_key,
        "portal_name": portal_info["name"],
        "active_step": active_step,
        "requires_auth": requires_auth,
        "is_authenticated": bool(user_id),
        "diagnosis_summary": " | ".join(diagnosis_points),
        "diagnosis_points": diagnosis_points,
        "sanitized_context": sanitized,
        "dom_actions": dom_actions,
        "copilot_portal_url": copilot_portal_url,
        "escalation_context": {
            "portal_key": portal_key,
            "current_route": sanitized.get("current_route", ""),
            "active_step": active_step,
            "error_banner": error_banner,
            "validation_error_count": len(validation_errors),
        },
    }


# ---------------------------------------------------------------------------
# Unified Chat Turn Evaluator (`evaluate_turn`)
# ---------------------------------------------------------------------------


def evaluate_turn(
    *,
    message: str,
    portal_context: Mapping[str, Any] | None = None,
    portal_task_submission: Mapping[str, Any] | None = None,
    conversation_history: Sequence[Mapping[str, Any]] | None = None,
    user_id: str | None = None,
    authenticated_tin: str | None = None,
    session_id: str | None = None,
    conversation_id: str | None = None,
    tenant_id: str = "default",
) -> PortalCopilotOutcome | None:
    """Evaluate a chat turn for inline form elicitation, Claude-style confirmation, or live DOM co-pilot help."""
    conv_id = str(conversation_id or "default")
    owner_key = db.conversation_state_key(
        conv_id,
        session_id=session_id or None,
        user_id=user_id or None,
        tenant_id=tenant_id,
    )
    anon_owner_key = db.conversation_state_key(
        conv_id,
        session_id=session_id or None,
        user_id=None,
        tenant_id=tenant_id,
    )

    # Gather entities from prior user turns in the same conversation (both in-memory owner cache
    # and conversation_history) so PII redacted by db.log_conversation is still remembered across turns
    history_entities: dict[str, Any] = {}
    with _TASK_CACHE_LOCK:
        if anon_owner_key in _CONVERSATION_ENTITY_MEMORY:
            history_entities.update(_CONVERSATION_ENTITY_MEMORY[anon_owner_key])
        if owner_key in _CONVERSATION_ENTITY_MEMORY:
            history_entities.update(_CONVERSATION_ENTITY_MEMORY[owner_key])
    def _should_carry_over_amount(msg_text: str) -> bool:
        low_m = msg_text.lower()
        if any(w in low_m for w in ("turnover", "annual sales", "gross sales", "salary", "threshold")):
            return any(w in low_m for w in ("prn", "payment slip", "need to pay ugx", "want to pay ugx"))
        return any(w in low_m for w in ("pay", "prn", "payment", "refund", "net tax"))

    if conversation_history:
        for turn in conversation_history:
            if isinstance(turn, Mapping):
                u_msg = str(turn.get("user_message") or turn.get("content") or "")
                if u_msg:
                    turn_ent = extract_task_entities(u_msg)
                    if "amount_ugx" in turn_ent and not _should_carry_over_amount(u_msg):
                        turn_ent.pop("amount_ugx", None)
                    history_entities.update(turn_ent)

    current_turn_entities = extract_task_entities(message)
    if current_turn_entities:
        mem_entities = dict(current_turn_entities)
        if "amount_ugx" in mem_entities and not _should_carry_over_amount(message):
            mem_entities.pop("amount_ugx", None)
        with _TASK_CACHE_LOCK:
            merged_mem = {**history_entities, **mem_entities}
            _CONVERSATION_ENTITY_MEMORY[owner_key] = merged_mem
            _CONVERSATION_ENTITY_MEMORY.move_to_end(owner_key)
            while len(_CONVERSATION_ENTITY_MEMORY) > _MAX_TASK_CACHE_ENTRIES:
                _CONVERSATION_ENTITY_MEMORY.popitem(last=False)

    # 1. Explicit Inline Form Submission or Post-Login Resumption
    if portal_task_submission and isinstance(portal_task_submission, Mapping):
        sub_task_id = str(portal_task_submission.get("task_id") or "").strip()
        sub_fields = portal_task_submission.get("fields")
        merged_fields: dict[str, Any] = {}
        if isinstance(sub_fields, Mapping):
            merged_fields.update(sub_fields)
        else:
            merged_fields.update(
                {k: v for k, v in portal_task_submission.items() if k not in ("task_id", "resumed_after_login")}
            )
        if sub_task_id and classify_portal_task(sub_task_id) is not None:
            card = build_portal_task_card(
                sub_task_id,
                extracted_fields=merged_fields,
                user_id=user_id,
                authenticated_tin=authenticated_tin,
                session_id=session_id,
                conversation_id=conv_id,
                tenant_id=tenant_id,
            )
            return _outcome_from_card(card)

    # 2. Natural-Language Task Intent Detection
    intent = detect_portal_task_intent(message, portal_context=portal_context)
    if intent is not None:
        if intent.is_guidance_or_portal_link_request:
            spec = classify_portal_task(intent.task_id)
            portal_key = intent.portal_key
            portal_info = EXTERNAL_URA_PORTALS.get(
                "e_services" if portal_key == "return_filing" else portal_key,
                EXTERNAL_URA_PORTALS["e_services"],
            )
            copilot_url = (
                f"/portal/{portal_key}?intent={urllib.parse.quote(intent.task_id)}"
                f"&conversation_id={urllib.parse.quote(conv_id)}&copilot=1"
            )
            requires_login = portal_requires_auth(portal_key, intent=intent.task_id)
            signin_url = f"/signin?continue=signin&returnTo={urllib.parse.quote(copilot_url, safe='')}"
            banner_card = {
                "card_type": "copilot_launch_banner",
                "task_id": intent.task_id,
                "portal_key": portal_key,
                "portal_name": portal_info["name"],
                "title": spec.title if spec else portal_info["name"],
                "requires_auth": requires_login,
                "is_authenticated": bool(user_id),
                "copilot_portal_url": copilot_url,
                "signin_portal_href": signin_url,
                "user_message": (
                    f"Open {portal_info['name']} with the built-in AI Co-Pilot active — whenever you get stuck, "
                    f"click the AI Co-Pilot button to highlight the exact field or complete the step for you."
                ),
            }
            return PortalCopilotOutcome(
                card_type="copilot_launch_banner",
                payload=banner_card,
                grounding_block=f"[PORTAL CO-PILOT LINK AVAILABLE: {copilot_url}]",
                deterministic_reply="",
            )

        merged_extracted = {**history_entities, **intent.extracted_fields}
        card = build_portal_task_card(
            intent.task_id,
            extracted_fields=merged_extracted,
            user_id=user_id,
            authenticated_tin=authenticated_tin,
            session_id=session_id,
            conversation_id=conv_id,
            tenant_id=tenant_id,
        )
        return _outcome_from_card(card)

    # 3. Live DOM Telemetry from Inbuilt Portal Co-Pilot or Browser Companion
    if portal_context and isinstance(portal_context, Mapping) and portal_context.get("portal_id"):
        assistance = analyze_portal_context(
            portal_context,
            user_id=user_id,
            conversation_id=conv_id,
        )
        diag = assistance["diagnosis_summary"]
        action_lines = "\n".join(
            f"{a['step_number']}. **{a['element']}**: {a['tooltip']}"
            for a in assistance["dom_actions"][:5]
        )
        stuck_reply = (
            f"### Inbuilt URA Portal Co-Pilot — {assistance['portal_name']}\n\n"
            f"**Active Step**: {assistance['active_step']}\n"
            f"**Diagnosis**: {diag}\n\n"
            f"**Guided Recovery Steps**:\n{action_lines}"
        )
        grounding = (
            f"[LIVE URA PORTAL DOM TELEMETRY — {assistance['portal_name']}]\n"
            f"Active Step: {assistance['active_step']}\n"
            f"Diagnosis: {diag}\n"
            f"Recommended DOM Actions: {len(assistance['dom_actions'])} interactive step(s) ready."
        )
        return PortalCopilotOutcome(
            card_type="stuck_portal_assistance",
            payload=assistance,
            grounding_block=grounding,
            deterministic_reply=stuck_reply,
        )

    return None


def _outcome_from_card(card: dict[str, Any]) -> PortalCopilotOutcome:
    """Wrap a card dictionary from `build_portal_task_card` into a `PortalCopilotOutcome` with grounded text."""
    ctype = str(card.get("card_type") or "")
    title = str(card.get("title") or "URA Portal Task")
    citation = str(card.get("statutory_citation") or "TPCA Cap. 343")
    portal_url = str(card.get("copilot_portal_url") or "/portal/e_services?copilot=1")

    if ctype == "step_up_login":
        reply = (
            f"### Sign-In Required — {title}\n\n"
            f"{card.get('login_reason')}\n\n"
            f"Use the **Sign in to URA Account & Return Here** button on the card below. "
            f"Once your sign-in is accepted, you will be brought right back here automatically with all your details preserved."
        )
    elif ctype == "access_denied":
        reply = f"### Official Secrecy Protection ({citation})\n\n{card.get('error')}"
    elif ctype == "inline_form":
        missing = ", ".join(card.get("missing_fields") or [])
        reply = (
            f"### Complete Details for {title}\n\n"
            f"Please fill in the form fields directly on the card below"
            f"{f' ({missing})' if missing else ''} and click **{card.get('submit_label', 'Review & Verify Details')}**.\n\n"
            f"You can also [open this screen with the Built-In URA AI Co-Pilot]({portal_url}) so the assistant can guide you or highlight fields directly on the portal."
        )
    elif ctype == "confirmation_required":
        reply = (
            f"### Action Permission Required — {title}\n\n"
            f"**{card.get('verification_prompt')}**\n\n"
            f"Review every parameter on the confirmation card below, then click **Allow & Execute** to proceed, "
            f"**Edit Details** to adjust any field, or **Disallow** to cancel."
        )
    elif ctype == "statutory_copilot_handoff":
        reply = (
            f"### Statutory Co-Pilot Staging — {title}\n\n"
            f"{card.get('verification_warning')}\n\n"
            f"Click **{card.get('open_portal_label')}** below to open the URA portal with the built-in AI Co-Pilot active and your fields pre-staged."
        )
    elif ctype == "executed_result":
        res = card.get("result") or {}
        msg = str(res.get("message") or f"Completed {title} verification.")
        reply = (
            f"### {title}\n\n"
            f"{msg}\n\n"
            f"*Mode: {'Live URA Integration' if card.get('live') else 'Prototype Simulation (`live=False`)'}*"
        )
    else:
        reply = f"### {title}\n\nReady to assist on `{portal_url}`."

    grounding = (
        f"[URA PORTAL CO-PILOT — {title} ({ctype})]\n"
        f"Statutory Basis: {citation}\n"
        f"Assistant Instruction: {reply}"
    )
    return PortalCopilotOutcome(
        card_type=ctype,
        payload=card,
        grounding_block=grounding,
        deterministic_reply=reply,
    )

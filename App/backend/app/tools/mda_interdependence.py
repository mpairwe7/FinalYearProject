"""Cross-MDA Interdependence, NTR/PRN Diagnostics, Life-Event Tax-Base Onboarding & B2G/UESW Clearance Tools (October 2026).

Implements the five pillars closing taxpayer challenges across the 10 Ugandan
Government Bodies (MDAs) with high URA dependency (`MoWT`, `DCIC`, `NIRA`,
`PPDA/MoFPED`, `UNBS`, `MLHUD`, `KCCA/IRAS`, `UPF`, `URSB`, `NDA/NEMA/UESW`):

1. ``resolve_mda_ntr_payment`` (Pillar 1):
   - Disambiguates NTR vs. ONTR vs. Stamp Duty fee heads across all 10 MDAs,
     computes FY2026-27 statutory fees (including live/snapshot USD->UGX
     conversion for foreign-currency immigration/customs fees), diagnoses
     3-hop PRN settlement delays (``Bank/Mobile Money -> URA e-Tax Ledger ->
     MDA Webhook``), and provides exact ``Form DT-3008`` NTR refund steps when
     a taxpayer paid under the wrong head or expired PRN.
2. ``assess_life_event_tax_onboarding`` (Pillar 2):
   - Converts high-volume citizen/MDA life events (KCCA/IRAS trading license,
     MoWT commercial vehicle registration, MLHUD land/property transfer, URSB
     OBRS business registration, B2G tender bidding) into frictionless tax-base
     onboarding (Presumptive Tax ``0%`` below ``UGX 10,000,000`` disclosure,
     Advance Transport Tax, Rental Income Tax) and runs NIRA NIN-to-TIN
     pre-flight name-order/format checks to prevent Instant TIN rejections.
3. ``evaluate_b2g_and_uesw_clearance`` (Pillar 3):
   - Evaluates PPDA ``e-GP`` / MoFPED ``IFMS`` government supplier compliance
     (60-second TCC readiness pre-check, mandatory EFRIS e-invoice FDN, ``6%``
     designated WHT above ``UGX 1,000,000`` cumulative contracts, and Deemed VAT
     on aid-funded projects under VAT Act s.24(7)) and Single-Window (``UESW`` /
     ``ASYCUDA World``) multi-agency cargo holds (``UNBS`` PVoC ``USD 2,000``
     FOB threshold & ``15%`` surcharge, ``NDA`` verification permits, and
     ``NEMA`` ``30%``/``70%`` EIA PRNs & used vehicle environmental levies).
"""

from __future__ import annotations

import re
from typing import Any

from ..tax.tables import get_table
from . import Tool, ToolRegistry, ToolSchema

# ---------------------------------------------------------------------------
# Canonical 10-MDA NTR / Statutory Fee & Touchpoint Catalog (FY2026-27)
# ---------------------------------------------------------------------------

MDA_SERVICE_CATALOG: dict[str, dict[str, Any]] = {
    "mowt_driving_permit": {
        "mda_code": "MDA1_MoWT",
        "mda_name": "Ministry of Works and Transport (UDLS — Uganda Driving Licensing System)",
        "ura_tax_head": "NTR (Non-Tax Revenue — MoWT / Driving Permits)",
        "wrong_head_warning": (
            "Do NOT select 'Stamp Duty' or 'Motor Vehicle Registration' when paying for a driving permit; "
            "select 'NTR' -> 'Ministry of Works and Transport' -> 'Driving Permit Fees'."
        ),
        "statutory_fee_ugx": 130_000.0,
        "statutory_basis": "Traffic and Road Safety Act (Cap. 361) & MoWT UDLS NTR Fee Schedule",
        "webhook_target": "UDLS Biometric Portal (udls.co.ug)",
    },
    "mowt_vehicle_transfer": {
        "mda_code": "MDA1_MoWT",
        "mda_name": "Ministry of Works and Transport & URA Motor Vehicle Registry",
        "ura_tax_head": "Stamp Duty (Motor Vehicle / Motorcycle Transfer)",
        "wrong_head_warning": (
            "Both the seller and buyer must hold active URA TINs, and any unpaid Police EPS traffic "
            "tickets on the vehicle must be cleared before e-Tax permits transfer of ownership."
        ),
        "statutory_fee_ugx": 200_000.0,
        "motorcycle_fee_ugx": 30_000.0,
        "statutory_basis": "Stamp Duty (Amendment) Bill/Act 2026 (FY2026-27): UGX 200,000 motor vehicle; UGX 30,000 motorcycle",
        "webhook_target": "URA e-Tax Motor Vehicle Registry & MoWT Digital Logbook System",
    },
    "dcic_passport": {
        "mda_code": "MDA2_DCIC",
        "mda_name": "Ministry of Internal Affairs — Directorate of Citizenship and Immigration Control (DCIC)",
        "ura_tax_head": "NTR (Ministry of Internal Affairs — Passports)",
        "wrong_head_warning": (
            "Ordinary e-Passport is UGX 250,000; Express e-Passport is UGX 400,000. If you paid UGX 250,000 "
            "and wish to upgrade to Express, generate a top-up PRN on passports.go.ug rather than paying "
            "onto an expired or unlinked PRN."
        ),
        "statutory_fee_ugx": 250_000.0,
        "express_fee_ugx": 400_000.0,
        "statutory_basis": "Uganda Citizenship and Immigration Control Act (Cap. 313) & Fees Regulations 2021 (SI 12 of 2021)",
        "webhook_target": "DCIC e-Passport Portal (passports.go.ug)",
    },
    "dcic_work_permit": {
        "mda_code": "MDA2_DCIC",
        "mda_name": "Ministry of Internal Affairs — Directorate of Citizenship and Immigration Control (DCIC)",
        "ura_tax_head": "NTR (Immigration — Work Permits, Special Passes & Dependant Passes)",
        "wrong_head_warning": (
            "Foreign-currency immigration fees (USD) are automatically converted to UGX on the URA portal "
            "using the official URA exchange rate on the date of PRN generation."
        ),
        "statutory_fee_usd": 2_500.0,
        "statutory_basis": "Uganda Citizenship and Immigration Control (Fees) Regulations 2021 (SI 12 of 2021)",
        "webhook_target": "DCIC e-Immigration Portal (visas.immigration.go.ug)",
    },
    "nira_national_id": {
        "mda_code": "MDA3_NIRA",
        "mda_name": "National Identification and Registration Authority (NIRA)",
        "ura_tax_head": "NTR (NIRA — Replacement / Renewal / Change of Particulars / Certificates)",
        "wrong_head_warning": (
            "First-time National ID registration and initial birth/death registration are FREE. "
            "Replacement of a lost/damaged ID is UGX 50,000; ID renewal/change of particulars under the "
            "revised NIRA fee schedule is up to UGX 300,000."
        ),
        "statutory_fee_ugx": 50_000.0,
        "renewal_fee_ugx": 300_000.0,
        "statutory_basis": "Registration of Persons Act 2015 (Cap. 332) & NIRA Summary of Registration of Persons Fees (2024)",
        "webhook_target": "NIRA Civil Registration System (nira.go.ug)",
    },
    "mlhud_land_transfer": {
        "mda_code": "MDA6_MLHUD",
        "mda_name": "Ministry of Lands, Housing and Urban Development (MLHUD / UgNLIS)",
        "ura_tax_head": "Stamp Duty (Chief Government Valuer Assessment) + ONTR Registration Fee",
        "wrong_head_warning": (
            "Under Tax Procedures Code Act s.135, a URA TIN is mandatory for land transactions above "
            "UGX 10,000,000. Always generate the Stamp Duty PRN against the joint CGV valuation assessment "
            "reference rather than as an unlinked general payment."
        ),
        "stamp_duty_rate": 0.01,
        "tpc_tin_threshold_ugx": 10_000_000.0,
        "statutory_basis": "Registration of Titles Act (Cap. 230), Stamp Duty Act Schedule 2 (property_transfer), & TPC Act s.135",
        "webhook_target": "MLHUD Uganda National Land Information System (UgNLIS)",
    },
    "kcca_iras_trading_license": {
        "mda_code": "MDA7_KCCA_MOLG",
        "mda_name": "Kampala Capital City Authority (KCCA e-Citie) & Local Governments (IRAS)",
        "ura_tax_head": "ONTR (Local Government Trading License) + URA Presumptive / Income Tax TIN Gate",
        "wrong_head_warning": (
            "Trading License fees go to KCCA (e-Citie) or your Municipal/District Council (iras.go.ug), "
            "whereas Presumptive Tax (0% under UGX 10M turnover) goes to URA. Under TPC Act s.135, "
            "KCCA/IRAS cannot issue a Trading License without an active URA TIN."
        ),
        "statutory_fee_ugx": 0.0,
        "statutory_basis": "Trade (Licensing) Act (Cap. 79), Local Governments Act (Cap. 243), & Tax Procedures Code Act s.135",
        "webhook_target": "KCCA e-Citie / Local Government IRAS Portal (iras.go.ug)",
    },
    "upf_eps_traffic_ticket": {
        "mda_code": "MDA8_UPF",
        "mda_name": "Uganda Police Force (UPF — Express Penalty Scheme EPS)",
        "ura_tax_head": "NTR (Uganda Police Force — Express Penalty Scheme EPS)",
        "wrong_head_warning": (
            "EPS traffic tickets must be paid within 28 days of issuance using the 13-digit URA PRN printed "
            "on the ticket; unpaid EPS tickets accrue a 50% surcharge and block motor vehicle transfers on e-Tax."
        ),
        "statutory_fee_ugx": 100_000.0,
        "statutory_basis": "Traffic and Road Safety (Express Penalty Scheme) Regulations 2024 (SI 56 of 2024) & Roads Act 2019 s.71",
        "webhook_target": "UPF EPS-Auto & URA Motor Vehicle Clearance System",
    },
    "ursb_company_registration": {
        "mda_code": "MDA9_URSB",
        "mda_name": "Uganda Registration Services Bureau (URSB — OBRS)",
        "ura_tax_head": "ONTR (URSB Company / Business Name Registration) + Stamp Duty on Nominal Capital",
        "wrong_head_warning": (
            "Every company director/shareholder must hold a verified individual URA TIN (or NIRA NIN) for "
            "OBRS to auto-issue the corporate TIN upon incorporation."
        ),
        "statutory_fee_ugx": 105_000.0,
        "statutory_basis": "Companies Act 2012, URSB Business Names Registration Amendment Rules 2024 (SI 43), & Trademarks Fees 2025",
        "webhook_target": "URSB Online Business Registration System (obrs.ursb.go.ug)",
    },
    "nema_eia_prn": {
        "mda_code": "MDA10_NDA_NEMA_UESW",
        "mda_name": "National Environment Management Authority (NEMA) — URA Portal EIA Integration",
        "ura_tax_head": "ONTR -> National Environment Management Authority (Split 30% Submission & 70% Approval PRNs)",
        "wrong_head_warning": (
            "Per the NEMA Technical Guide on URA Portal PRNs, environmental impact assessment (EIA) fees "
            "must be generated as TWO separate ONTR PRNs: a 30% Submission Fee PRN and a 70% Approval Fee PRN."
        ),
        "submission_split_pct": 0.30,
        "approval_split_pct": 0.70,
        "statutory_basis": "National Environment Act & NEMA Technical Guide on How to Generate 30% & 70% EIA PRNs on URA Portal",
        "webhook_target": "NEMA Environmental Licensing Portal & Uganda Electronic Single Window (UESW)",
    },
}


# ---------------------------------------------------------------------------
# Tool 1 (Pillar 1): Cross-MDA PRN & NTR Fee-Head Validator + 3-Hop Diagnostics
# ---------------------------------------------------------------------------
class ResolveMdaNtrPaymentTool(Tool):
    _schema = ToolSchema(
        name="resolve_mda_ntr_payment",
        description=(
            "Validate Non-Tax Revenue (NTR/ONTR) and Stamp Duty fee heads across Uganda's 10 high-dependency "
            "MDAs (MoWT/UDLS driving permits & vehicle transfers, DCIC passports & work permits, NIRA National IDs, "
            "MLHUD land transfers, KCCA/IRAS trading licenses, UPF EPS traffic tickets, URSB OBRS registration, "
            "NEMA 30%/70% EIA PRNs), diagnose 3-hop PRN settlement delays (Bank/Mobile Money -> URA e-Tax -> MDA "
            "portal), and provide Form DT-3008 refund steps for wrong-head or expired PRN payments."
        ),
        namespace="tax_calculator",
        risk="low",
        read_only=True,
        parameters={
            "type": "object",
            "properties": {
                "service_type": {
                    "type": "string",
                    "enum": list(MDA_SERVICE_CATALOG.keys()),
                    "description": "MDA service being paid for or diagnosed",
                },
                "transaction_value_ugx": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Optional transaction value in UGX (e.g. land CGV value or NEMA total EIA fee)",
                },
                "prn_status": {
                    "type": "string",
                    "enum": ["unpaid_registered", "paid_not_reflecting_at_mda", "expired", "paid_wrong_head"],
                    "description": "Current PRN state experienced by the taxpayer",
                },
            },
            "required": ["service_type"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "required": [
                "ok",
                "mda_code",
                "mda_name",
                "ura_tax_head",
                "payable_ugx",
                "wrong_head_warning",
                "reconciliation_steps",
                "statutory_basis",
                "explanation",
            ],
            "properties": {
                "ok": {"type": "boolean"},
                "mda_code": {"type": "string"},
                "mda_name": {"type": "string"},
                "ura_tax_head": {"type": "string"},
                "payable_ugx": {"type": "number"},
                "split_prns": {"type": "object"},
                "wrong_head_warning": {"type": "string"},
                "reconciliation_steps": {"type": "array", "items": {"type": "string"}},
                "statutory_basis": {"type": "string"},
                "explanation": {"type": "string"},
            },
        },
    )

    @property
    def schema(self) -> ToolSchema:
        return self._schema

    def execute(
        self,
        *,
        service_type: str,
        transaction_value_ugx: float = 0.0,
        prn_status: str = "paid_not_reflecting_at_mda",
    ) -> dict[str, Any]:
        if service_type not in MDA_SERVICE_CATALOG:
            return {
                "ok": False,
                "error": f"Unknown service_type {service_type!r}; expected one of {sorted(MDA_SERVICE_CATALOG)}",
                "mda_code": "",
                "mda_name": "",
                "ura_tax_head": "",
                "payable_ugx": 0.0,
                "wrong_head_warning": "",
                "reconciliation_steps": [],
                "statutory_basis": "",
                "explanation": "",
            }
        info = MDA_SERVICE_CATALOG[service_type]
        tbl = get_table("FY2026-27")
        val = max(0.0, float(transaction_value_ugx or 0.0))

        payable_ugx = float(info.get("statutory_fee_ugx", 0.0))
        split_prns: dict[str, float] = {}

        if service_type == "mlhud_land_transfer":
            rate = float(tbl.get("stamp_duty_property_transfer") or info.get("stamp_duty_rate") or 0.01)
            thresh = float(info.get("tpc_tin_threshold_ugx") or 10_000_000.0)
            payable_ugx = round(val * rate, 2) if val > 0 else 0.0
        elif service_type == "mowt_vehicle_transfer":
            payable_ugx = float(tbl.get("stamp_duty_motor_vehicle_transfer_ugx") or info.get("statutory_fee_ugx") or 200_000.0)
        elif service_type == "ursb_company_registration":
            payable_ugx = float(tbl.get("ursb_company_registration_min_fee_ugx") or info.get("statutory_fee_ugx") or 105_000.0)
        elif service_type == "nema_eia_prn" and val > 0:
            sub_fee = round(val * 0.30, 2)
            app_fee = round(val * 0.70, 2)
            payable_ugx = round(sub_fee + app_fee, 2)
            split_prns = {"submission_prn_30_pct_ugx": sub_fee, "approval_prn_70_pct_ugx": app_fee}
        elif service_type == "dcic_work_permit":
            usd_fee = float(info.get("statutory_fee_usd", 2500.0))
            fx_rate = 3750.0
            try:
                import json as _json
                from ..tax.tables import DATA_DIR

                fx_path = DATA_DIR / "fx" / "BOU_mid_rates_2026.json"
                if fx_path.is_file():
                    fx_data = _json.loads(fx_path.read_text(encoding="utf-8"))
                    usd_entry = (fx_data.get("rates") or {}).get("USD")
                    if isinstance(usd_entry, int | float) and float(usd_entry) > 0:
                        fx_rate = float(usd_entry)
                    elif isinstance(usd_entry, dict) and usd_entry.get("default"):
                        fx_rate = float(usd_entry["default"])
            except Exception:
                fx_rate = 3750.0
            payable_ugx = round(usd_fee * fx_rate, 2)

        if prn_status == "paid_wrong_head":
            steps = [
                "1. Do NOT pay a second time onto the wrong PRN.",
                "2. Generate a fresh 13-digit PRN under the correct tax/NTR head ("
                f"{info['ura_tax_head']}) so your {info['mda_name']} application can proceed.",
                "3. For the erroneous PRN, download manual NTR Refund Form DT-3008 from https://ura.go.ug "
                "(e-Services -> Download Manual Forms -> Domestic -> Refund).",
                "4. Attach the original bank/Mobile Money payment receipt, the new correct PRN receipt, "
                f"and a confirmation letter from {info['mda_name']} stating the wrong PRN was not utilized, "
                "and submit to your nearest URA Domestic Taxes office.",
            ]
        elif prn_status == "expired":
            steps = [
                "1. A URA PRN expires after 21 days if unpaid (status 'X'). Never send Mobile Money or bank "
                "transfers to an expired PRN.",
                "2. Visit https://ura.go.ug -> 'Make a Payment' -> 'Reactivate Expired PRN' (for domestic tax PRNs) "
                f"or re-initiate the payment slip from {info['webhook_target']} to get a valid 13-digit PRN.",
            ]
        else:
            steps = [
                "1. Verify 3-Hop PRN Status on https://ura.go.ug ('Make a Payment' -> 'View Payment Status'): "
                "status 'A' means registered/unpaid; status 'T' means Bank/Mobile Money settled at URA; "
                "status 'R' means reconciled with the MDA.",
                f"2. Once URA shows status 'T' or 'R', open {info['webhook_target']} and click 'Check/Verify Payment Status' "
                "using the exact 13-digit PRN (with no spaces) to pull the URA webhook receipt.",
                "3. If Mobile Money was deducted but URA still shows 'A' after 2 hours, contact the telecom/bank "
                "with the transaction ID to push the stuckaggregator settlement to URA e-Tax.",
            ]

        split_note = (
            f" Split into two ONTR PRNs: 30% Submission Fee = UGX {split_prns['submission_prn_30_pct_ugx']:,.0f} "
            f"and 70% Approval Fee = UGX {split_prns['approval_prn_70_pct_ugx']:,.0f}."
            if split_prns
            else ""
        )
        steps_block = "\n".join(steps)
        explanation = (
            f"{info['mda_name']} ({info['mda_code']}): Select URA head '{info['ura_tax_head']}'. "
            f"Statutory payable amount: UGX {payable_ugx:,.0f}.{split_note}\n\n"
            f"Guardrail: {info['wrong_head_warning']}\n\n"
            f"Reconciliation ({prn_status}):\n{steps_block}\n\n"
            f"(Basis: {info['statutory_basis']})."
        )
        return {
            "ok": True,
            "mda_code": info["mda_code"],
            "mda_name": info["mda_name"],
            "ura_tax_head": info["ura_tax_head"],
            "payable_ugx": payable_ugx,
            "split_prns": split_prns,
            "wrong_head_warning": info["wrong_head_warning"],
            "reconciliation_steps": steps,
            "statutory_basis": info["statutory_basis"],
            "explanation": explanation,
        }


# ---------------------------------------------------------------------------
# Tool 2 (Pillar 2): Life-Event Tax-Base Onboarding & NIRA NIN-TIN Pre-Flight
# ---------------------------------------------------------------------------
class AssessLifeEventTaxOnboardingTool(Tool):
    _schema = ToolSchema(
        name="assess_life_event_tax_onboarding",
        description=(
            "Evaluate mandatory URA TIN requirements under Tax Procedures Code Act s.135 for citizen/MDA "
            "life events (KCCA/IRAS trading license, MoWT commercial vehicle registration, MLHUD land transfer, "
            "URSB OBRS business registration), compute low-burden formalisation tax (e.g. 0% Presumptive Tax "
            "below UGX 10,000,000 turnover or Advance Vehicle Tax), and run a NIRA NIN-to-TIN pre-flight "
            "name-order/format check to prevent Instant TIN rejections."
        ),
        namespace="tax_calculator",
        risk="low",
        read_only=True,
        parameters={
            "type": "object",
            "properties": {
                "life_event": {
                    "type": "string",
                    "enum": [
                        "kcca_iras_trading_license",
                        "commercial_vehicle_registration",
                        "land_or_property_transfer",
                        "ursb_business_registration",
                        "nira_nin_tin_mismatch",
                    ],
                    "description": "Citizen or business life event triggering URA TIN / tax onboarding",
                },
                "annual_turnover_ugx": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Estimated annual gross business turnover in UGX (for presumptive/small business onboarding)",
                },
                "nin": {
                    "type": "string",
                    "maxLength": 32,
                    "description": "Optional 14-character Uganda National Identification Number (CM/CF...) for pre-flight check",
                },
                "stated_full_name": {
                    "type": "string",
                    "maxLength": 120,
                    "description": "Optional full name as typed by the applicant to check surname/given-name order",
                },
            },
            "required": ["life_event"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "required": [
                "ok",
                "life_event",
                "tpc_s135_tin_mandatory",
                "recommended_tax_head",
                "estimated_annual_tax_ugx",
                "nira_preflight",
                "onboarding_steps",
                "statutory_basis",
                "explanation",
            ],
            "properties": {
                "ok": {"type": "boolean"},
                "life_event": {"type": "string"},
                "tpc_s135_tin_mandatory": {"type": "boolean"},
                "recommended_tax_head": {"type": "string"},
                "estimated_annual_tax_ugx": {"type": "number"},
                "nira_preflight": {"type": "object"},
                "onboarding_steps": {"type": "array", "items": {"type": "string"}},
                "statutory_basis": {"type": "string"},
                "explanation": {"type": "string"},
            },
        },
    )

    @property
    def schema(self) -> ToolSchema:
        return self._schema

    def execute(
        self,
        *,
        life_event: str,
        annual_turnover_ugx: float = 0.0,
        nin: str = "",
        stated_full_name: str = "",
    ) -> dict[str, Any]:
        turnover = max(0.0, float(annual_turnover_ugx or 0.0))
        nin_clean = re.sub(r"\s+", "", str(nin or "")).upper()
        nin_valid = bool(re.match(r"^(?:CM|CF)[A-Z0-9]{12}$", nin_clean)) if nin_clean else True
        safe_name = re.sub(r"[^A-Za-z' -]+", " ", str(stated_full_name or "")).strip()[:80]
        name_parts = [p for p in safe_name.split() if p]

        nira_preflight = {
            "nin_provided": bool(nin_clean),
            "nin_format_valid": nin_valid,
            "name_order_guidance": (
                "On the NIRA National ID card, enter SURNAME (family/clan name) in the Surname field and "
                "GIVEN NAME (first/Christian/Muslim name) in the First Name field. Swapping Surname and "
                "Given Name or using a non-NIRA birth date is the #1 cause of Instant TIN rejection."
            ),
            "reORDERED_candidate": (
                f"Surname='{name_parts[-1]}', GivenName='{' '.join(name_parts[:-1])}'"
                if len(name_parts) >= 2
                else ""
            ),
        }

        if life_event == "kcca_iras_trading_license":
            # Presumptive tax calculation (Income Tax Act Cap. 338 Second Schedule marginal bands)
            if turnover <= 10_000_000:
                est_tax = 0.0
                no_rec_tax = 0.0
                band_note = "0% (NIL Presumptive Tax — annual turnover does not exceed UGX 10,000,000)"
            elif turnover <= 30_000_000:
                est_tax = round((turnover - 10_000_000.0) * 0.004, 2)
                no_rec_tax = 80_000.0
                band_note = (
                    f"0.4% of turnover in excess of UGX 10M with records = UGX {est_tax:,.0f}/year "
                    f"(or fixed UGX {no_rec_tax:,.0f}/year without records) on turnover UGX 10M–30M"
                )
            elif turnover <= 50_000_000:
                est_tax = round(80_000.0 + (turnover - 30_000_000.0) * 0.005, 2)
                no_rec_tax = 200_000.0
                band_note = (
                    f"UGX 80,000 + 0.5% of excess over UGX 30M with records = UGX {est_tax:,.0f}/year "
                    f"(or fixed UGX {no_rec_tax:,.0f}/year without records) on turnover UGX 30M–50M"
                )
            elif turnover <= 80_000_000:
                est_tax = round(180_000.0 + (turnover - 50_000_000.0) * 0.006, 2)
                no_rec_tax = 400_000.0
                band_note = (
                    f"UGX 180,000 + 0.6% of excess over UGX 50M with records = UGX {est_tax:,.0f}/year "
                    f"(or fixed UGX {no_rec_tax:,.0f}/year without records) on turnover UGX 50M–80M"
                )
            else:
                capped_t = min(turnover, 150_000_000.0)
                est_tax = round(360_000.0 + (capped_t - 80_000_000.0) * 0.007, 2)
                no_rec_tax = 900_000.0
                band_note = (
                    f"UGX 360,000 + 0.7% of excess over UGX 80M with records = UGX {est_tax:,.0f}/year "
                    f"(or fixed UGX {no_rec_tax:,.0f}/year without records) on turnover UGX 80M–150M"
                )

            rec_head = "Presumptive Income Tax (Small Business) + Instant Individual/Business TIN"
            basis = "Tax Procedures Code Act s.135 (mandatory TIN for local authority licensing) & Income Tax Act Second Schedule"
            steps = [
                "1. Apply for an Instant TIN on https://ura.go.ug using your 14-character NIRA NIN (matching NIRA Surname & DOB).",
                f"2. Presumptive Tax Assurance: {band_note}. Businesses below UGX 10,000,000 annual turnover pay ZERO (UGX 0) URA income tax.",
                "3. Enter your active 10-digit URA TIN on KCCA e-Citie or Local Government IRAS (iras.go.ug) to unlock your Trading License.",
            ]
        elif life_event == "commercial_vehicle_registration":
            est_tax = 50_000.0
            rec_head = "Advance Income Tax on Commercial Transport (UGX 50,000/tonne/year goods or UGX 20,000/seat/year passenger)"
            basis = "Tax Procedures Code Act s.135 & Income Tax Act (Advance Tax on Motor Vehicles)"
            steps = [
                "1. Ensure both buyer/owner and seller hold active 10-digit URA TINs linked to their NIRA NINs.",
                "2. Clear any pending Police Express Penalty Scheme (EPS) traffic PRNs on the vehicle.",
                "3. Pay Motor Vehicle Transfer Stamp Duty (UGX 200,000 vehicle / UGX 30,000 motorcycle) and Advance Transport Tax for commercial PSV/goods vehicles before MoWT logbook issuance.",
            ]
        elif life_event == "land_or_property_transfer":
            sd_rate = float(get_table("FY2026-27").get("stamp_duty_property_transfer") or 0.01)
            est_tax = 0.0
            rec_head = f"Stamp Duty ({sd_rate:.1%} of CGV value) & Rental Income Tax (if leased/commercial property)"
            basis = "Tax Procedures Code Act s.135, Stamp Duty Act 2014, & Income Tax Act s.5 (12% individual rental tax above UGX 2,820,000)"
            steps = [
                "1. Under Tax Procedures Code Act s.135, MLHUD cannot register a land transfer or lease above UGX 10,000,000 without valid URA TINs for both parties.",
                f"2. Obtain Chief Government Valuer (CGV) valuation and generate the {sd_rate:.1%} Stamp Duty PRN on https://ura.go.ug.",
                "3. If the property generates rent above UGX 2,820,000/year, activate the Rental Income Tax obligation (12% on gross rent above UGX 2,820,000 for individuals).",
            ]
        elif life_event == "ursb_business_registration":
            est_tax = 105_000.0
            rec_head = "Non-Individual Corporate TIN (Auto-Issued via URSB OBRS) / Individual Business TIN"
            basis = "Companies Act 2012, URSB Business Names Registration Amendment Rules 2024 (SI 43), & TPC Act s.135"
            steps = [
                "1. Verify every director/subscriber's 14-character NIRA NIN and individual 10-digit URA TIN first.",
                "2. Complete business name or company incorporation on URSB OBRS (obrs.ursb.go.ug) and pay the URSB ONTR PRN (UGX 105,000+).",
                "3. OBRS automatically transmits the incorporation payload to URA e-Tax to issue the company's 10-digit TIN and activate Income Tax / PAYE / EFRIS obligations.",
            ]
        else:
            est_tax = 0.0
            rec_head = "Instant Individual TIN (NIRA NIN Pre-Flight Verification)"
            basis = "Registration of Persons Act 2015 (Cap. 332) & Tax Procedures Code Act s.135"
            steps = [
                "1. Check your physical NIRA National ID card: copy the 14-character NIN (starts with CM for male or CF for female).",
                "2. Enter SURNAME and GIVEN NAME in the exact order printed on the NIRA card (do not swap them) and use the exact NIRA date of birth.",
                "3. If NIRA's record has a spelling or DOB error, apply for a NIRA Change of Particulars before re-submitting on URA e-Tax.",
            ]

        steps_block = "\n".join(steps)
        explanation = (
            f"Life-Event Onboarding ({life_event}): Mandatory URA TIN under TPC Act s.135 = True. "
            f"Recommended Tax Head: {rec_head} (Estimated Annual Tax: UGX {est_tax:,.0f}).\n\n"
            f"NIRA NIN Pre-Flight: {nira_preflight['name_order_guidance']}\n\n"
            f"Steps:\n{steps_block}\n\n"
            f"(Basis: {basis})."
        )
        return {
            "ok": True,
            "life_event": life_event,
            "tpc_s135_tin_mandatory": True,
            "recommended_tax_head": rec_head,
            "estimated_annual_tax_ugx": est_tax,
            "nira_preflight": nira_preflight,
            "onboarding_steps": steps,
            "statutory_basis": basis,
            "explanation": explanation,
        }


# ---------------------------------------------------------------------------
# Tool 3 (Pillar 3): Proactive B2G Supplier & Customs Single-Window Unblocker
# ---------------------------------------------------------------------------
class EvaluateB2gAndUeswClearanceTool(Tool):
    _schema = ToolSchema(
        name="evaluate_b2g_and_uesw_clearance",
        description=(
            "Evaluate B2G government supplier compliance for PPDA e-GP tenders and MoFPED IFMS payments "
            "(60-second Tax Clearance Certificate TCC readiness, mandatory EFRIS e-invoice FDN, 6% designated "
            "WHT above UGX 1,000,000 cumulative contracts, and Deemed VAT on aid-funded projects under VAT Act "
            "s.24(7)) and Single-Window (UESW / ASYCUDA World) customs holds (UNBS PVoC USD 2,000 FOB threshold "
            "& 15% surcharge, NDA drug verification, and NEMA environmental levy)."
        ),
        namespace="tax_calculator",
        risk="low",
        read_only=True,
        parameters={
            "type": "object",
            "properties": {
                "clearance_domain": {
                    "type": "string",
                    "enum": [
                        "ppda_egp_tcc_and_ifms_wht",
                        "aid_funded_deemed_vat",
                        "unbs_pvoc_import_hold",
                        "nda_nema_uesw_import_hold",
                    ],
                    "description": "B2G procurement or UESW customs clearance scenario to evaluate",
                },
                "amount_ugx": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Contract value or CIF value in UGX (optional)",
                },
                "fob_value_usd": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Consignment FOB value in USD for UNBS PVoC threshold evaluation (optional)",
                },
                "has_valid_certificate": {
                    "type": "boolean",
                    "description": "Whether the supplier holds a WHT exemption or UNBS PVoC certificate",
                },
            },
            "required": ["clearance_domain"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "required": [
                "ok",
                "clearance_domain",
                "blocking_agency",
                "wht_or_surcharge_ugx",
                "clearance_checklist",
                "statutory_basis",
                "explanation",
            ],
            "properties": {
                "ok": {"type": "boolean"},
                "clearance_domain": {"type": "string"},
                "blocking_agency": {"type": "string"},
                "wht_or_surcharge_ugx": {"type": "number"},
                "clearance_checklist": {"type": "array", "items": {"type": "string"}},
                "statutory_basis": {"type": "string"},
                "explanation": {"type": "string"},
            },
        },
    )

    @property
    def schema(self) -> ToolSchema:
        return self._schema

    def execute(
        self,
        *,
        clearance_domain: str,
        amount_ugx: float = 0.0,
        fob_value_usd: float = 0.0,
        has_valid_certificate: bool = False,
    ) -> dict[str, Any]:
        tbl = get_table("FY2026-27")
        amt = max(0.0, float(amount_ugx or 0.0))
        fob_usd = max(0.0, float(fob_value_usd or 0.0))

        if clearance_domain == "ppda_egp_tcc_and_ifms_wht":
            wht_thresh = float(tbl.get("wht_threshold") or 1_000_000.0)
            wht_rate = float(tbl.get("withholding_services") or tbl.get("withholding_goods") or 0.06)
            wht_due = 0.0 if has_valid_certificate else (round(amt * wht_rate, 2) if amt > wht_thresh else 0.0)
            agency = "PPDA (e-GP Portal) & MoFPED (IFMS Treasury Payment System)"
            basis = (
                "Public Procurement and Disposal of Public Assets Act, Tax Procedures Code Act s.135 (TCC), "
                "& Income Tax Act s.119(1) (6% WHT above UGX 1,000,000 cumulative contract value)"
            )
            checklist = [
                "1. TCC 60-Second Pre-Check: Ensure all due PAYE, VAT, and Income Tax returns are filed and "
                "any unpaid balance is either settled via PRN or covered by an approved URA instalment agreement.",
                "2. Mandatory EFRIS e-Invoice: Issue a fiscalized EFRIS e-invoice carrying a valid Fiscal Document "
                "Number (FDN); MoFPED IFMS blocks government supplier payments lacking an EFRIS FDN.",
                f"3. 6% Designated WHT Rule: Government MDAs must withhold 6% WHT on cumulative contract payments "
                f"exceeding UGX {wht_thresh:,.0f} (computed WHT on UGX {amt:,.0f} = UGX {wht_due:,.0f} on Form DT-1013 "
                "by the 15th of the following month) unless the supplier holds an active URA WHT Exemption Certificate.",
            ]
        elif clearance_domain == "aid_funded_deemed_vat":
            vat_rate = float(tbl.get("vat_standard") or 0.18)
            wht_due = round(amt * vat_rate, 2)
            agency = "Implementing Government Ministry/MDA & URA Business Policy (Deemed VAT)"
            basis = "Value Added Tax Act s.24(7) & URA Guide to Taxation of Government Agencies"
            checklist = [
                "1. Under VAT Act s.24(7), 18% VAT on taxable supplies made by a contractor to a Government MDA "
                f"solely for an official aid-funded project (computed Deemed VAT = UGX {wht_due:,.0f}) is deemed paid by the MDA.",
                "2. The implementing Ministry writes to the URA Assistant Commissioner Business Policy confirming "
                "project qualification, attaching the signed contract, procurement plan, and progress certificates.",
                "3. The contractor still issues an EFRIS e-invoice under the Deemed VAT mode and declares the "
                "deemed VAT return without paying cash VAT out of pocket.",
            ]
        elif clearance_domain == "unbs_pvoc_import_hold":
            pvoc_fob_min = float(tbl.get("unbs_pvoc_exemption_fob_usd") or 2000.0)
            surcharge_rate = float(tbl.get("unbs_pvoc_evasion_surcharge_rate") or 0.15)
            needs_pvoc = fob_usd == 0.0 or fob_usd > pvoc_fob_min
            wht_due = 0.0 if (has_valid_certificate or not needs_pvoc) else round(amt * surcharge_rate, 2)
            agency = "Uganda National Bureau of Standards (UNBS E-Portal) & URA ASYCUDA World"
            basis = "Uganda National Bureau of Standards Act & UNBS E-Portal ASYCUDA Compulsory Standards Notice FY2026-27"
            checklist = [
                f"1. PVoC FOB Threshold: Consignments with FOB value up to USD {pvoc_fob_min:,.0f} are exempt from mandatory pre-export PVoC.",
                f"2. 15% PVoC Surcharge: Goods above USD {pvoc_fob_min:,.0f} FOB arriving without a valid Certificate of Conformity (CoC) "
                f"incur a 15% CIF surcharge (UGX {wht_due:,.0f} on CIF UGX {amt:,.0f}) plus destination inspection fees.",
                "3. Link your UNBS Import Clearance Certificate (ICC) on https://eportal.unbs.go.ug to your ASYCUDA World SAD entry to release the Customs hold.",
            ]
        else:
            wht_due = 0.0
            agency = "National Drug Authority (NDA NDAMIS/IRIMS) & NEMA on Uganda Electronic Single Window (UESW)"
            basis = "National Drug Policy and Authority Act (Cap. 206) & NEMA Technical Guide on URA Portal EIA PRNs"
            checklist = [
                "1. NDA Regulated Imports: Submit proforma invoice and GMP certificates on NDAMIS/IRIMS (portal.nda.or.ug) "
                "to obtain an electronic Verification Certificate automatically visible in UESW / ASYCUDA World.",
                "2. NEMA Environmental Levy & EIA PRNs: Used motor vehicles aged 5–8 years pay 35% CIF environmental levy "
                "(>8 years pay 50% CIF); EIA project clearances require two separate URA ONTR PRNs (30% submission + 70% approval).",
            ]

        checklist_block = "\n".join(checklist)
        explanation = (
            f"B2G / Single-Window Clearance ({clearance_domain} — {agency}): "
            f"Computed WHT / Surcharge / Deemed Tax = UGX {wht_due:,.0f}.\n\n"
            f"Checklist:\n{checklist_block}\n\n"
            f"(Basis: {basis})."
        )
        return {
            "ok": True,
            "clearance_domain": clearance_domain,
            "blocking_agency": agency,
            "wht_or_surcharge_ugx": wht_due,
            "clearance_checklist": checklist,
            "statutory_basis": basis,
            "explanation": explanation,
        }


# ---------------------------------------------------------------------------
# Tool 4 (Phase 1): First-90-Days New Taxpayer Survival, Nil-Return Shield
# & FY2026-27 VAT Threshold Downgrade / Dormancy Advisor
# ---------------------------------------------------------------------------
class BuildNewTaxpayerComplianceShieldTool(Tool):
    _schema = ToolSchema(
        name="build_new_taxpayer_compliance_shield",
        description=(
            "Generate a Day-1 / First-90-Days compliance profile for new or inactive Ugandan taxpayers: "
            "prevents accidental UGX 200,000/month late-filing penalties on zero-sales TINs via the 60-Second "
            "Nil-Return Shield (TPC Act s.49(1)), distinguishes salaried-only employees and <UGX 10M micro-traders "
            "(no monthly filing), advises businesses with annual turnover between UGX 150M and UGX 300M on "
            "optional VAT deregistration under the FY2026-27 VAT Amendment (threshold raised to UGX 300,000,000), "
            "and guides closing/pausing businesses through formal tax-head dormancy."
        ),
        namespace="tax_calculator",
        risk="low",
        read_only=True,
        parameters={
            "type": "object",
            "properties": {
                "taxpayer_profile": {
                    "type": "string",
                    "enum": [
                        "salaried_employee_only",
                        "micro_trader_under_10m",
                        "new_company_zero_sales",
                        "active_vat_or_paye_business",
                        "vat_registered_150m_to_300m",
                        "closing_or_pausing_business",
                    ],
                    "description": "Taxpayer activity profile or lifecycle stage",
                },
                "annual_turnover_ugx": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Optional annual gross turnover in UGX",
                },
                "unfiled_months": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 120,
                    "description": "Optional number of months a required return was left unfiled",
                },
            },
            "required": ["taxpayer_profile"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "required": [
                "ok",
                "taxpayer_profile",
                "monthly_return_required",
                "nil_return_required_when_zero_sales",
                "non_filing_penalty_exposure_ugx",
                "vat_registration_threshold_ugx",
                "action_plan",
                "statutory_basis",
                "explanation",
            ],
            "properties": {
                "ok": {"type": "boolean"},
                "taxpayer_profile": {"type": "string"},
                "monthly_return_required": {"type": "boolean"},
                "nil_return_required_when_zero_sales": {"type": "boolean"},
                "non_filing_penalty_exposure_ugx": {"type": "number"},
                "vat_registration_threshold_ugx": {"type": "number"},
                "action_plan": {"type": "array", "items": {"type": "string"}},
                "statutory_basis": {"type": "string"},
                "explanation": {"type": "string"},
            },
        },
    )

    @property
    def schema(self) -> ToolSchema:
        return self._schema

    def execute(
        self,
        *,
        taxpayer_profile: str,
        annual_turnover_ugx: float = 0.0,
        unfiled_months: int = 0,
    ) -> dict[str, Any]:
        tbl = get_table("FY2026-27")
        min_pen = float(tbl.get("penal_tax_late_filing_minimum_ugx") or 200_000.0)
        vat_thresh = float(tbl.get("vat_registration_threshold_annual") or 300_000_000.0)
        months = max(0, int(unfiled_months or 0))

        if taxpayer_profile == "salaried_employee_only":
            monthly_req = False
            nil_req = False
            pen_exp = 0.0
            basis = "Income Tax Act (Cap. 338) s.116 & s.128 (Employer PAYE Return is Final Tax for Single Employment)"
            steps = [
                "1. No Monthly Return Needed: If your only income is salary from a single employer who deducts and remits PAYE, that PAYE is a final tax — you do NOT file monthly returns.",
                "2. Secondary Employment / Rental Exception: You only file an individual return if you have two or more employers, business income, or annual gross rental income exceeding UGX 2,820,000.",
            ]
        elif taxpayer_profile == "micro_trader_under_10m":
            monthly_req = False
            nil_req = False
            pen_exp = 0.0
            basis = "Income Tax Act (Cap. 338) Second Schedule (Presumptive Tax Lower Threshold UGX 10,000,000)"
            steps = [
                "1. 0% Tax Below UGX 10,000,000: Resident small businesses with annual gross turnover up to UGX 10,000,000 pay UGX 0 (NIL) Presumptive Income Tax.",
                "2. Keep simple daily sales records so that if your annual turnover grows above UGX 10,000,000, you qualify for the lower record-keeping presumptive rate (0.4% of excess over UGX 10M).",
            ]
        elif taxpayer_profile in ("new_company_zero_sales", "active_vat_or_paye_business"):
            monthly_req = True
            nil_req = True
            eff_months = max(1, months) if months > 0 else 1
            pen_exp = round(min_pen * eff_months, 2)
            basis = (
                "Tax Procedures Code Act (Cap. 343) s.49(1) (Minimum Late-Filing Penalty UGX 200,000 per return) "
                "& VAT / Income Tax Act Return Deadlines (15th of following month)"
            )
            steps = [
                f"1. Critical Day-1 Rule (Nil-Return Shield): Even if your new company or business made ZERO sales (UGX 0) this month/year, leaving an active PAYE, VAT, WHT, Excise, or Income Tax obligation unfiled triggers a mandatory penal tax of UGX {min_pen:,.0f} per unfiled return under TPC Act s.49(1) (exposure for {eff_months} unfiled return(s) = UGX {pen_exp:,.0f}).",
                "2. 60-Second Nil Return Procedure: Log in to https://ura.go.ug -> e-Services -> File a Return -> Monthly/Annual Return -> select the tax head and period -> tick 'Nil Return / Zero Activity' -> Submit before the 15th of the following month (or before the 6th/12th month for Provisional/Final Income Tax).",
                "3. Save the e-Acknowledgement Receipt number; filing a Nil Return costs UGX 0 and keeps your Tax Clearance Certificate (TCC) status 100% green.",
            ]
        elif taxpayer_profile == "vat_registered_150m_to_300m":
            monthly_req = True
            nil_req = True
            pen_exp = round(min_pen * months, 2)
            basis = f"Value Added Tax (Amendment) Act 2026 — Compulsory Annual VAT Threshold Raised to UGX {vat_thresh:,.0f}"
            steps = [
                f"1. FY2026-27 VAT Threshold Relief: Under the Value Added Tax (Amendment) Act 2026, the compulsory annual VAT registration threshold doubled from UGX 150,000,000 to UGX {vat_thresh:,.0f} (UGX 75,000,000 per quarter).",
                f"2. If your annual turnover is below UGX {vat_thresh:,.0f} (stated: UGX {float(annual_turnover_ugx or 0):,.0f}) and you do not need voluntary VAT registration for B2B/B2G input credits, apply on https://ura.go.ug (e-Services -> Registration -> Application for VAT Deregistration) to stop monthly VAT filing obligations.",
                "3. Continue filing monthly VAT returns (including Nil Returns) until URA issues the formal VAT cancellation notice.",
            ]
        else:
            monthly_req = False
            nil_req = False
            pen_exp = round(min_pen * months, 2)
            basis = "Tax Procedures Code Act (Cap. 343) — Deactivation / Temporary Dormancy of Tax Obligations"
            steps = [
                "1. Stop the Penalty Clock Before Closing: Never abandon an active TIN without notifying URA; e-Tax will continue expecting returns and assessing UGX 200,000/month late-filing penalties.",
                "2. File all outstanding returns (or Nil Returns) up to the date business ceased.",
                "3. Submit an 'Amendment of Registration -> Deactivate Tax Head / Temporary Dormancy' request on https://ura.go.ug attaching your closure notice or LC1/trading license cessation letter.",
            ]

        steps_block = "\n".join(steps)
        explanation = (
            f"New/Lifecycle Taxpayer Shield ({taxpayer_profile}): Monthly return required = {monthly_req}; "
            f"Nil return required on zero sales = {nil_req}; Total late-filing penalty exposure = UGX {pen_exp:,.0f} "
            f"(UGX {min_pen:,.0f} per unfiled return) "
            f"(FY2026-27 VAT threshold = UGX {vat_thresh:,.0f}).\n\n"
            f"Action Plan:\n{steps_block}\n\n"
            f"(Basis: {basis})."
        )
        return {
            "ok": True,
            "taxpayer_profile": taxpayer_profile,
            "monthly_return_required": monthly_req,
            "nil_return_required_when_zero_sales": nil_req,
            "non_filing_penalty_exposure_ugx": pen_exp,
            "vat_registration_threshold_ugx": vat_thresh,
            "action_plan": steps,
            "statutory_basis": basis,
            "explanation": explanation,
        }


# ---------------------------------------------------------------------------
# Tool 5 (Phase 2): Voluntary Disclosure (TPC Act s.66(1a)) 100% Penalty/Interest
# Waiver & TCC-Unlocking Instalment Simulator (TPC Act s.40)
# ---------------------------------------------------------------------------
class SimulateTaxReliefAndInstalmentsTool(Tool):
    _schema = ToolSchema(
        name="simulate_tax_relief_and_instalments",
        description=(
            "Simulate Ugandan statutory tax relief for taxpayers with unfiled returns, undeclared tax, or "
            "cash-flow hardship: computes standard late-filing penal tax (TPC Act s.49(1)) and 2%/month "
            "interest (s.39), compares against 100% penalty and interest waiver under Voluntary Disclosure "
            "(TPC Act s.66(1a) / Form DT-10307) when disclosed before a URA audit notice, and builds a "
            "TCC-unlocking monthly Instalment Payment Plan under TPC Act s.40."
        ),
        namespace="tax_calculator",
        risk="low",
        read_only=True,
        parameters={
            "type": "object",
            "properties": {
                "principal_tax_ugx": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Unpaid or undeclared principal tax amount in UGX",
                },
                "months_late": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 120,
                    "description": "Number of months the return or payment is overdue (default 1)",
                },
                "voluntary_before_audit_notice": {
                    "type": "boolean",
                    "description": "True if the taxpayer is disclosing voluntarily before receiving a URA audit notice",
                },
                "instalment_months": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 12,
                    "description": "Number of monthly instalments requested under TPC Act s.40 (1 to 12, default 6)",
                },
            },
            "required": ["principal_tax_ugx"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "required": [
                "ok",
                "principal_tax_ugx",
                "standard_penal_tax_ugx",
                "standard_interest_ugx",
                "total_without_relief_ugx",
                "voluntary_disclosure_payable_ugx",
                "penalty_and_interest_saved_ugx",
                "instalment_plan",
                "statutory_basis",
                "explanation",
            ],
            "properties": {
                "ok": {"type": "boolean"},
                "principal_tax_ugx": {"type": "number"},
                "standard_penal_tax_ugx": {"type": "number"},
                "standard_interest_ugx": {"type": "number"},
                "total_without_relief_ugx": {"type": "number"},
                "voluntary_disclosure_payable_ugx": {"type": "number"},
                "penalty_and_interest_saved_ugx": {"type": "number"},
                "instalment_plan": {"type": "object"},
                "statutory_basis": {"type": "string"},
                "explanation": {"type": "string"},
            },
        },
    )

    @property
    def schema(self) -> ToolSchema:
        return self._schema

    def execute(
        self,
        *,
        principal_tax_ugx: float,
        months_late: int = 1,
        voluntary_before_audit_notice: bool = True,
        instalment_months: int = 6,
    ) -> dict[str, Any]:
        tbl = get_table("FY2026-27")
        principal = max(0.0, float(principal_tax_ugx or 0.0))
        months = max(1, int(months_late or 1))
        inst_m = max(1, min(12, int(instalment_months or 6)))

        pen_rate = float(tbl.get("penal_tax_late_filing_monthly_rate") or 0.02)
        min_pen = float(tbl.get("penal_tax_late_filing_minimum_ugx") or 200_000.0)
        int_rate = float(tbl.get("late_payment_interest_monthly_rate") or 0.02)

        raw_pen = max(min_pen, principal * pen_rate * months) if principal > 0 else min_pen
        # Under TPC Act s.39, accumulated late payment interest is capped so it cannot exceed principal tax
        raw_int = min(principal, principal * int_rate * months) if principal > 0 else 0.0
        penal_tax = round(raw_pen, 2)
        interest = round(raw_int, 2)
        total_no_relief = round(principal + penal_tax + interest, 2)

        if voluntary_before_audit_notice:
            vd_payable = round(principal, 2)
            saved = round(penal_tax + interest, 2)
        else:
            vd_payable = total_no_relief
            saved = 0.0

        down_payment = round(vd_payable * 0.25, 2)
        rem_balance = round(vd_payable - down_payment, 2)
        monthly_instalment = round(rem_balance / inst_m, 2) if inst_m > 0 else rem_balance

        instalment_plan = {
            "initial_down_payment_25pct_ugx": down_payment,
            "remaining_balance_ugx": rem_balance,
            "instalment_months": inst_m,
            "monthly_instalment_ugx": monthly_instalment,
            "unlocks_tcc_upon_down_payment": True,
        }
        basis = (
            "Tax Procedures Code Act (Cap. 343) s.66(1a) (Voluntary Disclosure 100% penalty/interest waiver on "
            "Form DT-10307), s.40 (Instalment Payment Agreements), s.49(1) (Late-filing penalty), & s.39 (Interest cap)"
        )
        if voluntary_before_audit_notice:
            relief_note = (
                f"Under Voluntary Disclosure (TPC Act s.66(1a), Form DT-10307) submitted BEFORE a URA audit notice, "
                f"100% of penal tax and interest is WAIVED — you save UGX {saved:,.0f} and pay ONLY the principal UGX {vd_payable:,.0f}. "
            )
        else:
            relief_note = (
                "Voluntary Disclosure relief under TPC Act s.66(1a) is not available after a URA audit notice; "
                f"the full amount of UGX {vd_payable:,.0f} (principal, penal tax and interest) remains payable. "
            )
        explanation = (
            f"Tax Relief & Instalment Simulation on Principal UGX {principal:,.0f} ({months} month(s) overdue): "
            f"Without relief, Late-Filing Penal Tax = UGX {penal_tax:,.0f} + Late-Payment Interest (2%/month capped at principal) "
            f"= UGX {interest:,.0f} (Total = UGX {total_no_relief:,.0f}). "
            f"{relief_note}"
            f"Under TPC Act s.40 (Instalment Agreement), you can pay a 25% initial down payment (UGX {down_payment:,.0f}) "
            f"plus {inst_m} monthly instalments of UGX {monthly_instalment:,.0f}/month, which immediately unlocks your "
            f"Tax Clearance Certificate (TCC) without queueing at a URA office (Basis: {basis})."
        )
        return {
            "ok": True,
            "principal_tax_ugx": principal,
            "standard_penal_tax_ugx": penal_tax,
            "standard_interest_ugx": interest,
            "total_without_relief_ugx": total_no_relief,
            "voluntary_disclosure_payable_ugx": vd_payable,
            "penalty_and_interest_saved_ugx": saved,
            "instalment_plan": instalment_plan,
            "statutory_basis": basis,
            "explanation": explanation,
        }


# ---------------------------------------------------------------------------
# Tool 6 (Phase 3): Zero-Cost EFRIS Channel Matcher, FY2026-27 5% Consumer
# E-Receipt Refund Calculator & TIN Account Recovery Pre-Packager
# ---------------------------------------------------------------------------
class ResolveEfrisAndAccountRecoveryTool(Tool):
    _schema = ToolSchema(
        name="resolve_efris_and_account_recovery",
        description=(
            "Resolve two major drivers of walk-in queues at URA service centres: "
            "(1) EFRIS onboarding without buying an expensive EFD machine (matching small traders to free URA "
            "Mobile App, Web Portal, or 24-hour Offline Desktop channels) plus computing the FY2026-27 5% "
            "consumer VAT cash refund when 30-day EFRIS e-receipts reach UGX 2,000,000 (VAT Amendment Act 2026 s.7); "
            "and (2) Self-Service & Async Officer Fast-Track TIN Account Recovery when a taxpayer lost the SIM card "
            "or email address tied to their URA portal One-Time Password (OTP)."
        ),
        namespace="tax_calculator",
        risk="low",
        read_only=True,
        parameters={
            "type": "object",
            "properties": {
                "scenario": {
                    "type": "string",
                    "enum": [
                        "efris_free_channel_and_5pct_refund",
                        "lost_sim_or_email_tin_recovery",
                    ],
                    "description": "Whether the taxpayer needs EFRIS channel/refund computation or lost-SIM/email TIN recovery",
                },
                "thirty_day_ereceipt_purchases_ugx": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Total VAT-inclusive EFRIS e-receipts/e-invoices accumulated by a consumer within 30 consecutive days in UGX",
                },
            },
            "required": ["scenario"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "required": [
                "ok",
                "scenario",
                "qualifies_for_5pct_ereceipt_refund",
                "ereceipt_qualifying_threshold_ugx",
                "estimated_vat_refund_ugx",
                "efris_min_penalty_ugx",
                "steps",
                "statutory_basis",
                "explanation",
            ],
            "properties": {
                "ok": {"type": "boolean"},
                "scenario": {"type": "string"},
                "qualifies_for_5pct_ereceipt_refund": {"type": "boolean"},
                "ereceipt_qualifying_threshold_ugx": {"type": "number"},
                "estimated_vat_refund_ugx": {"type": "number"},
                "efris_min_penalty_ugx": {"type": "number"},
                "steps": {"type": "array", "items": {"type": "string"}},
                "statutory_basis": {"type": "string"},
                "explanation": {"type": "string"},
            },
        },
    )

    @property
    def schema(self) -> ToolSchema:
        return self._schema

    def execute(
        self,
        *,
        scenario: str,
        thirty_day_ereceipt_purchases_ugx: float = 0.0,
    ) -> dict[str, Any]:
        tbl = get_table("FY2026-27")
        thresh = float(tbl.get("efris_e_receipt_refund_qualifying_threshold_ugx") or 2_000_000.0)
        ref_rate = float(tbl.get("efris_e_receipt_refund_rate") or 0.05)
        min_pen = float(tbl.get("efris_penal_tax_minimum_ugx") or 200_000.0)
        vat_rate = float(tbl.get("vat_standard") or 0.18)
        purchases = max(0.0, float(thirty_day_ereceipt_purchases_ugx or 0.0))

        qualifies = purchases >= thresh
        # VAT embedded in VAT-inclusive purchases = purchases * (18 / 118); refund = 5% of tax paid
        embedded_vat = purchases * (vat_rate / (1.0 + vat_rate)) if qualifies else 0.0
        refund_ugx = round(embedded_vat * ref_rate, 2) if qualifies else 0.0

        if scenario == "lost_sim_or_email_tin_recovery":
            basis = "Tax Procedures Code Act (Cap. 343) — Amendment of Taxpayer Registration Particulars & NIRA NIN Verification"
            steps = [
                "1. No Need to Queue at a URA Branch: If you lost the phone number (SIM card) or email address that receives your URA e-Tax One-Time Password (OTP), first check if you can perform a SIM swap with MTN/Airtel using your NIRA National ID.",
                "2. If the number cannot be recovered, prepare: (a) your 14-character NIRA National ID (NIN), (b) a newly registered phone number in your own NIN name, and (c) an active email address.",
                "3. Submit an 'Amendment of Registration Particulars (Contact Update)' request via the URA portal or press 'Talk to an officer' right here in chat — your verified NIN slot and conversation history are packaged into an async ticket so a URA officer updates your registered phone/email without a branch visit.",
            ]
            steps_block = "\n".join(steps)
            explanation = (
                f"TIN Account & Lost SIM/Email Recovery:\n{steps_block}\n\n(Basis: {basis})."
            )
        else:
            basis = (
                "Value Added Tax (Amendment) Act 2026 s.7 amending VAT Act s.38 (5% e-receipt refund threshold "
                f"lowered from UGX 5,000,000 to UGX {thresh:,.0f} within 30 days) & Tax Procedures Code (Amendment) "
                f"Act 2026 s.4 substituting s.93 (EFRIS non-issuance minimum penalty 10 currency points = UGX {min_pen:,.0f} "
                "or double the tax due)"
            )
            steps = [
                "1. Zero-Cost EFRIS Channels (No Expensive EFD Machine Required): Small and medium businesses can issue valid EFRIS e-invoices/e-receipts (FDNs) for FREE using the URA Web Portal, the URA Mobile App on any smartphone, or the EFRIS Desktop Client (which supports a 24-hour offline sync window).",
                f"2. FY2026-27 Consumer 5% E-Receipt Refund (VAT Act s.38): Consumers who collect at least UGX {thresh:,.0f} in fiscalized EFRIS e-receipts/e-invoices within 30 consecutive days qualify for a 5% cash refund of the VAT paid (on UGX {purchases:,.0f} VAT-inclusive purchases: Qualifies = {qualifies}, Embedded VAT = UGX {embedded_vat:,.0f}, 5% Refund = UGX {refund_ugx:,.0f}).",
                f"3. Revised FY2026-27 EFRIS Penalty (TPC Act s.93): Failure to issue an EFRIS e-invoice or tamper with an EFD carries a penal tax of double the tax due or 10 currency points (UGX {min_pen:,.0f}), whichever is higher.",
            ]
            steps_block = "\n".join(steps)
            explanation = (
                f"EFRIS Free Onboarding & FY2026-27 5% E-Receipt Refund: Qualifying 30-day threshold = UGX {thresh:,.0f} "
                f"(reduced from UGX 5,000,000); Qualifies on UGX {purchases:,.0f} = {qualifies}; "
                f"Estimated 5% VAT Refund = UGX {refund_ugx:,.0f}; Minimum EFRIS non-issuance penalty = UGX {min_pen:,.0f}.\n\n"
                f"Steps:\n{steps_block}\n\n"
                f"(Basis: {basis})."
            )

        return {
            "ok": True,
            "scenario": scenario,
            "qualifies_for_5pct_ereceipt_refund": qualifies,
            "ereceipt_qualifying_threshold_ugx": thresh,
            "estimated_vat_refund_ugx": refund_ugx,
            "efris_min_penalty_ugx": min_pen,
            "steps": steps,
            "statutory_basis": basis,
            "explanation": explanation,
        }


ToolRegistry.register(ResolveMdaNtrPaymentTool())
ToolRegistry.register(AssessLifeEventTaxOnboardingTool())
ToolRegistry.register(EvaluateB2gAndUeswClearanceTool())
ToolRegistry.register(BuildNewTaxpayerComplianceShieldTool())
ToolRegistry.register(SimulateTaxReliefAndInstalmentsTool())
ToolRegistry.register(ResolveEfrisAndAccountRecoveryTool())

"""MCP Tool for navigating external URA portals, diagnosing screenshots, and collecting data (2026).

Implements the MCP 2026-07-28 specification for external source navigation,
visual screenshot troubleshooting, and structured tax entity extraction.
"""

from __future__ import annotations

import re
from typing import Any

from . import Tool, ToolRegistry, ToolSchema
from .portal_navigator_data import EXTERNAL_URA_PORTALS, _diagnose_portal_state

PORTAL_NAVIGATOR_NAMESPACE = "portal_navigator"

class NavigateExternalPortalTool(Tool):
    """MCP Tool for navigating external URA portals, diagnosing screen captures, and collecting structured data."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="navigate_external_portal",
            description=(
                "Navigate external URA portals (e-Services, EFRIS, ASYCUDA, PRN Payments, TIN Registration) "
                "to diagnose user queries and screenshots from external URA sites. Extracts structured tax entities "
                "(TIN, PRN, amounts, tax heads), identifies screen errors/blockers, and provides step-by-step "
                "navigation instructions with verified portal deep-links."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "extracted_text": {
                        "type": "string",
                        "description": "Text content or OCR from the external portal screen/screenshot.",
                    },
                    "portal_name": {
                        "type": "string",
                        "description": (
                            "Target URA portal if known: 'e_services', 'efris', 'asycuda', "
                            "'prn_payments', 'tin_registration', 'track_status', 'document_authentication', or 'auto'."
                        ),
                        "enum": [
                            "auto",
                            "e_services",
                            "efris",
                            "asycuda",
                            "prn_payments",
                            "tin_registration",
                            "track_status",
                            "document_authentication",
                            "tax_clearance",
                            "motor_vehicle",
                            "objection_appeals",
                            "voluntary_disclosure",
                            "whistle_blow",
                            "dts",
                            "tax_incentives",
                            "get_refund",
                            "stamp_duty",
                            "choose_tax_agent",
                            "export_process",
                            "customs_valuation",
                            "single_customs_territory",
                            "exempt_importation",
                            "aeo",
                            "customs_audits_refunds",
                            "warehousing",
                            "customs_enforcements",
                            "laws_and_acts",
                            "double_taxation_agreements",
                            "case_summary_reports",
                            "court_of_appeal",
                            "debt_collections",
                            "financial_intelligence_authority",
                            "customs_systems",
                        ],
                    },
                    "workflow_intent": {
                        "type": "string",
                        "description": "User intent (e.g. 'generate_prn', 'file_return', 'sync_efris', 'register_tin', 'resolve_error').",
                    },
                    "error_code_or_message": {
                        "type": "string",
                        "description": "Specific error code (e.g. '500', '404') or failure message visible on screen.",
                    },
                    "collect_entities": {
                        "type": "boolean",
                        "description": "Whether to extract and validate structured tax identifiers (default: true).",
                    },
                },
                "required": ["extracted_text"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "portal_metadata": {"type": "object"},
                    "collected_data": {"type": "object"},
                    "screen_diagnostic": {"type": "object"},
                    "navigation_guidance": {"type": "object"},
                    "explanation": {"type": "string"},
                },
                "required": ["ok", "portal_metadata", "collected_data", "navigation_guidance", "explanation"],
            },
            namespace=PORTAL_NAVIGATOR_NAMESPACE,
            risk="low",
            read_only=True,
            destructive=False,
            idempotent=True,
            open_world=True,
            title="Navigate external URA portal",
        )

    def execute(
        self,
        extracted_text: str,
        portal_name: str = "auto",
        workflow_intent: str = "",
        error_code_or_message: str = "",
        collect_entities: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        text = str(extracted_text or "").strip()
        text_lower = text.lower()
        err_msg = str(error_code_or_message or "").strip().lower()

        # 1. Identify portal
        normalized_portal = portal_name
        if portal_name in ("tax_refund", "refund"):
            normalized_portal = "get_refund"
        elif portal_name in ("tax_incentives", "incentives", "exemption", "exemptions"):
            normalized_portal = "tax_incentives"
        elif portal_name in ("motor_vehicle", "vehicle", "vehicles"):
            normalized_portal = "motor_vehicle"
        elif portal_name in ("choose_tax_agent", "tax_agent", "choose_agent", "choose_agents", "tax_agents"):
            normalized_portal = "choose_tax_agent"
        elif portal_name in ("objection_appeals", "objection", "objections", "appeals"):
            normalized_portal = "objection_appeals"
        elif portal_name in ("stamp_duty", "stamp", "stamp_duty_portal"):
            normalized_portal = "stamp_duty"
        elif portal_name in ("tax_clearance", "tcc", "clearance", "ax clearance"):
            normalized_portal = "tax_clearance"
        elif portal_name in ("export_process", "export", "exports", "export_procedures"):
            normalized_portal = "export_process"
        elif portal_name in ("customs_valuation", "valuation", "customs_bonds", "bonds"):
            normalized_portal = "customs_valuation"
        elif portal_name in ("single_customs_territory", "sct", "single_customs"):
            normalized_portal = "single_customs_territory"
        elif portal_name in ("exempt_importation", "exempt_importations", "eaccma_exemptions", "comprehensive_exemptions", "exempt_imports"):
            normalized_portal = "exempt_importation"
        elif portal_name in ("aeo", "authorized_economic_operator", "aeo_program", "aeo_erm"):
            normalized_portal = "aeo"
        elif portal_name in ("customs_audits_refunds", "customs_audits_and_refunds", "customs_audit", "customs_refunds", "duty_drawback", "diplomatic_refunds"):
            normalized_portal = "customs_audits_refunds"
        elif portal_name in ("warehousing", "bonded_warehouse", "customs_warehouse", "public_auction", "private_treaty", "online_auction"):
            normalized_portal = "warehousing"
        elif portal_name in ("customs_enforcements", "customs_enforcement", "enforcements", "customs_anti_smuggling", "prohibited_goods"):
            normalized_portal = "customs_enforcements"
        elif portal_name in ("laws_and_acts", "laws_acts", "laws_acts_regulations", "laws_and_regulations", "legal_policy", "tax_laws"):
            normalized_portal = "laws_and_acts"
        elif portal_name in ("double_taxation_agreements", "double_taxation_agreement", "dta", "dtas", "tax_treaties", "tax_treaty", "delegated_competent_authority", "delegated_competent_authorities"):
            normalized_portal = "double_taxation_agreements"
        elif portal_name in ("case_summary_reports", "case_summery_reports", "case_digest", "ura_case_digest", "tax_cases", "case_summaries"):
            normalized_portal = "case_summary_reports"
        elif portal_name in ("court_of_appeal", "court_of_appeals", "coa", "appellate_tax_judgments"):
            normalized_portal = "court_of_appeal"
        elif portal_name in ("debt_collections", "debt_collection", "debt_recovery", "tax_arrears", "arrears_management", "dcu", "the_debt_collection_function"):
            normalized_portal = "debt_collections"
        elif portal_name in ("financial_intelligence_authority", "fia", "aml_cft", "tax_crimes_risk_assessment", "domestic_tax_evasion_tool", "fia_risk_assessment"):
            normalized_portal = "financial_intelligence_authority"
        elif portal_name in ("customs_systems", "customs_system", "asycuda", "sycuda", "uesw", "single_window", "rects", "nii", "bwims"):
            normalized_portal = "customs_systems"
        elif portal_name in ("health_sector", "medical_sector", "health_and_medical_sector", "health", "medical", "pharmacy", "pharmaceutical"):
            normalized_portal = "health_sector"
        elif portal_name in ("business_formalisation", "business_formalization", "formalisation", "formalization", "formalise_business", "formalize_business"):
            normalized_portal = "business_formalisation"
        elif portal_name in ("oil_and_gas", "oil_gas", "petroleum", "miners_oil_gas", "dealers_oil_gas", "eacop", "petroleum_sector"):
            normalized_portal = "oil_and_gas"
        elif portal_name in ("agriculture_sector", "agriculture", "agri_business", "agribusiness", "crop_farming", "poultry_farming", "floriculture", "agri_inputs", "agro_processing"):
            normalized_portal = "agriculture_sector"
        elif portal_name in ("hospitality_sector", "hotel_sector", "hospitality", "hotels", "hotel_accommodation", "tourism_sector", "restaurants_catering", "recreation_facilities"):
            normalized_portal = "hospitality_sector"
        elif portal_name in ("wholesale_retail_sector", "wholesale_retail", "wholesale", "retail", "general_traders", "small_business_taxpayers", "vat_registered_category"):
            normalized_portal = "wholesale_retail_sector"
        elif portal_name in ("construction_sector", "construction", "construction_companies", "construction_professionals", "civil_engineering", "building_contractors"):
            normalized_portal = "construction_sector"
        elif portal_name in ("manufacturing_sector", "manufacturing", "manufacturer", "manufacturers", "tangible_products", "cosmetics_processing", "steel_sector", "textile_processing", "food_beverages_processing", "investors_guides"):
            normalized_portal = "manufacturing_sector"
        elif portal_name in ("education_sector", "education", "schools_proprietorship", "school_proprietors", "schools", "general_education"):
            normalized_portal = "education_sector"
        elif portal_name in ("mining_sector", "mining", "miners_of_minerals", "mineral_extraction", "quarry_operations", "sand_extractors"):
            normalized_portal = "mining_sector"
        elif portal_name in ("entertainment_sector", "entertainment", "events_companies", "promoter_event_manager", "promoters", "performers_artistes", "production_studios", "public_entertainment"):
            normalized_portal = "entertainment_sector"
        elif portal_name in ("schools_curriculum", "tax_curriculum", "a_level_curriculum", "o_level_curriculum", "schools_tax_curriculum"):
            normalized_portal = "schools_curriculum"
        elif portal_name in ("real_estate_sector", "real_estate", "property_management", "land_dealers", "property_developers", "real_estate_agents", "landlords_real_estate"):
            normalized_portal = "real_estate_sector"
        elif portal_name in ("fishing_sector", "fishing", "fisheries", "fisherman_fishmonger", "fish_processing", "fish_exporting"):
            normalized_portal = "fishing_sector"
        elif portal_name in ("transport_sector", "transport", "passenger_vehicles", "goods_vehicles", "commercial_transport", "tlb_transport"):
            normalized_portal = "transport_sector"
        elif portal_name in ("government_agencies", "government_agency", "mda_taxation", "mdas", "local_governments"):
            normalized_portal = "government_agencies"
        elif portal_name in ("opportunities_portal", "opportunities", "tenders", "procurement_management_system", "pms", "auctions", "online_auctions"):
            normalized_portal = "opportunities_portal"
        elif portal_name in ("research_publications", "research_lab", "research_faqs", "revenue_reports", "corporate_plans"):
            normalized_portal = "research_publications"
        portal_key = normalized_portal if normalized_portal != "auto" else self._detect_portal_key(text_lower, err_msg)
        portal_info = EXTERNAL_URA_PORTALS.get(portal_key, EXTERNAL_URA_PORTALS["e_services"])

        # 2. Extract structured data from external screen
        collected_data = {}
        if collect_entities:
            from ..vision.ocr import (
                extract_dates,
                extract_efris_invoice_numbers,
                extract_prn_numbers,
                extract_tax_heads,
                extract_tin_numbers,
                extract_ugx_amounts,
            )

            collected_data = {
                "tins": extract_tin_numbers(text)[:5],
                "prns": extract_prn_numbers(text)[:5],
                "efris_invoices": extract_efris_invoice_numbers(text)[:5],
                "amounts": extract_ugx_amounts(text)[:5],
                "dates": extract_dates(text)[:5],
                "tax_heads": extract_tax_heads(text)[:5],
            }

        # 3. Diagnose issue & detect screen state
        state, issue, severity, steps = self._diagnose_portal_state(portal_key, text_lower, err_msg)

        # 4. Generate UI hotspots and navigation targets
        ui_targets = [
            {
                "element": "Error / Alert Banner",
                "type": "error" if severity in ("error", "blocker") else "info",
                "instruction": issue if issue else "Review active screen parameters",
            },
            {
                "element": "Primary Action Button",
                "type": "action",
                "instruction": f"Click the relevant action button (e.g. Generate PRN / Validate) on {portal_info['name']}",
            },
        ]

        direct_links = [
            {"label": f"Open {portal_info['name']}", "url": portal_info["canonical_url"]},
        ]
        if portal_key == "prn_payments":
            direct_links.append({"label": "Search PRN Status", "url": "https://portal.ura.go.ug/payment"})
        elif portal_key == "efris":
            direct_links.append({"label": "EFRIS Knowledge & Guides", "url": "https://efris.ura.go.ug"})

        # 5. Build clear conversational explanation for the agent/user
        steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
        explanation = (
            f"**External Portal Navigation & Diagnostic — {portal_info['name']}**\n\n"
            f"• **Screen State**: {state}\n"
            f"• **Status / Alert**: {issue}\n\n"
            f"**Recommended Action Steps**:\n{steps_text}\n\n"
            f"Official Link: [{portal_info['name']}]({portal_info['canonical_url']})"
        )

        return {
            "ok": True,
            "portal_metadata": {
                "key": portal_key,
                "name": portal_info["name"],
                "canonical_url": portal_info["canonical_url"],
                "status": portal_info["status"],
                "category": portal_info["category"],
                "service_scope": portal_info["service_scope"],
            },
            "collected_data": collected_data,
            "screen_diagnostic": {
                "detected_state": state,
                "issue": issue,
                "severity": severity,
            },
            "navigation_guidance": {
                "steps": steps,
                "ui_targets": ui_targets,
                "direct_links": direct_links,
            },
            "explanation": explanation,
        }

    @staticmethod
    def _detect_portal_key(text_lower: str, err_msg: str) -> str:
        combined = f"{text_lower} {err_msg}"
        if any(k in combined for k in ("comprehensive list of exempt importation", "exempt importation", "exempt importations", "exemptions under eaccma", "eaccma fifth schedule", "apc 472", "apc 475", "apc 478", "apc 492", "comprehensive-list-of-exempt-importation")):
            return "exempt_importation"
        if (any(k in combined for k in ("authorized economic operator", "aeo program", "aeo erm", "aeo enterprise risk management", "safe framework", "wco safe", "prospective clients", "processes of attaining an aeo status", "benefits of the aeo program", "eligibility criteria for becoming an aeo", "objectives of the uganda aeo scheme")) or bool(re.search(r"\baeo\b", combined))) and not any(k in combined for k in ("aeoi", "automatic exchange")):
            return "aeo"
        if any(k in combined for k in ("customs audits and refunds", "customs-audits-and-refunds", "customs audit", "customs audits", "duty drawback", "drawback of import duty", "diplomatic refund", "diplomatic refunds", "customs refund", "customs refunds", "fuel refund", "fuel refunds", "form c30", "form c31", "form c33", "form c34", "instalment payment of ura taxes", "section 138", "section 139", "section 143", "section 144", "instalment payment")):
            return "customs_audits_refunds"
        if any(k in combined for k in ("warehousing", "bonded warehouse", "customs warehouse", "public online auction", "online auction", "private treaty", "want of entry", "singlewindow.go.ug/auction", "warehousing of goods", "customs warehousing manual", "im7", "cb6", "verification at owners")):
            return "warehousing"
        if any(k in combined for k in (
            "customs enforcements",
            "customs-enforcements",
            "customs enforcement",
            "transit monitoring unit",
            "transit goods licence",
            "transit goods license",
            "tgl",
            "road user charges",
            "prohibited goods",
            "restricted goods",
            "seizure notice",
            "form c37",
            "request to settle offence",
            "form c35",
            "compounding of offence",
            "goods handled at entebbe airport",
            "passenger baggage",
            "non-intrusive inspection",
            "x-ray radiation",
            "scan empty trucks",
            "used laptops prohibited",
        )):
            return "customs_enforcements"
        if any(k in combined for k in (
            "laws and acts",
            "laws, acts and regulations",
            "laws-and-acts",
            "compendium for various domestic tax laws",
            "east african tax law report",
            "tax appeals tribunals act",
            "convention on mutual administrative assistance in tax matters",
            "automatic exchange of information act",
            "free zones act 2014",
            "duty remission regulations",
            "common external tariff 2022",
            "comesa protocol on rules of origin",
            "anti-money laundering act 2013",
        )):
            return "laws_and_acts"
        if any(k in combined for k in (
            "double taxation agreement",
            "double taxation agreements",
            "double-taxation-agreements",
            "dta",
            "dtas",
            "income tax treaty",
            "delegated competent authorities",
            "delegated competent authority",
            "tax treaty",
            "tax treaties",
            "mutual agreement procedure",
        )):
            return "double_taxation_agreements"
        if any(k in combined for k in (
            "case summary report",
            "case summary reports",
            "case-summery-reports",
            "case digest",
            "ura case digest",
            "compendium eac tax cases",
        )):
            return "case_summary_reports"
        if any(k in combined for k in (
            "court of appeal",
            "celtel uganda ltd",
            "gulindwa paul",
            "civil appeal 22 of 2006",
        )):
            return "court_of_appeal"
        if any(k in combined for k in (
            "debt collection",
            "debt collections",
            "the debt collection function",
            "tax arrears",
            "distress proceedings",
            "agency notice",
            "garnishee",
            "departure prohibition",
            "temporary closure of business premises",
            "memorandum of understanding for instalment",
            "dcu mou",
        )):
            return "debt_collections"
        if any(k in combined for k in (
            "financial intelligence authority",
            "ml.tf risk assessment",
            "tax crimes and proceeds",
            "domestic tax evasion risk assessment",
        )):
            return "financial_intelligence_authority"
        if any(k in combined for k in (
            "customs system",
            "customs systems",
            "asycuda world",
            "asycuda",
            "uganda electronic single window",
            "uesw",
            "bwims",
            "bonded warehouse information management system",
            "touchpoint.ura.go.ug",
            "touchpoint portal",
        )):
            return "customs_systems"
        if any(k in combined for k in ("single customs territory", "sct", "first point of entry", "mombasa port", "dar es salaam port", "mutual recognition of customs clearing agents", "third-party declarant", "third party declarant", "rects free of charge", "sct business process manual", "c9/ c11", "c9/c11", "manifest splitting")):
            return "single_customs_territory"
        if any(k in combined for k in ("customs valuation", "methods of customs valuation", "security bond", "security bonds", "customs bond", "clearing agent", "clearing agents", "motor vehicle value guide", "general goods database", "customs-valuation", "what is customs value", "international rules", "rules for the determination of the customs value", "methods of customs valuation allowed under the acv")):
            return "customs_valuation"
        if any(k in combined for k in ("faqs for the export process", "the export process", "export process", "exporting goods", "export procedures", "how to export", "how to start exporting", "rex system", "prohibited exports", "restricted exports", "main export products", "the-exports-process")):
            return "export_process"
        if any(k in combined for k in ("choose agent", "choose-agent", "choose a tax agent", "licensed dt agent", "licensed customs agent", "licensed import & export", "licensed import/export", "choose-agents")):
            return "choose_tax_agent"
        if any(k in combined for k in ("objection", "appeal", "tax appeals tribunal", "dispute assessment", "penalty reversal", "alternative dispute resolution")) or ("section 24" in combined and "vat" not in combined and "deemed" not in combined) or bool(re.search(r"\btat\b", combined)):
            return "objection_appeals"
        if any(k in combined for k in ("refund", "overpaid tax", "vat refund", "input credit refund", "mda payment", "ontr refund", "ntr refund", "tax-refunds")):
            return "get_refund"
        if any(k in combined for k in (
            "non-tax revenues",
            "non tax revenues",
            "non-tax revenue",
            "non tax revenue",
            "mda fees",
            "ontr payment",
        )) and "refund" not in combined:
            return "non_tax_revenues"
        if any(k in combined for k in (
            "value added tax",
            "vat registration threshold",
            "vat guide",
            "input tax credit",
            "output tax",
            "standard rated supply",
            "zero rated supply",
        )):
            return "vat"
        if any(k in combined for k in (
            "documents required at port of entry",
            "documents at the point of entry",
            "point of entry documents",
            "port of entry documents",
        )):
            return "documents_point_of_entry"
        if any(k in combined for k in (
            "employment income",
            "taxes on employment income",
            "paye return",
            "pay as you earn",
            "benefit in kind",
            "benefits in kind",
            "employment tax",
            "employee benefit",
            "employee benefits",
            "exempt employee",
        )):
            return "employment_income"
        if any(k in combined for k in (
            "gaming tax",
            "betting tax",
            "pool betting",
            "casino operators",
            "sports betting",
            "gaming and pool betting",
            "taxation of gaming",
        )):
            return "gaming_and_pool_betting"
        if any(k in combined for k in (
            "automatic exchange of information",
            "aeoi",
            "common reporting standard",
            "foreign asset voluntary disclosure",
            "foreign asset disclosure",
            "favd",
            "fad form",
            "eoir",
            "aeoi vdp",
        )):
            return "aeoi"
        if any(k in combined for k in (
            "capital gains tax",
            "capital gain",
            "capital gains",
            "taxation of capital gains",
            "disposal of business asset",
        )):
            return "capital_gains"
        if any(k in combined for k in (
            "health and medical sector",
            "health sector",
            "medical sector",
            "herbal shops",
            "herbal shop",
            "pharmacy and drug shops",
            "pharmaceutical manufacturers",
            "hospitals, medical centers and clinics",
            "medical appliances",
            "medicaments",
        )):
            return "health_sector"
        if any(k in combined for k in (
            "business formalisation",
            "business formalization",
            "formalising a business",
            "formalizing a business",
            "steps to formalise",
            "steps to formalize",
            "benefits of formalisation",
            "benefits of formalization",
            "formal business",
        )):
            return "business_formalisation"
        if any(k in combined for k in (
            "oil and gas",
            "oil & gas",
            "petroleum sector",
            "petroleum sector q &a",
            "petroleum sector q&a",
            "miners of oil",
            "dealers in oil",
            "eacop",
            "east african crude oil pipeline",
            "electronic dispenser controller",
            "electronic dispenser controllers",
            "edcs",
            "upstream field services",
            "crude oil projects",
        )) or ("deemed vat" in combined and "aid" not in combined):
            return "oil_and_gas"
        if any(k in combined for k in (
            "agriculture sector",
            "agricultural sector",
            "agribusiness",
            "crop farming",
            "poultry farming",
            "poultry farmer",
            "floriculture",
            "floriculturist",
            "greenhouse",
            "agri-input",
            "agri input",
            "agro-processing",
            "agro processing",
            "horticulture",
            "parent stock",
            "hatching egg",
            "hatching eggs",
        )):
            return "agriculture_sector"
        if any(k in combined for k in (
            "hotel and accommodation",
            "hotel sector",
            "hospitality sector",
            "local hotel tax",
            "uhoa",
            "uganda hotel owners association",
            "recreation facilities",
            "recreational facilities",
            "outside catering",
            "restaurant and outside catering",
            "tourism sector",
            "tour operators",
            "safari vehicles",
            "sightseeing buses",
            "overland truck",
            "hotel logo",
        )):
            return "hospitality_sector"
        if any(k in combined for k in (
            "wholesale and retail",
            "wholesale & retail",
            "overview of wholesale",
            "general traders",
            "general wholesale",
            "wholesale trade",
            "retail trade",
            "vat-registered category",
            "vat registered category",
        )):
            return "wholesale_retail_sector"
        if any(k in combined for k in (
            "construction sector",
            "construction companies",
            "construction professionals",
            "overview of the construction",
            "civil engineering",
            "building contractors",
            "surveying equipment",
            "architects registration",
            "engineers registration",
            "aid-funded project",
            "aid funded project",
        )):
            return "construction_sector"
        if any(k in combined for k in (
            "manufacturing sector",
            "manufacturing of tangible",
            "manufacturer of tangible",
            "manufacturers of tangible",
            "cosmetics processing",
            "steel sector",
            "textile processing",
            "food and beverages processing",
            "food & beverages processing",
            "duty remission",
            "industrial park or free zone",
            "industrial parks or free zones",
            "raw materials for manufacture",
        )):
            return "manufacturing_sector"
        if any(k in combined for k in (
            "education sector",
            "school proprietors",
            "school proprietorship",
            "overview of the education",
            "schools proprietorship",
            "scholastic materials",
            "vocational institute",
            "technical institute",
            "florence agreement",
            "educational services",
            "charitable school",
            "private school",
            "school fees",
            "tuition fees",
        )):
            return "education_sector"
        if any(k in combined for k in (
            "mining sector",
            "overview of the mining",
            "miners of minerals",
            "sand extractors",
            "quarry operations",
            "mineral extraction",
            "mineral royalties",
            "precious metals",
            "precious stones",
            "industrial minerals",
            "mine rehabilitation fund",
            "mining operations",
            "subcontractors in mining",
            "subcontractor in mining",
        )):
            return "mining_sector"
        if any(k in combined for k in (
            "entertainment sector",
            "events companies",
            "promoter event manager",
            "promoters or events",
            "performers and artistes",
            "performers and artists",
            "production studios",
            "public entertainment",
            "non-resident public entertainer",
            "non resident public entertainer",
            "concert tickets",
            "ticket sales vat",
        )):
            return "entertainment_sector"
        if any(k in combined for k in (
            "a-level tax curriculum",
            "o-level tax curriculum",
            "schools tax curriculum",
            "a-level economics resource book",
            "entrepreneurship syllabus",
            "entrepreneurship education textbook",
        )):
            return "schools_curriculum"
        if any(k in combined for k in (
            "real estate sector",
            "overview of real estate",
            "property management",
            "land dealers",
            "land developers",
            "property developers",
            "real estate agents",
            "land lords in construction",
            "unimproved land",
        )):
            return "real_estate_sector"
        if any(k in combined for k in (
            "fishing sector",
            "overview of the fishing",
            "fisherman and fishmonger",
            "fish processing",
            "fish products export",
            "fish exporting",
            "aquaculture inputs",
            "fish eggs, fry",
            "fishmonger",
            "fishmongers",
            "fresh fish",
            "processed fish",
        )):
            return "fishing_sector"
        if any(k in combined for k in (
            "transport sector",
            "overview of the transport",
            "passenger and goods vehicles",
            "transport sector – goods",
            "transport licensing board",
            "tlb unified assessment",
            "nimpandikisa nta omulimo",
            "aircraft operators exemption",
            "foreign transporters exemption",
            "road tractors for semi",
        )):
            return "transport_sector"
        if (
            any(k in combined for k in (
                "government agencies",
                "government agency",
                "taxation of government agencies",
                "designated withholding agents",
                "aid-funded projects",
                "aid funded projects",
                "other non-tax revenue",
                "ontr fees",
            ))
            and "contractor" not in combined
        ):
            return "government_agencies"
        if any(k in combined for k in (
            "procurement management system",
            "pms supplier portal",
            "sourcing suppliers",
            "tender user manuals",
            "auctioning application",
            "auction platform for ura assets",
            "ura opportunities",
        )):
            return "opportunities_portal"
        if any(k in combined for k in (
            "ura research lab",
            "research faqs",
            "anonymised tax data",
            "anonymized tax data",
            "revenue performance reports",
            "corporate plans",
            "client satisfaction survey report",
            "strategic plan fy2025/26",
        )):
            return "research_publications"
        if any(k in combined for k in (
            "corporation tax",
            "corporate tax",
            "company tax",
            "company income tax",
            "corporate income tax",
        )):
            return "corporation_tax"
        if any(k in combined for k in (
            "business records",
            "record keeping",
            "keeping business records",
            "section 15 tpca",
            "rekod me biacara",
        )):
            return "business_records"
        if any(k in combined for k in (
            "help tool",
            "help-tool",
            "touchpoint",
            "touchpoint help",
            "book an appointment",
            "book appointment",
            "customer care",
            "toll free",
            "toll-free",
        )):
            return "help_tool"
        if any(k in combined for k in (
            "taxation handbook",
            "tax handbook",
            "a guide to taxation in uganda",
            "8th edition",
            "handbook archives",
        )):
            return "taxation_handbook"
        if any(k in combined for k in (
            "small business",
            "presumptive tax",
            "presumptive taxpayer",
            "taxes on small businesses",
            "small business taxpayer",
            "presumptive return",
        )):
            return "small_business"
        if any(k in combined for k in (
            "rental income tax",
            "rental tax",
            "rental income",
            "landlord tax",
            "property owner tax",
            "provisional rental return",
        )):
            return "rental_income_tax"
        if any(k in combined for k in (
            "taxpayer starter pack",
            "starter pack",
            "taxpayer registration starter pack",
            "starter pack brochure",
        )):
            return "taxpayer_starter_pack"
        if any(k in combined for k in ("incentive", "tax holiday", "free zone", "industrial park", "wht exemption", "exemption list", "withholding agent", "investors guide", "tax waiver", "tujenge", "tujenge pack", "section 47b")):
            return "tax_incentives"
        if any(k in combined for k in ("motor vehicle", "vehicle", "logbook", "number plate", "vanity plate", "tr vii")):
            return "motor_vehicle"
        if any(k in combined for k in ("stamp duty", "transfer of land", "property transfer", "debenture", "mortgage", "stamp certificate", "bulk assessment")):
            return "stamp_duty"
        if any(k in combined for k in ("tcc", "tax clearance", "ax clearance", "clearance certificate")):
            return "tax_clearance"
        if any(k in combined for k in ("track", "application status", "check status", "tracking")):
            return "track_status"
        if any(k in combined for k in ("authenticate", "verify document", "document authentication", "check if genuine", "verify tcc", "certificate genuine")):
            return "document_authentication"
        if any(k in combined for k in ("voluntary disclosure", "section 66", "waiver of penalty", "waiver of interest")):
            return "voluntary_disclosure"
        if any(k in combined for k in ("whistle", "informer", "informant", "report evasion", "smuggling")):
            return "whistle_blow"
        if any(k in combined for k in ("dts", "digital tax stamp", "stamp activation", "kakasa")):
            return "dts"
        if "efris" in combined or "fiscal receipt" in combined:
            return "efris"
        if "asycuda" in combined or "customs" in combined or "bill of entry" in combined:
            return "asycuda"
        if "registration" in combined and "tin" in combined or "nira" in combined or "get a tin" in combined:
            return "tin_registration"
        if "prn" in combined or "payment" in combined or "bank" in combined:
            return "prn_payments"
        return "e_services"

    _diagnose_portal_state = staticmethod(_diagnose_portal_state)

ToolRegistry.register(NavigateExternalPortalTool())

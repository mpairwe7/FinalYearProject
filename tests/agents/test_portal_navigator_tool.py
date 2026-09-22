"""Tests for the MCP navigate_external_portal tool (2026)."""

from __future__ import annotations

import pytest

from app.tools import ToolRegistry
from app.tools.portal_navigator import (
    EXTERNAL_URA_PORTALS,
    NavigateExternalPortalTool,
)


def test_portal_navigator_tool_is_registered():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None
    assert type(tool).__name__ == "NavigateExternalPortalTool"
    assert tool.schema.name == "navigate_external_portal"
    assert tool.schema.namespace == "portal_navigator"
    assert tool.schema.risk == "low"
    assert tool.schema.read_only is True
    assert tool.schema.open_world is True


def test_portal_navigator_mcp_descriptor():
    tools = ToolRegistry.mcp_tools("portal_navigator")
    assert len(tools) == 1
    t = tools[0]
    assert t["name"] == "navigate_external_portal"
    assert "outputSchema" in t
    assert t["annotations"]["openWorldHint"] is True
    assert t["annotations"]["readOnlyHint"] is True


def test_e_services_prn_error_diagnosis_and_data_collection():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    screen_text = (
        "URA e-Services Portal\n"
        "Generate PRN - Domestic Taxes\n"
        "Taxpayer TIN: 1009876543\n"
        "Assessment Amount: UGX 1,500,000\n"
        "Error: Mandatory payment mode selection missing\n"
    )

    res = tool.execute(
        extracted_text=screen_text,
        error_code_or_message="Mandatory payment mode selection missing",
    )

    assert res["ok"] is True
    assert "PRN" in res["portal_metadata"]["name"]
    assert res["portal_metadata"]["canonical_url"] == "https://portal.ura.go.ug/payment"

    # Data collection verification
    assert "1009876543" in res["collected_data"]["tins"]

    # Diagnostics & resolution steps
    assert res["screen_diagnostic"]["severity"] == "warning"
    steps = res["navigation_guidance"]["steps"]
    assert len(steps) >= 3
    assert any("Payment Mode" in s or "bank" in s.lower() for s in steps)

    # UI targets & direct link
    assert len(res["navigation_guidance"]["ui_targets"]) >= 2
    assert len(res["navigation_guidance"]["direct_links"]) >= 1
    assert "portal.ura.go.ug" in res["navigation_guidance"]["direct_links"][0]["url"]


def test_efris_offline_sync_issue_detection():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    screen_text = (
        "EFRIS Invoicing Terminal - efris.ura.go.ug\n"
        "System Alert: Offline synchronization window exceeded (24 hours)\n"
        "Fiscal device blocked until pending vouchers are synchronized.\n"
    )

    res = tool.execute(extracted_text=screen_text, portal_name="efris")

    assert res["ok"] is True
    assert "EFRIS" in res["portal_metadata"]["name"]
    assert res["portal_metadata"]["canonical_url"] == "https://efris.ura.go.ug"
    assert res["screen_diagnostic"]["severity"] == "blocker"
    assert any("Offline Sync" in s for s in res["navigation_guidance"]["steps"])


def test_http_500_server_fault_resolution():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    res = tool.execute(
        extracted_text="portal.ura.go.ug - Internal Server Error",
        error_code_or_message="HTTP 500 Internal Server Error",
    )

    assert res["ok"] is True
    assert res["screen_diagnostic"]["severity"] == "error"
    steps = res["navigation_guidance"]["steps"]
    assert any("refresh" in s.lower() or "ctrl+f5" in s.lower() for s in steps)
    assert any("incognito" in s.lower() or "private" in s.lower() for s in steps)


def test_tin_registration_portal_navigation_matches_faq_knowledge_base():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    screen_text = (
        "URA Web Portal — portal.ura.go.ug\n"
        "Taxpayer Registration > Instant TIN\n"
        "Requirements: Valid NIN registered with NIRA\n"
    )

    res = tool.execute(
        extracted_text=screen_text,
        portal_name="tin_registration",
        workflow_intent="register_tin",
    )

    assert res["ok"] is True
    assert "Taxpayer Registration" in res["portal_metadata"]["name"]
    assert "https://portal.ura.go.ug" in res["portal_metadata"]["canonical_url"]

    steps = res["navigation_guidance"]["steps"]
    # Check consistency with ura_instant_tin_application_faqs.csv
    # Q: "How do I apply for an instant TIN?"
    # A: "Go to ura.go.ug → click Get a TIN → choose Instant TIN → select Individual → enter NIN and personal details → confirm you are not a robot → submit."
    assert any("Get a TIN" in s or "Instant TIN" in s for s in steps)
    assert any("NIN" in s for s in steps)
    assert any("robot" in s.lower() or "captcha" in s.lower() for s in steps)
    assert any("free" in s.lower() for s in steps)
    assert any("5 minutes" in s or "sms" in s.lower() for s in steps)


def test_search_tin_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None
    res = tool.execute(extracted_text="e-Services > Search TIN by National ID NIN")
    assert res["ok"] is True
    assert "Search & Verify TIN" in res["screen_diagnostic"]["detected_state"]
    steps = res["navigation_guidance"]["steps"]
    assert any("Search TIN" in s for s in steps)
    assert any("NIN" in s for s in steps)


def test_print_tin_submitted_forms_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None
    res = tool.execute(extracted_text="Taxpayer Registration > Print TIN Submitted Forms using Application Reference Number")
    assert res["ok"] is True
    assert "Print TIN Submitted Forms" in res["screen_diagnostic"]["detected_state"]
    steps = res["navigation_guidance"]["steps"]
    assert any("Search Number" in s or "Reference Number" in s for s in steps)
    assert any("Print" in s for s in steps)


def test_non_resident_digital_services_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None
    res = tool.execute(extracted_text="e-Services > Non-Resident Digital Services Registration (DST & VAT)")
    assert res["ok"] is True
    assert "Non-Resident Digital" in res["screen_diagnostic"]["detected_state"]
    steps = res["navigation_guidance"]["steps"]
    assert any("Digital Services" in s or "DST" in s for s in steps)


def test_group_tin_and_non_individual_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None
    res_group = tool.execute(extracted_text="e-Services > Taxpayer Registration > Group TIN for SACCO")
    assert res_group["ok"] is True
    assert "Group TIN" in res_group["screen_diagnostic"]["detected_state"]

    res_non_ind = tool.execute(extracted_text="e-Services > Taxpayer Registration > Non-Individual Company with URSB")
    assert res_non_ind["ok"] is True
    assert "Non-Individual" in res_non_ind["screen_diagnostic"]["detected_state"]
    steps = res_non_ind["navigation_guidance"]["steps"]
    assert any("URSB" in s for s in steps)
    assert any("Form 20" in s or "Certificate" in s for s in steps)


def test_track_application_status_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None
    res = tool.execute(extracted_text="e-Services > Track Application Status with Application Search Number")
    assert res["ok"] is True
    assert "Track Application Status" in res["screen_diagnostic"]["detected_state"]
    steps = res["navigation_guidance"]["steps"]
    assert any("Search Number" in s or "Reference Number" in s for s in steps)
    assert any("Track Application Status" in s for s in steps)


def test_document_authentication_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None
    res = tool.execute(extracted_text="e-Services > Document Authentication to verify Tax Clearance Certificate TCC")
    assert res["ok"] is True
    assert "Document Authentication" in res["screen_diagnostic"]["detected_state"]
    steps = res["navigation_guidance"]["steps"]
    assert any("Certificate Number" in s or "Reference Number" in s for s in steps)
    assert any("Document Authentication" in s for s in steps)


def test_file_tax_return_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None
    res = tool.execute(extracted_text="portal.ura.go.ug > e-Services > Returns > File a Tax Return")
    assert res["ok"] is True
    assert "File a Tax Return" in res["screen_diagnostic"]["detected_state"]
    steps = res["navigation_guidance"]["steps"]
    assert any("File a Return" in s for s in steps)
    assert any("upload" in s.lower() or "template" in s.lower() for s in steps)
    assert any("Acknowledgement" in s or "Receipt" in s for s in steps)


def test_download_online_return_forms_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None
    res = tool.execute(extracted_text="e-Services > Returns > Download Online Return Forms (Excel Macro Template DT-1001)")
    assert res["ok"] is True
    assert "Download Online Return Forms" in res["screen_diagnostic"]["detected_state"]
    steps = res["navigation_guidance"]["steps"]
    assert any("Download Online Return" in s for s in steps)
    assert any("macro" in s.lower() for s in steps)
    assert any("Validate" in s for s in steps)


def test_download_manual_return_forms_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None
    res = tool.execute(extracted_text="e-Services > Returns > Download Manual Return Forms for Presumptive Tax")
    assert res["ok"] is True
    assert "Download Manual Return Forms" in res["screen_diagnostic"]["detected_state"]
    steps = res["navigation_guidance"]["steps"]
    assert any("Download Manual Return" in s for s in steps)
    assert any("Presumptive" in s for s in steps)


def test_it_dst_return_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None
    res = tool.execute(extracted_text="e-Services > Returns > File IT-DST Return for Non-Resident Service Providers")
    assert res["ok"] is True
    assert "IT-DST" in res["screen_diagnostic"]["detected_state"]
    steps = res["navigation_guidance"]["steps"]
    assert any("IT-DST" in s for s in steps)
    assert any("5%" in s for s in steps)


def test_efris_comprehensive_sub_paths_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        ("efris first time registration with EFD", "First Time Registration", "efris"),
        ("efris Invoices Management create invoice", "Invoice & Receipt Issuance", "20-digit"),
        ("efris Stock Management Stock In Details", "Stock & Inventory Management", "Commodity Code"),
        ("efris User Management Sub-Accounts cashier", "Cashier & Branch Sub-Account", "Cashier"),
        ("efris Credit Note Apply for Credit Note FDN", "Credit Note & Debit Note", "statutory reason"),
        ("efris FDN Validation check 20-digit FDN", "Fiscal Document Number (FDN) Validation", "Verification Code"),
        ("efris Terminal Management configure EFD printer Z-Report", "Electronic Fiscal Device", "Z-Report"),
        ("efris Reports Management download Z-Reports and VAT summary", "Financial & VAT Audit Reports", "Reports Management"),
        ("efris Accredited Integrators ERP API sandbox", "Accredited Software Integrators", "Sandbox"),
        ("efris Handbook and User Manuals brochure", "Handbook, User Manuals", "User Guide"),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="efris")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])



def test_health_sector_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Health and medical sector guide for hospitals and pharmaceutical manufacturers",
            "Health and Medical Sector Taxation Guide",
            "UCMB, UPMB",
        ),
        (
            "Herbal shops NDA operational license and URSB registration",
            "Health and Medical Sector Taxation Guide",
            "Herbal shops",
        ),
        (
            "Pharmacy and drug shops 1.5 km rule and licensing",
            "Health and Medical Sector Taxation Guide",
            "Class C OTC",
        ),
        (
            "Medical appliances and medicaments exemptions under health and medical sector guide",
            "Health and Medical Sector Taxation Guide",
            "EACCMA Fifth Schedule",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_business_formalisation_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Steps to formalise a business with URSB, URA TIN and municipal trading license",
            "Business Formalisation Guide",
            "3-Step Formalisation Roadmap",
        ),
        (
            "13 core benefits of formalisation including commercial bank loans",
            "Business Formalisation Guide",
            "13 Core Commercial Benefits",
        ),
        (
            "First time login account activation after business formalisation with 10-digit TIN",
            "Business Formalisation Guide",
            "January@2030",
        ),
        (
            "Download formalising a business guide booklet FY 2024-25",
            "Business Formalisation Guide",
            "BUSINESS-FORMALISATION-ENGLISH-FY-2024-25.pdf",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])



def test_vat_guide_thresholds_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Value added tax VAT registration threshold 300 million and EFRIS",
            "Value Added Tax (VAT) Guide & Thresholds",
            "UGX 300,000,000",
        ),
        (
            "Value Added Tax VAT guide standard rated supplies and zero rated exports",
            "Value Added Tax (VAT) Guide & Thresholds",
            "Form DT-1014",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_non_tax_revenues_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Non-tax revenues NTR and ONTR payment registration for driving permits and passports",
            "Non-Tax Revenues (NTR & ONTR Collections)",
            "UDLS driving permits",
        ),
        (
            "Paying non tax revenue fees for MDA services and foreign currency conversion",
            "Non-Tax Revenues (NTR & ONTR Collections)",
            "daily exchange rate",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_documents_at_point_of_entry_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Documents required at port of entry for customs import clearance",
            "Documents Required at Point of Entry",
            "Bill of Lading",
        ),
        (
            "Point of entry documents for international travelers arriving at Entebbe",
            "Documents Required at Point of Entry",
            "Yellow Fever Vaccination",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_employment_income_paye_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Taxes on employment income PAYE guide Section 19 ITA benefits in kind",
            "Employment Income & PAYE Guide",
            "Section 19 ITA",
        ),
        (
            "Exempt employee benefits medical expenses and life insurance",
            "Employment Income & PAYE Guide",
            "medical expense reimbursements",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_gaming_pool_betting_tax_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Gaming and pool betting tax rates 30% and NLGRB licensing",
            "Gaming, Pool Betting, Casino & Sports Betting Tax",
            "NLGRB",
        ),
        (
            "Sports betting and casino tax weekly return submission by Wednesday",
            "Gaming, Pool Betting, Casino & Sports Betting Tax",
            "Wednesday",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_aeoi_foreign_asset_disclosure_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Automatic exchange of information AEOI foreign asset voluntary disclosure FAD form",
            "Automatic Exchange of Information",
            "125 partner jurisdictions",
        ),
        (
            "Declare offshore bank accounts under foreign asset voluntary disclosure FAVD",
            "Automatic Exchange of Information",
            "aeoi_inquiries@ura.go.ug",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_capital_gains_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Taxation of capital gains on disposal of business asset land and commercial buildings",
            "Taxation of Capital Gains",
            "Section 18 ITA",
        ),
        (
            "Capital gains tax calculation with inflation indexation CPID CPIA",
            "Taxation of Capital Gains",
            "CPID / CPIA",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_corporation_tax_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Corporation tax guide for companies chargeable income net profit 30%",
            "Corporation Tax Guide & Return Filing",
            "Form DT-1001",
        ),
        (
            "Company income tax worldwide taxation resident company turnover",
            "Corporation Tax Guide & Return Filing",
            "UGX 500 million",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_business_records_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Business records keeping statutory requirements Section 15 TPCA in English",
            "Business Records & Statutory Bookkeeping",
            "Section 15 TPCA",
        ),
        (
            "Record keeping for expenses above 5 million requiring seller TIN",
            "Business Records & Statutory Bookkeeping",
            "UGX 5,000,000",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_small_business_presumptive_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Taxes on small businesses presumptive taxpayer turnover UGX 30 million",
            "Small Business (Presumptive) Taxpayers",
            "UGX 10M–150M",
        ),
        (
            "Presumptive tax rates with and without records under Section 4 Income Tax Act",
            "Small Business (Presumptive) Taxpayers",
            "Small-Business-Taxpayer-FY-2026-27.pdf",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_rental_income_tax_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Rental income tax calculation for individual landlord threshold 2,820,000",
            "Rental Income Tax Guide & Filing",
            "UGX 2,820,000",
        ),
        (
            "Corporate rental tax return filing with 50% expense cap",
            "Rental Income Tax Guide & Filing",
            "RENTAL-INCOME-TAX-2026-27.pdf",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_taxation_handbook_archives_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Taxation handbook archives 8th edition 2025-26 download",
            "Taxation Handbook Archives",
            "8th Edition",
        ),
        (
            "A guide to taxation in uganda schools curriculum and tax amendments booklet",
            "Taxation Handbook Archives",
            "package ID 66867",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_touchpoint_help_tool_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "URA help tool touchpoint customer care and appointment booking",
            "Touchpoint & Help Tool: Customer Support Channels",
            "0800 117 000",
        ),
        (
            "Book an appointment with URA officer at Nakawa headquarters",
            "Touchpoint & Help Tool: Customer Support Channels",
            "book-an-appointment",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])



def test_tcc_application_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None
    res = tool.execute(extracted_text="Tax Clearance > Apply for Tax Clearance Certificate TCC for bidding")
    assert res["ok"] is True
    assert "Tax Clearance" in res["screen_diagnostic"]["detected_state"]
    steps = res["navigation_guidance"]["steps"]
    assert any("Tax Clearance" in s for s in steps)
    assert any("compliance" in s.lower() or "ledger" in s.lower() for s in steps)


def test_motor_vehicle_transfer_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None
    res = tool.execute(extracted_text="Motor Vehicle > Transfer of Ownership to new buyer TIN")
    assert res["ok"] is True
    assert "Transfer of Ownership" in res["screen_diagnostic"]["detected_state"]
    steps = res["navigation_guidance"]["steps"]
    assert any("Transfer" in s for s in steps)
    assert any("buyer" in s.lower() for s in steps)


def test_objection_and_appeals_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None
    res = tool.execute(extracted_text="Objections and Appeals > Lodge Objection to tax assessment Section 24")
    assert res["ok"] is True
    assert "Objection" in res["screen_diagnostic"]["detected_state"]
    steps = res["navigation_guidance"]["steps"]
    assert any("45 days" in s for s in steps)
    assert any("90 days" in s for s in steps)


def test_voluntary_disclosure_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None
    res = tool.execute(extracted_text="e-Services > Voluntary Disclosure Program under Section 66 TPCA")
    assert res["ok"] is True
    assert "Voluntary Disclosure" in res["screen_diagnostic"]["detected_state"]
    steps = res["navigation_guidance"]["steps"]
    assert any("100% waiver" in s or "waiver" in s.lower() for s in steps)


def test_all_domestic_taxes_services_dispatch():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    paths = [
        ("whistle_blow", "Report tax evasion on touchpoint informer", "Informer & Whistleblower"),
        ("dts", "Digital tax stamp order for beer and spirits", "Digital Tax Stamps"),
        ("tax_incentives", "Apply for tax holiday in industrial park Section 21", "Tax Incentives"),
        ("get_refund", "Apply for VAT input tax refund Section 42", "Tax Refund Application"),
        ("stamp_duty", "Stamp duty assessment on land transfer", "Stamp Duty Assessment"),
        ("choose_tax_agent", "Appoint tax agent on portal", "Tax Agent Appointment"),
    ]

    for p_name, text, expected_state in paths:
        r = tool.execute(extracted_text=text, portal_name=p_name)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert len(r["navigation_guidance"]["steps"]) >= 3


def test_make_a_payment_comprehensive_sub_paths_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        ("Make a Payment Generate a Payment Slip PRN", "Generate a Payment Slip", "12-digit PRN"),
        ("Make a Payment Print a Payment Slip reprint barcode", "Print a Payment Slip", "barcode"),
        ("Make a Payment Pay with VISA or Master Card online checkout", "Pay with VISA or Master Card", "3D-Secure"),
        ("Make a Payment Reactivate Expired PRN validity extension", "Reactivate Expired PRN", "21 days"),
        ("Make a Payment Generate Payment Slip for Park User Fees UWA", "Park User Fees (UWA)", "Uganda Wildlife Authority"),
        ("Make a Payment Print Income Tax Certificate clearance", "Print Income Tax Certificate", "Income Tax Certificate"),
        ("Make a Payment View Payment Status real-time clearance", "View Payment Status", "Bank Transaction Reference"),
        ("Make a Payment Verify Advance Income Tax Payment motor vehicle", "Verify Advance Income Tax Payment", "commercial"),
        ("Make a Payment Pay For Hospital Fees Mulago Butabika", "Pay For Hospital Fees", "Mulago"),
        ("Make a Payment Download Manual Payment Forms deposit slip", "Download Manual Payment Forms", "Bank Payment Vouchers"),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="prn_payments")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_dts_comprehensive_sub_paths_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        ("dts registration for new manufacturer plant", "DTS Registration", "SICPA"),
        ("affix and activate digital tax stamps on packaging line", "Affix & Activate", "Production Line Controller"),
        ("download manual forms for damaged stamp declaration", "Download Manual DTS Forms", "DT 1019"),
        ("gazetted item catalogue for beer water cement", "Gazetted Item Catalogue", "9 mandatory gazetted"),
        ("digital tax stamps penalties and compliance overview", "Digital Tax Stamps (DTS) Overview", "Section 73B TPCA"),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="dts")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_tax_refund_comprehensive_sub_paths_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Tax Refund Payment made to Ministry, Department or Agency (MDAs)",
            "Payment made to Ministry, Department or Agency (MDAs)",
            "Clearance Endorsement Letter",
        ),
        (
            "Tax Refund Motor Vehicle or Stamp Duty or Driving Permit",
            "Motor Vehicle, Stamp Duty & Driving Permit",
            "chassis",
        ),
        (
            "Import or Export Tax Refund duty drawback under EACCMA",
            "Import & Export Customs Duties",
            "EACCMA",
        ),
        (
            "Tax Refund Track Application Status REF-10928374",
            "Track Application Status",
            "Reference Number",
        ),
        (
            "Tax Refund Download Refund Manual Forms Form DT-4001",
            "Download Manual Refund Forms",
            "DT-4001",
        ),
        (
            "Tax Refund Document Authentication check refund approval letter",
            "Document Authentication & Verification",
            "Reference Number",
        ),
        (
            "Tax Refund Section 42 TPCA VAT input credit reimbursement",
            "Tax Refund Application (Section 42 TPCA)",
            "90-day",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="get_refund")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_tax_incentives_comprehensive_sub_paths_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Tax Incentives apply for WHT exemption under Section 119",
            "WHT Exemption Application",
            "3 consecutive years",
        ),
        (
            "Tax Incentives Investors Guides capital thresholds",
            "Investors Guides",
            "USD 10 million",
        ),
        (
            "Tax Incentives Income Tax Exemption for charitable trust",
            "Income Tax Exemption",
            "Section 21",
        ),
        (
            "Tax Incentives Import or Export Tax Exemption on factory machinery",
            "Import & Export Customs Tax Exemption",
            "EACCMA Fifth Schedule",
        ),
        (
            "Tax Incentives Track Application Status EX-992817",
            "Track Application Status",
            "Search Number",
        ),
        (
            "Tax Incentives Withholding Tax Exemption List check vendor TIN",
            "Withholding Tax Exemption List",
            "June 30",
        ),
        (
            "Tax Incentives VAT Withholding Agent List verify client status",
            "VAT Withholding Agent List",
            "Form DT-1014",
        ),
        (
            "Tax Incentives Designated Income Tax WHT Agents For FY 2024/25 gazette",
            "Designated Income Tax WHT Agents For FY 2024/25",
            "FY 2024/25",
        ),
        (
            "Tax Incentives List of Non-Resident providers of digital services registered with URA Netflix Google",
            "List of Non-Resident Digital Service Providers",
            "Digital Services Tax",
        ),
        (
            "Tax Incentives 10-Year Tax Holiday agro-processing",
            "10-Year Tax Holiday",
            "USD 10 million",
        ),
        (
            "Tax Incentives Tax Waiver on penalty and interest Section 66",
            "Tax Waiver & Penal Interest Remission",
            "Section 66 TPCA",
        ),
        (
            "Tax Incentives Document Authentication verify certificate",
            "Document Authentication & Verification",
            "Reference Number",
        ),
        (
            "Tax Incentives Withholding Tax rates and Form DT-1013",
            "Withholding Tax (WHT) Overview & Filing",
            "Form DT-1013",
        ),
        (
            "Tax Incentives statutory benefits overview",
            "Tax Incentives & Statutory Exemptions Overview",
            "Section 21 ITA",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="tax_incentives")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_motor_vehicle_comprehensive_sub_paths_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Motor Vehicle Search Vehicle Details Application for plate UBA 123X",
            "Search Vehicle Details Application",
            "UGX 24,000",
        ),
        (
            "Motor Vehicle View Vehicle Search Report certified logbook details",
            "View Vehicle Search Report",
            "Chassis Number",
        ),
        (
            "Motor Vehicle Track Application Status MV-883719",
            "Track Application Status",
            "Application Search Number",
        ),
        (
            "Motor Vehicle Download Vehicle Manual Forms Form TR VII",
            "Download Manual Forms",
            "Form TR VII",
        ),
        (
            "Motor Vehicle Document Authentication check logbook serial",
            "Document Authentication & Logbook Verification",
            "Logbook Serial Number",
        ),
        (
            "Motor Vehicle Print TIN Submitted Forms reprint transfer deed",
            "Print TIN Submitted Forms",
            "watermark",
        ),
        (
            "Motor Vehicle Transfer of Ownership to buyer TIN",
            "Transfer of Ownership",
            "1.5% stamp duty",
        ),
        (
            "Motor Vehicle Personalized / Vanity Number Plates VIP 7",
            "Personalized / Vanity Number Plates",
            "UGX 20,000,000",
        ),
        (
            "Motor Vehicle registration and licensing services hub",
            "Motor Vehicle Services Hub",
            "Search Vehicle Details",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="motor_vehicle")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_choose_tax_agent_comprehensive_sub_paths_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Choose a Tax Agent Licensed Customs Agents for clearing import cargo",
            "Licensed Customs Agents",
            "Asycuda World",
        ),
        (
            "Choose a Tax Agent Licensed Domestic Tax DT Agents TARC certified",
            "Licensed Domestic Tax (DT) Agents",
            "Tax Agents Registration Committee",
        ),
        (
            "Choose a Tax Agent Income Tax Agents - WHT schedule Section 119",
            "Income Tax Agents - WHT",
            "Withholding Tax Credit Certificate",
        ),
        (
            "Choose a Tax Agent VAT Agents - WHT statutory withholding list",
            "VAT Agents - WHT",
            "Form DT-1014",
        ),
        (
            "Choose a Tax Agent Appoint Agent online mandate delegation",
            "Appoint a Tax Agent",
            "NEVER handover payment",
        ),
        (
            "Choose a Tax Agent guidelines and regulatory overview",
            "Choose a Tax Agent Overview",
            "Tax Agents Registration Committee",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="choose_tax_agent")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_objections_and_appeals_comprehensive_sub_paths_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Objections and Appeals Object to a Tax Assessment Section 24",
            "Object to a Tax Assessment",
            "45-day",
        ),
        (
            "Objections and Appeals Object To Other Decisions TCC refusal",
            "Object To Other Decisions",
            "Denial of Tax Clearance Certificate",
        ),
        (
            "Objections and Appeals Elect To An Objection Decision TAT appeal",
            "Elect To An Objection Decision",
            "30 statutory days",
        ),
        (
            "Objections and Appeals Apply To Extend Date To Lodge an Objection sickness",
            "Apply To Extend Date To Lodge an Objection",
            "reasonable grounds",
        ),
        (
            "Objections and Appeals Apply For Waiver of Payment Requirement extreme hardship",
            "Apply For Waiver of Payment Requirement",
            "30% pre-deposit",
        ),
        (
            "Objections and Appeals Apply For Penalty Reversal system downtime",
            "Apply For Penalty Reversal",
            "Voluntary Disclosure",
        ),
        (
            "Objections and Appeals Alternative Dispute Resolution (ADR) Form mediation",
            "Alternative Dispute Resolution (ADR) Form",
            "settlement agreement",
        ),
        (
            "Objections and Appeals Track Application Status OBJ-772819",
            "Track Application Status",
            "90-day statutory timeline",
        ),
        (
            "Objections and Appeals dispute resolution framework overview",
            "Objections and Appeals Overview",
            "Section 24",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="objection_appeals")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_stamp_duty_comprehensive_sub_paths_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Stamp Duty Print a Stamp Duty Certificate PRN 22910283",
            "Print a Stamp Duty Certificate",
            "Ministry of Lands",
        ),
        (
            "Stamp Duty Generate Duplicate Stamp Certificate for lost deed",
            "Generate Duplicate Stamp Certificate",
            "duplicate seal",
        ),
        (
            "Stamp Duty Issue Bulk Stamp Certificates for bank mortgages",
            "Issue Bulk Stamp Certificates",
            "consolidated PRN",
        ),
        (
            "Stamp Duty Login to file stamp duty Returns monthly instruments",
            "Login to File Stamp Duty Returns",
            "e-acknowledgement",
        ),
        (
            "Stamp Duty Registration Under Bulk Assessment for commercial bank",
            "Registration Under Bulk Assessment",
            "Bulk Assessment",
        ),
        (
            "Stamp Duty Document Authentication check stamp certificate",
            "Document Authentication & Verification",
            "Verify Document",
        ),
        (
            "Stamp Duty Print TIN Submitted Forms reprint assessment slip",
            "Print TIN Submitted Forms",
            "barcode",
        ),
        (
            "Stamp Duty assessment on property transfer 1.5%",
            "Stamp Duty Assessment & Property Clearance",
            "Chief Government Valuer",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="stamp_duty")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_tax_clearance_comprehensive_sub_paths_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Tax Clearance Track Application Status TCC-8829102",
            "Track Application Status",
            "TCC Application Search Number",
        ),
        (
            "Tax Clearance Document Authentication verify TCC-9928172 genuine",
            "Document Authentication & Verification",
            "Tax Clearance Certificate (TCC)",
        ),
        (
            "ax Clearance Track Application Status",
            "Track Application Status",
            "TCC Application Search Number",
        ),
        (
            "Tax Clearance Certificate requirements and bidding",
            "Tax Clearance Certificate (TCC) Requirements",
            "100% filing compliance",
        ),
        (
            "Tax Clearance apply for tax clearance certificate for tender",
            "Tax Clearance Certificate (TCC) Application",
            "24 to 48 hours",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="tax_clearance")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_export_process_faqs_comprehensive_sub_paths_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "The Export Process Main Export Products coffee tea cement",
            "Main Export Products",
            "Coffee",
        ),
        (
            "The Export Process Document Authentication verify certificate of origin",
            "Document Authentication & Verification",
            "Certificate of Origin",
        ),
        (
            "The Export Process One Stop Border Post Busia Malaba joint border",
            "One Stop Border Post (OSBP)",
            "Busia",
        ),
        (
            "The Export Process Import & Export Facilitation AEO green channel",
            "Import & Export Facilitation",
            "Single Window",
        ),
        (
            "The Export Process Management of Transit Cargo RECTS e-seals",
            "Management of Transit Cargo",
            "RECTS",
        ),
        (
            "The Export Process How to start exporting URSB TIN",
            "How to Start Exporting",
            "Produced in Uganda for Export",
        ),
        (
            "The Export Process Tax Incentives Under the Export Process duty drawback",
            "Tax Incentives Under the Export Process",
            "Section 24",
        ),
        (
            "The Export Process Key Export Markets for Ugandan Products Kenya DRC",
            "Key Export Markets for Ugandan Products",
            "South Sudan",
        ),
        (
            "The Export Process Key Documents Required to Export Commercial Invoice",
            "Key Documents Required to Export",
            "Commercial Invoice",
        ),
        (
            "The Export Process Export Procedures and Customs Requirements Asycuda SAD",
            "Export Procedures & Customs Requirements",
            "Single Window",
        ),
        (
            "The Export Process Payment Methods Used in International Trade Letters of Credit",
            "Payment Methods Used in International Trade",
            "Letters of Credit",
        ),
        (
            "The Export Process Quality Standards / Certifications Required UNBS MAAIF",
            "Quality Standards & Certifications Required",
            "Phytosanitary",
        ),
        (
            "The Export Process Logistics and Shipping Arrangements freight forwarders",
            "Logistics and Shipping Arrangements",
            "UCIFA",
        ),
        (
            "The Export Process Financial Assistance Options UDB financing",
            "Financial Assistance Options",
            "Uganda Development Bank",
        ),
        (
            "The Export Process general guidelines overview",
            "The Export Process Overview",
            "Asycuda World",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="export_process")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_customs_valuation_comprehensive_sub_paths_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Customs Valuation Methods of Customs Valuation under WTO ACV",
            "Methods of Customs Valuation",
            "Fourth Schedule",
        ),
        (
            "Customs Valuation Customs Legal and Security bonds process of bond execution",
            "Customs Legal & Security Bonds",
            "Form CB1",
        ),
        (
            "Customs Valuation Clearing Agents licensing conditions Section 145",
            "Clearing Agents Licensing & Compliance",
            "EACFFPC",
        ),
        (
            "Customs Valuation Motor vehicle Value Guide database August 2026",
            "Motor Vehicle Value Guide",
            "CIF (USD)",
        ),
        (
            "Motor vehicle Value Guide disclaimer and statutory right under Article 17 WTO ACV",
            "Motor Vehicle Value Guide",
            "Statutory Disclaimer",
        ),
        (
            "Click to download the Motor Vehicle Database Guides PDF archive",
            "Motor Vehicle Value Guide",
            "download-category/motor-vehicle-valuation-guides",
        ),
        (
            "Customs Valuation Revised General Goods Database reference prices",
            "Revised General Goods Database",
            "Document Processing Centre",
        ),
        (
            "https://ura.go.ug/en/revised-general-goods-database-5/ TSC UOM",
            "Revised General Goods Database",
            "01st September 2026",
        ),
        (
            "What are the different methods of Customs valuation allowed under the ACV?",
            "Methods of Customs Valuation",
            "Method 1 (Transaction Value)",
        ),
        (
            "Are there any international rules for the determination of the Customs value of goods?",
            "Customs Value & International Rules",
            "WTO Agreement on Customs Valuation",
        ),
        (
            "What is Customs value? ad valorem and specific duties",
            "Customs Value & International Rules",
            "ad valorem customs duties",
        ),
        (
            "Why is a bond executed? Section 106 EACCMA",
            "Customs Legal & Security Bonds",
            "Transit Bond",
        ),
        (
            "What is required to execute a bond? stamp duty 0.05%",
            "Customs Legal & Security Bonds",
            "Insurance",
        ),
        (
            "What types of bonds are there under Customs? CB6 RCTG CB10",
            "Customs Legal & Security Bonds",
            "Form CB1",
        ),
        (
            "Customs Valuation overview and Asycuda NII container validation",
            "Customs Valuation Overview & Asycuda NII Requirements",
            "September 7, 2026",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="customs_valuation")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_single_customs_territory_comprehensive_sub_paths_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Single Customs Territory how is fuel handled under SCT 10 days",
            "Fuel Handling & 10-Day Rule",
            "ten (10) days",
        ),
        (
            "Single Customs Territory contraband on same bill of lading manifest splitting C9/C11",
            "Manifest Splitting & C9/C11 Amendments",
            "Form C9/C11",
        ),
        (
            "Single Customs Territory consignment does not arrive in the country due to accidents thefts fire",
            "Cargo Incident & Loss Procedures",
            "Scene of Crime",
        ),
        (
            "Single Customs Territory mutual recognition of customs clearing agents third-party declarant",
            "Mutual Recognition & Declarants",
            "Third-Party Declarant",
        ),
        (
            "Single Customs Territory RECTS free of charge corridor security",
            "RECTS & Corridor Security",
            "free of charge",
        ),
        (
            "Single Customs Territory who monitors RCTG bond account performance active carnets",
            "RCTG Bond Account Monitoring",
            "sole responsibility",
        ),
        (
            "Single Customs Territory first point of entry Mombasa Dar es Salaam port CFS",
            "First Point of Entry & CFS Operations",
            "Container Freight Stations",
        ),
        (
            "Single Customs Territory SCT business process manual overview January 1 2014",
            "Single Customs Territory (SCT): Overview & Operations",
            "January 1, 2014",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="single_customs_territory")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_exempt_importation_subpaths():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Exempt importation agricultural machinery tractors sprayers irrigation equipment APC 478",
            "Agricultural Machinery & Implements (APC 478)",
            "APC) 478",
        ),
        (
            "Exempt importation solar panels wind energy deep cycle batteries photovoltaic Item 26 APC 472",
            "Solar & Wind Energy Equipment (APC 472, Item 26)",
            "deep cycle batteries",
        ),
        (
            "Exempt importation hotel operational equipment UHOA endorsement engraved logo Item 21 APC 472",
            "Hotel Operational Equipment (APC 472, Item 21)",
            "hotel logo",
        ),
        (
            "Exempt importation industrial spare parts replacement parts Chapters 84 and 85 UMA MTIC APC 492",
            "Industrial Machinery Replacement Parts (APC 492, Item 31)",
            "Chapters 84 and 85",
        ),
        (
            "Exempt importation oil and gas geothermal petroleum exploration PAU MEMD Item 30(a) APC 475",
            "Oil, Gas & Geothermal Exploration (APC 475, Item 30(a))",
            "Petroleum Authority of Uganda",
        ),
        (
            "Exempt importation diagnostic reagents drugs medicines NDA verification certificate APC 472 APC 478",
            "Drugs, Reagents & Medical Equipment (APC 472 & 478)",
            "National Drug Authority",
        ),
        (
            "Exempt importation fertilizers poultry parent stock animal feeds packing materials for export Item 11 Item 15",
            "Fertilizers, Feeds & Export Packaging (APC 472 & 478)",
            "FOR EXPORT ONLY",
        ),
        (
            "Comprehensive list of exempt importation overview EACCMA Fifth Schedule Part B VAT Act",
            "Comprehensive List of Exempt Importations Overview & APC Fast-Track Rules",
            "EACCMA 2004",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="exempt_importation")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_aeo_subpaths():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Authorized Economic Operator about the AEO program meaning of AEO WCO SAFE framework three pillars",
            "About the AEO Program & WCO SAFE Framework",
            "three pillars",
        ),
        (
            "Authorized Economic Operator AEO prospective clients manufacturers clearing agents bonded warehouse keepers",
            "Prospective Clients & Eligible Actors",
            "118 certified AEOs",
        ),
        (
            "Authorized Economic Operator objectives of the Uganda AEO scheme supply chain security",
            "Objectives of the Scheme",
            "trade facilitation",
        ),
        (
            "Authorized Economic Operator eligibility criteria for becoming an AEO 3 years compliance financial soundness",
            "Eligibility Criteria",
            "three (3) consecutive years",
        ),
        (
            "Authorized Economic Operator processes of attaining an AEO status self-assessment onsite inspection CIP",
            "Processes of Attaining AEO Status",
            "Compliance Improvement Plan",
        ),
        (
            "Authorized Economic Operator benefits of the AEO program to business self-management of customs bonded EAC MRA",
            "Benefits to Business & EAC MRA",
            "Withholding Tax",
        ),
        (
            "Authorized Economic Operator AEO enterprise risk management AEO ERM 40days certification e-certificate",
            "AEO Enterprise Risk Management (AEO ERM)",
            "40 days",
        ),
        (
            "Authorized Economic Operator AEO program overview guide application",
            "Authorized Economic Operator (AEO): Program Overview & Guide",
            "EAC regional mutual recognition",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="aeo")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_customs_audits_refunds_subpaths():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Customs audits and refunds instalment payment of URA taxes for motor vehicles tax work sheet DCU MOU",
            "Instalment Payment of Taxes Guide",
            "Central Records Office",
        ),
        (
            "Customs audits and refunds diplomatic fuel refund excise duty on fuel Section 114 Form C34 MFA Form 3",
            "Diplomatic Fuel & Customs Refunds",
            "Ministry of Foreign Affairs",
        ),
        (
            "Customs audits and refunds general customs duty refund Section 143 Section 144 Form C33 Form C34",
            "General Customs Duty Refunds",
            "twelve (12) months",
        ),
        (
            "Customs audits and refunds auction proceeds surplus balance Section 57 warehouse keeper charges",
            "Auction Sale Proceeds Recovery",
            "one (1) year",
        ),
        (
            "Customs audits and refunds procedure for duty drawback registration Form C30 input output ratio TID",
            "Duty Drawback (DDB) Registration Procedure",
            "Tariff and Information Division",
        ),
        (
            "Customs audits and refunds procedure for claiming duty drawback Form C31 12 months exportation US$100",
            "Duty Drawback (DDB) Claims Procedure",
            "Regulation 139(1)",
        ),
        (
            "Customs audits and refunds approval thresholds UGX 1, 000,000 AC Customs Audit Commissioner Customs AC Finance",
            "Approval Financial Thresholds & Payment Flow",
            "Manager Customs Audit",
        ),
        (
            "Customs audits and refunds public international organizations accredited to Uganda DANIDA USAID WHO UNICEF",
            "Accredited Public International Organizations Directory",
            "87 accredited public international bodies",
        ),
        (
            "Customs audits and refunds overview procedures guidelines post clearance audit",
            "Customs Audits and Refunds: Overview & Procedures",
            "Instalment Payment Facility",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="customs_audits_refunds")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_warehousing_subpaths():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Warehousing public online auction singlewindow.go.ug/auction participation fee $10 24 to 48 hours",
            "Public Online Auction via UeSW",
            "singlewindow.go.ug/auction",
        ),
        (
            "Warehousing private treaty disposal sales direct negotiation unsold lots free participation",
            "Private Treaty Disposal Sales",
            "direct negotiation",
        ),
        (
            "Warehousing want of entry list cargo redemption 14 days Section 42 notice",
            "Want of Entry List & Cargo Redemption",
            "Want of Entry",
        ),
        (
            "Warehousing of goods cargo receiving IM7 auto conversion CB6 bond prohibited items",
            "Warehousing of Goods & Regimes (IM7 & CB6)",
            "auto-convert",
        ),
        (
            "Warehousing statutory warehousing durations 6 months 9 months 270 days two (2) years",
            "Statutory Warehousing Durations & Extensions",
            "six (6) months",
        ),
        (
            "Warehousing provisional release verification at owners SCT-PEV WT8 fragile, bulky machinery",
            "Provisional Release & Owner's Premises Verification",
            "written permission",
        ),
        (
            "Warehousing enforcement of guidelines for the management of licensed bonded warehouses reflector jackets Section 64 Section 67",
            "Bonded Warehouse Management Enforcement Guidelines",
            "reflector jackets",
        ),
        (
            "Customs warehousing manual overview guidelines public car bonds",
            "Customs Warehousing: Overview & Guidelines",
            "Public general goods",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="warehousing")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_customs_enforcements_subpaths():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Customs enforcements prohibited goods used laptops computers chemicals Section 210",
            "Prohibited & Restricted Goods Schedule",
            "Finance Act 2009",
        ),
        (
            "Customs enforcements passenger baggage at Entebbe Airport USD 500 personal allowance USD 2000 TIN rule",
            "Entebbe Airport Passenger Baggage Clearance",
            "USD 500",
        ),
        (
            "Customs enforcements passenger baggage currency declaration 1,500 currency points USD 9000",
            "Entebbe Airport Passenger Baggage Clearance",
            "1,500 currency points",
        ),
        (
            "Customs enforcements transit cargo monitoring 21 gazetted transit routes Form C17 30 days security bond",
            "Transit Cargo Monitoring & Gazetted Corridors",
            "thirty (30) days",
        ),
        (
            "Customs enforcements transit goods license TGL Form C39 USD 200 fee road user charges RUC formula",
            "Transit Goods License (TGL) & Road User Charges",
            "USD 200",
        ),
        (
            "Customs enforcements regional electronic cargo tracking system RECTS eseal free of charge CMC RRU",
            "Regional Electronic Cargo Tracking System (RECTS)",
            "FREE OF CHARGE",
        ),
        (
            "Customs enforcements customs offences seizure notice Form C37 request to settle offence Form C35 compounding Section 219",
            "Customs Offences, Seizures & Compounding",
            "Form C37",
        ),
        (
            "Customs enforcements non-intrusive inspection NII x-ray radiation scan empty trucks foodstuffs free of charge",
            "Non-Intrusive Inspection (NII) & Scanning",
            "Atomic Energy Council",
        ),
        (
            "Customs enforcements overview anti smuggling operations",
            "Customs Enforcement: Overview & Operations",
            "Entebbe Airport Baggage",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="customs_enforcements")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_laws_and_acts_subpaths():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Laws and acts compendium for various domestic tax laws Income Tax Act Cap Value Added Tax TPCA 2014",
            "Compendium of Domestic Tax Laws",
            "Tax Procedures Code Act",
        ),
        (
            "Laws and acts convention on mutual administrative assistance in tax matters automatic exchange of information act common reporting standard CRS",
            "Mutual Administrative Assistance & AEOI Act 2023",
            "Common Reporting Standard",
        ),
        (
            "Laws and acts the tax appeals tribunals act Section 15 of the tax appeals 30% of the tax in dispute",
            "Tax Appeals Tribunals Act (Cap. 345)",
            "30% of the tax",
        ),
        (
            "Laws and acts procedure manual for application of the duty remission regulations EAC 2008 Section 140",
            "Duty Remission Regulations & Procedure Manual",
            "EAC Council",
        ),
        (
            "Laws and acts the East African Community customs management act EACCMA 2004 common external tariff 2022 4-band tariff",
            "EACCMA 2004 & Common External Tariff (CET 2022)",
            "4-band",
        ),
        (
            "Laws and acts anti-money laundering act 2013 financial intelligence authority FIA suspicious transaction",
            "Anti-Money Laundering Act & Regulations 2023",
            "Financial Intelligence Authority",
        ),
        (
            "Laws and acts the East African tax law report volume IV judicial precedents landmark tax judgments",
            "The East African Tax Law Reports (Volume IV)",
            "December 10, 2024",
        ),
        (
            "Laws and acts value added tax (amendment) act 2023 non-resident digital services efris input tax claim",
            "Value Added Tax (Amendment) Act, 2023",
            "non-resident digital",
        ),
        (
            "Laws and acts stamp duty act 2014 fixed duty of ugx 15,000 1% on transfer of land shares",
            "Stamp Duty Act 2014",
            "UGX 15,000",
        ),
        (
            "Laws and acts excise duty act 2014 digital tax stamps act Section 19b schedule 2 excisable goods",
            "Excise Duty Act 2014",
            "Section 19B",
        ),
        (
            "Laws and acts traffic and road safety act amendment 14-day statutory timeline environmental levy",
            "Traffic and Road Safety Amendment Act 2023",
            "14 days",
        ),
        (
            "Laws and acts lotteries and gaming amendment act 2023 section 118c 30% withholding tax on gaming winnings",
            "Lotteries and Gaming Amendment Act 2023",
            "30%",
        ),
        (
            "Laws and acts free zones act 2014 uganda free zones authority ufza 10-year income tax holiday 80% export",
            "The Free Zones Act 2014",
            "10-year",
        ),
        (
            "Laws and acts comesa protocol on rules of origin 2015 simplified trade regime regional value content",
            "EAC & COMESA Rules of Origin Protocols 2015",
            "COMESA Protocol",
        ),
        (
            "Laws and acts overview download category laws and regulations repository",
            "Laws, Acts & Regulations: Overview & Repository",
            "Domestic Tax Laws",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="laws_and_acts")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_double_taxation_agreements_subpaths():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Double taxation agreements delegated competent authorities directory John R. Musinguzi Agnes Nabwire David Dongo",
            "Delegated Competent Authorities Directory",
            "Commissioner General",
        ),
        (
            "Double taxation agreements India – Uganda income tax treaty 2004 Article 10 dividends 10% Article 11 interest",
            "India – Uganda Income Tax Treaty (2004)",
            "10%",
        ),
        (
            "Double taxation agreements South Africa – Uganda income tax treaty 1997 secondary tax on companies 10% 15%",
            "South Africa – Uganda Income Tax Treaty (1997)",
            "25%",
        ),
        (
            "Double taxation agreements Mauritius – Uganda income tax treaty 2003 service pe consultancy 4 months Article 13",
            "Mauritius – Uganda Income Tax Treaty (2003)",
            "4 months",
        ),
        (
            "Double taxation agreements Denmark – Uganda income tax treaty 2000 danish tax SAS Danmarks Nationalbank",
            "Denmark – Uganda Income Tax Treaty (2000)",
            "SAS Danmark",
        ),
        (
            "Double taxation agreements Netherlands – Uganda income tax treaty 2004 0% dividend 50% holding 3-year loan",
            "Netherlands – Uganda Income Tax Treaty (2004)",
            "50%",
        ),
        (
            "Double taxation agreements Norway – Uganda income tax treaty 1999 Norwegian Petroleum Fund Eksportfinans SAS",
            "Norway – Uganda Income Tax Treaty (1999)",
            "Petroleum Fund",
        ),
        (
            "Double taxation agreements United Kingdom uk – uganda income tax treaty 1992 183 days 15% dividend",
            "United Kingdom – Uganda Income Tax Treaty (1992)",
            "183 days",
        ),
        (
            "Double taxation agreements Section 88 treaty relief limitation on benefits lob tax residence certificate Form DT-1",
            "Treaty Relief Procedures, Form DT-1 & LOB Compliance",
            "Form DT-1",
        ),
        (
            "Double taxation agreements overview bilateral tax treaties network concessions",
            "Double Taxation Agreements: Overview & Treaty Network",
            "active DTAs",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="double_taxation_agreements")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_case_summary_reports_subpaths():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Case summary reports URA case digest Volume XI Jul - Dec 2025 38 decisions of the courts",
            "URA Case Digest Volume XI (Jul - Dec 2025)",
            "38 decisions",
        ),
        (
            "Case summary reports case digest Vol. VIII Jan- Mar 2024 High Court TAT",
            "Case Digest Volume VIII (Jan - Mar 2024)",
            "1,757 downloads",
        ),
        (
            "Case summary reports case digest Volume VI July – Sept 2023 Volume V April - June 2023",
            "Case Digest Volumes V & VI (2023 Editions)",
            "Volume VI",
        ),
        (
            "Case summary reports compendium EAC tax cases regional East African Community Partner States",
            "Compendium of EAC Tax Cases",
            "EAC Common External Tariff",
        ),
        (
            "Case summary reports overview download category judicial case digest precedents",
            "Case Summary Reports: Overview & Legal Digests",
            "Case Digest volumes",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="case_summary_reports")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_court_of_appeal_subpaths():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Court of appeal Celtel Uganda Ltd vs URA Civil Appeal 22 of 2006 airtime vouchers distributor discounts",
            "Celtel Uganda Ltd vs URA (Civil Appeal 22 of 2006)",
            "Civil Appeal No. 22 of 2006",
        ),
        (
            "Court of appeal Gulindwa Paul v Ug criminal tax fraud customs smuggling precedent",
            "Gulindwa Paul v Uganda (Criminal Tax Fraud)",
            "mens rea",
        ),
        (
            "Court of appeal overview appellate tax jurisprudence binding authority",
            "Court of Appeal: Landmark Tax Jurisprudence Overview",
            "Court of Appeal tax rulings",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="court_of_appeal")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_debt_collections_subpaths():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Debt collections Section 40 demand notice Section 41 distress proceedings court bailiff",
            "Demand Notices & Distress Proceedings",
            "14 days",
        ),
        (
            "Debt collections Section 42 temporary closure of business premises padlocked seal premises",
            "Temporary Closure of Business Premises",
            "fourteen (14) days",
        ),
        (
            "Debt collections Section 43 agency notice garnishee frozen bank account commercial bank",
            "Agency Notices & Bank Garnishee Orders",
            "Section 43 TPCA",
        ),
        (
            "Debt collections Section 45 departure prohibition dpo prevent travel immigration control",
            "Departure Prohibition Orders",
            "Entebbe International Airport",
        ),
        (
            "Debt collections Section 47 instalment payment agreement dcu mou down payment",
            "Instalment Payment Agreements & DCU MOU",
            "down payment",
        ),
        (
            "Debt collections overview recovery mandate tax arrears management",
            "Debt Collections: Overview & Recovery Mandate",
            "Part VIII",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="debt_collections")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_financial_intelligence_authority_subpaths():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Financial intelligence authority ML.TF risk assessment on tax crimes and proceeds July 18, 2025 World Bank tool",
            "ML/TF Risk Assessment on Tax Crimes and Proceeds",
            "July 18, 2025",
        ),
        (
            "Financial intelligence authority inter-agency collaboration asset recovery predicate crime ODPP CID",
            "Inter-Agency Taskforce & Asset Recovery",
            "predicate offences",
        ),
        (
            "Financial intelligence authority accountable persons cash transaction report CTR suspicious transaction report STR USD 10,000",
            "Accountable Persons & Mandatory Reporting (CTR/STR)",
            "USD 10,000",
        ),
        (
            "Financial intelligence authority overview AML/CFT framework national risk assessments",
            "Financial Intelligence Authority: AML/CFT Framework Overview",
            "AML Act 2013",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="financial_intelligence_authority")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_customs_systems_subpaths():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Customs systems ASYCUDA World installer touchpoint.ura.go.ug registration forms",
            "ASYCUDA World & Touchpoint Portal Access",
            "touchpoint.ura.go.ug",
        ),
        (
            "Customs systems Uganda Electronic Single Window UESW joint agency trade regulatory",
            "Uganda Electronic Single Window (UESW)",
            "Multi-Agency",
        ),
        (
            "Customs systems Regional Electronic Cargo Tracking System RECTS electronic smart seal",
            "Regional Electronic Cargo Tracking System (RECTS)",
            "smart electronic seals",
        ),
        (
            "Customs systems Non-Intrusive Inspection NII drive-through x-ray scanner Atomic Energy Council",
            "Non-Intrusive Inspection (NII)",
            "Atomic Energy Council",
        ),
        (
            "Customs systems Bonded Warehouse Information Management System BWIMS inventory tracking IM7",
            "Bonded Warehouse Information Management System (BWIMS)",
            "IM7",
        ),
        (
            "Customs systems Naivasha ICD Mombasa port overstayed cargo removal of goods",
            "Naivasha ICD & Mombasa Port Cargo Clearance",
            "Section 42(1)",
        ),
        (
            "Customs systems overview automated trade clearance platforms ecosystem",
            "Customs Systems: Overview & Trade Facilitation Platforms",
            "ASYCUDA World",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="customs_systems")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_taxpayer_starter_pack_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Download URA Taxpayer Starter Pack brochure booklet for new taxpayers",
            "Taxpayer Registration Starter Pack",
            "package ID 62580",
        ),
        (
            "New TIN registration starter pack onboarding and account setup",
            "Taxpayer Registration Starter Pack",
            "default password",
        ),
        (
            "Starter pack return filing calendar provisional returns and payment channels",
            "Taxpayer Registration Starter Pack",
            "USSD code *285#",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_tax_waiver_tujenge_pack_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Tujenge Pack tax waiver for unpaid penalties and interest under Section 47B TPCA",
            "Tax Waiver & Penal Interest Remission",
            "Section 47B TPCA",
        ),
        (
            "Apply for 100% tax waiver brochure by paying principal tax by 30th June 2026",
            "Tax Waiver & Penal Interest Remission",
            "30th June 2026",
        ),
        (
            "Pro-rata tax waiver relief calculation on domestic taxes",
            "Tax Waiver & Penal Interest Remission",
            "Pro-Rata Relief",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="tax_incentives")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_withholding_tax_rates_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Withholding tax guide Vol. 2 Issue 5 rates and Form DT-1013",
            "Withholding Tax (WHT) Overview & Filing",
            "WITHHOLDING-TAX-2026-27.pdf",
        ),
        (
            "Revised FY 2026/27 PAYE monthly threshold and withholding tax return",
            "Withholding Tax (WHT) Overview & Filing",
            "UGX 335,000",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text, portal_name="tax_incentives")
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])

def test_oil_and_gas_petroleum_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Miners of oil and gas exploration Tilenga and Kingfisher upstream value chain",
            "Petroleum, Oil & Gas Sector Guide",
            "Upstream (exploration/production)",
        ),
        (
            "East African Crude Oil Pipeline EACOP 10 year corporate income tax holiday",
            "Petroleum, Oil & Gas Sector Guide",
            "10-year CIT holiday",
        ),
        (
            "Dealers in oil and gas products fuel pumps Electronic Dispenser Controllers EDCs EFRIS",
            "Petroleum, Oil & Gas Sector Guide",
            "Electronic Dispenser Controllers (EDCs)",
        ),
        (
            "Petroleum sector deemed VAT provisions under Section 24(5) VAT Act",
            "Petroleum, Oil & Gas Sector Guide",
            "deemed VAT",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])

def test_agriculture_sector_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Agricultural sector taxation guide and commercial crop farming deduction Section 35",
            "Agricultural Sector Guide",
            "Section 35 ITA",
        ),
        (
            "Agribusiness citizen startup 3 year tax holiday for capital under 500 million",
            "Agricultural Sector Guide",
            "UGX 500 million",
        ),
        (
            "Floriculture greenhouse capital expenditure 20% annual deduction and flower export",
            "Agricultural Sector Guide",
            "20% annual straight-line deduction",
        ),
        (
            "Poultry farming parent stock and day-old chicks hatching eggs duty free",
            "Agricultural Sector Guide",
            "Broiler/layer parent stock",
        ),
        (
            "Agro-processing 10-year income tax holiday for 80% export and agricultural insurance stamp duty",
            "Agricultural Sector Guide",
            "Section 21(1)(y) ITA",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])

def test_hospitality_sector_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Hotel and accommodation sector guide UHOA partnership",
            "Hotel, Accommodation & Tourism Sector Guide",
            "UHOA",
        ),
        (
            "Local Hotel Tax rates for four and five star hotels USD 2 per room",
            "Hotel, Accommodation & Tourism Sector Guide",
            "Local Hotel Tax",
        ),
        (
            "Hotel and accommodation equipment duty exemption for licensed hotels under UHOA guide",
            "Hotel, Accommodation & Tourism Sector Guide",
            "EACCMA Item 21",
        ),
        (
            "Tour operators 4x4 safari vehicles and sightseeing buses duty free",
            "Hotel, Accommodation & Tourism Sector Guide",
            "Tourism Transport Incentives",
        ),
        (
            "Restaurants and outside catering mandatory EFRIS fiscal receipts",
            "Hotel, Accommodation & Tourism Sector Guide",
            "outside catering",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_wholesale_retail_sector_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Wholesale and retail trade sector guide general traders categorization",
            "Wholesale & Retail Trade Sector Guide",
            "Small Business Presumptive",
        ),
        (
            "Overview of wholesale trade bulk distribution and retail trade",
            "Wholesale & Retail Trade Sector Guide",
            "Trader Categorization",
        ),
        (
            "General wholesale traders VAT-registered category and EFRIS invoice",
            "Wholesale & Retail Trade Sector Guide",
            "EFRIS",
        ),
        (
            "Wholesale and retail sector tax incentives initial allowance 50 percent",
            "Wholesale & Retail Trade Sector Guide",
            "initial allowance",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])

def test_construction_sector_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Overview of the construction sector civil engineering and infrastructure",
            "Construction Sector Guide",
            "Multi-Agency Licensing",
        ),
        (
            "Construction companies corporation tax return Form DT-1001",
            "Construction Sector Guide",
            "30% CIT",
        ),
        (
            "Construction professionals withholding tax 6 percent on engineering design fees",
            "Construction Sector Guide",
            "6% WHT on construction contracts",
        ),
        (
            "Deemed VAT under Section 24(5) for contractors on aid-funded projects",
            "Construction Sector Guide",
            "aid-funded projects",
        ),
        (
            "Import duty on cranes and surveying equipment GPS theodolites",
            "Construction Sector Guide",
            "0% import duty on cranes",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])

def test_manufacturing_sector_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Manufacturing of tangible products steel plastics and appliances",
            "Manufacturing & Industrial Sector Guide",
            "Regulatory Licensing & Setup",
        ),
        (
            "Manufacturing sector 10-year income tax holiday in Industrial Parks or Free Zones",
            "Manufacturing & Industrial Sector Guide",
            "10-Year Income Tax Holiday",
        ),
        (
            "EAC duty remission scheme for industrial raw materials and packaging supplies",
            "Manufacturing & Industrial Sector Guide",
            "EAC Duty Remission Scheme",
        ),
        (
            "Manufacturing sector factory line tax stamps for excisable beer spirits soda cement sugar",
            "Manufacturing & Industrial Sector Guide",
            "Digital Tax Stamps (DTS)",
        ),
        (
            "Cosmetics processing steel sector and textile processing industry tax rules",
            "Manufacturing & Industrial Sector Guide",
            "Download Official Compendium",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])

def test_education_sector_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Overview of the education sector school proprietors licensing MoES",
            "Education Sector Guide",
            "MoES",
        ),
        (
            "Charitable school exemption ruling under Section 21(1)(f) Income Tax Act",
            "Education Sector Guide",
            "Section 21(1)(f) ITA",
        ),
        (
            "Educational services VAT exemption on tuition and boarding fees Schedule 2",
            "Education Sector Guide",
            "Schedule 2 VAT Act",
        ),
        (
            "Scholastic materials VAT exempt textbooks geometry sets crayons science chemicals",
            "Education Sector Guide",
            "Scholastic & Scientific Materials",
        ),
        (
            "Vocational institute 10-year income tax holiday and Florence Agreement scientific apparatus",
            "Education Sector Guide",
            "Vocational Institute Investment Incentives",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_mining_sector_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Overview of the mining sector precious metals precious stones and industrial minerals",
            "Mining & Mineral Extraction Sector Guide",
            "Mineral Classification & Licensing",
        ),
        (
            "Mineral royalties on gross market value and mining corporation tax 30 percent",
            "Mining & Mineral Extraction Sector Guide",
            "Mineral Royalties & Corporate Tax",
        ),
        (
            "Mining exploration depreciable assets 100 percent immediate deduction and mine rehabilitation fund",
            "Mining & Mineral Extraction Sector Guide",
            "Special Mining Deductions",
        ),
        (
            "Subcontractors in mining operations 10 percent final withholding tax",
            "Mining & Mineral Extraction Sector Guide",
            "Subcontractor Withholding Tax",
        ),
        (
            "Sand extractors and quarry operations commercial permits and NEMA EIA",
            "Mining & Mineral Extraction Sector Guide",
            "Sand & Quarry Compliance",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])

def test_entertainment_sector_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Overview of the entertainment sector public entertainment events and promoters",
            "Entertainment & Public Events Sector Guide",
            "Regulatory Licensing & Setup",
        ),
        (
            "Non-resident public entertainer Congolese musician withholding tax 15 percent",
            "Entertainment & Public Events Sector Guide",
            "15% final WHT on gross payments",
        ),
        (
            "Concert tickets VAT calculation 18 percent inclusive and gate audit",
            "Entertainment & Public Events Sector Guide",
            "18% inclusive VAT on concert tickets",
        ),
        (
            "Events companies and production studios corporate sponsorship invoice EFRIS",
            "Entertainment & Public Events Sector Guide",
            "Corporate Sponsorship",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_schools_curriculum_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "A-Level tax curriculum economics resource book and orientation manual",
            "Schools Tax Curriculum & Educational Resources",
            "A-Level Economics Resource Book",
        ),
        (
            "O-Level tax curriculum entrepreneurship education textbook and training manual",
            "Schools Tax Curriculum & Educational Resources",
            "O-Level Entrepreneurship Education TextBook",
        ),
        (
            "Schools tax curriculum national secondary school integration with NCDC and MoES",
            "Schools Tax Curriculum & Educational Resources",
            "National Curriculum Integration",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_real_estate_sector_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Overview of real estate land dealers and property developers",
            "Real Estate & Property Sector Guide",
            "Market Segments & Players",
        ),
        (
            "Real estate sector commercial land sales 30 percent corporate income tax",
            "Real Estate & Property Sector Guide",
            "30% CIT on net sales profits",
        ),
        (
            "Supply of unimproved land VAT exemption under Schedule 2",
            "Real Estate & Property Sector Guide",
            "unimproved bare land",
        ),
        (
            "Real estate agents licensed by MLHUD and property management fees",
            "Real Estate & Property Sector Guide",
            "Property Managers & Agents",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_fishing_sector_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Overview of the fishing sector fishermen fishmongers and industrial processors",
            "Fishing & Fisheries Sector Guide",
            "Fisheries Value Chain & Licensing",
        ),
        (
            "Fresh fish VAT exempt versus processed fish standard rate 18 percent",
            "Fishing & Fisheries Sector Guide",
            "100% VAT-exempt",
        ),
        (
            "Fish products export zero rated VAT under Schedule 3",
            "Fishing & Fisheries Sector Guide",
            "zero-rated (0% VAT)",
        ),
        (
            "Aquaculture inputs fish eggs, fry and fingerlings duty free",
            "Fishing & Fisheries Sector Guide",
            "EACCMA Customs Exemptions",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])

def test_transport_sector_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Overview of the transport sector passenger and goods vehicles TLB",
            "Commercial Transport Sector Guide",
            "TLB Unified Assessment",
        ),
        (
            "Aircraft operators income tax exemption and foreign transporters exemption",
            "Commercial Transport Sector Guide",
            "Statutory Income Tax Exemptions",
        ),
        (
            "Commercial vehicles 20 tonnes and road tractors for semi-trailers import duty free",
            "Commercial Transport Sector Guide",
            "Customs Reductions & Exemptions",
        ),
        (
            "Transport sector goods vehicles Nimpandikisa nta omulimo gunu nk’obusubuzi",
            "Commercial Transport Sector Guide",
            "Runyankole/Rukiga",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])

def test_government_agencies_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Taxation of government agencies and designated withholding agents obligations",
            "Government Agencies & MDAs Taxation Guide",
            "Public Agency Scope & Registration",
        ),
        (
            "Government agencies designated withholding agents 6 percent WHT on procurement",
            "Government Agencies & MDAs Taxation Guide",
            "Designated Withholding Agent Mandate",
        ),
        (
            "Government agency municipal fee collections reconciliation on e-Tax portal",
            "Government Agencies & MDAs Taxation Guide",
            "Other Non-Tax Revenue (ONTR) Reconciliation",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_opportunities_portal_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "Procurement management system PMS supplier portal registration and tenders",
            "Opportunities, Tenders & Auctions Portal",
            "PMS Supplier Registration",
        ),
        (
            "Tender user manuals sourcing suppliers help manual download",
            "Opportunities, Tenders & Auctions Portal",
            "Tender Sourcing Manuals",
        ),
        (
            "Auctioning application for URA assets RVD and bidding opportunities",
            "Opportunities, Tenders & Auctions Portal",
            "Online Public Auctions",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


def test_research_publications_navigation():
    tool = ToolRegistry.get("navigate_external_portal")
    assert tool is not None

    cases = [
        (
            "URA research lab research FAQs anonymised tax data access",
            "Research Lab & Corporate Publications Repository",
            "URA Research Lab",
        ),
        (
            "Download revenue performance reports annual collection statistics",
            "Research Lab & Corporate Publications Repository",
            "Annual Revenue Performance Reports",
        ),
        (
            "Download corporate plans strategic plan FY2025/26 and client satisfaction survey report",
            "Research Lab & Corporate Publications Repository",
            "Corporate Strategic Plans",
        ),
    ]

    for text, expected_state, expected_step_keyword in cases:
        r = tool.execute(extracted_text=text)
        assert r["ok"] is True
        assert expected_state in r["screen_diagnostic"]["detected_state"]
        assert any(expected_step_keyword.lower() in s.lower() for s in r["navigation_guidance"]["steps"])


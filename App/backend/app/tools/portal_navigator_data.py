"""Data and diagnostic rules for URA external portal navigation."""

from __future__ import annotations

import re
from typing import Any

EXTERNAL_URA_PORTALS: dict[str, dict[str, Any]] = {
    "e_services": {
        "name": "URA e-Services Web Portal",
        "canonical_url": "https://portal.ura.go.ug",
        "status": "operational",
        "category": "domestic_taxes",
        "service_scope": [
            "TIN Registration & Amendment",
            "Annual & Monthly Return Filing (VAT, PAYE, CIT, WHT)",
            "Assessment Ledger & Statement of Account",
            "Tax Clearance Certificates (TCC)",
            "Objections & Appeals Lodging (Section 24)",
        ],
        "common_issues": {
            "session_timeout": {
                "symptoms": ["session expired", "timed out", "inactivity", "unauthorized"],
                "cause": "Security inactivity timeout (15 minutes threshold).",
                "steps": [
                    "Click 'Back to Login' on the URA e-Services portal header.",
                    "Enter your 10-digit TIN and password; enter the SMS/Email OTP if prompted.",
                    "Re-open the target return or registration schedule directly from the left navigation menu.",
                ],
            },
            "excel_template_error": {
                "symptoms": ["template error", "macro disabled", "invalid format", "formula error", "upload rejected"],
                "cause": "Corrupted or outdated Excel return template, or macros disabled.",
                "steps": [
                    "Download a fresh official offline Excel template directly from the e-Services download page.",
                    "Ensure macros are enabled in Excel before filling (click 'Enable Content').",
                    "Paste values only (Ctrl+Shift+V) without modifying pre-set column headers or formula cells.",
                    "Generate the encrypted upload file and re-upload on the portal.",
                ],
            },
        },
    },
    "prn_payments": {
        "name": "URA PRN & Payment Gateway Portal",
        "canonical_url": "https://portal.ura.go.ug/payment",
        "status": "operational",
        "category": "revenue_collection",
        "service_scope": [
            "Payment Registration Number (PRN) Generation",
            "PRN Status Search & Verification",
            "Bank Gateway & Mobile Money Routing",
            "E-Receipt Download",
        ],
        "common_issues": {
            "missing_payment_mode": {
                "symptoms": ["missing mandatory", "payment mode", "select bank", "payment gateway"],
                "cause": "The payment mode or commercial bank dropdown has not been selected.",
                "steps": [
                    "Confirm the Tax Head code (e.g. 0010 for VAT, 0011 for Income Tax).",
                    "Scroll to 'Payment Mode' and select your commercial bank or Mobile Money (MTN / Airtel).",
                    "Click the green 'Generate PRN' button, copy the 12-14 digit PRN, and complete payment via bank app or *165# / *185#.",
                ],
            },
            "expired_prn": {
                "symptoms": ["prn expired", "payment failed", "invalid prn", "prn not found"],
                "cause": "Statutory PRN validity window (21-30 days depending on tax type) has lapsed.",
                "steps": [
                    "Do not attempt payment on the expired PRN.",
                    "Navigate to 'Generate PRN' under e-Services.",
                    "Select the liability assessment and generate a fresh PRN with a new validity window.",
                ],
            },
        },
    },
    "efris": {
        "name": "URA EFRIS Invoicing & Fiscalization Portal",
        "canonical_url": "https://efris.ura.go.ug",
        "status": "operational",
        "category": "electronic_invoicing",
        "service_scope": [
            "E-Invoice & E-Receipt Issuance",
            "Fiscal Device Management (EFD / SDC)",
            "Commodity Code & Stock Ledger Reconciliation",
            "Offline Synchronization Monitoring",
        ],
        "common_issues": {
            "offline_sync_overdue": {
                "symptoms": ["offline", "sync", "24 hours", "synchronization overdue", "fiscal device blocked"],
                "cause": "Offline invoicing buffer has exceeded the mandatory statutory 24-hour transmission window.",
                "steps": [
                    "Verify internet connectivity on the EFRIS fiscal device or POS integration server.",
                    "Open the EFRIS client application and navigate to 'System Management' > 'Offline Sync'.",
                    "Click 'Sync All Pending Records' to transmit encrypted fiscal vouchers to URA servers.",
                ],
            },
            "invalid_buyer_tin": {
                "symptoms": ["invalid tin", "buyer tin", "tin unverified", "purchaser tin"],
                "cause": "Buyer TIN is not registered for VAT or has invalid status in the national tax register.",
                "steps": [
                    "Verify the buyer's TIN using the URA e-Services TIN Search tool before issuing B2B invoice.",
                    "If the buyer is unregistered or an individual consumer, select 'Walk-in / Consumer' without entering a TIN.",
                ],
            },
        },
    },
    "asycuda": {
        "name": "URA Customs ASYCUDA World Portal",
        "canonical_url": "https://customs.ura.go.ug",
        "status": "operational",
        "category": "customs_and_border",
        "service_scope": [
            "Customs Bill of Entry & Declaration",
            "Manifest Ingestion & Verification",
            "EAC Common External Tariff (CET) HS Code Clearance",
            "Single Customs Territory (SCT) Clearance",
        ],
        "common_issues": {
            "hs_code_discrepancy": {
                "symptoms": ["hs code", "tariff code", "valuation error", "classification discrepancy"],
                "cause": "Declared Harmonized System (HS) code does not match item documentation or duty rate.",
                "steps": [
                    "Cross-reference product description against the EAC Common External Tariff Schedule.",
                    "Ensure correct 8-digit or 10-digit national sub-heading code is entered.",
                    "Attach commercial invoice, bill of lading, and packing list in clean PDF format.",
                ],
            },
        },
    },
    "export_process": {
        "name": "URA Customs Export Process Portal (https://ura.go.ug/en/the-exports-process/)",
        "canonical_url": "https://ura.go.ug/en/the-exports-process/",
        "status": "operational",
        "category": "customs_and_border",
        "service_scope": [
            "Export Procedures & Customs Declaration (Asycuda SAD)",
            "Key Documents Required to Export (Invoice, Packing List, Certificate of Origin)",
            "Prohibited & Restricted Goods Schedule (EACCMA Third Schedule)",
            "Export Incentives, Duty Drawback & VAT Zero-Rating (0%)",
            "REX System & Preferential Rules of Origin (EAC, COMESA, AGOA, GSP)",
            "Main Export Commodities (Coffee, Tea, Cement, Fish, Horticulture)",
            "One Stop Border Post (OSBP) & Regional Electronic Cargo Tracking (RECTS)",
            "Quality Standards & Sanitary/Phytosanitary Certification (MAAIF, UNBS)",
        ],
        "common_issues": {
            "restricted_export_permit_missing": {
                "symptoms": ["restricted goods", "timber export", "scrap metal", "permit missing"],
                "cause": "Exporting restricted goods under EACCMA Third Schedule Part B requires authorization from the relevant ministry.",
                "steps": [
                    "Obtain formal export authorization letter from the Ministry of Trade, Industry and Cooperatives (MTIC).",
                    "Attach phytosanitary certificate from MAAIF or forestry permit before lodging Asycuda export SAD.",
                ],
            },
            "origin_certificate_discrepancy": {
                "symptoms": ["rex rejected", "origin denied", "preferential tariff lost"],
                "cause": "Certificate of Origin does not comply with partner state rules of origin or REX declaration format.",
                "steps": [
                    "Ensure company is registered on the REX system for European/GSP exports.",
                    "Verify EAC or COMESA criteria on value addition and local material content.",
                ],
            },
        },
    },
    "customs_valuation": {
        "name": "URA Customs Valuation & Clearance Portal",
        "canonical_url": "https://ura.go.ug/en/category/imports-exports/customs-valuation/",
        "status": "operational",
        "category": "customs_and_border",
        "service_scope": [
            "Methods of Customs Valuation (6 Sequential Methods under WTO ACV & EACCMA)",
            "Customs Legal and Security Bonds (Transit CB1, Warehousing CB2, EAC Bond Section 107)",
            "Clearing Agents Licensing & Compliance (Section 145(1) EACCMA & Reg. 149-152)",
            "Motor Vehicle Value Guide (Statutory CIF Baselines As At August/September 2026)",
            "Revised General Goods Database (Reference Pricing & Valuation Risk Profiling)",
            "New Asycuda Validation Requirements (Container & Vehicle Plate NII Upgrades, Sept 2026)",
            "Document Processing Centre (DPC) Rulings & Appeals Workflow",
        ],
        "common_issues": {
            "valuation_uplift_notice": {
                "symptoms": ["valuation query", "dpc uplift", "invoice rejected", "reference price applied"],
                "cause": "Declared transaction value is significantly lower than the Revised General Goods Database reference benchmark.",
                "steps": [
                    "Submit proof of actual payment (swift confirmation, bank statement, telegraphic transfer TT).",
                    "Attach manufacturer sales contract, export declaration from country of origin, and purchase order.",
                ],
            },
            "expired_customs_bond": {
                "symptoms": ["bond invalid", "bond limit exceeded", "guarantee expired"],
                "cause": "Customs security bond CB1/CB2 has expired or reached maximum allowable penalty liability.",
                "steps": [
                    "Apply for bond renewal with your insurance underwriter or commercial bank.",
                    "Submit endorsed bond renewal endorsement to the URA Bond Management Unit in Nakawa.",
                ],
            },
        },
    },
    "single_customs_territory": {
        "name": "URA Single Customs Territory (SCT) Clearance Portal",
        "canonical_url": "https://ura.go.ug/en/category/imports-exports/single-customs-territory/",
        "status": "operational",
        "category": "customs_and_border",
        "service_scope": [
            "SCT Declaration & Clearance at First Point of Entry (Mombasa / Dar es Salaam)",
            "Mutual Recognition of Clearing Agents & Third-Party Declarants",
            "Regional Electronic Cargo Tracking System (RECTS Free Satellite Seals)",
            "SCT Fuel Ingestion & 10-Day Discharge Clearance Window",
            "RCTG Bond Management & COMESA Transit Guarantee Monitoring",
            "Contraband Manifest Splitting & C9/C11 Amendments",
            "Cargo Loss, Theft & Accident Incident Procedures",
            "Container Freight Stations (CFS) Clearance Operations",
        ],
        "common_issues": {
            "sct_fuel_delay_penalty": {
                "symptoms": ["fuel entry delayed", "10 days exceeded", "vessel discharge penalty"],
                "cause": "Under SCT regulations, fuel declarations must be lodged within 10 days after vessel discharge.",
                "steps": [
                    "Lodge fuel declaration in Asycuda within the 10-day statutory window.",
                    "Settle duty PRN immediately to avoid demurrage and late filing penalties.",
                ],
            },
            "rctg_bond_capacity_exhausted": {
                "symptoms": ["rctg bond exhausted", "transit hold", "active carnet pending"],
                "cause": "Active carnets have not been validated at destination, locking the clearing agent's transit guarantee limit.",
                "steps": [
                    "Check COMESA RCTG system on the UESW portal for pending destination validations.",
                    "Ensure destination customs officer completes T1 arrival confirmation to replenish the bond.",
                ],
            },
        },
    },
    "exempt_importation": {
        "name": "URA Comprehensive List of Exempt Importations (EACCMA Fifth Schedule & VAT Act)",
        "canonical_url": "https://ura.go.ug/en/import-export/comprehensive-list-of-exempt-importation/",
        "status": "operational",
        "category": "customs_and_border",
        "service_scope": [
            "Exempt Importations Without Pre-Approval (APC 472, 475, 478, 492)",
            "Agricultural Machinery & Implements (Tractors, Sprayers, Irrigation - APC 478)",
            "Fertilizers & Agro-Chemical Compounds (Item 11 Part B 5th Schedule - APC 472)",
            "Diagnostic Reagents & Medical Equipment (NDA Verification - Item 14 - APC 472)",
            "Solar & Wind Energy Equipment (Deep Cycle Batteries & PV Modules - Item 26 - APC 472)",
            "Hotel Operational Equipment (UHOA Endorsement & Engraved Logos - Item 21 - APC 472)",
            "Industrial Machinery Replacement Spare Parts (Chapters 84 & 85, UMA/MTIC - Item 31 - APC 492)",
            "Oil, Gas & Geothermal Machinery & Inputs (PAU/MEMD Recommendation - Item 30(a) - APC 475)",
            "Pharmaceutical Raw Materials & Medicament Packaging (NDA Approved - Item 16 - APC 472)",
            "Poultry Parent Stock, Incubator Eggs & Beekeeping Gear (MAAIF - Item 15 - APC 472)",
            "Packing Materials Designed for Export Goods (Marked 'For Export Only' - Item 2(e) - APC 472)",
            "Imported Drugs, Medicines & Medical Sundries (Section 18(10) VAT Act - APC 478)",
            "Animal Feeds & Nutritional Mixed Components (MAAIF Permit - APC 478)",
            "Medical PPE & Protective Wear Raw Materials (UNBS & NDA Certified - APC 478)",
        ],
        "common_issues": {
            "missing_agency_recommendation": {
                "symptoms": ["apc rejected", "recommendation missing", "exemption blocked", "maaif permit required"],
                "cause": "Fast-track exemption under APC 472/475/478/492 requires mandatory endorsement from the designated authority (MAAIF, NDA, UHOA, UMA, MTIC, PAU).",
                "steps": [
                    "Obtain official import permit / recommendation from the relevant regulatory agency before shipment.",
                    "Upload permit to Asycuda World and enter the correct statutory Additional Procedure Code (APC).",
                ],
            },
            "non_deep_cycle_battery_rejection": {
                "symptoms": ["solar battery rejected", "cranking battery", "solar exemption denied"],
                "cause": "Item 26 Part B strictly exempts deep cycle batteries. Standard automotive / cranking batteries do not qualify.",
                "steps": [
                    "Attach manufacturer's technical data sheets proving deep cycle solar/wind specification.",
                    "Ensure batteries are invoiced with compatible solar/wind generating equipment.",
                ],
            },
            "unengraved_hotel_goods": {
                "symptoms": ["hotel equipment rejected", "logo missing", "commercial diversion risk"],
                "cause": "Item 21 Part B requires hotel operational equipment to be permanently engraved or printed with the hotel logo.",
                "steps": [
                    "Ensure all operational goods (cutlery, linen, TVs, fridges) are permanently marked with the hotel logo.",
                    "Present valid Tourism License and endorsement letter from Uganda Hotel Owners Association (UHOA).",
                ],
            },
        },
    },
    "aeo": {
        "name": "URA Authorized Economic Operator (AEO) Portal & Program",
        "canonical_url": "https://ura.go.ug/en/category/imports-exports/authorized-economic-operator/",
        "status": "operational",
        "category": "customs_and_border",
        "service_scope": [
            "About the AEO Program & WCO SAFE Framework 3 Pillars",
            "AEO Prospective Clients (Importers, Exporters, Clearing Agents, Warehouses, Transporters)",
            "Objectives of the Uganda AEO Scheme",
            "Eligibility Criteria for Becoming an AEO (3-Year Tax Compliance, Financial Soundness, Internal Controls)",
            "Processes of Attaining AEO Status (EOI, Self-Assessment, Vetting, Onsite Inspection, MOU)",
            "Benefits of the AEO Program (WHT Exemption, Self-Management of Bonded Warehouses, Onsite Physical Exam, EAC MRA)",
            "AEO Enterprise Risk Management (AEO ERM Portal, 40-Day Fast-Track, e-Certificate, Audit Responses)",
        ],
        "common_issues": {
            "aeo_eligibility_failure": {
                "symptoms": ["aeo rejected", "compliance history insufficient", "3 years compliance required"],
                "cause": "Applicants must demonstrate at least 3 years of clean compliance history with URA domestic taxes and customs.",
                "steps": [
                    "Review past 3-year filing and payment compliance across Income Tax, VAT, PAYE, and Customs.",
                    "If unapproved, enroll in the Compliance Improvement Plan (CIP) to resolve compliance gaps.",
                ],
            },
            "aeo_erm_account_access": {
                "symptoms": ["aeo portal login error", "tin not authorized on erm", "erm access denied"],
                "cause": "Taxpayer TIN must have active customs role permissions or linked declarant profile.",
                "steps": [
                    "Log into AEO ERM portal using official business TIN.",
                    "Ensure company directors or registered clearing agents are linked to the TIN profile.",
                ],
            },
        },
    },
    "customs_audits_refunds": {
        "name": "URA Customs Audits and Refunds Portal & Division",
        "canonical_url": "https://ura.go.ug/en/category/imports-exports/customs-audits-and-refunds/",
        "status": "operational",
        "category": "customs_and_border",
        "service_scope": [
            "Instalment Payment of URA Taxes for Motor Vehicles & General Goods (DCU MOU)",
            "Diplomatic Customs Fuel & Excise Duty Refunds (Section 114(1) & Form C34)",
            "General Customs Duty Refunds (Sections 143, 144, & Form C33/C34)",
            "Duty Drawback (DDB) Manufacturer Registration (Sections 138-140 & Form C30)",
            "Duty Drawback (DDB) Export Claims (Form C31 & 12-Month Rule)",
            "Public International Organizations & Diplomatic Missions Directory (87 Accredited Bodies)",
            "Customs Warehouse Sale Proceeds Recovery (Section 57(3) & 57(4))",
        ],
        "common_issues": {
            "claim_time_bar_rejection": {
                "symptoms": ["refund rejected", "claim time barred", "12 months exceeded"],
                "cause": "Under EACCMA Sections 138(2)(c), 143(2), and 144(2), claims for customs duty refund or drawback must be lodged within 12 months from payment or export date.",
                "steps": [
                    "Ensure claims are lodged with Supervisor Refunds Unit within 12 calendar months.",
                    "Attach certified date stamped copies of customs entries and payment bank slips.",
                ],
            },
            "missing_mfa_authentication": {
                "symptoms": ["diplomatic refund blocked", "c34 unverified", "mfa approval missing"],
                "cause": "Diplomatic and fuel refund claims under Form C34 require mandatory authentication from the Ministry of Foreign Affairs (MFA).",
                "steps": [
                    "Submit Form C34 to Ministry of Foreign Affairs (MFA) for signature and official stamp.",
                    "Attach valid MFA Form 3 duty-free fuel authorization and approved mission vehicle list.",
                ],
            },
            "input_output_ratio_dispute": {
                "symptoms": ["duty drawback rejected", "yield rate disagreement", "tid formula missing"],
                "cause": "Drawback claims under Section 139 require pre-approved Input-Output yield coefficients fixed by Tariff and Information Division (TID).",
                "steps": [
                    "Register with CCD using Form C30 before exporting finished manufactured products.",
                    "Complete site verification with URA team to establish official input-output ratios with TID.",
                ],
            },
        },
    },
    "warehousing": {
        "name": "URA Customs Warehousing, Public Online Auction & Private Treaty Portal",
        "canonical_url": "https://ura.go.ug/en/category/imports-exports/warehousing/",
        "status": "operational",
        "category": "customs_and_border",
        "service_scope": [
            "Customs Bonded Warehouses (Public, Private, Car Bonds, Manufacture-Under-Bond)",
            "Warehousing Processes (Cargo Receiving, IM7 Auto-Conversion, CB6 Bond, IM4 Exit)",
            "Statutory Warehousing Periods (6-Month General Limit, 9-Month Extension, 2-Year Special Goods)",
            "Prohibited Warehousing Goods (Acids, Explosives, Perishables, Inflammables, Matches)",
            "Provisional Release & Verification at Owner's Premises (SCT-PEV & WT8)",
            "Public Online Auction via UeSW (singlewindow.go.ug/auction, $10 Fee, 24-48h PRN)",
            "Private Treaty Disposal Sales (Free Participation, Direct Negotiation, Unsold Lots)",
            "Want of Entry List & Cargo Redemption (14-Day Rule & Section 42 Notice)",
            "Enforcement Guidelines for Bonded Warehouses (Reflector Jackets, Uniforms, Sections 64 & 67 EACCMA)",
        ],
        "common_issues": {
            "overstayed_warehouse_auction": {
                "symptoms": ["cargo gazetted for auction", "270 days exceeded", "un-customed goods notice"],
                "cause": "Under EACCMA Section 42(1), goods exceeding the 6-month (or 9-month extended) warehousing limit are gazetted for public auction after a 30-day notice.",
                "steps": [
                    "Pay all accrued taxes, storage fees, penalties, and fines within 30 days of the public notice.",
                    "Lodge redemption declaration in Asycuda World to have the lot removed from the auction catalogue.",
                ],
            },
            "auction_bid_payment_default": {
                "symptoms": ["bid cancelled", "prn expired in auction", "24 hours exceeded"],
                "cause": "Winning bidders must pay 100% of the bid price via PRN within 24 to 48 hours; failure triggers forfeiture to the next bidder.",
                "steps": [
                    "Generate PRN immediately upon winning notification on singlewindow.go.ug/auction.",
                    "Settle payment via mobile money, bank counter, or credit card within the 24-48 hour window.",
                ],
            },
            "want_of_entry_hold": {
                "symptoms": ["want of entry", "cargo detained 14 days", "un-entered cargo"],
                "cause": "Cargo not entered within 14 days of arrival at a port or border is moved to the state warehouse as Want of Entry.",
                "steps": [
                    "Lodge Bill of Entry in Asycuda World attaching invoice, packing list, and bill of lading.",
                    "Pay outstanding taxes and warehouse rent charges to secure customs release.",
                ],
            },
        },
    },
    "customs_enforcements": {
        "name": "URA Customs Enforcement & Compliance Division",
        "canonical_url": "https://ura.go.ug/en/category/imports-exports/customs-enforcements/",
        "status": "operational",
        "category": "customs_and_border",
        "service_scope": [
            "Prohibited & Restricted Goods Schedule (Used Laptops, Chemicals, Section 210)",
            "Passenger Accompanied Baggage at Entebbe Airport (USD 500 Limit & USD 2000 TIN Rule)",
            "Transit Cargo Monitoring & 21 Gazetted Routes (Form C17, 30-Day Window, Security Bond)",
            "Transit Goods License (TGL) vs Road User Charges (RUC) (Form C39, USD 200 Fee)",
            "Regional Electronic Cargo Tracking System (RECTS) (e-Seals, Free Tracking, CMC/RRU)",
            "Customs Offences, Seizure Notices & Compounding (Form C37, Form C35, Section 219)",
            "Non-Intrusive Inspection (NII) & Cargo Scanning (Free of Charge, 1-Min Scan, Radiation Safety)",
        ],
        "common_issues": {
            "prohibited_goods_seizure": {
                "symptoms": ["prohibited goods seized", "used laptop confiscated", "form c37 issued"],
                "cause": "Under Finance Act 2009 and EACCMA, prohibited goods (used electronics, used underwear) are liable to mandatory forfeiture, destruction, and penalties up to 50% value.",
                "steps": [
                    "Acknowledge Seizure Notice Form C37 issued by customs officer.",
                    "Submit Form C35 Request to Settle Offence by Compounding under Section 219 EACCMA.",
                    "Pay assessed compounding penalties; arrange for goods destruction or re-export at owner's cost.",
                ],
            },
            "transit_seal_violation": {
                "symptoms": ["rects alert", "seal tampered", "route violation", "tmu hold"],
                "cause": "Deviating from gazetted transit corridors or tampering with RECTS e-Seals triggers automated alerts at the Central Monitoring Center (CMC).",
                "steps": [
                    "Remain at the location and contact the Rapid Response Unit (RRU) on 0323 442500 / 0323 442192.",
                    "Do not attempt to break or remove electronic seals without an authorized customs officer present.",
                ],
            },
            "baggage_exceeds_allowance": {
                "symptoms": ["entebbe baggage taxed", "usd 500 exceeded", "red channel referral"],
                "cause": "Passenger accompanied baggage exceeding USD 500 in value, or goods for commercial resale, attract standard import duty and VAT.",
                "steps": [
                    "Proceed to the Red Channel at Entebbe Airport and declare all items with purchase receipts.",
                    "If goods exceed USD 2,000, appoint a licensed clearing agent and clear via Asycuda using TIN.",
                ],
            },
        },
    },
    "laws_and_acts": {
        "name": "URA Laws, Acts, Regulations & Legal Repository",
        "canonical_url": "https://ura.go.ug/download-category/laws-and-acts/",
        "status": "operational",
        "category": "legal_and_policy",
        "service_scope": [
            "Compendium of Domestic Tax Laws (Income Tax, VAT, TPCA, Stamp Duty, Excise Duty)",
            "The East African Community Customs Management Act (EACCMA 2004)",
            "EAC Common External Tariff (CET 2022 4-Band Structure)",
            "EAC & COMESA Rules of Origin Protocols (2015)",
            "Procedure Manual for Duty Remission Regulations (2008)",
            "Automatic Exchange of Information (AEOI) & Common Reporting Standard (CRS) Act 2023",
            "The Tax Appeals Tribunal Act (Cap. 345 & Section 15 30% Rule)",
            "Anti-Money Laundering Act 2013 & Amendment Regulations 2023",
            "The Free Zones Act 2014 (Incentives & Export Quotas)",
            "Traffic and Road Safety Amendment Acts (Registration & Environmental Levy)",
            "The East African Tax Law Reports (Precedents & Case Law)",
        ],
        "common_issues": {
            "statutory_amendment_confusion": {
                "symptoms": ["outdated law cited", "previous rate applied", "amendment act missing"],
                "cause": "Tax rates and procedures are amended annually by Finance Acts and amendment statutes (e.g. VAT Amendment Act 2023, Excise Duty Amendment Acts).",
                "steps": [
                    "Visit ura.go.ug/download-category/laws-and-acts/ to download the latest consolidated Acts and Gazette addenda.",
                    "Review specific annual amendment schedules to confirm the effective fiscal year start date.",
                ],
            },
            "tat_30_percent_deposit_dispute": {
                "symptoms": ["tat appeal blocked", "section 15 deposit required", "30 percent payment hold"],
                "cause": "Under Section 15 of the Tax Appeals Tribunal Act, a taxpayer appealing an assessment must deposit 30% of the tax in dispute or the undisputed amount (whichever is greater).",
                "steps": [
                    "Pay the mandatory 30% deposit via URA PRN before lodging application with TAT registry.",
                    "Alternatively, apply for waiver of payment under Section 24 TPCA if hardship criteria are met.",
                ],
            },
        },
    },
    "health_sector": {
        "name": "URA Health and Medical Sector Taxation Guide",
        "canonical_url": "https://ura.go.ug/en/a-guide-to-taxation-of-the-health-and-medical-sector/",
        "status": "operational",
        "category": "tax_education",
        "service_scope": [
            "Healthcare Sector Structure (Public 66%, PNFP/PFPs 34%, Faith-Based Bureaus UCMB/UPMB/UOMB/UMMB)",
            "Herbal Shops (Medicinal Plants, NDA Licensing, URSB Name, Presumptive/Individual Tax)",
            "Pharmacies (Licensed Pharmacist, 30% CIT, PAYE, 6% WHT) vs Drug Shops (Class C OTC, 1.5 km Separation)",
            "Pharmaceutical Manufacturers (Raw Materials Exemption, Medicament Packaging, EACCMA Fifth Schedule)",
            "Hospital Equipment Tax Exemptions (Shadowless Lamps, Blood Freezers, X-Ray, Ultrasound, Wheelchairs)",
            "Specialized Hospital Developers (USD 5M Investment, Nil Stamp Duty, Feasibility & Machinery VAT Free)",
            "10-Year Income Tax Holiday for Medical Appliances & Pharmaceutical Plants (Industrial Park Criteria)",
        ],
        "common_issues": {
            "missing_nda_license": {
                "symptoms": ["nda verification failed", "drug shop license expired", "unregistered pharmacy"],
                "cause": "All pharmacies, drug shops, and herbal medicine outlets require annual licensing by the National Drug Authority (NDA).",
                "steps": [
                    "Obtain NDA operational license or premises permit before commercial opening.",
                    "Upload NDA permit and professional council certificate (UMDPC / UNMC) during URA tax registration.",
                ],
            },
            "unmarked_hospital_supplies": {
                "symptoms": ["hospital exemption rejected", "eaccma fifth schedule denied", "logo missing"],
                "cause": "Under Part B Fifth Schedule EACCMA, hospital equipment and supplies must be permanently printed or engraved with the hospital logo.",
                "steps": [
                    "Ensure imported goods (mattresses, linen, fridges, kitchenware) are indelibly printed with the registered hospital logo.",
                    "Present valid health facility operating license and clearance from the Ministry of Health.",
                ],
            },
        },
    },
    "business_formalisation": {
        "name": "URA Business Formalisation & Onboarding Portal",
        "canonical_url": "https://ura.go.ug/en/business-formalisation/",
        "status": "operational",
        "category": "tax_education",
        "service_scope": [
            "3-Step Formalisation Process (URSB Business Registration -> URA TIN -> KCCA/LC Trading License)",
            "13 Major Business Formalisation Benefits (Commercial Loans >50M, TCCs, Tenders, Brand Protection)",
            "First-Time e-Tax Account Activation & Secure Password Setup (ura.go.ug Login)",
            "Statutory Filing Calendars (Provisional Income Tax, Monthly VAT/PAYE by 15th)",
            "English Record Keeping Mandate (5-Year Rule under Section 15 TPCA)",
            "Transition from Presumptive Sole Proprietor to Formal Corporate Entity",
        ],
        "common_issues": {
            "first_time_login_failure": {
                "symptoms": ["initial password expired", "first login failed", "default password invalid"],
                "cause": "The temporary default password generated upon TIN registration must be used to set a permanent password.",
                "steps": [
                    "Visit ura.go.ug and click 'Login' on the top-right header.",
                    "Enter your 10-digit TIN as Login ID and the default password received via SMS or email.",
                    "Promptly set a new password containing uppercase, lowercase, numbers, and symbols (e.g. January@2030).",
                ],
            },
            "ursb_company_name_unlinked": {
                "symptoms": ["ursb number not found", "company registration mismatch", "form 20 missing"],
                "cause": "URSB integration sync latency or company registration details pending final registration.",
                "steps": [
                    "Verify your URSB Certificate of Incorporation and Form 20 on the URSB online portal.",
                    "Ensure directors' personal 10-digit TINs are active and validated prior to corporate registration.",
                ],
            },
        },
    },
    "oil_and_gas": {
        "name": "URA Petroleum, Oil & Gas Sector Taxation Portal",
        "canonical_url": "https://ura.go.ug/en/petroleum-sector-q-a/",
        "status": "operational",
        "category": "tax_education",
        "service_scope": [
            "Petroleum Sector Value Chain (Upstream Exploration, Midstream EACOP, Downstream White/Black Products)",
            "Regulatory Licensing (URSB, MEMD, PAU National Supplier Database NSD, URA)",
            "Core Taxes (30% CIT, 10% Contractor WHT, 15% Branch Profit Tax, Specific Fuel Excise Duty)",
            "Mandatory Fuel Station EFRIS Integration & Electronic Dispenser Controllers (EDCs)",
            "Petroleum Tax Incentives (Item 30(a) Part B EACCMA 5th Schedule, Deemed VAT Section 24(5))",
            "EACOP Fiscal Regime (10-Yr CIT Holiday, 5% Technical WHT, 0% Transit Fees, UGX 10k Stamp Duty)",
            "Dedicated URA Petroleum Division & Fast-Track EACOP Desk",
        ],
        "common_issues": {
            "edc_pump_sync_error": {
                "symptoms": ["edc disconnected", "fuel pump offline", "dispenser efris error"],
                "cause": "Fuel dispenser controller lost communication with EFRIS fiscal server.",
                "steps": [
                    "Check network cable and EDC controller interface box at fuel dispensing island.",
                    "Log in to EFRIS and check status of Electronic Dispenser Controller under Terminal Management.",
                    "Contact accredited POS integrator if pulse count or calibration mismatch occurs.",
                ],
            },
            "deemed_vat_rejection": {
                "symptoms": ["deemed vat rejected", "contractor vat assessed", "section 24(5) denied"],
                "cause": "Contractor is not supplying an approved upstream Licensee or lacks PAU approval.",
                "steps": [
                    "Verify Licensee petroleum exploration or production license with MEMD.",
                    "Attach Joint Operating Agreement (JOA) and PAU National Supplier Database (NSD) certificate.",
                ],
            },
        },
    },
    "agriculture_sector": {
        "name": "URA Agricultural Sector Taxation Guide",
        "canonical_url": "https://ura.go.ug/en/crop-farming/",
        "status": "operational",
        "category": "tax_education",
        "service_scope": [
            "Agricultural Sector Structure (68% Workforce, 24% GDP, 7M Farming Households, Section 35 Deductibility)",
            "Agribusiness Startups (<UGX 500M Capital 3-Year Income Tax Holiday Effective 1 July 2025)",
            "Floriculture & Horticulture (20% Annual Straight-Line Greenhouse Capital Deduction Over 5 Years, 0% VAT Exports)",
            "Poultry Farming (Parent Stock Duty-Free EACCMA 5th Schedule, Hatching Eggs, VAT-Exempt Feeds)",
            "Agri-Input Dealers (Tractors, Seeders, Sprayers Duty-Free; Seeds & Fertilizers 0% VAT)",
            "Agro-Processing Industry (10-Year Income Tax Holiday for 80%+ Exports Under Section 21(1)(y) ITA)",
            "2% Income Tax Deduction for Employing 5%+ Persons with Disabilities (PWDs)",
            "Nil Stamp Duty on Agricultural Insurance Policies",
        ],
        "common_issues": {
            "unapproved_chemical_import": {
                "symptoms": ["fertilizer duty applied", "seed exemption denied", "maaif permit missing"],
                "cause": "Under EACCMA Fifth Schedule Part B, imported seeds, fertilizers, and agrochemicals require official approval by the Ministry of Agriculture, Animal Industry and Fisheries (MAAIF).",
                "steps": [
                    "Obtain MAAIF import permit prior to shipment.",
                    "Submit MAAIF clearance certificate in Asycuda World under Additional Procedure Code APC 472.",
                ],
            },
            "commercial_poultry_feed_vat_dispute": {
                "symptoms": ["feed vat charged", "premixes taxed", "schedule 2 dispute"],
                "cause": "Suppliers incorrectly charging VAT on exempt animal feeds or raw mixed feed components.",
                "steps": [
                    "Verify supply falls under Second Schedule VAT Act (animal feeds, seed cake, wheat bran, concentrates).",
                    "Present URA VAT exemption certificate to supplier or request credit note.",
                ],
            },
        },
    },
    "hospitality_sector": {
        "name": "URA Hotel, Accommodation & Tourism Sector Guide",
        "canonical_url": "https://ura.go.ug/en/hotel-and-accommodation-sector/",
        "status": "operational",
        "category": "tax_education",
        "service_scope": [
            "Hotel & Accommodation Sector Structure (UHOA Partnership, UTB Licensing, KCCA Permits)",
            "Local Hotel Tax (LHT Rates: 5/4-Star $2, 3/2-Star UGX 2,000, Mid-Tier UGX 1,000, Budget UGX 500)",
            "EACCMA 5th Schedule Item 21 Duty-Free Hotel Goods (Logo-Marked Utensils, Cookers, ACs, Linen, TVs)",
            "Tour Operator & Safari Vehicle Incentives (4x4 Safari Jeeps, Sightseeing Buses, Overland Trucks, Boats)",
            "Restaurants & Outside Catering (30% CIT, Presumptive Turnover, Mandatory EFRIS Receipts, 6% WHT)",
            "Recreational Facilities (Golf Courses, Leisure Parks, Gymnasiums, Admissions VAT)",
            "Dedicated URA-UHOA 40-Page Sector Guide (Vol. 1 FY 2025-26)",
        ],
        "common_issues": {
            "unengraved_hotel_goods_rejection": {
                "symptoms": ["hotel customs exemption rejected", "item 21 denied", "logo missing"],
                "cause": "Under Item 21 Part B Fifth Schedule EACCMA, operational goods (linens, fridges, TVs, cutlery) must be permanently marked with the hotel logo.",
                "steps": [
                    "Ensure imported goods are indelibly engraved or printed with the hotel logo.",
                    "Present valid Tourism License from UTB and endorsement letter from Uganda Hotel Owners Association (UHOA).",
                ],
            },
            "lht_income_tax_deduction_error": {
                "symptoms": ["lht expense disallowed", "local hotel tax audit query"],
                "cause": "Hotel operators attempting to deduct Local Hotel Tax (LHT) as an allowable income tax business expense.",
                "steps": [
                    "Reconcile LHT as a pass-through collected on behalf of local government authorities.",
                    "Exclude LHT collections from gross trading revenue and remove from allowable deductions on Form DT-1001.",
                ],
            },
        },
    },
    "wholesale_retail_sector": {
        "name": "URA Wholesale & Retail Trade Sector Guide",
        "canonical_url": "https://ura.go.ug/en/overview-of-wholesale-and-retail/",
        "status": "operational",
        "category": "tax_education",
        "service_scope": [
            "Wholesale & Retail Trade Structure (Bulk Distribution vs Small-Unit Retailing)",
            "Small Business Presumptive Regime (Turnover UGX 10M-150M, Daily Average UGX 34,700)",
            "VAT Registration Categories (Compulsory vs Voluntary, Input-Output Offset, UGX 5M Cash Refund Rule)",
            "Mandatory EFRIS Invoicing & Pre-Filled Monthly VAT Returns",
            "Digital Tax Stamps (DTS) on Gazetted Retail Goods (Beers, Spirits, Sodas, Water, Cigarettes, Cement, Sugar)",
            "Import Taxes for General Traders (Customs Duty, 18% VAT, 6% WHT, 1.5% Infrastructure Levy)",
            "Capital Allowances (50% Initial Allowance on Plant & Machinery, 20% on Industrial Buildings Outside Kampala)",
        ],
        "common_issues": {
            "unregistered_vat_presumptive_overlap": {
                "symptoms": ["vat registration query", "presumptive threshold exceeded", "turnover above 150m"],
                "cause": "A trader operating under presumptive tax has exceeded UGX 150,000,000 annual turnover or UGX 37.5M quarterly.",
                "steps": [
                    "Immediately apply for compulsory VAT registration via portal.ura.go.ug within 20 days.",
                    "Begin issuing EFRIS fiscal receipts and file monthly Form DT-1014.",
                ],
            },
            "unsupported_input_vat_without_efris": {
                "symptoms": ["input vat disallowed", "efris invoice missing", "fdn missing"],
                "cause": "Purchases not supported by an authentic EFRIS fiscal invoice bearing a 20-digit FDN.",
                "steps": [
                    "Ensure all business suppliers issue electronic fiscal invoices at purchase.",
                    "Verify supplier TIN and 20-digit FDN on the URA Kakasa verification app before claiming credit.",
                ],
            },
        },
    },
    "construction_sector": {
        "name": "URA Construction Sector Taxation Portal",
        "canonical_url": "https://ura.go.ug/en/overview-of-the-construction-sector/",
        "status": "operational",
        "category": "tax_education",
        "service_scope": [
            "Construction Sector Structure (Civil Infrastructure, Residential, Commercial, Industrial Erection)",
            "Regulatory Licensing (URSB, UNABCEC, NEMA EIA, ERB, ARB, SRB)",
            "Taxes on Contractors (30% CIT, Form DT-1001 Twice-Yearly Provisional, PAYE for Site Workers)",
            "Withholding Tax (6% WHT on Works >UGX 1M, 6% Professional Design Fees, 15% Foreign Consultants)",
            "VAT on Civil Works (18% Standard Rate, Mandatory EFRIS Invoices, Section 24(5) Deemed VAT for Aid Projects)",
            "Customs & Equipment Incentives (0% Duty on Cranes & Surveying GPS, Duty-Free 20T+ Tippers, Bonded Temporary Imports)",
            "Industrial Building Allowance (20% Initial Allowance Outside Kampala, 5% Annual Straight-Line Depreciation)",
        ],
        "common_issues": {
            "aid_funded_deemed_vat_rejection": {
                "symptoms": ["aid project vat queried", "deemed vat rejected", "donor exemption unverified"],
                "cause": "Contractor is executing an aid-funded project but lacks an official endorsement letter from MOFPED / Donor agreement.",
                "steps": [
                    "Verify financing agreement is registered with the Ministry of Finance (MOFPED).",
                    "Attach bilateral financing agreement and project implementation contract under Section 24(5) VAT Act.",
                ],
            },
            "wht_deduction_on_exempt_materials": {
                "symptoms": ["wht deducted erroneously", "building materials withholding dispute"],
                "cause": "Client deducting 6% WHT from a construction contractor who holds an active 12-month URA WHT Exemption Certificate.",
                "steps": [
                    "Present certified copy of active URA Withholding Tax Exemption Certificate to client.",
                    "Client verifies certificate barcode on portal.ura.go.ug before releasing gross payment.",
                ],
            },
        },
    },
    "manufacturing_sector": {
        "name": "URA Manufacturing & Industrial Sector Taxation Portal",
        "canonical_url": "https://ura.go.ug/en/manufacturing-sector-en-2/",
        "status": "operational",
        "category": "tax_education",
        "service_scope": [
            "Manufacturing Sector Structure (Tangible Goods, Steel, Plastics, Food/Beverages, Cosmetics, Textiles)",
            "10-Year Income Tax Holiday in Industrial Parks & Free Zones (Section 21(1)(y)/(z) ITA)",
            "EAC Duty Remission Scheme for Industrial Raw Materials & Packaging Supplies",
            "EACCMA 5th Schedule Part B Item 31 Spare Parts Duty Exemption (APC 492)",
            "Mandatory Digital Tax Stamps (DTS) on Factory Production Lines (Beer, Spirits, Soda, Water, Sugar, Cement)",
            "Deemed VAT & 0% Import Duty on Industrial Plant and Machinery",
            "Official 64-Page Manufacturing Sector Compendium Guide",
        ],
        "common_issues": {
            "dts_line_stamper_desync": {
                "symptoms": ["stamper offline", "dts camera error", "production line halt", "unactivated stamps"],
                "cause": "Production line stamping applicator or high-speed vision camera lost sync with URA central DTS gateway.",
                "steps": [
                    "Check Ethernet connection and DTS controller interface unit on packaging line.",
                    "Verify buffer memory on local DTS station and run self-test.",
                    "Log ticket on URA DTS Support Portal or contact resident technical service provider.",
                ],
            },
            "duty_remission_quota_exhausted": {
                "symptoms": ["duty remission rejected", "quota exceeded", "standard cet applied"],
                "cause": "Approved annual gazetted duty remission quota for specified raw materials has been fully utilized.",
                "steps": [
                    "Check remaining quota balance on Asycuda World and UESW portal.",
                    "Apply to the Ministry of Finance / EAC Council of Ministers for supplementary quota allocation.",
                ],
            },
        },
    },
    "education_sector": {
        "name": "URA Education Sector & Schools Taxation Portal",
        "canonical_url": "https://ura.go.ug/en/education/",
        "status": "operational",
        "category": "tax_education",
        "service_scope": [
            "School Proprietorship & Institutional Licensing (MoES, URSB, KCCA Permits)",
            "Income Tax Status (Section 21(1)(f) Charitable Exemption vs 30% CIT on Private Schools)",
            "VAT Exemptions on Educational Services (Schedule 2 Tuition/Boarding Fees)",
            "Scholastic Materials Tax Exemption (Textbooks, Geometry Sets, Crayons, Science Chemicals)",
            "Florence Agreement Annex D Duty-Free Scientific Apparatus for Educational Institutions",
            "Vocational & Technical Institute 10-Year Tax Holiday (Capital USD 10M / USD 300k / USD 150k)",
            "Monthly Teacher/Staff PAYE & 6% WHT on Supplies",
        ],
        "common_issues": {
            "charitable_school_exemption_ruling_missing": {
                "symptoms": ["school cit assessed", "charitable status rejected", "section 21 ruling missing"],
                "cause": "School operating as charitable/non-profit lacks a formal written exemption ruling from the Commissioner General.",
                "steps": [
                    "Submit audited accounts and governing constitution to URA Domestic Taxes Exemption Committee.",
                    "Obtain formal Section 21(1)(f) written ruling before claiming corporate tax exemption.",
                ],
            },
            "imported_exercise_books_vat_dispute": {
                "symptoms": ["exercise books taxed", "imported stationery vat charged"],
                "cause": "Under the VAT Act, only locally produced exercise books in Uganda/EAC are VAT exempt; imported exercise books attract standard 18% VAT.",
                "steps": [
                    "Verify country of origin on Bill of Entry in Asycuda World.",
                    "Ensure local manufacturers attach EAC Rules of Origin Certificate to claim 0% VAT.",
                ],
            },
        },
    },
    "mining_sector": {
        "name": "URA Mining & Mineral Extraction Sector Portal",
        "canonical_url": "https://ura.go.ug/en/overview-of-the-mining-sector/",
        "status": "operational",
        "category": "tax_education",
        "service_scope": [
            "Mineral Classification (Precious Metals, Precious Stones, Base Metals, Industrial Minerals)",
            "Mining Licensing with DGSM / MEMD (Exploration, Mining Leases, Sand & Quarry Permits)",
            "Mineral Royalties & Domestic Taxes (30% CIT, Local Sand/Stone Quarry Levies)",
            "Special Mining Deductions (100% Exploration Asset Write-Off, Mine Rehabilitation Fund Deductions)",
            "10% Final Withholding Tax on Subcontractors",
            "Duty-Free Mining Machinery & Heavy Earthmoving Equipment under EACCMA 5th Schedule",
        ],
        "common_issues": {
            "unlicensed_quarrying_penalty": {
                "symptoms": ["quarry closure", "sand extraction hold", "dgsm license missing"],
                "cause": "Commercial sand or rock quarrying operating without a valid permit from DGSM/MEMD or NEMA EIA certificate.",
                "steps": [
                    "Secure commercial extraction permit from the Directorate of Geological Survey and Mines.",
                    "Obtain approved NEMA environmental impact assessment clearance before operations.",
                ],
            },
            "subcontractor_wht_overdeduction": {
                "symptoms": ["subcontractor 15 percent withheld", "mining wht dispute"],
                "cause": "Client deducting standard 15% WHT instead of the preferential 10% final mining subcontractor rate.",
                "steps": [
                    "Provide proof of active Mining Lease under the Mining and Minerals Act.",
                    "Apply 10% final WHT rate on mining service contracts.",
                ],
            },
        },
    },
    "entertainment_sector": {
        "name": "URA Entertainment & Public Events Sector Portal",
        "canonical_url": "https://ura.go.ug/en/taxation-of-public-entertainment-and-events/",
        "status": "operational",
        "category": "tax_education",
        "service_scope": [
            "Entertainment Sector Structure (Promoters, Artistes, Events Companies, Production Studios)",
            "Withholding Tax (15% Final WHT on Non-Resident Artistes, 6% on Local Artistes)",
            "VAT on Public Events & Concert Tickets (18% Inclusive Ticket Computation, Gate Ticket Audits)",
            "Corporate Sponsorship Invoicing via EFRIS",
            "Production Studios & Audio-Visual Houses (30% CIT, Studio Equipment Capital Deductions)",
            "Official 20-Page Entertainment Sector Guide (Issue 1 Vol. 1 FY 2025-26)",
        ],
        "common_issues": {
            "foreign_artiste_wht_default": {
                "symptoms": ["foreign musician wht queried", "15 percent withholding missing", "promoter liability"],
                "cause": "Failure by the event promoter to withhold and remit 15% final tax from payments to non-resident performers.",
                "steps": [
                    "Withhold 15% from gross contract fees agreed with the international artiste.",
                    "File Form DT-1013 and remit tax to URA within 15 days following the month of payment.",
                ],
            },
            "unnotified_concert_ticket_audit": {
                "symptoms": ["ura officers at gate", "ticket count seizure", "unnotified event"],
                "cause": "Organizers hosting commercial concerts without giving prior statutory notice to URA.",
                "steps": [
                    "Notify the local URA Domestic Taxes office in writing at least 14 days before the public event.",
                    "Submit printed ticket batches or electronic ticketing portal credentials for revenue sealing.",
                ],
            },
        },
    },
    "schools_curriculum": {
        "name": "URA Schools Tax Curriculum & Educational Resources",
        "canonical_url": "https://ura.go.ug/download-category/a-level-tax-curriculum/",
        "status": "operational",
        "category": "tax_education",
        "service_scope": [
            "A-Level Tax Curriculum (Economics Resource Book 34MB, Entrepreneurship Syllabus 3MB, Orientation Manual)",
            "O-Level Tax Curriculum (Entrepreneurship Textbook 4.8MB, Training Manual 2MB, Teachers Guide)",
            "Taxation Handbook Archives (1st to 8th Edition [2025-26], Chinese Edition, Annual Tax Amendments FY 2026-27)",
            "Secondary School Competence-Based Tax Literacy Integration",
        ],
        "common_issues": {
            "download_link_interruption": {
                "symptoms": ["curriculum pdf incomplete", "large download failed", "resource book download error"],
                "cause": "Large files (e.g. 34 MB A-Level Resource Book) interrupted by browser timeouts.",
                "steps": [
                    "Visit ura.go.ug/download-category/a-level-tax-curriculum/ using a stable high-speed connection.",
                    "Right-click 'Download' and select 'Save Link As' to avoid browser memory buffers.",
                ],
            },
        },
    },
    "real_estate_sector": {
        "name": "URA Real Estate & Property Sector Taxation Portal",
        "canonical_url": "https://ura.go.ug/en/overview-of-real-estate-en/",
        "status": "operational",
        "category": "tax_education",
        "service_scope": [
            "Real Estate Value Chain (Land Dealers, Land Developers, Property Developers, Property Managers, Agents)",
            "Taxation of Commercial Land & Property Sales (30% CIT or Individual Graduated Rates up to 40%)",
            "VAT Treatment (Exempt Unimproved Land & Residential Leases vs 18% Commercial Buildings)",
            "Property Managers (Pass-Through Rent Accounting, Agency Fees 18% VAT, 6% WHT)",
            "Stamp Duty on Real Estate (1.5% Land Transfers, 1% Leases, 0.5% Mortgages, Nil in Industrial Parks)",
            "Allowable Local Authority Expenses (Ground Rent, Property Rates, Environmental Fees)",
            "Official 18-Page Real Estate Sector Guide (FY 2025-26)",
        ],
        "common_issues": {
            "unimproved_land_vat_error": {
                "symptoms": ["land sale vat queried", "unimproved land vat charged"],
                "cause": "Incorrectly levying 18% VAT on the transfer or sale of bare unimproved land.",
                "steps": [
                    "Verify land has no structural development under Schedule 2 VAT Act.",
                    "Apply VAT exemption on raw land sales and submit declaration without 18% output VAT.",
                ],
            },
            "cgv_valuation_stamp_duty_dispute": {
                "symptoms": ["stamp duty assessment high", "cgv valuation query", "transfer value dispute"],
                "cause": "Stamp duty is assessed on Chief Government Valuer (CGV) open market valuation rather than the lower contract consideration.",
                "steps": [
                    "Obtain the CGV property inspection report from the Ministry of Lands.",
                    "Pay the 1.5% stamp duty on the CGV assessed value via generated PRN.",
                ],
            },
        },
    },
    "fishing_sector": {
        "name": "URA Fishing & Fisheries Sector Taxation Portal",
        "canonical_url": "https://ura.go.ug/en/overview-of-the-fishing-sector/",
        "status": "operational",
        "category": "tax_education",
        "service_scope": [
            "Fisheries Sector Structure (Fishermen, Fishmongers, Industrial Processors, Exporters)",
            "Multi-Agency Licensing (DFR / MAAIF Vessel Licenses, BMU Permits, UNBS HACCP)",
            "VAT Treatment (Exempt Raw Fresh Catch vs 18% Processed Fish vs 0% Exports)",
            "Small Business Presumptive Turnover Tax for Independent Fishmongers",
            "30% CIT & 10-Year Export Income Tax Holiday for Fish Processors",
            "EACCMA 5th Schedule Duty-Free Landed Catch, Aquaculture Fingerlings & Export Packaging",
        ],
        "common_issues": {
            "raw_fish_vat_overcharge": {
                "symptoms": ["raw fish vat charged", "landing site vat query"],
                "cause": "Incorrectly levying 18% VAT on fresh, unprocessed fish or raw foodstuffs.",
                "steps": [
                    "Verify fish is whole or fresh without industrial packaging/processing.",
                    "Apply VAT exemption under Second Schedule VAT Act.",
                ],
            },
            "fish_export_vat_zero_rating_dispute": {
                "symptoms": ["fish export vat denied", "input credit blocked"],
                "cause": "Exporter missing MAAIF fish health export certificate or customs export declaration.",
                "steps": [
                    "Attach DFR/MAAIF Sanitary Certificate and Asycuda Bill of Entry (SE1 regime).",
                    "Claim zero-rated VAT status under Schedule 3 VAT Act to secure input tax refund.",
                ],
            },
        },
    },
    "transport_sector": {
        "name": "URA Commercial Transport Sector Portal",
        "canonical_url": "https://ura.go.ug/en/transport-sector/",
        "status": "operational",
        "category": "tax_education",
        "service_scope": [
            "Transport Sector Scope (Commercial PSVs, Goods/Freight Carriers, Marine Vessels, Aircraft)",
            "TLB Unified Assessment (MoWT PSV License + URA Advance Income Tax)",
            "Advance Income Tax Offsetting Rules on Final Income Tax Return (Form DT-1001)",
            "Aircraft Operators & Foreign Transporters Statutory Income Tax Exemptions",
            "Customs Reductions: 0% Duty on 20T+ Trucks & Semi-Trailer Tractors; 10% on 5-20T Vehicles",
            "EACCMA 5th Schedule 100% Tax Exemption on Cargo/Passenger Vessels, Trawlers, and Ferries",
            "Runyankole/Rukiga Translation Support ('Nimpandikisa nta omulimo gunu nk’obusubuzi?')",
        ],
        "common_issues": {
            "tlb_unified_assessment_prn_mismatch": {
                "symptoms": ["tlb license blocked", "advance tax prn unpaid", "unified assessment error"],
                "cause": "Payment made on separate PRNs rather than the unified TLB joint assessment voucher.",
                "steps": [
                    "Generate single unified payment slip via Transport Licensing Board (TLB) portal.",
                    "Pay joint assessment PRN covering both PSV operational fee and motor vehicle advance tax.",
                ],
            },
            "advance_tax_offset_credit_missing": {
                "symptoms": ["advance tax not credited", "double tax on vehicle income", "dt-1001 offset missing"],
                "cause": "Taxpayer failing to enter motor vehicle advance tax payment registration numbers on Schedule 4 of Form DT-1001.",
                "steps": [
                    "Retrieve PRN receipts for advance tax paid during vehicle licensing.",
                    "Input advance tax credit amounts under tax credits schedule to offset final tax payable.",
                ],
            },
        },
    },
    "government_agencies": {
        "name": "URA Government Agencies & MDAs Taxation Portal",
        "canonical_url": "https://ura.go.ug/en/category/tax-education/",
        "status": "operational",
        "category": "tax_education",
        "service_scope": [
            "Government Agencies Mandate (Ministries, Departments, Agencies MDAs, Local Governments)",
            "Designated Withholding Agent Obligations (6% WHT on Contracts >UGX 1M, Form DT-1013)",
            "Compulsory VAT Registration for Public Bodies Engaging in Commercial Business",
            "Section 24(7) Deemed VAT for MDAs on Aid-Funded Projects",
            "Other Non-Tax Revenue (ONTR/NTR) Collections, Reconciliations & E-Tax Interface",
            "Official 48-Page Guide to Taxation of Government Agencies (FY 2023-24)",
        ],
        "common_issues": {
            "mda_unwithheld_tax_liability": {
                "symptoms": ["mda wht penalty", "unwithheld tax assessment", "accounting officer liability"],
                "cause": "Failure by a government agency accounting officer to deduct 6% WHT on vendor procurement contracts.",
                "steps": [
                    "Withhold 6% WHT from gross payments on all procurement vouchers exceeding UGX 1,000,000.",
                    "Remit WHT via PRN and file monthly return DT-1013 by the 15th to avoid 2% monthly penal tax.",
                ],
            },
            "mda_ontr_portal_reconciliation_lag": {
                "symptoms": ["ontr collections mismatch", "mda revenue report missing"],
                "cause": "MDA staff not configured with authorized principal accountant credentials on the URA e-Tax portal.",
                "steps": [
                    "Submit official request letter to Commissioner Domestic Taxes with statutory fee schedule.",
                    "Configure designated principal accountants on the e-Tax ONTR portal to generate real-time reports.",
                ],
            },
        },
    },
    "opportunities_portal": {
        "name": "URA Opportunities, Tenders & Auctions Portal",
        "canonical_url": "https://ura.go.ug/en/opportunities/tenders/procurement-management-system/",
        "status": "operational",
        "category": "opportunities",
        "service_scope": [
            "Procurement Management System (PMS Supplier Registration, EOIs, RFQs, Tender Bidding)",
            "Online Public Auctioning Application (Customs Overtime Cargo, Seized Goods, Board Assets)",
            "Auction Platform User Manuals (AUCTION PLATFORM FOR URA ASSETS RVD 2MB PDF)",
            "Tender Sourcing Manuals (Help for Sourcing Suppliers 965KB PDF)",
            "Careers, Internships & E-Learning Platform Modules",
        ],
        "common_issues": {
            "pms_tcc_validation_failure": {
                "symptoms": ["supplier registration blocked", "tcc expired", "pms submission error"],
                "cause": "Tenderer's Tax Clearance Certificate (TCC) has expired or TIN has tax compliance arrears.",
                "steps": [
                    "Generate new TCC on portal.ura.go.ug under Tax Clearance.",
                    "Upload valid TCC and NSSF compliance clearance to complete supplier profile renewal.",
                ],
            },
            "auction_winning_bid_prn_expiry": {
                "symptoms": ["auction lot cancelled", "bid prn expired", "forfeited winning bid"],
                "cause": "Failure to pay assessed winning bid PRN within the mandatory 48-72 hour statutory clearance window.",
                "steps": [
                    "Pay winning bid PRN immediately at bank or via mobile money upon award notification.",
                    "Present receipt to customs warehouse keeper for gate pass release before 72-hour forfeit.",
                ],
            },
        },
    },
    "research_publications": {
        "name": "URA Research Lab & Publications Repository",
        "canonical_url": "https://ura.go.ug/en/research-faqs/",
        "status": "operational",
        "category": "research_and_publications",
        "service_scope": [
            "URA Research Lab (Anonymized Tax Microdata for Academic & Policy Researchers)",
            "Research Lab Governance (Management Committee & Assistant Commissioner Research & Innovation)",
            "Annual Revenue Performance Reports (FY 2022-23 2.95MB, FY 2021-22 1.82MB, FY 2020-21)",
            "URA Strategic Plan (FY 2025/26 – 2029/30 7MB PDF, Client Satisfaction Survey Report 2024)",
            "Research Bulletins, Macroeconomic Statistics, and Trade Reports",
        ],
        "common_issues": {
            "research_data_access_denial": {
                "symptoms": ["microdata request pending", "research proposal unreviewed"],
                "cause": "Researcher proposal lacks institutional endorsement or signed data non-disclosure protocol.",
                "steps": [
                    "Submit comprehensive academic proposal endorsed by recognized university/institution.",
                    "Sign URA Research Lab Data Governance and Non-Disclosure Agreement.",
                ],
            },
        },
    },
    "tin_registration": {
        "name": "URA Taxpayer Registration & Instant TIN Portal",
        "canonical_url": "https://portal.ura.go.ug",
        "status": "operational",
        "category": "taxpayer_services",
        "service_scope": [
            "Instant Individual TIN Application",
            "Non-Individual & Corporate Business TIN Application",
            "NIN Integration & Biometric Verification with NIRA",
        ],
        "common_issues": {
            "nira_nin_mismatch": {
                "symptoms": ["nin verification failed", "nira error", "name mismatch", "date of birth"],
                "cause": "Applicant details do not match the National Identification and Registration Authority (NIRA) database exactly.",
                "steps": [
                    "Ensure names are entered in the exact order (Surname, Given name, Other names) as on your National ID.",
                    "Verify date of birth and 14-character NIN match your physical National ID card.",
                ],
            },
        },
    },
    "track_status": {
        "name": "URA e-Services Application Tracking Portal",
        "canonical_url": "https://portal.ura.go.ug",
        "status": "operational",
        "category": "taxpayer_services",
        "service_scope": [
            "Track TIN Application Progress",
            "Check Amendment Status",
            "Monitor Tax Clearance Certificate (TCC) Approval",
            "Upload Officer-Requested Supplementary Documents",
        ],
        "common_issues": {
            "invalid_reference_number": {
                "symptoms": ["invalid search number", "application not found", "reference does not exist"],
                "cause": "Typo in Application Search Number / Reference code, or submitted over 90 days ago.",
                "steps": [
                    "Check the SMS confirmation or email received when you initially submitted the application.",
                    "Ensure you enter the full Application Search Number including any prefix letters.",
                    "Verify the registered mobile phone number or email matches what you entered.",
                ],
            },
        },
    },
    "document_authentication": {
        "name": "URA Document Authentication & Verification Portal",
        "canonical_url": "https://portal.ura.go.ug",
        "status": "operational",
        "category": "public_verification",
        "service_scope": [
            "Verify Tax Clearance Certificates (TCC)",
            "Verify Withholding Tax (WHT) Exemption Certificates",
            "Verify Assessment Notices & PRN Payment Slips",
            "Authenticate Official TIN Registration Certificates",
            "Scan EFRIS Receipts and Digital Tax Stamps (DTS)",
        ],
        "common_issues": {
            "certificate_unverified": {
                "symptoms": ["certificate not found", "invalid certificate", "expired document", "unverified"],
                "cause": "Document reference number is incorrect, or document has expired or is counterfeit.",
                "steps": [
                    "Check the Certificate Number or Document Reference Number printed at the top-right of the physical document.",
                    "For electronic documents, scan the official QR code using the URA Kakasa mobile app.",
                    "If the portal indicates 'Record Not Found', the document may not be an authentic URA-issued certificate. Contact URA toll-free 0800 117 000 immediately.",
                ],
            },
        },
    },
    "tax_clearance": {
        "name": "URA Tax Clearance Certificate (TCC) Portal",
        "canonical_url": "https://portal.ura.go.ug",
        "status": "operational",
        "category": "compliance",
        "service_scope": [
            "Apply for TCC (Public Tenders / Bidding)",
            "TCC for Immigration / Work Permits",
            "TCC for License Renewals & Company Regulatory Filings",
            "TCC for Motor Vehicle Transfers",
        ],
        "common_issues": {
            "unfiled_returns": {
                "symptoms": ["unfiled return", "non-compliant", "outstanding return", "liability unpaid"],
                "cause": "TCC requires 100% compliance across all registered tax heads (no pending returns or overdue assessments).",
                "steps": [
                    "Check your tax ledger on e-Services > View Account Details to view unfiled periods.",
                    "File any missing returns (VAT, PAYE, Income Tax) and settle outstanding tax liabilities or enter an active installment agreement.",
                    "Re-submit the TCC application; compliant requests issue automatically within 24 to 48 hours.",
                ],
            },
        },
    },
    "motor_vehicle": {
        "name": "URA Motor Vehicle Services Portal",
        "canonical_url": "https://portal.ura.go.ug",
        "status": "operational",
        "category": "motor_vehicle",
        "service_scope": [
            "Search Vehicle Details Application (Ownership & Tax Status Search)",
            "View Vehicle Search Report (Official Certified Vehicle Search Certificate)",
            "Track Application Status (Real-Time Vehicle Registration & Transfer Tracking)",
            "Download Vehicle Manual Forms (TR VII, TR I, TR III, TR IV, TR VIII, TR X)",
            "Document Authentication (Logbook & Search Report Authenticity Check)",
            "Print TIN Submitted Forms (Transfer Deeds & Assessment Vouchers)",
            "Motor Vehicle Transfer of Ownership (Buyer & Seller Online Endorsement)",
            "Personalized / Vanity Number Plates (UGX 20,000,000 Custom Plates)",
            "Advance Income Tax on Commercial Passenger & Goods Vehicles",
        ],
        "common_issues": {
            "absentee_transferor": {
                "symptoms": ["cannot trace owner", "absentee seller", "lost transferor", "transfer problem"],
                "cause": "The registered vehicle owner cannot be traced to sign the transfer deed.",
                "steps": [
                    "Prepare an affidavit of ownership and pay appropriate stamp duty.",
                    "Publish a 14-day public notice in a national newspaper of wide circulation.",
                    "Present vehicle for official physical inspection at the licensing station.",
                    "Submit court order or police clearance alongside original logbook for commissioner approval.",
                ],
            },
            "unpaid_search_fee": {
                "symptoms": ["search report unavailable", "prn unpaid", "report pending"],
                "cause": "The statutory vehicle search fee (UGX 24,000) has not been confirmed by the bank.",
                "steps": [
                    "Settle the generated PRN via mobile money (*165# / *185#) or bank counter.",
                    "Wait 5–10 minutes for bank reconciliation, then reload 'View Vehicle Search Report'.",
                ],
            },
        },
    },
    "objection_appeals": {
        "name": "URA Objections & Legal Appeals Portal",
        "canonical_url": "https://portal.ura.go.ug",
        "status": "operational",
        "category": "dispute_resolution",
        "service_scope": [
            "Lodge Objection to Tax Assessment (Section 24 TPCA 2014)",
            "Upload Grounds of Objection and Supporting Financial Schedules",
            "Track 90-Day Statutory Objection Decision Timeline",
            "Appeals to Tax Appeals Tribunal (TAT)",
            "Alternative Dispute Resolution (ADR) Request",
        ],
        "common_issues": {
            "deadline_exceeded": {
                "symptoms": ["45 days", "out of time", "late objection", "extension needed"],
                "cause": "Objections must be lodged within 45 days from the date of service of the assessment notice.",
                "steps": [
                    "If the 45-day statutory deadline has passed, submit an Application for Extension of Time under Section 24(2) stating valid reasonable grounds (e.g. sickness, absence from Uganda).",
                    "Do not delay: once an assessment becomes final, collection enforcement commences.",
                ],
            },
        },
    },
    "voluntary_disclosure": {
        "name": "URA Voluntary Disclosure Portal (Section 66 TPCA)",
        "canonical_url": "https://portal.ura.go.ug",
        "status": "operational",
        "category": "compliance_relief",
        "service_scope": [
            "Section 66 Tax Procedures Code Act 100% Waiver of Penalties and Interest",
            "Voluntary Declaration of Unreported Tax Liabilities",
            "Installment Agreement for Principal Tax Settlement",
        ],
        "common_issues": {
            "ineligible_under_audit": {
                "symptoms": ["under audit", "notice issued", "investigation commenced", "not eligible"],
                "cause": "Voluntary disclosure benefits apply ONLY before URA officially notifies or commences an audit/investigation.",
                "steps": [
                    "Ensure no audit or tax investigation notice has been served prior to your application.",
                    "Submit detailed disclosure of omitted income, sales, or tax heads with supporting calculations.",
                    "Pay the principal tax in full or sign an agreed phased payment plan to secure the complete waiver.",
                ],
            },
        },
    },
    "whistle_blow": {
        "name": "URA Informer & Whistleblower Portal (Touchpoint)",
        "canonical_url": "https://touchpoint.ura.go.ug",
        "status": "operational",
        "category": "intelligence_and_investigations",
        "service_scope": [
            "Report Tax Evasion & Under-declaration",
            "Report Smuggling & Transit Diversion",
            "Report Non-issuance of EFRIS Invoices or Counterfeit Digital Stamps (DTS)",
            "Section 67 TPCA Informant Reward Scheme (up to 5% of recovered tax)",
        ],
        "common_issues": {},
    },
    "dts": {
        "name": "URA Digital Tax Stamps (DTS) Portal & Digital Tracking Solution",
        "canonical_url": "https://ura.go.ug/en/domestic-taxes/dts-digital-tax-stamps/",
        "status": "operational",
        "category": "excise_and_manufacturing",
        "service_scope": [
            "DTS Registration (Site Registration, SKU Product Approval, Line Enabler Setup)",
            "Affix and Activate Tax Stamps (Automated Applicator & Digital Activation System DAS)",
            "Order Physical / Digital Stamps for Gazetted Goods (Beer, Spirits, Wine, Soda, Water, Tobacco, Cement, Sugar, Cooking Oil, Juice)",
            "Download Manual Forms (DT 1019 Registration Annexure & DTS Rejects Monitoring Form)",
            "Gazetted Item Catalogue & Pricing (Water UGX 13, Soda UGX 17, Beer UGX 36, Tobacco UGX 75, Cement UGX 135, Bulker UGX 60,000)",
            "Physical Paper Stamp Collection at SICPA Uganda (Henley Business Park, Ntinda)",
            "KAKASA Mobile App & SMS 8119 Stamp Verification for Consumers and Stockists",
        ],
        "common_issues": {
            "stamp_activation_failure": {
                "symptoms": ["unactivated stamps", "das sync error", "goods held at distribution"],
                "cause": "Stamps must be activated via the Production Line Controller or DAS before leaving the factory or customs warehouse.",
                "steps": [
                    "Ensure automated line enabler or handheld scanner has active internet connection to URA servers.",
                    "Log into DTS web portal to verify activation batch submission.",
                ],
            },
            "damaged_stamps_reconciliation": {
                "symptoms": ["stamp jam on line", "torn stamp roll", "stamp wastage"],
                "cause": "Mechanical line jams or printing defects on automated applicators.",
                "steps": [
                    "Record serial numbers on the official DTS Rejects Monitoring Form.",
                    "Retain physical remnants and lodge declaration at designated URA station for credit reconciliation.",
                ],
            },
        },
    },
    "tax_incentives": {
        "name": "URA Tax Incentives & Exemptions Portal",
        "canonical_url": "https://portal.ura.go.ug",
        "status": "operational",
        "category": "investment_promotion",
        "service_scope": [
            "WHT Exemption Applications (Section 119(5) Income Tax Act)",
            "Investors Guides (Uganda Investment Authority & URA Framework)",
            "Income Tax Exemption (Section 21 ITA Charities & Agro-processors)",
            "Import or Export Tax Exemption (EACCMA Fifth Schedule Duty Free)",
            "Track Application Status (Real-Time Exemption Certificate Tracking)",
            "Withholding Tax (Rates, Deadlines & Compliance Management)",
            "Withholding Tax Exemption List (Gazetted Schedule of Exempt Taxpayers)",
            "VAT Withholding Agent List (Designated Agents under Section 5(2) VAT Act)",
            "Designated Income Tax WHT Agents For FY 2024/25 (Official Schedule)",
            "List of Non-Resident providers of digital services registered with URA",
            "Tax Holiday (10-Year Statutory Income Tax Holiday Criteria)",
            "Tax Waiver (Section 66 TPCA Voluntary Disclosure & Section 40 Remission)",
            "Document Authentication (Exemption Certificate & Ruling Verification)",
        ],
        "common_issues": {
            "wht_exemption_audit_gap": {
                "symptoms": ["exemption rejected", "tax arrears found", "3-year compliance failed"],
                "cause": "Withholding Tax exemption requires an unblemished tax filing and payment record for the preceding 3 consecutive years.",
                "steps": [
                    "Review ledger on portal.ura.go.ug > My Account > Account Statement to identify outstanding returns or penalties.",
                    "File all pending returns and settle outstanding liabilities before reapplying.",
                ],
            },
            "local_content_deficiency": {
                "symptoms": ["tax holiday declined", "local content missing", "employment threshold unmet"],
                "cause": "Section 21 tax holiday requires at least 50% locally sourced raw materials and 100 Ugandan citizen employees.",
                "steps": [
                    "Submit audited payroll showing at least 100 Ugandan workers registered with NSSF.",
                    "Provide certified procurement records showing local raw material usage.",
                ],
            },
            "non_resident_dst_unregistered": {
                "symptoms": ["dst not found", "digital provider unregistered", "wht on foreign tech"],
                "cause": "Non-resident tech provider has not registered under Section 86A for Uganda Digital Services Tax.",
                "steps": [
                    "Check the public URA Non-Resident DST Registry on portal.ura.go.ug.",
                    "If unregistered, local business must withhold 15% non-resident tax on payments.",
                ],
            },
        },
    },
    "get_refund": {
        "name": "URA Tax Refunds Portal (Section 42 TPCA)",
        "canonical_url": "https://portal.ura.go.ug",
        "status": "operational",
        "category": "revenue_accounting",
        "service_scope": [
            "VAT Input Credit Refund Applications (Section 31 VAT Act)",
            "Overpaid Income Tax & Withholding Tax Refunds (Section 113 ITA)",
            "Payment made to Ministry, Department or Agency (MDAs) Refunds",
            "Motor Vehicle, Stamp Duty & Driving Permit Fee Refunds",
            "Import or Export Tax Refund & Duty Drawbacks (EACCMA)",
            "Track Application Status (Audit, Approval & EFT Disbursement)",
            "Download Refund Manual Forms (DT-4001, C20, MDA Vouchers)",
            "Document Authentication & Refund Certificate Verification",
        ],
        "common_issues": {
            "unsupported_input_credit": {
                "symptoms": ["efris missing", "disallowed input", "invoice unverified", "refund rejected"],
                "cause": "VAT refunds require every claimed input tax invoice to be verified with an EFRIS Fiscal Document Number (FDN).",
                "steps": [
                    "Ensure all supplier invoices carry authentic EFRIS fiscal signatures.",
                    "Attach bank payment slips confirming actual monetary settlement for invoices exceeding UGX 5,000,000.",
                ],
            },
            "mda_endorsement_missing": {
                "symptoms": ["mda rejected", "ministry clearance missing", "passport refund delayed"],
                "cause": "Refunds for payments made to MDAs require an official clearance letter from the beneficiary agency.",
                "steps": [
                    "Obtain formal endorsement letter from the concerned MDA confirming service was cancelled or double-paid.",
                    "Upload endorsement letter under 'Supporting Documents' on portal.ura.go.ug > Tax Refund > Payment made to MDAs.",
                ],
            },
            "bank_tin_name_mismatch": {
                "symptoms": ["eft bounce", "bank mismatch", "refund held", "account name error"],
                "cause": "Bank account name does not match the registered legal name on the URA TIN.",
                "steps": [
                    "Ensure bank account is registered in the exact name as the URA TIN.",
                    "Submit stamped bank confirmation letter if name was updated recently.",
                ],
            },
        },
    },
    "stamp_duty": {
        "name": "URA Stamp Duty Management Portal",
        "canonical_url": "https://portal.ura.go.ug",
        "status": "operational",
        "category": "domestic_taxes",
        "service_scope": [
            "Stamp Duty Assessment on Land & Property Transfers (1.5%)",
            "Mortgages & Debentures Stamp Duty Assessment (0.5%)",
            "Tenancy & Commercial Lease Agreements (1%)",
            "Legal Affidavits and Powers of Attorney (UGX 15,000 flat)",
        ],
        "common_issues": {},
    },
    "choose_tax_agent": {
        "name": "URA Tax Agent Management Portal",
        "canonical_url": "https://ura.go.ug/en/choose-agents/",
        "status": "operational",
        "category": "taxpayer_services",
        "service_scope": [
            "Appoint Authorized Tax Agent / Practitioner (Online Delegation)",
            "Search Licensed Customs Agents (Import & Export Clearing Agents)",
            "Search Licensed Domestic Tax (DT) Agents (TARC Licensed)",
            "Search Income Tax Agents - WHT (Section 119 Designated Agents)",
            "Search VAT Agents - WHT (Section 5(2) VAT Withholding Agents)",
            "Tax Agent Representation & Scope (Filing, Petitions, Objections, Hearings)",
            "Taxpayer Safeguards (Never Handover Payment Obligations to Agents)",
            "Revoke or Transfer Agent Authority",
        ],
        "common_issues": {
            "agent_payment_handover_warning": {
                "symptoms": ["agent wants tax money", "gave money to agent", "agent diversion"],
                "cause": "Taxpayers must never give tax payment funds to tax agents directly.",
                "steps": [
                    "All taxes must be paid directly into the URA bank account via official PRN at commercial banks or mobile money.",
                    "Verify your URA ledger balance on portal.ura.go.ug to ensure payments reflect.",
                ],
            },
            "unlicensed_agent_risk": {
                "symptoms": ["unregistered agent", "license expired", "fake tax agent"],
                "cause": "The representative is not licensed by the Tax Agents Registration Committee (TARC).",
                "steps": [
                    "Search ura.go.ug/en/choose-agents/ to verify active license number and expiry date before appointing.",
                    "Do not share e-Tax master login credentials; use 'Appoint Agent' delegation on the portal.",
                ],
            },
        },
    },
    "double_taxation_agreements": {
        "name": "URA Double Taxation Agreements (DTA) & International Tax Treaties",
        "canonical_url": "https://ura.go.ug/en/category/legal-policy/double-taxation-agreements/",
        "status": "operational",
        "category": "legal_and_policy",
        "service_scope": [
            "Bilateral Tax Treaties (UK 1992, South Africa 1997, Norway 1999, Denmark 2000, Mauritius 2003, India 2004, Netherlands 2004)",
            "Delegated Competent Authorities Directory for EOI",
            "Withholding Tax (WHT) Concessionary Ceilings (0%, 5%, 10%, 15%)",
            "Permanent Establishment (PE) Statutory Thresholds (6 Months / 183 Days / 4 Months Service PE)",
            "Section 88 Income Tax Act (Treaty Supremacy & LOB Anti-Treaty Shopping)",
            "Mutual Agreement Procedure (MAP) & Dispute Resolution",
            "Exchange of Information (On Request, Spontaneous, and Automatic)",
            "Form DT-1 Tax Treaty Relief & Tax Residence Certificate (TRC) Processing",
        ],
        "common_issues": {
            "dta_relief_denial": {
                "symptoms": ["treaty rate rejected", "standard 15% applied", "lob disqualification"],
                "cause": "Under Section 88(5) ITA, treaty relief is denied if 50% or more of the entity is owned by non-residents of the treaty country, or if a valid Tax Residence Certificate (TRC) is missing.",
                "steps": [
                    "Obtain a certified Tax Residence Certificate from the treaty country tax authority.",
                    "Submit URA Form DT-1 along with proof of active commercial substance to the International Tax Unit.",
                ],
            },
            "pe_exposure_dispute": {
                "symptoms": ["unexpected branch profit tax", "service pe triggered", "construction pe assessed"],
                "cause": "Foreign contractors or consultants exceeding treaty duration limits (e.g. 6 months for construction, 4 months for service PE) are deemed to operate a taxable PE.",
                "steps": [
                    "Audit project presence duration against specific bilateral treaty articles.",
                    "Initiate Mutual Agreement Procedure (MAP) with the Delegated Competent Authority if double taxation arises.",
                ],
            },
        },
    },
    "case_summary_reports": {
        "name": "URA Case Summary Reports & Judicial Case Digest",
        "canonical_url": "https://ura.go.ug/download-category/case-summery-reports/",
        "status": "operational",
        "category": "legal_and_policy",
        "service_scope": [
            "URA Case Digest Volume XI (Jul - Dec 2025, 38 Landmark Decisions)",
            "Case Digest Volume VIII (Jan - Mar 2024)",
            "URA Case Digest Quarterly Compilations (2022 - 2023)",
            "Compendium of EAC Tax Cases (Regional Precedents)",
            "Tax Appeals Tribunal (TAT) Decisions & Judicial Interpretation",
            "High Court Commercial Division Judgments on Tax Assessments",
        ],
        "common_issues": {
            "precedent_research": {
                "symptoms": ["case citation needed", "previous court ruling", "tat decision search"],
                "cause": "Taxpayers and practitioners requiring binding authority on input tax claims, Section 24 TPCA objections, or transfer pricing disputes.",
                "steps": [
                    "Visit ura.go.ug/download-category/case-summery-reports/ to download Case Digest Volumes.",
                    "Review specific quarterly volumes for subject matter rulings and appellate orders.",
                ],
            },
        },
    },
    "court_of_appeal": {
        "name": "URA Court of Appeal Landmark Tax Judgments",
        "canonical_url": "https://ura.go.ug/download-category/court-of-appeal/",
        "status": "operational",
        "category": "legal_and_policy",
        "service_scope": [
            "Court of Appeal Landmark Decisions on Tax Law",
            "Celtel Uganda Ltd vs URA (Civil Appeal 22 of 2006, Airtime Value & VAT/Excise)",
            "Gulindwa Paul v Uganda (Criminal Tax Fraud & Evidentiary Standard)",
            "Appellate Hierarchy & Binding Constitutional Authority",
            "Appeals on Questions of Law from High Court Commercial Division",
        ],
        "common_issues": {
            "appellate_stare_decisis": {
                "symptoms": ["court of appeal authority", "assessment in conflict with precedent", "binding judgment"],
                "cause": "Under Article 132/134 of the Constitution, Court of Appeal decisions strictly bind the High Court, TAT, and URA administration.",
                "steps": [
                    "Download the full judgment from ura.go.ug/download-category/court-of-appeal/.",
                    "Cite the holding in statutory objection or appeal pleadings before TAT or High Court.",
                ],
            },
        },
    },
    "debt_collections": {
        "name": "URA Debt Collection & Tax Arrears Recovery",
        "canonical_url": "https://ura.go.ug/download-category/debt-collections/",
        "status": "operational",
        "category": "legal_and_policy",
        "service_scope": [
            "The Debt Collection Function Manual (August 1, 2023)",
            "Section 40 Demand Notices for Tax Arrears",
            "Section 41 Distress Proceedings & Asset Seizures",
            "Section 42 Temporary Closure of Business Premises (14-day Sealing)",
            "Section 43 Agency Notices (Garnishee Orders on Bank Accounts & Debtors)",
            "Section 44 Land Title Caveats & Charges over Property",
            "Section 45 Departure Prohibition Orders (DPO)",
            "Section 47 Debt Collection Unit (DCU) Instalment Payment MOUs",
        ],
        "common_issues": {
            "bank_account_garnishee": {
                "symptoms": ["bank account frozen", "agency notice served", "garnishee hold"],
                "cause": "Under Section 43 TPCA, URA serves Agency Notices to banks where tax debts remain unpaid after statutory demand.",
                "steps": [
                    "Contact the Debt Collection Unit (Nakawa Tower) to verify exact arrears balance.",
                    "Pay arrears in full via PRN or negotiate a 20%-30% down payment with an instalment MOU to secure agency release.",
                ],
            },
            "sealed_premises_dispute": {
                "symptoms": ["business closed by ura", "padlocked by enforcement", "revenue seal affixed"],
                "cause": "Under Section 42 TPCA, authorized officers can seal business premises for up to 14 days for tax default.",
                "steps": [
                    "Do not break seals (criminal offence under Section 42(3) TPCA).",
                    "Execute an instalment MOU with DCU or settle the assessment to obtain a De-sealing Order.",
                ],
            },
        },
    },
    "financial_intelligence_authority": {
        "name": "URA Financial Intelligence Authority & AML/CFT Compliance",
        "canonical_url": "https://ura.go.ug/download-category/financial-intelligence-authority/",
        "status": "operational",
        "category": "legal_and_policy",
        "service_scope": [
            "National ML/TF Risk Assessment on Tax Crimes and Proceeds (July 18, 2025)",
            "World Bank Domestic Tax Evasion Risk Assessment Tool Modules",
            "Trade-Based Money Laundering (TBML) & Customs Misinvoicing Controls",
            "Anti-Money Laundering Act 2013 & Amendment Regulations 2023 Compliance",
            "Cash Transaction Reports (CTR) & Suspicious Transaction Reports (STR)",
            "Inter-Agency Taskforce (URA, FIA, ODPP, CID, Bank of Uganda)",
        ],
        "common_issues": {
            "aml_compliance_reporting": {
                "symptoms": ["str submission needed", "ctr threshold query", "fia audit request"],
                "cause": "Accountable persons must comply with statutory reporting thresholds (USD 10,000 / UGX 20M) and due diligence.",
                "steps": [
                    "Report suspicious transactions to the FIA within 2 working days via the goAML portal.",
                    "Maintain customer due diligence (CDD) and beneficial ownership records for at least 10 years.",
                ],
            },
        },
    },
    "customs_systems": {
        "name": "URA Customs Systems & Trade Clearance Platforms",
        "canonical_url": "https://ura.go.ug/en/category/tax-education/general-tax-information/customs-systems/",
        "status": "operational",
        "category": "customs_and_trade",
        "service_scope": [
            "ASYCUDA World (Automated System for Customs Data)",
            "Uganda Electronic Single Window (UESW - Joint Agency Clearances)",
            "Regional Electronic Cargo Tracking System (RECTS - GPS Satellite Monitoring)",
            "Non-Intrusive Inspection (NII - High-Energy Container X-Ray Scanning)",
            "Bonded Warehouse Information Management System (BWIMS)",
            "URA Touchpoint Portal (ASYCUDA World Installer & System Registration Forms)",
            "Naivasha ICD and Mombasa Port Customs Cargo Clearance (Section 42 EACCMA)",
        ],
        "common_issues": {
            "asycuda_connectivity_error": {
                "symptoms": ["asycuda connection timeout", "java runtime error", "touchpoint installer needed"],
                "cause": "Clearing agents operating obsolete Java versions or unconfigured Touchpoint credentials.",
                "steps": [
                    "Download the latest ASYCUDA World Installer from https://touchpoint.ura.go.ug.",
                    "Verify user credentials and Java security exceptions on the Touchpoint portal.",
                ],
            },
            "rects_seal_alert": {
                "symptoms": ["seal tamper alarm", "route deviation alert", "transit truck impounded"],
                "cause": "Transit vehicle diverted from the 21 gazetted corridors or experienced an unauthorized stop.",
                "steps": [
                    "Contact the Transit Monitoring Unit (TMU) control center on 0323 442500.",
                    "Do not tamper with or detach electronic seals; await customs patrol verification.",
                ],
            },
        },
    },
}




def _diagnose_portal_state(
    portal_key: str, text_lower: str, err_msg: str
) -> tuple[str, str, str, list[str]]:
    combined = f"{text_lower} {err_msg}"

    if (
        re.search(r"\b(?:http\s*500|error\s*500|code\s*500|500\s+internal)\b", combined)
        or "internal server error" in combined
        or "server fault" in combined
    ):
        return (
            "Server Fault (HTTP 500)",
            "The external URA web service encountered an internal server error.",
            "error",
            [
                "Wait 60 seconds and refresh the browser window (Ctrl+F5).",
                "Open a Private / Incognito window to bypass stale session cookies.",
                "If submitting a return, ensure numeric figures contain no symbols or commas.",
            ],
        )

    if any(k in combined for k in ("session expired", "timed out", "unauthorized", "login required", "please login", "login expired", "session timeout")):
        return (
            "Session Expired / Authentication Required",
            "Your security session on the portal has expired.",
            "warning",
            [
                "Click 'Back to Login' on the URA e-Services portal.",
                "Enter your 10-digit TIN and password, and complete SMS OTP verification if prompted.",
                "Navigate back to your intended filing or payment page.",
            ],
        )

    # -------------------------------------------------------------------
    # Make a Payment & PRN Services Suite (13 Sub-Paths)
    # -------------------------------------------------------------------
    if portal_key in ("prn_payments", "make_a_payment") or (
        portal_key not in (
            "stamp_duty",
            "tax_clearance",
            "motor_vehicle",
            "get_refund",
            "tax_incentives",
            "choose_tax_agent",
            "objection_appeals",
            "dts",
            "efris",
            "asycuda",
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
        )
        and any(k in combined for k in ("make a payment", "payment slip", "prn", "pay tax", "pay taxes"))
    ):
        # 1. Reactivate Expired PRN
        if any(k in combined for k in ("reactivate expired prn", "expired prn", "prn expired", "reactivate prn")):
            return (
                "URA Portal — Reactivate Expired PRN",
                "Renewing validity on an expired Payment Registration Number without re-declaring tax.",
                "info",
                [
                    "Step 1: Note that standard URA PRNs expire after 21 days (or up to 28 days for certain taxes).",
                    "Step 2: Go to portal.ura.go.ug > e-Services > Make a Payment > Reactivate Expired PRN.",
                    "Step 3: Enter the expired 10 or 12-digit PRN number.",
                    "Step 4: Click 'Submit' to refresh and reactivate the PRN with an extended validity window.",
                    "Step 5: Proceed to settle payment via mobile money (*165# / *185#), bank counter, or online card.",
                ],
            )

        # 2. Pay with VISA or Master Card
        if any(k in combined for k in ("visa", "master card", "mastercard", "card payment", "credit card", "debit card")):
            return (
                "URA Portal — Pay with VISA or Master Card",
                "Instant electronic card checkout on the URA payment gateway.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Make a Payment > Pay with VISA or Master Card.",
                    "Step 2: Enter your 12-digit PRN to retrieve liability details and confirm taxpayer name.",
                    "Step 3: Choose card payment gateway (VISA, MasterCard, or UnionPay).",
                    "Step 4: Input cardholder name, 16-digit card number, expiry date, and 3-digit CVV security code.",
                    "Step 5: Complete 3D-Secure One-Time Password (OTP) verification sent by your bank.",
                    "Step 6: System generates an instant cleared e-receipt and posts funds to your tax ledger.",
                ],
            )

        # 3. Print a Payment Slip (Reprint)
        if any(k in combined for k in ("print a payment slip", "print payment slip", "reprint payment slip", "reprint prn")):
            return (
                "URA Portal — Print a Payment Slip",
                "Reprinting generated PRN payment vouchers with official bank barcodes.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Make a Payment > Print a Payment Slip.",
                    "Step 2: Enter the 10 or 12-digit PRN number or assessment registration code.",
                    "Step 3: Complete the security CAPTCHA code.",
                    "Step 4: Click 'Search / Print' to download the payment slip PDF complete with scannable bank barcode.",
                    "Step 5: Present the printed slip with cash or cheque at any commercial bank counter.",
                ],
            )

        # 4. Generate Payment Slip for Park User Fees
        if any(k in combined for k in ("park user fees", "park fees", "uwa", "wildlife", "gorilla permit", "murchison", "bwindi")):
            return (
                "URA Portal — Generate Payment Slip for Park User Fees (UWA)",
                "Generating official payment slips for Uganda Wildlife Authority national park entry and tourism activities.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Make a Payment > Generate Payment Slip for Park User Fees.",
                    "Step 2: Select Agency as 'Uganda Wildlife Authority (UWA)'.",
                    "Step 3: Select the specific National Park (e.g. Bwindi, Queen Elizabeth, Murchison Falls) and activity (Gorilla Trekking, Park Entry, Vehicle).",
                    "Step 4: Enter tourist/visitor Passport or National ID (NIN) details and group size.",
                    "Step 5: Generate UWA PRN and settle via mobile money (*165# / *185#) or bank.",
                ],
            )

        # 5. Print Income Tax Certificate
        if any(k in combined for k in ("print income tax certificate", "income tax certificate")):
            return (
                "URA Portal — Print Income Tax Certificate",
                "Retrieving certified income tax payment slips and annual settlement certificates.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Make a Payment > Print Income Tax Certificate.",
                    "Step 2: Enter your active 10-digit TIN and the relevant tax assessment year.",
                    "Step 3: System verifies cleared payments on your income tax ledger and generates your official printable Income Tax Certificate.",
                ],
            )

        # 6. View Payment Status
        if any(k in combined for k in ("view payment status", "payment status", "confirm payment posted", "has payment reflected", "check payment")):
            return (
                "URA Portal — View Payment Status",
                "Real-time verification of posted tax payments and bank transaction clearances.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Make a Payment > View Payment Status.",
                    "Step 2: Enter your 12-digit PRN.",
                    "Step 3: Click 'Search' to view real-time clearance status: Bank Transaction Reference, Date & Time of payment, Amount Paid, and Cleared Status.",
                    "Step 4: Click 'Download Receipt' to print your official URA tax receipt.",
                ],
            )

        # 7. Verify Advance Income Tax Payment
        if any(k in combined for k in ("verify advance income tax", "advance tax motor vehicle", "commercial vehicle tax")):
            return (
                "URA Portal — Verify Advance Income Tax Payment",
                "Confirming advance tax paid on commercial passenger and freight motor vehicles.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Make a Payment > Verify Advance Income Tax Payment.",
                    "Step 2: Enter vehicle registration number and payment PRN.",
                    "Step 3: The system confirms advance income tax compliance: UGX 20,000 per passenger seat for commercial buses/taxis, or UGX 50,000 per tonne for goods vehicles.",
                    "Step 4: Use the verified advance tax receipt when renewing route licenses with the Transport Licensing Board.",
                ],
            )

        # 8. Pay For Hospital Fees
        if any(k in combined for k in ("pay for hospital fees", "hospital fees", "mulago", "butabika", "referral hospital")):
            return (
                "URA Portal — Pay For Hospital Fees",
                "Generating official PRN payments for public referral hospitals and health services.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Make a Payment > Pay For Hospital Fees.",
                    "Step 2: Select the healthcare facility: Mulago National Referral Hospital, Butabika, Kiruddu, Kawempe, or Regional Referral Hospital.",
                    "Step 3: Select hospital department/service and input patient full name, National ID / NIN (or next of kin contact).",
                    "Step 4: Enter assessed fee amount, generate the PRN, and pay via mobile money or hospital bank counter.",
                ],
            )

        # 9. Download Manual Payment Forms
        if any(k in combined for k in ("download manual payment forms", "manual payment forms", "bank payment slip download")):
            return (
                "URA Portal — Download Manual Payment Forms",
                "Printable bank payment vouchers and general Non-Tax Revenue (NTR) deposit slips.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Make a Payment > Download Manual Payment Forms.",
                    "Step 2: Download printable Bank Payment Vouchers, General Non-Tax Revenue (NTR) deposit slips, or bulk commercial bank payment schedules.",
                    "Step 3: Fill in required fields in ink and present with cash or cheque to any authorized commercial bank branch.",
                ],
            )

        # 10. Generate a Payment Slip (PRN)
        if any(k in combined for k in ("generate a payment slip", "generate payment slip", "generate prn", "create prn", "bank", "mode", "gateway")):
            has_err = any(k in combined for k in ("missing", "error", "select bank", "payment mode"))
            return (
                "PRN Payment Mode Selection" if has_err else "URA Portal — Generate a Payment Slip (PRN)",
                (
                    "Payment mode or commercial bank gateway requires selection before PRN can be issued."
                    if has_err
                    else "Generating a Payment Registration Number for tax and government agency liabilities."
                ),
                "warning" if has_err else "info",
                [
                    "Step 1: Go to ura.go.ug or portal.ura.go.ug > e-Services > Make a Payment > Generate a Payment Slip.",
                    "Step 2: Choose payment category: Tax Head (VAT, PAYE, Income Tax, Withholding, Customs) or Non-Tax Revenue (NTR/Government Agencies).",
                    "Step 3: Enter your 10-digit TIN (or select 'Non-TIN' and enter NIN/Passport for government fees).",
                    "Step 4: Enter the exact payment amount in UGX.",
                    "Step 5: Select your payment channel: Commercial Bank or Mobile Money (MTN / Airtel).",
                    "Step 6: Click 'Submit' to receive your 12-digit PRN and download your payment slip.",
                ],
            )

        # Default Make a Payment overview
        return (
            "URA Portal — Make a Payment Overview",
            "Central payment gateway for tax and non-tax government revenues.",
            "info",
            [
                "Step 1: Every payment to URA requires a Payment Registration Number (PRN).",
                "Step 2: Generate a PRN via e-Services > Make a Payment > Generate a Payment Slip.",
                "Step 3: Pay via Mobile Money (*165# on MTN, *185# on Airtel), commercial bank counter, or online VISA/MasterCard.",
                "Step 4: Confirm payment clearance via View Payment Status.",
            ],
        )

    # -------------------------------------------------------------------
    # Detailed verification of the 8 URA Portal TIN Services
    # -------------------------------------------------------------------

    # 1. Search & Check TIN
    if portal_key not in ("tax_incentives", "get_refund", "motor_vehicle", "choose_tax_agent", "objection_appeals", "stamp_duty", "tax_clearance", "export_process", "customs_valuation", "single_customs_territory", "exempt_importation", "aeo", "customs_audits_refunds", "warehousing", "customs_enforcements", "laws_and_acts", "double_taxation_agreements") and (
        any(
            k in combined
            for k in (
                "search tin",
                "verify tin",
                "find tin",
                "tin lookup",
                "check tin",
                "check the ura tin",
                "check a tin",
                "check my tin",
                "search for a tin",
            )
        )
        or ("tin" in combined and any(k in combined for k in ("search", "verify", "check", "lookup", "find")) and not any(k in combined for k in ("incentive", "exemption", "refund", "wht", "agent list", "withholding")))
    ):
        if any(k in combined for k in ("require", "needed", "what do i need", "what is required")):
            return (
                "URA Portal — Search TIN Requirements & Inputs",
                "Requirements and search criteria to verify an active TIN on the URA portal.",
                "info",
                [
                    "Requirement 1 (Citizens): Your 14-character National Identification Number (NIN) from NIRA.",
                    "Requirement 2 (Non-Citizens): Your valid Passport Number or Work Permit Number.",
                    "Requirement 3 (Companies / Non-Individuals): Registered Business Legal Name or URSB Incorporation Number.",
                    "Cost: Free of charge — Search TIN is a public self-service that requires no login account or payment.",
                    "Step-by-step: Go to ura.go.ug > e-Services > Search TIN > choose your search mode, input details, complete CAPTCHA, and view TIN status.",
                ],
            )
        return (
            "URA Portal — Search & Verify TIN",
            "Self-service lookup to confirm active 10-digit TIN status or retrieve forgotten TIN.",
            "info",
            [
                "Step 1: Go to ura.go.ug or portal.ura.go.ug and click e-Services > Search TIN (or Quick Links > Search TIN).",
                "Step 2: Choose search mode: 'Search by National ID (NIN)', 'Search by Registration No.', or 'Search by Taxpayer Name'.",
                "Step 3: Enter your 14-character NIN or registered business legal name.",
                "Step 4: Complete the security code (CAPTCHA) displayed on the screen and click 'Search'.",
                "Step 5: View your active 10-digit TIN, Taxpayer Legal Name, Station, and registration status.",
                "Step 6: You can download or print the official TIN confirmation slip directly from the results page.",
            ],
        )

    # Tax Refund Suite (Section 42 TPCA) — 6 Sub-Paths + Overview
    if portal_key == "get_refund" or (
        portal_key not in ("export_process", "customs_audits_refunds", "warehousing", "customs_enforcements", "laws_and_acts", "double_taxation_agreements")
        and not any(k in combined for k in ("the export process", "faqs for the export process", "the-exports-process", "customs audits and refunds", "customs-audits-and-refunds", "duty drawback", "diplomatic refund", "fuel refund", "c30", "c31", "c33", "c34", "warehousing", "bonded warehouse", "customs warehouse", "public online auction", "private treaty", "customs enforcements", "customs enforcement", "prohibited goods", "seizure notice", "c37", "c35"))
        and any(k in combined for k in ("refund", "overpaid tax", "vat refund", "input credit refund"))
        and not any(k in combined for k in ("credit note", "cancel invoice", "correct invoice"))
    ):
        # 1. Payment made to Ministry, Department or Agency (MDAs) — ONTR Refund
        if any(k in combined for k in ("mda", "ministry", "agency", "department", "passport", "police", "court fee", "non-tax revenue", "ntr", "ontr")):
            return (
                "URA Portal — Tax Refund: Payment made to Ministry, Department or Agency (MDAs)",
                "Refund application for erroneous, duplicate, or unutilized Other Non-Tax Revenue (ONTR) payments made to Government MDAs via URA PRN.",
                "info",
                [
                    "Step 1: Go to ura.go.ug > Get a Refund > Payment made to Ministry, Department or Agency (MDAs) for Other Non-Tax Revenue (ONTR) refunds.",
                    "Step 2: Enter the 13-digit Payment Registration Number (PRN) to auto-populate taxpayer details, email, mobile number, and administrative location.",
                    "Step 3: On the Refund Details page, enter detailed reason for refund, commercial bank name, branch, account number, and account holder name (must match applicant).",
                    "Step 4: Attach supporting documents: erroneous bank slip, correct bank slip, and mandatory MDA Clearance Endorsement Letter confirming service was cancelled or unrendered.",
                    "Step 5: Enter the captcha code and click Save to receive an electronic acknowledgement receipt with a tracking number.",
                ],
            )

        # 2. Motor Vehicle or Stamp Duty or Driving Permit — NTR Refund
        if any(k in combined for k in ("motor vehicle", "vehicle", "stamp duty", "driving permit", "number plate", "logbook", "udls", "land transfer refund")):
            return (
                "URA Portal — Tax Refund: Motor Vehicle, Stamp Duty & Driving Permit",
                "Refund claims for duplicate, excess, or cancelled payments on vehicle registration, stamp duty, or driving permits (NTR).",
                "info",
                [
                    "Step 1: Go to ura.go.ug > Get a Refund > Motor vehicle or Driving permit for Non-Tax Revenue (NTR) refunds.",
                    "Step 2: Select claim category: Motor Vehicle (enter vehicle chassis/registration number), Driving Permit (enter permit number), or Stamp Duty (select 'Refund For - Use Assessment Number').",
                    "Step 3: For Stamp Duty refunds: Obtain confirmation letter from the MDA, apply for instrument cancellation at the Stamp Duty office, and obtain a cancellation notice with an assessment number.",
                    "Step 4: Enter 13-digit PRN, refund amount, applicant mobile number, commercial bank name, branch, and account number.",
                    "Step 5: As the portal has no online attachment upload field for NTRs, submit physical copies (bank slips, driving permit, cancellation notice, NIN) to the nearest URA office or email services@ura.go.ug.",
                ],
            )

        # 3. Import or Export Tax Refund
        if any(k in combined for k in ("import", "export", "customs", "duty drawback", "short landing", "asycuda", "re-export")):
            return (
                "URA Portal — Tax Refund: Import & Export Customs Duties",
                "Customs tax refunds and duty drawbacks under EACCMA for over-assessed duties, short-landed goods, or re-exports.",
                "info",
                [
                    "Step 1: Log in to portal.ura.go.ug or Asycuda World; navigate to e-Services > Tax Refund > Import or Export Tax Refund.",
                    "Step 2: Enter Customs Declaration Entry Number (Asycuda C-series / E-series reference) and declaration year.",
                    "Step 3: Select legal refund ground under EACCMA (Section 144 / 148): Tariff re-classification, short-landing/damaged goods, diplomatic exemption, or duty drawback on exported raw materials.",
                    "Step 4: Upload supporting customs documents: Bill of Lading, Airway Bill, customs assessment notice, port examination report, and certificate of re-export.",
                    "Step 5: Submit verified bank account details; customs revenue audit verifies the claim and processes electronic refund via EFT.",
                ],
            )

        # 4. Track Application Status
        if any(k in combined for k in ("track", "application status", "check status", "tracking", "status")):
            return (
                "URA Portal — Tax Refund: Track Application Status",
                "Real-time tracking of submitted tax refund claims through audit, approval, and EFT disbursement stages.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Tax Refund > Track Application Status (accessible with or without login).",
                    "Step 2: Enter your 10-digit TIN and the Refund Application Reference Number (e.g. REF-XXXXXXXX) received upon submission.",
                    "Step 3: Enter the security CAPTCHA code and click Track Status.",
                    "Step 4: Review progress stage: 'Application Received' → 'Desk Audit In-Progress' → 'Audit Approved' → 'Sent to Treasury/Bank of Uganda' → 'EFT Payment Disbursed'.",
                    "Step 5: If the system status states 'Additional Information Required', click the action link to upload requested bank statements or invoices within 15 statutory days.",
                ],
            )

        # 5. Download Refund Manual Forms
        if any(k in combined for k in ("manual form", "download form", "download refund manual", "dt-4001", "c20", "voucher")):
            return (
                "URA Portal — Tax Refund: Download Manual Refund Forms",
                "Official printable PDF claim forms for domestic taxes, customs duty refunds, and MDA non-tax revenue.",
                "info",
                [
                    "Step 1: Visit portal.ura.go.ug > e-Services > Tax Refund > Download Refund Manual Forms.",
                    "Step 2: Download the required statutory PDF form:",
                    "  • Form DT-4001: Domestic Tax Refund Application Form (Income Tax, VAT, Withholding, Excise).",
                    "  • Customs Form C20 / C21: Customs Duty Refund and Drawback Application Form (under EACCMA).",
                    "  • MDA Non-Tax Revenue (NTR) Refund Claim Voucher (for Government Ministries, Departments & Agencies).",
                    "  • Motor Vehicle & Stamp Duty Refund Claim Voucher.",
                    "Step 3: Complete all fields in ink, sign, attach proof of payment (PRN & bank deposit slips), and agency endorsements.",
                    "Step 4: Lodge the physical claim file at your nearest URA Domestic Taxes Service Centre or Customs Station.",
                ],
            )

        # 6. Document Authentication
        if any(k in combined for k in ("document authentication", "authenticate", "verify document", "verify voucher", "genuine")):
            return (
                "URA Portal — Tax Refund: Document Authentication & Verification",
                "Official verification of URA refund approval letters, withholding credit vouchers, and tax clearance documents.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Tax Refund > Document Authentication (or e-Services > Document Authentication).",
                    "Step 2: Select document category: Tax Refund Approval Letter, Withholding Tax Credit Voucher, or Tax Clearance Certificate (TCC).",
                    "Step 3: Enter the unique Document Reference Number or Acknowledgement Number printed on the URA document.",
                    "Step 4: Enter the security CAPTCHA code and click Verify Document.",
                    "Step 5: The portal validates the record against central databases, displaying taxpayer TIN, issue date, approved amount, and validity status.",
                ],
            )

        # Default: General Tax Refund (Section 42 TPCA / VAT & Income Tax)
        return (
            "URA Portal — Tax Refund Application (Section 42 TPCA)",
            "Application and tracking for VAT input credit refunds and overpaid income taxes.",
            "info",
            [
                "Step 1: Log in to portal.ura.go.ug with your active 10-digit TIN and password.",
                "Step 2: Navigate to e-Services > Tax Refund > Apply for Tax Refund (or Domestic Taxes > Refunds).",
                "Step 3: Select refund category: VAT Input Tax Credit (Section 31 VAT Act), Overpaid Income Tax (Section 113 ITA), or Withholding Tax Credit.",
                "Step 4: Enter verified commercial bank account details (bank account name must match URA TIN registration name).",
                "Step 5: Attach supporting documentation: EFRIS purchase invoices with FDNs, proof of payment, and withholding tax certificates.",
                "Step 6: URA audits and approves the refund within the 90-day statutory timeline, followed by electronic EFT bank disbursement.",
            ],
        )

    # Tax Incentives Suite (Section 21 ITA & WHT Exemptions) — 13 Sub-Paths + Overview
    if portal_key == "tax_incentives" or (
        any(
            k in combined
            for k in (
                "incentive",
                "tax holiday",
                "free zone",
                "industrial park",
                "wht exemption",
                "exemption list",
                "withholding agent",
                "investor guide",
                "investors guide",
                "tax waiver",
                "non-resident providers of digital services",
            )
        )
        and portal_key not in ("get_refund", "export_process", "laws_and_acts", "double_taxation_agreements", "oil_and_gas", "health_sector", "agriculture_sector", "hospitality_sector", "wholesale_retail_sector", "construction_sector", "manufacturing_sector", "education_sector", "mining_sector", "entertainment_sector", "schools_curriculum", "real_estate_sector", "fishing_sector", "transport_sector", "government_agencies", "opportunities_portal", "research_publications")
        and not any(k in combined for k in ("the export process", "faqs for the export process", "the-exports-process"))
        and "refund" not in combined
    ):
        # 1. WHT Exemption Application
        if (
            any(k in combined for k in ("wht exemption application", "apply for wht", "apply for withholding tax exemption"))
            or any(k in combined for k in ("wht exemption", "withholding tax exemption", "withholding exemption"))
        ) and not any(k in combined for k in ("list", "schedule", "register", "gazette")):
            return (
                "URA Portal — Tax Incentives: WHT Exemption Application",
                "Application for 6% Withholding Tax exemption under Section 119(5) of the Income Tax Act.",
                "info",
                [
                    "Step 1: Log in to portal.ura.go.ug with your active 10-digit TIN and password.",
                    "Step 2: Navigate to e-Services > Tax Incentives > WHT Exemption (or e-Registration > Tax Exemption Application).",
                    "Step 3: Verify statutory eligibility: Taxpayer must have filed all tax returns and settled all liabilities for the preceding 3 consecutive years without outstanding arrears.",
                    "Step 4: Complete the online WHT Exemption Application Form and attach audited financial statements.",
                    "Step 5: Submit application; system conducts automatic ledger compliance check and issues a WHT Exemption Certificate valid for 1 fiscal year (July 1 to June 30).",
                ],
            )

        # 2. Investors Guides
        if any(k in combined for k in ("investor guide", "investors guide", "investor handbook", "investment guide", "investors handbook")):
            return (
                "URA Portal — Tax Incentives: Investors Guides",
                "Comprehensive guidelines and statutory criteria for local and foreign investors in Uganda.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Tax Incentives > Investors Guides (or Quick Links > Investor Handbook).",
                    "Step 2: Capital investment thresholds: USD 10 million for foreign non-citizen investors; USD 1 million for Ugandan and East African Community (EAC) citizens (or USD 5 million for designated agro-processing exporters).",
                    "Step 3: Operational requirements: Employ at least 100 Ugandan citizens (or at least 70% of total staff) and utilize at least 50% locally sourced raw materials.",
                    "Step 4: Access guidelines on establishing operations in designated Namanve, Mbale, Soroti, and Kapeeka Industrial Parks or Free Zones.",
                    "Step 5: Download the official URA & Uganda Investment Authority (UIA) joint investment policy manual.",
                ],
            )

        # 3. Income Tax Exemption
        if any(k in combined for k in ("income tax exemption", "charity exemption", "charitable", "exempt income", "section 21 exemption")):
            return (
                "URA Portal — Tax Incentives: Income Tax Exemption (Section 21 ITA)",
                "Statutory exemptions from income tax for qualifying manufacturers, institutions, and collective investments.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Tax Incentives > Income Tax Exemption.",
                    "Step 2: Review exempt categories under Section 21 of the Income Tax Act: Registered charitable/religious/educational public institutions, collective investment schemes, amateur sporting associations, and qualifying agro-processors.",
                    "Step 3: Complete the statutory application and upload governing constitution, registered trust deed, UIA investment license, or sector regulator authorization.",
                    "Step 4: Attach 3 years of audited accounts and proof of charitable/public benefit expenditure.",
                    "Step 5: URA Technical Committee reviews the dossier and issues a formal Income Tax Exemption Ruling / Certificate.",
                ],
            )

        # 4. Import or Export Tax Exemption
        if any(k in combined for k in ("import or export", "import tax exemption", "export tax exemption", "customs exemption", "eaccma fifth schedule", "machinery exemption", "duty free machinery")):
            return (
                "URA Portal — Tax Incentives: Import & Export Customs Tax Exemption",
                "Customs import duty and VAT exemptions on machinery, equipment, and raw materials under EACCMA Fifth Schedule.",
                "info",
                [
                    "Step 1: Log in to portal.ura.go.ug or Asycuda World > e-Services > Tax Incentives > Import or Export Tax Exemption.",
                    "Step 2: Enter the Asycuda Customs Entry / Declaration Number and importer/exporter TIN.",
                    "Step 3: Select exemption legal regime under the EAC Customs Management Act (EACCMA Fifth Schedule): Plant and machinery for manufacturing (0% duty, VAT deemed-paid/exempt), specialized solar equipment, agricultural inputs, or mining gear.",
                    "Step 4: Upload supporting import documentation: Commercial Invoice, Bill of Lading, packing list, and sector ministry recommendation (e.g. Ministry of Finance, Agriculture, or Energy).",
                    "Step 5: Customs clearance officer applies the statutory exemption code in Asycuda World to release cargo duty-free.",
                ],
            )

        # 5. Track Application Status
        if any(k in combined for k in ("track", "application status", "check status", "tracking")):
            return (
                "URA Portal — Tax Incentives: Track Application Status",
                "Real-time tracking of submitted WHT exemption, income tax exemption, and tax incentive applications.",
                "info",
                [
                    "Step 1: Visit portal.ura.go.ug > e-Services > Tax Incentives > Track Application Status (accessible with or without login).",
                    "Step 2: Enter your 10-digit TIN and the Exemption Application Search Number / Reference Number received upon submission.",
                    "Step 3: Enter the on-screen security CAPTCHA and click Track Status.",
                    "Step 4: Review application processing stage: 'Application Lodged' → 'Tax Ledger Audit' → 'Compliance Review' → 'Approved - Certificate Issued' or 'Additional Compliance Documentation Required'.",
                    "Step 5: If approved, click the download link to retrieve your signed electronic Exemption Certificate in PDF format.",
                ],
            )

        # 6. Withholding Tax Exemption List
        if any(k in combined for k in ("wht exemption list", "withholding tax exemption list", "exemption list", "schedule of exempt taxpayers", "who is exempt from wht")):
            return (
                "URA Portal — Tax Incentives: Withholding Tax Exemption List",
                "Public gazetted registry of all compliant corporate entities and individuals granted 6% WHT exemption.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Tax Incentives > Withholding Tax Exemption List (public access, no login required).",
                    "Step 2: Search by company legal name or active 10-digit TIN to verify if a vendor or contractor is exempt from 6% WHT.",
                    "Step 3: Confirm certificate validity period (valid for the active fiscal year ending June 30).",
                    "Step 4: Download the full gazetted WHT Exemption Schedule in PDF or Excel format.",
                    "Step 5: Procuring entities must deduct 6% WHT if a vendor is not actively listed on this official schedule.",
                ],
            )

        # 7. VAT Withholding Agent List
        if any(k in combined for k in ("vat withholding agent", "vat wht agent", "vat withholding list", "vat agent list")):
            return (
                "URA Portal — Tax Incentives: VAT Withholding Agent List",
                "Statutory directory of entities designated by the Minister under Section 5(2) VAT Act to withhold VAT at source.",
                "info",
                [
                    "Step 1: Visit portal.ura.go.ug > e-Services > Tax Incentives > VAT Withholding Agent List.",
                    "Step 2: Search by organization name or TIN to check if a client or agency is a gazetted VAT withholding agent.",
                    "Step 3: Designated VAT agents (Government MDAs, public entities, and designated large taxpayers) withhold 6% (or 100% of 18% VAT where gazetted) upon paying suppliers.",
                    "Step 4: The agent must remit withheld VAT to URA on Form DT-1014 by the 15th day of the subsequent month.",
                    "Step 5: Suppliers receive an automatic VAT withholding credit on their URA ledger to offset their monthly VAT return payable.",
                ],
            )

        # 8. Designated Income Tax WHT Agents For FY 2024/25
        if any(k in combined for k in ("designated income tax", "wht agents for fy", "designated wht agents", "income tax wht agents", "agents for fy 2024/25", "wht agent list")):
            return (
                "URA Portal — Tax Incentives: Designated Income Tax WHT Agents For FY 2024/25",
                "Official statutory gazette of designated 6% Withholding Tax agents for Financial Year 2024/25.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Tax Incentives > Designated Income Tax WHT Agents For FY 2024/25.",
                    "Step 2: Search the active FY 2024/25 schedule by entity name or TIN.",
                    "Step 3: All listed companies, NGOs, and public institutions are legally mandated under Section 119 ITA to withhold 6% income tax from any supplier payment exceeding UGX 1,000,000.",
                    "Step 4: Failure to withhold makes the designated agent personally liable for the full 6% tax plus statutory penalties.",
                    "Step 5: Download the complete FY 2024/25 gazette notice in PDF format for corporate compliance records.",
                ],
            )

        # 9. List of Non-Resident providers of digital services registered with URA
        if any(k in combined for k in ("non-resident", "non resident", "digital services", "dst provider", "digital provider", "netflix", "google", "meta", "uber")):
            return (
                "URA Portal — Tax Incentives: List of Non-Resident Digital Service Providers",
                "Official public registry of foreign digital platform operators registered under Section 86A ITA for Uganda Digital Services Tax.",
                "info",
                [
                    "Step 1: Visit portal.ura.go.ug > e-Services > Tax Incentives > List of Non-Resident providers of digital services registered with URA.",
                    "Step 2: Search multinational tech platforms (e.g. Google, Meta, Netflix, Apple, Uber, Amazon, Microsoft, Zoom, Spotify).",
                    "Step 3: Verify the entity's registered Uganda Digital TIN and compliance standing.",
                    "Step 4: Registered non-resident providers remit 5% Digital Services Tax (DST) on gross digital revenue derived from Ugandan consumers.",
                    "Step 5: Local businesses procuring advertising or software services can confirm registration status for corporate withholding compliance.",
                ],
            )

        # 10. Tax Holiday
        if any(k in combined for k in ("tax holiday", "10-year holiday", "10 year holiday", "holiday criteria", "holiday conditions")):
            return (
                "URA Portal — Tax Incentives: 10-Year Tax Holiday (Section 21 ITA)",
                "Statutory 10-year income tax holiday for strategic investments in manufacturing, agro-processing, and industrial parks.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Tax Incentives > Tax Holiday.",
                    "Step 2: Verify eligibility under Section 21(1)(y)/(z) ITA: Investment in Free Zones, Industrial Parks, or agro-processing exporting 80%+ of finished goods.",
                    "Step 3: Ensure capital threshold of USD 10 million (foreign) or USD 1 million (local/EAC citizen) and minimum 100 Ugandan employees.",
                    "Step 4: Submit formal application along with UIA Investment License, environmental clearance (NEMA), and business plan.",
                    "Step 5: File mandatory annual monitoring compliance returns with URA to maintain tax holiday status throughout the 10-year term.",
                ],
            )

        # 11. Tax Waiver & Tujenge Pack (Section 47B TPCA)
        if any(k in combined for k in ("tax waiver", "tujenge", "tujenge pack", "section 47b", "section 47a", "waiver brochure", "waiver of penalty", "waiver of interest", "remission", "section 40", "hardship waiver")):
            return (
                "URA Portal — Tax Incentives: Tax Waiver & Penal Interest Remission (Tujenge Pack & Section 47B TPCA)",
                "Procedures for obtaining 100% waiver of penalties and interest under Section 47B TPCA, Tujenge Pack, Section 66 TPCA, and Section 40 remission.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Tax Incentives > Tax Waiver (or Domestic Taxes > Voluntary Disclosure / Tujenge Pack at ura.go.ug/en/tujenge-pack/).",
                    "Step 2: Under Section 47B TPCA (Tujenge Pack): 100% waiver of penal tax and interest outstanding as at 30th June 2024 is granted where the taxpayer settles all principal tax by 30th June 2026. Proactive voluntary disclosures under Section 66 TPCA also receive 100% waiver.",
                    "Step 3: Pro-Rata Relief: Where a taxpayer pays part of their principal tax outstanding as at 30th June 2024 by 30th June 2026, interest and penalties are waived proportionally to the extent of principal paid.",
                    "Step 4: Covered Domestic Taxes: Applies to penalties and interest under Income Tax, VAT, Excise Duty, Lotteries & Gaming, and Stamp Duty Acts. Non-principal penalties (DTS, EFRIS non-compliance, failure to provide information, court penalties) are excluded.",
                    "Step 5: Download the official Tax Waiver Brochure FY 2025/26 (package 67672) at ura.go.ug/download-category/tax-waiver-brochure/ for detailed computation schedules.",
                ],
            )

        # 12. Document Authentication
        if any(k in combined for k in ("document authentication", "authenticate", "verify document", "verify certificate", "verify ruling", "genuine")):
            return (
                "URA Portal — Tax Incentives: Document Authentication & Verification",
                "Verification of URA Exemption Certificates, WHT Exemption Letters, and Tax Holiday Rulings.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Tax Incentives > Document Authentication (or e-Services > Document Authentication).",
                    "Step 2: Select document type: WHT Exemption Certificate, Section 21 Exemption Letter, or Tax Clearance Certificate.",
                    "Step 3: Enter the unique Document Reference Number or Exemption Certificate Number printed on the document.",
                    "Step 4: Enter the security CAPTCHA code and click Verify Document.",
                    "Step 5: The portal verifies the record against official URA registers, displaying taxpayer legal name, TIN, issue date, expiry date, and validity status.",
                ],
            )

        # 13. Withholding Tax (General Overview & Rates)
        if any(k in combined for k in ("withholding tax", "wht overview", "wht rates", "wht return", "dt-1013", "withholding-tax")):
            return (
                "URA Portal — Tax Incentives: Withholding Tax (WHT) Overview & Filing (FY 2026-27)",
                "Comprehensive guidelines on statutory withholding tax rates, remittance deadlines, and filing obligations under Vol. 2 Issue 5.",
                "info",
                [
                    "Step 1: Understand statutory WHT rates: 6% on goods/services > UGX 1,000,000 to designated agents, resident professional fees, and import WHT; 10% on 10+ yr government bonds, listed dividends, reinsurance, and telecom/mobile money commissions; 15% on betting winnings, bank interest, unlisted dividends, and non-resident royalties/management; 20% on <10 yr bonds; 5% foreign debenture interest; 2% foreign transport/shipping.",
                    "Step 2: Revised FY 2026/27 PAYE monthly threshold: 0–UGX 335,000 Nil; 20% on UGX 335k–410k; UGX 15,000 + 25% on UGX 410k–485k; UGX 33,750 + 30% on UGX 485k–10M; + 10% above 10M.",
                    "Step 3: Log in to portal.ura.go.ug > e-Services > Returns > File a Return > Withholding Tax (Form DT-1013) or download the guide at ura.go.ug/storage/2026/09/WITHHOLDING-TAX-2026-27.pdf.",
                    "Step 4: Remit all withheld tax to URA within 15 days after the end of the month in which payment was made (or within 5 days for non-resident entertainers).",
                    "Step 5: Generate and issue automatic Withholding Tax Credit Certificates to payees via the portal and retain records for at least 5 years.",
                ],
            )

        # Default: Tax Incentives Overview
        return (
            "URA Portal — Tax Incentives & Statutory Exemptions Overview",
            "Fiscal investment incentives, 10-year tax holidays, and statutory withholding exemptions under Uganda tax law.",
            "info",
            [
                "Step 1: Log in to portal.ura.go.ug and navigate to e-Services > Tax Incentives.",
                "Step 2: Explore available regimes: Section 21 ITA 10-year tax holiday, Free Zone/Industrial Park developer relief, WHT exemptions, and EACCMA duty waivers.",
                "Step 3: Review qualifying criteria: Minimum investment capital (USD 1M domestic / USD 10M foreign), 50% local raw materials, and 100 Ugandan workers.",
                "Step 4: Submit applications online with supporting UIA license and certified financial accounts.",
                "Step 5: Monitor application status in real time via e-Services > Tax Incentives > Track Application Status.",
            ],
        )

    # Motor Vehicle Suite — 7 Sub-Paths + Overview
    if portal_key == "motor_vehicle" or (
        any(k in combined for k in ("motor vehicle", "vehicle", "logbook", "number plate", "vanity plate", "tr vii"))
        and portal_key not in ("tax_incentives", "get_refund", "customs_valuation", "laws_and_acts", "double_taxation_agreements", "hospitality_sector", "oil_and_gas", "agriculture_sector", "wholesale_retail_sector", "health_sector", "transport_sector", "construction_sector", "manufacturing_sector", "real_estate_sector")
        and not any(k in combined for k in ("customs valuation", "customs-valuation"))
        and "refund" not in combined
    ):
        # 1. Search Vehicle Details Application
        if any(
            k in combined
            for k in (
                "search vehicle details application",
                "search vehicle application",
                "apply for vehicle search",
                "search vehicle details",
                "search motor vehicle application",
                "vehicle search application",
            )
        ):
            return (
                "URA Portal — Motor Vehicle: Search Vehicle Details Application",
                "Application to perform an official motor vehicle search on ownership, engine, chassis, and tax status.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Motor Vehicle > Search Vehicle Details Application.",
                    "Step 2: Enter the vehicle Registration Number (e.g. UBA 123X) and select search reason (purchase due diligence, official inquiry, court case).",
                    "Step 3: Enter applicant TIN, registered mobile number, and contact email address.",
                    "Step 4: The system assesses the official statutory search fee of UGX 24,000 (plus bank charge UGX 2,000); generate the Payment Registration Number (PRN).",
                    "Step 5: Pay the PRN through any commercial bank, mobile money (*165# / *185#), or online card checkout.",
                    "Step 6: Retain your PRN and Search Reference Number to access the search report under 'View Vehicle Search Report'.",
                ],
            )

        # 2. View Vehicle Search Report
        if any(
            k in combined
            for k in (
                "view vehicle search report",
                "vehicle search report",
                "view search report",
                "print search report",
                "download search report",
            )
        ):
            return (
                "URA Portal — Motor Vehicle: View Vehicle Search Report",
                "Retrieve, view, and print certified motor vehicle registration details and ownership history.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Motor Vehicle > View Vehicle Search Report.",
                    "Step 2: Enter the Search Application Reference Number or the paid PRN number.",
                    "Step 3: Enter the security CAPTCHA code and click 'View / Generate Report'.",
                    "Step 4: The system verifies payment settlement and displays the full certified vehicle search record:",
                    "  • Current Registered Owner Legal Name and TIN",
                    "  • Vehicle Make, Model, Body Type, Colour, and Year of Manufacture",
                    "  • Chassis Number / VIN and Engine Number",
                    "  • Caveats, Liens, Bank Hypothecations, or Police Alerts",
                    "  • Customs Duty Payment Status and Original Entry Reference",
                    "Step 5: Click 'Download PDF' to print the official certified URA search report with security barcode.",
                ],
            )

        # 3. Track Application Status
        if any(k in combined for k in ("track", "application status", "check status", "tracking")):
            return (
                "URA Portal — Motor Vehicle: Track Application Status",
                "Real-time tracking of submitted vehicle transfers, initial registrations, and duplicate logbook claims.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Motor Vehicle > Track Application Status.",
                    "Step 2: Enter your 10-digit TIN and the Application Search Number received via SMS/email upon submission.",
                    "Step 3: Enter the security CAPTCHA code and click Track Status.",
                    "Step 4: Review application processing stage: 'Application Received' → 'Assessment Issued' → 'Physical Inspection Pending' → 'Officer Approval' → 'Logbook / Plates Ready for Pickup'.",
                    "Step 5: If the status displays 'Inspection Required', book an appointment at the nearest URA vehicle licensing station (e.g. Nakawa, Kyambogo).",
                ],
            )

        # 4. Download Vehicle Manual Forms
        if any(
            k in combined
            for k in (
                "manual form",
                "download form",
                "download vehicle manual",
                "tr vii",
                "tr i",
                "tr iii",
                "tr iv",
                "tr viii",
                "tr x",
            )
        ):
            return (
                "URA Portal — Motor Vehicle: Download Manual Forms",
                "Official statutory PDF forms for vehicle transfers, alterations, duplicate logbooks, and deregistration.",
                "info",
                [
                    "Step 1: Visit portal.ura.go.ug > e-Services > Motor Vehicle > Download Vehicle Manual Forms.",
                    "Step 2: Download the required statutory PDF form:",
                    "  • Form TR VII: Application for Transfer of Ownership of Motor Vehicle.",
                    "  • Form TR I: Application for First Registration of Motor Vehicle / Motorcycle.",
                    "  • Form TR III: Application for Duplicate Registration Certificate (Lost Logbook).",
                    "  • Form TR IV: Notice of Alteration of Motor Vehicle (Engine swap, colour change, body alteration).",
                    "  • Form TR VIII: Application for Cancellation of Registration / Deregistration.",
                    "  • Form TR X: Application for Personalized / Vanity Registration Plates.",
                    "Step 3: Print and fill out the form in ink, sign, and attach required police reports and tax receipts.",
                    "Step 4: Present the completed physical package at your designated URA Motor Vehicle Inspection Centre.",
                ],
            )

        # 5. Document Authentication
        if any(
            k in combined
            for k in (
                "document authentication",
                "authenticate",
                "verify logbook",
                "verify certificate",
                "verify registration",
                "genuine",
            )
        ):
            return (
                "URA Portal — Motor Vehicle: Document Authentication & Logbook Verification",
                "Online verification of computerized motor vehicle logbooks, search reports, and transfer certificates.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Motor Vehicle > Document Authentication (or e-Services > Document Authentication).",
                    "Step 2: Select document type: Motor Vehicle Registration Book (Logbook), Vehicle Search Certificate, or Transfer Approval Letter.",
                    "Step 3: Enter the unique Logbook Serial Number or Document Reference Number printed on the certificate.",
                    "Step 4: Enter the security CAPTCHA characters and click Verify Document.",
                    "Step 5: The portal verifies the authenticity of the logbook against the national vehicle registry, confirming registered owner name, chassis number, registration plate, and date of issue.",
                ],
            )

        # 6. Print TIN Submitted Forms
        if any(
            k in combined
            for k in (
                "print tin submitted forms",
                "print submitted form",
                "print vehicle form",
                "reprint form",
                "print form",
            )
        ):
            return (
                "URA Portal — Motor Vehicle: Print TIN Submitted Forms",
                "Reprint submitted motor vehicle transfer deeds, registration applications, and assessment vouchers.",
                "info",
                [
                    "Step 1: Visit portal.ura.go.ug > e-Services > Motor Vehicle > Print TIN Submitted Forms.",
                    "Step 2: Enter your active 10-digit TIN and the Application Reference Number / Search Number.",
                    "Step 3: Select the document to reprint: Form TR VII transfer deed, initial registration submission slip, or payment assessment note.",
                    "Step 4: Complete the security CAPTCHA verification code and click 'Print / Download'.",
                    "Step 5: Print the official PDF copy bearing the URA system watermark and barcode for presentation during physical vehicle inspection.",
                ],
            )

        # 7. Motor Vehicle Transfer of Ownership
        if any(k in combined for k in ("transfer", "ownership", "sell", "buy", "change owner")):
            return (
                "URA Portal — Motor Vehicle: Transfer of Ownership",
                "Official vehicle ownership transfer between registered seller and buyer.",
                "info",
                [
                    "Step 1: Seller logs in to portal.ura.go.ug > e-Services > Motor Vehicle > Transfer of Ownership.",
                    "Step 2: Seller inputs vehicle registration number and buyer's active 10-digit TIN.",
                    "Step 3: Buyer receives SMS/portal notification, logs in, and accepts the transfer.",
                    "Step 4: System assesses 1.5% stamp duty and statutory transfer fee (UGX 100,000); pay via generated PRN.",
                    "Step 5: Present vehicle for physical inspection at licensing station to collect updated logbook.",
                ],
            )

        # 8. Personalized / Vanity Number Plates
        if any(k in combined for k in ("personalized", "vanity", "custom plate")):
            return (
                "URA Portal — Motor Vehicle: Personalized / Vanity Number Plates",
                "Application procedure for customized motor vehicle registration plates.",
                "info",
                [
                    "Step 1: Log in to portal.ura.go.ug > e-Services > Motor Vehicle > Apply for Personalized Plate.",
                    "Step 2: Check plate availability (3 to 8 alphanumeric characters, subject to decency vetting).",
                    "Step 3: Generate PRN and pay statutory fee of UGX 20,000,000 via bank or electronic payment.",
                    "Step 4: Collect stamped authorization letter and manufacture approval within 5 working days.",
                ],
            )

        # Default: Motor Vehicle Services Hub
        return (
            "URA Portal — Motor Vehicle Services Hub",
            "Self-service motor vehicle licensing, logbook certification, search, and transfer.",
            "info",
            [
                "Step 1: Visit portal.ura.go.ug > e-Services > Motor Vehicle.",
                "Step 2: Choose service: Search Vehicle Details, View Vehicle Search Report, Transfer of Ownership, Download Forms, or Personalized Plates.",
                "Step 3: Enter vehicle registration number and applicant TIN to view certified records or generate payment PRN.",
                "Step 4: Track processing status online at any time via Motor Vehicle > Track Application Status.",
            ],
        )

    # Choose a Tax Agent Suite (https://ura.go.ug/en/choose-agents/) — 5 Sub-Paths + Overview
    if portal_key == "choose_tax_agent" or (
        any(
            k in combined
            for k in (
                "choose agent",
                "choose-agent",
                "choose a tax agent",
                "licensed dt agent",
                "licensed customs agent",
                "licensed import & export",
                "licensed import/export",
                "choose-agents",
            )
        )
        and portal_key not in ("tax_incentives", "get_refund", "motor_vehicle", "customs_valuation", "laws_and_acts", "double_taxation_agreements")
        and not any(k in combined for k in ("customs valuation", "customs-valuation"))
        and "refund" not in combined
    ):
        # 1. Licensed Customs Agents
        if any(k in combined for k in ("customs agent", "import & export", "import/export", "clearing agent", "clearing and forwarding")):
            return (
                "URA Portal — Choose a Tax Agent: Licensed Customs Agents",
                "Search and verify licensed import/export clearing and forwarding agents accredited under EACCMA.",
                "info",
                [
                    "Step 1: Visit ura.go.ug/en/choose-agents/licensed-import-export-tax-agents/.",
                    "Step 2: Search licensed clearing agents by Agent Name, TIN, Tax Office, or License Number.",
                    "Step 3: Check registration status, license issue date, and license expiry date to ensure active standing.",
                    "Step 4: Verify that the customs agent is accredited on Asycuda World for cargo declarations.",
                    "Step 5: Mandatory Safeguard: Never give tax payment funds to the clearing agent directly. Generate a PRN on the customs assessment and pay directly via bank or mobile money.",
                ],
            )

        # 2. Licensed Domestic Tax (DT) Agents
        if any(k in combined for k in ("domestic tax agent", "licensed dt agent", "dt agent", "tarc", "tax practitioner")):
            return (
                "URA Portal — Choose a Tax Agent: Licensed Domestic Tax (DT) Agents",
                "Directory of tax practitioners approved and licensed by the Tax Agents Registration Committee (TARC).",
                "info",
                [
                    "Step 1: Visit ura.go.ug/en/choose-agents/licensed-domestic-tax-agents/.",
                    "Step 2: Search registered tax practitioners by Agent Name, TIN, Tax Office, or License Number.",
                    "Step 3: Confirm active licensing status by the Tax Agents Registration Committee (TARC), approval date, and license expiration date.",
                    "Step 4: Once verified, log in to portal.ura.go.ug (or ura.go.ug/en/etax-login/) and navigate to e-Services > Tax Agent Management > Appoint Tax Agent.",
                    "Step 5: Input the agent's verified 10-digit TIN, specify representation scope (returns, objections, refunds), and submit the mandate.",
                ],
            )

        # 3. Income Tax Agents - WHT
        if any(k in combined for k in ("income tax agent", "income tax agents - wht", "income tax wht agent", "section 119 agent")):
            return (
                "URA Portal — Choose a Tax Agent: Income Tax Agents - WHT",
                "Statutory directory of entities designated under Section 119 ITA to withhold 6% income tax at source.",
                "info",
                [
                    "Step 1: Go to ura.go.ug/en/choose-agents/ and select the 'Income Tax Agents - WHT' tab.",
                    "Step 2: Search by TIN, Taxpayer Legal Name, or Designation Period.",
                    "Step 3: Verify if a customer, corporate client, or public agency is legally designated to withhold 6% income tax on payments exceeding UGX 1,000,000.",
                    "Step 4: Designated withholding agents must issue a formal URA Withholding Tax Credit Certificate upon withholding.",
                    "Step 5: Suppliers can verify withheld tax credits directly in their URA portal ledger to offset annual income tax liability.",
                ],
            )

        # 4. VAT Agents - WHT
        if any(k in combined for k in ("vat agent", "vat agents - wht", "vat wht agent", "section 5(2) agent")):
            return (
                "URA Portal — Choose a Tax Agent: VAT Agents - WHT",
                "Statutory schedule of designated VAT withholding agents appointed under Section 5(2) of the VAT Act.",
                "info",
                [
                    "Step 1: Visit ura.go.ug/en/choose-agents/ and select the 'VAT Agents - WHT' tab.",
                    "Step 2: Search designated entities by TIN, Taxpayer Name, or Designation Period.",
                    "Step 3: Appointed VAT agents (government ministries, large enterprises, and NGOs) withhold 6% (or standard 18% where gazetted) of taxable value from supplier invoices.",
                    "Step 4: Withheld VAT is remitted to URA on Form DT-1014 by the 15th day of the subsequent calendar month.",
                    "Step 5: Suppliers receive an automatic credit on their URA VAT ledger to offset monthly VAT payable.",
                ],
            )

        # 5. Appoint Agent (Portal Workflow & Responsibilities)
        if any(k in combined for k in ("appoint agent", "appoint a tax agent", "appoint tax agent", "authorization", "mandate", "delegate")):
            return (
                "URA Portal — Tax Agent Appointment & Authorization (Appoint a Tax Agent)",
                "Formal delegation of tax compliance and legal representation powers to a licensed agent.",
                "info",
                [
                    "Step 1: Log in to portal.ura.go.ug or click 'Appoint Agent' at ura.go.ug/en/etax-login/ using your active 10-digit TIN and password.",
                    "Step 2: Navigate to e-Services > Tax Agent Management > Appoint Tax Agent.",
                    "Step 3: Enter the licensed tax agent's 10-digit TIN or registered agency firm number.",
                    "Step 4: Select authorization scope: Return Filing & Certification, Ruling Requests & Petitions, Objections & Reinvestigations (Section 24 TPCA), Refund Claims (Section 42 TPCA), or Representation in Hearings/TAT.",
                    "Step 5: Set the mandate expiry date and submit; the appointed agent receives and confirms the mandate online.",
                    "Step 6: Crucial Rule: Taxpayers must NEVER handover payment obligations for taxes exclusively to tax agents. All tax payments must be paid directly into the URA bank account via PRN.",
                ],
            )

        # Default: Choose a Tax Agent Overview
        return (
            "URA Portal — Choose a Tax Agent Overview & Guidelines",
            "Regulatory guidelines, directories, and responsibilities for appointing licensed tax and customs agents in Uganda.",
            "info",
            [
                "Step 1: Visit ura.go.ug/en/choose-agents/ to explore licensed tax and customs agents.",
                "Step 2: A tax agent is an individual, partnership, or company vetted and licensed by the Tax Agents Registration Committee (TARC).",
                "Step 3: Tax agents assist with return preparation, petitions for rulings, objection lodging, refund applications, and taxpayer representation in meetings and hearings.",
                "Step 4: Use the four dedicated search registers: Licensed Customs Agents, Licensed DT Agents, Income Tax Agents - WHT, and VAT Agents - WHT.",
                "Step 5: Golden Rule: Always pay tax liabilities directly to URA using a generated PRN; never give tax payment funds to an agent.",
            ],
        )

    # Objections and Appeals Suite (Section 24 TPCA & Appeals) — 8 Sub-Paths + Overview
    if portal_key == "objection_appeals" or (
        (
            any(
                k in combined
                for k in (
                    "objection",
                    "appeal",
                    "tax appeals tribunal",
                    "dispute assessment",
                    "section 24",
                    "penalty reversal",
                    "alternative dispute resolution",
                )
            )
            or bool(re.search(r"\btat\b", combined))
        )
        and portal_key not in ("tax_incentives", "get_refund", "motor_vehicle", "choose_tax_agent", "stamp_duty", "tax_clearance", "export_process", "customs_valuation", "single_customs_territory", "exempt_importation", "aeo", "customs_audits_refunds", "warehousing", "customs_enforcements", "laws_and_acts", "double_taxation_agreements", "case_summary_reports", "court_of_appeal", "debt_collections", "financial_intelligence_authority", "oil_and_gas", "health_sector", "business_formalisation", "agriculture_sector", "hospitality_sector", "wholesale_retail_sector", "construction_sector", "manufacturing_sector", "education_sector", "mining_sector", "entertainment_sector", "schools_curriculum", "real_estate_sector", "fishing_sector", "transport_sector", "government_agencies", "opportunities_portal", "research_publications")
        and "refund" not in combined
    ):
        # 1. Object to a Tax Assessment
        if any(k in combined for k in ("object to a tax assessment", "object to assessment", "dispute assessment", "assessment objection")):
            return (
                "URA Portal — Objections & Appeals: Object to a Tax Assessment",
                "Formal statutory objection to a tax assessment issued by the Commissioner under Section 24 TPCA.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Objections and Appeals > Object to a Tax Assessment.",
                    "Step 2: Ensure submission within the strict 45-day statutory deadline from the date of service of the assessment notice.",
                    "Step 3: Enter the 10-digit TIN and the Assessment Registration Number (ARN) / Notice Number.",
                    "Step 4: Specify precisely in writing the legal and factual grounds on which you disagree with the assessment.",
                    "Step 5: Upload supporting documentation: audited accounts, bank statements, relevant contracts, and reconciliations.",
                    "Step 6: Submit to obtain your Objection Reference Number; URA must issue a decision within 90 days, or the objection is deemed allowed by law.",
                ],
            )

        # 2. Object To Other Decisions
        if any(k in combined for k in ("object to other decisions", "other decisions", "administrative decision", "licensing decision", "refusal decision")):
            return (
                "URA Portal — Objections & Appeals: Object To Other Decisions",
                "Objection to non-assessment administrative decisions, tax determinations, license refusals, or agency notices.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Objections and Appeals > Object To Other Decisions.",
                    "Step 2: Select decision category: Denial of Tax Clearance Certificate (TCC), TIN revocation/suspension, private ruling refusal, or third-party agency notice.",
                    "Step 3: Enter decision reference number, date of notification, and issuing URA department/officer.",
                    "Step 4: Articulate specific legal grounds of grievance under the relevant tax statute (TPCA, ITA, or VAT Act).",
                    "Step 5: Attach original URA decision letter, correspondence history, and supporting evidence.",
                    "Step 6: Submit online; the Commissioner reviews and issues an official administrative decision.",
                ],
            )

        # 3. Elect To An Objection Decision
        if any(k in combined for k in ("elect to an objection decision", "elect to objection", "election", "accept objection decision", "appeal to tat", "dissatisfied with objection")):
            return (
                "URA Portal — Objections & Appeals: Elect To An Objection Decision",
                "Formal taxpayer election to accept, settle, or appeal a Commissioner's objection decision.",
                "info",
                [
                    "Step 1: Log in to portal.ura.go.ug > e-Services > Objections and Appeals > Elect To An Objection Decision.",
                    "Step 2: Enter your Objection Reference Number to view the Commissioner General's formal decision.",
                    "Step 3: Choose your statutory election:",
                    "  • Option A: Accept the Objection Decision and generate a payment PRN for any confirmed liability.",
                    "  • Option B: Request Alternative Dispute Resolution (ADR) for structured settlement talks.",
                    "  • Option C: Lodge an appeal with the Tax Appeals Tribunal (TAT) within 30 statutory days under Section 16 TAT Act.",
                    "Step 4: Note: Appealing to TAT requires payment of 30% of the assessed tax or the undisputed portion (whichever is greater) under Section 15 TAT Act.",
                ],
            )

        # 4. Apply To Extend Date To Lodge an Objection
        if any(k in combined for k in ("extend date", "extension of time", "late objection", "apply to extend", "out of time", "extension to lodge")):
            return (
                "URA Portal — Objections & Appeals: Apply To Extend Date To Lodge an Objection",
                "Application for extension of the 45-day statutory objection period under Section 24(2) TPCA.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Objections and Appeals > Apply To Extend Date To Lodge an Objection.",
                    "Step 2: Enter assessment notice reference number and expiration date of the standard 45-day window.",
                    "Step 3: Provide reasonable grounds for delay: serious illness, absence from Uganda, postal/system transmission delays, or other reasonable causes under Section 24(2) TPCA.",
                    "Step 4: Attach corroborating evidence: certified medical records, travel passport stamps, or police reports.",
                    "Step 5: Submit application promptly before collection enforcement actions commence.",
                ],
            )

        # 5. Apply For Waiver of Payment Requirement
        if any(k in combined for k in ("waiver of payment requirement", "payment requirement waiver", "30% waiver", "tat deposit waiver", "pre-deposit waiver")):
            return (
                "URA Portal — Objections & Appeals: Apply For Waiver of Payment Requirement",
                "Request waiver or reduction of the statutory 30% pre-deposit required before appealing to TAT.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Objections and Appeals > Apply For Waiver of Payment Requirement.",
                    "Step 2: Reference your Objection Decision Number and intended Tax Appeals Tribunal (TAT) filing.",
                    "Step 3: Demonstrate extreme financial hardship, risk of business insolvency, or manifest revenue calculation error.",
                    "Step 4: Upload audited financial statements, cash flow projections, and bank statements proving inability to raise the 30% pre-deposit without irreparable damage.",
                    "Step 5: The Commissioner General reviews and determines whether to grant a partial waiver, installment schedule, or bank guarantee alternative.",
                ],
            )

        # 6. Apply For Penalty Reversal
        if any(k in combined for k in ("apply for penalty reversal", "penalty reversal", "reverse penalty", "penal tax reversal", "interest reversal")):
            return (
                "URA Portal — Objections & Appeals: Apply For Penalty Reversal",
                "Application to reverse or waive penal tax, late filing penalties, and statutory interest.",
                "info",
                [
                    "Step 1: Log in to portal.ura.go.ug > e-Services > Objections and Appeals > Apply For Penalty Reversal.",
                    "Step 2: Select the penalty assessment notice reference number and tax type (e.g. Income Tax, VAT, PAYE, WHT).",
                    "Step 3: Specify statutory grounds: URA system transmission downtime, bank integration settlement error, or compliance through Voluntary Disclosure (Section 66 TPCA).",
                    "Step 4: Attach payment proof (PRN bank deposit slips) showing the principal tax was paid, bank error confirmation, or system error logs.",
                    "Step 5: Submit for review; approved reversals credit the taxpayer ledger within 14 working days.",
                ],
            )

        # 7. Alternative Dispute Resolution (ADR) Form
        if any(k in combined for k in ("adr", "alternative dispute resolution", "amicable settlement", "mediation", "adr form", "adr regulations 2023", "conciliation", "section 24(11)")):
            return (
                "URA Portal — Objections & Appeals: Alternative Dispute Resolution (ADR) Form",
                "Application for collaborative, cost-effective mediation and amicable settlement of tax disputes outside court under Section 24(11) TPCA.",
                "info",
                [
                    "Step 1: Statutory Mandate: Governed under Section 24(11) of the Tax Procedures Code Act 2014 and the Tax Procedure Code (Alternative Dispute Resolution Procedure) Regulations 2023.",
                    "Step 2: 7-Day Application Deadline: Taxpayers dissatisfied with a tax decision must apply in writing within seven (7) days after service of the decision using the prescribed ADR Form.",
                    "Step 3: ADR Methods: Disputes are resolved via Conciliation (facilitated by an agreed independent conciliator) or Negotiation.",
                    "Step 4: Grounds for Rejection (15-Day Limit): URA rejects applications within 15 working days if the matter involves statutory law interpretation, public interest, deliberate non-compliance, fraud, or informer cases.",
                    "Step 5: Settlement & Amendment (14-Day Timeline): Executed settlement agreements are legally binding; the Commissioner must amend the tax assessment within fourteen (14) working days to give full effect to the agreement.",
                ],
            )

        # 8. Track Application Status
        if any(k in combined for k in ("track", "application status", "check status", "tracking")):
            return (
                "URA Portal — Objections & Appeals: Track Application Status",
                "Real-time tracking of submitted tax objections, penalty reversals, time extensions, and ADR requests.",
                "info",
                [
                    "Step 1: Visit portal.ura.go.ug > e-Services > Objections and Appeals > Track Application Status (accessible with or without login).",
                    "Step 2: Enter your 10-digit TIN and the Objection Reference Number / Search Number (e.g. OBJ-XXXXXXXX).",
                    "Step 3: Complete the security CAPTCHA code and click Track Status.",
                    "Step 4: Monitor progress stages: 'Objection Lodged' → 'Assigned to Legal / Review Officer' → 'Additional Submissions Pending' → 'Objection Decision Issued' (within the 90-day statutory timeline).",
                    "Step 5: Download the official signed Objection Decision notice in PDF format once finalized.",
                ],
            )

        # Default: Objections and Appeals Overview
        return (
            "URA Portal — Objections and Appeals Overview & Framework",
            "Statutory dispute resolution, objection timelines, and appellate procedures under Section 24 TPCA.",
            "info",
            [
                "Step 1: Go to portal.ura.go.ug > e-Services > Objections and Appeals.",
                "Step 2: Core statutory timeline: You have 45 days from service of assessment notice to lodge a formal written objection.",
                "Step 3: URA is legally required under Section 24(7) TPCA to issue an Objection Decision within 90 days; otherwise the objection is deemed allowed.",
                "Step 4: If dissatisfied with the decision, elect to request ADR or file an appeal to the Tax Appeals Tribunal (TAT) within 30 days.",
                "Step 5: Access services: Object to Assessment, Object to Other Decisions, Apply for Time Extension, Penalty Reversal, ADR, or Track Status.",
            ],
        )

    # Stamp Duty Suite (Cap. 339 & Schedule 2) — 7 Sub-Paths + Overview
    if portal_key == "stamp_duty" or (
        any(
            k in combined
            for k in (
                "stamp duty",
                "transfer of land",
                "property transfer",
                "debenture",
                "mortgage",
                "bulk assessment",
                "stamp certificate",
            )
        )
        and portal_key not in ("tax_incentives", "get_refund", "motor_vehicle", "choose_tax_agent", "objection_appeals", "customs_valuation", "export_process", "laws_and_acts", "double_taxation_agreements", "agriculture_sector", "oil_and_gas", "health_sector", "business_formalisation", "hospitality_sector", "wholesale_retail_sector", "construction_sector", "manufacturing_sector", "education_sector", "mining_sector", "entertainment_sector", "schools_curriculum", "real_estate_sector", "fishing_sector", "transport_sector", "government_agencies", "opportunities_portal", "research_publications")
        and not any(k in combined for k in ("customs valuation", "customs-valuation", "customs bond", "security bond"))
        and "refund" not in combined
    ):
        # 1. Print a Stamp Duty Certificate
        if any(k in combined for k in ("print a stamp duty certificate", "print stamp duty certificate", "print stamp certificate")):
            return (
                "URA Portal — Stamp Duty: Print a Stamp Duty Certificate",
                "Print or retrieve official stamped certificate after paying assessment.",
                "info",
                [
                    "Step 1: Submit original physical instruments to the nearest URA Domestic Taxes office for barcode generation before printing.",
                    "Step 2: Go to portal.ura.go.ug > e-Services > Stamp Duty > Print a Stamp Duty Certificate.",
                    "Step 3: Enter the paid 13-digit Payment Registration Number (PRN) or Assessment Number.",
                    "Step 4: Enter security CAPTCHA and click View / Print Certificate.",
                    "Step 5: The system verifies bank payment clearance and renders the official barcoded e-receipt with digital stamp.",
                    "Step 6: Print or download PDF to submit to the Ministry of Lands or Registration of Titles office.",
                ],
            )

        # 2. Generate Duplicate Stamp Certificate
        if any(k in combined for k in ("duplicate stamp certificate", "generate duplicate", "duplicate stamp", "lost stamp certificate")):
            return (
                "URA Portal — Stamp Duty: Generate Duplicate Stamp Certificate",
                "Reissue an official duplicate stamp certificate for lost or damaged originals.",
                "info",
                [
                    "Step 1: Obtain the Stamp Certificate Number and instrument barcode number from your URA Domestic Taxes office.",
                    "Step 2: Visit portal.ura.go.ug > e-Services > Stamp Duty > Generate Duplicate Stamp Certificate.",
                    "Step 3: Enter the certificate number and barcode number, click Search to generate a payment slip (PRN) for the duplicate fee.",
                    "Step 4: Settle the PRN payment via bank or mobile money.",
                    "Step 5: Return to the portal, select Stamp Certificate issuance, enter the acknowledgement number and barcode number, and print the certified duplicate certificate stamped with the official URA duplicate seal.",
                ],
            )

        # 3. Issue Bulk Stamp Certificates
        if any(k in combined for k in ("issue bulk", "bulk stamp certificate", "bulk certificates", "batch stamp")):
            return (
                "URA Portal — Stamp Duty: Issue Bulk Stamp Certificates",
                "Batch stamping workflow for financial institutions, real estate developers, and legal firms.",
                "info",
                [
                    "Step 1: Access is restricted to registered bulk declarants (commercial banks, microfinance institutions, and insurance companies).",
                    "Step 2: Log in to portal.ura.go.ug > e-Services > Stamp Duty > Issue Bulk Stamp Certificates.",
                    "Step 3: Upload consolidated Excel/CSV schedule of instruments along with non-individual TINs.",
                    "Step 4: Returns must be filed by the 15th day of the month following the month to which the Stamp Duty relates.",
                    "Step 5: Settle the consolidated PRN via bank EFT/RTGS and download individual stamped certificates in a batch ZIP file.",
                ],
            )

        # 4. Login to file stamp duty Returns
        if any(k in combined for k in ("login to file stamp duty returns", "file stamp duty return", "stamp duty return", "stamp duty returns", "etax-login")):
            return (
                "URA Portal — Stamp Duty: Login to File Stamp Duty Returns",
                "Authentication access for registered entities filing statutory monthly stamp duty returns.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug (or ura.go.ug/en/etax-login/) > e-Services > Stamp Duty > Login to file stamp duty Returns.",
                    "Step 2: Log in with your 10-digit TIN and portal password; enter the two-factor authentication OTP.",
                    "Step 3: Navigate to Returns > File a Return > Stamp Duty.",
                    "Step 4: Upload the validated Excel return template capturing all executed instruments for the period.",
                    "Step 5: Submit and obtain the official e-acknowledgement receipt and payment PRN.",
                ],
            )

        # 5. Registration Under Bulk Assessment
        if any(k in combined for k in ("registration under bulk assessment", "bulk assessment registration", "register bulk assessment", "bulk-registration")):
            return (
                "URA Portal — Stamp Duty: Registration Under Bulk Assessment",
                "Onboarding banks, microfinance institutions, and developers for simplified bulk stamping.",
                "info",
                [
                    "Step 1: Visit portal.ura.go.ug > e-Services > Stamp Duty > Registration Under Bulk Assessment (or ura.go.ug/en/bulk-registration/).",
                    "Step 2: Fill in the necessary information required in Section A and Section B.",
                    "Step 3: Submit the online registration application.",
                    "Step 4: Print out the e-acknowledgement receipt and submitted forms.",
                    "Step 5: Physically submit the printed forms to your local URA tax office for approval and profile activation.",
                ],
            )

        # 6. Document Authentication
        if any(k in combined for k in ("document authentication", "authenticate", "verify stamp", "verify certificate", "genuine")):
            return (
                "URA Portal — Stamp Duty: Document Authentication & Verification",
                "Online verification of stamp duty certificates, e-receipts, and property endorsements.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Stamp Duty > Document Authentication (or e-Services > Document Authentication).",
                    "Step 2: Select document type: Stamp Duty Certificate or Assessment Receipt.",
                    "Step 3: Enter the unique Certificate Number or Document Reference Number printed on the document.",
                    "Step 4: Enter the security CAPTCHA code and click Verify Document.",
                    "Step 5: The portal verifies the certificate against official land and stamp duty registries, displaying taxpayer TIN, property details, and stamp duty paid.",
                ],
            )

        # 7. Print TIN Submitted Forms
        if any(k in combined for k in ("print tin submitted forms", "print submitted form", "reprint form", "print form")):
            return (
                "URA Portal — Stamp Duty: Print TIN Submitted Forms",
                "Reprint filed stamp duty assessments, return declarations, and payment vouchers.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Stamp Duty > Print TIN Submitted Forms.",
                    "Step 2: Enter your 10-digit TIN and the Assessment Registration Number / Application Reference.",
                    "Step 3: Select the document to reprint: Stamp Duty assessment slip, property declaration, or return summary.",
                    "Step 4: Enter CAPTCHA characters and click Download PDF.",
                    "Step 5: Print the official document bearing the URA security barcode for submission to the land registry.",
                ],
            )

        # Default: Stamp Duty Assessment & Property Clearance
        return (
            "URA Portal — Stamp Duty Assessment & Property Clearance",
            "Assessment and payment of statutory stamp duty on legal and property instruments.",
            "info",
            [
                "Step 1: Go to portal.ura.go.ug > e-Services > Stamp Duty > Generate Assessment.",
                "Step 2: Select instrument type: Land/Property Transfer (1.5%), Commercial Lease (1%), Mortgage/Debenture (0.5%), or Flat Rate (UGX 15,000 for affidavits).",
                "Step 3: For property transfers: Enter plot number, block, district, and consideration amount; Chief Government Valuer confirms open market value.",
                "Step 4: Generate PRN and pay stamp duty through any commercial bank or mobile money.",
                "Step 5: Download the certified stamped e-receipt and take it to the Ministry of Lands (NLIS) to finalize title registration.",
            ],
        )

    # Tax Clearance (TCC) Suite — 3 Sub-Paths + Overview
    if portal_key == "tax_clearance" or (
        any(k in combined for k in ("tax clearance", "ax clearance", "clearance certificate", "tcc", "apply for tcc"))
        and portal_key not in ("tax_incentives", "get_refund", "motor_vehicle", "choose_tax_agent", "objection_appeals", "stamp_duty", "laws_and_acts", "double_taxation_agreements", "business_formalisation", "health_sector")
        and "refund" not in combined
    ):
        # 1. Track Application Status
        if any(k in combined for k in ("track", "application status", "check status", "tracking")):
            return (
                "URA Portal — Tax Clearance: Track Application Status",
                "Real-time tracking of submitted Tax Clearance Certificate (TCC) applications.",
                "info",
                [
                    "Step 1: Visit portal.ura.go.ug > Tax Clearance > Track Application Status.",
                    "Step 2: Enter your 10-digit TIN and the TCC Application Search Number / Reference Number.",
                    "Step 3: Enter security CAPTCHA and click Track Status.",
                    "Step 4: View real-time stage: 'Application Lodged' → 'Ledger Audit Review' → 'Officer Approval' → 'TCC Generated'.",
                    "Step 5: If approved, click Download PDF to retrieve your valid Tax Clearance Certificate with verification QR code.",
                ],
            )

        # 2. Document Authentication
        if any(k in combined for k in ("document authentication", "authenticate", "verify tcc", "verify certificate", "genuine")):
            return (
                "URA Portal — Tax Clearance: Document Authentication & Verification",
                "Verify the authenticity and validity of a URA Tax Clearance Certificate (TCC).",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > Tax Clearance > Document Authentication (or e-Services > Document Authentication).",
                    "Step 2: Select document type: Tax Clearance Certificate (TCC).",
                    "Step 3: Enter the unique TCC Certificate Number (e.g. TCC-XXXXXXXX) printed on the certificate.",
                    "Step 4: Enter CAPTCHA and click Verify Document.",
                    "Step 5: The portal verifies the certificate against live compliance databases, displaying taxpayer legal name, TIN, issue date, validity period, and clearance purpose.",
                ],
            )

        # 3. Requirements Check
        if any(k in combined for k in ("require", "needed", "what do i need", "what is required")):
            return (
                "URA Portal — Tax Clearance Certificate (TCC) Requirements",
                "Mandatory statutory compliance criteria to receive a valid URA TCC.",
                "info",
                [
                    "Requirement 1: 100% filing compliance — all registered tax returns (VAT, PAYE, Income Tax, WHT) must be filed up-to-date.",
                    "Requirement 2: Zero unpaid tax liabilities, or an active, approved instalment payment agreement.",
                    "Requirement 3: Stated purpose for TCC (e.g. government procurement bidding, work permit, license renewal, or vehicle transfer).",
                    "Turnaround: Electronic TCC is generated automatically within 24 to 48 hours for compliant taxpayers.",
                    "Step-by-step: Log in to portal.ura.go.ug > Tax Clearance > Apply for Tax Clearance Certificate > select purpose > submit.",
                ],
            )

        # Default: Apply for Tax Clearance Certificate
        return (
            "URA Portal — Tax Clearance Certificate (TCC) Application",
            "Automated application workflow for official URA Tax Clearance Certificates.",
            "info",
            [
                "Step 1: Log in to your e-Tax portal account at portal.ura.go.ug with your 10-digit TIN and password.",
                "Step 2: Navigate to Tax Clearance > Apply for Tax Clearance Certificate.",
                "Step 3: Select the specific purpose: Public Procurement Tender, Immigration/Work Permit, License Renewal, or Vehicle Transfer.",
                "Step 4: Review your compliance status ledger; ensure no missing returns or unpaid assessments are outstanding.",
                "Step 5: Submit application; compliant certificates are generated electronically within 24 to 48 hours.",
                "Step 6: Download and print the authentic TCC PDF complete with verification barcode.",
            ],
        )

    # The Export Process Suite (https://ura.go.ug/en/category/imports-exports/export-process/) — 14 Sub-Paths + Overview
    if portal_key == "export_process" or (
        any(
            k in combined
            for k in (
                "the export process",
                "faqs for the export process",
                "export process",
                "exporting goods",
                "export procedures",
                "how to export",
                "how to start exporting",
                "rex system",
                "prohibited exports",
                "restricted exports",
                "main export products",
                "the-exports-process",
                "one stop border post",
                "transit cargo",
                "export facilitation",
            )
        )
        and portal_key not in ("tax_incentives", "get_refund", "motor_vehicle", "choose_tax_agent", "objection_appeals", "stamp_duty", "tax_clearance", "customs_valuation", "single_customs_territory", "exempt_importation", "aeo", "customs_audits_refunds", "warehousing", "customs_enforcements", "laws_and_acts", "double_taxation_agreements")
        and "refund" not in combined
    ):
        # 1. Main Export Products
        if any(k in combined for k in ("main export products", "main exports", "what does uganda export")):
            return (
                "URA Portal — The Export Process: Main Export Products",
                "Uganda's primary export commodities, international markets, and destination compliance.",
                "info",
                [
                    "Step 1: Uganda's top export products include Coffee (Arabica and Robusta), Cement, Tea, Fish and fish products (Nile Perch, Tilapia), Oilseeds, Flowers, Fresh Fruits, Vegetables, and Processed Foods.",
                    "Step 2: Non-traditional exports experiencing rapid growth: Dairy products, iron and steel manufactures, plastics, and confectioneries.",
                    "Step 3: Check commodity specific export guidelines from respective statutory promotion boards (UCDA for coffee, UEPB for general commodities).",
                    "Step 4: Ensure items meet destination tariff classification under the EAC Common External Tariff (CET).",
                ],
            )

        # 2. Document Authentication
        if any(k in combined for k in ("document authentication", "authenticate", "verify export document", "verify origin certificate", "verify eur.1")):
            return (
                "URA Portal — The Export Process: Document Authentication & Verification",
                "Official verification of export declarations, origin certificates, and customs release orders.",
                "info",
                [
                    "Step 1: Go to ura.go.ug or portal.ura.go.ug > e-Services > Document Authentication.",
                    "Step 2: Select document category: Certificate of Origin (EAC, COMESA, GSP), Phytosanitary Certificate, or Customs Export Release Order.",
                    "Step 3: Enter the unique Certificate / Document Reference Number printed on the document.",
                    "Step 4: Enter the security CAPTCHA code and click 'Verify Document'.",
                    "Step 5: The portal validates the record against live URA customs and single window registers, displaying exporter TIN, destination country, product description, and issuance status.",
                ],
            )

        # 3. One Stop Border Post (OSBP)
        if any(k in combined for k in ("one stop border post", "osbp", "joint border", "busia", "malaba", "katuna", "mirama hills", "mutukula", "elegu", "mpondwe", "goli")):
            return (
                "URA Portal — The Export Process: One Stop Border Post (OSBP)",
                "Coordinated border clearance operations between Uganda and neighbouring East African partner states.",
                "info",
                [
                    "Step 1: Understand OSBP operation: Border agencies of Uganda and the neighbouring country operate under one roof, eliminating duplicate border stops.",
                    "Step 2: Key operational OSBPs: Busia & Malaba (Uganda–Kenya), Katuna & Mirama Hills (Uganda–Rwanda), Elegu (Uganda–South Sudan), Mutukula (Uganda–Tanzania), Mpondwe & Goli (Uganda–DRC).",
                    "Step 3: Clearance time reduced from 24 hours to under 4 hours via integrated joint verification and electronic data interchange.",
                    "Step 4: Simplified Trade Regime (STR): Cross-border small-scale traders moving goods worth under USD 2,000 use simplified customs documentation without needing clearing agents.",
                    "Step 5: Access Trade Information Desks (TIDs) at border points for real-time customs guidance and women cross-border trade facilitation.",
                ],
            )

        # 4. Import & Export Facilitation
        if any(k in combined for k in ("import & export facilitation", "import and export facilitation", "trade facilitation", "aeo facilitation", "green channel")):
            return (
                "URA Portal — The Export Process: Import & Export Facilitation",
                "Trade facilitation initiatives, fast-track routing, and Authorized Economic Operator privileges.",
                "info",
                [
                    "Step 1: Leverage Authorized Economic Operator (AEO) status: Compliant exporters enjoy green channel priority routing, zero physical inspection at border points, and expedited clearance.",
                    "Step 2: Utilize the Uganda Electronic Single Window (UESW) to submit all trade declarations and obtain cross-agency permits in a single electronic transaction.",
                    "Step 3: Pre-Clearance Facility: Exporters and agents can lodge and process export declarations before cargo arrives at border posts.",
                    "Step 4: Access dedicated URA Client Relationship Managers for large volume exporters.",
                    "Step 5: Benefit from coordinated border inspections involving URA, UNBS, MAAIF, and Police under the integrated border management framework.",
                ],
            )

        # 5. Management of Transit Cargo
        if any(k in combined for k in ("management of transit cargo", "transit cargo", "rects", "electronic cargo tracking", "transit bond", "cargo movement document", "c2")):
            return (
                "URA Portal — The Export Process: Management of Transit Cargo",
                "Satellite tracking, security seals, and border control for goods transiting through Uganda.",
                "info",
                [
                    "Step 1: Transit cargo moving through Uganda is monitored via the Regional Electronic Cargo Tracking System (RECTS) using tamper-proof satellite e-seals.",
                    "Step 2: Carrier obtains a Cargo Movement Document (Form C2 for intra-EAC or C63 road transit document from coastal ports like Mombasa/Dar es Salaam).",
                    "Step 3: Trucks must adhere strictly to designated geo-fenced transit corridors; deviation alerts trigger rapid response customs patrol intervention.",
                    "Step 4: At the exit border station (OSBP), the proper officer inspects e-seals, performs electronic exit in Asycuda, and automatically discharges the transit bond.",
                    "Step 5: Re-export of transit or warehoused goods must occur within the statutory 30-day window or approved extension.",
                ],
            )

        # 6. How to start exporting
        if any(k in combined for k in ("how to start exporting", "start exporting", "getting started with export", "become an exporter", "start export business")):
            return (
                "URA Portal — The Export Process: How to Start Exporting",
                "Essential registration steps, licenses, and business readiness to export from Uganda.",
                "info",
                [
                    "Step 1: Legal Registration: Register your business entity or company with the Uganda Registration Services Bureau (URSB).",
                    "Step 2: Tax Registration: Obtain an active 10-digit Taxpayer Identification Number (TIN) from URA with customs exporter profile enabled.",
                    "Step 3: Product Compliance: Contact the Uganda Export Promotion Board (UEPB) to register and ensure packaging labels state 'Produced in Uganda for Export'.",
                    "Step 4: Standards Certification: Secure requisite quality certification from UNBS and health/phytosanitary permits from MAAIF.",
                    "Step 5: Clearing Logistics: Partner with a licensed customs clearing agent to configure your profile on the Uganda Electronic Single Window (UESW) and Asycuda World.",
                ],
            )

        # 7. Tax Incentives Under the Export Process
        if any(k in combined for k in ("tax incentives under the export process", "export tax incentives", "duty drawback", "vat zero-rating", "export incentives", "zero-rated")):
            return (
                "URA Portal — The Export Process: Tax Incentives Under the Export Process",
                "Statutory tax benefits, input credit refunds, and duty remission for Ugandan exporters.",
                "info",
                [
                    "Step 1: VAT Zero-Rating (0%): Under Section 24 of the VAT Act, export supplies are zero-rated, entitling exporters to claim a refund of all input VAT incurred on raw materials and production.",
                    "Step 2: Duty Drawback Scheme: Under Section 138 EACCMA, exporters who import raw materials used in manufacturing goods for export claim a 100% refund of customs import duties.",
                    "Step 3: 10-Year Income Tax Holiday: Commercial agro-processors and manufacturers in Industrial Parks / Free Zones exporting 80%+ of finished goods enjoy a 10-year income tax holiday (Section 21 ITA).",
                    "Step 4: Zero Export Duty: Virtually all Ugandan manufactured and processed products are completely exempt from export taxes and customs levies.",
                    "Step 5: Apply for drawback and VAT refund disbursements via portal.ura.go.ug > e-Services > Tax Refund.",
                ],
            )

        # 8. Key Export Markets for Ugandan Products
        if any(k in combined for k in ("key export markets", "export markets for ugandan products", "top export destinations", "export destinations")):
            return (
                "URA Portal — The Export Process: Key Export Markets for Ugandan Products",
                "Regional and international market destinations for Ugandan manufactured and agricultural goods.",
                "info",
                [
                    "Step 1: Regional EAC Market: Kenya, South Sudan, Democratic Republic of Congo (DRC), Rwanda, Tanzania, and Burundi (duty-free access under EAC Customs Union).",
                    "Step 2: Continental COMESA & AfCFTA Markets: Broader African markets under the African Continental Free Trade Area.",
                    "Step 3: European Union Market: Duty-free, quota-free market access under the Everything But Arms (EBA) / Generalized System of Preferences (GSP) using the REX system.",
                    "Step 4: United States Market: Preferential tariff access under the African Growth and Opportunity Act (AGOA).",
                    "Step 5: Middle East & Asia: Growing markets for coffee, tea, fruits, spices, and processed food products in UAE, China, and India.",
                ],
            )

        # 9. Key Documents Required to Export
        if any(k in combined for k in ("key documents required to export", "documents required to export", "export documentation", "what documents are required to export")):
            return (
                "URA Portal — The Export Process: Key Documents Required to Export",
                "Mandatory shipping, commercial, and regulatory documentation for export customs clearance.",
                "info",
                [
                    "Step 1: Commercial Invoice: Exporter's invoice detailing product description, HS code, quantity, unit price, FOB value, and incoterms.",
                    "Step 2: Packing List: Specifying carton numbers, net weight, gross weight, and package dimensions.",
                    "Step 3: Transport Document: Bill of Lading (ocean freight), Air Waybill (air cargo), or Road Consignment Note / CMR.",
                    "Step 4: Certificate of Origin: REX invoice statement, EAC Certificate, COMESA Certificate, or AGOA visa.",
                    "Step 5: Quality / Phytosanitary Certificates: MAAIF phytosanitary certificate for plants, veterinary certificate for animal products, or UNBS conformity certificate.",
                    "Step 6: Customs Declaration (SAD): Electronic Single Administrative Document lodged in Asycuda World.",
                ],
            )

        # 10. Export Procedures and Customs Requirements
        if any(k in combined for k in ("export procedures and customs requirements", "export procedures", "customs clearance requirements", "customs requirements")):
            return (
                "URA Portal — The Export Process: Export Procedures & Customs Requirements",
                "Operational customs clearance steps, electronic declaration, and physical verification at border stations.",
                "info",
                [
                    "Step 1: Electronic Entry: Appoint a licensed customs clearing agent to lodge the Single Administrative Document (SAD) into Asycuda World via the Single Window.",
                    "Step 2: Assessment & Payment: Settle any applicable administrative fees or statutory commodity levies via generated PRN.",
                    "Step 3: Selectivity & Inspection: Asycuda routes cargo to Green (direct release), Yellow (document audit), or Red (physical inspection / non-intrusive scanner examination).",
                    "Step 4: Customs Release: Once satisfied, the proper customs officer issues an electronic Release Order and Cargo Movement Document C2.",
                    "Step 5: Border Exit: Cargo arrives at the One Stop Border Post (OSBP), customs confirms physical departure, and the exit note is generated.",
                ],
            )

        # 11. Payment Methods Used in International Trade
        if any(k in combined for k in ("payment methods used in international trade", "payment methods", "international trade payments", "letters of credit", "open account")):
            return (
                "URA Portal — The Export Process: Payment Methods Used in International Trade",
                "International commercial settlement mechanisms, financial instruments, and trade risk management.",
                "info",
                [
                    "Step 1: Letters of Credit (LCs): Irrevocable bank guarantee ensuring exporter receives payment upon presenting compliant shipping documents; lowest commercial risk.",
                    "Step 2: Cash in Advance / Advance Payment: Importer wires funds before shipment; recommended for new buyers or custom-manufactured goods.",
                    "Step 3: Documentary Collections (D/P and D/A): Banks act as intermediaries holding shipping documents until payment (Documents Against Payment) or acceptance of bill of exchange (Documents Against Acceptance).",
                    "Step 4: Open Account: Exporter ships goods and bills buyer with 30–90 days credit; best suited for well-established, trusted relationships.",
                    "Step 5: Consignment: Payment made only after goods are sold by the overseas distributor; highest risk for the exporter.",
                ],
            )

        # 12. Quality Standards / Certifications Required
        if any(k in combined for k in ("quality standards / certifications required", "quality standards", "certifications required", "unbs certification", "maaif certificate", "pvoc")):
            return (
                "URA Portal — The Export Process: Quality Standards & Certifications Required",
                "Regulatory quality assurance, laboratory testing, and sanitary/phytosanitary compliance for export.",
                "info",
                [
                    "Step 1: Ministry of Agriculture, Animal Industry and Fisheries (MAAIF): Mandatory inspection and Phytosanitary Certificates for horticultural produce, grain, and live plants.",
                    "Step 2: Uganda National Bureau of Standards (UNBS): Pre-export verification of conformity, product quality testing, and laboratory certification.",
                    "Step 3: Uganda Coffee Development Authority (UCDA): Coffee export quality certification, moisture testing, and cup grading.",
                    "Step 4: Directorate of Fisheries Resources (DFR): Health and sanitary certificates for fish and aquaculture products.",
                    "Step 5: Buyer / Destination Country Standards: Comply with destination sanitary requirements (e.g. EU maximum residue limits, US FDA rules).",
                ],
            )

        # 13. Logistics and Shipping Arrangements
        if any(k in combined for k in ("logistics and shipping arrangements", "logistics and shipping", "shipping arrangements", "freight forwarders", "multimodal transport")):
            return (
                "URA Portal — The Export Process: Logistics and Shipping Arrangements",
                "Freight transport modes, shipping documentation, and international transit logistics.",
                "info",
                [
                    "Step 1: Freight Forwarder Selection: Partner with experienced, licensed freight forwarders and members of the Uganda Clearing Industry and Forwarding Association (UCIFA).",
                    "Step 2: Mode of Transport: Choose maritime sea freight via Mombasa or Dar es Salaam for bulk goods, air freight via Entebbe International Airport for perishables, or road freight for regional EAC destinations.",
                    "Step 3: Incoterms Selection: Agree on standard ICC Incoterms (e.g. FOB Mombasa, CIF Rotterdam, EXW Kampala, FCA Entebbe) to define freight cost and risk transfer.",
                    "Step 4: Cargo Insurance: Secure Marine / Cargo Insurance coverage covering goods from factory floor to overseas destination.",
                    "Step 5: Regional Electronic Cargo Tracking (RECTS): For bonded transits, ensure electronic cargo tracking seals are installed to avoid en-route delays.",
                ],
            )

        # 14. Financial Assistance Options
        if any(k in combined for k in ("financial assistance options", "financial assistance", "export financing", "udb financing", "export credit")):
            return (
                "URA Portal — The Export Process: Financial Assistance Options",
                "Government credit facilities, export working capital loans, and trade development financing.",
                "info",
                [
                    "Step 1: Uganda Development Bank (UDB): Access low-interest, long-term development financing and export working capital loans for agro-processors and manufacturers.",
                    "Step 2: Commercial Bank Trade Finance: Utilize invoice discounting, packing credit loans, and export receivables financing facilities.",
                    "Step 3: Export Credit Guarantees: Explore African Export-Import Bank (Afreximbank) and East African Development Bank (EADB) credit lines and risk mitigation instruments.",
                    "Step 4: Agricultural Credit Facility (ACF): Bank of Uganda supported concessional loans for agricultural processing and value-addition equipment.",
                    "Step 5: Trade Hub Grants: Access matching grants and technical assistance from development partners like Trademark Africa and USAID Feed the Future.",
                ],
            )

        # Default: The Export Process Overview
        return (
            "URA Portal — The Export Process Overview & Guidelines",
            "Official guidance for exporters on trade documentation, incentives, customs clearance, and statutory regulations.",
            "info",
            [
                "Step 1: Visit ura.go.ug/en/category/imports-exports/export-process/ for the comprehensive guide to exporting from Uganda.",
                "Step 2: Ensure business registration with URSB, active TIN with URA, and appropriate export permits.",
                "Step 3: Engage a certified customs clearing agent to lodge declarations in Asycuda World via the Electronic Single Window.",
                "Step 4: Take advantage of export tax benefits: 0% VAT zero-rating, duty drawback, and zero customs duty on manufactured goods.",
                "Step 5: Utilize One Stop Border Posts (OSBP) and Regional Electronic Cargo Tracking for swift clearance.",
            ],
        )

    # Customs Valuation Suite (https://ura.go.ug/en/category/imports-exports/customs-valuation/) — 5 Sub-Paths + Overview
    if portal_key == "customs_valuation" or (
        any(
            k in combined
            for k in (
                "customs valuation",
                "methods of customs valuation",
                "security bond",
                "security bonds",
                "customs bond",
                "clearing agent",
                "clearing agents",
                "motor vehicle value guide",
                "general goods database",
                "customs-valuation",
            )
        )
        and portal_key not in ("tax_incentives", "get_refund", "motor_vehicle", "choose_tax_agent", "objection_appeals", "stamp_duty", "tax_clearance", "export_process", "single_customs_territory", "exempt_importation", "aeo", "customs_audits_refunds", "warehousing", "customs_enforcements", "laws_and_acts", "double_taxation_agreements")
        and not any(k in combined for k in ("single customs territory", "sct"))
        and "refund" not in combined
    ):
        # 1. Customs Value & International Rules (WTO ACV)
        if any(k in combined for k in ("what is customs value", "international rules", "rules for the determination of the customs value", "customs duties", "ad valorem", "acv rules")):
            return (
                "URA Portal — Customs Valuation: Customs Value & International Rules (WTO ACV)",
                "Definition of Customs Value, ad valorem vs. specific duties, and WTO ACV international rules.",
                "info",
                [
                    "Step 1: Customs Value Definition: The value of imported goods determined for the purpose of levying ad valorem customs duties (e.g. import duty 25% of Customs CIF value).",
                    "Step 2: Specific Duties: Duties, taxes, or fees levied based on specific measures of goods such as number, weight, volume, or capacity (e.g. UGX 1,000 per litre of fuel), regardless of monetary value.",
                    "Step 3: International Legal Framework: Governed by the WTO Agreement on Customs Valuation (ACV), adopted in 1994, which replaced the 1953 Brussels Definition of Value (BDV).",
                    "Step 4: Domestic Law: Domesticated in Section 122 and the Fourth Schedule of the East African Community Customs Management Act 2004 (EACCMA).",
                    "Step 5: Reference Text: The full ACV text is published in the World Customs Organization (WCO) Compendium on Customs Valuation and the WTO legal database.",
                ],
            )

        # 2. Methods of Customs Valuation
        if any(k in combined for k in ("methods of customs valuation", "what are the different methods", "different methods of customs valuation", "valuation methods", "methods of valuation", "acv methods", "transaction value method", "deductive value", "computed value", "fallback method")):
            return (
                "URA Portal — Customs Valuation: Methods of Customs Valuation (WTO ACV)",
                "The six sequential valuation methods under WTO ACV and EACCMA Fourth Schedule.",
                "info",
                [
                    "Step 1: Understand legal hierarchy: Section 122 and the Fourth Schedule of the East African Community Customs Management Act (EACCMA 2004) mandate 6 sequential valuation methods.",
                    "Step 2: Method 1 (Transaction Value): Primary method based on price actually paid or payable for goods sold for export to Uganda, adjusted for freight, insurance, assists, royalties, and packaging.",
                    "Step 3: Comparative Methods (Methods 2 & 3): Transaction value of identical goods or similar goods exported to Uganda at or about the same time, at the same commercial level and substantially same quantity.",
                    "Step 4: Alternative Methods (Methods 4 & 5): Deductive Value (local resale unit price minus domestic margins) and Computed Value (production material costs + general expenses + profit). Importers can request to reverse the order of Methods 4 and 5.",
                    "Step 5: Method 6 (Fallback Method): Value determined using reasonable means consistent with Article VII GATT / WTO ACV, flexibly applying preceding methods using data available in Uganda (e.g. Revised General Goods Database).",
                ],
            )

        # 2. Customs Legal and Security bonds
        if any(k in combined for k in ("customs legal and security bonds", "what is a customs security bond", "customs security bond", "security bond", "security bonds", "customs bond", "bond execution", "process of bond execution", "why is a bond executed", "execute a bond", "required to execute a bond", "types of bonds", "eac bond")):
            return (
                "URA Portal — Customs Valuation: Customs Legal & Security Bonds",
                "Guarantees executed with Customs and backed by approved insurance/bank institutions to protect revenue.",
                "info",
                [
                    "Step 1: Understand purpose: Customs Security Bonds protect government revenue on transactions where duties are suspended (e.g. transit cargo, bonded warehouses, temporary imports, inward processing).",
                    "Step 2: Types of Bonds: Transit Bond (Form CB1 for goods crossing Uganda), Bonded Warehouse Bond (Form CB2 under Section 62 EACCMA), Temporary Importation Bond, and East African Community Bond (EAC Bond under Section 107 EACCMA rolled out Feb 11, 2026).",
                    "Step 3: Bond Execution Process: Importer/agent applies online via Asycuda World, submits insurance bond guarantee or bank bond, and attaches company registration documents.",
                    "Step 4: Verification & Approval: URA Customs Bond Management Unit reviews solvency, validates underwriter limits, and activates the bond line in Asycuda.",
                    "Step 5: Bond Discharge & Cancellation: Once goods exit the country, are transferred to another approved regime, or duties are fully paid, the bond is automatically discharged.",
                ],
            )

        # 3. Clearing Agents
        if any(k in combined for k in ("clearing agents", "clearing agent", "customs clearing agent", "licensing of a company", "clearing agency license", "customs agency license 2026")):
            return (
                "URA Portal — Customs Valuation: Clearing Agents Licensing & Compliance",
                "Statutory requirements, licensing conditions, and operational standards for customs clearing agents.",
                "info",
                [
                    "Step 1: Legal Requirement: Under Section 145(1) of the EACCMA 2004 and Regulations 149–152 EACCMR 2010, only licensed customs agents may lodge cargo declarations in Asycuda World.",
                    "Step 2: Licensing Pre-requisites: Established physical office with computerized systems connected to the Uganda Electronic Single Window (UESW); valid Tax Clearance Certificate (TCC); membership with UCIFA.",
                    "Step 3: Professional Qualification: At least two full-time technical staff must possess the East African Customs Clearing and Freight Forwarding Practicing Certificate (EACFFPC).",
                    "Step 4: Application & Renewal: Submit annual license application via portal.ura.go.ug pursuant to annual public notices (e.g. Customs Agency License 2026 notices issued Aug 22, 2025 and Addendum Sept 19, 2025).",
                    "Step 5: Execute and maintain a mandatory Customs Agents Security Bond to cover potential revenue liabilities.",
                ],
            )

        # 4. Motor vehicle Value Guide
        if any(k in combined for k in ("motor vehicle value guide", "vehicle value guide", "used motor vehicle valuation database", "motor vehicle valuation guide", "motor vehicle database", "motor vehicle valuation guides", "used vehicle value", "disclaimer", "valuation guide")):
            return (
                "URA Portal — Customs Valuation: Motor Vehicle Value Guide",
                "Statutory baseline CIF database used by the Document Processing Centre (DPC) for used vehicle customs valuation.",
                "info",
                [
                    "Step 1: Download Official PDF Guides: Access the Motor Vehicle Valuation Guides archive at ura.go.ug/download-category/motor-vehicle-valuation-guides/ (updated As at 01st August 2026 by Isaac Kamya and As at 01st September 2026 by Charles Male).",
                    "Step 2: Table Specifications: Database columns detail S/N, HS Code, Country of Origin (COO), Description (Make, Model, Chassis Model, Year of Manufacture), Engine CC, and baseline CIF (USD).",
                    "Step 3: Tax Assessment Structure: From the determined CIF value, taxes are assessed as: 25% Import Duty; 18% VAT on (CIF + Duty); 6% Withholding Tax; 35% Environmental Levy (5–8 yrs old) or 50% (>8 yrs old); and 1.5% Infrastructure Levy.",
                    "Step 4: Official Statutory Disclaimer: Published values serve as predetermined baseline reference values under Section 122(5) EACCMA and Method 6 Fallback. DPC reserves the statutory right under Article 17 WTO ACV to adjust values where physical examination, auction sheet, mileage, or optional features demonstrate superior condition.",
                    "Step 5: Unlisted / Modified Vehicles: For unlisted or customized models, submit original export auction sheets and purchase invoices to the DPC Motor Vehicle Unit at Nakawa Inland Port for individualized valuation appraisal.",
                ],
            )

        # 5. Revised General Goods Database
        if any(k in combined for k in ("revised general goods database", "general goods database", "revised-general-goods-database-5", "goods valuation database", "reference prices", "dpc database", "tsc", "uom")):
            return (
                "URA Portal — Customs Valuation: Revised General Goods Database",
                "Reference pricing database and risk profile catalogue for commercial import valuation.",
                "info",
                [
                    "Step 1: Access Database Schedule: Visit ura.go.ug/en/revised-general-goods-database-5/ (published As at 01st September 2026).",
                    "Step 2: Table Columns: The schedule contains standardized fields: S/N, HS CODE (8-digit tariff code), TSC (Tariff Specification Code), BRAND, COMMERCIAL DESCRIPTION, COO (Country of Origin), UOM (Unit of Measure), CURRENCY (USD), CUSTOMS VALUE, and PRODUCT.",
                    "Step 3: Risk Targeting: When an importer's declared invoice price is significantly below the database benchmark, Asycuda routes the declaration to the Document Processing Centre (DPC) at Nakawa.",
                    "Step 4: Valuation Defense: The importer has the statutory right under Article 11 ACV to submit supporting financial evidence (TT reports, swift copies, sales contracts) within 15 days.",
                    "Step 5: Valuation Determination: If the declared value is not substantiated with verifiable payment records, DPC applies sequential valuation using the database reference values under Method 6 (Fallback).",
                ],
            )

        # Default: Customs Valuation Overview & Asycuda NII Requirements
        return (
            "URA Portal — Customs Valuation Overview & Asycuda NII Requirements",
            "Statutory customs valuation rules, WTO ACV adherence, and Non-Intrusive Inspection (NII) validation updates.",
            "info",
            [
                "Step 1: Visit ura.go.ug/en/category/imports-exports/customs-valuation/ for official valuation rules, guides, and public notices.",
                "Step 2: Adherence to Section 122 EACCMA: Duties are levied on an ad valorem basis (CIF for sea/road imports, Cost + Insurance for air freight into Entebbe).",
                "Step 3: Note the New Asycuda Validation Requirements for Vehicle Plate and Container Numbers implemented September 7, 2026 under the Non-Intrusive Inspection (NII) Upgrade Project.",
                "Step 4: Understand the 6 valuation methods applied sequentially by the Document Processing Centre (DPC) at Nakawa Inland Port.",
                "Step 5: For disputes, lodge an administrative objection or appeal via DPC Supervisor → Manager DPC → Commissioner Customs.",
            ],
        )

    # Single Customs Territory (SCT) Suite (https://ura.go.ug/en/category/imports-exports/single-customs-territory/) — 8 Sub-Paths + Overview
    if portal_key == "single_customs_territory" or (
        any(
            k in combined
            for k in (
                "single customs territory",
                "sct",
                "first point of entry",
                "mombasa port",
                "dar es salaam port",
                "mutual recognition of customs clearing agents",
                "third-party declarant",
                "third party declarant",
                "rects free of charge",
                "sct business process manual",
                "c9/ c11",
                "c9/c11",
                "manifest splitting",
            )
        )
        and portal_key not in ("tax_incentives", "get_refund", "motor_vehicle", "choose_tax_agent", "objection_appeals", "stamp_duty", "tax_clearance", "export_process", "customs_valuation", "exempt_importation", "aeo", "customs_audits_refunds", "warehousing", "customs_enforcements", "laws_and_acts", "double_taxation_agreements", "customs_systems")
        and "refund" not in combined
    ):
        # 1. Fuel Handling under SCT (10-Day Rule)
        if any(k in combined for k in ("fuel handled under sct", "how is fuel handled under sct", "sct fuel", "fuel product", "10 days to make a customs declaration")):
            return (
                "URA Portal — Single Customs Territory (SCT): Fuel Handling & 10-Day Rule",
                "Mandatory 10-day entry declaration and customs clearance protocol for fuel cargo under SCT.",
                "info",
                [
                    "Step 1: Mandatory Entry Deadline: Under SCT regulations (Oketch, August 25, 2023), fuel importers have a strict statutory window of ten (10) days to lodge a Customs declaration in Asycuda after fuel is discharged from the vessel.",
                    "Step 2: Penalties for Delay: Failing to lodge the declaration within 10 days of discharge triggers statutory late filing penalties and compounding demurrage fees.",
                    "Step 3: Metering & Bonded Storage: Fuel is discharged into KPC (Kenya Pipeline) or TPA bonded storage installations under joint supervision of URA and host customs officers.",
                    "Step 4: Duty Settlement & Transit: Once taxes are paid into the URA account via PRN, fuel tankers load from the pipeline depot and move under electronic cargo tracking seals to Uganda.",
                ],
            )

        # 2. Contraband Splitting & Manifest Amendments (C9/C11)
        if any(k in combined for k in ("contraband", "same bill of lading", "c9/ c11", "c9/c11", "c9", "c11", "manifest splitting", "manifest corrector")):
            return (
                "URA Portal — Single Customs Territory (SCT): Manifest Splitting & C9/C11 Amendments",
                "Procedure when multiple vehicles or goods share a bill of lading but one item is contraband.",
                "info",
                [
                    "Step 1: Contraband Detection: If an importer imports multiple motor vehicles or goods on the same bill of lading and one is identified as contraband (e.g. prohibited goods or over-age), the entire consignment is placed on hold.",
                    "Step 2: Manifest Splitting Application: The importer applies through their shipping line or shipping line agent to split the manifest to isolate the contraband vehicle/cargo.",
                    "Step 3: Form C9 / C11 Lodgement: The shipping line lodges a formal amendment request (Form C9/C11) supported by a Manifest Corrector letter from the shipper in the country of origin.",
                    "Step 4: Release of Compliant Goods: Upon approval by URA Customs, the compliant motor vehicles/goods proceed for standard clearance, while the contraband item is seized, auctioned, or re-exported.",
                ],
            )

        # 3. Consignment Accidents, Loss & Unforeseen Incidents
        if any(k in combined for k in ("consignment does not arrive", "does not arrive in the country", "accidents", "thefts", "fire", "transit accident", "lost along corridor")):
            return (
                "URA Portal — Single Customs Territory (SCT): Cargo Incident & Loss Procedures",
                "Statutory reporting workflow when SCT cargo encounters accidents, theft, or fire along transit corridors.",
                "info",
                [
                    "Step 1: Immediate Police Reporting: Driver or transporter must immediately obtain an Incident and Scene of Crime Report from local Police authorities where the incident occurred.",
                    "Step 2: Host Revenue Notification: Promptly report to the Revenue Authority of the Partner State (e.g. KRA in Kenya, TRA in Tanzania) for official physical site examination.",
                    "Step 3: URA Customs Alert: Notify the URA Transit Monitoring Unit (TMU) in Kampala and present police reports and photos to pause enforcement action against the transit bond.",
                    "Step 4: Insurance Survey: Engage accredited insurance loss adjusters to certify the destroyed or stolen cargo and prepare an official survey report.",
                    "Step 5: Bond Reconciliation: Present police abstracts and customs survey documentation to discharge or adjust the Regional Customs Transit Guarantee (RCTG).",
                ],
            )

        # 4. Mutual Recognition of Customs Clearing Agents & Third-Party Declarants
        if any(k in combined for k in ("mutual recognition", "mutual recognition of customs clearing agents", "third-party declarant", "third party declarant", "forwarder", "kpa processes", "tpa processes")):
            return (
                "URA Portal — Single Customs Territory (SCT): Mutual Recognition & Declarants",
                "Cross-border recognition of licensed customs agents and third-party port forwarder nomination.",
                "info",
                [
                    "Step 1: Mutual Recognition Principle: Customs clearing agents licensed by one EAC Partner State are legally recognized across all Partner States and granted operating access rights.",
                    "Step 2: Third-Party Declarant / Forwarder: A Ugandan clearing agent with no physical office at Mombasa or Dar es Salaam port can nominate an accredited 'Third-Party Declarant' or 'Forwarder' to handle local port and shipping line processes.",
                    "Step 3: Access Delegation: The primary Ugandan agent delegates permissions within Asycuda World and port gate-pass systems.",
                    "Step 4: Accountability: Both the principal clearing agent and the nominated third-party forwarder remain jointly liable for customs compliance.",
                ],
            )

        # 5. RECTS Tracking & Corridor Security
        if any(k in combined for k in ("rects", "regional electronic cargo tracking system", "free of charge", "security of goods along the corridors", "who meets the cost", "weigh bridges")):
            return (
                "URA Portal — Single Customs Territory (SCT): RECTS & Corridor Security",
                "Satellite electronic tracking, zero-cost policy for traders, and transit corridor management.",
                "info",
                [
                    "Step 1: Free of Charge: The Regional Electronic Cargo Tracking System (RECTS) satellite tracking service is provided 100% free of charge to importers and transporters.",
                    "Step 2: Satellite E-Seals: GPS-enabled electronic seals are affixed to containerized and wet cargo at first points of entry (Mombasa/Dar es Salaam) or border posts to monitor route adherence.",
                    "Step 3: Security Responsibility: While Partner States provide armed highway security, legal responsibility for safeguarding goods rests jointly with the clearing agent, transporter, and cargo owner.",
                    "Step 4: Reduced Roadblocks: Partner States dismantled redundant weighbridges along northern and central corridors, allowing continuous transit to final destination.",
                ],
            )

        # 6. RCTG Bond Account Monitoring
        if any(k in combined for k in ("who monitors rctg", "rctg bond account", "rctg performance", "active carnets", "monitor rctg")):
            return (
                "URA Portal — Single Customs Territory (SCT): RCTG Bond Account Monitoring",
                "Declarant monitoring duties for COMESA Regional Customs Transit Guarantee accounts.",
                "info",
                [
                    "Step 1: Declarant Obligation: Under SCT regulations (Oketch, August 25, 2023), it is the sole responsibility of the declarant to monitor the performance and balances of their RCTG bond account.",
                    "Step 2: Tracking Active Carnets: Declarants log in to the COMESA RCTG portal under the Uganda Electronic Single Window (UESW) to check bond capacity and active transit carnets.",
                    "Step 3: Automatic Replenishment: When goods reach destination and the T1 document is electronically validated by the border officer, the carnet is cancelled and bond capacity is replenished.",
                    "Step 4: Transit Monitoring Unit (TMU) Hold: If transit cargo is delayed or unaccounted for beyond statutory travel hours, TMU automatically suspends the guarantee account.",
                ],
            )

        # 7. First Point of Entry & CFS Clearance
        if any(k in combined for k in ("first point of entry", "first points of entry", "cfs", "container freight stations", "mombasa port", "dar es salaam port")):
            return (
                "URA Portal — Single Customs Territory (SCT): First Point of Entry & CFS Operations",
                "Customs clearance mechanisms at coastal maritime ports and Container Freight Stations.",
                "info",
                [
                    "Step 1: Clearance at Port: Under SCT, cargo destination declarations are lodged in URA Asycuda before vessel arrival; taxes are paid to URA, enabling direct release at Mombasa or Dar es Salaam.",
                    "Step 2: Stationed URA Officers: URA deploys customs officers stationed permanently at Mombasa and Dar es Salaam ports to supervise cargo verification, non-intrusive scanning, and release.",
                    "Step 3: Container Freight Stations (CFSs): Licensed extensions of seaports where groupage cargo and loose cargo containers are transferred for destuffing and joint inspection.",
                    "Step 4: Electronic Release: Once payment reflects in URA accounts, electronic clearance messages transmit to KPA/TPA systems to print gate-passes.",
                ],
            )

        # Default: Single Customs Territory Overview
        return (
            "URA Portal — Single Customs Territory (SCT): Overview & Operations",
            "Full customs union framework for seamless movement of goods across EAC Partner States.",
            "info",
            [
                "Step 1: Visit ura.go.ug/en/category/imports-exports/single-customs-territory/ for comprehensive SCT procedures, business manuals, and public notices.",
                "Step 2: Under SCT (commenced January 1, 2014), goods are cleared at the first point of entry (Mombasa/Dar es Salaam) using a single destination declaration.",
                "Step 3: Taxes are paid to URA at destination rates before cargo departs coastal ports, eliminating internal border transit delays.",
                "Step 4: All transit movements are monitored free of charge via RECTS satellite electronic seals.",
                "Step 5: Mutual recognition enables licensed Ugandan clearing agents and third-party forwarders to transact seamlessly across the EAC.",
            ],
        )

    # Comprehensive List of Exempt Importation Suite (https://ura.go.ug/en/import-export/comprehensive-list-of-exempt-importation/)
    if portal_key == "exempt_importation" or (
        any(
            k in combined
            for k in (
                "comprehensive list of exempt importation",
                "exempt importation",
                "exempt importations",
                "exemptions under eaccma",
                "eaccma fifth schedule",
                "apc 472",
                "apc 475",
                "apc 478",
                "apc 492",
                "comprehensive-list-of-exempt-importation",
            )
        )
        and portal_key not in ("tax_incentives", "get_refund", "motor_vehicle", "choose_tax_agent", "objection_appeals", "stamp_duty", "tax_clearance", "export_process", "customs_valuation", "single_customs_territory", "aeo", "customs_audits_refunds", "warehousing", "customs_enforcements", "laws_and_acts", "double_taxation_agreements")
        and "refund" not in combined
    ):
        # 1. Agricultural Machinery & Implements Exemption (APC 478)
        if any(k in combined for k in ("agricultural machinery", "agricultural implements", "tractor", "tractors", "sprayer", "sprayers", "irrigation equipment", "plough", "harvester", "milling", "apc 478 agricultural")):
            return (
                "URA Portal — Exempt Importation: Agricultural Machinery & Implements (APC 478)",
                "Statutory 0% Import Duty, 0% VAT, and 0% WHT for agricultural machinery, implements, and post-harvest tools under Section 18(10) VAT Act.",
                "info",
                [
                    "Step 1: Eligible Equipment: Covers agricultural tractors, sprayers, irrigation pumps and hoses, disc ploughs, combine harvesters, silage cutters, and grain milling machinery (HS Chapters 82 & 84).",
                    "Step 2: Taxes Payable: 0% Import Duty (EACCMA Fifth Schedule Part B Item 11/15), 0% VAT (VAT Act Third Schedule), and 0% Withholding Tax.",
                    "Step 3: Procedure Code: Lodge customs entry in Asycuda World entering national regime with Additional Procedure Code (APC) 478.",
                    "Step 4: Required Documentation: Commercial invoice, packing list, bill of lading, and manufacturer technical datasheets confirming agricultural specification.",
                    "Step 5: Fast-Track Clearance: Qualifies for fast-track exemption without requiring pre-approval letters from the Ministry if tariff lines correspond to standard agricultural machinery.",
                ],
            )

        # 2. Solar & Wind Green Energy Equipment Exemption (APC 472, Item 26)
        if any(k in combined for k in ("solar", "wind energy", "photovoltaic", "deep cycle", "solar panels", "solar batteries", "inverters", "charge controller", "item 26")):
            return (
                "URA Portal — Exempt Importation: Solar & Wind Energy Equipment (APC 472, Item 26)",
                "Full duty and VAT relief for equipment and accessories for solar and wind energy generation under Item 26 Part B of EACCMA Fifth Schedule.",
                "info",
                [
                    "Step 1: Statutory Scope: Item 26 Part B of the EACCMA Fifth Schedule exempts equipment and specialized accessories for solar and wind power generation, including photovoltaic panels, wind turbines, charge controllers, and deep cycle batteries.",
                    "Step 2: Battery Qualification Rule: Strictly deep cycle batteries designed for solar/wind energy storage qualify. Standard automotive starting/cranking batteries are excluded and liable to full duty.",
                    "Step 3: Taxes Payable: 0% Import Duty, 0% VAT, and 0% WHT under Additional Procedure Code (APC) 472 in Asycuda World.",
                    "Step 4: Clearance Procedure: Submit manufacturer technical spec sheets and equipment invoices verifying renewable energy design to customs for fast-track validation.",
                ],
            )

        # 3. Hotel Operational Equipment Exemption (APC 472, Item 21)
        if any(k in combined for k in ("hotel equipment", "hotel operational equipment", "hotel supplies", "uhoa", "item 21", "engraved")):
            return (
                "URA Portal — Exempt Importation: Hotel Operational Equipment (APC 472, Item 21)",
                "Import duty exemption for hotel equipment, appliances, and furnishings under Item 21 Part B of EACCMA Fifth Schedule.",
                "info",
                [
                    "Step 1: Statutory Scope: Item 21 Part B exempts hotel operational equipment including industrial laundry machines, commercial kitchen equipment, linen, cutlery, and refrigeration equipment.",
                    "Step 2: Mandatory Engraving Requirement: All operational items (linens, kitchenware, appliances) must be indelibly printed, engraved, or woven with the registered hotel logo to prevent commercial diversion.",
                    "Step 3: Required Approvals: Importer must possess a valid Uganda Tourism Operating License and an official endorsement letter from the Uganda Hotel Owners Association (UHOA).",
                    "Step 4: Asycuda Entry: Declare goods under APC 472 in Asycuda World, uploading the UHOA endorsement letter and photographic proof of branding prior to joint customs physical verification.",
                ],
            )

        # 4. Industrial Machinery Replacement Spare Parts (APC 492, Item 31)
        if any(k in combined for k in ("industrial spare parts", "industrial machinery parts", "replacement parts", "item 31", "chapters 84 and 85", "apc 492", "uma", "mtic")):
            return (
                "URA Portal — Exempt Importation: Industrial Machinery Replacement Parts (APC 492, Item 31)",
                "Exemption of spare parts for industrial machinery falling under HS Chapters 84 and 85 for use in manufacturing plants.",
                "info",
                [
                    "Step 1: Statutory Scope: Item 31 Part B of EACCMA Fifth Schedule exempts replacement spare parts for industrial machinery classified under Chapters 84 and 85 of the EAC Common External Tariff.",
                    "Step 2: Manufacturing Invariant: Parts must be imported solely for use in the repair or maintenance of machinery installed in the applicant's licensed manufacturing facility in Uganda.",
                    "Step 3: Sector Recommendation: Importer must obtain an endorsement letter from the Uganda Manufacturers Association (UMA) or the Ministry of Trade, Industry and Cooperatives (MTIC).",
                    "Step 4: Asycuda Processing: Lodge import entry using APC 492, attaching machinery technical schematics, factory layout, and the MTIC/UMA endorsement for fast-track clearance.",
                ],
            )

        # 5. Oil, Gas & Geothermal Machinery & Materials (APC 475, Item 30(a))
        if any(k in combined for k in ("oil and gas", "geothermal", "petroleum exploration", "pau", "memd", "item 30(a)", "item 30", "apc 475")):
            return (
                "URA Portal — Exempt Importation: Oil, Gas & Geothermal Exploration (APC 475, Item 30(a))",
                "Customs duty exemptions on machinery, specialized equipment, and materials for petroleum and geothermal operations.",
                "info",
                [
                    "Step 1: Statutory Scope: Item 30(a) Part B of EACCMA Fifth Schedule exempts goods, specialized drilling equipment, vehicles, and consumables for direct use in oil, gas, or geothermal exploration and development.",
                    "Step 2: Eligible Importers: Licensed exploration companies, production contractors, or approved tier-one subcontractors registered with the Petroleum Authority of Uganda (PAU).",
                    "Step 3: Master Facility List Approval: Importer must obtain project endorsement and import clearance under the PAU Master Facility List.",
                    "Step 4: Asycuda Entry: Declare goods under Additional Procedure Code (APC) 475 in Asycuda World, uploading the PAU/MEMD approval letter for immediate exemption.",
                ],
            )

        # 6. Diagnostic Reagents, Human Drugs & Medical PPE (APC 472 & APC 478)
        if any(k in combined for k in ("diagnostic reagents", "drugs", "medicines", "pharmaceutical", "medical equipment", "nda", "ppe", "protective wear", "item 14", "item 16")):
            return (
                "URA Portal — Exempt Importation: Drugs, Reagents & Medical Equipment (APC 472 & 478)",
                "Complete duty and VAT exemption on essential human medicines, diagnostic reagents, and healthcare consumables under Item 14/16 Part B and VAT Act.",
                "info",
                [
                    "Step 1: Statutory Scope: EACCMA Fifth Schedule Part B Item 14 (diagnostic reagents/equipment), Item 16 (pharmaceutical raw materials), and Section 18(10) VAT Act (human drugs and medical sundries).",
                    "Step 2: NDA Verification: Importers must secure National Drug Authority (NDA) import verification and permit through the Uganda Electronic Single Window prior to vessel arrival.",
                    "Step 3: APC Selection: Use APC 478 for finished essential human medicines and medical sundries; use APC 472 for diagnostic test reagents and medicament packaging materials.",
                    "Step 4: Clearance Protocol: Attach Certificate of Analysis (CoA), NDA verification certificate, and commercial invoice for electronic release in Asycuda.",
                ],
            )

        # 7. Fertilizers, Animal Feeds & Packaging for Export (APC 472 & APC 478)
        if any(k in combined for k in ("fertilizer", "fertilizers", "animal feeds", "parent stock", "poultry stock", "export packaging", "packing materials for export", "item 11", "item 15", "item 2(e)")):
            return (
                "URA Portal — Exempt Importation: Fertilizers, Feeds & Export Packaging (APC 472 & 478)",
                "Statutory exemptions on fertilizers, parent breeding stock, animal feeds, and packaging materials manufactured for export goods.",
                "info",
                [
                    "Step 1: Agricultural Inputs (Item 11 & 15 Part B): Fertilizers, poultry parent stock, and beekeeping gear qualify for 0% duty under APC 472 with Ministry of Agriculture (MAAIF) clearance.",
                    "Step 2: Animal Feeds: Processed animal feeds and mineral premixes attract 0% VAT and 0% duty under APC 478 pursuant to the VAT Act and MAAIF permit.",
                    "Step 3: Export Packaging Materials (Item 2(e) Part B): Sacks, cartons, and packaging supplies designed for packaging export goods qualify under APC 472 provided they are indelibly marked 'FOR EXPORT ONLY'.",
                    "Step 4: Clearance Workflow: Upload MAAIF permits or export packaging proof to Asycuda World for rapid clearance.",
                ],
            )

        # Default: Comprehensive List of Exempt Importation Overview
        return (
            "URA Portal — Comprehensive List of Exempt Importations Overview & APC Fast-Track Rules",
            "Official schedule of duty and tax-free importations under EACCMA Fifth Schedule Part B and the Uganda VAT Act.",
            "info",
            [
                "Step 1: Statutory Basis: Governed by the East African Community Customs Management Act (EACCMA 2004) Fifth Schedule Part B and Section 18(10) of the Value Added Tax Act.",
                "Step 2: Key Exemption Categories: Covers Agricultural Machinery (APC 478), Fertilizers & Seeds (APC 472), Solar/Wind Equipment (APC 472), Hotel Equipment (APC 472), Industrial Spares (APC 492), Oil/Gas Exploration (APC 475), Drugs & Reagents (APC 478/472), and Export Packaging (APC 472).",
                "Step 3: Fast-Track Declaration: Importers or licensed clearing agents select the designated Additional Procedure Code (APC) in Asycuda World and upload required regulatory permits (NDA, MAAIF, UMA, PAU, UHOA).",
                "Step 4: Pre-Approval Requirements: Goods matching standard tariff lines (agricultural tools, deep cycle solar gear) clear automatically; sector-specific items (hotel goods, industrial spares, mining inputs) require designated regulatory endorsements.",
                "Step 5: Access the official schedule at ura.go.ug/en/import-export/comprehensive-list-of-exempt-importation/ for tariff line breakdowns.",
            ],
        )

    # Authorized Economic Operator (AEO) Suite (https://ura.go.ug/en/category/imports-exports/authorized-economic-operator/)
    if portal_key == "aeo" or (
        any(
            k in combined
            for k in (
                "authorized economic operator",
                "aeo",
                "aeo program",
                "aeo erm",
                "aeo enterprise risk management",
                "safe framework",
                "wco safe",
                "prospective clients",
                "processes of attaining an aeo status",
                "benefits of the aeo program",
                "eligibility criteria for becoming an aeo",
                "objectives of the uganda aeo scheme",
            )
        )
        and portal_key not in ("tax_incentives", "get_refund", "motor_vehicle", "choose_tax_agent", "objection_appeals", "stamp_duty", "tax_clearance", "export_process", "customs_valuation", "single_customs_territory", "exempt_importation", "customs_audits_refunds", "warehousing", "customs_enforcements", "laws_and_acts", "double_taxation_agreements", "aeoi")
        and not any(k in combined for k in ("aeoi", "automatic exchange"))
        and "refund" not in combined
    ):
        # 1. About the AEO Program & WCO SAFE Framework
        if any(k in combined for k in ("about the aeo program", "meaning of aeo", "who is an aeo", "what is an aeo", "wco safe", "safe framework", "three pillars", "safe pillars")):
            return (
                "URA Portal — Authorized Economic Operator (AEO): About the AEO Program & WCO SAFE Framework",
                "Trade facilitation framework derived from the WCO SAFE Framework of Standards to reward compliant supply chain actors.",
                "info",
                [
                    "Step 1: Program Definition: Authorized Economic Operator (AEO) is a flagship trade facilitation initiative derived from the World Customs Organization (WCO) SAFE Framework of Standards (Brussels, 2005).",
                    "Step 2: Who is an AEO: An individual, corporate business entity, or government agency involved in international trade and duly certified by the Commissioner of Customs of URA upon fulfilling requisite supply chain security criteria.",
                    "Step 3: Three Pillars of WCO SAFE: (1) Customs-to-Customs network arrangements; (2) Customs-to-Business partnerships; (3) Customs-to-Other Government Agencies (OGAs) cooperation.",
                    "Step 4: Philosophy: Moves customs from transaction-based intervention to trust-based partnership, granting high facilitative privileges to verified compliant traders.",
                ],
            )

        # 2. AEO Prospective Clients & Supply Chain Actors
        if any(k in combined for k in ("prospective clients", "who can apply for aeo", "who qualifies for aeo", "aeo prospective clients", "stakeholders involved with the aeo", "supply chain actors")):
            return (
                "URA Portal — Authorized Economic Operator (AEO): Prospective Clients & Eligible Actors",
                "Supply chain participants eligible to apply for URA Authorized Economic Operator accreditation.",
                "info",
                [
                    "Step 1: Eligible Stakeholders: Open to all participants across the international supply chain: Manufacturers, Customs Clearing Agents (Brokers), Customs Bonded Warehouse Keepers, Importers, Exporters, Transporters/Hauliers, and Freight Forwarders.",
                    "Step 2: Voluntary Participation: The AEO scheme is 100% voluntary; applying is a strategic business decision based on the operator's volume, compliance maturity, and supply chain needs.",
                    "Step 3: Current National Footprint: Uganda currently has 118 certified AEOs across the supply chain, including 71 Importers/Exporters, 47 Clearing Agents, and 18 Bonded Warehouse Operators.",
                    "Step 4: Cross-Sector Value: Both small/medium enterprises (SMEs) and large multinational corporations with clean compliance records can apply.",
                ],
            )

        # 3. Objectives of the Uganda AEO Scheme
        if any(k in combined for k in ("objectives of the uganda aeo scheme", "objectives of the uganda aeo program", "objectives of aeo", "aims of aeo", "why the aeo scheme")):
            return (
                "URA Portal — Authorized Economic Operator (AEO): Objectives of the Scheme",
                "Strategic goals of the Uganda AEO program in promoting supply chain security and trade facilitation.",
                "info",
                [
                    "Step 1: Enhance Trade Facilitation: Streamline customs clearance procedures and encourage international best practices in cargo logistics.",
                    "Step 2: Secure Supply Chains: Safeguard the end-to-end security of the international trade supply chain against illicit trade, smuggling, and security threats.",
                    "Step 3: Promote Voluntary Compliance: Reward compliant taxpayers with tangible cost savings and expedited green-channel clearance.",
                    "Step 4: Optimize Customs Enforcement: Free up customs inspection resources to focus intelligence-driven enforcement on unknown or high-risk consignments.",
                ],
            )

        # 4. Eligibility Criteria for Becoming an AEO
        if any(k in combined for k in ("eligibility criteria for becoming an aeo", "eligibility requirements", "what are the eligibility requirements", "how to qualify for aeo", "qualify as aeo")):
            return (
                "URA Portal — Authorized Economic Operator (AEO): Eligibility Criteria",
                "Statutory and operational prerequisites for businesses applying for AEO certification.",
                "info",
                [
                    "Requirement 1: Active International Trade: The applicant business or individual must be actively involved in international import, export, logistics, or clearing operations.",
                    "Requirement 2: Automated Systems Integration: Demonstrated capability to install and operate customs and tax automated platforms (ASYCUDA World, e-Tax, EFRIS).",
                    "Requirement 3: Clean 3-Year Compliance Track Record: An impeccable tax and customs compliance history with URA for at least three (3) consecutive years.",
                    "Requirement 4: Financial Soundness: Proven financial viability supported by audited financial statements, with no history of bankruptcy or insolvency.",
                    "Requirement 5: Internal Controls: Implementation of an internal AEO Compliance Program and physical/procedural supply chain security controls.",
                ],
            )

        # 5. Processes of Attaining AEO Status & CIP Process
        if any(k in combined for k in ("processes of attaining an aeo status", "process to become an aeo", "what process do i go through to become an aeo", "attain aeo", "cip", "compliance improvement plan")):
            return (
                "URA Portal — Authorized Economic Operator (AEO): Processes of Attaining AEO Status",
                "Step-by-step accreditation lifecycle from Expression of Interest to MOU signing and CIP monitoring.",
                "info",
                [
                    "Step 1: Expression of Interest (EOI): Lodge a formal Expression of Interest letter to the Commissioner of Customs (or apply digitally via the AEO ERM portal).",
                    "Step 2: Preliminary Consultation: Engage in pre-application consultation sessions with the URA AEO Section to evaluate readiness.",
                    "Step 3: Self-Assessment: Complete and submit the comprehensive AEO Self-Assessment Form covering accounting, safety, and supply chain controls.",
                    "Step 4: Customs Vetting: URA conducts deep-dive risk evaluation, background checks, and compliance reviews across domestic taxes and customs.",
                    "Step 5: Onsite Physical Inspection: Customs officers inspect applicant physical premises, warehouses, perimeter security, and IT infrastructure.",
                    "Step 6: Certification & MOU: Upon approval, the Commissioner of Customs signs a formal Memorandum of Understanding (MOU) and issues the official AEO Accreditation Certificate.",
                    "Step 7: Unapproved Applicants (CIP): Businesses falling short of benchmarks are placed on a Compliance Improvement Plan (CIP) to resolve identified gaps within a monitored timeframe.",
                ],
            )

        # 6. Benefits of the AEO Program to Business (Immediate & Long-Term, EAC MRA)
        if any(k in combined for k in ("benefits of the aeo program to business", "benefits of the aeo program", "aeo benefits", "immediate benefits", "long term benefits", "eac mra", "mutual recognition arrangement", "self-management of customs bonded")):
            return (
                "URA Portal — Authorized Economic Operator (AEO): Benefits to Business & EAC MRA",
                "Immediate operational privileges, long-term business advantages, and regional EAC Mutual Recognition.",
                "info",
                [
                    "Benefit 1 (Immediate Operational Privileges): Priority treatment across all URA departments; choice of location for cargo physical examinations (onsite examination at importer's premises); automatic renewal of customs licenses upon fee settlement.",
                    "Benefit 2 (Self-Management of Bonded Warehouses): Bonded warehouse operators certified as AEOs enjoy full self-management without requiring permanent customs officer presence.",
                    "Benefit 3 (Tax & Cash Flow Relinquishment): Automatic exemption from 6% Withholding Tax (WHT) on imports and local supplies, drastically improving operating liquidity.",
                    "Benefit 4 (Regional EAC Mutual Recognition): Under the EAC AEO framework (www.wcoeacaeo-tpf.net), Ugandan AEOs enjoy mutual recognition and expedited green-lane clearance across Kenya, Tanzania, Rwanda, Burundi, South Sudan, and DRC.",
                    "Benefit 5 (Long-Term Competitiveness): Substantial reduction in transit dwell times and clearance costs, zero demurrage risk, enhanced international prestige, and strengthened internal supply chain security.",
                ],
            )

        # 7. AEO Enterprise Risk Management (AEO ERM) System & Portal Workflows
        if any(k in combined for k in ("aeo enterprise risk management", "aeo erm", "what is the aeo erm", "how do you create an account on the aeo erm", "how does one get access to the aeo erm", "will the taxpayer have access to reports", "certification take", "40days", "40 days", "erm portal", "e-aeo certificate")):
            return (
                "URA Portal — Authorized Economic Operator (AEO): AEO Enterprise Risk Management (AEO ERM)",
                "Digital portal automating AEO accreditation, Post Clearance Audits (PCA), and Compliance & Business Analysis (CBA).",
                "info",
                [
                    "Step 1: System Purpose: Modern web platform developed by URA Customs to digitize manual workflows across AEO certification, Post Clearance Audit (PCA), and Compliance & Business Analysis (CBA).",
                    "Step 2: 40-Day Fast-Track: Cuts AEO accreditation processing timeframe from 90 calendar days down to just 40 days.",
                    "Step 3: Portal Features: Taxpayers use business TIN to log in; submit self-assessment forms; track application progress in real time; download e-AEO certificates; review audit management letters; and generate PRNs directly linked to e-Tax.",
                    "Step 4: System Integrations: Seamlessly interfaces with Asycuda World, e-Tax, EFRIS, Ehub, Trade Statistics, and Customs Value Reference databases.",
                    "Step 5: Dispute Resolution: Includes built-in interactive feedback to lodge responses or objections against audit findings directly within the portal.",
                ],
            )

        # Default: Authorized Economic Operator Overview
        return (
            "URA Portal — Authorized Economic Operator (AEO): Program Overview & Guide",
            "URA flagship trade facilitation scheme providing green-channel customs clearance to safe and compliant businesses.",
            "info",
            [
                "Step 1: Visit ura.go.ug/en/category/imports-exports/authorized-economic-operator/ for full program guidelines and the AEO ERM digital portal.",
                "Step 2: Review core eligibility: 3-year clean tax compliance history, financial soundness, automated systems capability, and internal supply chain security.",
                "Step 3: Key advantages: 6% WHT exemption, self-management of bonded warehouses, onsite cargo examinations, priority clearance, and EAC regional mutual recognition.",
                "Step 4: Fast certification: Leverage the AEO ERM online portal to complete expression of interest and self-assessment within the modernized 40-day certification cycle.",
                "Step 5: For enquiries, contact the AEO Project Team at URA Headquarters, Nakawa, or email services@ura.go.ug.",
            ],
        )

    # Customs Audits and Refunds Suite (https://ura.go.ug/en/category/imports-exports/customs-audits-and-refunds/)
    if portal_key == "customs_audits_refunds" or (
        any(
            k in combined
            for k in (
                "customs audits and refunds",
                "customs-audits-and-refunds",
                "customs audit",
                "customs audits",
                "duty drawback",
                "drawback of import duty",
                "diplomatic refund",
                "diplomatic refunds",
                "customs refund",
                "customs refunds",
                "fuel refund",
                "fuel refunds",
                "form c30",
                "form c31",
                "form c33",
                "form c34",
                "instalment payment of ura taxes",
                "section 138",
                "section 139",
                "section 143",
                "section 144",
                "instalment payment",
            )
        )
        and portal_key not in ("tax_incentives", "motor_vehicle", "choose_tax_agent", "objection_appeals", "stamp_duty", "tax_clearance", "export_process", "customs_valuation", "single_customs_territory", "exempt_importation", "aeo", "warehousing", "customs_enforcements", "laws_and_acts", "double_taxation_agreements", "case_summary_reports", "court_of_appeal", "debt_collections", "financial_intelligence_authority")
    ):
        # 1. Instalment Payment of URA Taxes (Customs & General Goods)
        if any(k in combined for k in ("instalment payment of ura taxes", "instalment payment", "apply for instalment", "customs instalment", "tax work sheet", "dpc tax work sheet", "mou for instalment", "dcu mou", "ugx 100 million")):
            return (
                "URA Portal — Customs Audits & Refunds: Instalment Payment of Taxes Guide",
                "Application procedure, compliance checks, DPC Tax Work Sheet, and Debt Collection Unit MOU for instalment clearance.",
                "info",
                [
                    "Step 1: Application Letter: Applicant writes a formal letter to Commissioner Customs Department (CCD) requesting instalment payment, attaching Bill of Lading / Airway Bill, commercial invoice, export certificate, phone number, and valid email.",
                    "Step 2: Submission & Stamping: Deliver the letter in person to the Central Records Office (Upper Ground / Reception, URA Tower) for official receipt acknowledgement and stamping.",
                    "Step 3: Compliance Review: CCD conducts a compliance review check across all URA tax heads; upon clearance, CCD issues an acceptance letter via email and forwards the file to the Compliance and Business Analysis (CBA) division.",
                    "Step 4: Tax Work Sheet Lodgement: Supervisor Arrears Management advises the applicant to appoint a licensed clearing agent, who lodges an online Tax Work Sheet to the Document Processing Centre (DPC) via URA Touch Point.",
                    "Step 5: DPC Assessment: DPC computes taxes due and prepares a summarized Tax Work Sheet, forwarding the payment schedule to CBA and the Debt Collection Unit (DCU).",
                    "Step 6: MOU Execution: DCU prepares the Memorandum of Understanding (MOU). Applicant pays the down payment (first instalment) and signs the MOU.",
                    "Step 7: MOU Requirements: Post-dated cheques, UGX 30,000 Stamp Duty receipt, company Form 7/partnership deed, directors' ID copies/photos, company seal, and an official witness. Note: For liabilities exceeding UGX 100 Million, additional security (Land Title, Bank Guarantee, Insurance Bond, or Logbook) is mandatory, and for customs cases the applicant's original passport is deposited as security.",
                    "Step 8: Cargo Release: Clearing agent captures a customs declaration in Asycuda, and URA releases the goods/motor vehicle while remaining instalments are settled per schedule.",
                ],
            )

        # 2. Diplomatic Customs Refunds (Fuel & Excise Duty under Section 114(1) & Form C34)
        if any(k in combined for k in ("diplomatic refund", "diplomatic refunds", "fuel refund", "fuel refunds", "diplomatic id", "excise duty on fuel", "mfa form 3", "mfa/dp form3", "section 114", "diplomatic entities entitled")):
            return (
                "URA Portal — Customs Audits & Refunds: Diplomatic Fuel & Customs Refunds",
                "Statutory fuel excise duty refund procedure for accredited diplomats and missions under Section 114(1) and Fifth Schedule Part A.",
                "info",
                [
                    "Step 1: Legal Basis: Section 114(1) and Fifth Schedule Part A of EACCMA 2004, and Section 12(i) & (ii) of the Excise Duty Act 2014, granting refund of excise duty paid on fuel consumed.",
                    "Step 2: Entitled Beneficiaries: (a) Diplomats (holders of Diplomatic IDs starting with letter 'D...') and spouses; (b) Diplomatic / Consular Missions (UN agencies, Commonwealth High Commissions, foreign embassies under Item 4); (c) Accredited Donor Agencies with bilateral/multilateral agreements (Item 5 & 6).",
                    "Step 3: Mandatory Documentation: Duly completed Customs Refund Form C34 (authenticated with signature and official stamp of Ministry of Foreign Affairs); MFA Form 3 authorization (MFA/DP Form 3); MFA-approved vehicle/equipment list; fuel computation worksheet; MFA-stamped fuel purchase receipts/invoices; and updated TIN and bank details registered with URA Finance Division.",
                    "Step 4: Submission & Vetting: Submit claim to MFA for confirmation, then present to Supervisor Refunds Unit, Customs Audit Division for face vetting and register entry.",
                    "Step 5: Audit Review: Customs refunds audit officer validates authenticity and computations, prepares report, and routes for authorization and electronic payment via AC Finance Division.",
                ],
            )

        # 3. General Customs Duty Refunds (Sections 143, 144, & Form C33 / Form C34)
        if any(k in combined for k in ("general refund", "general claim", "section 143", "section 144", "form c33", "c.33", "goods damaged while subject to customs", "goods not in accordance with contract", "short-landing", "paid in error")):
            return (
                "URA Portal — Customs Audits & Refunds: General Customs Duty Refunds",
                "Procedures for duty refunds on damaged goods, contract discrepancy, and duty paid in error under Sections 143 and 144 EACCMA.",
                "info",
                [
                    "Step 1: Legal Provisions: Governed by EACCMA Section 143 (goods damaged before delivery or not in accordance with contract, returned under Section 75 or destroyed under customs supervision) and Section 144 (goods damaged/destroyed while subject to customs control, or duty paid in error).",
                    "Step 2: Prescribed Forms: Claimant fills Form C.33 for damaged/destroyed goods under s.144(1)(a) or Form C.34 for claims under s.143 and s.144(1)(b) (duty paid in error).",
                    "Step 3: Statutory 12-Month Limit: Claims must be formally presented to Customs within twelve (12) months from the date of payment of the duty.",
                    "Step 4: Required Attachments: Endorsed refund form, proof of tax payment, commercial invoices, packing lists, bill of lading, re-export permit or customs destruction certificate, and police accident/theft reports with photographs where applicable.",
                    "Step 5: Submission & Review: Lodge claim with Head of Customs Station where duty was paid. The station officer verifies details, updates the system Inspection Act, and forwards to Supervisor Refunds Unit for comprehensive audit.",
                ],
            )

        # 4. Customs Auction Sale Proceeds Recovery (Section 57(3) & 57(4))
        if any(k in combined for k in ("auction proceeds", "section 57", "proceeds of such sale", "warehouse keeper charges", "balance of sale", "surplus proceeds")):
            return (
                "URA Portal — Customs Audits & Refunds: Auction Sale Proceeds Recovery",
                "Statutory priority discharge of auction sale proceeds and surplus recovery under Section 57(3) and 57(4) EACCMA.",
                "info",
                [
                    "Step 1: Legal Order of Application (Section 57(3)): Proceeds from customs auction of entered/abandoned goods are discharged in statutory priority: (a) Customs duties; (b) Expenses of the sale; (c) Rent and storage charges due to Customs or warehouse keeper; (d) Port charges; (e) Freight and shipping charges.",
                    "Step 2: Surplus Balance Entitlement (Section 57(4)): Where any surplus balance remains after discharging all statutory liabilities, the original cargo owner is entitled to receive the balance.",
                    "Step 3: Strict 1-Year Limitation: The cargo owner must make formal application within one (1) year from the date of the auction sale; otherwise, the surplus balance is permanently forfeited to national customs revenue.",
                    "Step 4: Claim Workflow: Submit written application with original title documents, bill of lading, auction lot number, and bank account details to Customs Audit & Refunds Section for verification.",
                ],
            )

        # 5. Duty Drawback (DDB) Manufacturer Registration (Sections 138-140 & Form C30)
        if any(k in combined for k in ("procedure for duty drawback registration", "duty drawback registration", "register for duty drawback", "form c30", "c30", "input output ratio", "input-output", "tid")):
            return (
                "URA Portal — Customs Audits & Refunds: Duty Drawback (DDB) Registration Procedure",
                "Manufacturing plant onboarding, site inspection, and Tariff & Information Division (TID) input-output ratio certification under Form C30.",
                "info",
                [
                    "Step 1: Submission of Form C30: Manufacturing company completes and submits predefined Form C30 to Commissioner Customs Department (CCD) applying for DDB registration prior to exporting finished goods.",
                    "Step 2: Face Vetting: AC Customs Audit forwards application to Supervisor Refunds Unit to verify business name, physical factory location, TIN, VAT registration, certificate of incorporation, and director details.",
                    "Step 3: Factory Site Inspection: Supervisor Refunds constitutes an inspection team to conduct an onsite factory audit to verify manufacturing equipment, processing lines, and raw material inputs.",
                    "Step 4: Input-Output Ratios: Supervisor Refunds drafts a formal request to Tariff and Information Division (TID) to calculate and establish the official input-output coefficients and rate of yield.",
                    "Step 5: Approval & Registration Number: Team compiles inspection report; AC Customs Audit reviews, approves application, and allocates an official DDB Registration Number with effective export commencement date.",
                ],
            )

        # 6. Duty Drawback (DDB) Export Claims Procedure (Form C31 & 12-Month Rule)
        if any(k in combined for k in ("procedure for claiming duty drawback", "claiming duty drawback", "claim duty drawback", "form c31", "c.31", "drawback claim", "rate of yield", "us$100", "12 months of exportation")):
            return (
                "URA Portal — Customs Audits & Refunds: Duty Drawback (DDB) Claims Procedure",
                "Export claim submission, statutory 12-month limit, USD 100 minimum threshold, and audit verification under Form C31.",
                "info",
                [
                    "Step 1: Claim Submission (Form C31): Registered manufacturer completes Form C31 under Regulation 139(1) of EACCMR 2010 with supporting export documentation and lodges with Supervisor Refunds Unit.",
                    "Step 2: Strict 12-Month Window: The claim must be lodged within twelve (12) months from the date of exportation of the goods from Uganda.",
                    "Step 3: Mandatory Pre-Export Examination: Goods must have been entered on prescribed export entry and produced for physical examination by the proper customs officer before export departure.",
                    "Step 4: Statutory Disallowance Rules: Drawback is prohibited if the home consumption value is less than the drawback amount, or if total import duty paid was less than USD 100.",
                    "Step 5: Required Records: Export customs declarations, bill of lading / airway bill, certificate of export, raw material import declarations (C63 entries), tax payment receipts, and factory batch production records.",
                    "Step 6: Auditor Review & Report: Customs auditor examines computations against approved TID input-output ratios and compiles comprehensive audit report (background, objectives, documents, computation findings, recommendations).",
                ],
            )

        # 7. Customs Refund Approval Financial Thresholds & Payment Flow
        if any(k in combined for k in ("approval thresholds", "who approves refund", "authorized by manager customs audit", "ac customs audit", "commissioner customs", "ac finance division", "ugx 1, 000,000", "10,000,000")):
            return (
                "URA Portal — Customs Audits & Refunds: Approval Financial Thresholds & Payment Flow",
                "Statutory administrative delegation limits and electronic payment authorization across URA Customs Audit hierarchy.",
                "info",
                [
                    "Threshold 1 (Up to UGX 1,000,000): Recommended by Supervisor Refunds Unit, authorized and approved by Manager Customs Audit.",
                    "Threshold 2 (Above UGX 1,000,000 up to UGX 10,000,000): Recommended by Manager Customs Audit, authorized and approved by Assistant Commissioner (AC) Customs Audit.",
                    "Threshold 3 (Above UGX 10,000,000): Recommended by Assistant Commissioner (AC) Customs Audit, authorized and approved by Commissioner Customs.",
                    "Payment Memo Generation: Upon authorization, an official disbursement memo is generated by the auditor and transmitted to the Assistant Commissioner Finance Division.",
                    "Disbursement: URA Finance Division processes electronic payment (EFT) directly to the claimant's bank account registered on their URA TIN profile.",
                ],
            )

        # 8. Public International Organizations Accredited to Uganda Directory
        if any(k in combined for k in ("public international organizations", "accredited to uganda", "organizations accredited", "diplomatic directory", "un agencies", "danida", "usaid", "who", "unicef")):
            return (
                "URA Portal — Customs Audits & Refunds: Accredited Public International Organizations Directory",
                "Statutory directory of 87 diplomatic missions and public international organizations entitled to customs duty privileges.",
                "info",
                [
                    "Directory Scope: Covers 87 accredited public international bodies and development partners in Uganda (including DANIDA, GTZ/GIZ, EADB, ESAMI, EU, FAO, IMF, World Bank, WHO, WFP, UNICEF, UNDP, UNFPA, UNHCR, USAID, JICA, NORAD, ADA, DFID, ICRC, African Union, MSF, IOM, TMEA, etc.).",
                    "Statutory Entitlement: Goods and equipment imported for their official use qualify for duty exemption under EACCMA Fifth Schedule Part A (Items 4, 5, 6) and Section 114(1).",
                    "Fuel Refund Protocol: Fuel consumed by missions and eligible diplomatic staff qualifies for excise duty refund using Form C34 authenticated by the Ministry of Foreign Affairs (MFA).",
                    "Verification Requirement: All fuel purchase invoices and claim schedules must be verified against the MFA-approved mission vehicle and equipment list.",
                ],
            )

        # Default: Customs Audits and Refunds Suite Overview
        return (
            "URA Portal — Customs Audits and Refunds: Overview & Procedures",
            "Official customs audit, post-clearance examination, duty drawback, general refund, and diplomatic tax relief framework.",
            "info",
            [
                "Step 1: Visit ura.go.ug/en/category/imports-exports/customs-audits-and-refunds/ for full guidelines, statutory provisions, and downloadable customs forms.",
                "Step 2: Customs Audits: URA conducts systematic examinations of import/export financial records, declarations, and accounting books to verify accurate reporting and compliance with customs laws.",
                "Step 3: Duty Drawback (DDB): Registered manufacturers (Form C30) claim refund of import duty paid on imported raw materials used to produce exported goods (Form C31) within 12 months.",
                "Step 4: General Customs Refunds: Apply under Section 143 (damaged/returned goods) or Section 144 (error/damaged in customs) using Form C33 or C34 within 12 months.",
                "Step 5: Diplomatic Refunds: Accredited missions and diplomats claim excise duty on fuel using Form C34 authenticated by the Ministry of Foreign Affairs.",
                "Step 6: Instalment Payment Facility: Importers of motor vehicles and general goods facing cash flow constraints can apply to Commissioner Customs for structured instalment payment under a DCU MOU.",
            ],
        )

    # Warehousing Suite (https://ura.go.ug/en/category/imports-exports/warehousing/)
    if portal_key == "warehousing" or (
        any(
            k in combined
            for k in (
                "warehousing",
                "bonded warehouse",
                "customs warehouse",
                "public online auction",
                "online auction",
                "private treaty",
                "want of entry",
                "singlewindow.go.ug/auction",
                "warehousing of goods",
                "customs warehousing manual",
                "im7",
                "cb6",
                "verification at owners",
            )
        )
        and portal_key not in ("tax_incentives", "motor_vehicle", "choose_tax_agent", "objection_appeals", "stamp_duty", "tax_clearance", "export_process", "customs_valuation", "single_customs_territory", "exempt_importation", "aeo", "customs_audits_refunds", "customs_enforcements", "laws_and_acts", "double_taxation_agreements", "customs_systems")
    ):
        # 1. Public Online Auction via UeSW (singlewindow.go.ug/auction)
        if any(k in combined for k in ("public online auction", "online auction", "singlewindow.go.ug/auction", "participation fee", "$10", "10 usd", "24 to 48 hours", "24-48 hours", "add to cart", "submit bid", "reserve price", "as is, where is")):
            return (
                "URA Portal — Warehousing: Public Online Auction via UeSW",
                "Automated disposal of abandoned and overstayed customs cargo via the Uganda Electronic Single Window.",
                "info",
                [
                    "Step 1: Auction Portal & Access: As of 2026, customs auctions are 100% automated via the Uganda Electronic Single Window at singlewindow.go.ug/auction.",
                    "Step 2: Eligibility & Registration: Bidders must possess an active URA TIN, be at least 18 years old, and pay a non-refundable participation fee of USD 10 via the URA online payment portal.",
                    "Step 3: Physical Inspection Window: Upcoming lots are advertised in national press and social media; bidders are granted a 7-day physical inspection window at designated customs bonded warehouses (e.g. Nakawa HQ).",
                    "Step 4: Placing Bids: Log in to singlewindow.go.ug/auction using TIN credentials, select desired Lot, enter bid exceeding reserve price, click 'Add to Cart', and submit bid.",
                    "Step 5: Winning & Payment Timeline: Successful bidders receive email notifications and must generate a PRN and pay 100% of the bid price (plus registration/licensing fees for motor vehicles) within strict 24 to 48 hours. If the winning bidder defaults, the lot is offered to next highest bidders up to 6th position.",
                    "Step 6: Asset Collection: Present signed system-generated handover certificate, payment receipt, and valid National ID/Passport. Goods are sold on strict 'As Is, Where Is' basis without warranties.",
                ],
            )

        # 2. Private Treaty Disposal Sales
        if any(k in combined for k in ("private treaty", "direct negotiation", "unsold lots", "alternative disposal", "decongest customs warehouses")):
            return (
                "URA Portal — Warehousing: Private Treaty Disposal Sales",
                "Direct negotiation and alternative disposal channel for unsold auction lots and time-sensitive cargo.",
                "info",
                [
                    "Step 1: Concept & Purpose: Private Treaty is an alternative disposal channel under the EACCMA to sell abandoned, seized, or unsold auction lots (especially motor vehicles and general merchandise) to decongest customs warehouses and mobilize domestic revenue.",
                    "Step 2: Direct Negotiation: Unlike public auctions with rigid bidding cycles, Private Treaty permits direct price negotiation with potential buyers or controlled online bidding approved by URA Customs management.",
                    "Step 3: Free Participation: Participation in a Private Treaty sale is completely free of charge for individuals/entities with a valid TIN (no USD 10 fee required).",
                    "Step 4: Payment Terms: Winning/approved buyer must generate a PRN and settle 100% of the agreed purchase price within the specified timeframe.",
                    "Step 5: 'As Is' Condition: All items are sold strictly on an 'as is' basis with no post-sale warranties or return claims accepted.",
                ],
            )

        # 3. Want of Entry List & Cargo Redemption (Section 42 Notice)
        if any(k in combined for k in ("want of entry", "want of entry list", "14 days", "un-entered", "unentered", "redemption", "section 42")):
            return (
                "URA Portal — Warehousing: Want of Entry List & Cargo Redemption",
                "Classification of un-entered imported cargo, state warehouse transfers, 30-day gazette notice, and statutory redemption.",
                "info",
                [
                    "Step 1: Definition: 'Want of Entry' applies to imported cargo that has arrived at a border, seaport, airport, or bonded warehouse without customs entry documents (Bill of Entry) or duty payment within statutory limits.",
                    "Step 2: Statutory 14-Day Limit: Cargo not entered within 14 days from arrival (or expiry of approved warehousing) is transferred to the customs state warehouse as 'Want of Entry'.",
                    "Step 3: Section 42 Public Notice: Pursuant to Section 42(1) EACCMA 2004, the Commissioner Customs issues a 30-day public notice in the Uganda Gazette and national media requiring owners to clear un-customed goods.",
                    "Step 4: Redemption Process: Before auction action, owners can redeem cargo by submitting an IM4 entry in Asycuda World and paying all outstanding import duties, storage rent, penalties, and administrative fees within the 30-day notice window.",
                    "Step 5: Disposal: Cargo remaining un-entered or unredeemed upon expiration of the 30-day notice is permanently scheduled for public online auction or private treaty disposal.",
                ],
            )

        # 4. Warehousing of Goods & Regimes (IM7 Conversion & CB6 Bond)
        if any(k in combined for k in ("warehousing of goods", "cargo receiving", "im7", "im 7", "cb6", "cargo management", "prohibited from being warehoused", "which items are prohibited", "cargo release and exit")):
            return (
                "URA Portal — Warehousing: Warehousing of Goods & Regimes (IM7 & CB6)",
                "Customs warehousing lifecycle from transit auto-conversion (IM7) to CB6 bond commitment and IM4 home consumption release.",
                "info",
                [
                    "Step 1: Cargo Receiving & Auto-Conversion: Border/barrier officer and bond keeper crosscheck transit document (T1) against container seals and truck marks; retrieve T1n from Asycuda World and auto-convert to warehousing regime (IM7).",
                    "Step 2: Prohibited Warehousing Goods: Acids, chalk, ammunition, explosives, fireworks, dried fish, perishable goods, matches, and inflammable items (except petroleum stored in approved installations) cannot be warehoused.",
                    "Step 3: Risk Selectivity: IM7 declarations pass through Asycuda risk lanes (Green/Blue auto-release; Yellow processed by DPC; Red assigned for physical verification by customs officer).",
                    "Step 4: Bond Execution (CB6): Following DPC release, the warehouse bond (CB6) is committed and cargo is offloaded into the licensed bond store.",
                    "Step 5: Clearance & Exit (IM4 / Re-export): Clearing agent lodges home consumption entry (IM4) or re-export entry, pays assessed taxes, triggers DPC release, performs bond clearance, and generates an EFRIS electronic receipt.",
                ],
            )

        # 5. Statutory Warehousing Periods & Limits
        if any(k in combined for k in ("how long can an importer keep goods", "how long are goods warehoused", "warehousing period", "6 months", "9 months", "270 days", "two (2) years", "2 years", "special goods")):
            return (
                "URA Portal — Warehousing: Statutory Warehousing Durations & Extensions",
                "Statutory warehousing timeframes under EACCMA: 6-month standard limit, 3-month extension, and 2-year special category.",
                "info",
                [
                    "General Merchandise: Dutiable goods are permitted in a customs bonded warehouse for a standard period of six (6) months.",
                    "Commissioner's Extension: With prior written application and approval of the Commissioner of Customs, general goods may receive an extension of three (3) months, creating an absolute cap of nine (9) months (270 days).",
                    "Special 2-Year Category: Up to two (2) years warehousing is granted for: (1) Bulk wines and spirits imported by licensed manufacturers; (2) Goods stocked in duty-free shops; (3) Brand new motor vehicles warehoused by authorized franchise dealers.",
                    "Consequence of Expiry: Goods exceeding statutory limits become 'un-customed overstayed goods' and are gazetted for public auction within 30 days under Section 42(1) EACCMA.",
                ],
            )

        # 6. Provisional Release & Verification at Owner's Premises
        if any(k in combined for k in ("provisional release", "verification at owners", "verification at owner", "sct-pev", "wt8", "fragile, bulky")):
            return (
                "URA Portal — Warehousing: Provisional Release & Owner's Premises Verification",
                "Protocols for clearing bulky, fragile, or unassembled heavy industrial plant machinery directly at the owner's premises.",
                "info",
                [
                    "Step 1: Eligible Goods: Fragile, bulky, unassembled, or heavy industrial machinery plants; goods cleared under partial/instalment MOUs; and goods for repair.",
                    "Step 2: Premises Prerequisites: The importer must possess a safe, secure, lockable, and sealable store/room approved by Customs.",
                    "Step 3: Declaration & Application: Lodge customs declaration in Asycuda (SCT-PEV or Warehousing Transit WT8) and submit a written request to Commissioner Customs on cargo arrival at the bond.",
                    "Step 4: Inspection & Approval: Proper customs officer inspects and confirms the nature of goods; Commissioner grants written permission; DPC releases the warehousing declaration.",
                    "Step 5: Home Consumption & Departure: Importer files an IM4 home consumption entry, pays initial duties, and cargo departs bond under customs seal for physical verification at owner's facility.",
                    "Step 6: Verification Timelines: Standard bond verification takes 3-12 hours for single clients and 12-24 hours for consolidated groupage cargo.",
                ],
            )

        # 7. Enforcement of Guidelines for Licensed Customs Bonded Warehouses (2026 Guidelines)
        if any(k in combined for k in ("enforcement of guidelines for the management of licensed", "enforcement of guidelines", "guidelines for the management of licensed", "reflector jackets", "distinct uniforms", "section 64", "section 67", "kpis")):
            return (
                "URA Portal — Warehousing: Bonded Warehouse Management Enforcement Guidelines",
                "Mandatory operational directives issued February 24, 2026 for all licensed customs bonded warehouse operators.",
                "info",
                [
                    "Directive 1 (Access Control): Bonded warehouses must implement controlled electronic access systems logging visitor identity, purpose, and valid IDs. All staff and visitors must wear high-visibility reflector jackets at all times on premises.",
                    "Directive 2 (Staff Identification): Loaders and casual workers must be provided distinct uniforms, unique ID numbers, and registered photo records with National Identification Numbers (NINs).",
                    "Directive 3 (Safety & Accountability under Section 67): All warehoused goods remain under Customs control. Warehouse keepers must produce goods on request. Unaccounted losses or substitutions constitute offences under Section 67 EACCMA, attracting full duties, fines, and license revocation.",
                    "Directive 4 (Facilities & Equipment under Section 64): Warehouse operators must maintain functional examination, record-keeping, and storage equipment as mandated under Section 64 EACCMA.",
                    "Directive 5 (Compliance KPIs & 2027 Renewal): URA monitors operations via Key Performance Indicators (KPIs); non-compliance results in automatic license cancellation and disqualification for 2027 renewal.",
                ],
            )

        # Default: Warehousing Overview
        return (
            "URA Portal — Customs Warehousing: Overview & Guidelines",
            "Customs warehousing regimes, bonded storage operations, public online auctions, and private treaty disposal sales.",
            "info",
            [
                "Step 1: Visit ura.go.ug/en/category/imports-exports/warehousing/ for comprehensive bonded warehouse guides, the Customs Warehousing Manual, and public disposal notices.",
                "Step 2: Types of Warehouses: Public general goods, public car bonds, private general bonds, private car bonds, and manufacture-under-bond installations.",
                "Step 3: Storage Timelines: Standard goods warehoused for 6 months (max 9 months upon Commissioner's extension); special goods (wines, duty-free, new motor vehicles) up to 2 years.",
                "Step 4: Public Online Auctions: Automated via singlewindow.go.ug/auction with a $10 participation fee, 7-day inspection window, and 24-48 hour PRN payment window.",
                "Step 5: Private Treaty Sales: Free participation for TIN holders to purchase unsold auction lots through direct negotiation or controlled online bidding.",
                "Step 6: Want of Entry: Cargo unentered within 14 days moves to state warehouse; 30-day notice is published before auction.",
            ],
        )

    # Customs Enforcements Suite (https://ura.go.ug/en/category/imports-exports/customs-enforcements/)
    if portal_key == "customs_enforcements" or (
        any(
            k in combined
            for k in (
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
            )
        )
        and portal_key not in ("tax_incentives", "motor_vehicle", "choose_tax_agent", "objection_appeals", "stamp_duty", "tax_clearance", "export_process", "customs_valuation", "single_customs_territory", "exempt_importation", "aeo", "customs_audits_refunds", "warehousing", "laws_and_acts", "double_taxation_agreements")
    ):
        # 1. Prohibited & Restricted Goods Schedule (Used Electronics, Chemicals, Section 210)
        if any(k in combined for k in ("prohibited goods", "restricted goods", "used laptops", "used electronics", "used computers", "used fridges", "mercury", "consumer protection", "section 210", "crocidolite", "dieldrin", "ddt")):
            return (
                "URA Portal — Customs Enforcements: Prohibited & Restricted Goods Schedule",
                "Statutory prohibition and restriction lists under EACCMA, Finance Act 2009, and Consumer Protection Regulations 2017.",
                "info",
                [
                    "Step 1: Prohibited Goods Definition: Goods whose importation or exportation is completely banned by law on grounds of public health, environment, security, or morals. Includes counterfeit currency, pornography, white phosphorus matches, toxic cosmetics containing mercury, and used passenger car tyres.",
                    "Step 2: Used Electronics Prohibition: Under Section 2 of Finance Act 2009, used computers, laptops, monitors, TV sets, and fridges are strictly prohibited to protect the environment (duty on new laptops is 0%). Importing attracts penalties up to 5 years imprisonment or a fine of 50% of commercial value, plus destruction costs at owner's expense.",
                    "Step 3: Restricted Goods: Goods requiring prior regulatory permits before importation: live animals and animal products (MAAIF), medicines and pharmaceutical sundries (NDA), minerals, and wildlife products.",
                    "Step 4: Unmanned Aerial Vehicles (Drones): Restricted strictly on national security grounds; requires prior written security clearance and operational license from Ugandan defense/security agencies.",
                    "Step 5: Forfeiture (Section 210 EACCMA): Prohibited and uncustomed goods are subject to mandatory seizure and forfeiture.",
                ],
            )

        # 2. Passenger Accompanied Baggage at Entebbe Airport (USD 500 & USD 2000 Rules)
        if any(k in combined for k in ("goods handled at entebbe airport", "passenger baggage", "entebbe airport", "entebbe international airport", "usd 500", "500$", "usd 2000", "personal use", "accompanied baggage", "duty free shop", "currency declaration", "1,500 currency points")):
            return (
                "URA Portal — Customs Enforcements: Entebbe Airport Passenger Baggage Clearance",
                "Customs clearance rules, USD 500 duty-free personal allowance, and USD 2,000 TIN threshold for arriving passengers.",
                "info",
                [
                    "Step 1: USD 500 Duty-Free Allowance: Arriving passengers enjoy duty exemption on accompanied goods for personal use provided: (1) Total value does not exceed USD 500; (2) Goods are not for commercial resale; (3) Goods are not for distribution; (4) Passenger has been outside Uganda for more than 24 hours; (5) Goods accompany the passenger.",
                    "Step 2: Duty-Free Quantities: Up to 1 litre spirits or 2 litres wine, 250ml perfume / 500ml toilet water, and 250g tobacco products (cigarettes/cigars).",
                    "Step 3: Currency Declaration Threshold: Carrying currency in or out of Uganda exceeding 1,500 currency points (approx. UGX 30,000,000 or USD 9,000) requires a mandatory written customs declaration.",
                    "Step 4: TIN & Agent Threshold (USD 2,000 Rule): Passenger accompanied baggage valued up to USD 2,000 does NOT require a TIN or a licensed customs clearing agent. If goods exceed USD 2,000 or are consigned to a corporate entity, a TIN and a licensed customs agent are legally mandatory.",
                    "Step 5: Red vs. Green Channel: Passengers carrying commercial goods, restricted items, or goods exceeding USD 500 must exit through the Red Channel to declare items to customs.",
                    "Step 6: Penalties: Misdeclaration or smuggling attracts up to 3 years imprisonment or fines up to USD 10,000 under EACCMA.",
                    "Step 7: Official Brochure: Download the complete Passenger Baggage Guide at ura.go.ug/storage/2025/03/Passenger-baggage-personal-effects-2024-25.pdf.",
                ],
            )

        # 3. Transit Cargo Monitoring & 21 Gazetted Corridors
        if any(k in combined for k in ("transit management", "transit monitoring unit", "tmu", "gazetted transit routes", "transit routes in uganda", "through transit", "inward transit", "outward transit", "30 days")):
            return (
                "URA Portal — Customs Enforcements: Transit Cargo Monitoring & Gazetted Corridors",
                "Transit procedures, 30-day movement window, security bond execution, and 21 gazetted transport routes.",
                "info",
                [
                    "Step 1: Transit Regimes: Covers Through Transit (foreign-to-foreign), Inward Transit (border port to inland station), and Outward Transit (guaranteed export from inland to border exit). Declared on Form C17 (IM8, WT8, ST8).",
                    "Step 2: Customs Security Bond: All transit goods are covered by a customs security bond executed by the agent, equal to the full tax liability of the cargo, which is cancelled upon verified border exit.",
                    "Step 3: 30-Day Statutory Limit: The transit journey must be completed and goods must exit Uganda within thirty (30) days from the date of the transit entry.",
                    "Step 4: 21 Gazetted Transit Corridors: Cargo must move strictly along approved routes: Malaba/Busia–Kampala, Kampala–Karuma–Elegu, Kampala–Karuma–Arua–Oraba/Vurra, Mutukula–Masaka–Kampala, Kampala–Mbarara–Katuna/Mirama Hills/Cyanika, Kampala–Mubende–Fort Portal–Mpondwe/Busunga/Ntoroko, Entebbe–Kampala.",
                    "Step 5: Incident & Accident Protocol: In case of breakdown or road accident, report immediately to the nearest Customs office or Rapid Response Unit (RRU) on 0323 442500 for supervised transshipment.",
                ],
            )

        # 4. Transit Goods License (TGL) vs Road User Charges (RUC)
        if any(k in combined for k in ("transit goods licence", "transit goods license", "tgl", "road user charges", "road toll", "form c39", "form c28", "usd 200", "transporter")):
            return (
                "URA Portal — Customs Enforcements: Transit Goods License (TGL) & Road User Charges",
                "Vehicle licensing requirements, Form C39 application, USD 200 annual fee, and Road User Charges formula.",
                "info",
                [
                    "Step 1: Mandatory TGL: Under Section 244(2) EACCMA, conveying transit cargo in an unlicensed vehicle is an offence attracting a fine up to USD 5,000. All transit vehicles must hold a valid Transit Goods License (TGL).",
                    "Step 2: TGL Application (Form C39): Transporter applies via the Uganda Electronic Single Window (UESW); vehicle undergoes physical inspection to verify it is sealable, has no false compartments, and has 'TRANSIT GOODS' boldly painted on both sides.",
                    "Step 3: TGL Validity & Regional Recognition: Upon paying USD 200 per vehicle, Commissioner issues license on Form C28. TGL is valid for one (1) calendar year and is recognized across all EAC Partner States.",
                    "Step 4: Road User Charges (RUC / Road Toll): Foreign trucks pay RUC per trip at each entry into Uganda for national road usage.",
                    "Step 5: RUC Computation Formula: (Distance in KMs / 100) x Rate per Vehicle (e.g. USD 10 for 3-axle truck) x Current URA Exchange Rate.",
                ],
            )

        # 5. Regional Electronic Cargo Tracking System (RECTS)
        if any(k in combined for k in ("rects", "regional electronic cargo tracking system", "eseal", "e-seal", "electronic seal", "centralized monitoring center", "cmc", "rapid response unit", "rru")):
            return (
                "URA Portal — Customs Enforcements: Regional Electronic Cargo Tracking System (RECTS)",
                "Satellite-based transit tracking operations across Kenya, Rwanda, and Uganda via Centralized Monitoring Center (CMC).",
                "info",
                [
                    "Step 1: System Coverage: RECTS is an integrated satellite tracking platform monitoring transit consignments across Kenya, Uganda, and Rwanda (with South Sudan and Burundi onboarding).",
                    "Step 2: Free of Charge: Tracking under RECTS is completely 100% FREE OF CHARGE. URA levies no tracking fees on traders.",
                    "Step 3: Arming & Disarming: Border customs officers attach smart electronic seals (e-Seals) and activate them in RECTS at entry ports; destination border officers verify seal integrity and deactivate e-Seals upon verified exit.",
                    "Step 4: Centralized Monitoring Center (CMC): CMC officers monitor consignments 24/7 in real time for route deviations, unauthorized stops, or seal tampering.",
                    "Step 5: Rapid Response Unit (RRU): When violations or geofence breaches occur, CMC escalates to mobile RRU patrol teams to intercept cargo. Transporters must pay replacement fees if e-Seals are lost or damaged.",
                ],
            )

        # 6. Customs Offences, Seizure Notices & Compounding (Section 219)
        if any(k in combined for k in ("customs offence", "customs offences", "seizure notice", "form c37", "c37", "request to settle offence", "form c35", "c35", "compounding", "section 219", "section 199", "outright smuggling", "customs-offences")):
            return (
                "URA Portal — Customs Enforcements: Customs Offences, Seizures & Compounding",
                "Seizure Notice Form C37 issuance, offence investigation, compounding under Section 219, and court prosecution.",
                "info",
                [
                    "Step 1: Customs Offences (Part XVII EACCMA): Covers misdeclaration, concealment, undervaluation, outright smuggling, uncustomed goods conveyance, and unauthorized entry into customs areas. Download the guide at ura.go.ug/storage/2024/06/Customs-Offences.pdf.",
                    "Step 2: Conveyance & Prohibited Goods Offence: Using a vehicle to convey uncustomed goods attracts a fine up to USD 5,000 (Section 199). Section 200 EACCMA imposes imprisonment up to 5 years, fines equal to 50% of dutiable value, and mandatory forfeiture.",
                    "Step 3: Seizure Notice (Form C37): Proper customs officer conducts verification, records statements, deposits goods in the customs warehouse, and issues Seizure Notice Form C37 under Regulation 200 EACCMR 2010.",
                    "Step 4: Compounding Process (Section 219 EACCMA): Offender may admit liability by submitting Form C35 (Request to Settle Offence). Case officer compiles offence report, and compounding officer makes a legally binding ruling imposing taxes and compounding penalties.",
                    "Step 5: Court Settlement: Alternatively, offenders who decline compounding are prosecuted in the courts of law under Part XIX (Sections 220–228) EACCMA. Note that duty remains payable in addition to any fines.",
                ],
            )

        # 7. Non-Intrusive Inspection (NII) & Cargo Scanning
        if any(k in combined for k in ("non-intrusive inspection", "nii", "x-ray radiation", "radiation", "scan empty trucks", "how long does it take to scan", "does ura charge a fee for in order to scan", "foodstuffs", "baggage even when")):
            return (
                "URA Portal — Customs Enforcements: Non-Intrusive Inspection (NII) & Scanning",
                "X-ray scanner operations, 1-minute scan duration, radiation health safety standards, and 100% free scanning policy.",
                "info",
                [
                    "Step 1: 100% Free of Charge: URA does NOT charge any fee for scanning cargo or passenger baggage. NII scanning is completely free as an internal risk management control.",
                    "Step 2: Rapid Scan Duration: Non-Intrusive Inspection (NII) takes less than one (1) minute per truck or container, drastically cutting dwell times.",
                    "Step 3: Foodstuff Safety: X-ray radiation has zero effect on food safety, flavour, texture, or nutritional value; scanned food items remain 100% safe for human consumption.",
                    "Step 4: Personnel & Driver Safety: Scanner radiation levels strictly comply with national and international Atomic Energy Council safety limits; driver health is not put at risk.",
                    "Step 5: Mandatory Coverage: All passenger baggage at Entebbe Airport is scanned regardless of channel; empty transit trucks are scanned to instantly confirm they carry no concealed contraband.",
                ],
            )

        # Default: Customs Enforcement Overview
        return (
            "URA Portal — Customs Enforcement: Overview & Operations",
            "Anti-smuggling operations, transit monitoring, cargo scanning, and customs offence management framework.",
            "info",
            [
                "Step 1: Visit ura.go.ug/en/category/imports-exports/customs-enforcements/ for full enforcement guides, transit procedures, and prohibited goods lists.",
                "Step 2: Prohibited Goods: Completely banned items (used electronics, used fridges, toxic cosmetics, used tyres) attract forfeiture and fines under EACCMA.",
                "Step 3: Entebbe Airport Baggage: USD 500 personal duty-free allowance; goods exceeding USD 2,000 require a TIN and licensed customs clearing agent.",
                "Step 4: Transit Controls: 30-day window, USD 200 TGL license, 21 approved corridors, and free satellite tracking under RECTS.",
                "Step 5: NII Scanning: 100% free cargo and baggage inspection completed in under 1 minute under Atomic Energy Council radiation standards.",
                "Step 6: Contacts: Reach Customs Enforcement directly on 0323 442192 / 0323 442190 or Transit Monitoring Unit on 0323 442500.",
            ],
        )

    # Laws, Acts and Regulations Suite (https://ura.go.ug/download-category/laws-and-acts/)
    if portal_key == "laws_and_acts" or (
        any(
            k in combined
            for k in (
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
                "traffic and road safety act",
                "lotteries and gaming act",
            )
        )
        and portal_key not in ("tax_incentives", "motor_vehicle", "choose_tax_agent", "objection_appeals", "stamp_duty", "tax_clearance", "export_process", "customs_valuation", "single_customs_territory", "exempt_importation", "aeo", "customs_audits_refunds", "warehousing", "customs_enforcements", "double_taxation_agreements", "case_summary_reports", "court_of_appeal")
    ):
        # 1. Compendium of Domestic Tax Laws
        if any(k in combined for k in ("compendium for various domestic tax laws", "compendium", "domestic tax laws", "income tax act cap", "tax procedures code act 2014")):
            return (
                "URA Portal — Laws & Acts: Compendium of Domestic Tax Laws",
                "Consolidated statutes governing Income Tax, VAT, Tax Procedures Code, Stamp Duty, and Excise Duty in Uganda.",
                "info",
                [
                    "Step 1: Statutory Scope: The URA Domestic Tax Compendium consolidates core national tax statutes: Income Tax Act (Cap. 340), Value Added Tax Act (Cap. 349), Tax Procedures Code Act 2014, Stamp Duty Act 2014, and Excise Duty Act 2014.",
                    "Step 2: Annual Amendments: Includes all statutory revisions enacted under annual Finance Acts, detailing current tax rate tables, withholding schedules, and filing thresholds.",
                    "Step 3: Administrative Framework: Governed by the Tax Procedures Code Act (TPCA 2014), which standardizes tax registration, tax returns, assessments, objections, interest computation, and recovery across all domestic heads.",
                    "Step 4: Download Access: Available as full PDF compendiums at ura.go.ug/download-category/laws-and-acts/ for public legal and tax research.",
                ],
            )

        # 2. Convention on Mutual Administrative Assistance & AEOI / CRS Act 2023
        if any(k in combined for k in ("convention on mutual administrative assistance in tax matters", "automatic exchange of information act", "common reporting standard", "aeoi", "crs", "due diligence", "reporting obligations", "anti-avoidance")):
            return (
                "URA Portal — Laws & Acts: Mutual Administrative Assistance & AEOI Act 2023",
                "Implementation of the OECD Multilateral Convention and Common Reporting Standard (CRS) for international tax transparency.",
                "info",
                [
                    "Step 1: Statutory Purpose: The Convention on Mutual Administrative Assistance in Tax Matters (Implementation) Act 2023 (enacted August 1, 2023 / updated May 5, 2025) gives full domestic force of law to the OECD Multilateral Convention in Uganda.",
                    "Step 2: Automatic Exchange of Information (AEOI): Section 3 & 4 domesticate the Multilateral Competent Authority Agreement on Automatic Exchange of Financial Account Information and the Common Reporting Standard (CRS).",
                    "Step 3: Due Diligence & Reporting (Sections 5 & 6): Reporting Financial Institutions (commercial banks, custodial institutions, investment funds) must perform mandatory due diligence to identify non-resident financial accounts and report balances annually to URA.",
                    "Step 4: Offences & Anti-Avoidance (Sections 7 & 8): Imposes strict penalties on financial institutions for failure to report or false reporting, and empowers the Commissioner General to nullify artificial tax avoidance schemes.",
                ],
            )

        # 3. Tax Appeals Tribunal Act (Cap. 345 & Section 15 30% Deposit Rule)
        if any(k in combined for k in ("the tax appeals tribunals act", "tax appeals tribunal act", "tat act", "section 15 of the tax appeals", "30% of the tax in dispute", "tat dispute", "commercial division of the high court")):
            return (
                "URA Portal — Laws & Acts: Tax Appeals Tribunals Act (Cap. 345)",
                "Statutory framework for independent tax adjudication, 30-day appeal limits, and Section 15 tax deposit requirements.",
                "info",
                [
                    "Step 1: Statutory Mandate: Established under the Tax Appeals Tribunals Act (Cap. 345) pursuant to Article 152(3) of the 1995 Constitution as an independent specialized tax court.",
                    "Step 2: Jurisdiction: Hears and determines disputes between taxpayers and URA arising from tax assessments, taxation decisions, and objection rulings across all tax heads.",
                    "Step 3: Filing Deadline: Applications for review must be lodged with the TAT registry within thirty (30) days from the date of receipt of the taxation decision or objection notice.",
                    "Step 4: Mandatory 30% Tax Deposit (Section 15): Before an appeal can be entertained, the taxpayer must pay 30% of the tax assessed in dispute or the undisputed tax portion (whichever is greater) to URA via PRN.",
                    "Step 5: Judicial Hierarchy: Decisions of the Tribunal are binding decrees; appeals on questions of law lie to the High Court of Uganda (Commercial Division) within 30 days.",
                ],
            )

        # 4. Procedure Manual for Duty Remission Regulations (EAC 2008 & Section 140)
        if any(k in combined for k in ("duty remission regulations", "procedure manual for application of the duty remission", "duty remission manual", "duty remission committees", "section 140", "eaccma section 140")):
            return (
                "URA Portal — Laws & Acts: Duty Remission Regulations & Procedure Manual",
                "Operationalization of customs duty remission for manufacturing inputs under EAC Regulations 2008 and EACCMA Section 140.",
                "info",
                [
                    "Step 1: Legal Basis: Governed by the EAC Customs Duty Remission Regulations 2008 enacted under Section 140 of the East African Community Customs Management Act 2004.",
                    "Step 2: Operational Manual: Sets out the statutory procedure manual developed by the EAC Council of Ministers for processing and administering duty remission on manufacturing inputs across Partner States.",
                    "Step 3: Institutional Roles: Spells out the mandates and evaluation criteria of the National and Regional Duty Remission Committees, vetting raw material requirements and production capacities.",
                    "Step 4: Gazette Publication: Manufacturers granted duty remission, approved input quotas, and reduced tariff rates (e.g. 0% or 10%) are published annually by the EAC Council in the official East African Community Gazette.",
                ],
            )

        # 5. East African Community Customs Management Act (EACCMA 2004) & CET 2022
        if any(k in combined for k in ("the east african community customs management act", "eaccma 2004", "customs management act 2004", "common external tariff 2022", "cet 2022", "4-band tariff", "four-band")):
            return (
                "URA Portal — Laws & Acts: EACCMA 2004 & Common External Tariff (CET 2022)",
                "Regional customs legal code governing imports, exports, warehousing, enforcement, and the 4-band tariff structure.",
                "info",
                [
                    "Step 1: Regional Legal Code: EACCMA 2004 governs all customs operations across the East African Community (Kenya, Uganda, Tanzania, Rwanda, Burundi, South Sudan, and DRC).",
                    "Step 2: Common External Tariff (CET 2022 Version): Operationalizes the EAC 4-band ad valorem tariff structure: Band 1: 0% (raw materials and capital machinery); Band 2: 10% (intermediate goods); Band 3: 25% (finished consumer goods); Band 4: 35% (maximum tariff for sensitive and locally produced industrial goods).",
                    "Step 3: Rules of Origin Rules 2015: Governs intra-EAC trade preferences requiring goods to be wholly produced or achieve a minimum 35% regional value addition to move duty-free.",
                    "Step 4: COMESA Protocol on Rules of Origin 2015: Regulates duty-free access across COMESA Member States under simplified certificates of origin.",
                ],
            )

        # 6. Anti-Money Laundering Act 2013 & Amendment Regulations 2023
        if any(k in combined for k in ("anti-money laundering act 2013", "anti-money laundering", "aml act", "aml regulations", "financial intelligence authority", "fia", "suspicious transaction", "cash transaction report")):
            return (
                "URA Portal — Laws & Acts: Anti-Money Laundering Act & Regulations 2023",
                "Statutory obligations, Financial Intelligence Authority (FIA) compliance, and suspicious transaction reporting.",
                "info",
                [
                    "Step 1: Legal Framework: Anti-Money Laundering Act 2013 and the Anti-Money Laundering (Amendment) Regulations 2023, aimed at combating money laundering, terrorist financing, and illicit financial flows.",
                    "Step 2: Accountable Persons & Reporting Entities: Imposes mandatory compliance on banks, microfinance institutions, forex bureaus, casinos, real estate agents, accountants, and advocates.",
                    "Step 3: Customer Due Diligence (CDD): Entities must identify beneficial ownership, maintain customer verification records for at least 10 years, and report Large Cash Transactions (CTRs exceeding USD 10,000 / UGX 20M).",
                    "Step 4: Suspicious Transaction Reports (STRs): Mandatory filing of STRs to the Financial Intelligence Authority (FIA) within 2 working days of forming suspicion.",
                ],
            )

        # 7. The East African Tax Law Reports & Judicial Precedents
        if any(k in combined for k in ("the east african tax law report volume iv", "east african tax law report", "tax law report", "tax case law", "judicial precedents", "court judgments")):
            return (
                "URA Portal — Laws & Acts: The East African Tax Law Reports (Volume IV)",
                "Compilation of landmark tax judgments, High Court decisions, and regional Tax Appeals Tribunal jurisprudence.",
                "info",
                [
                    "Step 1: Publication Scope: The East African Tax Law Report Volume IV (published December 10, 2024) is an authoritative legal compendium compiling precedent tax rulings and judgments from Uganda, Kenya, Tanzania, and regional appellate courts.",
                    "Step 2: Judicial Precedents: Features definitive rulings on corporate tax residency, permanent establishment, withholding tax on cross-border management fees, VAT input credit verification, and customs valuation fallback disputes.",
                    "Step 3: Case Research: Widely utilized by legal practitioners, tax consultants, and judicial officers to support tax dispute litigation and statutory interpretation.",
                    "Step 4: Access: Download Volume IV and earlier jurisprudence editions directly from ura.go.ug/en/download/the-east-african-tax-law-report-volume-iv/.",
                ],
            )

        # 8. Value Added Tax (Amendment) Act, 2023
        if any(k in combined for k in ("value added tax (amendment) act", "vat amendment act", "vat amendment 2023", "non-resident digital services", "efris input tax claim")):
            return (
                "URA Portal — Laws & Acts: Value Added Tax (Amendment) Act, 2023",
                "Key statutory amendments covering non-resident digital services VAT, EFRIS electronic invoicing, and exempt supplies.",
                "info",
                [
                    "Step 1: Legislative Publication: Enacted August 1, 2023 and updated August 29, 2023 amending the VAT Act (Cap. 349).",
                    "Step 2: Non-Resident Digital Services: Mandates foreign suppliers of electronic services (cloud, streaming, advertising, social media) to register for VAT and collect 18% tax from consumers in Uganda.",
                    "Step 3: EFRIS Invoicing Mandatory (Section 24): Taxpayers must obtain electronic fiscal receipts or invoices generated through EFRIS to support input tax deduction claims.",
                    "Step 4: Exempt & Zero-Rated Supplies: Realigns VAT exemptions on cooking gas (LPG), medical oxygen, and agricultural inputs.",
                    "Step 5: Download: Available at ura.go.ug/en/download/value-added-tax-amendment-act-2023/.",
                ],
            )

        # 9. Stamp Duty Act 2014 & Key Exemptions
        if any(k in combined for k in ("stamp duty act 2014", "stamp duty act", "stamp duty rates", "fixed duty of ugx 15,000", "1% on transfer")):
            return (
                "URA Portal — Laws & Acts: Stamp Duty Act 2014",
                "Statutory rates on legal instruments, property transfers, mortgages, and strategic investment exemptions.",
                "info",
                [
                    "Step 1: Statutory Framework: Enacted in 2014 and updated August 25, 2023 imposing stamp duty on legal and commercial instruments in Uganda.",
                    "Step 2: Standard Fixed Rates: Fixed duty of UGX 15,000 on agreements, affidavits, powers of attorney, caveats, and corporate articles.",
                    "Step 3: Ad Valorem Rates: 1% stamp duty applied on transfer of land/property, transfer of shares, mortgages, and debentures.",
                    "Step 4: Statutory Exemptions: Complete exemption for strategic investments exceeding USD 50 million (foreign) or USD 10 million (domestic), and intra-group company transfers.",
                    "Step 5: Direct PDF: Download the full Act at ura.go.ug/storage/2023/08/Stamp-duty-Act-2014.pdf.",
                ],
            )

        # 10. Excise Duty Act 2014 & Digital Tax Stamps (DTS)
        if any(k in combined for k in ("excise duty act 2014", "excise duty act", "digital tax stamps act", "section 19b", "schedule 2 excisable goods")):
            return (
                "URA Portal — Laws & Acts: Excise Duty Act 2014",
                "Excise duty schedules on manufactured goods, telecommunications, mobile money, and Section 19B Digital Tax Stamps.",
                "info",
                [
                    "Step 1: Statutory Framework: Enacted in 2014 and updated August 25, 2023 governing duties on locally manufactured and imported excisable goods.",
                    "Step 2: Section 19B Digital Tax Stamps (DTS): Requires physical stamps with scannable QR codes affixed to beer, spirits, wine, soft drinks, bottled water, tobacco, cement, and sugar.",
                    "Step 3: Rates & Services: 12% on telecom airtime and data, 0.5% on mobile money withdrawals, and specific fuel rates (UGX 1,450/L petrol, UGX 1,130/L diesel).",
                    "Step 4: Returns Filing: Monthly excise returns must be submitted by the 15th of the following month via the URA e-Tax portal.",
                    "Step 5: Direct PDF: Download the full statute at ura.go.ug/storage/2023/08/The-Excise-Duty-Act-2014-.pdf.",
                ],
            )

        # 11. Traffic and Road Safety (Amendment) (No. 2) Act, 2023
        if any(k in combined for k in ("traffic and road safety act", "traffic and road safety amendment", "14-day statutory timeline", "environmental levy")):
            return (
                "URA Portal — Laws & Acts: Traffic and Road Safety Amendment Act 2023",
                "Motor vehicle licensing modernization, environmental levy adjustments, and mandatory 14-day ownership transfer.",
                "info",
                [
                    "Step 1: Publication: Enacted and published August 1, 2023 amending the Traffic and Road Safety Act 1998.",
                    "Step 2: Registration & Licensing: Modernizes fee schedules for initial vehicle registration, commercial road service permits, and personalized number plates.",
                    "Step 3: Environmental Levy: Revises environmental levy schedules applied on imported used passenger motor vehicles based on age from manufacture.",
                    "Step 4: 14-Day Ownership Transfer: Imposes strict statutory obligation to register change of motor vehicle ownership within 14 days of sale.",
                    "Step 5: Download: Available at ura.go.ug/en/download/traffic-and-road-safety-act-1998-amendment-no-2-act-2023/.",
                ],
            )

        # 12. Lotteries and Gaming (Amendment) Act, 2023
        if any(k in combined for k in ("lotteries and gaming (amendment) act", "lotteries and gaming amendment", "lotteries and gaming act", "gaming winnings", "section 118c", "30% withholding tax on gaming")):
            return (
                "URA Portal — Laws & Acts: Lotteries and Gaming Amendment Act 2023",
                "Gaming tax landscape, 30% withholding tax on winnings, and daily electronic data transmission to URA and NLGRB.",
                "info",
                [
                    "Step 1: Publication: Published August 1, 2023 to regulate betting revenue and enhance transparency in gaming operations.",
                    "Step 2: Section 118C Withholding Tax: Raised the withholding tax rate on betting and gaming winnings from 15% to thirty percent (30%).",
                    "Step 3: Operator Gaming Tax: Imposes a 30% gaming tax on gross gaming revenue (GGR) of licensed casino and betting operators.",
                    "Step 4: Automated Data Transmission: Mandates electronic integration of betting systems with URA and NLGRB for real-time bet and payout tracking.",
                    "Step 5: Download: Available at ura.go.ug/en/download/lotteries-and-gaming-amendment-act-2023/.",
                ],
            )

        # 13. The Free Zones Act 2014 & Tax Incentives
        if any(k in combined for k in ("free zones act 2014", "free zones act", "uganda free zones authority", "ufza", "10-year income tax holiday", "80% export")):
            return (
                "URA Portal — Laws & Acts: The Free Zones Act 2014",
                "Legal regime for Special Economic Zones, Export Processing Zones, and statutory tax incentives.",
                "info",
                [
                    "Step 1: Statutory Framework: Enacted in 2014 establishing the Uganda Free Zones Authority (UFZA) to regulate export-oriented Free Zones.",
                    "Step 2: Tax Holidays: Developers and operators enjoy a 10-year corporate income tax holiday on manufacturing and processing profits.",
                    "Step 3: Customs & VAT Relief: 0% import duty and 0% VAT on capital plant, machinery, spare parts, and imported raw materials.",
                    "Step 4: Export Threshold: Enterprises must export at least eighty percent (80%) of manufactured output outside the domestic customs territory.",
                    "Step 5: Download: Available at ura.go.ug/en/download/the-free-zones-act-2014/.",
                ],
            )

        # 14. COMESA & EAC Rules of Origin Protocols 2015
        if any(k in combined for k in ("comesa protocol on rules of origin", "eac customs union (rules of origin)", "rules of origin 2015", "simplified trade regime", "simplified certificate of origin", "regional value content")):
            return (
                "URA Portal — Laws & Acts: EAC & COMESA Rules of Origin Protocols 2015",
                "Preferential trade rules, value addition criteria (35% RVC), and Simplified Certificate of Origin (SCO) up to USD 2,000.",
                "info",
                [
                    "Step 1: Regulatory Scope: Governs duty-free trade under the EAC Customs Union Rules of Origin 2015 and COMESA Protocol on Rules of Origin 2015.",
                    "Step 2: Originating Criteria: Goods must be wholly produced, undergo change in tariff heading (CTH), or achieve a minimum Regional Value Content (RVC) of 35%.",
                    "Step 3: COMESA File: Dedicated 1.32 MB COMESA Protocol covers change of tariff heading workings and economic development priority goods (25% threshold).",
                    "Step 4: Simplified Trade Regime (STR): Cross-border small traders carrying qualifying goods up to USD 2,000 use an EAC Simplified Certificate of Origin at border posts.",
                    "Step 5: Download: Available at ura.go.ug/en/download/comesa-protocol-on-rules-of-origin-2015/ and ura.go.ug/en/download/the-east-african-community-customs-union-rules-of-origin-rules-2015/.",
                ],
            )

        # Default: Laws, Acts and Regulations Repository Overview
        return (
            "URA Portal — Laws, Acts & Regulations: Overview & Repository",
            "Official legal repository hosting primary tax statutes, regional customs acts, EAC protocols, and procedural manuals.",
            "info",
            [
                "Step 1: Visit ura.go.ug/download-category/laws-and-acts/ to browse and download all active revenue statutes, regulations, and manuals.",
                "Step 2: Domestic Tax Statutes: Download the consolidated Compendium for Domestic Tax Laws (Income Tax, VAT, TPCA, Stamp Duty, Excise Duty).",
                "Step 3: Regional Customs Framework: Access EACCMA 2004, the Common External Tariff (CET 2022 Version), EAC Duty Remission Regulations, and Rules of Origin Protocols.",
                "Step 4: Transparency & Appeals: Download the Tax Appeals Tribunal Act, AEOI/CRS Act 2023, Anti-Money Laundering Regulations, and East African Tax Law Reports.",
                "Step 5: Public Access: All statutory documents are available free of charge in downloadable PDF formats without login requirements.",
            ],
        )

    # Double Taxation Agreements Suite (https://ura.go.ug/en/category/legal-policy/double-taxation-agreements/)
    if portal_key == "double_taxation_agreements" or (
        any(
            k in combined
            for k in (
                "double taxation agreement",
                "double taxation agreements",
                "double-taxation-agreements",
                "dta",
                "dtas",
                "income tax treaty",
                "tax treaty",
                "tax treaties",
                "delegated competent authorities",
                "delegated competent authority",
                "mutual agreement procedure",
            )
        )
        and portal_key not in ("tax_incentives", "motor_vehicle", "choose_tax_agent", "objection_appeals", "stamp_duty", "tax_clearance", "export_process", "customs_valuation", "single_customs_territory", "exempt_importation", "aeo", "customs_audits_refunds", "warehousing", "customs_enforcements", "laws_and_acts", "case_summary_reports", "court_of_appeal")
    ):
        # 1. Delegated Competent Authorities Directory
        if any(k in combined for k in ("delegated competent authorities", "delegated competent authority", "competent authorities in tax matters", "eoi_gp", "john r. musinguzi", "agnes nabwire", "david dongo", "julius nkwasire", "exchange of information office")):
            return (
                "URA Portal — Double Taxation Agreements: Delegated Competent Authorities Directory",
                "Designated and delegated officials responsible for international Exchange of Information (EOI) and treaty administration.",
                "info",
                [
                    "Step 1: Statutory Purpose: Published August 30, 2023 by Charles Male designating competent authorities for Exchange of Information (On Request, Spontaneous, and Automatic).",
                    "Step 2: Central Competent Authority: Mr. John R. Musinguzi, Commissioner General URA (Tel: +256-323-442042 / +256-417-442042, Email: jmusinguzi@ura.go.ug, Generic EOI: EOI_GP@ura.go.ug).",
                    "Step 3: Authorized Contact Persons: Mrs. Agnes Nabwire Asobola (Commissioner Tax Investigations, anabwire@ura.go.ug, +256-323-442076); Mr. Julius Nkwasire (Assistant Commissioner Intelligence, jnkwasire@ura.go.ug, +256-323-443819); Mr. David Dongo (Manager Exchange of Information, ddongo@ura.go.ug, +256 417 442 221).",
                    "Step 4: Location: URA Headquarters, Plot M193/M194, Nakawa Industrial Area, P. O. Box 7279, Kampala.",
                    "Step 5: Portal Access: Full directory available at ura.go.ug/en/delegated-competent-authorities/.",
                ],
            )

        # 2. India – Uganda Income Tax Treaty (2004)
        if any(k in combined for k in ("india – uganda", "india - uganda", "india income tax treaty", "india dta")):
            return (
                "URA Portal — DTA: India – Uganda Income Tax Treaty (2004)",
                "Bilateral tax convention governing cross-border withholding tax caps, PE rules, and commercial traffic.",
                "info",
                [
                    "Step 1: Treaty Status: Concluded 30 April 2004, entered into force 27 August 2004, effective 1 April 2005 (India) and 1 July 2005 (Uganda).",
                    "Step 2: Permanent Establishment (Article 5): Building sites, construction or assembly projects constitute a PE if lasting more than six (6) months.",
                    "Step 3: Withholding Tax Caps: Dividends (Article 10) capped at 10%; Interest (Article 11) capped at 10% (Government/Central Bank fully exempt); Royalties and Fees for Technical Services (Article 12) capped at 10%.",
                    "Step 4: Shipping & Air Transport (Article 8): Taxable solely in the Contracting State of the enterprise's residence.",
                    "Step 5: Access: Hosted at ura.go.ug/en/india-uganda-income-tax-treaty-2004/.",
                ],
            )

        # 3. South Africa – Uganda Income Tax Treaty (1997)
        if any(k in combined for k in ("south africa – uganda", "south africa - uganda", "south africa income tax treaty", "south africa dta")):
            return (
                "URA Portal — DTA: South Africa – Uganda Income Tax Treaty (1997)",
                "Bilateral convention governing South African normal tax and secondary tax on companies vs. Ugandan corporate tax.",
                "info",
                [
                    "Step 1: Treaty Status: Concluded 27 May 1997, entered into force 9 April 2001, effective 1 January 2002.",
                    "Step 2: Permanent Establishment (Article 5): Construction, installation, assembly, or supervisory activities lasting more than six (6) months constitute a PE.",
                    "Step 3: Dividends (Article 10): 10% if the beneficial owner holds at least 25% of company capital; 15% in all other cases.",
                    "Step 4: Interest & Royalties: Interest (Article 11) capped at 10% (Government exempt); Royalties (Article 12) capped at 10%; Technical Fees (Article 13) capped at 10%.",
                    "Step 5: Access: Hosted at ura.go.ug/en/south-africa-uganda-income-tax-treaty-1997/.",
                ],
            )

        # 4. Mauritius – Uganda Income Tax Treaty (2003)
        if any(k in combined for k in ("mauritius – uganda", "mauritius - uganda", "mauritius income tax treaty", "mauritius dta")):
            return (
                "URA Portal — DTA: Mauritius – Uganda Income Tax Treaty (2003)",
                "Bilateral tax treaty with Mauritius covering corporate holding structures, Service PE, and technical consultancy fees.",
                "info",
                [
                    "Step 1: Treaty Status: Concluded 19 September 2003, entered into force 21 July 2004, effective 1 July 2005.",
                    "Step 2: Permanent Establishment (Article 5): Building sites lasting > 6 months; Service PE triggered if consultancy services aggregate > 4 months in any 12-month period.",
                    "Step 3: Withholding Tax Caps: Interest (Article 11) capped at 10% (Government exempt); Royalties (Article 12) capped at 10%; Technical Fees (Article 13) capped at 10% (taxpayer can elect net basis taxation under Article 7).",
                    "Step 4: LOB Compliance: Strict verification under Section 88(5) ITA to prevent treaty shopping via Mauritian shell entities.",
                    "Step 5: Access: Hosted at ura.go.ug/en/mauritius-uganda-income-tax-treaty-2003/.",
                ],
            )

        # 5. Denmark – Uganda Income Tax Treaty (2000)
        if any(k in combined for k in ("denmark – uganda", "denmark - uganda", "denmark income tax treaty", "denmark dta", "danish tax")):
            return (
                "URA Portal — DTA: Denmark – Uganda Income Tax Treaty (2000)",
                "Bilateral convention governing Danish municipal/hydrocarbon taxes and Ugandan income tax.",
                "info",
                [
                    "Step 1: Treaty Status: Concluded 14 January 2000, entered into force 8 May 2001, effective 1 January 2002.",
                    "Step 2: Permanent Establishment (Article 5): Construction, assembly, installation, or supervisory/consultancy activities lasting > 6 months.",
                    "Step 3: Withholding Tax Caps: Dividends (Article 10) 10% (>= 25% holding) / 15% others (0% Government/Central Bank/NSSF); Interest (Article 11) 10% (Government/NSSF exempt); Royalties (Article 12) 10%; Management Fees (Article 13) 10%.",
                    "Step 4: SAS Consortium (Article 8): Aviation profits taxed in proportion to SAS Danmark A/S participation.",
                    "Step 5: Access: Hosted at ura.go.ug/en/denmark-uganda-income-tax-treaty-2000/.",
                ],
            )

        # 6. Netherlands – Uganda Income Tax Treaty (2004)
        if any(k in combined for k in ("netherlands – uganda", "netherlands - uganda", "netherlands income tax treaty", "netherlands dta", "dutch tax")):
            return (
                "URA Portal — DTA: Netherlands – Uganda Income Tax Treaty (2004)",
                "Bilateral treaty providing substantial dividend exemptions, 4-month Service PE, and 3-year loan interest relief.",
                "info",
                [
                    "Step 1: Treaty Status: Concluded 31 August 2004, entered into force 10 September 2006, effective 1 November 2006 (WHT), 1 January 2007 (Netherlands) and 1 July 2007 (Uganda).",
                    "Step 2: Permanent Establishment (Article 5): Construction project > 6 months; Service PE triggered if consultancy services aggregate > 4 months in any 12-month period.",
                    "Step 3: Dividends (Article 10): 0% (tax exempt) if holding directly >= 50% capital for post-treaty investments; 5% if holding < 50%; 15% in all other cases.",
                    "Step 4: Interest (Article 11): Capped at 10%, with full exemption for loans granted/guaranteed by Government/Central Bank or commercial banks/public institutions with repayment terms >= 3 years.",
                    "Step 5: Access: Hosted at ura.go.ug/en/netherlands-uganda-income-tax-treaty/.",
                ],
            )

        # 7. Norway – Uganda Income Tax Treaty (1999)
        if any(k in combined for k in ("norway – uganda", "norway - uganda", "norway income tax treaty", "norway dta", "norwegian tax")):
            return (
                "URA Portal — DTA: Norway – Uganda Income Tax Treaty (1999)",
                "Bilateral convention governing Norwegian national/municipal income taxes and Ugandan income tax.",
                "info",
                [
                    "Step 1: Treaty Status: Concluded 7 September 1999, entered into force 16 May 2001, effective 1 January 2002.",
                    "Step 2: Permanent Establishment (Article 5): Construction, installation, or supervisory/consultancy activities lasting > 6 months.",
                    "Step 3: Withholding Tax Caps: Dividends (Article 10) 10% (>= 25% holding) / 15% others (0% Petroleum Fund/Government/NSSF); Interest (Article 11) 10% (Government/Petroleum Fund/Eksportfinans/NSSF exempt); Royalties (Article 12) 10%; Management Fees (Article 13) 10%.",
                    "Step 4: SAS Airline Rule (Article 8): Aviation profits apportioned to SAS Norge ASA share.",
                    "Step 5: Access: Hosted at ura.go.ug/en/norway-uganda-income-tax-treaty-1999/.",
                ],
            )

        # 8. United Kingdom – Uganda Income Tax Treaty (1992)
        if any(k in combined for k in ("united kingdom", "uk – uganda", "uk - uganda", "great britain", "uk income tax treaty", "uk dta")):
            return (
                "URA Portal — DTA: United Kingdom – Uganda Income Tax Treaty (1992)",
                "Bilateral tax treaty covering UK income, corporation, and capital gains taxes vs. Ugandan income tax.",
                "info",
                [
                    "Step 1: Treaty Status: Concluded 23 December 1992, entered into force 21 December 1993, effective 1 January 1994 (Uganda) and April 1994 (UK).",
                    "Step 2: Permanent Establishment (Article 5): Building site, construction, or installation project lasting more than 183 days.",
                    "Step 3: Withholding Tax Caps: Dividends (Article 10) capped at 15%; Interest (Article 11) capped at 15% (Government exempt); Royalties (Article 12) capped at 15%; Technical Fees (Article 13) capped at 15%.",
                    "Step 4: Capital Gains (Article 14): Real estate and immovable property alienation taxed in the source state.",
                    "Step 5: Access: Hosted at ura.go.ug/en/uganda-united-kingdom-income-tax-treaty/.",
                ],
            )

        # 9. Section 88 ITA Treaty Relief, Form DT-1 & LOB Rules
        if any(k in combined for k in ("section 88", "treaty relief", "limitation on benefits", "lob", "anti-treaty shopping", "tax residence certificate", "trc", "form dt-1", "form dt1", "mutual agreement procedure", "map")):
            return (
                "URA Portal — DTA: Treaty Relief Procedures, Form DT-1 & LOB Compliance",
                "Statutory procedure to claim reduced treaty withholding rates and prevent treaty shopping under Section 88 ITA.",
                "info",
                [
                    "Step 1: Treaty Supremacy (Section 88(1) ITA): Valid international tax agreements prevail over domestic income tax laws to the extent of inconsistency.",
                    "Step 2: Limitation on Benefits (Section 88(5) ITA): Treaty benefits are strictly denied if 50% or more of the resident entity is owned by non-residents of the treaty country, unless substantive active operations exist.",
                    "Step 3: Tax Residence Certificate (TRC): Foreign payees must submit an authenticated TRC issued by their home tax authority for the current year.",
                    "Step 4: URA Form DT-1: Withholding agents must complete Form DT-1 and secure approval from the URA International Tax Unit before applying treaty concessionary rates.",
                    "Step 5: Mutual Agreement Procedure (MAP): Disputes regarding double taxation or transfer pricing adjustments are resolved via bilateral competent authority consultations.",
                ],
            )

        # Default: Double Taxation Agreements Overview
        return (
            "URA Portal — Double Taxation Agreements: Overview & Treaty Network",
            "Repository of bilateral tax conventions, concessionary withholding rates, and international tax transparency agreements.",
            "info",
            [
                "Step 1: Visit ura.go.ug/en/category/legal-policy/double-taxation-agreements/ to view all active bilateral treaties.",
                "Step 2: Active Treaties: Uganda has active DTAs with the UK, South Africa, Norway, Denmark, Mauritius, India, and the Netherlands.",
                "Step 3: Withholding Concessions: Review specific treaty articles for reduced rates on dividends, interest, royalties, and technical/management fees.",
                "Step 4: Competent Authorities: Exchange of information and treaty dispute resolutions are managed by the Delegated Competent Authorities (Nakawa HQ).",
                "Step 5: Documentation: Prepare URA Form DT-1 and certified Tax Residence Certificates to claim treaty relief.",
            ],
        )

    # Case Summary Reports Suite (https://ura.go.ug/download-category/case-summery-reports/)
    if portal_key == "case_summary_reports" or (
        any(
            k in combined
            for k in (
                "case summary report",
                "case summary reports",
                "case-summery-reports",
                "case digest",
                "ura case digest",
                "compendium eac tax cases",
            )
        )
        and portal_key not in ("tax_incentives", "motor_vehicle", "choose_tax_agent", "objection_appeals", "stamp_duty", "tax_clearance", "export_process", "customs_valuation", "single_customs_territory", "exempt_importation", "aeo", "customs_audits_refunds", "warehousing", "customs_enforcements", "laws_and_acts", "double_taxation_agreements", "court_of_appeal", "debt_collections", "financial_intelligence_authority")
    ):
        # 1. URA Case Digest Volume XI (Jul - Dec 2025)
        if any(k in combined for k in ("volume xi", "vol xi", "volume 11", "vol 11", "jul - dec 2025", "july - dec 2025", "38 decisions", "38 landmark")):
            return (
                "URA Portal — Case Summary Reports: URA Case Digest Volume XI (Jul - Dec 2025)",
                "Authoritative collection of 38 judicial decisions delivered between July and December 2025 across domestic and customs taxes.",
                "info",
                [
                    "Step 1: Publication Scope: Published April 13, 2026 (13 MB PDF, 588 downloads) covering 38 decisions of the courts and Tax Appeals Tribunal.",
                    "Step 2: Precedent Topics: Corporate tax residency (Section 14 ITA), mandatory EFRIS invoicing for VAT input credit (Section 24 VAT Act), and Section 15 TAT 30% tax deposits.",
                    "Step 3: Criminal & International: Covers criminal tax evasion prosecutions and digital services tax assessments for non-resident platforms.",
                    "Step 4: Direct Download: Download the full volume at ura.go.ug/storage/2026/04/The-URA-Case-Digest-Volume-XI.pdf.",
                ],
            )

        # 2. Case Digest Vol. VIII (Jan - Mar 2024)
        if any(k in combined for k in ("vol. viii", "volume viii", "vol 8", "volume 8", "jan- mar 2024", "jan - mar 2024")):
            return (
                "URA Portal — Case Summary Reports: Case Digest Volume VIII (Jan - Mar 2024)",
                "Quarterly compilation of landmark High Court and TAT rulings for Q3 FY 2023/24.",
                "info",
                [
                    "Step 1: Publication Scope: Published April 30, 2024 (12 MB PDF, 1,757 downloads) compiling key tax dispute decisions.",
                    "Step 2: Core Decisions: Covers input VAT claims on non-remitted supplier invoices, Section 24 TPCA objection timelines, and agency notice validity under Section 43 TPCA.",
                    "Step 3: Customs & Employment: Features Common External Tariff (CET) reclassifications and PAYE benefit-in-kind rulings.",
                    "Step 4: Access: Hosted at ura.go.ug/en/download/case-digest-vol-iii-2024/.",
                ],
            )

        # 3. URA Case Digest Vol VI & Vol V (2023 Editions)
        if any(k in combined for k in ("vol vi", "volume vi", "vol 6", "volume 6", "july – sept 2023", "vol v", "volume v", "vol 5", "volume 5", "april - june 2023")):
            return (
                "URA Portal — Case Summary Reports: Case Digest Volumes V & VI (2023 Editions)",
                "Precedent rulings on non-resident withholding tax, PE determinations, bad debt deductibility, and voluntary disclosure.",
                "info",
                [
                    "Step 1: Volume VI (July - Sept 2023): Published November 1, 2023 (6 MB, 838 downloads at ura.go.ug/storage/2023/11/Case-Digest-Volume-VI-version-4.pdf) covering non-resident management fees and DTA permanent establishment disputes.",
                    "Step 2: Volume V (April - June 2023): Published October 30, 2023 (4 MB, 626 downloads at ura.go.ug/storage/2023/10/10722_Case_Digest_Volume_V_Version_3.pdf) covering Section 24 ITA bad debts, intra-group stamp duty exemptions, and transit cargo route seizures.",
                    "Step 3: Procedural Immunity: Explains judicial applications of Section 66 TPCA voluntary disclosure penalty relief.",
                ],
            )

        # 4. Compendium of EAC Tax Cases
        if any(k in combined for k in ("compendium eac tax cases", "compendium of eac tax cases", "eac tax cases", "regional tax cases")):
            return (
                "URA Portal — Case Summary Reports: Compendium of EAC Tax Cases",
                "Regional comparative tax jurisprudence across East African Community Partner States.",
                "info",
                [
                    "Step 1: Scope: Published August 1, 2023 (1.67 MB, 655 downloads at ura.go.ug/en/download/compendium-eac-tax-cases/).",
                    "Step 2: Judicial Focus: Compiles landmark tax decisions from Uganda, Kenya, Tanzania, and Rwanda regional courts.",
                    "Step 3: Key Themes: EAC Common External Tariff (CET) disputes, rules of origin authentication, SCT cross-border transfers, and intercompany transfer pricing.",
                ],
            )

        # Default: Case Summary Reports Overview
        return (
            "URA Portal — Case Summary Reports: Overview & Legal Digests",
            "Curated case digests providing tax practitioners and the public with transparent judicial precedents.",
            "info",
            [
                "Step 1: Visit ura.go.ug/download-category/case-summery-reports/ to browse and download all Case Digest volumes.",
                "Step 2: Available Compilations: Download Case Digest Volume XI (Jul - Dec 2025), Volume VIII (2024), Volumes I-VI (2022-2023), and the EAC Tax Cases Compendium.",
                "Step 3: Practical Utility: Utilize judicial interpretations of Section 24 TPCA, EFRIS invoicing, 30% TAT deposits, and customs valuation to guide tax dispute litigation.",
                "Step 4: Public Access: All case summary reports are free of charge in downloadable PDF formats.",
            ],
        )

    # Court of Appeal Suite (https://ura.go.ug/download-category/court-of-appeal/)
    if portal_key == "court_of_appeal" or (
        any(
            k in combined
            for k in (
                "court of appeal",
                "court of appeals",
                "celtel uganda ltd",
                "gulindwa paul",
                "civil appeal 22 of 2006",
            )
        )
        and portal_key not in ("tax_incentives", "motor_vehicle", "choose_tax_agent", "objection_appeals", "stamp_duty", "tax_clearance", "export_process", "customs_valuation", "single_customs_territory", "exempt_importation", "aeo", "customs_audits_refunds", "warehousing", "customs_enforcements", "laws_and_acts", "double_taxation_agreements", "case_summary_reports", "debt_collections", "financial_intelligence_authority")
    ):
        # 1. Celtel Uganda Ltd vs URA (Civil Appeal No. 22 of 2006)
        if any(k in combined for k in ("celtel", "celtel uganda ltd vs ura", "civil appeal 22 of 2006", "airtime vouchers", "distributor discounts", "interconnection fees")):
            return (
                "URA Portal — Court of Appeal: Celtel Uganda Ltd vs URA (Civil Appeal 22 of 2006)",
                "Landmark appellate decision determining taxable value for excise duty and VAT on telecom airtime vouchers and discounts.",
                "info",
                [
                    "Step 1: Case Details: Civil Appeal No. 22 of 2006 between Celtel Uganda Limited and Uganda Revenue Authority (8 MB PDF at ura.go.ug/en/download/celtel-uganda-ltd-vs-ura-civil-appeal-22-of-2006-2/).",
                    "Step 2: Taxable Value of Airtime: Established whether excise duty and VAT should be computed on the face value of airtime scratch cards or the discounted price received from distributors.",
                    "Step 3: Distributor Discount Holding: Settled the tax treatment of commercial trade discounts versus promotional airtime under the Value Added Tax Act.",
                    "Step 4: Interconnection Charges: Clarified taxability and input credit recovery on telecom network interconnection traffic.",
                ],
            )

        # 2. Gulindwa Paul v Uganda (Criminal Tax Fraud Precedent)
        if any(k in combined for k in ("gulindwa", "gulindwa paul", "gulindwa paul v ug", "criminal tax fraud", "customs smuggling precedent")):
            return (
                "URA Portal — Court of Appeal: Gulindwa Paul v Uganda (Criminal Tax Fraud)",
                "Landmark criminal appellate judgment establishing evidentiary thresholds and imprisonment sanctions for customs fraud.",
                "info",
                [
                    "Step 1: Case Details: Criminal Appeal precedent in Gulindwa Paul v Uganda (6 MB PDF at ura.go.ug/en/download/gulindwa-paul-v-ug/).",
                    "Step 2: Evidentiary Standard: Affirmed that in criminal prosecutions for fraudulent evasion of customs duty under EACCMA, the prosecution must prove fraudulent intent (mens rea) beyond reasonable doubt.",
                    "Step 3: Document Falsification: Upheld convictions for forged invoices, falsified bills of lading, and fraudulent declaration entries.",
                    "Step 4: Penal Consequences: Reaffirmed custodial sentences and restitution orders for economic crimes against public revenue.",
                ],
            )

        # Default: Court of Appeal Tax Jurisprudence Overview
        return (
            "URA Portal — Court of Appeal: Landmark Tax Jurisprudence Overview",
            "Repository of binding appellate precedents governing statutory interpretation and judicial review of tax disputes.",
            "info",
            [
                "Step 1: Visit ura.go.ug/download-category/court-of-appeal/ to access binding Court of Appeal tax rulings.",
                "Step 2: Binding Authority: Under the Constitution, Court of Appeal decisions strictly bind the High Court, Chief Magistrate Courts, the Tax Appeals Tribunal, and URA tax administration.",
                "Step 3: Prominent Cases: Download full transcripts of Celtel Uganda Ltd vs URA (Civil Appeal 22 of 2006) and Gulindwa Paul v Uganda.",
                "Step 4: Appellate Path: Appeals on questions of law from the High Court Commercial Division lie to the Court of Appeal within 30 days.",
            ],
        )

    # Debt Collections Suite (https://ura.go.ug/download-category/debt-collections/)
    if portal_key == "debt_collections" or (
        any(
            k in combined
            for k in (
                "debt collection",
                "debt collections",
                "the debt collection function",
                "tax arrears",
                "distress proceedings",
                "agency notice",
                "agency notices",
                "garnishee",
                "departure prohibition",
                "temporary closure of business premises",
                "memorandum of understanding for instalment",
                "dcu mou",
            )
        )
        and portal_key not in ("tax_incentives", "motor_vehicle", "choose_tax_agent", "objection_appeals", "stamp_duty", "tax_clearance", "export_process", "customs_valuation", "single_customs_territory", "exempt_importation", "aeo", "customs_audits_refunds", "warehousing", "customs_enforcements", "laws_and_acts", "double_taxation_agreements", "case_summary_reports", "court_of_appeal", "financial_intelligence_authority")
    ):
        # 1. Demand Notice & Distress Proceedings (Section 40 & 41 TPCA)
        if any(k in combined for k in ("section 40", "section 41", "demand notice", "distress proceedings", "distress warrant", "seize goods", "court bailiff", "auction goods")):
            return (
                "URA Portal — Debt Collections: Demand Notices & Distress Proceedings (Section 40 & 41 TPCA)",
                "Statutory procedure for serving 14-day demand notices, issuing distress warrants, and seizing taxpayer assets.",
                "info",
                [
                    "Step 1: Section 40 Demand Notice: Where tax remains unpaid after the due date, URA serves a formal demand notice requiring settlement within 14 days.",
                    "Step 2: Section 41 Distress Warrant: If the taxpayer defaults, the Commissioner issues a distress warrant authorizing court bailiffs to seize goods, chattels, and equipment.",
                    "Step 3: Asset Storage: Seized property is held at the taxpayer's cost for up to 10 days before public auction if the debt remains unpaid.",
                    "Step 4: Release Condition: Property is released immediately upon full payment of the tax debt plus statutory bailiff execution fees.",
                ],
            )

        # 2. Temporary Closure of Business Premises (Section 42 TPCA)
        if any(k in combined for k in ("section 42", "temporary closure", "closure of business premises", "seal premises", "padlock", "breaking seals")):
            return (
                "URA Portal — Debt Collections: Temporary Closure of Business Premises (Section 42 TPCA)",
                "Powers to padlock and seal non-compliant business premises for up to 14 days to enforce revenue recovery.",
                "info",
                [
                    "Step 1: Statutory Authority: Under Section 42 TPCA, authorized URA officers accompanied by police officers may enter and seal business premises.",
                    "Step 2: Duration: Premises remain padlocked and sealed for a maximum statutory window of fourteen (14) days.",
                    "Step 3: Offence of Breaking Seals: Breaking, tampering with, or removing URA revenue seals without authorization is a criminal offence punishable by imprisonment or heavy fines.",
                    "Step 4: De-sealing Procedure: Premises are de-sealed only upon full debt liquidation or signing an approved instalment MOU with the Debt Collection Unit.",
                ],
            )

        # 3. Agency Notices / Bank Garnishee Orders (Section 43 TPCA)
        if any(k in combined for k in ("section 43", "agency notice", "agency notices", "garnishee", "frozen bank account", "freeze bank", "third party debtor")):
            return (
                "URA Portal — Debt Collections: Agency Notices & Bank Garnishee Orders (Section 43 TPCA)",
                "Statutory notices served on commercial banks, employers, and debtors directing direct payment of funds to URA.",
                "info",
                [
                    "Step 1: Issuance: Served under Section 43 TPCA on any third party (e.g. commercial bank, corporate client, employer) holding funds for the defaulting taxpayer.",
                    "Step 2: Absolute Priority: Agency notices take legal priority over all other liens, mortgages, or encumbrances against the funds.",
                    "Step 3: Third-Party Obligation: The recipient agent must remit the specified funds directly to URA within the timeline stated in the notice.",
                    "Step 4: Personal Liability: Any agent who fails to comply without lawful excuse becomes personally liable for the full tax debt stated in the notice.",
                ],
            )

        # 4. Departure Prohibition Orders (Section 45 TPCA)
        if any(k in combined for k in ("section 45", "departure prohibition", "dpo", "prevent travel", "immigration control", "border exit")):
            return (
                "URA Portal — Debt Collections: Departure Prohibition Orders (Section 45 TPCA)",
                "Administrative travel bans restricting tax defaulters from departing Uganda through airports and land borders.",
                "info",
                [
                    "Step 1: Statutory Trigger: Issued where the Commissioner General has reasonable grounds to believe a defaulting taxpayer intends to leave Uganda without settling arrears.",
                    "Step 2: Service & Notification: The DPO is served directly on the taxpayer and transmitted to the Directorate of Citizenship and Immigration Control (DCIC).",
                    "Step 3: Border Enforcement: Enforced across Entebbe International Airport and all border control stations barring departure.",
                    "Step 4: Revocation: Revoked only when the tax debt is fully cleared or satisfactory security (bank guarantee / bond) is accepted by URA.",
                ],
            )

        # 5. Instalment Payment Agreements & DCU MOU (Section 47 TPCA)
        if any(k in combined for k in ("section 47", "instalment payment", "mou for instalment", "dcu mou", "payment agreement", "down payment", "debt collection unit")):
            return (
                "URA Portal — Debt Collections: Instalment Payment Agreements & DCU MOU",
                "Negotiation of structured tax arrears payment plans through binding Memoranda of Understanding with the Debt Collection Unit.",
                "info",
                [
                    "Step 1: Application: Taxpayers facing genuine financial hardship write a formal application to the Commissioner General requesting instalment clearance.",
                    "Step 2: Financial Assessment: DCU reviews tax records, bank statements, and cash flows to assess viable instalment capacity.",
                    "Step 3: Down Payment: Taxpayers must pay an upfront down payment (typically 20% to 30% of total arrears) before executing the MOU.",
                    "Step 4: Payment Window & Interest: Repayment schedules typically span 3 to 12 months; statutory late payment interest (2% simple interest per month) applies to unpaid balances.",
                    "Step 5: Revocation: Failure to pay any scheduled instalment immediately terminates the MOU and triggers summary enforcement.",
                ],
            )

        # Default: Debt Collections Function Overview
        return (
            "URA Portal — Debt Collections: Overview & Recovery Mandate",
            "Official manual and operational procedures governing tax arrears management, legal enforcement, and taxpayer compliance relief.",
            "info",
            [
                "Step 1: Visit ura.go.ug/download-category/debt-collections/ to download 'The Debt Collection Function' guide (66.51 KB).",
                "Step 2: Early Engagement: Taxpayers with tax arrears should immediately contact the Debt Collection Unit (Nakawa Tower) before enforcement escalates.",
                "Step 3: Structured MOUs: Eligible taxpayers can avert bank garnishee orders or property caveats by negotiating an instalment MOU.",
                "Step 4: Legal Framework: Debt recovery is governed under Part VIII (Sections 40-47) of the Tax Procedures Code Act 2014.",
            ],
        )

    # Financial Intelligence Authority Suite (https://ura.go.ug/download-category/financial-intelligence-authority/)
    if portal_key == "financial_intelligence_authority" or (
        any(
            k in combined
            for k in (
                "financial intelligence authority",
                "ml.tf risk assessment",
                "tax crimes and proceeds",
                "domestic tax evasion risk assessment",
                "aml/cft",
                "fia uganda",
            )
        )
        and portal_key not in ("tax_incentives", "motor_vehicle", "choose_tax_agent", "objection_appeals", "stamp_duty", "tax_clearance", "export_process", "customs_valuation", "single_customs_territory", "exempt_importation", "aeo", "customs_audits_refunds", "warehousing", "customs_enforcements", "laws_and_acts", "double_taxation_agreements", "case_summary_reports", "court_of_appeal", "debt_collections")
    ):
        # 1. National ML/TF Risk Assessment on Tax Crimes and Proceeds (July 18, 2025)
        if any(k in combined for k in ("ml.tf risk assessment", "ml/tf risk assessment", "risk assessment on tax crimes", "july 18, 2025", "world bank domestic tax evasion tool", "3 mb")):
            return (
                "URA Portal — Financial Intelligence Authority: ML/TF Risk Assessment on Tax Crimes and Proceeds",
                "National self-assessment conducted by Ugandan authorities using the World Bank Domestic Tax Evasion Risk Assessment Tool.",
                "info",
                [
                    "Step 1: Publication Scope: Published July 18, 2025 (3 MB PDF, 310 downloads at ura.go.ug/en/download/ml-tf-risk-assessment-on-tax-crimes-and-proceeds/). # gitleaks:allow",
                    "Step 2: World Bank Methodology: Conducted as a national self-assessment using the Domestic Tax Evasion Risk Assessment Tool developed by the World Bank Group.",
                    "Step 3: Primary Threat Areas: Highlights money laundering risks from missing trader VAT carousel fraud, trade-based misinvoicing (TBML), cash-intensive shadow economy, and illicit financial flows (IFFs).",
                    "Step 4: Compliance Alignment: Guides institutional coordination between URA Tax Investigations, FIA, ODPP, CID, and Bank of Uganda.",
                ],
            )

        # 2. Inter-Agency Collaboration & Asset Recovery
        if any(k in combined for k in ("inter-agency", "asset recovery", "predicate crime", "odpp", "cid", "bank of uganda", "financial intelligence reports", "fir")):
            return (
                "URA Portal — Financial Intelligence Authority: Inter-Agency Taskforce & Asset Recovery",
                "Multi-agency operational framework between URA, FIA, ODPP, and CID to prosecute tax fraud and recover illicit assets.",
                "info",
                [
                    "Step 1: Predicate Offence: Tax crimes are designated predicate offences to money laundering under the Anti-Money Laundering Act 2013.",
                    "Step 2: Financial Intelligence Reports: The FIA disseminates actionable financial intelligence reports (FIRs) and suspicious transaction disclosures to URA Tax Investigations.",
                    "Step 3: Joint Asset Forfeiture: Enables freezing of bank accounts and civil/criminal asset forfeiture under Section 46 TPCA and the AML Act.",
                    "Step 4: Inter-Agency Taskforce: Coordinates joint investigations between URA, FIA, CID, ODPP, and Bank of Uganda.",
                ],
            )

        # 3. Accountable Persons & Mandatory Reporting (CTR/STR)
        if any(k in combined for k in ("accountable persons", "cash transaction report", "ctr", "suspicious transaction report", "str", "usd 10,000", "ugx 20,000,000", "2 working days")):
            return (
                "URA Portal — Financial Intelligence Authority: Accountable Persons & Mandatory Reporting (CTR/STR)",
                "Statutory compliance obligations for financial institutions, forex bureaus, casinos, real estate agents, and legal practitioners.",
                "info",
                [
                    "Step 1: Customer Due Diligence (CDD): Accountable persons must identify customers, beneficial owners, and PEPs, retaining records for at least 10 years.",
                    "Step 2: Large Cash Reporting (CTR): Mandatory reporting of cash transactions equal to or exceeding USD 10,000 or UGX 20,000,000.",
                    "Step 3: Suspicious Transaction Reports (STR): Mandatory submission of STRs to the FIA within two (2) working days of forming suspicion via the goAML portal.",
                    "Step 4: Penal Sanctions: Non-compliance attracts administrative penalties up to UGX 100 million and criminal prosecution of compliance officers.",
                ],
            )

        # Default: Financial Intelligence Authority Overview
        return (
            "URA Portal — Financial Intelligence Authority: AML/CFT Framework Overview",
            "Repository of national risk assessments, anti-money laundering regulations, and compliance guidelines.",
            "info",
            [
                "Step 1: Visit ura.go.ug/download-category/financial-intelligence-authority/ to download national risk assessments.",
                "Step 2: Download the ML/TF Risk Assessment on Tax Crimes and Proceeds (July 18, 2025, 3 MB).",
                "Step 3: Inter-Agency Collaboration: URA and FIA collaborate to detect tax evasion, track illicit financial flows, and enforce AML regulations.",
                "Step 4: Mandatory Compliance: Accountable entities must comply with CDD, CTR, and STR reporting obligations under the AML Act 2013.",
            ],
        )

    # Customs Systems Suite (https://ura.go.ug/en/category/tax-education/general-tax-information/customs-systems/)
    if portal_key == "customs_systems" or (
        any(
            k in combined
            for k in (
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
            )
        )
        and portal_key not in ("tax_incentives", "motor_vehicle", "choose_tax_agent", "objection_appeals", "stamp_duty", "tax_clearance", "export_process", "customs_valuation", "single_customs_territory", "exempt_importation", "aeo", "customs_audits_refunds", "warehousing", "customs_enforcements", "laws_and_acts", "double_taxation_agreements", "case_summary_reports", "court_of_appeal", "debt_collections", "financial_intelligence_authority")
    ):
        # 1. ASYCUDA World & Touchpoint Portal Installer
        if any(k in combined for k in ("asycuda world", "asycuda", "sycuda", "touchpoint", "touchpoint.ura.go.ug", "urgent notice to all customs clients", "customs system registration forms")):
            return (
                "URA Portal — Customs Systems: ASYCUDA World & Touchpoint Portal Access",
                "UNCTAD web-based customs declaration engine and Touchpoint access for installers and registration forms.",
                "info",
                [
                    "Step 1: System Purpose: ASYCUDA World (published June 24, 2024 by Okao Brian at ura.go.ug/en/sycuda/) processes 100% of Uganda's import, export, and transit declarations.",
                    "Step 2: Automated Risk Lanes: Automatically routes entries into Green (direct release), Blue (post-clearance audit), Yellow (DPC document review), and Red (physical inspection).",
                    "Step 3: Touchpoint Announcement: Under the March 10, 2025 Urgent Notice to All Customs Clients by Kamugisha Kabahweza Allan, the ASYCUDA World Installer and registration forms are hosted at touchpoint.ura.go.ug.",
                    "Step 4: System Access: Registered clearing agents, freight forwarders, and shipping lines configure Java runtime and access Asycuda via secure Touchpoint credentials.",
                ],
            )

        # 2. Uganda Electronic Single Window (UESW)
        if any(k in combined for k in ("uesw", "uganda electronic single window", "joint agency", "single window")):
            return (
                "URA Portal — Customs Systems: Uganda Electronic Single Window (UESW)",
                "Single-submission trade facilitation platform integrating URA and over 20 Other Government Agencies (OGAs).",
                "info",
                [
                    "Step 1: Framework: Published June 24, 2024 by Okao Brian (ura.go.ug/en/uesw/) enabling cross-border trade stakeholders to lodge standardized trade regulatory documents.",
                    "Step 2: Multi-Agency Integration: Seamlessly links URA with UNBS, NDA, MAAIF, Ministry of Trade, and local government authorities.",
                    "Step 3: Digital Clearances: Traders apply for import/export permits, phytosanitary certificates, and standards releases concurrently, minimizing border delays.",
                    "Step 4: Auction Platform: Also powers the automated customs public auction portal at singlewindow.go.ug/auction.",
                ],
            )

        # 3. Regional Electronic Cargo Tracking System (RECTS)
        if any(k in combined for k in ("rects", "regional electronic cargo tracking", "electronic smart seal", "satellite tracking")):
            return (
                "URA Portal — Customs Systems: Regional Electronic Cargo Tracking System (RECTS)",
                "Real-time GPS/satellite transit cargo monitoring across Uganda, Kenya, and Rwanda Northern Corridor routes.",
                "info",
                [
                    "Step 1: Operational Mandate: Published June 24, 2024 by Okao Brian (ura.go.ug/en/rects/) providing 24/7 monitoring of containerized transit cargo.",
                    "Step 2: Smart Electronic Seals: Affixed at Mombasa, Naivasha, or border points; free of charge for transit traders.",
                    "Step 3: Automated Geofencing: Real-time central control rooms trigger instant alarms if trucks divert from 21 gazetted routes or if seals are tampered with.",
                    "Step 4: Contact TMU: In case of transit distress or breakdown, contact Transit Monitoring Unit on 0323 442500.",
                ],
            )

        # 4. Non-Intrusive Inspection (NII)
        if any(k in combined for k in ("nii", "non-intrusive inspection", "drive-through scanner", "x-ray scanner", "atomic energy council")):
            return (
                "URA Portal — Customs Systems: Non-Intrusive Inspection (NII)",
                "High-speed drive-through and mobile X-ray scanning of containerized freight and passenger baggage in under 1 minute.",
                "info",
                [
                    "Step 1: Technology & Safety: Published June 21, 2024 by Sandra Kakooza (ura.go.ug/en/non-intrusive-inspection/) under Atomic Energy Council radiation safety approvals.",
                    "Step 2: Rapid Non-Destructive Scanning: Scans full trucks and containers without physical unpacking, completed in less than 60 seconds at major borders (Malaba, Busia, Mutukula, Katuna, Entebbe).",
                    "Step 3: Contraband Detection: Identifies concealed contraband, weapons, undeclared dutiable goods, and misclassified merchandise.",
                ],
            )

        # 5. Bonded Warehouse Information Management System (BWIMS)
        if any(k in combined for k in ("bwims", "bonded warehouse information management system", "bonded warehouse inventory")):
            return (
                "URA Portal — Customs Systems: Bonded Warehouse Information Management System (BWIMS)",
                "Digital inventory tracking and customs accounting across all licensed private and public bonded warehouses.",
                "info",
                [
                    "Step 1: Operational Strategy: Published June 21, 2024 by Sandra Kakooza (ura.go.ug/en/bwims/) under the Domestic Revenue Mobilization Strategy (DRMS).",
                    "Step 2: Stock Tracking: Real-time electronic accounting from cargo arrival (IM7 regime) to storage, internal bond transfer, and home consumption release (IM4).",
                    "Step 3: Automated Reconciliation: Reconciles stock balances with Asycuda World and flags overstayed cargo nearing statutory limits (6 months / 9 months).",
                ],
            )

        # 6. Naivasha ICD & Mombasa Port Customs Warehouse Clearance
        if any(k in combined for k in ("naivasha", "naivasha icd", "mombasa port", "overstayed cargo", "removal of goods in the customs warehouse")):
            return (
                "URA Portal — Customs Systems: Naivasha ICD & Mombasa Port Cargo Clearance",
                "Statutory 30-day removal notice for overstayed cargo under Section 42(1) EACCMA 2004.",
                "info",
                [
                    "Step 1: Statutory Authority: Published August 5, 2026 by Kamugisha Kabahweza Allan under Section 42(1) EACCMA 2004.",
                    "Step 2: Mandatory 30-Day Clearance: Importers with overstayed cargo at Naivasha Inland Container Depot or Mombasa Port must clear taxes and remove goods within 30 days.",
                    "Step 3: Consequence of Default: Unredeemed goods are scheduled for disposal via public online auction or private treaty direct sale.",
                ],
            )

        # Default: Customs Systems Overview
        return (
            "URA Portal — Customs Systems: Overview & Trade Facilitation Platforms",
            "Automated customs ecosystem integrating ASYCUDA World, UESW, RECTS, NII scanners, and BWIMS.",
            "info",
            [
                "Step 1: Visit ura.go.ug/en/category/tax-education/general-tax-information/customs-systems/ to access guides for all trade clearance platforms.",
                "Step 2: Cargo Declarations: Use ASYCUDA World installer and registration forms via touchpoint.ura.go.ug.",
                "Step 3: Joint Clearances: Lodge permits and licenses with other government agencies via the Uganda Electronic Single Window.",
                "Step 4: Transit Security: Benefit from free satellite tracking under RECTS along 21 approved corridors.",
            ],
        )

    # 1b. Track Application Status
    if portal_key == "track_status" or any(
        k in combined
        for k in (
            "track application",
            "track status",
            "check status of tin",
            "check application status",
            "track tin",
            "track my tin",
            "application search number",
        )
    ):
        if any(k in combined for k in ("require", "needed", "what do i need", "what is required")):
            return (
                "URA Portal — Track Application Requirements",
                "Inputs required to track a submitted TIN or tax registration application on the portal.",
                "info",
                [
                    "Requirement 1: Your Application Search Number / Reference Number (sent via SMS/email upon initial submission).",
                    "Requirement 2: The registered mobile phone number or email address provided during application.",
                    "Requirement 3: Entry of the on-screen security CAPTCHA code.",
                    "Cost: 100% Free of charge — tracking application status requires no login credentials or fee.",
                    "Step-by-step: Visit portal.ura.go.ug > e-Services > Track Application Status > enter Search Number and contact > view status.",
                ],
            )
        return (
            "URA Portal — Track Application Status Workflow",
            "Self-service tracking for submitted TIN, amendment, or waiver applications.",
            "info",
            [
                "Step 1: Go to ura.go.ug or portal.ura.go.ug and click e-Services > Track Application Status (or Quick Links > Track Status).",
                "Step 2: Enter your Application Search Number / Reference Number received via SMS or email.",
                "Step 3: Enter the registered mobile phone number or email address used during submission.",
                "Step 4: Enter the security CAPTCHA characters and click 'Search / Submit'.",
                "Step 5: The portal displays the current processing stage: 'Under Review', 'Approved - TIN Generated', or 'Additional Documents Required'.",
                "Step 6: If additional documents are requested by the officer, click the upload button to attach them directly.",
            ],
        )

    # 1c. Document Authentication
    if portal_key == "document_authentication" or any(
        k in combined
        for k in (
            "authenticate",
            "verify document",
            "document authentication",
            "check if genuine",
            "verify tcc",
            "verify certificate",
            "is tcc genuine",
        )
    ):
        if any(k in combined for k in ("require", "needed", "what do i need", "what is required")):
            return (
                "URA Portal — Document Authentication Requirements",
                "Inputs required to verify official URA certificates, notices, and tax receipts.",
                "info",
                [
                    "Requirement 1: The official Certificate Number or Document Reference Number printed on the certificate.",
                    "Requirement 2: Alternatively, the scannable QR Code printed on EFRIS receipts, TCCs, or Digital Tax Stamps (DTS).",
                    "Requirement 3: Security CAPTCHA verification on the portal screen.",
                    "Cost: Free public service — open to employers, banks, public procurement bodies, and citizens.",
                    "Step-by-step: Go to portal.ura.go.ug > e-Services > Document Authentication > input Certificate Number > click Verify.",
                ],
            )
        return (
            "URA Portal — Document Authentication Workflow",
            "Official online validation to confirm the genuineness of URA-issued certificates and receipts.",
            "info",
            [
                "Step 1: Go to ura.go.ug or portal.ura.go.ug and navigate to e-Services > Document Authentication.",
                "Step 2: Select the document type: Tax Clearance Certificate (TCC), WHT Exemption Certificate, Assessment Notice, or TIN Certificate.",
                "Step 3: Enter the Certificate Number or Document Reference Number exactly as printed.",
                "Step 4: Complete the security CAPTCHA and click 'Verify / Search'.",
                "Step 5: The portal verifies the record against the national ledger, displaying: Taxpayer Name, Issue Date, Expiry Date, and Official Status (Valid / Invalid / Expired).",
            ],
        )

    # 2. Print TIN Submitted Forms
    if any(k in combined for k in ("print tin", "submitted form", "reprint application", "print submitted", "acknowledgement receipt")):
        return (
            "URA Portal — Print TIN Submitted Forms",
            "Reprint official submitted registration forms and acknowledgement receipts.",
            "info",
            [
                "Step 1: Go to portal.ura.go.ug > e-Services > Taxpayer Registration > Print TIN Submitted Forms.",
                "Step 2: Select application category: Individual, Non-Individual, or Instant TIN.",
                "Step 3: Enter your Application Search Number / Reference Number (sent via SMS/email upon initial submission).",
                "Step 4: Enter the registered email address or mobile phone number used during application.",
                "Step 5: Complete CAPTCHA and click 'Print' to download your completed application PDF and receipt.",
            ],
        )

    # -------------------------------------------------------------------
    # Detailed verification of the Return Filing & Documentation Paths
    # -------------------------------------------------------------------

    # 2b. IT-Digital Service Tax Return Form for Non-Resident Service Providers
    if portal_key == "it_dst_return" or any(
        k in combined
        for k in (
            "it-dst",
            "it dst",
            "digital service tax return",
            "non-resident service providers return",
            "non-resident digital service return",
            "dst return",
        )
    ):
        return (
            "URA Portal — File IT-DST Return (Non-Resident Digital Services)",
            "Quarterly tax return under Section 86A of the Income Tax Act for foreign digital service providers.",
            "info",
            [
                "Step 1: Log in to portal.ura.go.ug with your Non-Resident Digital TIN and password.",
                "Step 2: Navigate to e-Services > Returns > Non-Resident Digital Services > File IT-DST Return.",
                "Step 3: Select the tax year and calendar quarter (Q1 due April 15, Q2 due July 15, Q3 due October 15, Q4 due January 15).",
                "Step 4: Enter gross revenue earned from digital services provided to consumers in Uganda.",
                "Step 5: The portal automatically calculates the 5% Digital Services Tax liability.",
                "Step 6: Submit the return to generate an official e-acknowledgement and payment PRN (payable in USD or UGX).",
            ],
        )

    # 2c. Download Online Return Forms
    if portal_key == "download_online_return_forms" or (
        portal_key not in ("construction_sector", "hospitality_sector", "agriculture_sector", "oil_and_gas", "health_sector", "corporation_tax", "vat")
        and any(
            k in combined
            for k in (
                "download online return",
                "online return forms",
                "download return templates",
                "excel return template",
                "macro template",
                "dt-1001",
                "dt-1014",
            )
        )
    ):
        return (
            "URA Portal — Download Online Return Forms",
            "Official macro-enabled Excel return templates for offline preparation before portal upload.",
            "info",
            [
                "Step 1: Go to portal.ura.go.ug > e-Services > Returns > Download Online Return Forms (no login required).",
                "Step 2: Select the required return schedule: Form DT-1001 (Income Tax), Form DT-1014 (VAT), Form DT-1004 (PAYE), or Form DT-1013 (WHT).",
                "Step 3: Click 'Download' to save the official macro-enabled Excel workbook (.xlsm/.xls).",
                "Step 4: Open Excel and click 'Enable Editing' and 'Enable Content' (macros are mandatory for validation).",
                "Step 5: Do NOT rename the downloaded template; paste values only without modifying formula cells or headers.",
                "Step 6: Click the yellow 'Validate' button on the summary sheet to generate the encrypted upload file for submission.",
            ],
        )

    # 2d. Download Manual Return Forms
    if portal_key == "download_manual_return_forms" or any(
        k in combined
        for k in (
            "download manual return",
            "manual return forms",
            "paper return forms",
            "presumptive return form",
        )
    ):
        return (
            "URA Portal — Download Manual Return Forms",
            "Printable PDF forms for presumptive small businesses, manual filings, and statutory notices.",
            "info",
            [
                "Step 1: Visit portal.ura.go.ug > e-Services > Returns > Download Manual Return Forms (or Domestic Taxes > Manual Forms).",
                "Step 2: Choose your manual form: Presumptive Income Tax Return (turnover 10M–150M), Form TR VII (Motor Vehicle Transfer), or Form DT-1015 (Notice of Objection).",
                "Step 3: Download and print the PDF document.",
                "Step 4: Fill in all particulars neatly in blue or black ink, sign the declaration, and attach required supporting evidence.",
                "Step 5: Physically submit the completed manual form to your designated URA domestic tax service centre.",
            ],
        )

    # 2d. Taxpayer Starter Pack
    if portal_key == "taxpayer_starter_pack" or any(k in combined for k in (
        "taxpayer starter pack",
        "starter pack",
        "taxpayer registration starter pack",
        "starter pack brochure",
    )):
        return (
            "URA Portal — Tax Education: Taxpayer Registration Starter Pack",
            "Comprehensive onboarding guide for newly registered taxpayers, account setup, return calendar, and payment channels.",
            "info",
            [
                "Step 1: Download the official 16-page Taxpayer Starter Pack booklet (package ID 62580) at ura.go.ug/download-category/taxpayer-registration-starter-pack/.",
                "Step 2: First-Time Account Setup: Visit ura.go.ug > Login, enter your 10-digit TIN as Login ID, use the default password sent to your registered email, and change your password.",
                "Step 3: Return Filing Deadlines: Provisional Individual returns due last day of 3rd month (4 installments); Companies due last day of 6th month (2 installments); Final income tax return due within 6 months after year-end; Monthly returns (PAYE, WHT, VAT, Excise) due by 15th of following month.",
                "Step 4: Payment Channels: Settle taxes using commercial banks, USSD code *285#, credit/debit cards (VISA, MasterCard, AmEx, UnionPay), Mobile Money, EFT, RTGS, or POS using an active PRN.",
                "Step 5: Taxpayer Rights & Obligations: Maintain proper records for at least 5 years, quote TIN on all commercial transactions, and apply for installment plans under Section 42 TPCA if experiencing liquidity constraints.",
            ],
        )

    # 2d2. Small Business (Presumptive) Taxpayers
    if portal_key == "small_business" or (
        portal_key not in ("health_sector", "business_formalisation", "tax_incentives", "corporation_tax", "wholesale_retail_sector")
        and any(k in combined for k in ("small business", "presumptive tax", "presumptive taxpayer", "taxes on small businesses", "small business taxpayer"))
    ):
        return (
            "URA Portal — Domestic Taxes: Small Business (Presumptive) Taxpayers",
            "Simplified turnover tax regime for resident businesses with annual gross sales between UGX 10,000,000 and UGX 150,000,000.",
            "info",
            [
                "Step 1: Verify eligibility: Resident businesses with annual turnover of UGX 10M–150M. Businesses under UGX 10M pay Nil tax. Professional services (medical, dental, engineering, legal, accounting, architecture, construction) are excluded.",
                "Step 2: Review tax rate schedule: UGX 10M–30M: 0.4% excess over 10M (or UGX 80k without records); UGX 30M–50M: UGX 80k + 0.5% excess over 30M (or UGX 200k without records); UGX 50M–80M: UGX 180k + 0.6% excess over 50M (or UGX 400k without records); UGX 80M–150M: UGX 360k + 0.7% excess over 80M (or UGX 900k without records).",
                "Step 3: Registration: Individual requires NIN; Non-individual requires URSB Certificate of Incorporation, Form 7, and director TINs.",
                "Step 4: File and Pay: Visit ura.go.ug > Make a payment > Select 'Income Tax for small business' > input TIN and turnover to generate PRN (payment doubles as return filing).",
                "Step 5: Settlement: Pay via bank, Mobile Money, PayWay, VISA/MasterCard, Ask URA App, or USSD code *285#. Download the guide at ura.go.ug/storage/2026/09/Small-Business-Taxpayer-FY-2026-27.pdf.",
            ],
        )

    # 2d3. Rental Income Tax (Section 5 ITA)
    if portal_key == "rental_income_tax" or any(k in combined for k in ("rental income tax", "rental tax", "rental income", "landlord tax", "property owner tax", "provisional rental return")):
        return (
            "URA Portal — Domestic Taxes: Rental Income Tax Guide & Filing (Section 5 ITA)",
            "Statutory provisions, separate source segregation, individual and corporate rental tax computation, and compliance deadlines.",
            "info",
            [
                "Step 1: Individual Landlords: Gross annual rent minus statutory threshold of UGX 2,820,000, taxed at twelve percent (12%). No other expense deductions are allowed.",
                "Step 2: Non-Individual Landlords (Companies, Trusts, Retirement Funds): Gross rent minus allowable expenses (capped at 50% of gross rent), taxed at standard corporation rate of thirty percent (30%).",
                "Step 3: Partnerships: Apportion gross rental income and calculate tax according to profit/loss sharing ratio in partnership deed.",
                "Step 4: Filing & Compliance: Provisional returns due within 3 months of financial year start for individuals (monthly filing optional) and 6 months for companies; final return due within 6 months after year-end via portal.ura.go.ug.",
                "Step 5: Mandatory EFRIS: Landlords must issue fiscalized e-invoices/e-receipts for rental payments received. Download the official guide at ura.go.ug/storage/2026/09/RENTAL-INCOME-TAX-2026-27.pdf.",
            ],
        )

    # 2d4. Taxation Handbook Archives
    if portal_key == "taxation_handbook" or any(k in combined for k in ("taxation handbook", "tax handbook", "a guide to taxation in uganda", "8th edition", "handbook archives")):
        return (
            "URA Portal — Tax Education: Taxation Handbook Archives",
            "Official compendium 'Taxation Handbook: A Guide to Taxation in Uganda' (8th Edition [2025-26], ISBN 978-9970-02-977-8).",
            "info",
            [
                "Step 1: Navigate to ura.go.ug/download-category/taxation-handbook/ under Schools Tax Curriculum & Tax Education Archives.",
                "Step 2: Download the current 8th Edition [2025-26] (120-page compendium, 4 MB PDF, package ID 66867, 8,782+ downloads).",
                "Step 3: Explore comprehensive statutory coverage: Part A (Background), Part B (Income Tax, Presumptive, CIT, Rental), Part C (VAT & EFRIS), Part D (Excise & DTS), Part E (Stamp Duty), Part F (Customs & SCT), and Part G (Procedures & Appeals).",
                "Step 4: Review annual Tax Amendments FY (2026-2027) booklet (4 MB PDF, 10,242+ downloads) for recent legislative changes.",
                "Step 5: Access historical editions (1st to 7th editions, including 2023-24 Chinese Edition) for audit and prior-year tax assessments.",
            ],
        )

    # 2d5. Touchpoint & Help Tool
    if portal_key == "help_tool" or (
        portal_key not in ("whistle_blow", "tin_registration", "dts", "tax_incentives", "get_refund", "stamp_duty", "choose_tax_agent", "objection_appeals", "motor_vehicle", "export_process", "single_customs_territory", "customs_valuation", "aeo", "warehousing", "customs_enforcements")
        and not any(k in combined for k in ("whistle", "informer", "informant"))
        and any(k in combined for k in ("help tool", "help-tool", "touchpoint help", "touchpoint portal", "book an appointment", "book appointment", "customer care", "toll free", "toll-free"))
    ):
        return (
            "URA Portal — Touchpoint & Help Tool: Customer Support Channels",
            "Self-service assistance, appointment booking, ticket management, and URA contact channels.",
            "info",
            [
                "Step 1: Access the URA Help Tool directly at ura.go.ug/en/help-tool/ or through touchpoint.ura.go.ug.",
                "Step 2: Contact Toll-Free Hotlines: 0800 117 000 / 0800 217 000 (Mon–Fri 8am–5pm) or WhatsApp +256 772 140 000.",
                "Step 3: Book an In-Person Appointment: Visit ura.go.ug/en/book-an-appointment/, select your service category and preferred regional office, and select a time slot.",
                "Step 4: Report Tax Evasion: Call the dedicated whistleblowing hotline at +256 (0)323442055 or email services@ura.go.ug.",
                "Step 5: Physical Service: Visit URA Headquarters at Plot M193/M194, Nakawa Industrial Area, Kampala, or any of the 60+ regional domestic and customs service centres.",
            ],
        )

    # 2d6. Corporation Tax (Company Income Tax)
    if portal_key == "corporation_tax" or (
        portal_key not in ("oil_and_gas", "health_sector", "business_formalisation", "tax_incentives", "agriculture_sector", "hospitality_sector", "wholesale_retail_sector", "construction_sector", "manufacturing_sector", "education_sector", "mining_sector", "entertainment_sector", "schools_curriculum", "real_estate_sector", "fishing_sector", "transport_sector", "government_agencies", "opportunities_portal", "research_publications")
        and any(k in combined for k in ("corporation tax", "corporate tax", "company tax", "company income tax", "corporate income tax"))
    ):
        return (
            "URA Portal — Domestic Taxes: Corporation Tax Guide & Return Filing",
            "Statutory 30% corporation tax for resident and non-resident companies, chargeable income computation, and provisional filing.",
            "info",
            [
                "Step 1: Statutory Scope: Imposed under the Income Tax Act on limited liability companies, companies limited by guarantee, associations, and NGOs at a standard thirty percent (30%) rate on net business profits.",
                "Step 2: Worldwide vs Source Taxation: Resident companies are taxed on worldwide income; non-resident companies are taxed on Ugandan-source business income.",
                "Step 3: Chargeable Income Computation: Determine gross trading turnover + other receipts (e.g. subletting space), deduct direct cost of sales and allowable operational/administrative overheads.",
                "Step 4: Return Calendar: Non-individual entities file two provisional returns (by the end of the 6th month and by the end of the financial year) and the final annual return within 6 months after year-end via Form DT-1001.",
                "Step 5: Audited Accounts: Entities with annual turnover exceeding UGX 500 million must file returns accompanied by audited financial statements certified by an ICPAU-registered accountant.",
            ],
        )

    # 2d7. Business Records & Statutory Bookkeeping (Section 15 TPCA)
    if portal_key == "business_records" or any(k in combined for k in ("business records", "record keeping", "keeping business records", "section 15 tpca", "rekod me biacara")):
        return (
            "URA Portal — Tax Education: Business Records & Statutory Bookkeeping",
            "Requirements for maintaining physical and electronic business records, retention periods, and language compliance under Section 15 TPCA.",
            "info",
            [
                "Step 1: Core Records Required: Maintain sales invoices, purchase receipts, contracts, bank statements, asset registers, stock ledgers, utility bills, and payroll sheets.",
                "Step 2: Statutory Language: Under Section 15 TPCA, records must be kept in English. Any taxpayer seeking to maintain books in another language or currency must apply in writing to the Commissioner General.",
                "Step 3: Five-Year Retention Rule: Preserve all transaction documents and tax receipts for at least five (5) years from the end of the tax period (or until proceedings conclude).",
                "Step 4: Mandatory TIN on Expenses: Every expense transaction exceeding UGX 5,000,000 must mandatorily record the Tax Identification Number (TIN) of the seller.",
                "Step 5: EFRIS Invoicing & Audited Books: VAT-registered businesses must issue fiscalized e-invoices via EFRIS, and businesses with turnover above UGX 500M must maintain audited books by an ICPAU accountant.",
            ],
        )

    # 2d8. Automatic Exchange of Information (AEOI & FAVD)
    if portal_key == "aeoi" or any(k in combined for k in (
        "automatic exchange of information",
        "aeoi",
        "common reporting standard",
        "foreign asset voluntary disclosure",
        "foreign asset disclosure",
        "favd",
        "fad form",
        "aeoi vdp",
    )):
        return (
            "URA Portal — Legal & Policy: Automatic Exchange of Information (AEOI & FAVD)",
            "Global tax transparency framework, CRS financial account reporting (aeoi.go.ug), and Foreign Asset Voluntary Disclosure.",
            "info",
            [
                "Step 1: Statutory Framework: Governed by the Convention on Mutual Administrative Assistance in Tax Matters (Implementation) Act 2023, enabling annual bulk financial account data exchanges with 125 partner jurisdictions effective September 2025.",
                "Step 2: Foreign Asset Voluntary Disclosure (FAVD): Resident taxpayers with undisclosed offshore accounts, foreign real estate, or foreign-source income (dividends, interest, royalties) can voluntarily regularize under 100% waiver of penalties and interest, and criminal immunity.",
                "Step 3: Download FAD Forms: Obtain 'FAD Form AEOI Individual Final' or 'FAD Form AEOI Non Individual Final' from ura.go.ug/download-category/foreign-asset-voluntary-disclosure/.",
                "Step 4: Submission: Submit completed FAD form with 3 years of bank statements, amended returns, and PRN payment proof via URA Touchpoint (touchpoint.ura.go.ug) under Service Request 'Foreign Asset Disclosure', or deliver to Commissioner Tax Investigations (14th Floor, URA Tower).",
                "Step 5: Reporting Financial Institutions (CRS Portal): Financial institutions must file annual CRS returns for the calendar year ending 31 December by 31st May of the following year via aeoi.go.ug (or file Nil returns if no reportable accounts exist). Inquiries: aeoi_inquiries@ura.go.ug.",
            ],
        )

    # 2d9. Taxation of Capital Gains (Section 18 ITA)
    if portal_key == "capital_gains" or any(k in combined for k in ("capital gains tax", "capital gain", "capital gains", "taxation of capital gains")):
        return (
            "URA Portal — Domestic Taxes: Taxation of Capital Gains (Section 18 ITA)",
            "Statutory provisions on disposal of non-depreciable business assets, shares, and commercial buildings, inflation indexation, and exemptions.",
            "info",
            [
                "Step 1: Statutory Definition: Under Section 18(1)(a) of the Income Tax Act (Section 18 ITA), capital gains arise on disposal of non-depreciable business assets (land, buildings), shares, and commercial property. Disposals by individuals are taxed as business or property income; corporate disposals are taxed at standard 30% corporation tax.",
                "Step 2: Computation: Capital Gain/Loss = Disposal proceeds received minus cost base. Capital losses are allowable as deductions against taxable income.",
                "Step 3: Inflation Adjustment (12+ Months): For business assets held for 12 months or more, index the cost base: Adjusted Cost Base = Original Cost (CB) x (CPID / CPIA), where CPID is CPI for sale month and CPIA is CPI for month before acquisition. Assets sold within 12 months receive no inflation relief.",
                "Step 4: Non-Recognized Disposals: No gain or loss is recognized on transfers between spouses, divorce settlements, involuntary conversions reinvested within 1 year in similar assets, or transmission on death to beneficiaries/trustees.",
                "Step 5: Filing & Guidance: Capital gains are declared in annual income tax returns (Form DT-1001). Download the official guide at ura.go.ug/storage/2024/06/FAQS-ON-TAXATION-OF-CAPITAL-GAINS.pdf.",
            ],
        )

    # 2d10. Documents Required at Point of Entry
    if portal_key == "documents_point_of_entry" or any(k in combined for k in (
        "documents required at port of entry",
        "documents at the point of entry",
        "point of entry documents",
        "port of entry documents",
    )):
        return (
            "URA Portal — Tax Education: Documents Required at Point of Entry",
            "Statutory documentation required for goods clearance, international travelers, and foreign corporate registration.",
            "info",
            [
                "Step 1: Goods Point of Entry Documents: (1) Bill of Lading (B/L) or Airway Bill (AWB); (2) Marine/Cargo Insurance Certificate; (3) Proforma Invoice; (4) Commercial Invoice; (5) Certificate of Origin; (6) Permits for Restricted Goods (Sections 3 & 4 External Trade Act); (7) Purchase Order; (8) Packing List; (9) Sales Contract; (10) Evidence of Payment (bank transfer / SWIFT slip).",
                "Step 2: International Travelers: Valid passport (>=6 months validity), Ugandan visa/e-Visa, Yellow Fever Vaccination Certificate (mandatory >1 yr old), return/onward ticket, and Passenger Baggage Declaration for goods exceeding USD 500/2,000 allowance.",
                "Step 3: Foreign Companies Entering Uganda: Certificate of Incorporation, Board resolution to register in Uganda, Memorandum & Articles of Association, list of directors/secretary, registered local office address, audited accounts, URA TIN, trading license, and UIA investment license.",
                "Step 4: Clearance Channels: Document verification is managed through the Uganda Electronic Single Window (UESW) and Document Processing Centre (DPC) at Nakawa.",
            ],
        )

    # 2d11. Employment Income & PAYE (Section 19 ITA)
    if portal_key == "employment_income" or any(k in combined for k in ("employment income", "taxes on employment income", "pay as you earn", "benefit in kind", "benefits in kind", "employee benefit", "employee benefits", "exempt employee")):
        return (
            "URA Portal — Domestic Taxes: Employment Income & PAYE Guide (Section 19 ITA)",
            "Statutory provisions on employee taxation, benefits in kind valuation, exempt employment benefits, and monthly PAYE filing.",
            "info",
            [
                "Step 1: Statutory Scope: Employment income encompasses wages, salaries, leave pay, overtime, fees, commissions, gratuities, bonuses, and non-cash benefits in kind provided to employees under Section 19 ITA.",
                "Step 2: Exempt Benefits: Pension, medical expense reimbursements, life insurance premiums by taxable employers, equal-terms meals on premises, employer retirement fund contributions, de minimis benefits (<UGX 10k/month), and 25% of terminal benefits for 10+ years' service.",
                "Step 3: Benefits in Kind Valuation: Motor vehicle benefit (20% x market value x days private / 365); domestic staff (total cost paid); residential housing; low-interest loans (difference between statutory BoU rate and actual rate).",
                "Step 4: Monthly Filing & Remittance: Employers must withhold PAYE and file monthly returns (Form DT-1004) by the 15th day of the following month via portal.ura.go.ug.",
                "Step 5: Official Guide: Download the complete guide at ura.go.ug/storage/2024/08/Employment-Income-English-2023-24.pdf.",
            ],
        )

    # 2d12. Gaming, Pool Betting, Casino & Sports Betting Tax
    if portal_key == "gaming_and_pool_betting" or any(k in combined for k in ("gaming tax", "betting tax", "pool betting", "casino operators", "sports betting", "gaming and pool betting", "taxation of gaming")):
        return (
            "URA Portal — Domestic Taxes: Gaming, Pool Betting, Casino & Sports Betting Tax",
            "Licensing with NLGRB, 30% gaming tax, 30% betting tax, 15% WHT on net winnings, and weekly return submission.",
            "info",
            [
                "Step 1: Regulatory Licensing: Operators must be licensed by the National Lotteries and Gaming Regulatory Board (NLGRB) and registered with URSB and URA before conducting gaming, casino, or betting operations.",
                "Step 2: Core Tax Rates: 30% Gaming Tax on gaming revenue (stakes less payouts); 30% Betting Tax on betting revenue; 15% Withholding Tax on net winnings (payout less stake) as a final tax under Section 118C ITA.",
                "Step 3: Filing Schedule: Weekly Gaming/Betting Return must be filed and payment remitted by Wednesday of the following week; Monthly summary return and WHT on winnings return due by the 15th of the following month.",
                "Step 4: General Taxes: Operators must register and account for PAYE (on staff salaries), 30% Corporation Tax on net business profits, and 18% VAT on auxiliary bar/restaurant services.",
                "Step 5: Download Official Guide: Download 'TAXATION OF THE GAMING, BETTING & LOTTERY IN UGANDA' (Vol. 1 Issue 1 FY 2026-27, 8 pages) at ura.go.ug/storage/2026/09/Gaming-and-Lottery-Manuscript-FY-2026-27.pdf.",
            ],
        )

    # 2d13. Value Added Tax (VAT) Guide & Thresholds
    if portal_key == "vat" or any(k in combined for k in ("value added tax", "vat registration threshold", "vat guide", "standard rated supply", "zero rated supply")):
        return (
            "URA Portal — Domestic Taxes: Value Added Tax (VAT) Guide & Thresholds",
            "Statutory provisions on VAT, compulsory UGX 300M threshold (UGX 75M quarterly), EFRIS e-invoicing, and 18% standard rate.",
            "info",
            [
                "Step 1: Statutory Scope: VAT is an indirect consumption tax charged at eighteen percent (18%) on taxable goods and services in Uganda and imports. Zero-rated supplies (0%) include exports, locally made drugs, and Ugandan milled cereals.",
                "Step 2: Registration Thresholds (FY 2026/27): Compulsory registration applies if taxable turnover exceeds UGX 75,000,000 in three (3) consecutive months or UGX 300,000,000 in twelve (12) months. Must apply within 20 days after the quarter.",
                "Step 3: Voluntary Registration: Permitted for businesses below the threshold if they maintain a fixed place of business, proper records, and qualify as fit-and-proper.",
                "Step 4: EFRIS Integration: All VAT-registered persons must issue e-invoices/e-receipts through EFRIS to fiscalize transactions and auto-populate monthly returns.",
                "Step 5: Filing & Deadlines: File monthly VAT returns (Form DT-1014) and remit tax by the 15th day of the following month via portal.ura.go.ug. Download the guide at ura.go.ug/storage/2026/09/VALUE-ADDED-TAX-FY-2026-27ENG-1.pdf.",
            ],
        )

    # 2d14. Non-Tax Revenues (NTR & ONTR)
    if portal_key == "non_tax_revenues" or (
        portal_key != "get_refund" and "refund" not in combined and any(k in combined for k in ("non-tax revenues", "non tax revenues", "non-tax revenue", "non tax revenue", "mda fees", "ontr payment"))
    ):
        return (
            "URA Portal — Tax Education: Non-Tax Revenues (NTR & ONTR Collections)",
            "URA payment registration and revenue collection framework on behalf of Government Ministries, Departments, and Agencies (MDAs).",
            "info",
            [
                "Step 1: URA Mandate: URA facilitates payment registration (PRN generation) and accounts for non-tax revenues collected on behalf of MDAs under MOFPED authority. URA does NOT deliver the actual physical services.",
                "Step 2: Partner MDAs: Ministry of Works (UDLS driving permits, motor vehicle fees), Ministry of Internal Affairs (passports, work permits, student passes, NGO registration), Police (firearms, EPS traffic fines), NIRA (National ID, birth/death certificates), Ministry of Lands (title registration, leases).",
                "Step 3: Generating PRN: Go to ura.go.ug > Make a Payment > Generate a payment slip; select tax head 'NTR' (for driving permits/police) or 'ONTR' (select specific MDA and service category from dropdown).",
                "Step 4: Foreign Currency Conversion: Fees denominated in foreign currencies (e.g. USD) are automatically converted to Uganda Shillings using the official URA daily exchange rate.",
                "Step 5: Payment Channels & Service Access: Settle PRN via bank, Mobile Money, PayWay, or debit/credit card; present the stamped/confirmed payment slip to the relevant MDA to receive your service.",
            ],
        )

    # 2d15. Health & Medical Sector Taxation Guide
    if portal_key == "health_sector" or any(k in combined for k in (
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
        return (
            "URA Portal — Tax Education: Health and Medical Sector Taxation Guide",
            "Comprehensive statutory tax guide and exemptions for hospitals, clinics, pharmacies, herbal shops, and pharmaceutical manufacturers.",
            "info",
            [
                "Step 1: Regulatory & Sector Landscape: Health sector comprises 66% public and 34% private providers (PNFP bureaus UCMB, UPMB, UOMB, UMMB and commercial PFPs). All facilities require URSB registration, NDA licensing, and professional accreditation (UMDPC/UNMC).",
                "Step 2: Subsector Tax Regimes: Herbal shops pay presumptive or individual income tax; Drug shops dispense Class C OTC items (min 1.5 km from pharmacies); Pharmacies pay 30% CIT, staff PAYE, and 6% WHT on supplies > UGX 1M; Pharmaceutical manufacturers pay 30% CIT with 100% duty exemptions on raw materials and medicament packaging.",
                "Step 3: Hospital Equipment Exemptions: Under EACCMA Fifth Schedule Part B, hospital goods marked with logo (shadowless lamps, blood freezers, mortuary units, ambulances, wheelchairs, gloves, hospital furniture) are 100% tax exempt.",
                "Step 4: Medical Diagnostic Equipment: Under EAC CET and VAT Act, X-ray machines, blood chemical analyzers, ultrasound machines, cardiographic gear, and dental equipment attract 0% import duty and are VAT exempt.",
                "Step 5: Major Investment Incentives: Developers investing at least USD 5M in referral-level specialized hospitals receive 100% stamp duty exemption and VAT-free construction/machinery; medical manufacturers investing USD 10M (foreign) or USD 300,000 (citizen) enjoy a 10-year income tax holiday.",
            ],
        )

    # 2d16. Business Formalisation Guide
    if portal_key == "business_formalisation" or any(k in combined for k in (
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
        return (
            "URA Portal — Tax Education: Business Formalisation Guide",
            "Official 3-step business formalisation roadmap, first-time portal account activation, and 13 core commercial benefits.",
            "info",
            [
                "Step 1: 3-Step Formalisation Roadmap: Step 1 (URSB business/company registration & certificate); Step 2 (URA online TIN registration with NIN or company documents); Step 3 (KCCA or municipal trading license, renewed annually).",
                "Step 2: 13 Core Commercial Benefits: Formal businesses gain eligibility for government/corporate tenders, commercial bank loans above UGX 50M, brand protection, limited liability, Tax Clearance Certificates (TCC), property transfers > UGX 10M, and URA tax refund rights.",
                "Step 3: First-Time Account Activation: Visit ura.go.ug > Login, input 10-digit TIN as Login ID, use temporary password from registration email, and immediately configure a strong password (letters, digits, symbols like January@2030).",
                "Step 4: Statutory Compliance & Filing: Provisional income tax returns, monthly VAT and PAYE returns by the 15th of the following month, and mandatory issuance of EFRIS fiscal receipts for VAT-registered firms.",
                "Step 5: Record Keeping Mandate: Retain complete commercial transaction records in English for at least five (5) years under Section 15 TPCA. Download the official guide at ura.go.ug/storage/2024/01/BUSINESS-FORMALISATION-ENGLISH-FY-2024-25.pdf.",
            ],
        )

    # 2d17. Petroleum, Oil & Gas Sector Guide & EACOP Regime
    if portal_key == "oil_and_gas" or any(k in combined for k in (
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
    )):
        return (
            "URA Portal — Tax Education: Petroleum, Oil & Gas Sector Guide (EACOP & Upstream/Downstream)",
            "Statutory tax rules, deemed VAT, EACOP special fiscal regime, and fuel Electronic Dispenser Controllers (EDCs).",
            "info",
            [
                "Step 1: Petroleum Value Chain & Licensing: Upstream (exploration/production), Midstream (EACOP/refinery), Downstream (marketing/fuel stations). Entities register with URSB, MEMD, PAU (National Supplier Database), and URA.",
                "Step 2: Core Tax Obligations: 30% CIT on net chargeable income, 10% WHT on non-resident upstream field contractors, 15% Branch Profit Tax on repatriated branch earnings, specific Excise Duty per liter of fuel, and PAYE for personnel.",
                "Step 3: Downstream Fuel Dispenser Mandate: Fuel service stations must install Electronic Dispenser Controllers (EDCs) connected to fuel pump nozzles to issue mandatory EFRIS fiscal e-receipts for all fuel sales.",
                "Step 4: Statutory Incentives & Deemed VAT: EACCMA Fifth Schedule Part B Item 30(a) duty-free import of specialized oil machinery (MEMD/PAU approved); Section 24(5) VAT Act deemed VAT on contractor supplies to licensees; uncapped loss carry-forward under Section 89GA ITA.",
                "Step 5: EACOP Dedicated Fiscal Regime: 10-year CIT holiday for EACOP Project Company; 5% WHT on non-resident technical fees; 0% VAT on pipeline transit and crude exports; nominal UGX 10,000 flat stamp duty on land and finance transfers; zero transit fees and aggregate USD 100,000 annual municipal levy cap.",
                "Step 6: Dedicated URA Administration: Petroleum Division at URA Nakawa Headquarters and dedicated fast-track EACOP Help Desk provide expedited customs clearance and accelerated VAT cash refunds.",
            ],
        )

    # 2d18. Agricultural Sector Guide & Farming Incentives
    if portal_key == "agriculture_sector" or any(k in combined for k in (
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
        return (
            "URA Portal — Tax Education: Agricultural Sector Guide (Crop, Poultry, Floriculture, Agro-Processing)",
            "Statutory tax rules, farming deductions under Section 35 ITA, 3-year startup holiday, and EACCMA agricultural customs exemptions.",
            "info",
            [
                "Step 1: Sector Scope & Taxability: Agriculture employs 68% of the labor force and contributes 24% of GDP. Farming is NOT exempt: commercial farmers pay tax on net business profits, with farm expenses fully deductible under Section 35 ITA.",
                "Step 2: Startup & Employment Incentives: Citizen agribusiness startups with capital under UGX 500 million enjoy a 3-year income tax holiday (effective 1 July 2025); employers with at least 5% PWD staff deduct 2% of income tax payable; agricultural supplies are 100% exempt from 6% WHT.",
                "Step 3: Floriculture & Greenhouse Allowances: Capital expenditure on horticultural plants and greenhouse construction (including land draining and clearing) qualifies for a 20% annual straight-line deduction across 5 consecutive years; flower exports are zero-rated (0% VAT).",
                "Step 4: Poultry & Livestock Exemption: Broiler/layer parent stock and breeding animals are 100% duty-free under EACCMA Fifth Schedule Part B for farmers (VAT-exempt for dealers); hatching eggs (strictly for day-old chicks) and animal feeds/premixes are VAT-exempt.",
                "Step 5: Machinery & Inputs: Agricultural tractors, hoes, ploughs, sprayers, refrigerated trucks, and dairy cans are duty-free under EACCMA; fertilizers, seeds, and agrochemicals attract 0% duty with MAAIF permit and 0% VAT.",
                "Step 6: Agro-Processing & Insurance: Agro-processors exporting 80%+ outside Uganda receive a 10-year income tax holiday under Section 21(1)(y) ITA; agricultural insurance policies are subject to NIL stamp duty. Download the 28-page guide (Vol. 1 Issue 4 FY 2025-26) at ura.go.ug/storage/2026/01/A-GUIDE-TO-TAXATION-OF-THE-AGRICULTURAL-SECTOR-VOL-1-ISSUE-4-FY-2025-2026.pdf.",
            ],
        )

    # 2d19. Hotel, Accommodation & Tourism Sector Guide
    if portal_key == "hospitality_sector" or any(k in combined for k in (
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
    )):
        return (
            "URA Portal — Tax Education: Hotel, Accommodation & Tourism Sector Guide (UHOA & EACCMA Incentives)",
            "Statutory tax rules, Local Hotel Tax (LHT), EACCMA Item 21 hotel logo duty exemptions, and safari vehicle incentives.",
            "info",
            [
                "Step 1: Regulatory & Sector Framework: Governed with Uganda Hotel Owners Association (UHOA) and UTB. Commercial operators must hold URSB incorporation, tourism license, municipal trading license, and URA TIN.",
                "Step 2: Local Hotel Tax (LHT): Statutorily collected from room occupants per night (4/5-star USD 2, 2/3-star UGX 2,000, mid-tier UGX 1,000, budget UGX 500) and remitted monthly to KCCA/local council; LHT is NOT recognized as hotel revenue or deductible expense.",
                "Step 3: EACCMA Item 21 Hotel Exemption: Goods permanently marked with hotel logo imported by licensed hotels (washing machines, cookers, kitchenware, fridges, air conditioning, cutlery, TVs, carpets, furniture, linen, gym equipment) are 100% tax exempt.",
                "Step 4: Tourism Transport Incentives: 4x4 safari vehicles, sightseeing buses (reclining coach seats, first aid), overland trucks (high clearance, mobile kitchen/tents), and tourism boats imported by licensed tour operators are duty-free under EACCMA Fifth Schedule.",
                "Step 5: Restaurants, Catering & Recreation: Standard 30% CIT (or presumptive tax for turnover <UGX 150M), mandatory EFRIS fiscal receipts for meals/beverages/admissions, and 6% WHT on outside catering contracts >UGX 1M.",
                "Step 6: Download Official Compendium: Download the 40-page joint URA–UHOA guide (Vol. 1 FY 2025-26) at ura.go.ug/storage/2025/10/A-guide-to-taxation-of-the-Hotel-and-Accomodation-Sector-2025-26.pdf.",
            ],
        )

    # 2d20. Wholesale & Retail Trade Sector Guide
    if portal_key == "wholesale_retail_sector" or any(k in combined for k in (
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
        return (
            "URA Portal — Tax Education: Wholesale & Retail Trade Sector Guide",
            "Comprehensive trade guidelines, presumptive turnover taxation, VAT compulsory vs voluntary rules, and EFRIS/DTS compliance.",
            "info",
            [
                "Step 1: Trader Categorization: 1) Small Business Presumptive (turnover UGX 10M-150M, daily average UGX 34,700); 2) VAT-Registered Traders (turnover >UGX 37.5M in 3 months or >UGX 150M in 12 months, or voluntary registration); 3) Standard Corporate Entities (30% CIT on net profit).",
                "Step 2: Presumptive Rules: Businesses under UGX 10M pay Nil tax; UGX 10M-150M pay final presumptive turnover tax based on statutory schedules (with or without books of accounts); tax payment serves as return filing.",
                "Step 3: VAT Mechanics & Input Tax Credit: 18% standard rate on taxable supplies, 0% on exports/locally milled cereals, exempt on raw foodstuffs/financial services; excess input credit >UGX 5,000,000 qualifies for URA cash refund or future tax offset.",
                "Step 4: Smart Solutions (EFRIS & DTS): All VAT-registered traders must issue e-invoices with a 20-digit FDN at checkout; wholesalers/retailers of gazetted goods (beers, spirits, wines, sodas, water, cigarettes, cement, sugar) must ensure units bear activated Digital Tax Stamps.",
                "Step 5: Capital Allowances & Customs: 50% initial allowance on plant & machinery, 20% on industrial buildings outside Kampala (50 km radius); imported goods attract CIF customs duty (CET 0-35%), 18% VAT, 6% WHT, and 1.5% infrastructure levy. Download the 36-page guide at ura.go.ug/storage/2024/06/Aguide-To-Taxation-Of-The-Wholesale-And-Retail-Sector22-23-1.pdf.",
            ],
        )

    # 2d21. Construction Sector Guide (Companies & Professionals)
    if portal_key == "construction_sector" or any(k in combined for k in (
        "construction sector",
        "construction companies",
        "construction professionals",
        "overview of the construction",
        "civil engineering",
        "building contractors",
        "surveying equipment",
        "architects registration",
        "engineers registration",
    )):
        return (
            "URA Portal — Tax Education: Construction Sector Guide (Companies & Professionals)",
            "Statutory tax rules for civil works, 6% WHT on contracts/design fees, Section 24(5) deemed VAT for aid projects, and machinery incentives.",
            "info",
            [
                "Step 1: Multi-Agency Licensing: Construction firms require URSB incorporation, local trading license, UNABCEC registration, and NEMA environmental approvals; professionals require ERB, ARB, or SRB licenses; TIN required for public tenders.",
                "Step 2: Income Tax & Return Calendar: 30% CIT on net chargeable profits (deducting plant hire, materials, casual wages, fuel); provisional returns filed by 6th and 12th months; professional consultants pay individual income tax (cannot pay presumptive tax).",
                "Step 3: Withholding Tax Rules: 6% WHT on construction contracts and materials >UGX 1M, 6% WHT on professional engineering/architectural fees, and 15% on non-resident consultants; compliant firms apply for 12-month WHT exemption.",
                "Step 4: VAT & Deemed VAT: 18% VAT with mandatory EFRIS fiscal receipts; Section 24(5) VAT Act deems VAT paid for contractors on aid-funded projects; developers of USD 5M+ specialized referral hospitals receive 100% VAT exemption on construction and design.",
                "Step 5: Customs & Capital Allowances: 0% import duty on cranes and surveying equipment (GPS/theodolites); duty-free tippers >20 tonnes GVW; temporary import bond for heavy machinery; 20% initial allowance for industrial buildings outside Kampala plus 5% annual depreciation.",
                "Step 6: Download Official Compendium: Download the 28-page guide (FY 2022-23) at ura.go.ug/storage/2024/02/CONSTRUCTION-SECTOR-2022-23-1.pdf.",
            ],
        )

    # 2d22. Manufacturing & Industrial Sector Guide
    if portal_key == "manufacturing_sector" or any(k in combined for k in (
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
        "raw materials for manufacture",
    )):
        return (
            "URA Portal — Tax Education: Manufacturing & Industrial Sector Guide",
            "Statutory tax rules for manufacturers, 10-year tax holidays in industrial parks, EAC duty remission, and DTS production line rules.",
            "info",
            [
                "Step 1: Regulatory Licensing & Setup: Mandatory URSB incorporation, UNBS certification / Distinctive Mark, NEMA approvals, UIA investment license for industrial parks, and URA TIN registration.",
                "Step 2: 10-Year Income Tax Holiday: Under Section 21(1)(y)/(z) ITA, manufacturers investing in Industrial Parks or Free Zones (min USD 10M foreign / USD 300,000 EAC citizen / USD 150,000 upcountry) using 50%+ local raw materials and 100+ Ugandan workers receive a 10-year income tax holiday; exporters of 80%+ finished goods also qualify.",
                "Step 3: EAC Duty Remission Scheme: Manufacturers can import approved industrial raw materials and packaging supplies at preferential 0% or 10% duty under gazetted EAC quotas; industrial machinery spare parts (Chapters 84 & 85) are exempt under EACCMA Fifth Schedule Item 31 (APC 492).",
                "Step 4: VAT & Plant Machinery: Importation or local supply of industrial plant and machinery is 0% duty and VAT-exempt / deemed paid; supply of locally produced raw materials to park operators is VAT-exempt; construction materials in parks bear Nil excise duty.",
                "Step 5: Digital Tax Stamps (DTS): Manufacturers of gazetted excisable goods (beer, spirits, wine, soda, bottled water, tobacco, cement, sugar) must affix activated digital tax stamps on every unit via automated line applicators; production volumes auto-reconcile with excise returns.",
                "Step 6: Download Official Compendium: Download the 64-page guide at ura.go.ug/storage/2025/10/A-GUIDE-TO-TAXATION-OF-THE-MANUFACTURING-SECTOR-FINAL.pdf and explore Investor Guides at ura.go.ug/download-category/investors-guides/.",
            ],
        )

    # 2d23. Education Sector Guide (Schools & Institutions)
    if portal_key == "education_sector" or any(k in combined for k in (
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
        return (
            "URA Portal — Tax Education: Education Sector Guide (Schools & Institutions)",
            "Statutory tax rules for schools, Section 21(1)(f) charitable exemptions, VAT on educational services, and scholastic materials.",
            "info",
            [
                "Step 1: Institutional Classification & Licensing: Governed by Ministry of Education and Sports (MoES) and URSB; schools require municipal permits and URA TIN; private commercial schools pay 30% CIT on net profit; non-profit charitable schools require written Commissioner exemption ruling under Section 21(1)(f) ITA.",
                "Step 2: Educational Services VAT Exemption: Provision of educational services (tuition, exams, boarding fees directly provided by schools) is strictly exempt under Schedule 2 VAT Act.",
                "Step 3: Scholastic & Scientific Materials: Textbooks, Bibles, Qu'rans, mathematical sets, geometry sets, crayons, lead pencils, rulers, erasers, and science chemicals are 100% VAT exempt; locally produced exercise books are VAT exempt; scientific research apparatus imported under Florence Agreement Annex D is duty-free under EACCMA Fifth Schedule.",
                "Step 4: Vocational Institute Investment Incentives: Investors establishing vocational/technical institutes (min capital USD 10M foreign / USD 300,000 citizen / USD 150,000 upcountry) using 70%+ local inputs and 70%+ EAC staff enjoy a 10-year income tax holiday, VAT-free design/inputs, nil stamp duty on land/debentures, and nil excise duty on construction.",
                "Step 5: Employment & Withholding Taxes: Monthly PAYE must be withheld on all teacher and administrative salaries; 6% WHT applies to school procurements of goods and services >UGX 1,000,000.",
                "Step 6: Download Official Compendium: Download the 28-page guide at ura.go.ug/storage/2025/10/TAXATION-OF-THE-EDUCATION-SECTOR.pdf.",
            ],
        )

    # 2d24. Mining & Mineral Extraction Sector Guide
    if portal_key == "mining_sector" or any(k in combined for k in (
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
        return (
            "URA Portal — Tax Education: Mining & Mineral Extraction Sector Guide",
            "Statutory rules for mineral extraction, mineral royalties, 10% subcontractor final WHT, 100% exploration depreciation, and sand/quarry permits.",
            "info",
            [
                "Step 1: Mineral Classification & Licensing: Governed under the Mining and Minerals Act across Precious Metals, Precious Stones, Base Metals, and Industrial Minerals (sand, stone, limestone, clay). Operators must hold valid DGSM/MEMD licenses, NEMA EIA approvals, and URA TIN.",
                "Step 2: Mineral Royalties & Corporate Tax: Mineral royalties are levied on gross market value of extracted minerals; mining companies pay 30% Corporation Tax on net chargeable income; sand extractors and stone quarries pay 30% CIT (or presumptive turnover tax for sales <UGX 150M).",
                "Step 3: Special Mining Deductions: 100% immediate depreciation write-off for exploratory depreciable assets; deduction for contributions to an approved mine rehabilitation fund; recovery of exploration work program costs; deduction of social infrastructure costs under mining leases.",
                "Step 4: Subcontractor Withholding Tax: Payments to subcontractors for mining operations are subject to a preferential 10% final Withholding Tax under the Income Tax Act (reduced from standard 15%).",
                "Step 5: Customs & Earthmoving Equipment: Specialized mining exploration and core drilling machinery is exempt from customs duty under EACCMA Fifth Schedule Part B Item 30 upon MEMD recommendation; excavators, bulldozers, and heavy tippers >20 tonnes GVW are duty-free by tariff.",
                "Step 6: Sand & Quarry Compliance: Riverbed dredging and rock quarrying require commercial DGSM extraction permits, NEMA environmental clearances, and EFRIS fiscal invoices for commercial sand and gravel sales.",
            ],
        )

    # 2d25. Entertainment & Public Events Sector Guide
    if portal_key == "entertainment_sector" or any(k in combined for k in (
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
        return (
            "URA Portal — Tax Education: Entertainment & Public Events Sector Guide",
            "Statutory tax rules for promoters, 15% final WHT on foreign performers, 6% on local artistes, 18% VAT on concert tickets, and studio allowances.",
            "info",
            [
                "Step 1: Regulatory Licensing & Setup: Events companies and promoters require URSB registration, municipal permits (KCCA/local council), and URA TIN; private commercial studios pay 30% CIT on net profit.",
                "Step 2: Withholding Tax on Performers: Promoters must withhold 15% final WHT on gross payments made to non-resident foreign entertainers; 6% WHT applies to payments made to local resident artistes regardless of payer's agent status.",
                "Step 3: VAT on Concerts & Gate Audits: 18% inclusive VAT on concert tickets and public event admissions for registered promoters (turnover >UGX 150M); event organizers must notify URA at least 14 days prior for gate ticket revenue verification.",
                "Step 4: Corporate Sponsorship: Promoters receiving corporate sponsorship funds or goods must issue an EFRIS VAT-inclusive fiscal invoice to the sponsor.",
                "Step 5: Production Studios: Commercial recording and video production studios claim accelerated depreciation on high-tech studio gear, mixing consoles, and cameras; all contracts must be fiscalized via EFRIS.",
                "Step 6: Download Official Compendium: Download the 20-page guide (Issue 1 Vol. 1 FY 2025-26) at ura.go.ug/storage/2025/10/TAXATION-OF-THE-ENTERTAINMENT-SECTOR.pdf.",
            ],
        )

    # 2d26. Schools Tax Curriculum & Educational Resources
    if portal_key == "schools_curriculum" or any(k in combined for k in (
        "a-level tax curriculum",
        "o-level tax curriculum",
        "schools tax curriculum",
        "a-level economics resource book",
        "entrepreneurship syllabus",
        "entrepreneurship education textbook",
    )):
        return (
            "URA Portal — Tax Education: Schools Tax Curriculum & Educational Resources",
            "National secondary school tax education syllabi, teacher manuals, student textbooks, and taxation handbook compendiums.",
            "info",
            [
                "Step 1: National Curriculum Integration: Developed jointly with NCDC and MoES to embed taxation and civic tax literacy into secondary school Economics, Commerce, and Entrepreneurship subjects.",
                "Step 2: A-Level Resources: Download A-Level Economics Resource Book (34 MB), Entrepreneurship Syllabus (3 MB), Orientation Manual (5.92 MB), and Teachers Guide (5 MB) at ura.go.ug/download-category/a-level-tax-curriculum/.",
                "Step 3: O-Level Resources: Download O-Level Entrepreneurship Education TextBook (4.84 MB, 3,299+ downloads), Training Manual (2.06 MB), and Teachers Guide at ura.go.ug/download-category/o-level-tax-curriculum/.",
                "Step 4: Taxation Handbook Compendiums: Access the 8th Edition [2025-26] (4 MB), Chinese Edition, and annual Tax Amendments FY (2026-2027) booklet (10,272+ downloads) at ura.go.ug/download-category/taxation-handbook/.",
            ],
        )

    # 2d27. Real Estate & Property Sector Guide
    if portal_key == "real_estate_sector" or any(k in combined for k in (
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
        return (
            "URA Portal — Tax Education: Real Estate & Property Sector Guide",
            "Statutory tax rules for land dealers, property developers, MLHUD real estate agents, unimproved land VAT exemption, and CGV stamp duty.",
            "info",
            [
                "Step 1: Market Segments & Players: Raw land, residential, commercial, and industrial properties. Operators comprise land dealers, land developers, property developers, property managers, MLHUD-licensed agents, and landlords.",
                "Step 2: Land & Property Sales Taxation: Corporate real estate companies pay 30% CIT on net sales profits; sole proprietor dealers pay resident individual graduated income tax up to 40%; real estate dealers are excluded from presumptive tax.",
                "Step 3: VAT on Real Estate: Sale/lease of unimproved bare land and residential dwelling rentals are 100% VAT exempt; sales/lettings of commercial complexes, shopping malls, arcades, and serviced plots attract standard 18% VAT (threshold UGX 150M).",
                "Step 4: Property Managers & Agents: Rent collected by property managers is a pass-through to landlords; management fees and agency commissions attract 18% VAT and 6% WHT on contracts >UGX 1,000,000 via mandatory EFRIS billing.",
                "Step 5: Stamp Duty & Local Rates: 1.5% stamp duty on land transfers based on Chief Government Valuer (CGV) market valuation; 1% on commercial leases; Nil stamp duty in industrial parks; ground rent and municipal property rates are fully deductible expenses.",
                "Step 6: Download Official Compendium: Download the 18-page guide (Vol. 1 FY 2025-26) at ura.go.ug/storage/2026/01/REAL-ESTATE-SECTOR-FY-2025-26.pdf.",
            ],
        )

    # 2d28. Fishing & Fisheries Sector Guide
    if portal_key == "fishing_sector" or any(k in combined for k in (
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
        return (
            "URA Portal — Tax Education: Fishing & Fisheries Sector Guide",
            "Statutory tax rules for fishermen, fishmongers, industrial fish processing plants, zero-rated exports, and EACCMA duty exemptions.",
            "info",
            [
                "Step 1: Fisheries Value Chain & Licensing: Catching, wholesale/retail fishmongering, industrial processing, and exports. Regulated by MAAIF Directorate of Fisheries Resources (DFR vessel licenses, sanitary certificates), BMU permits, UNBS HACCP, and URA TIN.",
                "Step 2: VAT Treatment (Raw vs Processed vs Export): Fresh, whole, unprocessed fish is 100% VAT-exempt under Schedule 2 VAT Act; industrially processed fish sold domestically attracts 18% VAT with EFRIS fiscal invoices; exported fish products are zero-rated (0% VAT) under Schedule 3.",
                "Step 3: Income Tax Regimes: Small independent fishmongers earning UGX 10M–150M pay final presumptive turnover tax (turnover <UGX 10M pays Nil); incorporated fish processors pay 30% Corporation Tax on net profits; processors exporting 80%+ enjoy a 10-year income tax holiday under Section 21(1)(y) ITA.",
                "Step 4: EACCMA Customs Exemptions: Fresh catch landed by registered EAC vessels, aquaculture fingerlings/fish eggs, and specialized export packaging materials are 100% exempt from all customs duties and taxes under EACCMA Fifth Schedule Part B.",
                "Step 5: Official Documentation: Access guidance across Overview of Fishing, Fish Processing, Fisherman and Fishmonger, and Fish Products Export under URA Tax Education > Fishing at ura.go.ug/en/category/tax-education/fishing/.",
            ],
        )

    # 2d29. Transport Sector Guide (Passenger, Goods & Marine Logistics)
    if portal_key == "transport_sector" or any(k in combined for k in (
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
        return (
            "URA Portal — Tax Education: Commercial Transport Sector Guide",
            "Statutory tax rules for commercial passenger and freight transport, TLB unified assessment, aircraft exemptions, and marine vessel incentives.",
            "info",
            [
                "Step 1: Multi-Agency Licensing: Commercial passenger and freight transport entities register with URSB, Ministry of Works and Transport (MoWT), Transport Licensing Board (TLB), and URA for a 10-digit TIN.",
                "Step 2: TLB Unified Assessment: TLB issues a joint assessment combining the MoWT PSV operating license fee and URA Advance Income Tax; payment via a single PRN covers both obligations simultaneously.",
                "Step 3: Advance Tax Offset Credit: Advance income tax paid on commercial passenger or freight vehicles is NOT a lost cost: it serves as an advance tax credit that is fully deducted from final income tax payable on Form DT-1001.",
                "Step 4: Statutory Income Tax Exemptions: Complete income tax exemption applies to aircraft operators engaged in domestic/international air traffic or aircraft leasing, and foreign transporters embarking passengers/goods outside Uganda.",
                "Step 5: Customs Reductions & Exemptions: 0% import duty for one year on commercial vehicles >=20 tonnes GVW and road tractors for semi-trailers; 10% duty on 5-20 tonne goods vehicles; passenger/cargo vessels, commercial fishing trawlers, and ferry boats are 100% exempt from all taxes under EACCMA Fifth Schedule Part B.",
                "Step 6: Multilingual Guides: Access English and Runyankole/Rukiga registration guides under URA Tax Education > Transport at ura.go.ug/en/category/tax-education/transport/.",
            ],
        )

    # 2d30. Government Agencies & MDAs Taxation Guide
    if portal_key == "government_agencies" or (
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
        return (
            "URA Portal — Tax Education: Government Agencies & MDAs Taxation Guide",
            "Statutory tax rules for public bodies, designated withholding agent mandate, compulsory VAT on commercial activities, and deemed VAT.",
            "info",
            [
                "Step 1: Public Agency Scope & Registration: Central government ministries, departments, semi-autonomous agencies (MDAs), and local governments must hold active TINs to account for statutory taxes.",
                "Step 2: Designated Withholding Agent Mandate: MDAs must withhold 6% WHT on all procurement contract payments exceeding UGX 1,000,000 made to vendors and remit by the 15th via Form DT-1013; accounting officers face personal liability for unwithheld tax.",
                "Step 3: Compulsory Commercial VAT: Public bodies engaging in commercial business activities (hall hire, tendering concessions, public markets, street parking fees, toilet management, billboard adverts) must compulsorily register for VAT from the inception date.",
                "Step 4: Section 24(7) Deemed VAT for Aid Projects: Tax payable on supplies to an MDA by a contractor executing an official aid-funded project is deemed paid; MDAs write to the Assistant Commissioner Business Policy to confirm eligibility.",
                "Step 5: Other Non-Tax Revenue (ONTR) Reconciliation: URA collects NTR fees on behalf of MDAs under MOFPED authority; authorized MDA principal accountants reconcile collections in real-time via the e-Tax ONTR portal.",
                "Step 6: Download Official Compendium: Download the 48-page guide (FY 2023-24) at ura.go.ug/storage/2024/09/GUIDE-TO-TAXATION-OF-GOVERNMENT-AGENCIES-2023-24.pdf.",
            ],
        )

    # 2d31. Opportunities, Tenders & Auctions Portal
    if portal_key == "opportunities_portal" or any(k in combined for k in (
        "procurement management system",
        "pms supplier portal",
        "sourcing suppliers",
        "tender user manuals",
        "auctioning application",
        "auction platform for ura assets",
        "ura opportunities",
    )):
        return (
            "URA Portal — Opportunities, Tenders & Auctions Portal",
            "Supplier registration on Procurement Management System (PMS), public tender bidding, and online customs auctions.",
            "info",
            [
                "Step 1: PMS Supplier Registration: Bidders for URA procurement contracts register on the Procurement Management System (PMS) at ura.go.ug/en/opportunities/tenders/procurement-management-system/ with valid TCC and NSSF clearances.",
                "Step 2: Tender Sourcing Manuals: Access 'Help for Sourcing Suppliers' user guide (965 KB PDF) at ura.go.ug/download-category/tender-user-manuals/ to navigate bidding and RFQs.",
                "Step 3: Online Public Auctions: Overtime customs cargo, abandoned goods, and board assets are auctioned online via the Auctioning Application at ura.go.ug/en/opportunities/auctions/auctioning-application/ (or singlewindow.go.ug/auction).",
                "Step 4: Auction Bidding & Payment: Register with 10-digit TIN and NIN, view lots physically during gazetted warehouse inspection days, bid online, and settle assessed winning bid PRNs within 48 to 72 hours.",
                "Step 5: Auction User Manual: Download 'AUCTION PLATFORM FOR URA ASSETS RVD' (2 MB PDF) at ura.go.ug/download-category/user-guides/.",
            ],
        )

    # 2d32. Research Lab & Corporate Publications Repository
    if portal_key == "research_publications" or any(k in combined for k in (
        "ura research lab",
        "research faqs",
        "anonymised tax data",
        "anonymized tax data",
        "revenue performance reports",
        "corporate plans",
        "client satisfaction survey report",
        "strategic plan fy2025/26",
    )):
        return (
            "URA Portal — Research Lab & Corporate Publications Repository",
            "Access to clean anonymized tax microdata for academic research, annual revenue performance reports, and corporate strategic plans.",
            "info",
            [
                "Step 1: URA Research Lab: Repository of clean, anonymized tax microdata under the Research and Innovation Division for evidence-based tax policy and academic research.",
                "Step 2: Research Lab Access: Accredited researchers submit formal proposals endorsed by universities and sign URA data governance and non-disclosure agreements under Research FAQs at ura.go.ug/en/research-faqs/.",
                "Step 3: Annual Revenue Performance Reports: Download official fiscal collection reports (FY 2022-23 2.95 MB, FY 2021-22 1.82 MB, FY 2020-21) at ura.go.ug/download-category/revenue-performance-reports/.",
                "Step 4: Corporate Strategic Plans: Download URA Strategic Plan FY 2025/26 – 2029/30 (7 MB PDF) and Client Satisfaction Survey Report 2024 (7 MB) at ura.go.ug/download-category/corporate-plans/.",
            ],
        )

    # 2e. File a Tax Return (Central e-Returns Submission)
    if portal_key == "file_return" or any(
        k in combined
        for k in ("file a return", "file return", "file a tax return", "return filing", "e-returns")
    ):
        return (
            "URA Portal — File a Tax Return",
            "Central electronic return submission workflow for monthly, quarterly, and annual taxes.",
            "info",
            [
                "Step 1: Log in to ura.go.ug or portal.ura.go.ug with your 10-digit TIN and password.",
                "Step 2: Navigate to e-Services > Returns > File a Return (or e-Returns > Submit Return).",
                "Step 3: Select the tax type: Income Tax (DT-1001), Value Added Tax (DT-1014), PAYE (DT-1004), or WHT (DT-1013).",
                "Step 4: Choose the Return Period matching your filing obligation.",
                "Step 5: Upload the validated return file generated from the official offline Excel template.",
                "Step 6: Complete the security CAPTCHA and click 'Submit'.",
                "Step 7: The system generates an immediate e-Acknowledgement Receipt and assessment notice with a PRN for any tax payable.",
            ],
        )

    # 3. Non Resident Digital Service Providers Registration
    if any(k in combined for k in ("non resident", "digital service", "digital provider", "dst", "netflix", "meta")):
        return (
            "URA Portal — Non-Resident Digital Service Providers Registration",
            "Registration for foreign electronic service providers under Section 16(2) VAT Act and Section 86A ITA.",
            "info",
            [
                "Step 1: Go to portal.ura.go.ug > e-Services > Taxpayer Registration > Non-Resident Digital Services.",
                "Step 2: Enter foreign corporate details: Legal Entity Name, Country of Incorporation, and Business Registration ID.",
                "Step 3: Input official corporate address, designated local/foreign representative details, and contact email.",
                "Step 4: Select tax obligations: 5% Digital Services Tax (DST) and Electronic Services VAT.",
                "Step 5: Submit application online without needing a physical permanent establishment (PE) in Uganda.",
            ],
        )

    # 4. Group TIN Registration
    if "group tin" in combined or "joint venture" in combined or "sacco group" in combined:
        return (
            "URA Portal — Group TIN Registration",
            "Tax registration for joint ventures, consortiums, SACCOs, and unincorporated associations.",
            "info",
            [
                "Step 1: Go to portal.ura.go.ug > e-Services > Taxpayer Registration > Group TIN.",
                "Step 2: Identify the Lead Member / Principal Representative who holds an active 10-digit URA TIN.",
                "Step 3: Enter group legal title, bylaws/constitution document, and official physical meeting address.",
                "Step 4: Add member details: enter active personal TINs and National IDs for all executive participants.",
                "Step 5: Submit for domestic taxes review; group is assigned a master Group TIN for collective tax filings.",
            ],
        )

    # 5. TIN Registration – Non Individual
    if any(k in combined for k in ("non individual", "non-individual", "company tin", "corporate tin", "partnership tin")):
        return (
            "URA Portal — TIN Registration (Non-Individual)",
            "Formal registration for companies, partnerships, trusts, NGOs, and statutory corporations.",
            "info",
            [
                "Step 1: Go to portal.ura.go.ug > e-Services > Taxpayer Registration > Non-Individual.",
                "Step 2: Select business entity type (Private Company, Public Company, Partnership, Trust, or NGO).",
                "Step 3: Enter URSB Registration Number (auto-validates Company Name and Form 20 against business registry).",
                "Step 4: Enter active 10-digit personal TINs for all Directors, Company Secretary, and Chief Executive.",
                "Step 5: Upload Certificate of Incorporation and Company Form 20; select tax heads (CIT, PAYE, VAT if turnover >= 150M).",
                "Step 6: Submit application; URA reviews and issues TIN within 24 to 48 hours.",
            ],
        )

    # 6. TIN Registration – Individual (Standard / Non-Instant)
    if any(k in combined for k in ("individual tin", "registration individual", "standard individual", "minor tin", "non-citizen", "refugee")):
        return (
            "URA Portal — TIN Registration (Individual)",
            "Standard individual registration for residents, non-citizens, minors, and sole proprietors.",
            "info",
            [
                "Step 1: Go to portal.ura.go.ug > e-Services > Taxpayer Registration > Individual.",
                "Step 2: Select applicant category: Resident Citizen (without NIN), Non-Citizen, Minor (under 18), or Refugee.",
                "Step 3: Attach identification: Passport + Work Permit (non-citizens), Court Guardianship Order (minors), or Refugee ID.",
                "Step 4: If registering a Sole Proprietorship business name, attach the URSB Business Name Certificate.",
                "Step 5: Fill in personal particulars, physical address, and source of income; click Submit.",
                "Step 6: URA registration team verifies attachments and issues your 10-digit TIN certificate within 24 to 48 hours.",
            ],
        )

    # 7. Instant TIN Application
    if portal_key == "tin_registration" or ("tin" in combined and any(k in combined for k in ("register", "apply", "instant", "nira", "nin"))):
        if any(k in combined for k in ("nin mismatch", "mismatch", "unverified", "failed nira", "nira error", "nira rejected", "nira verification failed", "error")):
            return (
                "NIRA Identity Verification Mismatch",
                "Applicant NIN or name does not match the National Identification and Registration Authority (NIRA) database.",
                "warning",
                [
                    "Step 1: Type your names in the exact order (Surname, Given name) matching your physical National ID.",
                    "Step 2: Confirm your 14-character NIN matches your National ID card.",
                    "Step 3: If recently registered at NIRA, allow 24-48 hours for data synchronization with URA.",
                ],
            )
        return (
            "URA Instant TIN Application Workflow",
            "Instant TIN generation active for individuals with valid NIRA National ID.",
            "info",
            [
                "Step 1: Go to ura.go.ug or portal.ura.go.ug and click 'Get a TIN' > 'Instant TIN Application'.",
                "Step 2: Select 'Individual' (with NIRA National ID).",
                "Step 3: Enter your 14-character NIN, full legal name, source of income, registered mobile, and email.",
                "Step 4: Check 'I am not a robot' CAPTCHA verification and click 'Submit'.",
                "Step 5: Registration is free. Your 10-digit TIN is issued automatically within 5 minutes via SMS and email.",
            ],
        )

    # 8. Get a TIN (General Landing)
    if "get a tin" in combined or "get tin" in combined:
        return (
            "URA Portal — Get a TIN Overview",
            "Landing guidance and eligibility verification for acquiring a URA Tax Identification Number.",
            "info",
            [
                "Step 1: Visit ura.go.ug or portal.ura.go.ug and click the 'Get a TIN' tab.",
                "Step 2: Review required documentation (National ID for citizens, URSB incorporation documents for companies).",
                "Step 3: Choose your registration flow: Instant TIN (individuals with NIN), Non-Individual (companies), or Special categories.",
                "Step 4: All TIN registration flows are free of charge on the URA web portal.",
                "Step 5: After receiving your 10-digit TIN, create an online portal account to file returns and access e-Services.",
            ],
        )

    # -------------------------------------------------------------------
    # Additional Domestic Taxes Core Services
    # -------------------------------------------------------------------

    # 12. Voluntary Disclosure (Section 66 TPCA)
    if portal_key == "voluntary_disclosure" or any(k in combined for k in ("voluntary disclosure", "section 66", "waiver")):
        return (
            "URA Portal — Voluntary Disclosure Program (Section 66 TPCA)",
            "Statutory program offering 100% waiver of penalties and interest for proactive tax disclosures.",
            "info",
            [
                "Step 1: Confirm eligibility: Disclosure must occur BEFORE URA serves an official notice of audit or investigation.",
                "Step 2: Go to portal.ura.go.ug > e-Services > Voluntary Disclosure.",
                "Step 3: Submit full declaration of previously omitted income, undeclared sales, or unpaid tax heads.",
                "Step 4: Attach reconciliation statements and compute principal tax payable.",
                "Step 5: Receive 100% waiver of penal tax and interest; settle principal tax in full or sign an agreed installment agreement.",
            ],
        )

    # 13. Whistle Blow (Touchpoint / Informer & Whistleblower Reward)
    if portal_key == "whistle_blow" or any(k in combined for k in ("whistle", "informer", "informant", "report evasion", "smuggling")):
        return (
            "URA Portal — Informer & Whistleblower Reporting (Section 67 TPCA)",
            "Confidential portal to report tax evasion, non-issuance of EFRIS receipts, or smuggling.",
            "info",
            [
                "Step 1: Visit touchpoint.ura.go.ug, ura.go.ug/en/report-non-compliance/, or call +256 (0)323442055 (anonymous reporting fully supported).",
                "Step 2: Select report category: EFRIS Non-Issuance, Tax Under-Declaration, Customs Smuggling, or Staff Integrity.",
                "Step 3: Actionable Intelligence: Provide Business Name, TIN, physical location, and evidence/receipt photos.",
                "Step 4: Statutory Rewards: Informers receive 1% of tax assessed up to UGX 15,000,000 for identification, or 5% of recovered tax up to UGX 100,000,000 (URA staff are excluded).",
                "Step 5: Full Legal Protection: Identity is strictly protected under the Whistleblowers Protection Act 2010 against victimization or retaliation.",
                "Step 6: Official Flier: Download the Whistleblowing Guide at ura.go.ug/storage/2024/09/WHISTLEBLOW-FLIER-2023-24.pdf.",
            ],
        )

    # 14. Digital Tax Stamps (DTS) Suite (5 Sub-Paths)
    if portal_key == "dts" or any(k in combined for k in ("dts", "digital tax stamp", "tax stamp", "kakasa", "stamp order")):
        # 1. DTS Registration
        if any(k in combined for k in ("registration", "register", "onboarding")):
            return (
                "URA Portal — DTS Registration (Manufacturers & Importers)",
                "Onboarding and facility declaration for mandatory Digital Tax Stamps.",
                "info",
                [
                    "Step 1: Log in to portal.ura.go.ug with your active 10-digit TIN and password.",
                    "Step 2: Navigate to e-Services > Digital Tax Stamps > DTS Registration.",
                    "Step 3: Select taxpayer category: Local Manufacturer or Importer of gazetted excisable goods.",
                    "Step 4: Declare production facility address, number of packaging lines, and estimated monthly volumes.",
                    "Step 5: Specify hardware setup: automated line applicator/ejection system or manual stamping area.",
                    "Step 6: Submit application; URA and SICPA Uganda inspect premises and configure line-enable within 5 business days.",
                ],
            )

        # 2. Affix and Activate Tax stamps
        if any(k in combined for k in ("affix and activate", "activate", "affix", "activation", "order stamps")):
            return (
                "URA Portal — Affix & Activate Digital Tax Stamps",
                "End-to-end stamp ordering, physical application, and production activation.",
                "info",
                [
                    "Step 1: Go to portal.ura.go.ug > e-Services > Digital Tax Stamps > Order Stamps; forecast quantities and pay stamp fees via PRN.",
                    "Step 2: Collect physical paper stamp rolls from SICPA Uganda (Henley Business Park, Ntinda, Kampala) or receive direct marking codes.",
                    "Step 3: Affix stamps onto product packaging at the packaging line (automated applicator or manual application).",
                    "Step 4: Activate Stamps: Before products leave the factory or customs warehouse, scan and activate stamps via the Production Line Controller (PLC) or handheld terminal.",
                    "Step 5: Products leaving the facility without stamp activation are legally deemed unstamped contraband under Section 73B TPCA.",
                ],
            )

        # 3. Download Manual Forms
        if any(k in combined for k in ("manual form", "manual dts forms", "damaged stamp", "spoiled stamp", "reconciliation sheet", "dt 1019", "dts rejects monitoring form")):
            return (
                "URA Portal — Download Manual DTS Forms",
                "Printable forms for DTS registration annexures, spoiled stamp declarations, and physical stock reconciliations.",
                "info",
                [
                    "Step 1: Visit ura.go.ug/download-category/manual-dts-forms/ to access official DTS document templates.",
                    "Step 2: Download DT 1019 - DTS & LED Registration Annexure (2.30 MB Excel sheet) for production line and SKU declarations.",
                    "Step 3: Download the DTS Rejects Monitoring Form (55.50 KB Excel sheet) to record serial numbers of stamps damaged during line jams or breakages.",
                    "Step 4: Attach physical remnants of damaged stamps and submit to your designated URA excise station for credit and reconciliation.",
                ],
            )

        # 4. Gazetted Item Catalogue & Stamp Prices
        if any(k in combined for k in ("catalogue", "catalog", "gazetted item", "gazetted goods", "which goods", "list of goods", "price per stamp", "prices per stamp")):
            return (
                "URA Portal — DTS Gazetted Item Catalogue & Pricing Schedule",
                "Statutory schedule of excisable commodities subject to mandatory Digital Tax Stamps and approved per-stamp unit pricing.",
                "info",
                [
                    "Step 1: Go to ura.go.ug/download-category/dts-gazetted-services-catalogue/ to download the official 401.42 KB catalogue covering 9 mandatory gazetted commodity categories.",
                    "Step 2: Approved per-stamp prices (VAT exclusive): Bottled water (UGX 13), Soda (UGX 17), Fruit/vegetable juice (UGX 17), Other non-alcoholic (UGX 17), Other alcoholic (UGX 17), Fermented beverages (UGX 35), Beer (UGX 36), Sugar (UGX 39), Cooking oil (UGX 40), Spirits (UGX 60), Wine (UGX 60), Tobacco products (UGX 75), Cement (UGX 135), and Cement bulker seals (UGX 60,000).",
                    "Step 3: Payment for stamp orders is made via PRN at approved banks: Stanbic Bank, KCB Bank, GT Bank, or NCBA Bank.",
                    "Step 4: Cross-reference packaging specifications and required stamp type (paper security stamp vs. direct marking ink).",
                ],
            )

        # 5. Stamp Collection at SICPA Uganda & Authentication (KAKASA / SMS 8119)
        if any(k in combined for k in ("sicpa", "henley business park", "ntinda industrial area", "pick digital stamps", "kakasa", "sms 8119", "kabiriiti", "stamp validator")):
            return (
                "URA Portal — DTS Collection & Consumer Verification (KAKASA & SMS 8119)",
                "Physical stamp pickup location at SICPA Uganda and public authenticity verification channels.",
                "info",
                [
                    "Step 1: Collection Address: SICPA Uganda Limited, Henley Business Park, Ntinda Industrial Area, P.O. Box 30330 Kampala (Coordinates: 0.340632, 32.616436).",
                    "Step 2: Directions: From Jinja Road (Nakawa-Naguru junction), take Stretcher Road for ~1km, turn right at Shell Stretcher for 50m to Henley Business Park opposite Wispro (U) Ltd.",
                    "Step 3: Authorized Collector ID: Designated collectors must present a valid National ID or Passport and a company authorization letter/stamp.",
                    "Step 4: Smartphone Verification: Scan QR codes using the free KAKASA App available on Google Play Store and Apple App Store.",
                    "Step 5: Feature Phone Verification: For basic 'Kabiriiti' phones, type the code printed on the stamp and send an SMS to 8119 for an instant verification report.",
                ],
            )

        # Default DTS Overview & Penalties
        return (
            "URA Portal — Digital Tax Stamps (DTS) Overview & Compliance",
            "Automated track-and-trace solution to combat counterfeiting and secure excise revenue.",
            "info",
            [
                "Step 1: Digital Tax Stamps (DTS) are physical paper stamps or direct digital ink markings applied to gazetted excisable goods.",
                "Step 2: Statutory penalties under Section 73B TPCA: Failure to affix/activate stamps attracts double the tax due or UGX 50,000,000 (higher).",
                "Step 3: Defacing or printing over stamps carries double tax due or UGX 20,000,000; possession of unstamped gazetted goods carries double tax due or UGX 50,000,000.",
                "Step 4: Citizens and retailers verify authenticity free of charge using the URA Kakasa mobile app or SMS to 8119.",
                "Step 5: Official Brochure: Download the complete DTS Brochure at ura.go.ug/storage/2026/09/DIGITAL-TAX-STAMPS-BROCHURE-FY-2026-27-3.pdf.",
            ],
        )

    # -------------------------------------------------------------------
    # Detailed verification of EFRIS Sub-Paths & Workflows
    # -------------------------------------------------------------------

    if portal_key == "efris" or "efris" in combined:
        # 1. EFRIS Registration
        if any(k in combined for k in ("registration", "register", "first time")):
            return (
                "URA EFRIS — First Time Registration Workflow",
                "Initial onboarding and system selection for EFRIS electronic fiscal invoicing.",
                "info",
                [
                    "Step 1: Visit ura.go.ug or efris.ura.go.ug and click on EFRIS > First Time Registration.",
                    "Step 2: Log in using your active 10-digit TIN and web portal password; enter the SMS/Email OTP.",
                    "Step 3: Choose your invoicing solution: Web Portal, Electronic Fiscal Device (EFD), Desktop Client, or System-to-System ERP API.",
                    "Step 4: Add business branches, physical store addresses, and warehouse locations.",
                    "Step 5: Submit your registration; once approved by URA, your EFRIS fiscal profile activates immediately.",
                ],
            )

        # 2. Invoice / Receipt Issuance
        if any(k in combined for k in ("issue invoice", "issue receipt", "generate invoice", "create invoice", "create receipt", "issuance")):
            return (
                "URA EFRIS — Invoice & Receipt Issuance Workflow",
                "Generating authentic fiscal invoices and receipts with 20-digit FDN and QR codes.",
                "info",
                [
                    "Step 1: Log in to efris.ura.go.ug and navigate to Invoices Management > Create Invoice / Receipt.",
                    "Step 2: For B2B transactions, enter buyer's 10-digit TIN (auto-validates legal name); for consumers, select 'Walk-in / Consumer'.",
                    "Step 3: Select goods or services from your stock catalogue mapped to URA Commodity Codes.",
                    "Step 4: Enter quantities and unit prices; verify the 18% standard VAT rate (or 0% zero-rated / exempt).",
                    "Step 5: Click 'Save and Issue' to produce an authentic voucher with a 20-digit Fiscal Document Number (FDN) and QR code.",
                ],
            )

        # 3. Stock Management
        if any(k in combined for k in ("stock", "inventory", "stock in", "stock out", "stock ledger")):
            return (
                "URA EFRIS — Stock & Inventory Management",
                "Tracking opening balance, stock additions, and real-time inventory deductions.",
                "info",
                [
                    "Step 1: Log in to efris.ura.go.ug > Stock Management.",
                    "Step 2: To record inventory, click Stock In > Stock In Details.",
                    "Step 3: Choose stock-in source: Initial Stock (Opening Balance), Local Purchases (auto-populated from supplier e-invoices), or Customs Import (ASYCUDA bill of entry).",
                    "Step 4: Map items to approved URA Commodity Codes and units of measure.",
                    "Step 5: Approve the stock-in transaction; available inventory quantities update immediately.",
                    "Step 6: When sales invoices are issued, EFRIS automatically deducts stock quantities in real time.",
                ],
            )

        # 4. Create Sub Accounts
        if any(k in combined for k in ("sub account", "sub-account", "cashier", "operator account", "branch login")):
            return (
                "URA EFRIS — Cashier & Branch Sub-Account Management",
                "Creating branch cashier and operator logins with role-based access control.",
                "info",
                [
                    "Step 1: Log in to efris.ura.go.ug using your Master Administrator TIN credentials.",
                    "Step 2: Navigate to System Management > User Management > Sub-Accounts > Add Sub-Account.",
                    "Step 3: Enter operator particulars: Full Name, National ID (NIN), mobile number, and email.",
                    "Step 4: Assign the operator to their physical branch / store terminal location.",
                    "Step 5: Set permissions: assign 'Cashier' (issue receipts and view daily sales only) or 'Branch Manager' without admin rights.",
                    "Step 6: System generates login credentials for the cashier to operate without accessing master company settings.",
                ],
            )

        # 5. Credit Note / Debit Note
        if any(k in combined for k in ("credit note", "debit note", "cancel invoice", "correct invoice", "refund note")):
            return (
                "URA EFRIS — Credit Note & Debit Note Invoice Adjustment",
                "Statutory procedure to adjust or cancel already issued electronic fiscal invoices.",
                "info",
                [
                    "Step 1: Note that issued EFRIS fiscal invoices cannot be deleted or altered directly.",
                    "Step 2: Go to efris.ura.go.ug > Invoices Management > Credit Note / Debit Note > Apply for Credit Note.",
                    "Step 3: Enter the original 20-digit Fiscal Document Number (FDN) of the invoice being adjusted.",
                    "Step 4: Select statutory reason: Goods Returned, Price Adjustment / Discount, or Invoicing Error.",
                    "Step 5: Input adjusted quantities or amounts and submit.",
                    "Step 6: For B2B invoices, the buyer receives a notification and must log in to approve the credit note before VAT adjustments reflect.",
                ],
            )

        # 6. FDN Validation
        if any(k in combined for k in ("fdn", "fiscal document number", "validate fdn", "verify fdn")):
            return (
                "URA EFRIS — Fiscal Document Number (FDN) Validation",
                "Validating authenticity of 20-digit FDN and verification codes online.",
                "info",
                [
                    "Step 1: Visit efris.ura.go.ug or portal.ura.go.ug > Quick Links > FDN Validation.",
                    "Step 2: Enter the 20-digit Fiscal Document Number (FDN) printed on the invoice or receipt.",
                    "Step 3: Enter the verification code printed below the barcode/QR code.",
                    "Step 4: Click 'Verify' to display genuine seller TIN, buyer TIN, invoice date, total amount, and 18% VAT.",
                    "Step 5: Alternatively, scan the printed QR code using the free URA Kakasa mobile app.",
                ],
            )

        # 7. EFRIS Reports
        if any(k in combined for k in ("reports management", "efris report", "sales report", "stock report", "vat report", "audit report")):
            return (
                "URA EFRIS — Financial & VAT Audit Reports",
                "Exporting sales transaction summaries, Z-reports, and stock balance ledgers.",
                "info",
                [
                    "Step 1: Log in to efris.ura.go.ug and navigate to Reports Management.",
                    "Step 2: Select desired report: Daily Sales Summary, Periodic Z-Reports, Stock Balance Ledger, or Monthly Output VAT Report.",
                    "Step 3: Select reporting date range and filter by branch or store terminal.",
                    "Step 4: Click 'Query / Export' to download the report in Excel or PDF format.",
                    "Step 5: Use the monthly output VAT totals to reconcile directly with your monthly VAT return (Form DT-1014).",
                ],
            )

        # 8. Electronic Fiscal Device (EFD)
        if any(k in combined for k in ("efd", "electronic fiscal device", "fiscal printer", "terminal management")):
            return (
                "URA EFRIS — Electronic Fiscal Device (EFD) Hardware & Operation",
                "Configuration and daily operations of standalone EFD cash registers.",
                "info",
                [
                    "Step 1: Procure a certified EFD device from a URA-accredited terminal vendor.",
                    "Step 2: In the EFRIS portal, go to System Management > Terminal Management and link the device serial number to your TIN.",
                    "Step 3: Insert an active data SIM card and power on the device to establish connection with URA servers.",
                    "Step 4: Configure tax rates (18% standard, zero-rated, exempt) and commodity item codes.",
                    "Step 5: Issue sales receipts at checkout; each receipt prints an automatic fiscal signature and QR code.",
                    "Step 6: At the end of each business day, perform an official Z-Report to close the daily batch and synchronize records.",
                ],
            )

        # 9. List of Accredited EFRIS Software Integrators
        if any(k in combined for k in ("integrator", "accredited software", "erp integration", "system to system", "api sandbox")):
            return (
                "URA EFRIS — Accredited Software Integrators & API Certification",
                "Listing of certified POS/ERP vendors and API developer sandbox onboarding.",
                "info",
                [
                    "Step 1: Visit efris.ura.go.ug > Downloads & Developer Support > Accredited Integrators.",
                    "Step 2: View the official directory of certified third-party software vendors providing URA-compliant POS and ERP systems.",
                    "Step 3: For businesses integrating in-house ERP systems: register on the EFRIS API Developer Sandbox.",
                    "Step 4: Complete required test cases (invoice generation, credit notes, stock sync, digital signatures) and submit test logs for URA accreditation.",
                ],
            )

        # 10. EFRIS Handbook & Brochure
        if any(k in combined for k in ("handbook", "brochure", "user manual", "manual", "guide")):
            return (
                "URA EFRIS — Handbook, User Manuals & Guidelines",
                "Downloading official sector-specific user guides, manuals, and brochures.",
                "info",
                [
                    "Step 1: Visit efris.ura.go.ug > Downloads / Publications or ura.go.ug/download-category/efris-handbook/.",
                    "Step 2: Access official publications: EFRIS HANDBOOK FY 2026-27 (English 4MB, Runyankole 3MB, Swahili 3MB, Luganda 3MB), Comprehensive EFRIS User Guide, Sector Guidelines (Manufacturing, Retail, Hospitality), and Commodity Code Catalogue.",
                    "Step 3: Download step-by-step installation guides for the EFRIS Desktop Client software.",
                ],
            )

        # 11. Document Authentication
        if any(k in combined for k in ("authenticate", "verify", "genuine", "kakasa")):
            return (
                "URA EFRIS — Receipt & Invoice Authentication",
                "Verifying authenticity of electronic fiscal invoices and receipts.",
                "info",
                [
                    "Step 1: Open the free URA Kakasa mobile app on iOS or Android, or visit portal.ura.go.ug > e-Services > Document Authentication.",
                    "Step 2: Scan the QR code printed on the invoice or enter the 20-digit Fiscal Document Number (FDN).",
                    "Step 3: Confirm matching seller TIN, transaction date, total amount, and 18% VAT.",
                ],
            )

        # 12. Offline Blackout Sync
        if any(k in combined for k in ("offline", "sync", "24", "blackout")):
            return (
                "EFRIS Offline Sync Limit Exceeded (24-Hour Rule)",
                "Offline invoicing has exceeded the mandatory 24-hour statutory synchronization window.",
                "blocker",
                [
                    "Step 1: Check internet connectivity on your fiscal device or point-of-sale terminal.",
                    "Step 2: In the EFRIS client software, go to System Management > Offline Sync.",
                    "Step 3: Click 'Sync All Pending Invoices' to transmit queued fiscal receipts to URA servers.",
                ],
            )

        # Default general EFRIS overview
        return (
            "URA EFRIS — Electronic Invoicing Overview",
            "Central electronic fiscal receipting and invoicing system for Uganda.",
            "info",
            [
                "Step 1: Visit efris.ura.go.ug and log in with your active 10-digit TIN and password.",
                "Step 2: Ensure all sales are issued with an authentic 20-digit Fiscal Document Number (FDN).",
                "Step 3: Maintain real-time stock balances through the Stock Management module.",
                "Step 4: Settle monthly VAT returns by the 15th of the following month.",
            ],
        )

    # Default standard navigation guidance
    portal_info = EXTERNAL_URA_PORTALS.get(portal_key, EXTERNAL_URA_PORTALS["e_services"])
    return (
        f"Active Portal Screen — {portal_info['name']}",
        "Portal view active with valid taxpayer context.",
        "info",
        [
            "Verify your 10-digit TIN displayed in the top banner.",
            "Select the appropriate module from the left menu navigation.",
            "Complete the required entries and save the reference confirmation.",
        ],
    )

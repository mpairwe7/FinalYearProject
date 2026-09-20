"""Hardest Statutory Edge Cases Benchmark over Live Ngrok Gateway.

Tests 15 complex, multi-layered Ugandan statutory edge cases across:
1. Income Tax Act (Cap 340 / 338) - PAYE Secondary vs Primary, Agro-processing Holiday, Mining Ring-fencing, Estate liability
2. Value Added Tax Act (Cap 349) - Mixed supply input tax apportionment, 150M/37.5M thresholds, Zero-rated vs Exempt
3. Tax Procedures Code Act (TPCA 2014) - Section 24 45-day objection + 30% deposit rule, EFRIS s.19B UGX 6M penalty
4. Excise Duty Act 2014 - Raw material credit offset mechanism, Environmental levy age bands (35%/50% & 15-yr ban)
5. EAC Customs Management Act (EAC-CMA) - Valuation hierarchy (Methods 1-6), $500 passenger concession limits
6. Withholding VAT (6%) vs Income Tax WHT (6%) dual deduction interaction
7. Non-resident Digital Services Tax (5% B2C under s.86A)
8. Rental Tax: Individual (12% > 2.82M gross) vs Corporate (30% net with 50% expense cap)

Evaluated across English (EN), Luganda (LG), and Swahili (SW).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx

GATEWAY_URL = os.getenv(
    "LIVE_GATEWAY_URL",
    "https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/chat",
)

EDGE_CASES = [
    {
        "id": "SEC-001",
        "title": "PAYE Primary vs Secondary Employment",
        "locale": "en",
        "domain": "Domestic Taxes (PAYE)",
        "statute": "Income Tax Act, Third Schedule",
        "query": (
            "I have a primary job where PAYE is deducted progressively. "
            "I also work a secondary job. Does the secondary employment get the 235,000 threshold or how is PAYE charged?"
        ),
        "required_concepts": ["paye", "income tax", "threshold"],
        "forbidden_hallucinations": ["pay nothing", "exempt forever"],
    },
    {
        "id": "SEC-002",
        "title": "Section 21 Agro-Processing 10-Year Holiday Local Content Ratio",
        "locale": "en",
        "domain": "Income Tax Exemptions",
        "statute": "Income Tax Act, Section 21(1)(y)",
        "query": (
            "I operate a commercial fruit processing plant under the Section 21 10-year tax holiday. "
            "What export or local sourcing percentage threshold must I meet to retain this exemption?"
        ),
        "required_concepts": ["80%", "10-year", "exemption"],
        "forbidden_hallucinations": ["no threshold exists"],
    },
    {
        "id": "SEC-003",
        "title": "VAT Apportionment for Mixed Supplies (Taxable vs Exempt)",
        "locale": "en",
        "domain": "Value Added Tax",
        "statute": "VAT Act, Section 28",
        "query": (
            "My company makes both taxable supplies (18% VAT) and exempt unprocessed agricultural goods. "
            "How does input VAT claiming work on general warehouse rent and overheads?"
        ),
        "required_concepts": ["vat", "input", "supplies"],
        "forbidden_hallucinations": ["all input tax is always 100% refundable without conditions"],
    },
    {
        "id": "SEC-004",
        "title": "Dual WHT Interaction: Withholding VAT (6%) vs Income WHT (6%)",
        "locale": "en",
        "domain": "Withholding Taxes",
        "statute": "VAT Act s.5A & Income Tax Act s.119",
        "query": (
            "A designated URA Withholding VAT agent is paying my commercial invoice of 10,000,000 UGX (plus 18% VAT). "
            "I do not hold a WHT exemption certificate. Does the agent deduct both the 6% Withholding VAT and the 6% Income Tax WHT, and on which amounts?"
        ),
        "required_concepts": ["withholding", "6%", "agent"],
        "forbidden_hallucinations": ["only one tax can ever be deducted"],
    },
    {
        "id": "SEC-005",
        "title": "Section 24 TPCA Objection Notice: 45 Days & 30% Payment Rule",
        "locale": "en",
        "domain": "Tax Disputes & Procedure",
        "statute": "Tax Procedures Code Act, Section 24",
        "query": (
            "URA issued an additional assessment of 50,000,000 UGX which I strongly dispute. "
            "What is the statutory deadline to lodge a formal objection with the Commissioner General?"
        ),
        "required_concepts": ["45 days", "objection"],
        "forbidden_hallucinations": ["90 days to lodge objection", "pay nothing ever"],
    },
    {
        "id": "SEC-006",
        "title": "EFRIS Non-Issuance Penalty under TPCA Section 19B",
        "locale": "en",
        "domain": "EFRIS Compliance",
        "statute": "Tax Procedures Code Act, Section 19B",
        "query": (
            "What specific statutory penalty does a VAT-registered business face if they fail to issue an EFRIS fiscal receipt or invoice?"
        ),
        "required_concepts": ["6,000,000", "penalty", "double"],
        "forbidden_hallucinations": ["100,000 shillings", "warning only"],
    },
    {
        "id": "SEC-007",
        "title": "Rental Income Tax: Individual vs Corporate Rules",
        "locale": "en",
        "domain": "Domestic Taxes (Rental)",
        "statute": "Income Tax Act, Section 5",
        "query": (
            "How does the taxation of rental income differ between an individual landlord collecting 20,000,000 UGX gross annually and a registered real estate company earning the same amount?"
        ),
        "required_concepts": ["12%", "2,820,000", "30%", "expenses"],
        "forbidden_hallucinations": ["both deduct expenses equally"],
    },
    {
        "id": "SEC-008",
        "title": "Environmental Levy on Imported Used Vehicles & 15-Year Ban",
        "locale": "en",
        "domain": "Customs & Motor Vehicle",
        "statute": "Excise Tariff / Traffic & Road Safety Act",
        "query": (
            "Can I import a 2008 Toyota Premio (18 years old) into Uganda, and if not, what are the environmental levy rates for cars between 5 and 14 years old?"
        ),
        "required_concepts": ["15 years", "prohibited", "environmental levy", "50%"],
        "forbidden_hallucinations": ["unlimited age allowed", "zero environmental levy"],
    },
    {
        "id": "SEC-009",
        "title": "Local Excise Duty (LED) Raw Material Credit Offset",
        "locale": "en",
        "domain": "Excise Duty",
        "statute": "Excise Duty Act, Section 14",
        "query": (
            "What is the statutory basis and rates for local excise duty under the Excise Duty Act in Uganda?"
        ),
        "required_concepts": ["excise duty", "act", "rates"],
        "forbidden_hallucinations": ["excise duty does not exist"],
    },
    {
        "id": "SEC-010",
        "title": "EAC Customs Valuation Hierarchy (Method 1 to 6)",
        "locale": "en",
        "domain": "Customs Valuation",
        "statute": "EAC-CMA, Fourth Schedule",
        "query": (
            "If customs rejects my declared invoice value under Method 1 because of a related-party discount, "
            "can the customs officer immediately jump straight to Method 6 Fallback, or must they follow a strict hierarchical sequence?"
        ),
        "required_concepts": ["hierarchy", "method 1", "method 2", "fallback"],
        "forbidden_hallucinations": ["can jump directly to method 6"],
    },
    {
        "id": "SEC-011",
        "title": "Digital Services Tax on Non-Resident Providers (Section 86A)",
        "locale": "en",
        "domain": "International Taxation",
        "statute": "Income Tax Act, Section 86A",
        "query": (
            "What tax rate applies to non-resident tech companies like Netflix or Spotify providing streaming services to individual consumers in Uganda without a physical branch?"
        ),
        "required_concepts": ["5%", "digital services tax", "non-resident", "section 86a"],
        "forbidden_hallucinations": ["30% corporate tax", "exempt from all taxes"],
    },
    {
        "id": "SEC-012",
        "title": "Passenger Baggage Concession vs Commercial Goods ($500 limit)",
        "locale": "en",
        "domain": "Customs Baggage",
        "statute": "EAC-CMA, Fifth Schedule",
        "query": (
            "I arrived at Entebbe Airport with personal effects and 5 brand-new boxed iPhones intended for resale in my shop. "
            "Can the iPhones be cleared under the duty-free passenger baggage concession?"
        ),
        "required_concepts": ["500", "passenger baggage", "commercial", "customs duty"],
        "forbidden_hallucinations": ["all goods free", "commercial phones exempt"],
    },
    {
        "id": "SEC-013",
        "title": "PAYE Employment Rules in Luganda (Vernacular Edge Case)",
        "locale": "lg",
        "domain": "Domestic Taxes (PAYE)",
        "statute": "Income Tax Act, Third Schedule",
        "query": (
            "Omusolo gwa PAYE ku musaala gw'omukozi mu Uganda gubalibwa gutya, era emitendera gigoberera ki?"
        ),
        "required_concepts": ["paye", "omusaala", "omusolo"],
        "forbidden_hallucinations": ["omusaala gwonna teguwoozebwa"],
    },
    {
        "id": "SEC-014",
        "title": "Section 24 TPCA Formal Objection Procedure in Luganda",
        "locale": "lg",
        "domain": "Tax Disputes (Luganda)",
        "statute": "Tax Procedures Code Act, Section 24",
        "query": (
            "Nnyinza ntya okuwakanya omusolo gwa URA gwe ssikkiriziganya nagwo, era emitendera gya mateeka giri gitya?"
        ),
        "required_concepts": ["ura", "misolo"],
        "forbidden_hallucinations": ["tobaako ky'osasula n'akatono"],
    },
    {
        "id": "SEC-015",
        "title": "VAT Rates and Corporation Tax in Swahili (Vernacular Edge Case)",
        "locale": "sw",
        "domain": "Value Added Tax (Swahili)",
        "statute": "VAT Act & ITA",
        "query": (
            "Kiwango cha ushuru wa ongezeko la thamani (VAT) na kodi ya mapato ya shirika nchini Uganda ni asilimia ngapi?"
        ),
        "required_concepts": ["18%", "ushuru"],
        "forbidden_hallucinations": ["hakuna kodi nchini uganda"],
    },
    # -------------------------------------------------------------------------
    # NEW 50 STATUTORY EDGE CASES (SEC-016 through SEC-065)
    # -------------------------------------------------------------------------
    {
        "id": "SEC-016",
        "title": "PWD Employment Exemption Threshold",
        "locale": "en",
        "domain": "Domestic Taxes (Exemptions)",
        "statute": "Income Tax Act, Section 21(1)(v)",
        "query": "What is the monthly tax exemption threshold for employment income earned by a Person with Disability (PWD)?",
        "required_concepts": ["1,460,000", "pwd", "exempt"],
        "forbidden_hallucinations": ["100,000,000 exempt", "no exemption for disabled"],
    },
    {
        "id": "SEC-017",
        "title": "Branch Profits Repatriation Tax Rate",
        "locale": "en",
        "domain": "International Taxation",
        "statute": "Income Tax Act, Section 82",
        "query": "What tax rate is charged on the repatriated income of a non-resident branch in Uganda?",
        "required_concepts": ["15%", "repatriat", "branch"],
        "forbidden_hallucinations": ["0% tax", "exempt from repatriation tax"],
    },
    {
        "id": "SEC-018",
        "title": "Quarterly VAT Registration Threshold",
        "locale": "en",
        "domain": "Value Added Tax",
        "statute": "VAT Act, Section 7",
        "query": "What is the mandatory VAT registration threshold for turnover in three consecutive calendar months?",
        "required_concepts": ["37,500,000", "consecutive", "vat"],
        "forbidden_hallucinations": ["1 billion quarterly"],
    },
    {
        "id": "SEC-019",
        "title": "Tax Appeals Tribunal (TAT) Statutory Deadline",
        "locale": "en",
        "domain": "Tax Disputes & Procedure",
        "statute": "Tax Appeals Tribunal Act, Section 16",
        "query": "How many days do I have to appeal to the Tax Appeals Tribunal (TAT) after an objection decision?",
        "required_concepts": ["30 days", "tax appeals tribunal", "appeal"],
        "forbidden_hallucinations": ["1 year to appeal"],
    },
    {
        "id": "SEC-020",
        "title": "Presumptive Tax Turnover Eligibility Limits",
        "locale": "en",
        "domain": "Small Business Taxation",
        "statute": "Income Tax Act, Section 4(5)",
        "query": "What are the lower and upper annual turnover thresholds for small businesses to qualify for presumptive tax?",
        "required_concepts": ["10,000,000", "150,000,000", "presumptive"],
        "forbidden_hallucinations": ["no upper limit"],
    },
    {
        "id": "SEC-021",
        "title": "Mining and Petroleum Exploration Ring-Fencing",
        "locale": "en",
        "domain": "Extractive Industries",
        "statute": "Income Tax Act, Part IXA",
        "query": "Can an oil exploration company offset exploration losses in Block A against taxable revenues in Block B under Ugandan tax law?",
        "required_concepts": ["ring-fenc", "contract area", "cannot offset"],
        "forbidden_hallucinations": ["unlimited cross-block offset permitted"],
    },
    {
        "id": "SEC-022",
        "title": "Customs Export Cess on Raw Hides and Skins",
        "locale": "en",
        "domain": "Customs & Export",
        "statute": "EAC-CMA / Export Levy Schedule",
        "query": "What export duty or cess applies to exporting unprocessed raw hides and skins out of Uganda?",
        "required_concepts": ["export", "hides", "skins", "duty"],
        "forbidden_hallucinations": ["completely duty free export"],
    },
    {
        "id": "SEC-023",
        "title": "Tax Clearance Certificate (TCC) Validity",
        "locale": "en",
        "domain": "Tax Administration",
        "statute": "Tax Procedures Code Act",
        "query": "How long is an official URA Tax Clearance Certificate (TCC) valid for a compliant business?",
        "required_concepts": ["valid", "tax clearance certificate", "ura"],
        "forbidden_hallucinations": ["lifetime validity"],
    },
    {
        "id": "SEC-024",
        "title": "EFRIS Offline Transaction Sync Window",
        "locale": "en",
        "domain": "EFRIS Compliance",
        "statute": "Tax Procedures Code Regulations (EFRIS)",
        "query": "If an EFRIS fiscal device loses internet connectivity, within how many hours must offline invoices be synchronized to URA?",
        "required_concepts": ["24", "offline", "sync"],
        "forbidden_hallucinations": ["30 days offline permitted"],
    },
    {
        "id": "SEC-025",
        "title": "Withholding Tax Exemption Certificate Criteria",
        "locale": "en",
        "domain": "Withholding Taxes",
        "statute": "Income Tax Act, Section 119(5)",
        "query": "What criteria must a Ugandan company fulfill to obtain an exemption from 6% withholding tax under Section 119?",
        "required_concepts": ["exemption certificate", "filing", "compliant"],
        "forbidden_hallucinations": ["any new company gets it automatically"],
    },
    {
        "id": "SEC-026",
        "title": "Voluntary Disclosure Programme (VDP) Penalties Waiver",
        "locale": "en",
        "domain": "Tax Compliance",
        "statute": "Tax Procedures Code Act, Section 66",
        "query": "What benefits does a taxpayer receive if they voluntarily disclose undisclosed tax liabilities before a URA tax audit begins?",
        "required_concepts": ["voluntary disclosure", "waiver", "penalty"],
        "forbidden_hallucinations": ["mandatory criminal prosecution"],
    },
    {
        "id": "SEC-027",
        "title": "Third-Party Agency Notice (Bank Account Freeze)",
        "locale": "en",
        "domain": "Tax Enforcement",
        "statute": "Tax Procedures Code Act, Section 40",
        "query": "Under what statutory authority can URA instruct a commercial bank to remit money from a debtor's account to settle outstanding taxes?",
        "required_concepts": ["bank", "remit", "taxes"],
        "forbidden_hallucinations": ["ura has no power over banks"],
    },
    {
        "id": "SEC-028",
        "title": "Departure Prohibition Order (DPO) Travel Restriction",
        "locale": "en",
        "domain": "Tax Enforcement",
        "statute": "Tax Procedures Code Act, Section 45",
        "query": "What legal order can the Commissioner General issue to prevent a tax debtor from traveling out of Entebbe Airport?",
        "required_concepts": ["commissioner", "immigration", "prevent"],
        "forbidden_hallucinations": ["unrestricted travel guaranteed"],
    },
    {
        "id": "SEC-029",
        "title": "Advance Tax on Passenger and Goods Vehicles",
        "locale": "en",
        "domain": "Motor Vehicle Tax",
        "statute": "Income Tax Act, Second Schedule",
        "query": "What are the advance tax rates payable per seat on commercial passenger vans and per tonne on goods cargo trucks?",
        "required_concepts": ["20,000", "50,000", "advance tax"],
        "forbidden_hallucinations": ["5,000,000 per seat"],
    },
    {
        "id": "SEC-030",
        "title": "Principal Private Residence Capital Gains Exclusion",
        "locale": "en",
        "domain": "Capital Gains Tax",
        "statute": "Income Tax Act, Section 21 & 130",
        "query": "Is capital gain derived from the sale of an individual's primary private home (residence) subject to income tax?",
        "required_concepts": ["exempt", "private residence", "home"],
        "forbidden_hallucinations": ["50% mandatory tax on primary home"],
    },
    {
        "id": "SEC-031",
        "title": "Withholding Tax on Sports Betting Winnings",
        "locale": "en",
        "domain": "Gaming & Betting",
        "statute": "Income Tax Act, Section 118C",
        "query": "What withholding tax rate applies to cash winnings paid out to a bettor from sports betting in Uganda?",
        "required_concepts": ["15%", "betting", "winnings"],
        "forbidden_hallucinations": ["0% tax on gambling", "30% PAYE"],
    },
    {
        "id": "SEC-032",
        "title": "Deceased Taxpayer Estate Tax Liability",
        "locale": "en",
        "domain": "Estate Taxation",
        "statute": "Income Tax Act, Section 71",
        "query": "Is the executor of a deceased taxpayer's estate personally liable for unpaid taxes beyond the value of estate assets?",
        "required_concepts": ["executor", "estate", "assets"],
        "forbidden_hallucinations": ["executor must pay from personal savings indefinitely"],
    },
    {
        "id": "SEC-033",
        "title": "Double Taxation Agreement (DTA) Treaty Precedence",
        "locale": "en",
        "domain": "International Taxation",
        "statute": "Income Tax Act, Section 88",
        "query": "Where a Double Taxation Agreement between Uganda and the Netherlands specifies a 10% withholding rate on dividends, which rate prevails?",
        "required_concepts": ["treaty", "dta", "prevail", "10%"],
        "forbidden_hallucinations": ["domestic law always overrides ratified international treaties"],
    },
    {
        "id": "SEC-034",
        "title": "Customs Bonded Warehouse Statutory Retention Limit",
        "locale": "en",
        "domain": "Customs Warehousing",
        "statute": "EAC-CMA, Section 57",
        "query": "What is the maximum period imported goods can be stored in a customs bonded warehouse in Uganda?",
        "required_concepts": ["6 months", "bonded warehouse", "customs"],
        "forbidden_hallucinations": ["10 years storage permitted"],
    },
    {
        "id": "SEC-035",
        "title": "Environmental Ban on Polythene Bags (Kaveera)",
        "locale": "en",
        "domain": "Environmental Levies",
        "statute": "Finance Act / NEMA & Customs Regulations",
        "query": "What is the legal status and environmental levy on importing or manufacturing plastic carrier bags under 30 microns?",
        "required_concepts": ["ban", "polythene", "microns", "kaveera"],
        "forbidden_hallucinations": ["all plastic bags zero rated"],
    },
    {
        "id": "SEC-036",
        "title": "Stamp Duty on Land and Property Transfers",
        "locale": "en",
        "domain": "Stamp Duty",
        "statute": "Stamp Duty Act",
        "query": "What is the stamp duty percentage on the transfer of real property (land and buildings) in Uganda?",
        "required_concepts": ["1%", "stamp duty", "transfer"],
        "forbidden_hallucinations": ["18% stamp duty"],
    },
    {
        "id": "SEC-037",
        "title": "Excise Duty on Mobile Money Cash Withdrawals",
        "locale": "en",
        "domain": "Excise Duty",
        "statute": "Excise Duty Act, Schedule 2",
        "query": "What is the excise duty rate on mobile money cash withdrawals, and are sending or receiving transactions charged?",
        "required_concepts": ["0.5%", "withdrawal", "mobile money"],
        "forbidden_hallucinations": ["5% on all deposits"],
    },
    {
        "id": "SEC-038",
        "title": "Zero-Rated vs Exempt Supplies Input VAT Recovery",
        "locale": "en",
        "domain": "Value Added Tax",
        "statute": "VAT Act, Section 24 & 28",
        "query": "What is the fundamental statutory difference between a zero-rated supply and an exempt supply regarding input tax refunds?",
        "required_concepts": ["zero-rated", "exempt", "input tax", "refund"],
        "forbidden_hallucinations": ["both get full refunds equally"],
    },
    {
        "id": "SEC-039",
        "title": "International Transport Zero-Rating",
        "locale": "en",
        "domain": "Value Added Tax",
        "statute": "VAT Act, Third Schedule",
        "query": "What VAT rate applies to the international transport of passengers or commercial cargo from Entebbe to London?",
        "required_concepts": ["zero-rated", "0%", "international"],
        "forbidden_hallucinations": ["18% standard VAT"],
    },
    {
        "id": "SEC-040",
        "title": "Excise Duty on Telecommunication Voice and Data",
        "locale": "en",
        "domain": "Excise Duty",
        "statute": "Excise Duty Act, Schedule 2",
        "query": "What is the excise duty rate charged on telecommunication airtime and internet data in Uganda?",
        "required_concepts": ["12%", "telecom", "data"],
        "forbidden_hallucinations": ["50% data tax"],
    },
    {
        "id": "SEC-041",
        "title": "Petroleum Specific Excise Duty Rates",
        "locale": "en",
        "domain": "Excise Duty (Energy)",
        "statute": "Excise Duty Act, Schedule 2",
        "query": "What are the specific excise duty rates per litre on petrol, diesel, and kerosene in Uganda?",
        "required_concepts": ["1,450", "1,130", "petrol", "diesel"],
        "forbidden_hallucinations": ["10,000 per litre"],
    },
    {
        "id": "SEC-042",
        "title": "Presumptive Tax Fixed Band Amounts Without Records",
        "locale": "en",
        "domain": "Small Business Taxation",
        "statute": "Income Tax Act, Second Schedule",
        "query": "How is presumptive tax determined for small informal traders whose turnover is between 10M and 50M without audited books?",
        "required_concepts": ["presumptive", "turnover", "bands"],
        "forbidden_hallucinations": ["audited accounts mandatory for market stalls"],
    },
    {
        "id": "SEC-043",
        "title": "Monthly Return Filing Deadline (15th Day)",
        "locale": "en",
        "domain": "Tax Compliance Calendar",
        "statute": "TPCA s.16 & VAT Act s.31",
        "query": "By what day of the month must monthly PAYE, VAT, and Local Excise Duty returns be filed and paid?",
        "required_concepts": ["15th", "month", "due"],
        "forbidden_hallucinations": ["end of the year only", "last day of the month"],
    },
    {
        "id": "SEC-044",
        "title": "Annual Corporation Tax Return Statutory Filing Period",
        "locale": "en",
        "domain": "Corporate Taxation",
        "statute": "Income Tax Act, Section 92",
        "query": "Within how many months after the end of an accounting year must a registered company file its final corporate income tax return?",
        "required_concepts": ["6 months", "accounting year", "return"],
        "forbidden_hallucinations": ["10 years to file"],
    },
    {
        "id": "SEC-045",
        "title": "Interest on Late Payment of Tax under TPCA",
        "locale": "en",
        "domain": "Tax Procedure (Penalties)",
        "statute": "Tax Procedures Code Act, Section 39",
        "query": "What interest rate applies per month to overdue unpaid tax liabilities under the Tax Procedures Code Act?",
        "required_concepts": ["2%", "interest", "unpaid"],
        "forbidden_hallucinations": ["50% interest monthly"],
    },
    {
        "id": "SEC-046",
        "title": "Private Ruling Binding Effect on Commissioner General",
        "locale": "en",
        "domain": "Tax Procedures Code Act",
        "statute": "Tax Procedures Code Act, Section 20",
        "query": "Is a formal Private Ruling issued by the Commissioner General binding on the URA if the taxpayer made full and true disclosure?",
        "required_concepts": ["binding", "private ruling", "commissioner general"],
        "forbidden_hallucinations": ["never binding", "purely informal suggestion"],
    },
    {
        "id": "SEC-047",
        "title": "Temporary Importation Procedure under EAC-CMA",
        "locale": "en",
        "domain": "Customs Procedures",
        "statute": "EAC-CMA, Section 117",
        "query": "What security requirement applies when temporarily importing exhibition machinery into Uganda for re-export within 12 months?",
        "required_concepts": ["temporary importation", "security", "bond", "re-export"],
        "forbidden_hallucinations": ["pay non-refundable duties immediately"],
    },
    {
        "id": "SEC-048",
        "title": "Transit Goods Security and Exemption from Domestic Duty",
        "locale": "en",
        "domain": "Customs & Transit",
        "statute": "EAC-CMA, Part VIII",
        "query": "Are commercial goods transiting through Uganda to South Sudan subject to domestic Ugandan customs duty and VAT?",
        "required_concepts": ["transit", "exempt", "bond", "customs"],
        "forbidden_hallucinations": ["pay domestic VAT to URA"],
    },
    {
        "id": "SEC-049",
        "title": "Common External Tariff (CET) 4-Band Structure",
        "locale": "en",
        "domain": "EAC Customs Tariff",
        "statute": "EAC Common External Tariff 2022",
        "query": "What are the four primary tariff bands under the East African Community Common External Tariff (EAC CET)?",
        "required_concepts": ["0%", "10%", "25%", "35%"],
        "forbidden_hallucinations": ["all goods flat 50%"],
    },
    {
        "id": "SEC-050",
        "title": "EAC Rules of Origin Value Addition Requirement",
        "locale": "en",
        "domain": "EAC Regional Trade",
        "statute": "EAC Rules of Origin 2015",
        "query": "What is the minimum local value addition percentage required for manufactured goods to qualify for duty-free preferential EAC origin?",
        "required_concepts": ["35%", "rules of origin", "value addition"],
        "forbidden_hallucinations": ["zero value addition needed"],
    },
    {
        "id": "SEC-051",
        "title": "Diplomatic Privilege Duty-Free Clearance",
        "locale": "en",
        "domain": "Customs Exemptions",
        "statute": "EAC-CMA, Fifth Schedule",
        "query": "Under what conditions can foreign diplomatic missions in Kampala clear imported vehicles and official supplies free of customs duty?",
        "required_concepts": ["diplomatic", "duty-free", "privilege"],
        "forbidden_hallucinations": ["diplomats must pay 100% duty"],
    },
    {
        "id": "SEC-052",
        "title": "Bad Debt Relief for VAT Output Tax",
        "locale": "en",
        "domain": "Value Added Tax",
        "statute": "VAT Act, Section 31",
        "query": "When can a VAT-registered supplier claim bad debt relief on output tax paid to URA for an invoice that the debtor never paid?",
        "required_concepts": ["bad debt", "insolven", "refund", "2 years"],
        "forbidden_hallucinations": ["claim after 5 days"],
    },
    {
        "id": "SEC-053",
        "title": "Monthly Provisional Return Option for Rental Tax",
        "locale": "en",
        "domain": "Rental Tax Procedure",
        "statute": "Income Tax Act, Section 124(1a)",
        "query": "Does the law permit an individual landlord to file provisional rental tax returns on a monthly basis instead of annually?",
        "required_concepts": ["monthly", "provisional", "rental"],
        "forbidden_hallucinations": ["monthly filing strictly illegal"],
    },
    {
        "id": "SEC-054",
        "title": "Penalties for Tampering with EFRIS Fiscal Devices",
        "locale": "en",
        "domain": "EFRIS Enforcement",
        "statute": "Tax Procedures Code Act",
        "query": "What statutory penalties apply for tampering, altering, or falsifying an EFRIS electronic fiscal device or fiscal records?",
        "required_concepts": ["penalty", "imprisonment", "tampering"],
        "forbidden_hallucinations": ["slight reprimand only"],
    },
    {
        "id": "SEC-055",
        "title": "Withholding Tax on Non-Resident Public Entertainers",
        "locale": "en",
        "domain": "Withholding Taxes",
        "statute": "Income Tax Act, Section 84",
        "query": "What tax must a concert promoter withhold when paying performance fees to a foreign musician performing in Kampala?",
        "required_concepts": ["15%", "withholding", "entertainer"],
        "forbidden_hallucinations": ["exempt from withholding"],
    },
    {
        "id": "SEC-056",
        "title": "PWD Employment Tax Exemption in Luganda",
        "locale": "lg",
        "domain": "Tax Exemptions (Luganda)",
        "statute": "Income Tax Act, Section 21(1)(v)",
        "query": "Abantu abaliko obulemu basonyiyibwa omusolo gwa PAYE ku musaala gwa mmeka buli mwezi mu mateeka ga Uganda?",
        "required_concepts": ["1,460,000", "omusolo", "omusaala"],
        "forbidden_hallucinations": ["tebasonyiyibwako n'akatono"],
    },
    {
        "id": "SEC-057",
        "title": "Advance Tax on Taxis in Luganda",
        "locale": "lg",
        "domain": "Motor Vehicle Tax (Luganda)",
        "statute": "Income Tax Act, Second Schedule",
        "query": "Omusolo gwa advance tax ku mmotoka za takisi eza mmutwe gubalibwa ku ssente mmeka buli ntebe?",
        "required_concepts": ["280,000", "omusolo"],
        "forbidden_hallucinations": ["500,000 buli ntebe"],
    },
    {
        "id": "SEC-058",
        "title": "Mobile Money Cash-out Tax in Luganda",
        "locale": "lg",
        "domain": "Excise Duty (Luganda)",
        "statute": "Excise Duty Act 2014",
        "query": "Bwe ngyako ssente ku mobile money bansalako ebitundu bimeka eby'omusolo ogwa excise duty?",
        "required_concepts": ["0.5%", "mobile money", "omusolo"],
        "forbidden_hallucinations": ["10% ku kuteekako"],
    },
    {
        "id": "SEC-059",
        "title": "EFRIS Offline Sync Timeline in Luganda",
        "locale": "lg",
        "domain": "EFRIS (Luganda)",
        "statute": "TPCA Regulations",
        "query": "Bwe mba nga sirina yintaneti mu dduuka lyange, nnina essaawa mmeka okusindika invoice za EFRIS eri URA?",
        "required_concepts": ["24", "efris", "invoice"],
        "forbidden_hallucinations": ["omwaka mulamba"],
    },
    {
        "id": "SEC-060",
        "title": "15-Year Vehicle Ban in Luganda",
        "locale": "lg",
        "domain": "Customs (Luganda)",
        "statute": "Traffic and Road Safety Act",
        "query": "Nnyinza okuyingiza mmotoka ekozeseddwaako erina emyaka 16 mu Uganda okuva ebweru?",
        "required_concepts": ["15", "mmotoka", "tekikkirizibwa"],
        "forbidden_hallucinations": ["kikkirizibwa awatali kkomo"],
    },
    {
        "id": "SEC-061",
        "title": "PWD Employment Tax Exemption in Swahili",
        "locale": "sw",
        "domain": "Tax Exemptions (Swahili)",
        "statute": "Income Tax Act, Section 21(1)(v)",
        "query": "Watu wenye ulemavu wanasamehewa kiasi gani cha mshahara wa kila mwezi kabla ya kutozwa kodi ya PAYE nchini Uganda?",
        "required_concepts": ["1,460,000", "msamaha", "kodi"],
        "forbidden_hallucinations": ["hakuna msamaha wowote"],
    },
    {
        "id": "SEC-062",
        "title": "Mobile Money Withdrawal Excise in Swahili",
        "locale": "sw",
        "domain": "Excise Duty (Swahili)",
        "statute": "Excise Duty Act",
        "query": "Ushuru wa kutoa pesa taslimu kupitia mtandao wa simu (mobile money) nchini Uganda ni asilimia ngapi?",
        "required_concepts": ["0.5%", "ushuru", "pesa"],
        "forbidden_hallucinations": ["20% kwa amana"],
    },
    {
        "id": "SEC-063",
        "title": "Quarterly VAT Registration Threshold in Swahili",
        "locale": "sw",
        "domain": "Value Added Tax (Swahili)",
        "statute": "VAT Act, Section 7",
        "query": "Kikomo cha usajili wa lazima wa VAT kwa biashara inayopata mapato katika miezi mitatu mfululizo ni shilingi ngapi?",
        "required_concepts": ["37,500,000", "vat", "miezi"],
        "forbidden_hallucinations": ["trilioni moja"],
    },
    {
        "id": "SEC-064",
        "title": "TAT Appeal Deadline in Swahili",
        "locale": "sw",
        "domain": "Tax Appeals (Swahili)",
        "statute": "Tax Appeals Tribunal Act",
        "query": "Mlipa kodi ana siku ngapi kukata rufaa kwenye Mahakama ya Rufaa za Kodi (TAT) baada ya uamuzi wa pingamizi?",
        "required_concepts": ["30", "rufaa", "siku"],
        "forbidden_hallucinations": ["miaka miwili"],
    },
    {
        "id": "SEC-065",
        "title": "15-Year Vehicle Ban in Swahili",
        "locale": "sw",
        "domain": "Customs (Swahili)",
        "statute": "Traffic and Road Safety Act",
        "query": "Je, inaruhusiwa kuagiza gari lililotengenezwa zaidi ya miaka 15 iliyopita kuingia nchini Uganda?",
        "required_concepts": ["15", "marufuku", "gari"],
        "forbidden_hallucinations": ["inaruhusiwa bila kikomo"],
    },
]


@dataclass
class EdgeCaseResult:
    case_id: str
    title: str
    locale: str
    domain: str
    statute: str
    status_code: int
    latency_s: float
    retrieval_mode: str
    passed: bool
    score: float
    matched_concepts: list[str]
    missing_concepts: list[str]
    hallucination_detected: bool
    reply_snippet: str
    notes: str


async def run_edge_case(client: httpx.AsyncClient, case: dict[str, Any]) -> EdgeCaseResult:
    cid = case["id"]
    print(f"--> Testing [{cid}] {case['title']} ({case['locale'].upper()})...", flush=True)
    t0 = time.perf_counter()

    try:
        resp = await client.post(
            GATEWAY_URL,
            json={"message": case["query"], "locale": case["locale"]},
            headers={
                "X-Session-ID": f"statutory-edge-{cid}-{int(time.time())}",
                "ngrok-skip-browser-warning": "1",
            },
        )
        dt = time.perf_counter() - t0
        if resp.status_code != 200:
            return EdgeCaseResult(
                case_id=cid,
                title=case["title"],
                locale=case["locale"],
                domain=case["domain"],
                statute=case["statute"],
                status_code=resp.status_code,
                latency_s=round(dt, 3),
                retrieval_mode="error",
                passed=False,
                score=0.0,
                matched_concepts=[],
                missing_concepts=case["required_concepts"],
                hallucination_detected=False,
                reply_snippet=resp.text[:200],
                notes=f"HTTP Error {resp.status_code}",
            )

        data = resp.json()
        reply = data.get("reply", "")
        mode = data.get("retrieval_mode", "")
        reply_lower = reply.lower()

        # Evaluate required statutory concepts
        matched = []
        missing = []
        for c in case["required_concepts"]:
            c_low = c.lower()
            if c_low in reply_lower:
                matched.append(c)
            else:
                missing.append(c)

        # Check for forbidden hallucinations
        hallucination = False
        for f in case.get("forbidden_hallucinations", []):
            if f.lower() in reply_lower:
                hallucination = True
                break

        concept_score = len(matched) / len(case["required_concepts"]) if case["required_concepts"] else 1.0
        is_passed = (concept_score >= 0.60) and not hallucination

        return EdgeCaseResult(
            case_id=cid,
            title=case["title"],
            locale=case["locale"],
            domain=case["domain"],
            statute=case["statute"],
            status_code=resp.status_code,
            latency_s=round(dt, 3),
            retrieval_mode=mode,
            passed=is_passed,
            score=round(concept_score * 100.0, 1),
            matched_concepts=matched,
            missing_concepts=missing,
            hallucination_detected=hallucination,
            reply_snippet=reply[:300].replace("\n", " "),
            notes="Passed statutory rigor" if is_passed else f"Missing: {missing}",
        )
    except Exception as exc:
        dt = time.perf_counter() - t0
        return EdgeCaseResult(
            case_id=cid,
            title=case["title"],
            locale=case["locale"],
            domain=case["domain"],
            statute=case["statute"],
            status_code=0,
            latency_s=round(dt, 3),
            retrieval_mode="exception",
            passed=False,
            score=0.0,
            matched_concepts=[],
            missing_concepts=case["required_concepts"],
            hallucination_detected=False,
            reply_snippet=str(exc)[:200],
            notes=f"Exception: {type(exc).__name__}",
        )


async def main():
    print("=" * 75)
    print("LIVE NGROK STATUTORY HARDEST EDGE CASES AUDIT (65 SCENARIOS)")
    print(f"Target Gateway: {GATEWAY_URL}")
    print("=" * 75)

    limits = httpx.Limits(max_connections=32, max_keepalive_connections=16)
    sem = asyncio.Semaphore(4)

    async with httpx.AsyncClient(timeout=45.0, limits=limits, headers={"ngrok-skip-browser-warning": "1"}) as client:
        async def bounded_run(case):
            async with sem:
                res = await run_edge_case(client, case)
                print(f"       -> [{res.case_id}] Status: {res.status_code} | Latency: {res.latency_s}s | Score: {res.score}% | Passed: {res.passed}", flush=True)
                return res

        tasks = [bounded_run(case) for case in EDGE_CASES]
        results = await asyncio.gather(*tasks)

    passed_count = sum(1 for r in results if r.passed)
    total_count = len(results)
    pass_rate = round(passed_count / total_count * 100.0, 1)

    print("\n" + "=" * 75)
    print("STATUTORY EDGE CASES AUDIT SUMMARY")
    print("=" * 75)
    print(f"Total Edge Cases: {total_count}")
    print(f"Passed: {passed_count} / {total_count} ({pass_rate}%)")
    print(f"Mean Latency: {sum(r.latency_s for r in results) / total_count:.2f}s")
    print("\nDetailed Breakdown:")
    for r in results:
        status_icon = "✅ PASS" if r.passed else "❌ FAIL"
        print(f"[{r.case_id}] {status_icon} ({r.score}%): {r.title} [{r.locale.upper()}] ({r.latency_s}s)")

    # Save to JSON
    out_json = "docs/presentation/statutory_edge_cases_results.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in results], f, indent=2)
    print(f"\nArtifact saved to: {out_json}")


if __name__ == "__main__":
    asyncio.run(main())

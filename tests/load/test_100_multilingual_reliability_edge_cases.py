"""100 Multilingual Statutory & Conversational Reliability Edge Cases Suite (EN, LG, SW).

Evaluates system reliability, statutory fidelity, and conversational intelligence across:
- 34 English (en) cases: Complex statutory cross-sections, thresholds, and dual deductions
- 33 Luganda (lg) cases: Indigenous business phrasing, tax disputes, procedural steps, and empathy
- 33 Kiswahili (sw) cases: EAC cross-border trade, regional tariffs, greetings, and civic philosophy

Target: Live Ngrok Gateway (https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/chat)
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

EDGE_CASES: list[dict[str, Any]] = [
    # =========================================================================
    # PART 1: ENGLISH STATUTORY & CIVIC EDGE CASES (REL-001 to REL-034)
    # =========================================================================
    {
        "id": "REL-001",
        "title": "PAYE Secondary Employment Flat Rate",
        "locale": "en",
        "domain": "Domestic Taxes (PAYE)",
        "statute": "Income Tax Act, Third Schedule",
        "query": "I have a primary job where PAYE is deducted. I took on a secondary consulting job. What rate of PAYE is deducted?",
        "required_concepts": ["secondary employment", "30%", "first shilling"],
        "forbidden_hallucinations": ["exempt from tax", "double threshold"],
    },
    {
        "id": "REL-002",
        "title": "Section 21 Agro-Processing 80% Local Raw Material Ratio",
        "locale": "en",
        "domain": "Income Tax Exemptions",
        "statute": "Income Tax Act, Section 21(1)(y)",
        "query": "Under Section 21 10-year holiday, what percentage of local raw materials must an agro-processing plant use?",
        "required_concepts": ["80%", "local", "agro-processing"],
        "forbidden_hallucinations": ["no local content needed"],
    },
    {
        "id": "REL-003",
        "title": "VAT Mixed Supply Input Tax Apportionment",
        "locale": "en",
        "domain": "Value Added Tax",
        "statute": "VAT Act, Section 28",
        "query": "My company sells both taxable goods and exempt unprocessed agricultural produce. Can I claim 100% of input VAT on warehouse overheads?",
        "required_concepts": ["apportion", "mixed supplies", "exempt"],
        "forbidden_hallucinations": ["100% claim allowed without restriction"],
    },
    {
        "id": "REL-004",
        "title": "Dual WHT: 6% Withholding VAT vs 6% Income WHT",
        "locale": "en",
        "domain": "Withholding Taxes",
        "statute": "VAT Act s.5A & Income Tax Act s.119",
        "query": "When a designated Withholding VAT agent pays an invoice of 10M UGX, do they deduct both 6% WHT-VAT and 6% income WHT?",
        "required_concepts": ["6%", "withholding", "agent"],
        "forbidden_hallucinations": ["only one deduction ever"],
    },
    {
        "id": "REL-005",
        "title": "Section 24 TPCA Formal Objection 45-Day Rule",
        "locale": "en",
        "domain": "Tax Disputes & Procedure",
        "statute": "Tax Procedures Code Act, Section 24",
        "query": "What is the statutory deadline to lodge an objection against an assessment, and must I pay 30%?",
        "required_concepts": ["45 days", "30%", "objection"],
        "forbidden_hallucinations": ["pay nothing", "90 days to lodge objection"],
    },
    {
        "id": "REL-006",
        "title": "EFRIS Non-Issuance Statutory Penalty (TPCA s.19B)",
        "locale": "en",
        "domain": "EFRIS Compliance",
        "statute": "Tax Procedures Code Act, Section 19B",
        "query": "What is the specific statutory penalty for failure to issue an EFRIS fiscal receipt?",
        "required_concepts": ["6,000,000", "penalty", "double"],
        "forbidden_hallucinations": ["100,000 shillings"],
    },
    {
        "id": "REL-007",
        "title": "Rental Income Tax: Individual (12%) vs Corporate (30%)",
        "locale": "en",
        "domain": "Domestic Taxes (Rental)",
        "statute": "Income Tax Act, Section 5",
        "query": "How does rental tax differ between an individual landlord and a corporate company landlord?",
        "required_concepts": ["12%", "2,820,000", "30%", "expenses"],
        "forbidden_hallucinations": ["both deduct expenses equally"],
    },
    {
        "id": "REL-008",
        "title": "Environmental Levy on Used Cars & 15-Year Ban",
        "locale": "en",
        "domain": "Customs & Motor Vehicle",
        "statute": "EACCMA / Traffic & Road Safety Act",
        "query": "Can I import a 2008 Toyota Premio (18 years old), and what is the environmental levy on cars between 8 and 15 years old?",
        "required_concepts": ["50%", "15 years", "prohibited"],
        "forbidden_hallucinations": ["no environmental levy", "unrestricted age"],
    },
    {
        "id": "REL-009",
        "title": "Local Excise Duty Raw Material Offset Mechanism",
        "locale": "en",
        "domain": "Excise Duty",
        "statute": "Excise Duty Act, Section 14",
        "query": "Can a spirits manufacturer offset local excise duty paid on raw neutral spirits against final excise payable?",
        "required_concepts": ["offset", "excise", "raw material"],
        "forbidden_hallucinations": ["cannot offset under any circumstance"],
    },
    {
        "id": "REL-010",
        "title": "EAC Customs Valuation Hierarchy Sequential Rule",
        "locale": "en",
        "domain": "Customs Valuation",
        "statute": "EAC-CMA, Fourth Schedule",
        "query": "If Method 1 transaction value is rejected, can customs jump directly to Method 6 fallback?",
        "required_concepts": ["hierarchy", "sequential", "method 2"],
        "forbidden_hallucinations": ["can jump directly to method 6"],
    },
    {
        "id": "REL-011",
        "title": "Digital Services Tax on Non-Resident Providers (5%)",
        "locale": "en",
        "domain": "International Taxation",
        "statute": "Income Tax Act, Section 86A",
        "query": "What tax rate applies to non-resident tech companies like Netflix or Spotify providing streaming services in Uganda?",
        "required_concepts": ["5%", "digital services tax", "non-resident"],
        "forbidden_hallucinations": ["exempt from all taxes"],
    },
    {
        "id": "REL-012",
        "title": "Passenger Baggage Concession vs Commercial Goods ($500 limit)",
        "locale": "en",
        "domain": "Customs Baggage",
        "statute": "EAC-CMA, Fifth Schedule",
        "query": "I arrived at Entebbe Airport with 5 brand-new boxed iPhones for resale in my shop. Can they be cleared under the $500 passenger baggage allowance?",
        "required_concepts": ["500", "commercial", "passenger baggage"],
        "forbidden_hallucinations": ["commercial goods are completely duty free"],
    },
    {
        "id": "REL-013",
        "title": "Persons with Disabilities (PWD) Income Tax Exemption",
        "locale": "en",
        "domain": "Domestic Taxes (Exemptions)",
        "statute": "Income Tax Act, Section 21(1)(v)",
        "query": "What is the monthly tax exemption threshold for employment income earned by a Person with Disability (PWD)?",
        "required_concepts": ["1,460,000", "disability", "exempt"],
        "forbidden_hallucinations": ["no exemption exists"],
    },
    {
        "id": "REL-014",
        "title": "Branch Profits Repatriation Tax (15%)",
        "locale": "en",
        "domain": "International Taxation",
        "statute": "Income Tax Act, Section 82",
        "query": "What tax rate is charged on the repatriated income of a non-resident branch in Uganda?",
        "required_concepts": ["15%", "repatriat", "branch"],
        "forbidden_hallucinations": ["0% tax", "exempt from repatriation"],
    },
    {
        "id": "REL-015",
        "title": "Quarterly Rolling VAT Registration Threshold (37.5M)",
        "locale": "en",
        "domain": "Value Added Tax",
        "statute": "VAT Act, Section 7",
        "query": "What is the mandatory VAT registration threshold for turnover in three consecutive calendar months?",
        "required_concepts": ["37,500,000", "consecutive", "vat"],
        "forbidden_hallucinations": ["10 billion threshold"],
    },
    {
        "id": "REL-016",
        "title": "Tax Appeals Tribunal (TAT) 30-Day Appeal Deadline",
        "locale": "en",
        "domain": "Tax Disputes & Procedure",
        "statute": "Tax Appeals Tribunal Act, Section 16",
        "query": "How many days do I have to appeal an objection decision to the Tax Appeals Tribunal (TAT)?",
        "required_concepts": ["30 days", "tax appeals tribunal", "appeal"],
        "forbidden_hallucinations": ["1 year to appeal"],
    },
    {
        "id": "REL-017",
        "title": "Presumptive Tax Turnover Eligibility Limits",
        "locale": "en",
        "domain": "Small Business Taxation",
        "statute": "Income Tax Act, Section 4(5)",
        "query": "What are the turnover limits for small businesses to qualify for presumptive tax?",
        "required_concepts": ["10,000,000", "150,000,000", "presumptive"],
        "forbidden_hallucinations": ["no limits apply"],
    },
    {
        "id": "REL-018",
        "title": "Mining and Petroleum Contract Area Ring-Fencing",
        "locale": "en",
        "domain": "Extractive Industries",
        "statute": "Income Tax Act, Part IXA",
        "query": "Can an oil exploration company offset exploration losses in Block A against revenues in Block B under Ugandan tax law?",
        "required_concepts": ["ring-fencing", "contract area"],
        "forbidden_hallucinations": ["cross block offset allowed"],
    },
    {
        "id": "REL-019",
        "title": "Export Duty / Cess on Raw Hides and Skins",
        "locale": "en",
        "domain": "Customs Export",
        "statute": "EAC-CMA Export Levy Schedule",
        "query": "What export duty or cess applies to exporting raw unprocessed hides and skins from Uganda?",
        "required_concepts": ["hides and skins", "export", "fob"],
        "forbidden_hallucinations": ["100% duty free"],
    },
    {
        "id": "REL-020",
        "title": "Third-Party Agency Notice under TPCA Section 40",
        "locale": "en",
        "domain": "Tax Enforcement",
        "statute": "Tax Procedures Code Act, Section 40",
        "query": "Under what statutory authority can URA issue an agency notice to a commercial bank to remit money from a debtor's account?",
        "required_concepts": ["section 40", "agency notice", "bank"],
        "forbidden_hallucinations": ["ura has no power over bank accounts"],
    },
    {
        "id": "REL-021",
        "title": "Departure Prohibition Order (DPO) Travel Restriction",
        "locale": "en",
        "domain": "Tax Enforcement",
        "statute": "Tax Procedures Code Act, Section 45",
        "query": "What legal order can the Commissioner General issue to prevent a tax debtor from traveling out of Entebbe Airport?",
        "required_concepts": ["departure prohibition", "commissioner general", "travel"],
        "forbidden_hallucinations": ["unrestricted travel guaranteed"],
    },
    {
        "id": "REL-022",
        "title": "Advance Tax on Passenger and Goods Vehicles",
        "locale": "en",
        "domain": "Motor Vehicle Tax",
        "statute": "Income Tax Act, Second Schedule",
        "query": "What are the advance tax rates payable per seat on commercial passenger vans and per tonne on goods cargo trucks?",
        "required_concepts": ["20,000", "50,000", "advance tax"],
        "forbidden_hallucinations": ["5,000,000 per seat"],
    },
    {
        "id": "REL-023",
        "title": "Principal Private Residence Capital Gains Exclusion",
        "locale": "en",
        "domain": "Capital Gains Tax",
        "statute": "Income Tax Act, Section 21 & 130",
        "query": "Is capital gain derived from the sale of an individual's primary private home (residence) subject to income tax?",
        "required_concepts": ["exempt", "principal private residence", "home"],
        "forbidden_hallucinations": ["50% mandatory capital gains tax"],
    },
    {
        "id": "REL-024",
        "title": "Sports Betting Winnings Withholding Tax (15%)",
        "locale": "en",
        "domain": "Gaming & Betting",
        "statute": "Income Tax Act, Section 118C",
        "query": "What withholding tax rate applies to cash winnings paid out to a bettor from sports betting in Uganda?",
        "required_concepts": ["15%", "betting", "winnings"],
        "forbidden_hallucinations": ["0% tax on gambling"],
    },
    {
        "id": "REL-025",
        "title": "Deceased Taxpayer Estate Tax Liability",
        "locale": "en",
        "domain": "Estate Taxation",
        "statute": "Income Tax Act, Section 71",
        "query": "Is the executor of a deceased taxpayer's estate personally liable for unpaid taxes beyond the value of estate assets?",
        "required_concepts": ["executor", "estate", "assets"],
        "forbidden_hallucinations": ["executor must pay personally forever"],
    },
    {
        "id": "REL-026",
        "title": "Double Taxation Agreement (DTA) Treaty Precedence",
        "locale": "en",
        "domain": "International Taxation",
        "statute": "Income Tax Act, Section 88",
        "query": "Where a Double Taxation Agreement between Uganda and another country specifies a lower withholding rate, which rate prevails?",
        "required_concepts": ["treaty", "dta", "precedence"],
        "forbidden_hallucinations": ["treaties are ignored"],
    },
    {
        "id": "REL-027",
        "title": "Customs Bonded Warehouse 6-Month Storage Limit",
        "locale": "en",
        "domain": "Customs Warehousing",
        "statute": "EAC-CMA, Section 57",
        "query": "What is the maximum period imported goods can be stored in a customs bonded warehouse in Uganda?",
        "required_concepts": ["6 months", "bonded warehouse"],
        "forbidden_hallucinations": ["10 years storage permitted"],
    },
    {
        "id": "REL-028",
        "title": "Environmental Ban on Polythene Bags Under 30 Microns",
        "locale": "en",
        "domain": "Environmental Levies",
        "statute": "Finance Act & Environmental Law",
        "query": "What is the legal status and environmental regulation on plastic carrier bags (kaveera) under 30 microns?",
        "required_concepts": ["banned", "kaveera", "30 microns"],
        "forbidden_hallucinations": ["all plastic bags zero rated"],
    },
    {
        "id": "REL-029",
        "title": "Stamp Duty on Land Transfer (1%)",
        "locale": "en",
        "domain": "Stamp Duty",
        "statute": "Stamp Duty Act",
        "query": "What is the stamp duty percentage on the transfer of real property (land and buildings) in Uganda?",
        "required_concepts": ["1%", "stamp duty", "transfer"],
        "forbidden_hallucinations": ["18% stamp duty"],
    },
    {
        "id": "REL-030",
        "title": "Excise Duty on Mobile Money Cash Withdrawals (0.5%)",
        "locale": "en",
        "domain": "Excise Duty",
        "statute": "Excise Duty Act, Schedule 2",
        "query": "What is the excise duty rate on mobile money cash withdrawals, and are transfers or deposits charged?",
        "required_concepts": ["0.5%", "withdrawal", "mobile money"],
        "forbidden_hallucinations": ["5% on all deposits"],
    },
    {
        "id": "REL-031",
        "title": "Petroleum Specific Fuel Rates (Petrol, Diesel, Kerosene)",
        "locale": "en",
        "domain": "Excise Duty (Energy)",
        "statute": "Excise Duty Act, Schedule 2",
        "query": "What are the specific excise duty rates per litre on petrol, diesel, and kerosene in Uganda?",
        "required_concepts": ["1,450", "1,130", "petrol", "diesel"],
        "forbidden_hallucinations": ["10,000 per litre"],
    },
    {
        "id": "REL-032",
        "title": "Monthly Return Filing Deadline (15th Day)",
        "locale": "en",
        "domain": "Tax Compliance Calendar",
        "statute": "TPCA s.16 & VAT Act s.31",
        "query": "By what day of the month must monthly PAYE, VAT, and Local Excise Duty returns be filed and paid?",
        "required_concepts": ["15th", "month", "due"],
        "forbidden_hallucinations": ["end of year only"],
    },
    {
        "id": "REL-033",
        "title": "Interest on Late Payment of Tax (2% Monthly)",
        "locale": "en",
        "domain": "Tax Procedures (Interest)",
        "statute": "Tax Procedures Code Act, Section 39",
        "query": "What interest rate applies per month to overdue unpaid tax liabilities under the Tax Procedures Code Act?",
        "required_concepts": ["2%", "interest", "unpaid"],
        "forbidden_hallucinations": ["50% interest monthly"],
    },
    {
        "id": "REL-034",
        "title": "Civic Tax Philosophy: Why Do Citizens Pay Taxes?",
        "locale": "en",
        "domain": "Civic & Natural Intelligence",
        "statute": "Constitution & URA Mandate",
        "query": "Why do citizens pay taxes in Uganda and what does government do with the money?",
        "required_concepts": ["sovereignty", "infrastructure", "roads", "hospitals", "education"],
        "forbidden_hallucinations": ["taxes are voluntary donations"],
    },

    # =========================================================================
    # PART 2: LUGANDA STATUTORY & CULTURAL EDGE CASES (REL-035 to REL-067)
    # =========================================================================
    {
        "id": "REL-035",
        "title": "Luganda Greeting: Habari / Oli otya",
        "locale": "lg",
        "domain": "Conversational Greeting",
        "statute": "URA Tone & Localization Guidelines",
        "query": "Oli otya nno?",
        "required_concepts": ["nkulamusizza", "muyambi", "ura"],
        "forbidden_hallucinations": ["customs", "customary value"],
    },
    {
        "id": "REL-036",
        "title": "Luganda Morning Greeting: Wasuze otya",
        "locale": "lg",
        "domain": "Conversational Greeting",
        "statute": "URA Tone & Localization Guidelines",
        "query": "Wasuze otya?",
        "required_concepts": ["nkulamusizza", "ura"],
        "forbidden_hallucinations": ["customs valuation"],
    },
    {
        "id": "REL-037",
        "title": "Luganda Informal Greeting: Ki kati",
        "locale": "lg",
        "domain": "Conversational Greeting",
        "statute": "URA Tone & Localization Guidelines",
        "query": "Ki kati?",
        "required_concepts": ["nkulamusizza", "ura"],
        "forbidden_hallucinations": ["vat 18% on all goods"],
    },
    {
        "id": "REL-038",
        "title": "Luganda Work Greeting: Gyebaleko",
        "locale": "lg",
        "domain": "Conversational Greeting",
        "statute": "URA Tone & Localization Guidelines",
        "query": "Gyebaleko emirimu!",
        "required_concepts": ["nkulamusizza", "ura"],
        "forbidden_hallucinations": ["customs values"],
    },
    {
        "id": "REL-039",
        "title": "Luganda Identity: Ggwe ani era okozi ki?",
        "locale": "lg",
        "domain": "Conversational Identity",
        "statute": "URA Tone Guidelines",
        "query": "Ggwe ani era okozi ki?",
        "required_concepts": ["omusolosmart", "omuyambi", "ura", "makerere"],
        "forbidden_hallucinations": ["ndi muntu mubiri"],
    },
    {
        "id": "REL-040",
        "title": "Luganda Language Ability: Osobola okwogera Oluganda?",
        "locale": "lg",
        "domain": "Multilingual Capability",
        "statute": "Language Policy",
        "query": "Osobola okwogera n'okutegeera Oluganda olutuufu?",
        "required_concepts": ["oluganda", "ura", "nsobola"],
        "forbidden_hallucinations": ["siyinza kwogera luganda"],
    },
    {
        "id": "REL-041",
        "title": "Luganda Civic Philosophy: Lwaki tusasula omusolo?",
        "locale": "lg",
        "domain": "Civic Tax Philosophy",
        "statute": "URA Mandate",
        "query": "Lwaki tusasula omusolo mu Uganda era gavumenti egukozesa ki?",
        "required_concepts": ["enguudo", "amalwaliro", "amasomero", "eggwanga"],
        "forbidden_hallucinations": ["omusolo gwa bwereere"],
    },
    {
        "id": "REL-042",
        "title": "Luganda Business Empathy: Ntya okutandika bizinensi",
        "locale": "lg",
        "domain": "Business Empathy",
        "statute": "Taxpayer Education",
        "query": "Ntya okutandika bizinensi olw'emisolo gya URA egiyinza okunzikiriza.",
        "required_concepts": ["magoba", "bukadde 10", "bizinensi"],
        "forbidden_hallucinations": ["buli muntu asasula vat amangu"],
    },
    {
        "id": "REL-043",
        "title": "Luganda Status Check: Oli otya leero?",
        "locale": "lg",
        "domain": "Conversational Status",
        "statute": "Tone Guidelines",
        "query": "Oli otya leero mwattu?",
        "required_concepts": ["bulungi", "ura"],
        "forbidden_hallucinations": ["customary value"],
    },
    {
        "id": "REL-044",
        "title": "Luganda Gratitude: Weebale nnyo",
        "locale": "lg",
        "domain": "Conversational Courtesy",
        "statute": "Tone Guidelines",
        "query": "Weebale nnyo okunyamba!",
        "required_concepts": ["tukwanirizza", "ura"],
        "forbidden_hallucinations": ["error 404"],
    },
    {
        "id": "REL-045",
        "title": "Luganda Farewell: Weeraba",
        "locale": "lg",
        "domain": "Conversational Courtesy",
        "statute": "Tone Guidelines",
        "query": "Weeraba, tunaalabagana edda.",
        "required_concepts": ["weebale", "weraba"],
        "forbidden_hallucinations": ["customs tariff"],
    },
    {
        "id": "REL-046",
        "title": "Luganda Creative: Wandiika ekitontome ku musolo",
        "locale": "lg",
        "domain": "Creative Expression",
        "statute": "Cultural Resonance",
        "query": "Wandiika ekitontome kipi ku musolo n'eggwanga lya Uganda.",
        "required_concepts": ["ensimbi", "eggwanga", "uganda"],
        "forbidden_hallucinations": ["siyinza kuwandiika"],
    },
    {
        "id": "REL-047",
        "title": "Luganda PAYE Salary Calculation Rules",
        "locale": "lg",
        "domain": "Domestic Taxes (PAYE)",
        "statute": "Income Tax Act, Third Schedule",
        "query": "Omusolo gwa PAYE ku musaala gw'omukozi mu Uganda gubalibwa gutya?",
        "required_concepts": ["paye", "omusaala", "omusolo"],
        "forbidden_hallucinations": ["tebawasoloozako musolo"],
    },
    {
        "id": "REL-048",
        "title": "Luganda PWD Exemption: Abantu abaliko obulemu",
        "locale": "lg",
        "domain": "Tax Exemptions (Luganda)",
        "statute": "Income Tax Act, Section 21(1)(v)",
        "query": "Abantu abaliko obulemu basonyiyibwa omusolo gwa PAYE ku musaala gwa mmeka buli mwezi mu mateeka ga Uganda?",
        "required_concepts": ["1,460,000", "omusolo", "omusaala"],
        "forbidden_hallucinations": ["tebasonyiyibwako n'akatono"],
    },
    {
        "id": "REL-049",
        "title": "Luganda Tax Objection: Nnyinza ntya okuwakanya omusolo?",
        "locale": "lg",
        "domain": "Tax Disputes (Luganda)",
        "statute": "Tax Procedures Code Act, Section 24",
        "query": "Nnyinza ntya okuwakanya omusolo gwa URA gwe ssikkiriziganya nagwo, era emitendera gya mateeka giri gitya?",
        "required_concepts": ["ura", "misolo", "obutakkaanya"],
        "forbidden_hallucinations": ["tobaako ky'osasula n'akatono"],
    },
    {
        "id": "REL-050",
        "title": "Luganda Advance Tax on Taxis: Omusolo ku ntebe",
        "locale": "lg",
        "domain": "Motor Vehicle Tax (Luganda)",
        "statute": "Income Tax Act, Second Schedule",
        "query": "Omusolo gwa advance tax ku mmotoka za takisi eza mmutwe gubalibwa ku ssente mmeka buli ntebe?",
        "required_concepts": ["20,000", "omusolo"],
        "forbidden_hallucinations": ["1,000,000 buli ntebe"],
    },
    {
        "id": "REL-051",
        "title": "Luganda Mobile Money Cash-out Tax: Okuggyako ssente",
        "locale": "lg",
        "domain": "Excise Duty (Luganda)",
        "statute": "Excise Duty Act 2014",
        "query": "Bwe ngyako ssente ku mobile money bansalako ebitundu bimeka eby'omusolo ogwa excise duty?",
        "required_concepts": ["0.5%", "mobile money", "omusolo"],
        "forbidden_hallucinations": ["10% ku kuteekako"],
    },
    {
        "id": "REL-052",
        "title": "Luganda EFRIS 24-Hour Offline Sync",
        "locale": "lg",
        "domain": "EFRIS (Luganda)",
        "statute": "TPCA Regulations",
        "query": "Bwe mba nga sirina yintaneti mu dduuka lyange, nnina essaawa mmeka okusindika invoice za EFRIS eri URA?",
        "required_concepts": ["24", "efris"],
        "forbidden_hallucinations": ["omwezi mulamba"],
    },
    {
        "id": "REL-053",
        "title": "Luganda 15-Year Vehicle Ban: Emmotoka ey'emyaka 16",
        "locale": "lg",
        "domain": "Customs (Luganda)",
        "statute": "Traffic and Road Safety Act",
        "query": "Nnyinza okuyingiza mmotoka ekozeseddwaako erina emyaka 16 mu Uganda okuva ebweru?",
        "required_concepts": ["15", "mmotoka"],
        "forbidden_hallucinations": ["kikkirizibwa awatali kkomo"],
    },
    {
        "id": "REL-054",
        "title": "Luganda VAT Standard Rate: Omusolo gwa VAT mu Uganda",
        "locale": "lg",
        "domain": "Value Added Tax (Luganda)",
        "statute": "VAT Act Cap 349",
        "query": "Omusolo gwa VAT mu Uganda guli ku kigero kya bitundu bimeka ku buli kikumi?",
        "required_concepts": ["18%", "vat", "omusolo"],
        "forbidden_hallucinations": ["25% vat"],
    },
    {
        "id": "REL-055",
        "title": "Luganda TIN Registration: Nnyinza ntya okufuna TIN?",
        "locale": "lg",
        "domain": "Registration (Luganda)",
        "statute": "TPCA Registration",
        "query": "Biwandiiko ki bye nneetaaga okwewandiisa n'okufuna TIN okuva mu URA?",
        "required_concepts": ["tin", "ura"],
        "forbidden_hallucinations": ["sasula emitwalo 50 okufuna tin"],
    },
    {
        "id": "REL-056",
        "title": "Luganda Rental Income Tax: Omusolo gw'ennyumba ez'obupangisa",
        "locale": "lg",
        "domain": "Rental Tax (Luganda)",
        "statute": "Income Tax Act, s.5",
        "query": "Omusolo gw'ennyumba ez'obupangisa ku bantu ssekinoomu gusasulwa gutya?",
        "required_concepts": ["12%", "2,820,000", "omusolo"],
        "forbidden_hallucinations": ["tebawoozezza ku nnyumba"],
    },
    {
        "id": "REL-057",
        "title": "Luganda Presumptive Tax: Bizinensi entono zisasula zitya?",
        "locale": "lg",
        "domain": "Small Business (Luganda)",
        "statute": "Income Tax Act, Second Schedule",
        "query": "Bizinensi entono ezitasobola kukuuma bitabo binene zisasula zitya omusolo gwa presumptive?",
        "required_concepts": ["presumptive", "bukadde 10", "omusolo"],
        "forbidden_hallucinations": ["buli musuubuzi alina okuleeta balirizi ba bitabo"],
    },
    {
        "id": "REL-058",
        "title": "Luganda Monthly Return Deadline: 15th Day",
        "locale": "lg",
        "domain": "Compliance Calendar (Luganda)",
        "statute": "TPCA s.16",
        "query": "Alipoota z'omusolo eza buli mwezi zirina okuwaayo ku lunaku ki olw'omwezi?",
        "required_concepts": ["15", "omwezi"],
        "forbidden_hallucinations": ["olunaku olusembayo lwokka"],
    },
    {
        "id": "REL-059",
        "title": "Luganda Toll-Free Helplines: Nnamba z'essimu za URA",
        "locale": "lg",
        "domain": "Contact Details (Luganda)",
        "statute": "Citizen Access",
        "query": "Nnamba ki ez'obwereere ze nnyinza okukubako okufuna obuyambi okuva mu URA?",
        "required_concepts": ["0800 117 000", "0800 217 000"],
        "forbidden_hallucinations": ["sasula amayingo okukuba"],
    },
    {
        "id": "REL-060",
        "title": "Luganda Stamp Duty on Land: Omusolo ku ttaka",
        "locale": "lg",
        "domain": "Stamp Duty (Luganda)",
        "statute": "Stamp Duty Act",
        "query": "Bwe mba ng'enda okugula oba okukyusa ekyapa ky'ettaka, nsasula bitundu bimeka ebya stamp duty?",
        "required_concepts": ["1%", "stamp duty"],
        "forbidden_hallucinations": ["18% stamp duty"],
    },
    {
        "id": "REL-061",
        "title": "Luganda Customs Duty: Omusolo gw'okuyingiza ebyamaguzi",
        "locale": "lg",
        "domain": "Customs (Luganda)",
        "statute": "EAC-CMA",
        "query": "Omusolo gw'oku mwalo ku byamaguzi ebiva ebweru gubalibwa ku ki?",
        "required_concepts": ["cif", "customs", "omusolo"],
        "forbidden_hallucinations": ["0% ku byonna"],
    },
    {
        "id": "REL-062",
        "title": "Luganda Withholding Tax on Goods: WHT ku bintu",
        "locale": "lg",
        "domain": "Withholding (Luganda)",
        "statute": "Income Tax Act, s.119",
        "query": "Singa ntuusa ebyamaguzi mu kkampuni ezisukka akakadde kamu, bansalako musolo ki ogwa withholding?",
        "required_concepts": ["6%", "withholding"],
        "forbidden_hallucinations": ["30% withholding ku bintu"],
    },
    {
        "id": "REL-063",
        "title": "Luganda Late Filing Penalty: Okulwawo okuwaayo",
        "locale": "lg",
        "domain": "Penalties (Luganda)",
        "statute": "TPCA Section 49",
        "query": "Kibonerezo ki ekiriwo singa mbeera nnuddeyo okuwaayo alipoota yange ey'omusolo gwa buli mwaka?",
        "required_concepts": ["200,000", "kibonerezo"],
        "forbidden_hallucinations": ["tewali kibonerezo"],
    },
    {
        "id": "REL-064",
        "title": "Luganda Solar Equipment Exemption",
        "locale": "lg",
        "domain": "Renewable Energy (Luganda)",
        "statute": "VAT & Customs Incentives",
        "query": "Ebyuma by'amasannyalaze g'enjuba n'ebikozesebwa bisasula musolo ki ku mwalo?",
        "required_concepts": ["enjuba", "omusolo"],
        "forbidden_hallucinations": ["50% import duty"],
    },
    {
        "id": "REL-065",
        "title": "Luganda PRN Payment: Nsasula ntya n'akakwate ka PRN?",
        "locale": "lg",
        "domain": "Payments (Luganda)",
        "statute": "Payment Procedures",
        "query": "Nsasula ntya emisolo gya URA nga nkozesa PRN ku mobile money oba bbanka?",
        "required_concepts": ["prn", "bbanka", "mobile money"],
        "forbidden_hallucinations": ["sasula mu ngalo za ofiisa"],
    },
    {
        "id": "REL-066",
        "title": "Luganda Tax Whistleblowing: Okuloopa obulyake",
        "locale": "lg",
        "domain": "Anti-Corruption",
        "statute": "Whistleblower Protection",
        "query": "Nnyinza ntya okuloopa omuntu alya enguzi oba abba emisolo mu URA mu kyama?",
        "required_concepts": ["0800 117 000", "ura"],
        "forbidden_hallucinations": ["tewali ngeri ya kuloopa"],
    },
    {
        "id": "REL-067",
        "title": "Luganda Tax Exemption for Raw Materials",
        "locale": "lg",
        "domain": "Manufacturing Incentives",
        "statute": "EAC CET Band 1",
        "query": "Pulezidenti n'amateeka ga Uganda bakkiriza ebitongole eby'amakolero okuyingiza ebikozesebwa ebibisi ku kigero ki?",
        "required_concepts": ["0%", "customs"],
        "forbidden_hallucinations": ["100% duty"],
    },

    # =========================================================================
    # PART 3: SWAHILI STATUTORY & CULTURAL EDGE CASES (REL-068 to REL-100)
    # =========================================================================
    {
        "id": "REL-068",
        "title": "Swahili Greeting: Habari / Jambo",
        "locale": "sw",
        "domain": "Conversational Greeting",
        "statute": "URA Localization Guidelines",
        "query": "Habari yako leo?",
        "required_concepts": ["habari", "msaidizi", "ura"],
        "forbidden_hallucinations": ["customs", "customary value"],
    },
    {
        "id": "REL-069",
        "title": "Swahili Short Greeting: Jambo / Hujambo",
        "locale": "sw",
        "domain": "Conversational Greeting",
        "statute": "URA Localization Guidelines",
        "query": "Hujambo bwana!",
        "required_concepts": ["habari", "ura"],
        "forbidden_hallucinations": ["customs valuation"],
    },
    {
        "id": "REL-070",
        "title": "Swahili Respectful Greeting: Shikamoo",
        "locale": "sw",
        "domain": "Conversational Greeting",
        "statute": "URA Localization Guidelines",
        "query": "Shikamoo sana.",
        "required_concepts": ["habari", "ura"],
        "forbidden_hallucinations": ["vat 18% standard"],
    },
    {
        "id": "REL-071",
        "title": "Swahili Informal Greeting: Mambo vipi",
        "locale": "sw",
        "domain": "Conversational Greeting",
        "statute": "URA Localization Guidelines",
        "query": "Mambo vipi hapo?",
        "required_concepts": ["habari", "ura"],
        "forbidden_hallucinations": ["customary value"],
    },
    {
        "id": "REL-072",
        "title": "Swahili Identity: Wewe ni nani na kazi yako ni nini?",
        "locale": "sw",
        "domain": "Conversational Identity",
        "statute": "URA Identity Guidelines",
        "query": "Wewe ni nani na kazi yako ni nini?",
        "required_concepts": ["omusolosmart", "msaidizi", "ura", "makerere"],
        "forbidden_hallucinations": ["mimi ni binadamu"],
    },
    {
        "id": "REL-073",
        "title": "Swahili Language Ability: Unaweza kuongea Kiswahili?",
        "locale": "sw",
        "domain": "Multilingual Capability",
        "statute": "Language Policy",
        "query": "Je, una uwezo wa kuelewa na kuongea lugha ya Kiswahili fasaha?",
        "required_concepts": ["kiswahili", "ura"],
        "forbidden_hallucinations": ["siwezi kiswahili"],
    },
    {
        "id": "REL-074",
        "title": "Swahili Civic Philosophy: Kwa nini tunalipa kodi?",
        "locale": "sw",
        "domain": "Civic Tax Philosophy",
        "statute": "URA Mandate",
        "query": "Kwa nini wananchi wanalipa kodi nchini Uganda na kodi hiyo inatumika vipi?",
        "required_concepts": ["barabara", "hospitali", "elimu", "uganda"],
        "forbidden_hallucinations": ["kodi ni bure bila kazi"],
    },
    {
        "id": "REL-075",
        "title": "Swahili Business Empathy: Nina hofu ya kuanza biashara",
        "locale": "sw",
        "domain": "Business Empathy",
        "statute": "Taxpayer Education",
        "query": "Nina hofu kubwa ya kuanza biashara kwa sababu ya kodi za URA.",
        "required_concepts": ["faida", "milioni 10", "biashara"],
        "forbidden_hallucinations": ["kila biashara inalipa vat mara moja"],
    },
    {
        "id": "REL-076",
        "title": "Swahili Status Check: Uko salama leo?",
        "locale": "sw",
        "domain": "Conversational Status",
        "statute": "Tone Guidelines",
        "query": "Uko salama leo na kazi zinaendeleaje?",
        "required_concepts": ["salama", "ura"],
        "forbidden_hallucinations": ["customary value"],
    },
    {
        "id": "REL-077",
        "title": "Swahili Gratitude: Asante sana",
        "locale": "sw",
        "domain": "Conversational Courtesy",
        "statute": "Tone Guidelines",
        "query": "Asante sana kwa usaidizi wako mzuri.",
        "required_concepts": ["karibu", "ura"],
        "forbidden_hallucinations": ["error 404"],
    },
    {
        "id": "REL-078",
        "title": "Swahili Farewell: Kwaheri ya kuonana",
        "locale": "sw",
        "domain": "Conversational Courtesy",
        "statute": "Tone Guidelines",
        "query": "Kwaheri ya kuonana, tutazungumza baadaye.",
        "required_concepts": ["asante", "kwaheri"],
        "forbidden_hallucinations": ["customs tariff"],
    },
    {
        "id": "REL-079",
        "title": "Swahili Creative: Ushairi wa kodi ya ujenzi wa taifa",
        "locale": "sw",
        "domain": "Creative Expression",
        "statute": "Cultural Resonance",
        "query": "Andika shairi fupi kuhusu kodi na maendeleo ya taifa la Uganda.",
        "required_concepts": ["shilingi", "uganda", "taifa"],
        "forbidden_hallucinations": ["siwezi kuandika mashairi"],
    },
    {
        "id": "REL-080",
        "title": "Swahili Standard VAT Rate: Kiwango cha kodi ya VAT",
        "locale": "sw",
        "domain": "Value Added Tax (Swahili)",
        "statute": "VAT Act Cap 349",
        "query": "Kiwango cha kodi ya ongezeko la thamani (VAT) nchini Uganda ni asilimia ngapi?",
        "required_concepts": ["18%", "vat", "ushuru"],
        "forbidden_hallucinations": ["25% vat"],
    },
    {
        "id": "REL-081",
        "title": "Swahili Corporate Income Tax Rate (30%)",
        "locale": "sw",
        "domain": "Corporate Tax (Swahili)",
        "statute": "Income Tax Act",
        "query": "Kiwango cha kodi ya mapato ya kampuni au shirika (corporate tax) nchini Uganda ni asilimia ngapi?",
        "required_concepts": ["30%", "shirika"],
        "forbidden_hallucinations": ["0% kwa makampuni yote"],
    },
    {
        "id": "REL-082",
        "title": "Swahili PWD Exemption: Watu wenye ulemavu",
        "locale": "sw",
        "domain": "Tax Exemptions (Swahili)",
        "statute": "Income Tax Act, Section 21(1)(v)",
        "query": "Watu wenye ulemavu wanasamehewa kiasi gani cha mshahara wa kila mwezi kabla ya kutozwa kodi ya PAYE nchini Uganda?",
        "required_concepts": ["1,460,000", "msamaha", "kodi"],
        "forbidden_hallucinations": ["hakuna msamaha wowote"],
    },
    {
        "id": "REL-083",
        "title": "Swahili Mobile Money Withdrawal Tax (0.5%)",
        "locale": "sw",
        "domain": "Excise Duty (Swahili)",
        "statute": "Excise Duty Act",
        "query": "Ushuru wa kutoa pesa taslimu kupitia mtandao wa simu (mobile money) nchini Uganda ni asilimia ngapi?",
        "required_concepts": ["0.5%", "ushuru", "pesa"],
        "forbidden_hallucinations": ["20% kwa amana"],
    },
    {
        "id": "REL-084",
        "title": "Swahili Quarterly VAT Threshold (37.5M)",
        "locale": "sw",
        "domain": "Value Added Tax (Swahili)",
        "statute": "VAT Act, Section 7",
        "query": "Kikomo cha usajili wa lazima wa VAT kwa biashara inayopata mapato katika miezi mitatu mfululizo ni shilingi ngapi?",
        "required_concepts": ["37,500,000", "vat", "miezi"],
        "forbidden_hallucinations": ["trilioni moja"],
    },
    {
        "id": "REL-085",
        "title": "Swahili TAT Appeal Deadline: Mahakama ya Rufaa za Kodi",
        "locale": "sw",
        "domain": "Tax Appeals (Swahili)",
        "statute": "Tax Appeals Tribunal Act",
        "query": "Mlipa kodi ana siku ngapi kukata rufaa kwenye Mahakama ya Rufaa za Kodi (TAT) baada ya uamuzi wa pingamizi?",
        "required_concepts": ["30", "rufaa", "siku"],
        "forbidden_hallucinations": ["miaka miwili"],
    },
    {
        "id": "REL-086",
        "title": "Swahili 15-Year Vehicle Ban: Kuagiza gari la zamani",
        "locale": "sw",
        "domain": "Customs (Swahili)",
        "statute": "Traffic and Road Safety Act",
        "query": "Je, inaruhusiwa kuagiza gari lililotengenezwa zaidi ya miaka 15 iliyopita kuingia nchini Uganda?",
        "required_concepts": ["15", "marufuku", "gari"],
        "forbidden_hallucinations": ["inaruhusiwa bila kikomo"],
    },
    {
        "id": "REL-087",
        "title": "Swahili TIN Registration: Ninawezaje kupata TIN?",
        "locale": "sw",
        "domain": "Registration (Swahili)",
        "statute": "TPCA Registration",
        "query": "Ni nyaraka gani zinazohitajika ili kujisajili na kupata namba ya TIN kutoka URA?",
        "required_concepts": ["tin", "ura"],
        "forbidden_hallucinations": ["lipa pesa taslimu kwa afisa"],
    },
    {
        "id": "REL-088",
        "title": "Swahili Rental Tax: Kodi ya pango kwa mtu binafsi",
        "locale": "sw",
        "domain": "Rental Tax (Swahili)",
        "statute": "Income Tax Act, s.5",
        "query": "Kodi ya mapato ya pango kwa mwenye nyumba binafsi inakokotolewaje nchini Uganda?",
        "required_concepts": ["12%", "2,820,000", "kodi"],
        "forbidden_hallucinations": ["hakuna kodi ya pango"],
    },
    {
        "id": "REL-089",
        "title": "Swahili Presumptive Tax: Kodi ya makadirio kwa biashara ndogo",
        "locale": "sw",
        "domain": "Small Business (Swahili)",
        "statute": "Income Tax Act, Second Schedule",
        "query": "Biashara ndogo ndogo ambazo hazina vitabu vya mahesabu zinalipaje kodi ya makadirio?",
        "required_concepts": ["milioni 10", "kodi"],
        "forbidden_hallucinations": ["lazima wakaguzi wa kimataifa"],
    },
    {
        "id": "REL-090",
        "title": "Swahili Monthly Return Deadline (15th Day)",
        "locale": "sw",
        "domain": "Compliance Calendar (Swahili)",
        "statute": "TPCA s.16",
        "query": "Marejesho ya kodi ya kila mwezi yanapaswa kuwasilishwa kufikia tarehe ngapi ya mwezi unaofuata?",
        "required_concepts": ["15", "mwezi"],
        "forbidden_hallucinations": ["mwisho wa mwaka tu"],
    },
    {
        "id": "REL-091",
        "title": "Swahili Toll-Free Numbers: Nambari za bure za URA",
        "locale": "sw",
        "domain": "Contact Details (Swahili)",
        "statute": "Citizen Access",
        "query": "Ni nambari gani za simu zisizo na malipo (toll-free) za kupiga ili kupata msaada kutoka URA?",
        "required_concepts": ["0800 117 000", "0800 217 000"],
        "forbidden_hallucinations": ["lipa gharama kubwa za simu"],
    },
    {
        "id": "REL-092",
        "title": "Swahili Stamp Duty: Ushuru wa stempu kwa ununuzi wa ardhi",
        "locale": "sw",
        "domain": "Stamp Duty (Swahili)",
        "statute": "Stamp Duty Act",
        "query": "Ushuru wa stempu (stamp duty) wakati wa kuhamisha ardhi au majengo ni asilimia ngapi?",
        "required_concepts": ["1%", "stamp duty"],
        "forbidden_hallucinations": ["18% stamp duty"],
    },
    {
        "id": "REL-093",
        "title": "Swahili Withholding Tax on Goods (6%)",
        "locale": "sw",
        "domain": "Withholding (Swahili)",
        "statute": "Income Tax Act, s.119",
        "query": "Mtu anayesambaza bidhaa zenye thamani ya zaidi ya milioni moja hukatwa asilimia ngapi ya kodi ya zuio?",
        "required_concepts": ["6%", "zuio"],
        "forbidden_hallucinations": ["30% kodi ya zuio kwa bidhaa"],
    },
    {
        "id": "REL-094",
        "title": "Swahili EAC Common External Tariff Bands",
        "locale": "sw",
        "domain": "Customs Tariff (Swahili)",
        "statute": "EAC CET 2022",
        "query": "Je, ushuru wa forodha wa pamoja wa Jumuiya ya Afrika Mashariki (EAC CET) una viwango vipi vikuu?",
        "required_concepts": ["0%", "10%", "25%", "35%"],
        "forbidden_hallucinations": ["kiwango kimoja tu cha 50%"],
    },
    {
        "id": "REL-095",
        "title": "Swahili EAC Rules of Origin 35% Value Addition",
        "locale": "sw",
        "domain": "Regional Trade (Swahili)",
        "statute": "EAC Rules of Origin",
        "query": "Bidhaa zinazotengenezwa ndani ya EAC zinahitaji kuongeza thamani ya asilimia ngapi ili kusafirishwa bila ushuru?",
        "required_concepts": ["35%", "thamani"],
        "forbidden_hallucinations": ["hakuna ongezeko la thamani linalohitajika"],
    },
    {
        "id": "REL-096",
        "title": "Swahili EFRIS Electronic Invoicing Requirement",
        "locale": "sw",
        "domain": "EFRIS (Swahili)",
        "statute": "TPCA s.19B",
        "query": "Mfumo wa EFRIS unawalazimu wafanyabiashara waliosajiliwa kwa VAT kufanya nini wanapouza bidhaa?",
        "required_concepts": ["efris", "ankara", "risiti"],
        "forbidden_hallucinations": ["efris ni ya hiari kwa kila mtu"],
    },
    {
        "id": "REL-097",
        "title": "Swahili Solar Equipment Exemption",
        "locale": "sw",
        "domain": "Renewable Energy (Swahili)",
        "statute": "VAT & Customs Act",
        "query": "Je, vifaa vya nishati ya jua (solar) kama betri na paneli vinatozwa ushuru gani wa forodha?",
        "required_concepts": ["jua", "ushuru"],
        "forbidden_hallucinations": ["50% ushuru wa forodha"],
    },
    {
        "id": "REL-098",
        "title": "Swahili PRN Bank / Mobile Money Payments",
        "locale": "sw",
        "domain": "Payments (Swahili)",
        "statute": "Payment Procedures",
        "query": "Ninawezaje kulipa kodi ya URA kwa kutumia namba ya usajili wa malipo (PRN)?",
        "required_concepts": ["prn", "benki", "simu"],
        "forbidden_hallucinations": ["mpe afisa pesa mkononi"],
    },
    {
        "id": "REL-099",
        "title": "Swahili Customs Baggage USD 500 Allowance",
        "locale": "sw",
        "domain": "Customs Baggage (Swahili)",
        "statute": "EAC-CMA 5th Schedule",
        "query": "Msafiri anayeingia nchini kupitia Uwanja wa Ndege wa Entebbe anaruhusiwa kubeba bidhaa za kibinafsi za thamani gani bila ushuru?",
        "required_concepts": ["500", "dola"],
        "forbidden_hallucinations": ["dola milioni moja bure"],
    },
    {
        "id": "REL-100",
        "title": "Swahili Reporting Tax Fraud / Whistleblowing",
        "locale": "sw",
        "domain": "Anti-Corruption (Swahili)",
        "statute": "Whistleblower Protection",
        "query": "Ninawezaje kutoa taarifa kwa siri kuhusu ufisadi au ukwepaji kodi kwa URA?",
        "required_concepts": ["0800 117 000", "ura"],
        "forbidden_hallucinations": ["hakuna njia ya kutoa taarifa"],
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
    t0 = time.perf_counter()

    try:
        resp = await client.post(
            GATEWAY_URL,
            json={"message": case["query"], "locale": case["locale"]},
            headers={
                "X-Session-ID": f"rel-100-{cid}-{int(time.time())}",
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
        is_passed = (concept_score >= 0.50) and not hallucination

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
            reply_snippet=reply[:280].replace("\n", " "),
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
    print("=" * 78)
    print("LIVE NGROK MULTILINGUAL RELIABILITY AUDIT: 100 EDGE CASES (EN, LG, SW)")
    print(f"Target Gateway: {GATEWAY_URL}")
    print("=" * 78)

    limits = httpx.Limits(max_connections=32, max_keepalive_connections=16)
    sem = asyncio.Semaphore(4)

    t_start = time.perf_counter()
    async with httpx.AsyncClient(timeout=45.0, limits=limits, headers={"ngrok-skip-browser-warning": "1"}) as client:
        async def bounded_run(case):
            async with sem:
                res = await run_edge_case(client, case)
                status_str = "PASS" if res.passed else "FAIL"
                print(f"  [{res.case_id}] {status_str} ({res.score}%): {res.title} [{res.locale.upper()}] ({res.latency_s}s)", flush=True)
                return res

        tasks = [bounded_run(case) for case in EDGE_CASES]
        results = await asyncio.gather(*tasks)

    t_wall = time.perf_counter() - t_start
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    pass_rate = round(passed / total * 100.0, 1)

    print("\n" + "=" * 78)
    print("AUDIT SUMMARY RESULTS")
    print("=" * 78)
    print(f"Total Scenarios: {total}")
    print(f"Successful Passed: {passed} / {total} ({pass_rate}%)")
    print(f"Total Duration: {t_wall:.2f}s | Throughput: {total / t_wall:.2f} QPS")
    print(f"Mean Latency: {sum(r.latency_s for r in results) / total:.2f}s")

    # Language breakdown
    print("\nLanguage Breakdown:")
    for loc in ("en", "lg", "sw"):
        loc_res = [r for r in results if r.locale == loc]
        loc_passed = sum(1 for r in loc_res if r.passed)
        loc_rate = round(loc_passed / len(loc_res) * 100.0, 1) if loc_res else 0.0
        loc_lat = sum(r.latency_s for r in loc_res) / len(loc_res) if loc_res else 0.0
        print(f"  [{loc.upper()}] Passed: {loc_passed}/{len(loc_res)} ({loc_rate}%) | Mean Latency: {loc_lat:.2f}s")

    # Save to JSON
    out_json = "docs/presentation/multilang_100_reliability_audit_results.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in results], f, indent=2)
    print(f"\nArtifact saved to: {out_json}")


if __name__ == "__main__":
    asyncio.run(main())

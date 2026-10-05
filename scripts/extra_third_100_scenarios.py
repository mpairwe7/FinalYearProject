#!/usr/bin/env python3
"""Defines 100 additional scenarios (10 per pillar) to create the 300-scenario master benchmark suite."""

from scripts.evaluate_cx_100_scenarios_ngrok import TurnStep, CXScenario

def build_extra_third_100_scenarios() -> list[CXScenario]:
    extras: list[CXScenario] = []

    # -------------------------------------------------------------------------
    # PILLAR 1 EXTRA: Guided Workflows (10 Scenarios: CX-P1-21 to CX-P1-30)
    # -------------------------------------------------------------------------
    extras.extend([
        CXScenario(
            id="CX-P1-21",
            category="Pillar 1: Guided Workflows",
            title="Diplomat and Foreign Embassy Special Tax Exemption Registration Flow",
            description="Guides diplomatic mission staff through privileged tax exemption registration with Ministry of Foreign Affairs protocol.",
            turns=[TurnStep("I am a foreign embassy consular officer in Kampala. Guide me on how to register for diplomatic VAT and duty exemption", expect_reply_regex=[r"(?:diplomat|embassy|exemption|protocol|foreign\s+affairs|ura)"], expect_resources_min=1)],
        ),
        CXScenario(
            id="CX-P1-22",
            category="Pillar 1: Guided Workflows",
            title="Trust and Charitable Foundation Non-Individual TIN Registration Flow",
            description="Walks through registration requirements for registered trustees and charitable trust deeds.",
            turns=[TurnStep("We registered a family charitable foundation. How do we obtain a non-individual TIN as registered trustees?", expect_reply_regex=[r"(?:trust|foundation|deed|trustee|tin|registration)"], expect_resources_min=1)],
        ),
        CXScenario(
            id="CX-P1-23",
            category="Pillar 1: Guided Workflows",
            title="Cross-Border Transporter COMESA Carrier License Registration Flow",
            description="Guides international cargo transit operators on securing carrier licenses with URA customs.",
            turns=[TurnStep("Guide our freight company on registering a cross-border COMESA carrier license for transit trucking to Rwanda", expect_reply_regex=[r"(?:carrier|transit|customs|comesa|transport|license)"])],
        ),
        CXScenario(
            id="CX-P1-24",
            category="Pillar 1: Guided Workflows",
            title="Voluntary Disclosure Program (VDP) Penalty Relief Guided Journey",
            description="Walks taxpayers through Section 66A TPCA 100% penal relief by proactive disclosure of unpaid taxes.",
            turns=[TurnStep("I want to voluntarily disclose unpaid rental income tax from 2024 before an audit starts. How do I apply for VDP penalty relief?", expect_reply_regex=[r"(?:voluntary\s+disclosure|vdp|section\s+66a?|penalty|waiver)"], expect_resources_min=1)],
        ),
        CXScenario(
            id="CX-P1-25",
            category="Pillar 1: Guided Workflows",
            title="Tax Agent License Accreditation & Renewal Workflow",
            description="Details the annual application requirements for certified public accountants seeking Tax Agent accreditation.",
            turns=[TurnStep("How does a certified accountant apply for a URA Tax Agent license to represent clients on eTax?", expect_reply_regex=[r"(?:tax\s+agent|license|icpau|accredit|board|etax)"])],
        ),
        CXScenario(
            id="CX-P1-26",
            category="Pillar 1: Guided Workflows",
            title="Bonded Warehouse Operator Customs License Renewal Flow",
            description="Guides customs warehouse operators on annual licensing, surety bond validation, and physical security compliance.",
            turns=[TurnStep("Guide me through the annual renewal process for our customs bonded warehouse in Nakawa", expect_reply_regex=[r"(?:bonded\s+warehouse|customs|license|bond|renewal)"])],
        ),
        CXScenario(
            id="CX-P1-27",
            category="Pillar 1: Guided Workflows",
            title="Motor Vehicle Customized Vanity Plate Application Journey",
            description="Walks through requirements, fees, and approval steps for personalized registration plates.",
            turns=[TurnStep("I want to apply for a personalized vanity number plate for my private vehicle. What is the process and fee?", expect_reply_regex=[r"(?:personalized|plate|vanity|customized|motor\s+vehicle|ugx)"])],
        ),
        CXScenario(
            id="CX-P1-28",
            category="Pillar 1: Guided Workflows",
            title="Withholding Tax Exemption List Annual Application Flow",
            description="Guides compliant corporate taxpayers on applying for the 6% withholding tax exemption list under Section 119 ITA.",
            turns=[TurnStep("Guide our manufacturing company on how to apply for the 6% Withholding Tax exemption certificate on supply of goods", expect_reply_regex=[r"(?:withholding|exemption|section\s+119|wht|compliance)"], expect_resources_min=1)],
        ),
        CXScenario(
            id="CX-P1-29",
            category="Pillar 1: Guided Workflows",
            title="Amending Business Registration Principal Place of Business Flow",
            description="Step-by-step guidance on updating registered tax office and trading physical address.",
            turns=[TurnStep("We moved our corporate headquarters from Kololo to Bugolobi. How do I update our physical address on e-Tax?", expect_reply_regex=[r"(?:amend|address|etax|profile|update|premises)"])],
        ),
        CXScenario(
            id="CX-P1-30",
            category="Pillar 1: Guided Workflows",
            title="Online Taxpayer Profile Multi-Factor Authentication (MFA) Recovery Flow",
            description="Explains account unlocking and phone number updating when an administrator loses authentication access.",
            turns=[TurnStep("I lost the phone number registered for OTP authentication on my company e-Tax account. How do I recover access?", expect_reply_regex=[r"(?:otp|recover|authenticat|phone|mfa|etax|officer)"])],
        ),
    ])

    # -------------------------------------------------------------------------
    # PILLAR 2 EXTRA: Deterministic Computations (10 Scenarios: CX-P2-21 to CX-P2-30)
    # -------------------------------------------------------------------------
    extras.extend([
        CXScenario(
            id="CX-P2-21",
            category="Pillar 2: Deterministic Computations",
            title="Secondary Employment PAYE Flat 30% Deduction",
            description="Calculates PAYE on second consulting job of 4,000,000 UGX where primary employer uses thresholds.",
            turns=[TurnStep("I have a primary job where threshold is applied. For my weekend lecturing job paying 4,000,000 UGX, how much PAYE must be deducted?", expect_reply_regex=[r"(?:30%|1,?200,?000|secondary\s+employment|first\s+shilling)"])],
        ),
        CXScenario(
            id="CX-P2-22",
            category="Pillar 2: Deterministic Computations",
            title="Executive Free Housing Benefit Cap Calculation",
            description="Computes taxable employment benefit for employer-provided housing capped at 15% of employment income.",
            turns=[TurnStep("An executive has monthly basic salary of 10,000,000 UGX and the company pays 3,000,000 UGX rent for their apartment. What is the taxable housing benefit?", expect_reply_regex=[r"(?:15%|1,?500,?000|housing\s+benefit|cap|market\s+value)"])],
        ),
        CXScenario(
            id="CX-P2-23",
            category="Pillar 2: Deterministic Computations",
            title="Expatriate Non-Resident PAYE on Foreign Currency Salary",
            description="Calculates non-resident PAYE brackets starting from the first shilling on foreign currency package.",
            turns=[TurnStep("Calculate monthly PAYE for a non-resident engineer earning USD 4,000 when the official statutory exchange rate is 3,750 UGX per dollar", expect_reply_regex=[r"(?:non-resident|first\s+shilling|15,?000,?000|paye|tax)"])],
        ),
        CXScenario(
            id="CX-P2-24",
            category="Pillar 2: Deterministic Computations",
            title="Commercial Factory Capital Gains Tax with Cost Base & Improvements",
            description="Calculates CGT on sale of commercial warehouse with purchase cost and capitalized improvements.",
            turns=[TurnStep("Calculate capital gains tax on sale of a commercial warehouse for 800M UGX bought in 2021 for 500M UGX with documented improvements of 100M UGX", expect_reply_regex=[r"(?:gain|200,?000,?000|60,?000,?000|30%|capital\s+gains?)"])],
        ),
        CXScenario(
            id="CX-P2-25",
            category="Pillar 2: Deterministic Computations",
            title="Mixed VAT Supply Apportionment Calculation",
            description="Apportions input tax for grain milling facility producing both taxable flour and exempt animal feeds.",
            turns=[TurnStep("A grain mill generates 70M UGX taxable sales and 30M UGX exempt sales, incurring 10M UGX input VAT on factory power. How much input VAT can be claimed?", expect_reply_regex=[r"(?:70%|7,?000,?000|apportion|input\s+vat|exempt)"])],
        ),
        CXScenario(
            id="CX-P2-26",
            category="Pillar 2: Deterministic Computations",
            title="Withholding Tax on Cross-Border Software Royalties under DTA",
            description="Determines applicable treaty withholding rate on software license payments to European entity.",
            turns=[TurnStep("What is the withholding tax rate when a Ugandan bank pays a USD 50,000 annual core software license fee to a software vendor in the Netherlands?", expect_reply_regex=[r"(?:treaty|dta|royalty|withholding|10%|15%)"])],
        ),
        CXScenario(
            id="CX-P2-27",
            category="Pillar 2: Deterministic Computations",
            title="Highway Transit Road Toll Calculation for Heavy Articulated Truck",
            description="Computes road user charge and transit fee for 30-tonne commercial transit vehicle.",
            turns=[TurnStep("How is the road transit toll and entry fee calculated for a 30-tonne foreign cargo truck transiting from Busia to Katuna border?", expect_reply_regex=[r"(?:transit|toll|charge|customs|ton|border)"])],
        ),
        CXScenario(
            id="CX-P2-28",
            category="Pillar 2: Deterministic Computations",
            title="Presumptive Tax Schedule for Upcountry Commercial Motorcycle Operator",
            description="Evaluates annual presumptive tax obligations for rural transport operators outside Kampala.",
            turns=[TurnStep("How much presumptive tax does a commercial boda-boda rider operating in Gulu municipality pay annually under current rates?", expect_reply_regex=[r"(?:presumptive|boda|annual|fixed|ugx|transport)"])],
        ),
        CXScenario(
            id="CX-P2-29",
            category="Pillar 2: Deterministic Computations",
            title="Digital Services Tax (5%) on Foreign Streaming Subscriptions",
            description="Calculates 5% non-resident digital services tax on subscription revenues sourced from Ugandan subscribers.",
            turns=[TurnStep("An offshore digital video streaming platform bills 100,000,000 UGX monthly from Ugandan consumer subscribers. What is their Digital Services Tax liability?", expect_reply_regex=[r"(?:5%|5,?000,?000|digital\s+services?\s+tax|dst|non-resident)"])],
        ),
        CXScenario(
            id="CX-P2-30",
            category="Pillar 2: Deterministic Computations",
            title="Compounded Late Filing Penal Interest Calculation (Section 32 TPCA)",
            description="Computes 2% monthly compounding interest on overdue principal tax balance of 20M UGX over 4 months.",
            turns=[TurnStep("Calculate the Section 32 2% monthly compounding interest on an overdue principal VAT balance of 20,000,000 UGX that is 4 months overdue", expect_reply_regex=[r"(?:2%|compound|interest|section\s+32|overdue|principal)"])],
        ),
    ])

    # -------------------------------------------------------------------------
    # PILLAR 3 EXTRA: Narrative Stories & Complex Cases (10 Scenarios: CX-P3-21 to CX-P3-30)
    # -------------------------------------------------------------------------
    extras.extend([
        CXScenario(
            id="CX-P3-21",
            category="Pillar 3: Narrative Stories",
            title="Diaspora Doctor Establishing Specialist Hospital in Gulu",
            description="A diaspora surgeon importing diagnostic MRI machines and specialized hospital beds requests statutory exemptions.",
            turns=[TurnStep("I am a Ugandan doctor living in the UK returning to build a specialist trauma hospital in Gulu. We are importing medical equipment and an ambulance. What tax exemptions apply under Schedule 5 of EACCMA?", expect_reply_regex=[r"(?:medical|equipment|exempt|eaccma|hospital|duty\s+free|ambulance)"])],
        ),
        CXScenario(
            id="CX-P3-22",
            category="Pillar 3: Narrative Stories",
            title="Jinja Nile Perch Fish Processing Exporter Facing Value-Addition Cess",
            description="An industrial fish processing firm queries zero-rating on export fillets and statutory quality inspection cess.",
            turns=[TurnStep("Our fish factory in Jinja processes and chills fresh Nile Perch fillets for export to Amsterdam. What VAT and export levies apply to our consignments?", expect_reply_regex=[r"(?:zero-rated|export|fish|fillet|vat|cess|levy)"])],
        ),
        CXScenario(
            id="CX-P3-23",
            category="Pillar 3: Narrative Stories",
            title="Kampala Tech Startup Issuing Employee Stock Ownership Plan (ESOP)",
            description="A software engineering firm in Ntinda structuring share options for local developers queries tax timing.",
            turns=[TurnStep("Our fintech startup is granting stock options to our Ugandan developers. At what point is tax triggered—upon vesting, exercise, or sale of the shares?", expect_reply_regex=[r"(?:esop|exercise|vesting|benefit|employment\s+income|capital\s+gains?|shares?)"])],
        ),
        CXScenario(
            id="CX-P3-24",
            category="Pillar 3: Narrative Stories",
            title="Mbale Arabica Coffee Cooperative Navigating Market Price Collapse",
            description="Smallholder farmer cooperative queries provisional tax reduction due to sharp climatic crop failure.",
            turns=[TurnStep("Our coffee farmers cooperative in Mbale experienced a 50% harvest drop due to drought and price slump. How do we apply to revise our provisional tax payments downwards?", expect_reply_regex=[r"(?:provisional|amend|revise|cooperative|harvest|estimated)"])],
        ),
        CXScenario(
            id="CX-P3-25",
            category="Pillar 3: Narrative Stories",
            title="Supermarket Chain Handling EFRIS Server Network Cut on High-Volume Sales",
            description="Retail manager queries statutory compliance and offline queueing rules when internet connection fails during peak trading.",
            turns=[TurnStep("During Black Friday sales at our retail supermarket, our fiber connection dropped for 4 hours while queues were long. What are the rules for offline EFRIS invoice issuance?", expect_reply_regex=[r"(?:offline|efris|queue|sync|reconnect|invoice|hours?)"])],
        ),
        CXScenario(
            id="CX-P3-26",
            category="Pillar 3: Narrative Stories",
            title="Arua Female Solar Assembler Operating Under Regional Incubator",
            description="Women-led manufacturing cooperative assembling clean energy lamps inquires on local content incentives.",
            turns=[TurnStep("Our women cooperative in Arua assembles solar lanterns using locally pressed components and imported photovoltaic cells. Are we eligible for agro-industrial or manufacturing tax holidays?", expect_reply_regex=[r"(?:holiday|manufactur|incentive|solar|exemption|local\s+content)"])],
        ),
        CXScenario(
            id="CX-P3-27",
            category="Pillar 3: Narrative Stories",
            title="Karamoja Gold Exploration Licensee Transitioning to Commercial Mining",
            description="Exploration company converting to active commercial mining inquires about mineral royalties and ring-fencing.",
            turns=[TurnStep("We hold an exploration license in Karamoja and are now applying for a commercial mining lease. How are mining royalties and expenditure ring-fencing applied under the Mining Act?", expect_reply_regex=[r"(?:mining|royalty|ring-fencing|lease|exploration|mineral)"])],
        ),
        CXScenario(
            id="CX-P3-28",
            category="Pillar 3: Narrative Stories",
            title="Wakiso Commercial Poultry Breeder Balancing Feeds VAT with Egg Sales",
            description="Commercial egg producer asks whether input VAT paid on imported parent stock and premix can be recovered.",
            turns=[TurnStep("I run a commercial poultry hatchery with 20,000 layers in Wakiso. We buy imported feed premix subject to VAT but fresh eggs are exempt. Can I reclaim any of my input VAT?", expect_reply_regex=[r"(?:exempt|poultry|eggs|input\s+vat|feed|unprocessed)"])],
        ),
        CXScenario(
            id="CX-P3-29",
            category="Pillar 3: Narrative Stories",
            title="Family Hardware Enterprise Restructuring into Limited Holding Company",
            description="Founders transitioning sole proprietorship into holding structure seek advice on asset transfer stamp duty.",
            turns=[TurnStep("My brother and I run a family hardware business with 3 branches as a partnership. We want to incorporate a holding company and transfer our commercial stores. Does capital gains or stamp duty apply on transfer?", expect_reply_regex=[r"(?:re-organization|transfer|stamp\s+duty|capital\s+gains?|holding\s+company|incorporat)"])],
        ),
        CXScenario(
            id="CX-P3-30",
            category="Pillar 3: Narrative Stories",
            title="Freelance Remote Graphic Designer Receiving Overseas Client Remittances",
            description="Digital remote worker receiving international payments queries personal income tax and withholding.",
            turns=[TurnStep("I am a remote 3D artist living in Entebbe freelancing for gaming studios in Canada, getting paid via Wise in USD. How do I declare this income and am I subject to Ugandan income tax?", expect_reply_regex=[r"(?:worldwide|resident|income\s+tax|declare|foreign|freelance)"])],
        ),
    ])

    # -------------------------------------------------------------------------
    # PILLAR 4 EXTRA: Actionable Resources & Verification Tools (10 Scenarios: CX-P4-21 to CX-P4-30)
    # -------------------------------------------------------------------------
    extras.extend([
        CXScenario(
            id="CX-P4-21",
            category="Pillar 4: Actionable Resources",
            title="DT-1014 Withholding Tax Monthly Return CSV Upload Template",
            description="Provides official template format and guidance for bulk withholding tax schedule submission.",
            turns=[TurnStep("Provide the official URA Withholding Tax return template DT-1014 for bulk CSV schedule upload", expect_reply_regex=[r"(?:dt-1014|template|withholding|csv|schedule|etax)"], expect_resources_min=1)],
        ),
        CXScenario(
            id="CX-P4-22",
            category="Pillar 4: Actionable Resources",
            title="DT-1002 Corporation Income Tax Macro-Enabled Excel Return",
            description="Directs corporate finance teams to the official corporation return calculation spreadsheet.",
            turns=[TurnStep("Where can I download the official macro-enabled Excel return form DT-1002 for annual corporation tax filing?", expect_reply_regex=[r"(?:dt-1002|corporation|return|excel|macro|download)"], expect_resources_min=1)],
        ),
        CXScenario(
            id="CX-P4-23",
            category="Pillar 4: Actionable Resources",
            title="Presumptive Tax Simplified Self-Assessment Worksheet",
            description="Provides simplified worksheet for informal businesses evaluating turnover bands.",
            turns=[TurnStep("I need the simplified self-assessment worksheet to calculate presumptive tax for my retail shop", expect_reply_regex=[r"(?:presumptive|worksheet|turnover|band|retail|ura)"], expect_resources_min=1)],
        ),
        CXScenario(
            id="CX-P4-24",
            category="Pillar 4: Actionable Resources",
            title="Commercial Banks Direct Debit & EFT Processing Codes for URA",
            description="Supplies clearing bank branch codes and channel options for paying URA assessments.",
            turns=[TurnStep("Which commercial banks in Uganda support direct electronic PRN payments without visiting a physical branch?", expect_reply_regex=[r"(?:bank|direct\s+debit|eft|prn|online\s+banking|rtgs)"])],
        ),
        CXScenario(
            id="CX-P4-25",
            category="Pillar 4: Actionable Resources",
            title="Regional URA Contact Directory & Dedicated One-Stop Service Centers",
            description="Provides verified contact numbers and physical addresses for regional hubs.",
            turns=[TurnStep("Provide the official contact numbers and physical address for the URA One-Stop Centre in Jinja and Mbale", expect_reply_regex=[r"(?:jinja|mbale|0800|contact|office|service\s+centre)"])],
        ),
        CXScenario(
            id="CX-P4-26",
            category="Pillar 4: Actionable Resources",
            title="EAC Common External Tariff (CET) Schedule 2026/2027 Access",
            description="Provides direct access to the East African Community Common External Tariff tariff book.",
            turns=[TurnStep("Where can I download or inspect the EAC Common External Tariff 2026/2027 classification schedule?", expect_reply_regex=[r"(?:cet|tariff|customs|eac|common\s+external|schedule)"], expect_resources_min=1)],
        ),
        CXScenario(
            id="CX-P4-27",
            category="Pillar 4: Actionable Resources",
            title="Section 21 Agro-Processing 80% Local Raw Material Audit Checklist",
            description="Supplies verification checklist for processors claiming the 10-year income tax holiday.",
            turns=[TurnStep("What is the official audit verification checklist for the 80% local raw material threshold under Section 21?", expect_reply_regex=[r"(?:checklist|80%|raw\s+material|section\s+21|audit|verification)"], expect_resources_min=1)],
        ),
        CXScenario(
            id="CX-P4-28",
            category="Pillar 4: Actionable Resources",
            title="Form DT-1011 Notice of Objection to Tax Assessment Fillable Template",
            description="Supplies official objection form template under Section 24 of the Tax Procedures Code Act.",
            turns=[TurnStep("Provide the official Form DT-1011 template for lodging an objection against an additional income tax assessment", expect_reply_regex=[r"(?:dt-1011|objection|form|section\s+24|assessment|template)"], expect_resources_min=1)],
        ),
        CXScenario(
            id="CX-P4-29",
            category="Pillar 4: Actionable Resources",
            title="Customs Form C63 Single Administrative Document Declaration Guide",
            description="Provides guidance on clearing agents completing the C63 customs entry on ASYCUDA World.",
            turns=[TurnStep("Where do I find the instructional guide for filling the customs Form C63 declaration on ASYCUDA World?", expect_reply_regex=[r"(?:c63|asycuda|customs|declaration|single\s+administrative|entry)"], expect_resources_min=1)],
        ),
        CXScenario(
            id="CX-P4-30",
            category="Pillar 4: Actionable Resources",
            title="Standard Operating Procedures for EFRIS Mobile App for Retailers",
            description="Provides user guide and manual links for the Android/iOS EFRIS mobile fiscal application.",
            turns=[TurnStep("Provide the user guide for issuing e-receipts using the URA EFRIS mobile phone app", expect_reply_regex=[r"(?:efris|mobile|app|receipt|guide|download)"], expect_resources_min=1)],
        ),
    ])

    # -------------------------------------------------------------------------
    # PILLAR 5 EXTRA: Empathetic Crisis Guidance & Problem Remediation (10 Scenarios: CX-P5-21 to CX-P5-30)
    # -------------------------------------------------------------------------
    extras.extend([
        CXScenario(
            id="CX-P5-21",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Taxpayer Distressed by Imminent Auctioneer Property Seizure",
            description="Taxpayer panicked after private bailiff serves 24-hour warrant of execution on family property.",
            turns=[TurnStep("Auctioneers are at my business gate threatening to attach our stock and vehicles in 24 hours for unpaid income tax. Please help me, what can I do?", expect_reply_regex=[r"(?:stay|distress|section\s+42|instalment|supervisor|officer|warrant)"])],
        ),
        CXScenario(
            id="CX-P5-22",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Single Mother Facing Complete Salary Attachment Under Agency Notice",
            description="Employee left with zero living funds after Section 44 notice attaches 100% of monthly bank deposits.",
            turns=[TurnStep("URA sent an agency notice to my bank and they froze my entire salary. I have two young children and cannot buy food or pay rent. Is there any statutory living allowance protection?", expect_reply_regex=[r"(?:agency\s+notice|section\s+44|hardship|living|officer|remedy)"])],
        ),
        CXScenario(
            id="CX-P5-23",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Hospital Account Frozen Jeopardizing Critical Emergency Care",
            description="Hospital director seeking immediate emergency variance to unfreeze medical supply operational account.",
            turns=[TurnStep("URA froze our private hospital operations account over an outstanding PAYE dispute, and we cannot purchase oxygen or pay doctors today. We need immediate emergency escalation!", expect_reply_regex=[r"(?:emergency|escalat|unfreeze|hospital|commissioner|officer|critical)"])],
        ),
        CXScenario(
            id="CX-P5-24",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Natural Disaster / Flood Destruction of Business and Tax Records",
            description="Shopkeeper whose premises were destroyed by flash floods queries reconstructed assessment relief.",
            turns=[TurnStep("Our retail shop in Kasese was completely submerged in flash floods and all physical stock and accounting books were destroyed. How do I handle upcoming tax assessments?", expect_reply_regex=[r"(?:force\s+majeure|disaster|records|reconstruct|relief|section\s+66)"])],
        ),
        CXScenario(
            id="CX-P5-25",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Taxpayer Experiencing Severe Mental Health Crisis & Suicidal Ideation",
            description="Distressed entrepreneur expressing hopelessness over accumulated commercial tax liabilities.",
            turns=[TurnStep("I cannot take this pressure anymore. The penal interest has reached 500 million and I feel like taking my own life tonight. Everything I worked for is gone.", expect_reply_regex=[r"(?:help|0800\s+21\s+21\s+21|mental\s+health|crisis|counsel|support|officer)"])],
        ),
        CXScenario(
            id="CX-P5-26",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Whistleblower Protection Against Retaliation on Counterfeit Stamp Reporting",
            description="Warehouse worker seeking confidential channel to report employer applying forged digital tax stamps.",
            turns=[TurnStep("My employer is applying fake DTS stamps on vodka bottles at night. If I report this, how does URA protect my identity from violent retaliation?", expect_reply_regex=[r"(?:whistleblower|confidential|protect|anonym|informant|reward)"])],
        ),
        CXScenario(
            id="CX-P5-27",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Embezzlement by Former Tax Consultant Creating Phantom Arrears",
            description="Business owner discovering their certified accountant stole remitted tax funds without filing.",
            turns=[TurnStep("We discovered our former finance manager was forging URA payment receipts and stole 80M UGX meant for VAT. URA now issued a demand notice. How do we present police case evidence?", expect_reply_regex=[r"(?:fraud|police|evidence|investigat|remedy|agent|relief)"])],
        ),
        CXScenario(
            id="CX-P5-28",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Innocent Third-Party Vehicle Buyer Defending Against Seizure",
            description="Purchaser bought registered vehicle only to have customs impound it for unpaid previous import duty.",
            turns=[TurnStep("Customs impounded my Toyota Prado which I bought 6 months ago with a clean logbook, claiming the first importer never paid 18M UGX import duty. Can they seize my car?", expect_reply_regex=[r"(?:lien|seizure|customs|duty|bona\s+fide|purchaser|logbook)"])],
        ),
        CXScenario(
            id="CX-P5-29",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Informal Artisan Shop Closed for Non-Possession of EFD Device",
            description="Metal fabricator in Katwe whose workshop was locked by enforcement officers seeks compliance path.",
            turns=[TurnStep("Enforcement officers locked my small welding shop in Katwe because I do not have an EFRIS machine. I only make 50,000 UGX a day and cannot afford 2M for a machine. What do I do?", expect_reply_regex=[r"(?:mobile|app|free|artisan|threshold|re-open|officer)"])],
        ),
        CXScenario(
            id="CX-P5-30",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Elderly Pensioner Facing Inheritance Foreclosure Over Rental Discrepancy",
            description="72-year-old widow confused by automated commercial rental assessment on residential flats.",
            turns=[TurnStep("I am a 72-year-old widow living off modest rent from two small rooms. URA issued an assessment claiming 24 million UGX which I don't understand and cannot pay. Please help me appeal.", expect_reply_regex=[r"(?:objection|threshold|elderly|assistance|officer|relief|appeal)"])],
        ),
    ])

    # -------------------------------------------------------------------------
    # PILLAR 6 EXTRA: Closed-Loop Bug Reporting & Knowledge Discrepancies (10 Scenarios: CX-P6-21 to CX-P6-30)
    # -------------------------------------------------------------------------
    extras.extend([
        CXScenario(
            id="CX-P6-21",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Discrepancy: Outdated PAYE Zero-Rate Threshold Bug Report",
            description="Taxpayer corrects assistant when older guidance states 235,000 UGX rather than statutory 335,000 UGX.",
            turns=[TurnStep("Your earlier answer stated that the first 235,000 UGX of monthly employment income is tax-free, but Section 116 ITA amended this to 335,000 UGX. Please log a correction report.", expect_reply_regex=[r"(?:#kb-[a-z0-9]+|discrepancy|335,?000|log|report)"], expect_discrepancy=True)],
        ),
        CXScenario(
            id="CX-P6-22",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Discrepancy: Mandatory VAT Registration Turnover Threshold Bug Report",
            description="User disputes incorrect 50M UGX registration threshold, asserting statutory 150M UGX.",
            turns=[TurnStep("A previous officer note quoted the mandatory VAT registration threshold as 50 million UGX, but the statutory limit is 150 million UGX per year. Log a knowledge review report.", expect_reply_regex=[r"(?:#kb-[a-z0-9]+|150\s+million|discrepancy|review|report)"], expect_discrepancy=True)],
        ),
        CXScenario(
            id="CX-P6-23",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Discrepancy: Individual Rental Income Tax Flat 12% Bug Report",
            description="Taxpayer disputes outdated 20% rental rate with 75% expense deduction, affirming the flat 12% above threshold.",
            turns=[TurnStep("I want to log a correction: individual residential rental income tax is no longer 20% with 75% expenses; it is a flat 12% on gross rent exceeding 2,820,000 UGX annually. Record this report.", expect_reply_regex=[r"(?:#kb-[a-z0-9]+|12%|rental|discrepancy|log)"], expect_discrepancy=True)],
        ),
        CXScenario(
            id="CX-P6-24",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Discrepancy: Mobile Money Cash Withdrawal 0.5% Excise Bug Report",
            description="Taxpayer notes that 0.5% excise applies only on withdrawal, not on sending peer-to-peer.",
            turns=[TurnStep("Record a bug report: mobile money excise duty of 0.5% only applies on cash withdrawals, not on receiving or sending transfers between users.", expect_reply_regex=[r"(?:#kb-[a-z0-9]+|0\.5%|withdrawal|mobile\s+money|discrepancy)"], expect_discrepancy=True)],
        ),
        CXScenario(
            id="CX-P6-25",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Discrepancy: 15-Year Motor Vehicle Import Prohibition Exception Bug Report",
            description="User corrects assistant on environmental levy surcharge vs absolute prohibition on passenger cars over 15 years.",
            turns=[TurnStep("Log a knowledge review report: motor vehicles manufactured more than 15 years ago are strictly prohibited from importation under Section 14A EACCMA, not merely subjected to environmental surcharge.", expect_reply_regex=[r"(?:#kb-[a-z0-9]+|prohibit|15\s+years?|eaccma|discrepancy)"], expect_discrepancy=True)],
        ),
        CXScenario(
            id="CX-P6-26",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Discrepancy: Voluntary Disclosure Program (VDP) 100% Penalty Waiver Bug Report",
            description="Disputes statement that VDP requires paying 50% penalties, confirming 100% statutory waiver.",
            turns=[TurnStep("Log a knowledge report: Section 66A TPCA guarantees a 100% waiver of penalties and interest if voluntary disclosure is made before an audit starts, not a 50% partial waiver.", expect_reply_regex=[r"(?:#kb-[a-z0-9]+|100%|waiver|section\s+66a?|discrepancy)"], expect_discrepancy=True)],
        ),
        CXScenario(
            id="CX-P6-27",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Discrepancy: Digital Services Tax Non-Resident 5% Rate Bug Report",
            description="Taxpayer clarifies DST rate is 5% under Section 86A ITA, not 15% standard withholding tax.",
            turns=[TurnStep("Please record a discrepancy report: Digital Services Tax on non-resident electronic service providers is 5% under Section 86A, not 15%.", expect_reply_regex=[r"(?:#kb-[a-z0-9]+|5%|dst|digital\s+services|discrepancy)"], expect_discrepancy=True)],
        ),
        CXScenario(
            id="CX-P6-28",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Discrepancy: Withholding Tax on Local Construction Supplies 6% Bug Report",
            description="Corrects advice asserting 15% professional fee WHT on physical civil works supplies.",
            turns=[TurnStep("Log a report: withholding tax on supply of physical goods and construction civil works by designated agents is 6%, not 15%.", expect_reply_regex=[r"(?:#kb-[a-z0-9]+|6%|construction|withholding|discrepancy)"], expect_discrepancy=True)],
        ),
        CXScenario(
            id="CX-P6-29",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Discrepancy: Principal Private Residence CGT Exemption Bug Report",
            description="Confirms that sale of a taxpayer's sole owner-occupied home is completely exempt from CGT.",
            turns=[TurnStep("Log an official discrepancy report: Section 21 of the Income Tax Act strictly exempts capital gains realized from the sale of an individual's principal private residence.", expect_reply_regex=[r"(?:#kb-[a-z0-9]+|principal\s+private\s+residence|exempt|cgt|discrepancy)"], expect_discrepancy=True)],
        ),
        CXScenario(
            id="CX-P6-30",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Discrepancy: Objection Statutory Window 45 Days Bug Report",
            description="Disputes reference to 30 days to lodge an objection, reaffirming the 45-day window under Section 24 TPCA.",
            turns=[TurnStep("Your FAQ stated that a taxpayer has 30 days to lodge an objection against an assessment, but Section 24(1) TPCA grants 45 days. Log a bug report for this statutory error.", expect_reply_regex=[r"(?:#kb-[a-z0-9]+|45\s+days|section\s+24|objection|discrepancy)"], expect_discrepancy=True)],
        ),
    ])

    # -------------------------------------------------------------------------
    # PILLAR 7 EXTRA: Multilingual Task Fulfillment (10 Scenarios: CX-P7-21 to CX-P7-30)
    # -------------------------------------------------------------------------
    extras.extend([
        CXScenario(
            id="CX-P7-21",
            category="Pillar 7: Multilingual Fulfillment",
            title="Luganda: Rental Income Tax Computation on 8M UGX Monthly Rent",
            description="Calculates 12% rental income tax on residential property in Luganda.",
            turns=[TurnStep("Nnina amayumba g'abapangisa mu Kampala nga nfuna obukadde munaana (8,000,000 UGX) buli mwezi. Mbalira omusolo gwa Rental Tax gwe nteekwa okusasula.", expect_reply_regex=[r"(?:12%|omusolo|abapangisa|rental|ugx)"])],
        ),
        CXScenario(
            id="CX-P7-22",
            category="Pillar 7: Multilingual Fulfillment",
            title="Luganda: Minor Child TIN Registration Guidance",
            description="Guides parent on registering a TIN for a 2-year-old child with birth certificate.",
            turns=[TurnStep("Nnyinza ntya okufunira omwana wange ow'emyaka ebiri TIN nga nkozesa satifikeeti ye ey'obuzaale okuva mu NIRA?", expect_reply_regex=[r"(?:omwana|tin|satifikeeti|nira|wandiisa|namba)"])],
        ),
        CXScenario(
            id="CX-P7-23",
            category="Pillar 7: Multilingual Fulfillment",
            title="Luganda: Section 42 TPCA Tax Arrears Installment Agreement",
            description="Explains how a struggling taxpayer requests paying tax debt in monthly instalments in Luganda.",
            turns=[TurnStep("Nkoze ntya okusaba URA banzikirize okusasula ebbanja ly'omusolo lyange ery'obukadde 15 mu bitundu bitundu buli mwezi?", expect_reply_regex=[r"(?:ebbanja|bitundu|osasula|enkola|ura|section\s+42)"])],
        ),
        CXScenario(
            id="CX-P7-24",
            category="Pillar 7: Multilingual Fulfillment",
            title="Luganda: Stamp Duty Rates on Kibanja Land Purchase Agreement",
            description="Details stamp duty requirements when acquiring customary kibanja occupancy in Luganda.",
            turns=[TurnStep("Omuwendo gwa Stamp Duty guli gwa bitundu bimeka ku ndagaano y'okugula ekibanja eky'obukadde 50 mu Buganda?", expect_reply_regex=[r"(?:stamp\s+duty|ettaka|ndagaano|bitundu|kibanja)"])],
        ),
        CXScenario(
            id="CX-P7-25",
            category="Pillar 7: Multilingual Fulfillment",
            title="Luganda: Private Medical Clinic Tax Compliance Rules",
            description="Explains VAT exemption and PAYE obligations for private clinics in Luganda.",
            turns=[TurnStep("Kkoliniki y'obwannannyini eteekwa okusasula omusolo ki? Eddagala n'obujjanjabi biriko VAT?", expect_reply_regex=[r"(?:eddagala|bujjanjabi|vat|sonyiye|paye|omusolo)"])],
        ),
        CXScenario(
            id="CX-P7-26",
            category="Pillar 7: Multilingual Fulfillment",
            title="Swahili: Non-Resident Contract Engineer PAYE Calculation",
            description="Calculates progressive non-resident PAYE in Swahili from first shilling.",
            turns=[TurnStep("Mhandisi asiye mkazi wa Uganda anayepokea mshahara wa 6,000,000 UGX anakatwa kodi ya PAYE kiasi gani?", expect_reply_regex=[r"(?:asiye\s+mkazi|paye|kodi|mshahara|ugx)"])],
        ),
        CXScenario(
            id="CX-P7-27",
            category="Pillar 7: Multilingual Fulfillment",
            title="Swahili: Agricultural Tractor Import Duty & VAT Exemption",
            description="Explains zero import duty and zero VAT on agricultural machinery imports in Swahili.",
            turns=[TurnStep("Je, uagizaji wa matrekta ya kilimo na vifaa vyake kutoka ng'ambo unatozwa ushuru wa forodha na VAT?", expect_reply_regex=[r"(?:kilimo|msamaha|ushuru|forodha|vat|trekta)"])],
        ),
        CXScenario(
            id="CX-P7-28",
            category="Pillar 7: Multilingual Fulfillment",
            title="Swahili: Tax Clearance Certificate (TCC) for Public Procurement Tender",
            description="Guides contractor on securing TCC for municipal road works tender in Swahili.",
            turns=[TurnStep("Kampuni yetu ya ujenzi inahitaji Cheti cha Uzingatiaji Kodi (TCC) ili kuwasilisha zabuni ya barabara. Tunaombaje?", expect_reply_regex=[r"(?:tcc|cheti|zabuni|kodi|etax|maombi)"])],
        ),
        CXScenario(
            id="CX-P7-29",
            category="Pillar 7: Multilingual Fulfillment",
            title="Swahili: Generating PRN for Monthly VAT Payment via Bank",
            description="Explains generating e-Tax PRN voucher and paying via commercial bank in Swahili.",
            turns=[TurnStep("Jinsi ya kutengeneza nambari ya usajili wa malipo (PRN) kwenye mtandao ili kulipa kodi ya VAT benki?", expect_reply_regex=[r"(?:prn|malipo|vat|benki|nambari|etax)"])],
        ),
        CXScenario(
            id="CX-P7-30",
            category="Pillar 7: Multilingual Fulfillment",
            title="Swahili: Section 42 TPCA Debt Installment Agreement Request",
            description="Walks through negotiating an installment agreement for tax arrears in Swahili.",
            turns=[TurnStep("Biashara yetu inakabiliwa na deni la kodi la 25,000,000 UGX. Tunawezaje kuomba kulipa kwa awamu chini ya Kifungu cha 42?", expect_reply_regex=[r"(?:kifungu\s+cha\s+42|awamu|deni|malipo|makubaliano|ura)"])],
        ),
    ])

    # -------------------------------------------------------------------------
    # PILLAR 8 EXTRA: Statutory Boundary Probing & Classification (10 Scenarios: CX-P8-21 to CX-P8-30)
    # -------------------------------------------------------------------------
    extras.extend([
        CXScenario(
            id="CX-P8-21",
            category="Pillar 8: Statutory Boundary Probing",
            title="Distinguishing Taxable Commercial Leases from Exempt Residential Tenancies",
            description="Probes the statutory boundary between VAT-taxable commercial retail letting and exempt residential dwelling leases.",
            turns=[TurnStep("If a single building has retail shops on the ground floor and apartments on the upper floors, how is VAT applied to the rent?", expect_reply_regex=[r"(?:commercial|residential|exempt|taxable|apportion|vat)"])],
        ),
        CXScenario(
            id="CX-P8-22",
            category="Pillar 8: Statutory Boundary Probing",
            title="Zero-Rated Export of Services vs Domestic Financial Services",
            description="Examines VAT status of software support provided to foreign clients vs domestic financial transactions.",
            turns=[TurnStep("Does a Ugandan software developer billing an enterprise client in London qualify for 0% VAT zero-rating as an export of service?", expect_reply_regex=[r"(?:zero-rated|export\s+of\s+service|0%|consumption|outside\s+uganda)"])],
        ),
        CXScenario(
            id="CX-P8-23",
            category="Pillar 8: Statutory Boundary Probing",
            title="Thin Capitalization & Section 25 30% Tax EBITDA Interest Cap",
            description="Probes the interest expense deduction ceiling on cross-border related party shareholder loans.",
            turns=[TurnStep("Can a Ugandan subsidiary deduct 100% of interest paid on a loan from its foreign parent company under Section 25 ITA?", expect_reply_regex=[r"(?:30%|ebitda|interest|thin\s+capitalization|section\s+25|cap)"])],
        ),
        CXScenario(
            id="CX-P8-24",
            category="Pillar 8: Statutory Boundary Probing",
            title="Transfer Pricing Documentation Thresholds for Controlled Transactions",
            description="Probes documentation requirements for related-party commercial dealings exceeding statutory aggregate thresholds.",
            turns=[TurnStep("At what transaction value is a multinational subsidiary in Uganda legally required to prepare contemporaneous transfer pricing documentation?", expect_reply_regex=[r"(?:transfer\s+pricing|related\s+party|documentation|threshold|arm's\s+length)"])],
        ),
        CXScenario(
            id="CX-P8-25",
            category="Pillar 8: Statutory Boundary Probing",
            title="Assessed Tax Loss 5-Year 50% Limitation Rule (Section 38 ITA)",
            description="Probes limitation on carrying forward tax losses to shield current year taxable income.",
            turns=[TurnStep("Can a company carry forward assessed tax losses for 7 consecutive years to reduce its taxable income to zero?", expect_reply_regex=[r"(?:5\s+years?|50%|loss|carry\s+forward|section\s+38|limitation)"])],
        ),
        CXScenario(
            id="CX-P8-26",
            category="Pillar 8: Statutory Boundary Probing",
            title="Imported Electronic Software vs Technical Management Services",
            description="Distinguishes 15% non-resident management service withholding from standard software shrink-wrap acquisitions.",
            turns=[TurnStep("Is payment for an off-the-shelf downloadable software license treated as a royalty, technical fee, or digital service for withholding tax?", expect_reply_regex=[r"(?:royalty|technical|withholding|software|dst|service)"])],
        ),
        CXScenario(
            id="CX-P8-27",
            category="Pillar 8: Statutory Boundary Probing",
            title="VAT Input Recovery Disallowance on Staff Entertainment and Meals",
            description="Evaluates input tax restrictions on staff lunches, entertainment, and non-operational passenger vehicles.",
            turns=[TurnStep("Can our company claim input VAT paid on staff lunch catering and executive golf club entertainment expenses?", expect_reply_regex=[r"(?:disallow|entertainment|meals?|input\s+vat|cannot|prohibit)"])],
        ),
        CXScenario(
            id="CX-P8-28",
            category="Pillar 8: Statutory Boundary Probing",
            title="Motor Vehicle Private Use Apportionment for Dual-Purpose Fleet",
            description="Tests statutory rules for apportioning motor vehicle depreciation when used for both commercial delivery and personal driving.",
            turns=[TurnStep("How is capital allowance calculated on a double-cabin pickup truck used 60% for commercial farm deliveries and 40% for personal family travel?", expect_reply_regex=[r"(?:apportion|allowance|depreciation|private\s+use|commercial|pickup)"])],
        ),
        CXScenario(
            id="CX-P8-29",
            category="Pillar 8: Statutory Boundary Probing",
            title="Mineral Export Royalty Rates under the Mining and Minerals Act 2022",
            description="Examines royalty rates on precious metals and unrefined industrial minerals under current legislation.",
            turns=[TurnStep("What mineral royalty percentage applies to the commercial export of artisanal gold vs refined industrial wolfram?", expect_reply_regex=[r"(?:royalty|mineral|gold|mining\s+act|export|percentage)"])],
        ),
        CXScenario(
            id="CX-P8-30",
            category="Pillar 8: Statutory Boundary Probing",
            title="Medical Diagnostic Reagents vs General Industrial Chemicals Tariff Distinction",
            description="Probes customs classification between duty-free medical diagnostic supplies and dutiable industrial chemicals.",
            turns=[TurnStep("How do customs officers differentiate between duty-free clinical laboratory reagents and dutiable general industrial laboratory solvents?", expect_reply_regex=[r"(?:reagent|medical|duty\s+free|customs|classification|tariff)"])],
        ),
    ])

    # -------------------------------------------------------------------------
    # PILLAR 9 EXTRA: Omnichannel Tracking & Integrations (10 Scenarios: CX-P9-21 to CX-P9-30)
    # -------------------------------------------------------------------------
    extras.extend([
        CXScenario(
            id="CX-P9-21",
            category="Pillar 9: Omnichannel Tracking",
            title="Online Objection Tracking via e-Tax Case Management",
            description="Explains checking status and officer assignments for formal objections lodged under Section 24 TPCA.",
            turns=[TurnStep("How do I check the real-time review status of an objection case I submitted on e-Tax 20 days ago?", expect_reply_regex=[r"(?:objection|track|status|case|etax|officer)"])],
        ),
        CXScenario(
            id="CX-P9-22",
            category="Pillar 9: Omnichannel Tracking",
            title="Large Taxpayers Office (LTO) VAT Refund Audit Queue Tracking",
            description="Guides corporate finance directors on tracking statutory 30-day and 90-day VAT refund processing stages.",
            turns=[TurnStep("How does an enterprise track the audit verification stage of an 800M UGX VAT refund claim lodged with the Large Taxpayers Office?", expect_reply_regex=[r"(?:refund|audit|lto|status|track|verification)"])],
        ),
        CXScenario(
            id="CX-P9-23",
            category="Pillar 9: Omnichannel Tracking",
            title="Single Customs Territory (SCT) Mombasa Port Container Clearance Tracking",
            description="Explains tracking entry declarations and container release notices across regional EAC corridors.",
            turns=[TurnStep("How do I verify if our sea container at Mombasa port has received customs release clearance under the Single Customs Territory?", expect_reply_regex=[r"(?:sct|mombasa|single\s+customs|container|clearance|track|release)"])],
        ),
        CXScenario(
            id="CX-P9-24",
            category="Pillar 9: Omnichannel Tracking",
            title="EFRIS Cryptographic Signature and Fiscal Document Verification",
            description="Validates cryptographic receipt verification using the official URA EFRIS public scanner.",
            turns=[TurnStep("How can a consumer verify the authenticity of an EFRIS fiscal electronic invoice and check if the QR code signature is genuine?", expect_reply_regex=[r"(?:efris|qr\s+code|verify|authentic|signature|fiscal)"])],
        ),
        CXScenario(
            id="CX-P9-25",
            category="Pillar 9: Omnichannel Tracking",
            title="e-Tax Ledger Reconciliation After Weekend Bank Clearing Lag",
            description="Explains reconciling cleared payments when commercial bank transfers reflect on Monday morning.",
            turns=[TurnStep("I paid 15M UGX via Stanbic Bank on Friday afternoon but my e-Tax ledger still shows an overdue balance on Monday. How do I reconcile this?", expect_reply_regex=[r"(?:reconcil|ledger|bank|clearing|etax|prn|lag)"])],
        ),
        CXScenario(
            id="CX-P9-26",
            category="Pillar 9: Omnichannel Tracking",
            title="Motor Vehicle Transfer Buyer Acceptance & Logbook Processing Status",
            description="Explains tracking the status of vehicle ownership transfer after seller portal endorsement.",
            turns=[TurnStep("The seller initiated the vehicle transfer online. How do I track when my new logbook is ready for collection at Nakawa?", expect_reply_regex=[r"(?:logbook|transfer|buyer|track|nakawa|motor\s+vehicle)"])],
        ),
        CXScenario(
            id="CX-P9-27",
            category="Pillar 9: Omnichannel Tracking",
            title="Regional Electronic Cargo Tracking System (RECTS) Transit Seal Alert Monitoring",
            description="Explains resolving tamper alerts on GPS electronic container seals during highway transport.",
            turns=[TurnStep("Our transit truck triggered an accidental RECTS seal tamper alert on the Northern Corridor. How do we notify customs monitoring officers?", expect_reply_regex=[r"(?:rects|seal|transit|alert|tamper|corridor|customs)"])],
        ),
        CXScenario(
            id="CX-P9-28",
            category="Pillar 9: Omnichannel Tracking",
            title="Withholding Tax Exemption List Processing Queue Monitoring",
            description="Explains tracking annual corporate WHT exemption approval through the Commissioner's review list.",
            turns=[TurnStep("Where can we track whether our company has been approved on the newly published quarterly Withholding Tax Exemption List?", expect_reply_regex=[r"(?:wht|exemption\s+list|track|gazette|published|commissioner)"])],
        ),
        CXScenario(
            id="CX-P9-29",
            category="Pillar 9: Omnichannel Tracking",
            title="Advance Tax Ruling Application Status Tracking with Commissioner General",
            description="Details tracking statutory response timeframes for private ruling requests under Section 61 TPCA.",
            turns=[TurnStep("How do we follow up on an Advance Private Ruling application submitted to the Commissioner General over 45 days ago?", expect_reply_regex=[r"(?:advance\s+ruling|section\s+61|commissioner|status|track)"])],
        ),
        CXScenario(
            id="CX-P9-30",
            category="Pillar 9: Omnichannel Tracking",
            title="Official URA Tax Audit Letter Authenticity Verification",
            description="Guides taxpayers on verifying official appointment letters from audit teams to prevent fraud.",
            turns=[TurnStep("Two men brought an audit notice to our offices claiming to be URA compliance officers. How do I verify their identity and the audit notice reference?", expect_reply_regex=[r"(?:verify|audit|officer|authentic|toll-free|fraud|credentials)"])],
        ),
    ])

    # -------------------------------------------------------------------------
    # PILLAR 10 EXTRA: Advanced Situational Advisory (10 Scenarios: CX-P10-21 to CX-P10-30)
    # -------------------------------------------------------------------------
    extras.extend([
        CXScenario(
            id="CX-P10-21",
            category="Pillar 10: Advanced Advisory",
            title="Uganda-Tanzania Cross-Border Joint Venture Construction Tax Architecture",
            description="Advises on permanent establishment, withholding tax, and branch profit repatriation under EAC treaty rules.",
            turns=[TurnStep("A Ugandan construction firm is forming an unincorporated 50/50 joint venture with a Tanzanian firm for an oil pipeline sub-contract. What permanent establishment and withholding rules apply?", expect_reply_regex=[r"(?:permanent\s+establishment|pe|joint\s+venture|withholding|tanzania|branch)"])],
        ),
        CXScenario(
            id="CX-P10-22",
            category="Pillar 10: Advanced Advisory",
            title="Debt-to-Equity Conversion in Distressed Agricultural Agro-Enterprise",
            description="Evaluates debt cancellation income, taxable forgiveness, and equity restructuring under Income Tax Act.",
            turns=[TurnStep("Our commercial farming company cannot repay a 5 billion UGX bank loan and the bank agreed to convert the debt into equity shares. Does this trigger taxable debt forgiveness income?", expect_reply_regex=[r"(?:debt\s+forgiveness|debt-to-equity|shares?|income\s+tax|relief|restructur)"])],
        ),
        CXScenario(
            id="CX-P10-23",
            category="Pillar 10: Advanced Advisory",
            title="Insurance Compensation Payout for Fire-Destroyed Plant Machinery",
            description="Examines rollover relief and involuntary asset disposal rules under Section 54 of the Income Tax Act.",
            turns=[TurnStep("A fire destroyed our packaging plant and our insurer paid 2 billion UGX compensation. If we reinvest the entire payout in new machinery within 12 months, does tax apply?", expect_reply_regex=[r"(?:rollover|involuntary|insurance|section\s+54|compensation|reinvest)"])],
        ),
        CXScenario(
            id="CX-P10-24",
            category="Pillar 10: Advanced Advisory",
            title="Corporate De-Merger & Real Estate Asset Spin-Off Tax Neutrality",
            description="Explains tax-neutral group reorganizations and stamp duty exemptions under Section 73 ITA and Stamp Duty Act.",
            turns=[TurnStep("We want to spin off our manufacturing company's commercial real estate into a separate sister property company. How do we achieve statutory tax neutrality?", expect_reply_regex=[r"(?:re-organization|spin-off|tax\s+neutral|stamp\s+duty|section\s+73|assets?)"])],
        ),
        CXScenario(
            id="CX-P10-25",
            category="Pillar 10: Advanced Advisory",
            title="Industrial Park Developer Statutory 10-Year Exemption Eligibility",
            description="Evaluates minimum capital investment criteria under Section 21(1)(af) ITA for industrial park operators.",
            turns=[TurnStep("What are the minimum capital investment and local employment thresholds for an industrial park developer to qualify for the 10-year income tax holiday?", expect_reply_regex=[r"(?:industrial\s+park|holiday|capital|investment|section\s+21|employment)"])],
        ),
        CXScenario(
            id="CX-P10-26",
            category="Pillar 10: Advanced Advisory",
            title="Used Specialized Industrial Plant Customs GATT Valuation Methodology",
            description="Explains customs alternative valuation methods when importing second-hand machinery without recent commercial invoices.",
            turns=[TurnStep("We purchased specialized used milling equipment from a liquidated German factory without standard supplier invoices. How will URA customs determine the customs value under GATT rules?", expect_reply_regex=[r"(?:gatt|valuation|transaction\s+value|deductive|computed|customs)"])],
        ),
        CXScenario(
            id="CX-P10-27",
            category="Pillar 10: Advanced Advisory",
            title="Cryptocurrency & Digital Asset Commercial Trading Taxation",
            description="Explains current revenue authority legal stance on declaring digital asset trading gains as commercial business income.",
            turns=[TurnStep("How are profits from peer-to-peer cryptocurrency and digital asset trading treated under Uganda's Income Tax Act?", expect_reply_regex=[r"(?:income|business|crypto|digital\s+asset|trading|taxable)"])],
        ),
        CXScenario(
            id="CX-P10-28",
            category="Pillar 10: Advanced Advisory",
            title="Carbon Credit Reforestation Export Revenues Tax Treatment",
            description="Details income tax and VAT zero-rating on voluntary carbon credits generated from forestry projects in Uganda.",
            turns=[TurnStep("Our forestry conservation project sells verified carbon offset credits to corporate buyers in Europe. How are carbon credit revenues treated for VAT and corporate tax in Uganda?", expect_reply_regex=[r"(?:carbon|credit|export|zero-rated|income\s+tax|vat)"])],
        ),
        CXScenario(
            id="CX-P10-29",
            category="Pillar 10: Advanced Advisory",
            title="Cross-Border Cloud Infrastructure Hosting Payments Withholding Obligations",
            description="Advises on withholding tax obligations on monthly hosting payments made to global cloud service providers.",
            turns=[TurnStep("Our Ugandan fintech pays $10,000 monthly to Amazon Web Services for cloud hosting in Ireland. Are we required to deduct withholding tax or does Digital Services Tax apply?", expect_reply_regex=[r"(?:withholding|cloud|hosting|dst|digital\s+services|non-resident)"])],
        ),
        CXScenario(
            id="CX-P10-30",
            category="Pillar 10: Advanced Advisory",
            title="Family Business Intergenerational Share Transfer & Succession Planning",
            description="Details capital gains tax exemptions on inter vivos gifts between immediate family members under Section 21 ITA.",
            turns=[TurnStep("An aging founder wants to gift 40% of company shares to his adult children who run daily operations. Is this gift of shares subject to capital gains tax in Uganda?", expect_reply_regex=[r"(?:gift|shares?|capital\s+gains?|succession|family|exempt|transfer)"])],
        ),
    ])

    return extras

if __name__ == "__main__":
    ex = build_extra_third_100_scenarios()
    print(f"Created {len(ex)} third batch extra scenarios.")

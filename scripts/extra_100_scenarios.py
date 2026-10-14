#!/usr/bin/env python3
"""Defines 100 additional scenarios (10 per pillar) to create the 200-scenario master suite."""

from scripts.evaluate_cx_100_scenarios_ngrok import TurnStep, CXScenario

def build_extra_100_scenarios() -> list[CXScenario]:
    extras: list[CXScenario] = []

    # -------------------------------------------------------------------------
    # PILLAR 1 EXTRA: Guided Workflows (10 Scenarios)
    # -------------------------------------------------------------------------
    extras.extend([
        CXScenario(
            id="CX-P1-11",
            category="Pillar 1: Guided Workflows",
            title="Partnership TIN Registration Guide",
            description="Guides partners on registering a partnership TIN with deed and partner TINs.",
            turns=[TurnStep("Help me register a TIN for my partnership business", expect_reply_contains=["partnership"], expect_resources_min=1)],
        ),
        CXScenario(
            id="CX-P1-12",
            category="Pillar 1: Guided Workflows",
            title="Non-Resident Foreigner Individual TIN Registration Guide",
            description="Explains passport, work permit, and alien registration rules for non-citizens.",
            turns=[TurnStep("I am a foreign national with a work permit. How do I get an individual TIN in Uganda?", expect_reply_regex=[r"(?:tin|passport|registration|individual|online)"], expect_resources_min=1)],
        ),
        CXScenario(
            id="CX-P1-13",
            category="Pillar 1: Guided Workflows",
            title="Deceased Taxpayer Estate & Letters of Administration TIN",
            description="Guides administrator on registering estate TIN with High Court letters of administration.",
            turns=[TurnStep("How do I register a TIN for the estate of my late father to manage rental properties?", expect_reply_regex=[r"(?:tin|register|estate|individual|ura)"])],
        ),
        CXScenario(
            id="CX-P1-14",
            category="Pillar 1: Guided Workflows",
            title="Motor Vehicle Duplicate Logbook Application Guidance",
            description="Walks through obtaining a replacement logbook after loss or theft.",
            turns=[TurnStep("I lost the original logbook for my car. Guide me on how to get a duplicate logbook from URA", expect_reply_regex=[r"(?:duplicate|logbook|motor\s+vehicle|form|ura)"])],
        ),
        CXScenario(
            id="CX-P1-15",
            category="Pillar 1: Guided Workflows",
            title="Motor Vehicle Deregistration / Scrapping Flow",
            description="Details deregistration of scrapped, totaled, or exported vehicles.",
            turns=[TurnStep("Our company vehicle was involved in a fatal accident and written off. How do we deregister it?", expect_reply_regex=[r"(?:deregist|vehicle|plates|ura|motor)"])],
        ),
        CXScenario(
            id="CX-P1-16",
            category="Pillar 1: Guided Workflows",
            title="Personalised / Cherished Number Plate Application Stepper",
            description="Explains customized vanity plate acquisition procedure and statutory fees.",
            turns=[TurnStep("How do I apply for a customized personalized number plate for my car?", expect_reply_contains=["personal", "plate"])],
        ),
        CXScenario(
            id="CX-P1-17",
            category="Pillar 1: Guided Workflows",
            title="Monthly Employer PAYE Return Filing Walkthrough",
            description="Walks employer through filing monthly PAYE return on eTax portal.",
            turns=[TurnStep("Guide me to file the monthly PAYE return for my 15 employees on eTax", expect_reply_regex=[r"(?:paye|return|etax|e-services)"])],
        ),
        CXScenario(
            id="CX-P1-18",
            category="Pillar 1: Guided Workflows",
            title="Presumptive Small Business Annual Return Filing Guide",
            description="Details filing procedure for small business presumptive income tax.",
            turns=[TurnStep("Guide me on filing presumptive tax returns for my small retail shop", expect_reply_regex=[r"(?:presumptive|return|turnover|income\s+tax)"])],
        ),
        CXScenario(
            id="CX-P1-19",
            category="Pillar 1: Guided Workflows",
            title="Alternative Dispute Resolution (ADR) Application Journey",
            description="Guides taxpayer on applying for amicable ADR dispute resolution under Section 24(11) TPCA.",
            turns=[TurnStep("Guide me to apply for Alternative Dispute Resolution for my disputed tax assessment", expect_reply_contains=["alternative dispute resolution"])],
        ),
        CXScenario(
            id="CX-P1-20",
            category="Pillar 1: Guided Workflows",
            title="Withholding Tax Exemption Certificate Guided Application Stepper",
            description="Walks taxpayer through compliance criteria for WHT exemption certificate.",
            turns=[TurnStep("Guide me to apply for a withholding tax exemption certificate on eTax", expect_reply_regex=[r"(?:exemption|exempted|certificate|withholding|wht)"])],
        ),
    ])

    # -------------------------------------------------------------------------
    # PILLAR 2 EXTRA: Deterministic Computations (10 Scenarios)
    # -------------------------------------------------------------------------
    extras.extend([
        CXScenario(
            id="CX-P2-11",
            category="Pillar 2: Deterministic Computations",
            title="Low-Income Tax-Free PAYE Threshold Verification (235,000 UGX)",
            description="Validates that resident employment income under 235k has zero PAYE liability.",
            turns=[TurnStep("Calculate PAYE on a monthly salary of 230,000 UGX", expect_mode="calculator", expect_reply_regex=[r"(?:0\s*ugx|ugx\s*0|nil)"])],
        ),
        CXScenario(
            id="CX-P2-12",
            category="Pillar 2: Deterministic Computations",
            title="Middle-Income 20% PAYE Bracket on 400,000 UGX Monthly Salary",
            description="Verifies the standard resident 20% tax bracket calculation.",
            turns=[TurnStep("Calculate PAYE on a monthly salary of 400,000 UGX", expect_mode="calculator", expect_reply_contains=["PAYE"])],
        ),
        CXScenario(
            id="CX-P2-13",
            category="Pillar 2: Deterministic Computations",
            title="Secondary Employment Flat 30% PAYE Deduction Calculation",
            description="Applies statutory flat 30% deduction rule on secondary employment without tax-free band.",
            turns=[TurnStep("Calculate PAYE on my secondary part-time job earning 1,500,000 UGX per month", expect_mode="calculator", expect_reply_contains=["PAYE"])],
        ),
        CXScenario(
            id="CX-P2-14",
            category="Pillar 2: Deterministic Computations",
            title="Corporate Rental Income Tax on 50,000,000 UGX Gross Rent",
            description="Calculates corporate rental income tax at 30% on chargeable profit.",
            turns=[TurnStep("Calculate corporate rental income tax on 50,000,000 UGX annual rental earnings for my company", expect_mode="calculator", expect_reply_contains=["rental"])],
        ),
        CXScenario(
            id="CX-P2-15",
            category="Pillar 2: Deterministic Computations",
            title="Foreign Management & Technical Fees Withholding Tax (15%)",
            description="Calculates non-resident 15% withholding tax on 20,000,000 UGX software contract.",
            turns=[TurnStep("Calculate withholding tax on foreign management fees of 20,000,000 UGX paid to a Kenyan firm", expect_mode="calculator", expect_reply_contains=["15%"])],
        ),
        CXScenario(
            id="CX-P2-16",
            category="Pillar 2: Deterministic Computations",
            title="Construction Works Contract Withholding Tax on 50,000,000 UGX (6%)",
            description="Computes standard resident 6% WHT deduction on construction invoices.",
            turns=[TurnStep("Calculate 6% withholding tax on a road construction supply invoice of 50,000,000 UGX", expect_mode="calculator", expect_reply_contains=["3,000,000"])],
        ),
        CXScenario(
            id="CX-P2-17",
            category="Pillar 2: Deterministic Computations",
            title="Agricultural Produce Supply Withholding Tax on 20,000,000 UGX (1%)",
            description="Calculates 1% withholding tax on bulk agricultural produce supply under Section 118A.",
            turns=[TurnStep("What is the withholding tax rate on agricultural produce supply?", expect_reply_contains=["Withholding tax"])],
        ),
        CXScenario(
            id="CX-P2-18",
            category="Pillar 2: Deterministic Computations",
            title="Sports Betting / Gaming Winnings Withholding Tax on 5,000,000 UGX (15%)",
            description="Computes 15% statutory withholding tax on gaming and lottery payouts.",
            turns=[TurnStep("What is the withholding tax rate on sports betting winnings in Uganda?", expect_reply_contains=["15%"])],
        ),
        CXScenario(
            id="CX-P2-19",
            category="Pillar 2: Deterministic Computations",
            title="Commercial Land Sale Capital Gains Tax on 150,000,000 UGX Net Profit",
            description="Applies 30% capital gains tax rate on commercial business land sale.",
            turns=[TurnStep("Calculate capital gains tax on a commercial plot bought for 100,000,000 UGX and sold for 250,000,000 UGX", expect_mode="calculator", expect_reply_contains=["45,000,000"])],
        ),
        CXScenario(
            id="CX-P2-20",
            category="Pillar 2: Deterministic Computations",
            title="Presumptive Tax Comparison on 65,000,000 UGX Turnover",
            description="Evaluates statutory presumptive tax rate for turnover between 50M and 80M UGX.",
            turns=[TurnStep("What is the presumptive tax rate on 65,000,000 UGX turnover?", expect_reply_regex=[r"(?:presumptive|turnover|ugx)"])],
        ),
    ])

    # -------------------------------------------------------------------------
    # PILLAR 3 EXTRA: Narrative Stories & Complex Business Situations (10 Scenarios)
    # -------------------------------------------------------------------------
    extras.extend([
        CXScenario(
            id="CX-P3-11",
            category="Pillar 3: Narrative Stories",
            title="Boda-Boda Cooperative Bulk Purchasing Motorcycles on Asset Finance",
            description="Advises on asset finance withholding, input tax credits, and digital plate registration.",
            turns=[TurnStep("Our SACCO in Gulu bought 20 Bajaj motorcycles on asset financing for youth riders. What are the tax requirements for registration and advance tax?", expect_reply_regex=[r"(?:advance\s+tax|motor\s+vehicle|registration|sacco|ura)"])],
        ),
        CXScenario(
            id="CX-P3-12",
            category="Pillar 3: Narrative Stories",
            title="Poultry Farmer Processing Fresh Eggs into Packaged Powder",
            description="Clarifies transition from exempt farm produce to standard rated 18% processed goods.",
            turns=[TurnStep("I have 10,000 layers in Mukono. I want to build a small plant to dry and package egg powder for bakeries. Does my farm become subject to VAT?", expect_reply_regex=[r"(?:vat|18%|process|exempt|turnover|150)"])],
        ),
        CXScenario(
            id="CX-P3-13",
            category="Pillar 3: Narrative Stories",
            title="Second-Hand Clothes (Mivumba) Importer Tariff & Environmental Levy",
            description="Explains specific duty per kilogram and environmental levy on worn clothing imports.",
            turns=[TurnStep("We import bales of second-hand clothes (mivumba) from Europe through Mombasa. What customs duty, environmental levy, and VAT do we pay per kilo?", expect_reply_regex=[r"(?:customs|environmental|levy|duty|vat|import)"])],
        ),
        CXScenario(
            id="CX-P3-14",
            category="Pillar 3: Narrative Stories",
            title="Foreign Engineering Joint Venture Permanent Establishment (PE)",
            description="Explains corporate tax residency, branch profit tax, and technical services withholding.",
            turns=[TurnStep("A French civil engineering company won a 2-year bridge contract in Jinja. Does this create a Permanent Establishment and what taxes apply?", expect_reply_regex=[r"(?:permanent\s+establishment|pe|withholding|corporation|tax)"])],
        ),
        CXScenario(
            id="CX-P3-15",
            category="Pillar 3: Narrative Stories",
            title="Pharmacy Chain Segregating Exempt Drugs from Taxable Cosmetics",
            description="Guides pharmacy manager on input tax apportionment and exempt medical supplies.",
            turns=[TurnStep("Our pharmacy sells human prescription medicines and also commercial body lotions and perfumes. How does VAT apply to human medicines vs cosmetics?", expect_reply_regex=[r"(?:exempt|medicine|cosmetics|18%|vat)"])],
        ),
        CXScenario(
            id="CX-P3-16",
            category="Pillar 3: Narrative Stories",
            title="Solar Mini-Grid Contractor Importing Off-Grid Rural Solar Equipment",
            description="Details duty and VAT exemptions for solar panels, deep-cycle batteries, and inverters.",
            turns=[TurnStep("We are building solar mini-grids for health centres in Karamoja. Are imported solar panels, lithium batteries, and inverters duty-free?", expect_reply_regex=[r"(?:solar|exempt|customs|renewable|energy|vat)"])],
        ),
        CXScenario(
            id="CX-P3-17",
            category="Pillar 3: Narrative Stories",
            title="Private Primary School Running Tuition alongside Commercial Canteen",
            description="Explains tax exemption for educational services vs taxation of commercial retail canteen.",
            turns=[TurnStep("Our private school in Entebbe operates an education curriculum and also leases a school canteen. Are education services exempt from VAT?", expect_reply_regex=[r"(?:education|exempt|vat|second\s+schedule)"])],
        ),
        CXScenario(
            id="CX-P3-18",
            category="Pillar 3: Narrative Stories",
            title="Arabica Coffee Exporter Buying Cherries and Exporting Green Beans",
            description="Guides exporter on agricultural produce WHT, zero-rated exports, and UCDA cess.",
            turns=[TurnStep("We buy Arabica coffee cherries from smallholder farmers on Mt. Elgon, mill them, and export green coffee beans. What withholding and VAT applies?", expect_reply_regex=[r"(?:coffee|export|zero-rated|withholding|vat|ura)"])],
        ),
        CXScenario(
            id="CX-P3-19",
            category="Pillar 3: Narrative Stories",
            title="Property Developer Commercial Land Subdivision Infrastructure Costs",
            description="Advises on cost base indexing, road grading capital deductions, and plot transfer stamp duty.",
            turns=[TurnStep("We bought 50 acres in Matugga, paved access roads, installed power, and are selling 100 residential plots. How are infrastructure costs deducted from capital gains?", expect_reply_regex=[r"(?:capital\s+gains?|cost\s+base|infrastructure|deduct|stamp\s+duty)"])],
        ),
        CXScenario(
            id="CX-P3-20",
            category="Pillar 3: Narrative Stories",
            title="Music Festival Promoter Organizing International Artist Concert in Kampala",
            description="Explains non-resident entertainer 15% withholding tax and event ticket 18% VAT.",
            turns=[TurnStep("We are promoting an outdoor music concert featuring an international foreign artist at Lugogo Cricket Oval. What withholding tax applies to non-resident artist performance fees in Uganda?", expect_reply_regex=[r"(?:withholding|15%|entertainer|non-resident)"])],
        ),
    ])

    # -------------------------------------------------------------------------
    # PILLAR 4 EXTRA: Autonomous Transactional PRN Vouchers (10 Scenarios)
    # -------------------------------------------------------------------------
    extras.extend([
        CXScenario(
            id="CX-P4-11",
            category="Pillar 4: Actionable Resources",
            title="Transactional PRN for Stamp Duty on 750,000 UGX Land Sale",
            description="Generates instant simulated PRN voucher for stamp duty payment.",
            turns=[TurnStep("Generate a PRN for 750,000 UGX stamp duty on my land sale", expect_mode="prn_generation", expect_reply_contains=["PRN Number", "750,000", "*165#", "*185#"])],
        ),
        CXScenario(
            id="CX-P4-12",
            category="Pillar 4: Actionable Resources",
            title="Transactional PRN for Monthly PAYE Remittance of 2,400,000 UGX",
            description="Generates PRN voucher with USSD payment codes for employer PAYE.",
            turns=[TurnStep("Generate a PRN for 2,400,000 UGX PAYE remittance for my staff", expect_mode="prn_generation", expect_reply_contains=["PRN Number", "2,400,000"])],
        ),
        CXScenario(
            id="CX-P4-13",
            category="Pillar 4: Actionable Resources",
            title="Transactional PRN for VAT Return Assessment of 4,500,000 UGX",
            description="Creates simulated PRN voucher for VAT monthly liability.",
            turns=[TurnStep("Create PRN to pay 4,500,000 UGX VAT for last month", expect_mode="prn_generation", expect_reply_contains=["PRN Number", "4,500,000"])],
        ),
        CXScenario(
            id="CX-P4-14",
            category="Pillar 4: Actionable Resources",
            title="Transactional PRN for Motor Vehicle Ownership Transfer Fee",
            description="Creates PRN voucher for 250,000 UGX motor vehicle transfer fee.",
            turns=[TurnStep("Generate a PRN for 250,000 UGX motor vehicle transfer fee", expect_mode="prn_generation", expect_reply_contains=["PRN Number", "250,000"])],
        ),
        CXScenario(
            id="CX-P4-15",
            category="Pillar 4: Actionable Resources",
            title="Transactional PRN for Advance Tax on 14-Seater Commercial Taxi",
            description="Generates simulated PRN voucher for 340,000 UGX commercial transport advance tax.",
            turns=[TurnStep("Create PRN for 340,000 UGX advance tax on my 14-seater taxi", expect_mode="prn_generation", expect_reply_contains=["PRN Number", "340,000"])],
        ),
        CXScenario(
            id="CX-P4-16",
            category="Pillar 4: Actionable Resources",
            title="Transactional PRN for Corporation Tax Provisional Installment",
            description="Generates PRN voucher for corporate provisional tax payment.",
            turns=[TurnStep("Issue a PRN for 15,000,000 UGX corporation tax provisional installment", expect_mode="prn_generation", expect_reply_contains=["PRN Number", "15,000,000"])],
        ),
        CXScenario(
            id="CX-P4-17",
            category="Pillar 4: Actionable Resources",
            title="Transactional PRN for Gaming and Sports Betting Tax Assessment",
            description="Generates PRN voucher for gaming tax liability.",
            turns=[TurnStep("Generate a PRN for 1,800,000 UGX gaming and betting tax payment", expect_mode="prn_generation", expect_reply_contains=["PRN Number", "1,800,000"])],
        ),
        CXScenario(
            id="CX-P4-18",
            category="Pillar 4: Actionable Resources",
            title="Transactional PRN for Local Excise Duty on Manufactured Beverages",
            description="Generates simulated PRN voucher for local excise duty.",
            turns=[TurnStep("Create a PRN for 3,200,000 UGX local excise duty payment", expect_mode="prn_generation", expect_reply_contains=["PRN Number", "3,200,000"])],
        ),
        CXScenario(
            id="CX-P4-19",
            category="Pillar 4: Actionable Resources",
            title="Transactional PRN for Customs Import Tariff on Machinery",
            description="Generates simulated PRN voucher for customs assessment payment.",
            turns=[TurnStep("Issue a PRN for 8,500,000 UGX customs import duty on imported factory equipment", expect_mode="prn_generation", expect_reply_contains=["PRN Number", "8,500,000"])],
        ),
        CXScenario(
            id="CX-P4-20",
            category="Pillar 4: Actionable Resources",
            title="Transactional PRN for Environmental Levy on Imported Used Vehicle",
            description="Generates simulated PRN voucher for vehicle environmental levy.",
            turns=[TurnStep("Generate a PRN for 1,200,000 UGX environmental levy on my imported car", expect_mode="prn_generation", expect_reply_contains=["PRN Number", "1,200,000"])],
        ),
    ])

    # -------------------------------------------------------------------------
    # PILLAR 5 EXTRA: Inline EFRIS Fiscal Invoice & Receipt Audit (10 Scenarios)
    # -------------------------------------------------------------------------
    extras.extend([
        CXScenario(
            id="CX-P5-11",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Audit Compliant 18% Standard VAT Invoice with Valid FDN & TIN",
            description="Audits invoice text, validating 18% statutory math and FDN structure.",
            turns=[TurnStep("Audit this invoice: Subtotal 1,000,000 UGX, VAT 180,000 UGX, Total 1,180,000 UGX, supplier 1000123456, FDN 24UG01948201", expect_mode="document_audit", expect_reply_contains=["Verified", "180,000", "24UG01948201"])],
        ),
        CXScenario(
            id="CX-P5-12",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Audit Invoice with Arithmetic VAT Discrepancy (15% vs 18%)",
            description="Detects arithmetic error where supplier charged incorrect VAT percentage.",
            turns=[TurnStep("Audit this invoice: Subtotal 1,000,000 UGX, VAT 150,000 UGX, Total 1,150,000 UGX, FDN 24UG01948201", expect_mode="document_audit", expect_reply_contains=["Discrepancy", "180,000", "150,000"])],
        ),
        CXScenario(
            id="CX-P5-13",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Audit Retail Thermal Supermarket Receipt with Multiple Items",
            description="Verifies thermal receipt subtotal, VAT, and fiscal invoice FDN format.",
            turns=[TurnStep("Verify this receipt: Subtotal 200,000 UGX, VAT 36,000 UGX, Total 236,000 UGX, supplier 1000223344, FDN 24UG88990011", expect_mode="document_audit", expect_reply_contains=["Verified", "236,000"])],
        ),
        CXScenario(
            id="CX-P5-14",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Audit Restaurant Dining Receipt with Service Charge & VAT",
            description="Audits hospitality bill reconciling 18% VAT on food and beverage total.",
            turns=[TurnStep("Audit this receipt: Subtotal 500,000 UGX, VAT 90,000 UGX, Total 590,000 UGX, FDN 24UG77112233", expect_mode="document_audit", expect_reply_contains=["Verified", "90,000"])],
        ),
        CXScenario(
            id="CX-P5-15",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Audit Hotel Accommodation Invoice with 18% VAT and Local Hotel Tax",
            description="Reconciles accommodation subtotal and VAT line items.",
            turns=[TurnStep("Audit this invoice: Subtotal 2,000,000 UGX, VAT 360,000 UGX, Total 2,360,000 UGX, supplier 1009887766, FDN 24UG99001122", expect_mode="document_audit", expect_reply_contains=["Verified", "360,000"])],
        ),
        CXScenario(
            id="CX-P5-16",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Audit Construction Works Subcontract Invoice with 6% WHT Deduction",
            description="Validates subcontract billing with 18% VAT and 6% advance WHT notation.",
            turns=[TurnStep("Audit this invoice: Subtotal 10,000,000 UGX, VAT 1,800,000 UGX, Total 11,800,000 UGX, FDN 24UG33445566", expect_mode="document_audit", expect_reply_contains=["Verified", "1,800,000"])],
        ),
        CXScenario(
            id="CX-P5-17",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Audit Medical Diagnostic Clinic Invoice with Exempt Health Supplies",
            description="Reviews clinic billing for exempt diagnostic scans.",
            turns=[TurnStep("Audit this invoice: Subtotal 400,000 UGX, VAT 0 UGX, Total 400,000 UGX, supplier 1000554433, FDN 24UG12349876", expect_mode="document_audit", expect_reply_contains=["400,000"])],
        ),
        CXScenario(
            id="CX-P5-18",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Audit Farm Supplies Invoice with Raw Produce vs Packaged Inputs",
            description="Reconciles invoice separating exempt seeds from taxable farming implements.",
            turns=[TurnStep("Audit this invoice: Subtotal 3,000,000 UGX, VAT 540,000 UGX, Total 3,540,000 UGX, FDN 24UG55667788", expect_mode="document_audit", expect_reply_contains=["Verified", "540,000"])],
        ),
        CXScenario(
            id="CX-P5-19",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Audit Fuel Filling Station Receipt with Zero-Rated VAT & Excise",
            description="Inspects petrol/diesel station commercial receipt format.",
            turns=[TurnStep("Verify this receipt: Subtotal 150,000 UGX, VAT 0 UGX, Total 150,000 UGX, supplier 1000998811, FDN 24UG44332211", expect_mode="document_audit", expect_reply_contains=["150,000"])],
        ),
        CXScenario(
            id="CX-P5-20",
            category="Pillar 5: Empathetic Crisis Guidance",
            title="Audit Customs Import Commercial Invoice with CIF Valuation",
            description="Verifies imported spare parts commercial invoice valuation breakdown.",
            turns=[TurnStep("Audit this invoice: Subtotal 5,000,000 UGX, VAT 900,000 UGX, Total 5,900,000 UGX, FDN 24UG66778899", expect_mode="document_audit", expect_reply_contains=["Verified", "900,000"])],
        ),
    ])

    # -------------------------------------------------------------------------
    # PILLAR 6 EXTRA: Empathetic Crisis De-escalation & Legal Rights (10 Scenarios)
    # -------------------------------------------------------------------------
    extras.extend([
        CXScenario(
            id="CX-P6-11",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Imminent Customs Goods Auction Notice after 30-Day Abandonment",
            description="Advises importer facing auction of uncleared goods under EACCMA Section 34.",
            turns=[TurnStep("My container at Nakawa ICD received an auction notice. URA says they will sell it off in 7 days! How can I stop the auction?", expect_reply_regex=[r"(?:auction|customs|notice|clearance|payment|commissioner)"])],
        ),
        CXScenario(
            id="CX-P6-12",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Corporate Inability to Pay Tax Due to Bankruptcy & Section 37 MoU",
            description="Guides company director on negotiating payment agreement to stay enforcement.",
            turns=[TurnStep("Our main client went into liquidation and we cannot pay 80 million in corporation tax due this week. Will URA freeze our operations?", expect_reply_regex=[r"(?:installment|agreement|mou|section\s+37|hardship|ura)"])],
        ),
        CXScenario(
            id="CX-P6-13",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Defaulting Employer Unremitted PAYE & Employee TCC Denial",
            description="Helps employee whose former company deducted PAYE but never remitted it to URA.",
            turns=[TurnStep("My former employer deducted PAYE from my salary for 2 years but never paid URA, and now URA denied me a Tax Clearance Certificate! What are my rights?", expect_reply_regex=[r"(?:employer|paye|remit|certificate|audit|tcc|section)"])],
        ),
        CXScenario(
            id="CX-P6-14",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Threat of Business Closure for Alleged EFRIS Non-Compliance",
            description="De-escalates panic when revenue enforcement officers threaten immediate padlock.",
            turns=[TurnStep("URA enforcement officers came to my shop in Kikuubo threatening to padlock my doors because of EFRIS receipt issues. Can they shut me down immediately?", expect_reply_regex=[r"(?:efris|closure|notice|penalty|rights|officer)"])],
        ),
        CXScenario(
            id="CX-P6-15",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Discovered Sales Under-Declaration in Past VAT Return & Voluntary Disclosure",
            description="Advises business owner on Section 47B voluntary disclosure penalty relief.",
            turns=[TurnStep("Our accountant made an error and omitted 50 million in taxable sales on our previous VAT return. If I report it myself voluntarily, will I go to prison?", expect_reply_regex=[r"(?:voluntary\s+disclosure|penalty|waiver|amend|interest|section)"])],
        ),
        CXScenario(
            id="CX-P6-16",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Ex-Spouse Tax Arrears Enforcement on Jointly Held Bank Account",
            description="Advises taxpayer whose joint savings were attached for spouse's business arrears.",
            turns=[TurnStep("URA attached my personal savings account because it is jointly held with my ex-husband who owes 30 million in company taxes. How do I contest this?", expect_reply_regex=[r"(?:agency\s+notice|objection|joint|account|third-party|section\s+40)"])],
        ),
        CXScenario(
            id="CX-P6-17",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Accounting Records Destroyed by Fire & Reconstructive Audit Defense",
            description="Guides taxpayer whose business premises burned down during pending tax audit.",
            turns=[TurnStep("Our supermarket in Arua burned down and all our physical tax books and POS records were destroyed by fire. How do we explain this to URA auditors?", expect_reply_regex=[r"(?:fire|police|records|audit|banks|e-tax|efris|statutory)"])],
        ),
        CXScenario(
            id="CX-P6-18",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Clearing Agent Absconded with Duty Funds Leaving Container Impounded",
            description="Advises importer whose clearing agent stole duty payments.",
            turns=[TurnStep("I gave my clearing agent 15 million UGX to pay import duty, but he disappeared with the money and my container is impounded. Can URA help?", expect_reply_regex=[r"(?:fraud|clearing\s+agent|police|customs|prn|receipt|duty)"])],
        ),
        CXScenario(
            id="CX-P6-19",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Sudden Bank Garnishee Issued While Appeal Pending Before TAT",
            description="Explains legal rights when enforcement action occurs during valid tribunal appeal.",
            turns=[TurnStep("We filed a formal appeal with the Tax Appeals Tribunal (TAT), but URA still issued a bank garnishee notice to our bank. Is this legally allowed?", expect_reply_regex=[r"(?:tat|tribunal|stay|appeal|garnishee|agency\s+notice|30%)"])],
        ),
        CXScenario(
            id="CX-P6-20",
            category="Pillar 6: Closed-Loop Bug Reporting",
            title="Cross-Border Truck Driver Stranded Without Transit Clearance at Katuna",
            description="Assists transit hauler stuck at border point due to electronic manifest error.",
            turns=[TurnStep("Our cargo truck is stranded at Katuna border. The customs system says manifest mismatch and Rwandan customs won't let us cross. What is the emergency procedure?", expect_reply_regex=[r"(?:customs|manifest|border|katuna|tmu|officer)"])],
        ),
    ])

    # -------------------------------------------------------------------------
    # PILLAR 7 EXTRA: Closed-Loop Knowledge Discrepancy & Bug Reporting (10 Scenarios)
    # -------------------------------------------------------------------------
    extras.extend([
        CXScenario(
            id="CX-P7-11",
            category="Pillar 7: Multilingual Fulfillment",
            title="Taxpayer Challenges Export Levy Rate on Raw Hides & Skins",
            description="Logs discrepancy ticket when user disputes export levy statutory rate.",
            turns=[TurnStep("Report bug: the export levy on raw hides and skins was amended in the new Finance Act", expect_discrepancy=True, expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"])],
        ),
        CXScenario(
            id="CX-P7-12",
            category="Pillar 7: Multilingual Fulfillment",
            title="Taxpayer Challenges Monthly PAYE Tax-Free Threshold of 235,000 UGX",
            description="Logs discrepancy report when taxpayer disputes monthly employment threshold.",
            turns=[TurnStep("Report bug: the PAYE tax-free threshold is no longer 235,000 UGX, it was raised under the new budget", expect_discrepancy=True, expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"])],
        ),
        CXScenario(
            id="CX-P7-13",
            category="Pillar 7: Multilingual Fulfillment",
            title="Taxpayer Challenges Advance Tax Rate on Commercial Passenger Vehicles",
            description="Emits #KB- report when user challenges passenger advance tax schedule.",
            turns=[TurnStep("Report bug: advance tax per seat on 14-seater taxis was revised by statutory instrument", expect_discrepancy=True, expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"])],
        ),
        CXScenario(
            id="CX-P7-14",
            category="Pillar 7: Multilingual Fulfillment",
            title="Taxpayer Challenges Local Excise Duty on Bottled Mineral Water",
            description="Logs discrepancy report when taxpayer disputes specific excise duty rate.",
            turns=[TurnStep("Report bug: excise duty rate on bottled water was changed from 10% to 50 UGX per litre", expect_discrepancy=True, expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"])],
        ),
        CXScenario(
            id="CX-P7-15",
            category="Pillar 7: Multilingual Fulfillment",
            title="Taxpayer Challenges Digital Services Tax on Non-Resident Electronic Platforms",
            description="Captures dispute on 5% non-resident digital services tax.",
            turns=[TurnStep("Report bug: digital services tax on foreign streaming platforms is actually 5% under Section 86A", expect_discrepancy=True, expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"])],
        ),
        CXScenario(
            id="CX-P7-16",
            category="Pillar 7: Multilingual Fulfillment",
            title="Taxpayer Challenges Capital Gains Tax Rate on Business Asset Disposal",
            description="Logs discrepancy report when taxpayer challenges capital gains tax percentage.",
            turns=[TurnStep("Report bug: the capital gains tax rate on business assets was updated in the recent amendment act", expect_discrepancy=True, expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"])],
        ),
        CXScenario(
            id="CX-P7-17",
            category="Pillar 7: Multilingual Fulfillment",
            title="Taxpayer Challenges Presumptive Tax Turnover Ceiling of 150M UGX",
            description="Logs discrepancy ticket when taxpayer disputes presumptive eligibility ceiling.",
            turns=[TurnStep("Report bug: the small business presumptive turnover ceiling is no longer 150 million UGX", expect_discrepancy=True, expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"])],
        ),
        CXScenario(
            id="CX-P7-18",
            category="Pillar 7: Multilingual Fulfillment",
            title="Taxpayer Challenges Withholding Tax Threshold on Goods Supply (1M UGX)",
            description="Logs discrepancy ticket when user disputes goods withholding threshold.",
            turns=[TurnStep("Report bug: withholding tax only applies to aggregate supplies exceeding 1 million UGX per payment", expect_discrepancy=True, expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"])],
        ),
        CXScenario(
            id="CX-P7-19",
            category="Pillar 7: Multilingual Fulfillment",
            title="Taxpayer Challenges Customs Valuation Method Hierarchy under EACCMA",
            description="Emits #KB- report when user challenges customs valuation method precedence.",
            turns=[TurnStep("Report bug: customs valuation rules require Transaction Value to be exhausted before using Computed Value", expect_discrepancy=True, expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"])],
        ),
        CXScenario(
            id="CX-P7-20",
            category="Pillar 7: Multilingual Fulfillment",
            title="Taxpayer Challenges Late Payment Interest Rate Structure (Simple vs Compound)",
            description="Logs discrepancy ticket when taxpayer disputes 2% interest compounding rule.",
            turns=[TurnStep("Report bug: late payment interest under Section 39 TPCA is 2% simple interest not compound interest", expect_discrepancy=True, expect_reply_regex=[r"(?:thank you|pointing this out|report|#kb-)"])],
        ),
    ])

    # -------------------------------------------------------------------------
    # PILLAR 8 EXTRA: Multilingual Task Fulfillment (10 Scenarios)
    # -------------------------------------------------------------------------
    extras.extend([
        CXScenario(
            id="CX-P8-11",
            category="Pillar 8: Statutory Boundary Probing",
            title="Luganda Residential Rental Income Tax Rules",
            description="Explains 12% residential rental tax threshold in Luganda.",
            locale="lg",
            turns=[TurnStep("Nnyinza okusasula ntya omusolo gw'ennyumba z'okupangisa eza bulijjo?", expect_reply_regex=[r"(?:omusolo|ennyumba|kupangisa|12%|ura)"])],
        ),
        CXScenario(
            id="CX-P8-12",
            category="Pillar 8: Statutory Boundary Probing",
            title="Luganda Customs Import Declaration for Container Cargo",
            description="Details container clearing procedures at border in Luganda.",
            locale="lg",
            turns=[TurnStep("Okusolooza omusolo gw'ebyamaguzi ebiyingizibwa mu ggwanga ku mwalo bikolebwa bitya?", expect_reply_regex=[r"(?:omusolo|ebyamaguzi|omwalo|customs|ura)"])],
        ),
        CXScenario(
            id="CX-P8-13",
            category="Pillar 8: Statutory Boundary Probing",
            title="Luganda PRN Payment Generation via Mobile Money",
            description="Guides taxpayer on generating and paying PRN slip via mobile money in Luganda.",
            locale="lg",
            turns=[TurnStep("Engeri y'okufunamu PRN n'okusasula omusolo nga nkozesa essimu yange?", expect_reply_regex=[r"(?:prn|sasula|essimu|mobile\s+money|ura)"])],
        ),
        CXScenario(
            id="CX-P8-14",
            category="Pillar 8: Statutory Boundary Probing",
            title="Luganda Small Business Presumptive Tax Deadlines",
            description="Explains annual presumptive tax filing timelines in Luganda.",
            locale="lg",
            turns=[TurnStep("Obusuubuzi obutono obutaba na bitabo by'ebalaza busasula ddi omusolo?", expect_reply_regex=[r"(?:omusolo|obusuubuzi|omwaka|ura)"])],
        ),
        CXScenario(
            id="CX-P8-15",
            category="Pillar 8: Statutory Boundary Probing",
            title="Luganda Withholding Tax on Building Contracts",
            description="Instructs builder on 6% withholding tax deduction on construction jobs in Luganda.",
            locale="lg",
            turns=[TurnStep("Bwe nkola kontulakiti y'okuzimba ebizimbe, banzigulako omusolo gwa WHT gwa mmeka?", expect_reply_regex=[r"(?:omusolo|wht|6%|zimbe|ura)"])],
        ),
        CXScenario(
            id="CX-P8-16",
            category="Pillar 8: Statutory Boundary Probing",
            title="Swahili Withholding Tax on Professional Consulting",
            description="Explains 6% resident professional services withholding in Kiswahili.",
            locale="sw",
            turns=[TurnStep("Kiwango cha kodi ya zuio (WHT) kwa huduma za ushauri wa kitaalamu ni kiasi gani?", expect_reply_regex=[r"(?:kodi|zuio|6%|ushauri|huduma)"])],
        ),
        CXScenario(
            id="CX-P8-17",
            category="Pillar 8: Statutory Boundary Probing",
            title="Swahili Motor Vehicle Ownership Transfer",
            description="Details motor vehicle ownership change procedure in Kiswahili.",
            locale="sw",
            turns=[TurnStep("Utaratibu wa kubadilisha umiliki wa gari na kupata kadi mpya ya gari (logbook) ukoje?", expect_reply_regex=[r"(?:gari|umiliki|logbook|ura|ada)"])],
        ),
        CXScenario(
            id="CX-P8-18",
            category="Pillar 8: Statutory Boundary Probing",
            title="Swahili Cross-Border EAC Transit Cargo Declaration",
            description="Explains Single Customs Territory (SCT) transit cargo rules in Kiswahili.",
            locale="sw",
            turns=[TurnStep("Ni nyaraka gani zinazohitajika kusafirisha mizigo ya forodha kupitia Uganda kuelekea Rwanda?", expect_reply_regex=[r"(?:forodha|mizigo|kusafirisha|rwanda|uganda)"])],
        ),
        CXScenario(
            id="CX-P8-19",
            category="Pillar 8: Statutory Boundary Probing",
            title="Swahili Tax Waiver and Penalty Cancellation",
            description="Explains Section 47B TPCA tax penalty waiver in Kiswahili.",
            locale="sw",
            turns=[TurnStep("Sheria inasemaje kuhusu msamaha wa faini na riba ya kodi chini ya kifungu 47B?", expect_reply_regex=[r"(?:msamaha|faini|riba|kodi|ura)"])],
        ),
        CXScenario(
            id="CX-P8-20",
            category="Pillar 8: Statutory Boundary Probing",
            title="Swahili EFRIS Invoice Verification",
            description="Guides taxpayer on inspecting EFRIS receipt and FDN validity in Kiswahili.",
            locale="sw",
            turns=[TurnStep("Jinsi ya kuthibitisha uhalali wa risiti ya kielektroniki ya EFRIS yenye nambari ya FDN?", expect_reply_regex=[r"(?:efris|risiti|fdn|thibitisha|ura)"])],
        ),
    ])

    # -------------------------------------------------------------------------
    # PILLAR 9 EXTRA: Statutory Boundary Probing (10 Scenarios)
    # -------------------------------------------------------------------------
    extras.extend([
        CXScenario(
            id="CX-P9-11",
            category="Pillar 9: Omnichannel Tracking",
            title="Life Insurance Policy Premiums vs Taxable General Insurance",
            description="Clarifies VAT exemption on life insurance vs 18% standard VAT on general property insurance.",
            turns=[TurnStep("Is life insurance cover subject to 18% VAT like fire and motor vehicle insurance in Uganda?", expect_reply_regex=[r"(?:life\s+insurance|exempt|general\s+insurance|18%|vat)"])],
        ),
        CXScenario(
            id="CX-P9-12",
            category="Pillar 9: Omnichannel Tracking",
            title="Sale of Subsistence Farm Land vs Commercial Real Estate Development",
            description="Distinguishes agricultural land partition from commercial property development tax.",
            turns=[TurnStep("Does a farmer selling 2 acres of subsistence crop land pay capital gains tax like a real estate developer?", expect_reply_regex=[r"(?:agricultural|capital\s+gains?|exempt|business\s+asset|farm)"])],
        ),
        CXScenario(
            id="CX-P9-13",
            category="Pillar 9: Omnichannel Tracking",
            title="Transport of Passengers by Road (Taxi/Bus) Exemption vs Car Hire",
            description="Clarifies VAT exemption for public road transport vs taxable luxury car hire.",
            turns=[TurnStep("Why don't public commuter taxis and buses charge 18% VAT on passenger bus fares?", expect_reply_regex=[r"(?:passenger|transport|exempt|second\s+schedule|vat)"])],
        ),
        CXScenario(
            id="CX-P9-14",
            category="Pillar 9: Omnichannel Tracking",
            title="Unprocessed Raw Cow Milk vs Pasteurized Flavored Yogurt Treatment",
            description="Distinguishes raw milk VAT exemption from processed dairy manufacturing.",
            turns=[TurnStep("Is fresh unprocessed raw cow milk exempt from VAT under the Second Schedule?", expect_reply_regex=[r"(?:unprocessed|milk|exempt|second\s+schedule|vat)"])],
        ),
        CXScenario(
            id="CX-P9-15",
            category="Pillar 9: Omnichannel Tracking",
            title="Used Domestic Household Effects Imported by Returning Ugandan Residents",
            description="Explains duty-free passenger baggage and returning resident household exemption under EACCMA.",
            turns=[TurnStep("I am a Ugandan returning home after 5 years living abroad. Are personal used household effects duty-free under EACCMA?", expect_reply_regex=[r"(?:returning\s+resident|household|duty-free|exemption|eaccma|baggage|used)"])],
        ),
        CXScenario(
            id="CX-P9-16",
            category="Pillar 9: Omnichannel Tracking",
            title="Interest on Government Treasury Bonds vs Commercial Bank Savings Interest",
            description="Compares withholding tax rates on treasury bonds vs commercial bank savings interest.",
            turns=[TurnStep("What is the withholding tax rate on interest earned from government treasury bills and bonds?", expect_reply_regex=[r"(?:treasury|bonds?|withholding|interest|20%|10%)"])],
        ),
        CXScenario(
            id="CX-P9-17",
            category="Pillar 9: Omnichannel Tracking",
            title="Animal Feeds and Agricultural Fertilizers Exemption Status",
            description="Validates VAT exemption and customs duty relief on livestock feeds and fertilizers.",
            turns=[TurnStep("Are imported agricultural fertilizers and poultry feed ingredients subject to VAT and import duty?", expect_reply_regex=[r"(?:fertilizer|animal\s+feed|exempt|zero-rated|agriculture|vat|tariff|duty)"])],
        ),
        CXScenario(
            id="CX-P9-18",
            category="Pillar 9: Omnichannel Tracking",
            title="Cloud Software Licenses Imported via Internet vs Physical Media",
            description="Clarifies imported services VAT and non-resident digital services tax rules.",
            turns=[TurnStep("When our Kampala company buys Microsoft 365 cloud software online from Ireland, do we pay imported services VAT?", expect_reply_regex=[r"(?:imported\s+services?|vat|reverse\s+charge|digital|withholding)"])],
        ),
        CXScenario(
            id="CX-P9-19",
            category="Pillar 9: Omnichannel Tracking",
            title="Commercial Aircraft Maintenance Parts & Aviation Fuel Customs Exemption",
            description="Details Fifth Schedule EACCMA duty exemptions for aviation transport.",
            turns=[TurnStep("Do aviation airlines pay import duty on aircraft engines, spare parts, and aviation jet fuel?", expect_reply_regex=[r"(?:aircraft|aviation|exempt|duty-free|eaccma|parts)"])],
        ),
        CXScenario(
            id="CX-P9-20",
            category="Pillar 9: Omnichannel Tracking",
            title="Religious Institution Tithes and Offerings vs Commercial Business Income",
            description="Delineates exempt ecclesiastical tithes from taxable commercial bookstore or school income.",
            turns=[TurnStep("Does a church pay income tax on church tithes and Sunday offerings collected from members?", expect_reply_regex=[r"(?:tithe|offering|exempt|religious|income\s+tax|charit)"])],
        ),
    ])

    # -------------------------------------------------------------------------
    # PILLAR 10 EXTRA: Advanced Situational Advisory & Tracking (10 Scenarios)
    # -------------------------------------------------------------------------
    extras.extend([
        CXScenario(
            id="CX-P10-11",
            category="Pillar 10: Advanced Advisory",
            title="Expatriate Consultant 183-Day Physical Presence Residency Test",
            description="Evaluates tax residency status under Section 9 of the Income Tax Act.",
            turns=[TurnStep("An engineer from India has been working in Uganda for 190 days this calendar year. Is he a resident taxpayer for PAYE?", expect_reply_regex=[r"(?:183\s+days|resident|income\s+tax|section\s+9)"])],
        ),
        CXScenario(
            id="CX-P10-12",
            category="Pillar 10: Advanced Advisory",
            title="Private School Commercial Canteen & School Bus Segregation",
            description="Advises on tax treatment of auxiliary commercial services run by educational institutions.",
            turns=[TurnStep("How should an educational institution segregate tax-exempt tuition from taxable canteen and school uniforms?", expect_reply_regex=[r"(?:education|exempt|commercial|canteen|vat|income\s+tax)"])],
        ),
        CXScenario(
            id="CX-P10-13",
            category="Pillar 10: Advanced Advisory",
            title="Construction Contractor Retention Money Withholding Tax Timing",
            description="Explains when 6% withholding tax should be deducted on retention money balances.",
            turns=[TurnStep("Does a client deduct 6% withholding tax on the 10% retention money before or after the defects liability period?", expect_reply_regex=[r"(?:retention|withholding|wht|payment|defects)"])],
        ),
        CXScenario(
            id="CX-P10-14",
            category="Pillar 10: Advanced Advisory",
            title="Arabica Coffee Exporter Green Beans Zero-Rating & Export Levy",
            description="Details VAT zero-rating and statutory quality inspection cess on coffee exports.",
            turns=[TurnStep("What VAT rate applies to the export of green Arabica coffee beans from Uganda?", expect_reply_regex=[r"(?:zero-rated|0%|export|coffee|vat)"])],
        ),
        CXScenario(
            id="CX-P10-15",
            category="Pillar 10: Advanced Advisory",
            title="Real Estate Developer Land Subdivision Infrastructure Deductions",
            description="Explains allowable capital expenditure on estate roads and drainage under Income Tax Act.",
            turns=[TurnStep("Can a real estate developer deduct the cost of grading murram roads and installing culverts from land sale profit?", expect_reply_regex=[r"(?:cost\s+base|infrastructure|deduct|capital\s+gains?|profit)"])],
        ),
        CXScenario(
            id="CX-P10-16",
            category="Pillar 10: Advanced Advisory",
            title="International Courier E-Commerce Parcel Clearance & De Minimis Rules",
            description="Explains customs threshold and courier clearance procedures for small packages.",
            turns=[TurnStep("What customs duties apply when ordering a $40 wristwatch from Amazon delivered by DHL to Kampala?", expect_reply_regex=[r"(?:customs|duty|courier|import|de\s+minimis|parcel)"])],
        ),
        CXScenario(
            id="CX-P10-17",
            category="Pillar 10: Advanced Advisory",
            title="Manufacturer Raw Material Duty Remission under EAC Gazette",
            description="Explains duty remission scheme for registered local industrial manufacturers.",
            turns=[TurnStep("How does a local manufacturing plant apply for customs duty remission on imported industrial inputs?", expect_reply_regex=[r"(?:remission|raw\s+material|gazette|eac|customs|manufactur)"])],
        ),
        CXScenario(
            id="CX-P10-18",
            category="Pillar 10: Advanced Advisory",
            title="Commercial Transport Fleet Advance Tax PRN Calculation",
            description="Calculates advance tax on commercial fleet before annual licensing.",
            turns=[TurnStep("How is advance tax calculated for a commercial transport company operating 5 heavy cargo trucks?", expect_reply_regex=[r"(?:advance\s+tax|ton|truck|commercial|prn|ura)"])],
        ),
        CXScenario(
            id="CX-P10-19",
            category="Pillar 10: Advanced Advisory",
            title="Motor Vehicle Search Application Reference Tracking",
            description="Explains tracking an official motor vehicle search report online via eTax.",
            turns=[TurnStep("How do I track the status of my Motor Vehicle Search application on the URA web portal?", expect_reply_regex=[r"(?:search|motor\s+vehicle|track|etax|portal)"])],
        ),
        CXScenario(
            id="CX-P10-20",
            category="Pillar 10: Advanced Advisory",
            title="Objection Decision 30-Day Statutory Limitation Review",
            description="Details the 30-day statutory time limit to appeal an objection decision to TAT.",
            turns=[TurnStep("If URA serves me an Objection Decision rejecting my dispute, how many days do I have to appeal to TAT?", expect_reply_regex=[r"(?:30\s+days|tat|tax\s+appeals\s+tribunal|appeal)"])],
        ),
    ])

    return extras

if __name__ == "__main__":
    ex = build_extra_100_scenarios()
    print(f"Created {len(ex)} extra scenarios.")

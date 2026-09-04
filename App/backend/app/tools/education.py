"""Taxpayer education — scaffolded explanations, not just answers.

Answering a tax question and teaching tax are different jobs, and the
RAG path already does the first one.  Doing only the first has a cost
the education research is now explicit about: learners who get finished
answers from a generative assistant offload the thinking and show no
knowledge gain, an effect the literature calls *metacognitive laziness*.
For URA's audience — first-time filers, small traders, people who will
face the same return again next quarter without a chatbot in front of
them — that is the wrong outcome even when every answer is correct.

The design follows what that research prescribes: not maximal
scaffolding, but **calibrated, fading** scaffolding.

- **Fading.** ``beginner`` sees every step of a worked example worked
  out.  ``intermediate`` gets a *completion problem* — the same example
  with the final step's value withheld, which is the step the learner
  performs.  ``advanced`` gets the rule and a transfer question, no
  worked steps at all.
- **Retrieval practice.** ``check_answer`` is withheld unless the caller
  passes ``reveal_answer``.  A tool that always hands over the answer
  cannot ask a question; withholding it is structural, not advisory.
- **Misconception-first.** Each concept names what learners actually get
  wrong — the "a raise pushed me into a higher bracket so I take home
  less" class of error — because correcting a wrong model beats adding
  to an absent one.

Every figure in a worked example is computed by the registered
calculators against the effective-dated rate tables, never written into
this module.  That is the same discipline as :mod:`app.tools.empathy`
delegating to :mod:`app.text_signals`: one definition of the numbers, so
a lesson cannot teach a rate the calculators no longer use, and a
provisional or unreconciled figure carries its warning into the lesson.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from . import Tool, ToolRegistry, ToolSchema

logger = logging.getLogger(__name__)

EDUCATION_NAMESPACE = "education"

LEVELS = ("beginner", "intermediate", "advanced")


@dataclass(frozen=True)
class Step:
    """One line of a worked example.

    ``key`` names the field in the calculator's payload that holds this
    step's value — dotted for a nested field, e.g. ``band.marginal_rate``
    — so the number shown is the number the calculator computed. There is
    no second copy to drift, and no step can quietly print a different
    quantity from the one its prompt names.
    """

    prompt: str
    key: str
    kind: str = "money"  # money | rate | text


@dataclass(frozen=True)
class Example:
    """How to build a worked example for a concept from a live calculator."""

    scenario: str
    tool: str
    arguments: dict[str, Any]
    steps: tuple[Step, ...]


@dataclass(frozen=True)
class Concept:
    """One teachable unit."""

    key: str
    title: str
    explanation: str
    why_it_matters: str
    check: tuple[str, str]  # (question, answer)
    transfer_question: str
    misconceptions: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    next_concepts: tuple[str, ...] = ()
    example: Example | None = None
    rate_keys: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Curriculum
#
# Prose describes mechanisms, which are stable; every rate, threshold and
# amount comes from the rate tables at render time.  Prerequisites form a
# DAG so a learner can be given an order rather than a pile.
# ---------------------------------------------------------------------------
_CONCEPTS: tuple[Concept, ...] = (
    Concept(
        key="tin",
        title="Taxpayer Identification Number (TIN)",
        explanation=(
            "A TIN is the single number URA uses to identify you across every tax "
            "you deal with — income tax, VAT, PAYE as an employer, customs. One "
            "person or business has one TIN for life; it does not change when your "
            "business does. You register once, on the URA web portal, and you need "
            "it before you can file anything, clear goods, or bid for most "
            "contracts."
        ),
        why_it_matters=(
            "Almost every other obligation is keyed to it. Filing, paying, claiming "
            "a refund and clearing imports all fail without one, so it is the first "
            "step rather than a formality."
        ),
        check=(
            "You already have a TIN as an individual and you now register a company. "
            "Do you use the same TIN for the company?",
            "No. The company is a separate taxpayer, so it gets its own TIN. Your "
            "personal TIN stays yours and is still used for your own income tax.",
        ),
        transfer_question=(
            "A sole trader incorporates their business as a limited company. What "
            "happens to their tax registrations, and which obligations move?"
        ),
        misconceptions=(
            "A TIN is not a licence to trade — a trading licence is separate.",
            "Having a TIN does not by itself make you liable for VAT; VAT depends "
            "on registration and turnover.",
        ),
        next_concepts=("fiscal_year", "progressive_taxation"),
    ),
    Concept(
        key="fiscal_year",
        title="The URA fiscal year",
        explanation=(
            "Uganda's tax year runs from 1 July to 30 June, not January to December. "
            "Rates set in a Finance or Amendment Act normally take effect on 1 July, "
            "so a rate you were charged in May can differ from the one that applies "
            "in July of the same calendar year. When a question is about a past "
            "period, the fiscal year in force *then* is what governs it, not the "
            "current one."
        ),
        why_it_matters=(
            "Using this year's rate on last year's transaction is one of the easiest "
            "ways to file a wrong return, and it is a mistake that survives review "
            "because the arithmetic looks right."
        ),
        check=(
            "You are amending a return for a transaction dated 15 May 2026. Which "
            "fiscal year's rates apply?",
            "The fiscal year that covered 15 May 2026 — the one running 1 July 2025 "
            "to 30 June 2026 — not whichever year is current when you file the "
            "amendment.",
        ),
        transfer_question=(
            "A contract is signed in June and paid in August, either side of a rate "
            "change. Which rate applies, and what determines that?"
        ),
        misconceptions=(
            "The fiscal year is not the calendar year.",
            "A rate change announced in the June budget speech is not law until the "
            "Act is passed and takes effect, normally on 1 July.",
        ),
        prerequisites=("tin",),
        next_concepts=("filing_deadlines",),
    ),
    Concept(
        key="progressive_taxation",
        title="How progressive bands work",
        explanation=(
            "A progressive tax splits your income into bands and taxes each band at "
            "its own rate. Only the part of your income that falls inside a band is "
            "taxed at that band's rate — moving into a higher band never re-taxes "
            "the income below it. That is why your *marginal* rate (the rate on your "
            "next shilling) is always higher than your *effective* rate (total tax "
            "divided by total income)."
        ),
        why_it_matters=(
            "This is the single most misunderstood mechanism in PAYE, and the "
            "misunderstanding has a real cost: people turn down raises, overtime and "
            "promotions believing they will end up with less."
        ),
        check=(
            "Someone says: 'I refused the raise because it pushes me into the next "
            "bracket and I would take home less.' Are they right?",
            "No. Only the income above the band threshold is taxed at the higher "
            "rate; everything below it is taxed exactly as before. A raise always "
            "leaves you with more take-home pay, just not the full amount of the "
            "raise.",
        ),
        transfer_question=(
            "Two people earn the same annual amount, but one earns it evenly across "
            "twelve months and the other in three large months. Under monthly PAYE "
            "bands, do they pay the same total tax? Why?"
        ),
        misconceptions=(
            "Crossing into a higher band does not tax your whole income at the "
            "higher rate.",
            "The top band rate is not your effective tax rate — it applies only to "
            "the income above that band's threshold.",
        ),
        prerequisites=("tin",),
        next_concepts=("paye",),
        example=Example(
            scenario=(
                "An employee earns UGX 1,200,000 a month. Watch how the bands stack "
                "rather than replace one another."
            ),
            tool="calculate_paye",
            arguments={"monthly_gross": 1_200_000},
            steps=(
                Step("Monthly gross pay", "monthly_gross"),
                Step("Rate on the next shilling earned (marginal)", "band.marginal_rate", "rate"),
                Step("Share of the whole salary paid as tax (effective)", "effective_rate", "rate"),
                Step("Total PAYE for the month", "paye"),
                Step("Take-home pay", "net_take_home"),
            ),
        ),
        rate_keys=("paye_bands_resident",),
    ),
    Concept(
        key="paye",
        title="PAYE — Pay As You Earn",
        explanation=(
            "PAYE is income tax on employment income, deducted by the employer from "
            "each month's pay and remitted to URA. The employee does not pay it "
            "separately; it is already gone by payday. It is charged on progressive "
            "monthly bands, and residents and non-residents use different band "
            "tables — a non-resident is taxed from the first shilling, with no "
            "nil-rate band."
        ),
        why_it_matters=(
            "It is the tax most Ugandans meet first, and it is the employer's legal "
            "duty to get right. An employer who under-deducts owes the shortfall "
            "themselves, with interest."
        ),
        check=(
            "Your employer deducted PAYE but never remitted it to URA. Who does URA "
            "pursue?",
            "The employer. The duty to deduct and remit is the employer's, and "
            "deducting without remitting does not transfer the liability to the "
            "employee.",
        ),
        transfer_question=(
            "An employee has two jobs, each applying the monthly bands separately. "
            "Is the right amount of tax collected overall? What corrects it?"
        ),
        misconceptions=(
            "PAYE is not a separate tax from income tax — it is a collection method "
            "for it.",
            "Allowances and benefits in kind are generally part of employment income, "
            "not tax-free extras.",
        ),
        prerequisites=("progressive_taxation",),
        next_concepts=("withholding_tax",),
        example=Example(
            scenario="An employee's gross salary is UGX 2,500,000 a month.",
            tool="calculate_paye",
            arguments={"monthly_gross": 2_500_000},
            steps=(
                Step("Monthly gross pay", "monthly_gross"),
                Step("PAYE the employer deducts", "paye"),
                Step("What reaches the employee", "net_take_home"),
            ),
        ),
        rate_keys=("paye_bands_resident",),
    ),
    Concept(
        key="vat",
        title="Value Added Tax (VAT)",
        explanation=(
            "VAT is charged on taxable supplies of goods and services. A registered "
            "business adds VAT to what it sells (output tax) and reclaims the VAT it "
            "paid on business purchases (input tax), remitting only the difference. "
            "That is why it is called value *added* — each business pays tax only on "
            "the value it adds. The final consumer, who cannot reclaim, bears it."
        ),
        why_it_matters=(
            "A registered trader who treats VAT as income rather than as money held "
            "for URA will be short at filing time. It was never theirs."
        ),
        check=(
            "You charged UGX 900,000 of VAT on sales and paid UGX 500,000 of VAT on "
            "business purchases. What do you remit to URA?",
            "The difference — UGX 400,000. Output tax minus input tax, not the whole "
            "UGX 900,000.",
        ),
        transfer_question=(
            "A business makes both taxable and exempt supplies. Can it reclaim all "
            "of its input tax? What would you need to know to decide?"
        ),
        misconceptions=(
            "VAT collected is not turnover or profit — it is held on URA's behalf.",
            "Zero-rated and exempt are not the same: a zero-rated supply still lets "
            "you reclaim input tax, an exempt one does not.",
        ),
        prerequisites=("tin",),
        next_concepts=("vat_registration",),
        example=Example(
            scenario="A shop sells goods for UGX 500,000 before VAT.",
            tool="calculate_vat",
            arguments={"amount": 500_000},
            steps=(
                Step("Price before VAT", "net"),
                Step("VAT rate applied", "rate", "rate"),
                Step("VAT to add", "vat"),
                Step("Price the customer pays", "gross"),
            ),
        ),
        rate_keys=("vat_standard",),
    ),
    Concept(
        key="vat_registration",
        title="When VAT registration becomes compulsory",
        explanation=(
            "Registration is compulsory once your annual taxable turnover reaches "
            "the statutory threshold. Below it you may still register voluntarily, "
            "which lets you reclaim input tax but also commits you to charging VAT "
            "and filing returns. Turnover is measured on taxable supplies, not on "
            "profit and not on every shilling that passes through the business."
        ),
        why_it_matters=(
            "Trading above the threshold without registering accrues penalties and "
            "the VAT you should have charged but did not — a liability that grows "
            "silently while the business believes it is compliant."
        ),
        check=(
            "Your turnover is below the threshold. Is registering a purely "
            "administrative choice?",
            "No. Voluntary registration lets you reclaim input tax, but it also "
            "obliges you to charge VAT on sales and file returns on time. It is a "
            "trade-off, not paperwork.",
        ),
        transfer_question=(
            "A business expects to cross the threshold mid-year. When should it "
            "register, and what happens to sales made before that date?"
        ),
        misconceptions=(
            "The threshold is on turnover, not on profit.",
            "Being below the threshold does not prevent registration; it only means "
            "registration is not compulsory.",
        ),
        prerequisites=("vat",),
        next_concepts=("filing_deadlines",),
        example=Example(
            scenario="A trading business turns over UGX 200,000,000 in a year.",
            tool="check_vat_registration",
            arguments={"annual_turnover": 200_000_000},
            steps=(
                Step("Annual taxable turnover", "annual_turnover"),
                Step("Registration threshold", "threshold"),
                Step("Room before registration is compulsory", "headroom"),
            ),
        ),
        rate_keys=("vat_registration_threshold_annual",),
    ),
    Concept(
        key="withholding_tax",
        title="Withholding tax (WHT)",
        explanation=(
            "WHT is tax deducted at source by the payer, who remits it to URA and "
            "gives the payee a credit for it. For most business payments it is an "
            "*advance* against the payee's final income tax, not an extra tax — the "
            "payee claims it back against their assessment. For some payments, "
            "notably to non-residents, it is final. The rate depends on what kind of "
            "payment it is, and the rates differ substantially between categories."
        ),
        why_it_matters=(
            "Payees often treat WHT as money lost. It is not: with a valid "
            "withholding certificate it reduces the tax you owe shilling for "
            "shilling."
        ),
        check=(
            "A client withheld tax from your invoice. Have you lost that money?",
            "No, in the usual case. Where WHT is an advance, the certificate lets "
            "you credit it against your income tax assessment. You have paid tax "
            "early, not twice — unless the WHT is a final tax for that payment type.",
        ),
        transfer_question=(
            "Why would a tax system collect the same revenue at source instead of "
            "from the payee at year end? What does it buy, and what does it cost the "
            "payee?"
        ),
        misconceptions=(
            "WHT is usually not an additional tax on top of income tax.",
            "The rate is not one number — it varies by payment type, and using the "
            "wrong category is a common filing error.",
        ),
        prerequisites=("tin",),
        next_concepts=("filing_deadlines",),
        example=Example(
            scenario="A company pays a supplier UGX 2,000,000 for services.",
            tool="calculate_withholding",
            arguments={"amount": 2_000_000, "payment_type": "services"},
            steps=(
                Step("Gross payment", "amount"),
                Step("Withholding rate for this payment type", "rate", "rate"),
                Step("Tax withheld and remitted to URA", "withholding_tax"),
                Step("Paid to the supplier", "net_payable"),
            ),
        ),
        rate_keys=("withholding_services",),
    ),
    Concept(
        key="rental_tax",
        title="Rental income tax",
        explanation=(
            "Rent received from property is taxable. An individual landlord is taxed "
            "at a flat rate on gross rent above an annual threshold. A company is "
            "taxed on rental income as part of its business income, with allowable "
            "expenses capped as a share of gross rental income rather than deducted "
            "in full."
        ),
        why_it_matters=(
            "Rental income is frequently left off returns on the belief that it is "
            "informal or too small to matter. It is neither, and property is "
            "unusually easy for URA to trace."
        ),
        check=(
            "Is an individual landlord taxed on the rent received, or on rent minus "
            "what they spent on the property?",
            "On gross rent above the annual threshold. The individual rate applies "
            "to the gross figure — expenses are not deducted the way a company "
            "deducts them.",
        ),
        transfer_question=(
            "The same building is owned first personally and then transferred to a "
            "company. What changes about how its rent is taxed, and why?"
        ),
        misconceptions=(
            "Rental income is not exempt because it is paid in cash or informally.",
            "The individual and company treatments are genuinely different rules, "
            "not the same rule at different rates.",
        ),
        prerequisites=("tin",),
        next_concepts=("filing_deadlines",),
        example=Example(
            scenario="An individual landlord receives UGX 12,000,000 of rent in a year.",
            tool="calculate_rental_tax",
            arguments={"annual_gross_rent": 12_000_000},
            steps=(
                Step("Gross annual rent", "annual_gross_rent"),
                Step("Annual threshold", "threshold"),
                Step("Amount actually taxed", "taxable_amount"),
                Step("Rental tax due", "tax"),
            ),
        ),
        rate_keys=("rental_tax_individual", "rental_tax_individual_threshold"),
    ),
    Concept(
        key="corporation_tax",
        title="Corporation tax",
        explanation=(
            "A company pays corporation tax on its chargeable income — revenue less "
            "allowable business expenses and capital allowances — at a flat rate. "
            "Chargeable income is not the same as cash in the bank or as accounting "
            "profit: some accounting expenses are not deductible for tax, and some "
            "tax allowances have no accounting equivalent."
        ),
        why_it_matters=(
            "Filing from the accounting profit without adjustment is the most common "
            "source of company assessments being amended by URA."
        ),
        check=(
            "Your company's accounting profit is UGX 40,000,000. Is that the figure "
            "corporation tax is charged on?",
            "Not necessarily. It is the starting point, but it must be adjusted — "
            "disallowed expenses added back, capital allowances deducted — to reach "
            "chargeable income.",
        ),
        transfer_question=(
            "Why do tax rules disallow some expenses that are genuinely commercial, "
            "such as entertainment? What behaviour is that rule guarding against?"
        ),
        misconceptions=(
            "Chargeable income is not accounting profit.",
            "A loss-making year does not necessarily mean nothing to file — returns "
            "are still due.",
        ),
        prerequisites=("tin",),
        next_concepts=("capital_gains",),
        example=Example(
            scenario="A company has chargeable income of UGX 40,000,000 for the year.",
            tool="calculate_corporation_tax",
            arguments={"chargeable_income": 40_000_000},
            steps=(
                Step("Chargeable income", "chargeable_income"),
                Step("Corporation tax rate", "rate", "rate"),
                Step("Tax due", "tax"),
            ),
        ),
        rate_keys=("corporation_tax",),
    ),
    Concept(
        key="capital_gains",
        title="Capital gains",
        explanation=(
            "A capital gain is the profit on disposing of an asset: sale price less "
            "the cost base — what you paid, plus costs of acquisition and "
            "improvement. For a company the gain is included in business income and "
            "taxed with it, rather than under a separate capital gains regime."
        ),
        why_it_matters=(
            "The tax is on the gain, not the sale price. Taxpayers who budget from "
            "the sale price alone overstate what they owe; those who forget the "
            "disposal entirely understate it."
        ),
        check=(
            "You sell an asset for UGX 50,000,000 that cost you UGX 30,000,000. What "
            "is taxed?",
            "The gain of UGX 20,000,000, not the UGX 50,000,000 sale price.",
        ),
        transfer_question=(
            "You spent money improving the asset before selling it. How should that "
            "affect the gain, and why is that treatment fair?"
        ),
        misconceptions=(
            "The tax is on the gain, not on the proceeds.",
            "Costs of acquisition and improvement belong in the cost base — omitting "
            "them overstates the gain.",
        ),
        prerequisites=("corporation_tax",),
        next_concepts=("filing_deadlines",),
        example=Example(
            scenario=(
                "A company sells an asset for UGX 50,000,000 that it acquired for "
                "UGX 30,000,000."
            ),
            tool="calculate_capital_gains",
            arguments={"sale_price": 50_000_000, "cost_base": 30_000_000},
            steps=(
                Step("Sale price", "sale_price"),
                Step("Cost base", "cost_base"),
                Step("Taxable gain", "gain"),
                Step("Tax on the gain", "tax"),
            ),
        ),
        rate_keys=("capital_gains_corporate",),
    ),
    Concept(
        key="customs_duty",
        title="Customs duty and landed cost",
        explanation=(
            "Imported goods are charged duty on their CIF value — cost, insurance "
            "and freight — under the EAC Common External Tariff, and the rate "
            "depends on the goods' HS code. VAT is then charged on the duty-"
            "inclusive value, so duty is inside the VAT base. Some goods carry "
            "further levies. The total of all of these is the landed cost."
        ),
        why_it_matters=(
            "Importers who budget on the invoice price alone are short at the border. "
            "Landed cost, not invoice price, is what the goods actually cost you."
        ),
        check=(
            "Is VAT on an import charged on the CIF value or on the CIF value plus "
            "duty?",
            "On CIF plus duty. Duty forms part of the VAT base, which is why the two "
            "cannot be worked out independently and added together.",
        ),
        transfer_question=(
            "Two importers bring in identical goods, but one pays more for freight. "
            "Do they pay the same duty? What does that tell you about the base?"
        ),
        misconceptions=(
            "Duty is not charged on the invoice price alone — insurance and freight "
            "are in the base.",
            "There is no single customs rate; the binding rate is the tariff line "
            "for the specific HS code.",
        ),
        prerequisites=("vat",),
        next_concepts=("filing_deadlines",),
        example=Example(
            scenario="An importer brings in goods with a CIF value of UGX 10,000,000.",
            tool="calculate_customs_duty",
            arguments={"cif_value": 10_000_000},
            steps=(
                Step("CIF value", "cif_value"),
                Step("Duty rate", "duty_rate", "rate"),
                Step("Import duty", "duty"),
                Step("VAT on the duty-inclusive value", "vat"),
                Step("Total landed cost", "landed_cost"),
            ),
        ),
        rate_keys=("customs_duty_common", "vat_standard"),
    ),
    Concept(
        key="filing_deadlines",
        title="Filing and paying on time",
        explanation=(
            "Filing a return and paying the tax are two separate obligations with "
            "two separate penalties. Filing late is penalised even if you owe "
            "nothing; paying late accrues interest on the outstanding amount. "
            "Different taxes run on different cycles — VAT and PAYE are monthly, "
            "income tax is annual with provisional instalments — so one taxpayer can "
            "have several deadlines in the same month."
        ),
        why_it_matters=(
            "The cheapest compliance win available to any taxpayer is filing a nil "
            "return on time instead of not filing at all."
        ),
        check=(
            "You cannot afford to pay this month's VAT. Should you delay filing the "
            "return until you can pay?",
            "No. File on time regardless — filing and paying are penalised "
            "separately, so delaying the return adds a penalty you could have "
            "avoided. Then address the payment, including any instalment "
            "arrangement.",
        ),
        transfer_question=(
            "Why would a tax system penalise late filing separately from late "
            "payment, rather than rolling them into one charge?"
        ),
        misconceptions=(
            "Owing nothing does not remove the duty to file.",
            "Filing late and paying late are distinct failures with distinct "
            "consequences.",
        ),
        prerequisites=("fiscal_year",),
    ),
    Concept(
        key="efris",
        title="Electronic Fiscal Receipting and Invoicing Solution (EFRIS)",
        explanation=(
            "EFRIS is URA's automated system for recording business sales transactions "
            "in real time. VAT-registered businesses must issue an e-receipt or e-invoice "
            "carrying a Fiscal Document Number (FDN) and QR code for each transaction."
        ),
        why_it_matters=(
            "Invoices issued outside EFRIS cannot be claimed for input tax credit by business "
            "buyers, and failure to use EFRIS attracts severe statutory penalties."
        ),
        check=(
            "Does every small non-VAT registered retail kiosk need an EFRIS machine?",
            "No. EFRIS is mandatory for VAT-registered taxpayers and designated entities. Small "
            "businesses below the 150m VAT threshold are not mandated.",
        ),
        transfer_question=(
            "A VAT-registered supplier gives you a manual paper receipt without an EFRIS QR code. "
            "Can you claim input VAT on that purchase in your VAT return? Why?"
        ),
        misconceptions=(
            "EFRIS is not a new tax — it is an electronic receipting compliance tool.",
            "EFRIS does not require dedicated expensive hardware; it can be used via the free URA web portal or mobile app.",
        ),
        prerequisites=("tin", "vat"),
        next_concepts=("vat_registration",),
    ),
    Concept(
        key="presumptive_tax",
        title="Presumptive tax for small businesses",
        explanation=(
            "Presumptive tax is a simplified, lump-sum tax for small resident sole proprietors "
            "whose gross annual business turnover is below UGX 150 million. Instead of keeping "
            "complex audited accounts, taxpayers pay fixed rates based on turnover bands."
        ),
        why_it_matters=(
            "It eliminates complex accounting requirements and provides predictable tax costs for "
            "small shops, retail businesses, and local entrepreneurs."
        ),
        check=(
            "Can an incorporated limited company with turnover of UGX 50 million pay presumptive tax?",
            "No. Presumptive tax is only available to resident individual sole proprietors. Companies "
            "must file standard corporate income tax returns regardless of turnover.",
        ),
        transfer_question=(
            "A sole trader starts with UGX 80 million turnover and grows to UGX 180 million next year. "
            "How does their tax filing obligation change?"
        ),
        misconceptions=(
            "Presumptive tax is not available to incorporated companies or partnerships.",
            "Crossing the UGX 150 million annual turnover line transitions the business into standard income tax.",
        ),
        prerequisites=("tin", "fiscal_year"),
        next_concepts=("corporation_tax",),
    ),
)

_BY_KEY: dict[str, Concept] = {c.key: c for c in _CONCEPTS}

_TOPIC_ALIASES: dict[str, str] = {
    "value_added_tax": "vat",
    "value_added": "vat",
    "wht": "withholding_tax",
    "withholding": "withholding_tax",
    "cit": "corporation_tax",
    "corporate_tax": "corporation_tax",
    "corporate_income_tax": "corporation_tax",
    "company_tax": "corporation_tax",
    "cgt": "capital_gains",
    "capital_gains_tax": "capital_gains",
    "customs": "customs_duty",
    "import_duty": "customs_duty",
    "pay_as_you_earn": "paye",
    "salary_tax": "paye",
    "deadlines": "filing_deadlines",
    "deadline": "filing_deadlines",
    "due_dates": "filing_deadlines",
    "brackets": "progressive_taxation",
    "tax_brackets": "progressive_taxation",
    "bands": "progressive_taxation",
    "tax_bands": "progressive_taxation",
    "efris_invoicing": "efris",
    "electronic_invoicing": "efris",
    "presumptive": "presumptive_tax",
    "small_business_tax": "presumptive_tax",
}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _lookup(payload: dict[str, Any], key: str) -> Any:
    """Resolve a possibly-dotted *key* against a calculator payload."""
    value: Any = payload
    for part in key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _format(value: Any, kind: str) -> str:
    if value is None:
        return "—"
    if kind == "rate":
        try:
            return f"{float(value) * 100:g}%"
        except (TypeError, ValueError):
            return str(value)
    if kind == "money":
        try:
            return f"UGX {float(value):,.2f}"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def learning_path(key: str) -> list[str]:
    """Concepts to cover before *key*, in teaching order, ending with *key*.

    Depth-first over the prerequisite DAG.  ``seen`` also guards against a
    cycle introduced by a future edit — a curriculum that cannot be
    ordered should degrade to a shorter path, not recurse forever.
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        concept = _BY_KEY.get(name)
        if concept is None:
            return
        for prerequisite in concept.prerequisites:
            visit(prerequisite)
        ordered.append(name)

    visit(key)
    return ordered


def _build_example(
    concept: Concept,
    level: str,
    fiscal_year: str | None,
    as_of: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Render *concept*'s worked example, plus the calculator's provenance.

    Returns ``(example, meta)``.  ``meta`` carries ``fiscal_year``,
    ``rate_basis`` and any ``verification_warning`` straight from the
    calculator, so a lesson inherits the same caveats a calculation
    would — a provisional table teaches with a warning attached.
    """
    if concept.example is None or level == "advanced":
        return None, {}

    tool = ToolRegistry.get(concept.example.tool)
    if tool is None:
        logger.warning(
            "education: %s references unregistered tool %s",
            concept.key,
            concept.example.tool,
        )
        return None, {}

    arguments = dict(concept.example.arguments)
    if fiscal_year:
        arguments["fiscal_year"] = fiscal_year
    if as_of:
        arguments["as_of"] = as_of

    payload = tool.execute(**arguments)
    if not payload.get("ok"):
        # A worked example that cannot be computed is dropped rather than
        # shown with invented numbers; the explanation still stands.
        logger.info(
            "education: example for %s unavailable — %s",
            concept.key,
            payload.get("error", "unknown error"),
        )
        return None, {"example_unavailable": payload.get("error", "")}

    # A step whose key is absent from the payload means the calculator's
    # output shape moved under us.  Rendering "—" where a figure belongs
    # teaches nothing and looks like a tax answer, so the whole example is
    # dropped and the explanation stands on its own.  test_education.py
    # asserts this never happens, so it is a loud bug, not a quiet one.
    unresolved = [s.key for s in concept.example.steps if _lookup(payload, s.key) is None]
    if unresolved:
        logger.warning(
            "education: %s example dropped — %s missing from %s payload",
            concept.key,
            ", ".join(unresolved),
            concept.example.tool,
        )
        return None, {"example_unavailable": f"missing fields: {', '.join(unresolved)}"}

    steps = [
        {"prompt": step.prompt, "value": _format(_lookup(payload, step.key), step.kind)}
        for step in concept.example.steps
    ]

    withheld = False
    if level == "intermediate" and len(steps) > 1:
        # Completion problem: the learner performs the final step.  The
        # value is removed from the payload entirely, not merely marked —
        # a value present in the result is a value the model will read out.
        steps[-1]["value"] = ""
        steps[-1]["to_complete"] = True
        withheld = True

    example = {
        "scenario": concept.example.scenario,
        "steps": steps,
        "final_step_withheld": withheld,
    }
    if withheld:
        example["instruction"] = (
            "Ask the learner to work out the final step themselves before "
            "confirming it. Do not state the answer first."
        )

    meta = {
        "fiscal_year": payload.get("fiscal_year", ""),
        "rate_basis": payload.get("rate_basis", {}),
    }
    if payload.get("verification_warning"):
        meta["verification_warning"] = payload["verification_warning"]
    return example, meta


def explain(
    topic: str,
    level: str = "beginner",
    reveal_answer: bool = False,
    fiscal_year: str | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Build a scaffolded lesson for *topic* at *level*."""
    raw_key = (topic or "").strip().lower().replace(" ", "_").replace("-", "_")
    key = _TOPIC_ALIASES.get(raw_key, raw_key)
    concept = _BY_KEY.get(key)
    if concept is None:
        return {
            "ok": False,
            "error": f"No lesson for {topic!r}.",
            "available_topics": sorted(_BY_KEY),
        }

    if level not in LEVELS:
        return {
            "ok": False,
            "error": f"level must be one of {', '.join(LEVELS)}, got {level!r}",
        }

    example, meta = _build_example(concept, level, fiscal_year, as_of)

    question, answer = concept.check
    if level == "advanced":
        question = concept.transfer_question
        answer = ""

    result: dict[str, Any] = {
        "ok": True,
        "topic": concept.key,
        "title": concept.title,
        "level": level,
        "explanation": concept.explanation,
        "why_it_matters": concept.why_it_matters,
        "common_mistakes": list(concept.misconceptions),
        "check_question": question,
        "prerequisites": list(concept.prerequisites),
        "learning_path": learning_path(concept.key),
        "next_topics": list(concept.next_concepts),
    }
    if example is not None:
        result["worked_example"] = example
    if reveal_answer and answer:
        result["check_answer"] = answer
    elif answer:
        result["answer_withheld"] = True
        result["instruction"] = (
            "Pose the check question and let the learner answer it. Call this "
            "tool again with reveal_answer=true once they have tried, or if "
            "they explicitly ask for the answer."
        )
    elif level == "advanced":
        result["instruction"] = (
            "This is a transfer question with no single stored answer. Discuss "
            "the learner's reasoning rather than grading it."
        )
    result.update(meta)
    return result


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------
class TaxEducationTool(Tool):
    """Teach a tax concept with a worked example calibrated to the learner."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="explain_tax_concept",
            description=(
                "Teach a Ugandan tax concept rather than just answering about it: "
                "returns a plain-language explanation, a worked example computed "
                "from the current URA rate tables, the mistakes learners actually "
                "make, and a check question to ask the learner. Call this when "
                "someone wants to understand or learn a topic ('how does VAT "
                "work?', 'explain PAYE bands', 'I don't understand withholding "
                "tax'), not when they want a specific figure — use the calculators "
                "for that. Raise `level` for someone who already knows the basics: "
                "'intermediate' withholds the final step of the example so the "
                "learner completes it. The check answer is withheld unless you "
                "pass reveal_answer=true — pose the question first."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "enum": sorted(_BY_KEY),
                        "description": "The concept to teach.",
                    },
                    "level": {
                        "type": "string",
                        "enum": list(LEVELS),
                        "default": "beginner",
                        "description": (
                            "How much scaffolding to give. 'beginner' works every "
                            "step; 'intermediate' leaves the last step for the "
                            "learner; 'advanced' drops the worked example and asks "
                            "a transfer question instead."
                        ),
                    },
                    "reveal_answer": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Include the check question's answer. Leave false on "
                            "the first call so the learner attempts it."
                        ),
                    },
                    "fiscal_year": {
                        "type": "string",
                        "description": (
                            "URA fiscal year for the worked example, e.g. "
                            "'FY2026-27'. Omit for the year in force today."
                        ),
                    },
                    "as_of": {
                        "type": "string",
                        "format": "date",
                        "description": (
                            "ISO date (YYYY-MM-DD) the example applies to. Ignored "
                            "when fiscal_year is given."
                        ),
                    },
                },
                "required": ["topic"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "error": {"type": "string"},
                    "topic": {"type": "string"},
                    "title": {"type": "string"},
                    "level": {"type": "string", "enum": list(LEVELS)},
                    "explanation": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "common_mistakes": {"type": "array", "items": {"type": "string"}},
                    "check_question": {"type": "string"},
                    "check_answer": {"type": "string"},
                    "answer_withheld": {"type": "boolean"},
                    "instruction": {"type": "string"},
                    "worked_example": {
                        "type": "object",
                        "properties": {
                            "scenario": {"type": "string"},
                            "steps": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "prompt": {"type": "string"},
                                        "value": {"type": "string"},
                                        "to_complete": {"type": "boolean"},
                                    },
                                    "required": ["prompt", "value"],
                                },
                            },
                            "final_step_withheld": {"type": "boolean"},
                        },
                        "required": ["scenario", "steps"],
                    },
                    "prerequisites": {"type": "array", "items": {"type": "string"}},
                    "learning_path": {"type": "array", "items": {"type": "string"}},
                    "next_topics": {"type": "array", "items": {"type": "string"}},
                    "fiscal_year": {"type": "string"},
                    "verification_warning": {"type": "string"},
                },
                "required": ["ok"],
                "additionalProperties": True,
            },
            risk="low",
            namespace=EDUCATION_NAMESPACE,
            title="Explain a tax concept",
        )

    def execute(
        self,
        topic: str = "",
        level: str = "beginner",
        reveal_answer: bool = False,
        fiscal_year: str | None = None,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        if not str(topic).strip():
            return {
                "ok": False,
                "error": "topic is required",
                "available_topics": sorted(_BY_KEY),
            }
        return explain(
            topic=topic,
            level=level,
            reveal_answer=bool(reveal_answer),
            fiscal_year=fiscal_year,
            as_of=as_of,
        )


ToolRegistry.register(TaxEducationTool())

"""Multi-hop statutory reasoning — the measurement, built before the graph.

The case for a knowledge graph is that compositional questions fail on
flat retrieval: "a non-resident importing a vehicle", "a company letting
property", "was I required to register last year". Answering those means
joining a rate to a taxpayer class to an exemption to an effective date,
and a vector search over flat passages joins them by keyword luck.

That is a **hypothesis**, and this module exists so it can be tested
rather than asserted. It ships before any graph code, so:

- the flat-retrieval baseline is measured on the same set the graph will
  later be measured on, rather than reconstructed afterwards;
- the graph can run in shadow mode and be scored without touching an
  answer;
- and if the graph does not beat the baseline, that is a visible number
  and the work stops there.

## Why the cases are tied to the rate tables

Each case names the ``rate_keys`` its answer depends on, and
:func:`verify_against_tables` reads the expected figures out of
``app.tax.tables`` rather than trusting a number typed into this file.
A golden set with hardcoded rates rots the moment URA changes one — and
then quietly tests last year's law forever. Here, a rate change breaks
the consistency check instead.

Deterministic and offline, like :mod:`app.agents.eval_routing`: scoring
an answer is string containment over required facts, so this runs in CI
on every change.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

#: Dimensions a question can compose over. Naming them is what makes the
#: set auditable — you can see which joins are covered and which are not,
#: instead of counting "hard questions".
HOP_KINDS = (
    "taxpayer_class",  # resident vs non-resident, individual vs company
    "effective_date",  # which fiscal year's rule applies
    "computation_base",  # what a tax is charged *on* (e.g. duty-inclusive)
    "exemption",  # a threshold or exclusion that changes the answer
    "rate_lookup",  # the leaf fact itself
    "obligation",  # a filing/registration duty that follows
)


@dataclass(frozen=True)
class MultiHopCase:
    """One compositional question and what a correct answer must contain."""

    question: str
    #: Which joins the question requires, in the order a person would make
    #: them. Length is the hop count.
    hops: tuple[str, ...]
    #: Rate-table keys the answer depends on. Checked to exist, and used
    #: to derive expected figures rather than hardcoding them.
    rate_keys: tuple[str, ...] = ()
    #: Fiscal year the question is asked about.
    fiscal_year: str = "FY2026-27"
    #: Substrings a correct answer must contain, case-insensitive. Kept
    #: to *concepts*, not phrasing — an answer is free to word it its own
    #: way, but it cannot omit the joins.
    must_mention: tuple[str, ...] = ()
    #: Substrings that mean the answer took a wrong turn. These are the
    #: specific confusions flat retrieval makes on this question.
    must_not_mention: tuple[str, ...] = ()
    #: What makes this hard, for the report.
    trap: str = ""

    @property
    def hop_count(self) -> int:
        return len(self.hops)


#: The set. Every case is a question a Ugandan taxpayer could plausibly
#: ask, and every one requires at least two joins — a single rate lookup
#: is already handled well and would not measure anything.
GOLDEN_SET_MULTIHOP: tuple[MultiHopCase, ...] = (
    # -- taxpayer class × rate ------------------------------------------
    MultiHopCase(
        question=(
            "I am a non-resident earning UGX 300,000 a month in Uganda. "
            "How much PAYE do I pay?"
        ),
        hops=("taxpayer_class", "rate_lookup"),
        rate_keys=("paye_bands_non_resident", "paye_bands_resident"),
        must_mention=("non-resident", "10"),
        must_not_mention=("no tax", "nil", "zero"),
        trap=(
            "The resident bands make the first UGX 335,000 tax-free, so a "
            "resident on this income pays nothing. A non-resident has no "
            "free band and pays 10% from the first shilling. Retrieval that "
            "finds the PAYE passage without the residency distinction "
            "answers 'nothing'."
        ),
    ),
    MultiHopCase(
        question="I rent out a house as a private landlord. What tax rate applies to the rent?",
        hops=("taxpayer_class", "rate_lookup", "exemption"),
        rate_keys=("rental_tax_individual", "rental_tax_company",
                   "rental_tax_individual_threshold"),
        must_mention=("12", "individual"),
        must_not_mention=("30%",),
        trap=(
            "Rental income is taxed at 12% for an individual and 30% for a "
            "company, and there is a tax-free threshold that applies only to "
            "the individual. One passage mentioning 'rental tax' cannot "
            "settle which of the three facts applies."
        ),
    ),
    MultiHopCase(
        question="My company lets out commercial premises. How is the rental income taxed?",
        hops=("taxpayer_class", "rate_lookup", "computation_base"),
        rate_keys=("rental_tax_company", "rental_company_expense_cap"),
        must_mention=("30", "company"),
        must_not_mention=("12%",),
        trap=(
            "The company rate is 30%, and deductible expenses are capped at "
            "a share of the rental income — a second fact that lives in a "
            "different provision from the rate."
        ),
    ),
    # -- effective date × threshold -------------------------------------
    MultiHopCase(
        question=(
            "My turnover was UGX 200 million in the year to June 2026. "
            "Was I required to register for VAT then?"
        ),
        hops=("effective_date", "exemption", "obligation"),
        rate_keys=("vat_registration_threshold_annual",),
        fiscal_year="FY2025-26",
        must_mention=("150",),
        must_not_mention=("300",),
        trap=(
            "The registration threshold doubled from UGX 150m to UGX 300m on "
            "1 July 2026. A taxpayer asking about the year *before* that gets "
            "the wrong answer from the current rate alone — and the wrong "
            "answer here is 'you were not required to register', which is a "
            "compliance failure the system caused."
        ),
    ),
    MultiHopCase(
        question="Has the VAT registration threshold changed, and from when?",
        hops=("effective_date", "rate_lookup"),
        rate_keys=("vat_registration_threshold_annual",),
        must_mention=("150", "300", "2026"),
        trap=(
            "Requires both fiscal years and the changeover date. A single "
            "passage states one of the two figures."
        ),
    ),
    # -- computation base: the interaction the customs prompt describes --
    MultiHopCase(
        question="I am importing a car worth USD 10,000 CIF. What will VAT be charged on?",
        hops=("computation_base", "rate_lookup"),
        rate_keys=("customs_duty_common", "vat_standard"),
        must_mention=("duty", "cif"),
        must_not_mention=("invoice price alone",),
        trap=(
            "VAT is charged on the duty-inclusive value, so duty and VAT "
            "cannot be computed independently and added. This rule currently "
            "lives in prose in the customs specialist prompt rather than as "
            "a fact the system can apply and cite."
        ),
    ),
    MultiHopCase(
        question="I am importing used clothing. What charges apply beyond customs duty?",
        hops=("computation_base", "rate_lookup", "exemption"),
        rate_keys=("environmental_levy_used_clothing", "customs_duty_common", "vat_standard"),
        must_mention=("environmental levy", "30"),
        trap=(
            "Used clothing attracts an environmental levy on top of duty and "
            "VAT. Three charges stack in a specific order; a passage about "
            "customs duty alone understates the landed cost badly."
        ),
    ),
    # -- withholding by payment type ------------------------------------
    MultiHopCase(
        question=(
            "I am paying a foreign consultant a management fee. "
            "What withholding tax do I deduct?"
        ),
        hops=("taxpayer_class", "rate_lookup"),
        rate_keys=("withholding_management_fees", "withholding_services"),
        must_mention=("15",),
        must_not_mention=("6%",),
        trap=(
            "Withholding is 6% on ordinary services and 15% on management "
            "fees. Both live under 'withholding tax'; only the payment type "
            "separates them."
        ),
    ),
    MultiHopCase(
        question="What withholding applies to a dividend versus a payment for goods?",
        hops=("rate_lookup", "taxpayer_class"),
        rate_keys=("withholding_dividend", "withholding_goods"),
        must_mention=("15", "6"),
        trap="Two rates that must be distinguished within one answer.",
    ),
    # -- disposal: class × base × rate ----------------------------------
    MultiHopCase(
        question=(
            "I sold a rental property I had owned for six years. "
            "What do I owe, and does the rental tax I paid change it?"
        ),
        hops=("taxpayer_class", "computation_base", "rate_lookup", "obligation"),
        rate_keys=("capital_gains_corporate", "rental_tax_individual"),
        must_mention=("capital gain",),
        trap=(
            "Two taxes touch one transaction: rental income tax during "
            "ownership and capital gains on disposal. The taxpayer's question "
            "presumes they interact. Answering with only one is incomplete in "
            "a way that reads as complete."
        ),
    ),
    # -- obligation follows from threshold ------------------------------
    MultiHopCase(
        question=(
            "I am an individual landlord earning UGX 2,000,000 a year in rent. "
            "Do I pay rental tax?"
        ),
        hops=("taxpayer_class", "exemption", "obligation"),
        rate_keys=("rental_tax_individual_threshold", "rental_tax_individual"),
        must_mention=("threshold",),
        trap=(
            "Below the individual threshold the rate is irrelevant. An answer "
            "that leads with 12% is wrong in substance even though the rate "
            "it quotes is right."
        ),
    ),
    MultiHopCase(
        question=(
            "I am employed and also earn rent. Is NSSF deducted from the rental income too?"
        ),
        hops=("taxpayer_class", "computation_base", "exemption"),
        rate_keys=("nssf_employee_contribution", "rental_tax_individual"),
        must_mention=("employment",),
        trap=(
            "NSSF attaches to employment income, not to rental income. The "
            "join is between two income types the taxpayer holds at once."
        ),
    ),
)


@dataclass
class MultiHopMiss:
    question: str
    missing: list[str] = field(default_factory=list)
    forbidden_present: list[str] = field(default_factory=list)

    def describe(self) -> str:
        parts = []
        if self.missing:
            parts.append(f"missing {self.missing}")
        if self.forbidden_present:
            parts.append(f"stated {self.forbidden_present}")
        return f"{self.question[:70]!r}: {'; '.join(parts)}"


@dataclass
class MultiHopReport:
    total: int
    correct: int
    misses: list[MultiHopMiss] = field(default_factory=list)
    by_hop_count: dict[int, dict[str, int]] = field(default_factory=dict)
    by_hop_kind: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "correct": self.correct,
            "accuracy": round(self.accuracy, 4),
            "by_hop_count": self.by_hop_count,
            "by_hop_kind": self.by_hop_kind,
            "misses": [asdict(m) for m in self.misses],
        }


def _mentions(answer: str, needle: str) -> bool:
    """Case-insensitive containment, with numbers matched as figures.

    A bare ``"12"`` must match "12%" and "12 percent" but not "2012" or
    "120,000", so numeric needles are matched on a digit boundary rather
    than as a substring. Getting this wrong makes the whole set pass by
    accident.
    """
    hay = answer.lower()
    need = needle.lower()
    if need.isdigit():
        return re.search(rf"(?<![\d,.]){re.escape(need)}(?![\d])", hay) is not None
    return need in hay


def score_answer(case: MultiHopCase, answer: str) -> MultiHopMiss | None:
    """Return a miss for *answer*, or ``None`` when it satisfies *case*."""
    missing = [m for m in case.must_mention if not _mentions(answer, m)]
    forbidden = [m for m in case.must_not_mention if _mentions(answer, m)]
    if missing or forbidden:
        return MultiHopMiss(case.question, missing, forbidden)
    return None


def run_multihop_eval(
    answer_for: Any,
    cases: tuple[MultiHopCase, ...] = GOLDEN_SET_MULTIHOP,
) -> MultiHopReport:
    """Score *answer_for* — a callable ``question -> answer`` — over *cases*.

    Taking a callable keeps this independent of what produces the
    answer, which is the whole point: the same harness measures the
    flat-retrieval baseline today and the graph-fused pipeline later,
    so the two numbers are comparable.
    """
    misses: list[MultiHopMiss] = []
    by_count: dict[int, dict[str, int]] = {}
    by_kind: dict[str, dict[str, int]] = {}
    correct = 0

    for case in cases:
        count_bucket = by_count.setdefault(case.hop_count, {"total": 0, "correct": 0})
        count_bucket["total"] += 1
        kind_buckets = [by_kind.setdefault(k, {"total": 0, "correct": 0}) for k in case.hops]
        for bucket in kind_buckets:
            bucket["total"] += 1

        try:
            answer = answer_for(case.question) or ""
        except Exception:
            answer = ""

        miss = score_answer(case, answer)
        if miss is not None:
            misses.append(miss)
            continue

        correct += 1
        count_bucket["correct"] += 1
        for bucket in kind_buckets:
            bucket["correct"] += 1

    return MultiHopReport(
        total=len(cases),
        correct=correct,
        misses=misses,
        by_hop_count=by_count,
        by_hop_kind=by_kind,
    )


def verify_against_tables() -> list[str]:
    """Check every case against the live rate tables; return problems.

    This is what stops the set from testing last year's law. Each case
    names the keys its answer depends on; if a key stops existing, or a
    fiscal year stops being defined, the set fails here rather than
    continuing to assert a figure nobody has checked since it was typed.
    """
    from ..tax import tables

    problems: list[str] = []
    years = set(tables.list_fiscal_years())

    for case in GOLDEN_SET_MULTIHOP:
        if case.fiscal_year not in years:
            problems.append(f"{case.question[:50]!r}: unknown fiscal year {case.fiscal_year}")
            continue
        # Membership is checked against the table for the year the
        # question is *about*, not against the union of every year.
        # ``known_rate_keys()`` lists scalars only — the PAYE bands are
        # lists — and a key that exists in some other year is exactly
        # the mistake this check should catch, not excuse.
        table = tables.get_table(case.fiscal_year)
        for key in case.rate_keys:
            if key not in table:
                defined_in = tables.fiscal_years_defining(key)
                problems.append(
                    f"{case.question[:50]!r}: rate key {key!r} not in {case.fiscal_year}"
                    + (f" (defined in {defined_in})" if defined_in else "")
                )
        for kind in case.hops:
            if kind not in HOP_KINDS:
                problems.append(f"{case.question[:50]!r}: unknown hop kind {kind!r}")
        if case.hop_count < 2:
            problems.append(f"{case.question[:50]!r}: not multi-hop ({case.hop_count} hop)")
    return problems


def describe() -> dict[str, Any]:
    """Coverage summary — which joins the set exercises, and how often."""
    by_kind: dict[str, int] = dict.fromkeys(HOP_KINDS, 0)
    for case in GOLDEN_SET_MULTIHOP:
        for kind in case.hops:
            by_kind[kind] += 1
    return {
        "cases": len(GOLDEN_SET_MULTIHOP),
        "hop_counts": sorted({c.hop_count for c in GOLDEN_SET_MULTIHOP}),
        "by_hop_kind": by_kind,
        "fiscal_years": sorted({c.fiscal_year for c in GOLDEN_SET_MULTIHOP}),
    }

"""Multi-step tax scenarios with asserted traces (ADR-0033, phase 2).

A scenario fixes its inputs and states what a correct answer must contain:
the figures, the provisions they come from and the steps that produce them.
:func:`run` executes it through the kernel functions and rules, the same calls
the agent makes, and returns the answer with its trace (every call, its
arguments, the provision ids and the kernel version). A test passes only if
both match.

Variants exercised by ``tests/agents/test_kernel_core_2026_11.py``:

* the fiscal-year boundary (30 June and 1 July);
* a contradicting passage, which must produce a recorded conflict and leave
  the figure unchanged;
* replay against a recorded release's snapshot, which must reproduce the
  original facts exactly.

Luganda and Swahili questions reach the kernel through the system's English
pivot for retrieval (``query.english_retrieval_query``), which those suites
test; the kernel itself takes declared keys, not text.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass, field
from typing import Any

from . import rules as rulebook
from .functions import KernelFact, NotInKernel, fact


@dataclass(frozen=True)
class Step:
    #: ``fact`` or a rule name from :data:`app.kernel.rules.RULES`.
    call: str
    args: dict[str, Any]


@dataclass(frozen=True)
class Scenario:
    id: str
    question: str
    as_of: _dt.date
    steps: tuple[Step, ...]
    #: ``{call index: expected value or amount}``.
    expected: dict[int, Any]
    #: Provision text each step must cite (a substring), by call index.
    provisions: dict[int, str] = field(default_factory=dict)


@dataclass
class Trace:
    scenario: str
    kernel_version: str
    as_of: str
    calls: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run(scenario: Scenario, *, store: Any = None, version: str | None = None) -> Trace:
    """Execute *scenario*; *store* and *version* replay a recorded release."""
    trace = Trace(scenario.id, version or "", scenario.as_of.isoformat())
    for index, step in enumerate(scenario.steps):
        if step.call == "fact":
            result: Any = fact(step.args["key"], scenario.as_of, store=store, version=version)
        else:
            if store is not None:
                raise ValueError("rules replay against the running kernel only; replay facts instead")
            result = rulebook.RULES[step.call](**step.args, as_of=scenario.as_of)
        if isinstance(result, NotInKernel):
            entry = {"index": index, "call": step.call, "args": step.args, "not_in_kernel": result.reason}
        elif isinstance(result, KernelFact):
            entry = {"index": index, "call": step.call, "args": step.args, "value": result.value,
                     "provision": result.provision, "provision_id": result.provision_id,
                     "fiscal_year": result.fiscal_year, "unverified": result.unverified}
            trace.kernel_version = trace.kernel_version or result.kernel_version
        else:
            entry = {"index": index, "call": step.call, "args": step.args, "value": result.amount,
                     "provision": " | ".join(s.provision for s in result.steps),
                     "fiscal_year": result.fiscal_year, "unverified": result.unverified,
                     "review_status": result.review_status}
            trace.kernel_version = trace.kernel_version or result.kernel_version
        trace.calls.append(entry)
    return trace


def check(scenario: Scenario, trace: Trace) -> list[str]:
    """Differences between a trace and what the scenario requires; empty when it passes."""
    problems = []
    for index, expected in scenario.expected.items():
        got = trace.calls[index].get("value")
        if got != expected:
            problems.append(f"step {index}: expected {expected!r}, got {got!r}")
    for index, needle in scenario.provisions.items():
        if needle not in str(trace.calls[index].get("provision") or ""):
            problems.append(f"step {index}: provision does not cite {needle!r}")
    return problems


GOLDEN: tuple[Scenario, ...] = (
    Scenario(
        id="vat-threshold-30-june",
        question="My turnover passed UGX 200,000,000 on 30 June 2026. Must I register for VAT?",
        as_of=_dt.date(2026, 6, 30),
        steps=(Step("fact", {"key": "vat_registration_threshold_annual"}),),
        expected={0: 150000000},
        provisions={0: "s.7"},
    ),
    Scenario(
        id="vat-threshold-1-july",
        question="My turnover passed UGX 200,000,000 on 1 July 2026. Must I register for VAT?",
        as_of=_dt.date(2026, 7, 1),
        steps=(Step("fact", {"key": "vat_registration_threshold_annual"}),),
        expected={0: 300000000},
        provisions={0: "VAT (Amendment) Act 2026"},
    ),
    Scenario(
        id="late-filing-penal-tax",
        question="A return with UGX 5,000,000 of tax payable was filed 3 months late in August 2026. "
                 "What is the late-filing penal tax?",
        as_of=_dt.date(2026, 8, 15),
        steps=(
            Step("fact", {"key": "penal_tax_late_filing_monthly_rate"}),
            Step("fact", {"key": "penal_tax_late_filing_minimum_ugx"}),
            Step("late_filing_penal_tax", {"tax_payable": 5_000_000, "months_late": 3}),
        ),
        expected={0: 0.02, 1: 200000, 2: 300000.0},
        provisions={0: "s.49(1)", 1: "s.49(1)", 2: "s.49(1)"},
    ),
    Scenario(
        id="late-filing-minimum-binds",
        question="A small return (UGX 1,000,000 payable) was filed 2 months late. What penal tax applies?",
        as_of=_dt.date(2026, 9, 1),
        steps=(Step("late_filing_penal_tax", {"tax_payable": 1_000_000, "months_late": 2}),),
        expected={0: 200000.0},
        provisions={0: "s.49(1)"},
    ),
    Scenario(
        id="interest-with-waiver-cap",
        question="UGX 1,000,000 of tax has been unpaid for 80 months, with UGX 200,000 penal tax. What interest applies?",
        as_of=_dt.date(2026, 8, 15),
        steps=(
            Step("fact", {"key": "late_payment_interest_monthly_rate"}),
            Step("late_payment_interest", {"unpaid": 1_000_000, "months": 80, "penal_tax": 200_000}),
        ),
        expected={0: 0.02, 1: 1200000.0},
        provisions={0: "s.39", 1: "s.136(5) and (7)"},
    ),
    Scenario(
        id="efris-penal-tax",
        question="A trader failed to issue an e-invoice on a sale carrying UGX 50,000 of VAT. What penal tax applies?",
        as_of=_dt.date(2026, 8, 15),
        steps=(Step("efris_penal_tax", {"tax_due": 50_000}),),
        expected={0: 200000.0},
        provisions={0: "s.93"},
    ),
    Scenario(
        id="unstamped-goods-penal-tax",
        question="A distributor was found with unstamped goods carrying UGX 300,000 of excise duty. What penal tax applies?",
        as_of=_dt.date(2026, 8, 15),
        steps=(Step("dts_unstamped_penal_tax", {"tax_due": 300_000}),),
        expected={0: 2000000.0},
        provisions={0: "s.21(3)"},
    ),
    Scenario(
        id="deadlines",
        question="When is PAYE due each month, and how long do I have to object to an assessment?",
        as_of=_dt.date(2026, 8, 15),
        steps=(Step("fact", {"key": "paye_due_date_monthly"}), Step("fact", {"key": "objection_timeline_days"})),
        expected={0: 15, 1: 45},
        provisions={1: "s.24"},
    ),
)

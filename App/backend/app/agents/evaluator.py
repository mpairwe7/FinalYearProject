"""Evaluator-optimizer — verify an answer before a taxpayer reads it.

The system already regenerates once when faithfulness falls below a
floor. That is a single self-judged pass, and a model grading its own
output reliably catches formatting faults while missing reasoning
faults. Two asymmetries fix that.

**A different judge.**  The draft comes from the agentic tier; the
non-deterministic axes are judged from a higher one. Self-evaluation at
the same capability is close to free and close to worthless.

**Determinism wherever the question allows it.**  Whether a figure is
right is not a judgement call. If the taxpayer asked something the
calculators can answer, :func:`verify_money` re-derives the figure
through the same MCP path the agent used and compares it against the
numbers actually printed in the draft. A mismatch is a hard reject with
no model in the loop at all — no tokens, no latency, no opinion.

That ordering is the point: the cheap, certain check runs first and can
reject on its own. The expensive, uncertain one only runs on what
survives.

## What this deliberately does not do

It does not loop. :class:`RevisionBudget` permits **one** revision, on
turns that carry money or are bound for a human. An unbounded
critique-revise cycle is a cost incident with a quality story attached,
and the agent loop next door already learned that lesson —
:class:`app.agents.loop_control.ToolCallBudget` exists because an
iteration cap is not a budget.

Feature flag: ``FLAG_EVALUATOR_OPTIMIZER``, default off.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..calculator_router import plan_calculation
from ..entailment import canonical_amounts, percentages

logger = logging.getLogger(__name__)

#: Citation markers, stripped before any figure is read out of an answer.
#:
#: Every grounded answer ends its sentences with ``[1]``, ``[2]`` … and
#: those parse as the amounts 1 and 2. Left in, an answer that states no
#: figure at all looks like one that states several, so "the model gave
#: a calculation question a figure-free reply" — a real and common
#: failure — would be reported as a *mismatch* instead of as a missing
#: answer, with the wrong revision instruction attached.
_CITATION_MARKER_RE = re.compile(r"\[\d{1,3}\]")

#: Revisions allowed per turn.  One.  A second pass on an answer the
#: first pass could not fix is far more likely to be a prompt problem
#: than something another round would solve.
DEFAULT_MAX_REVISIONS = 1

#: Relative tolerance when comparing a printed figure against the
#: recomputed one.  Answers round — "about UGX 90,000" for 90,000.00 is
#: correct prose — so an exact-equality check would reject good answers.
#: Wide enough for presentation rounding, far too tight to hide a wrong
#: rate or a missing band.
MONEY_TOLERANCE = 0.01

#: Below this, a recomputed figure is not worth matching against the
#: draft: single digits collide with years, section numbers and list
#: indices, so "matching" them proves nothing.
MIN_VERIFIABLE_AMOUNT = 1000.0


@dataclass(frozen=True)
class Verdict:
    """Outcome of one evaluation pass.

    Booleans, not scores. A score invites a threshold, a threshold
    invites tuning, and "0.62 grounded" is not a thing a taxpayer-facing
    system should act on. Each field is a question with an answer.

    ``unverified`` is the honest third state: the checks that could not
    run, kept distinct from the checks that ran and passed. An answer
    nothing could verify must not look identical to a verified one.
    """

    grounded: bool = True
    numerically_consistent: bool = True
    cites_effective_year: bool = True
    tone_appropriate: bool = True
    actionable: bool = True
    revision_note: str = ""
    unverified: tuple[str, ...] = ()
    #: Diagnostics for the audit trail — never shown to the taxpayer.
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return (
            self.grounded
            and self.numerically_consistent
            and self.cites_effective_year
            and self.tone_appropriate
            and self.actionable
        )

    def failures(self) -> list[str]:
        """Names of the checks that rejected, worst first."""
        checks = (
            ("numerically_consistent", self.numerically_consistent),
            ("grounded", self.grounded),
            ("cites_effective_year", self.cites_effective_year),
            ("actionable", self.actionable),
            ("tone_appropriate", self.tone_appropriate),
        )
        return [name for name, ok in checks if not ok]


@dataclass
class RevisionBudget:
    """How many revisions a turn may spend, and on what.

    Shaped after :class:`app.agents.loop_control.ToolCallBudget`: the
    ceilings are constructor arguments, the module defaults are what the
    live path uses, and the budget refuses rather than raises.
    """

    max_revisions: int = DEFAULT_MAX_REVISIONS
    revisions_used: int = 0

    def may_revise(self, *, carries_money: bool, escalation_bound: bool) -> tuple[bool, str]:
        """Whether another revision is allowed, and why not when it is not.

        Revision is spent only where a wrong answer is expensive: a
        figure the taxpayer may act on, or an answer a URA officer will
        read. Rewriting a greeting for tone is not worth a second
        generation at a higher tier.
        """
        if self.revisions_used >= self.max_revisions:
            return False, f"revision budget spent ({self.max_revisions})"
        if not (carries_money or escalation_bound):
            return False, "not money-bearing or escalation-bound"
        return True, "revision allowed"

    def spend(self) -> None:
        self.revisions_used += 1


def _relative_match(expected: float, candidates: set[float]) -> bool:
    """True if any candidate is within :data:`MONEY_TOLERANCE` of *expected*."""
    if expected == 0:
        return 0.0 in candidates
    return any(abs(c - expected) / abs(expected) <= MONEY_TOLERANCE for c in candidates)


def _numeric_fields(result: dict[str, Any]) -> list[tuple[str, float]]:
    """Money-sized numbers a calculator result asserts, largest first.

    Largest first because the headline figure — the tax due, the landed
    cost — is the one a taxpayer acts on and the one an answer is most
    likely to state.
    """
    found: list[tuple[str, float]] = []
    for key, value in result.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if abs(float(value)) >= MIN_VERIFIABLE_AMOUNT:
            found.append((key, float(value)))
    found.sort(key=lambda kv: -abs(kv[1]))
    return found


def verify_money(query: str, answer: str, call_tool: Any) -> tuple[bool | None, dict[str, Any]]:
    """Recompute the query's figure and check the answer against it.

    Returns ``(None, …)`` when the question is not one the calculators
    can settle — no calculation intent, or arguments still missing. That
    is *unverified*, not *passed*, and the caller must keep the two
    apart.

    *call_tool* is injected rather than imported so this is testable
    without an MCP client, and so the caller controls the security
    context the recomputation runs under.
    """
    plan = plan_calculation(query)
    if plan is None:
        return None, {"skipped": "no calculation intent"}
    if plan.missing:
        return None, {"skipped": f"missing inputs: {', '.join(plan.missing)}"}

    try:
        result = call_tool(plan.tool, dict(plan.params))
    except Exception as exc:  # a verifier must never break the turn
        logger.warning("verify_money: recomputation failed: %s", exc)
        return None, {"skipped": f"recomputation failed: {exc}"}

    if not isinstance(result, dict) or result.get("ok") is False:
        return None, {"skipped": "calculator returned no result"}

    expected = _numeric_fields(result)
    if not expected:
        return None, {"skipped": "no verifiable figure in the result"}

    stated = canonical_amounts(_CITATION_MARKER_RE.sub(" ", answer))
    if not stated:
        # The answer to a calculation question printed no figure at all.
        # Not a mismatch — a non-answer — and the caller should see it.
        return False, {"tool": plan.tool, "reason": "answer states no figure",
                       "expected": dict(expected[:3])}

    for key, value in expected:
        if _relative_match(value, stated):
            return True, {"tool": plan.tool, "matched_field": key, "matched_value": value}

    return False, {
        "tool": plan.tool,
        "reason": "no stated figure matches the recomputation",
        "expected": dict(expected[:3]),
        "stated": sorted(stated)[:5],
    }


def verify_rate_currency(answer: str, effective_year: str) -> bool | None:
    """Whether an answer quoting a rate says which year it applies to.

    Rates change on 1 July. A percentage with no fiscal year attached is
    a figure a taxpayer cannot check and may apply to the wrong period.
    Returns ``None`` when the answer quotes no rate at all.
    """
    if not percentages(answer):
        return None
    if not effective_year:
        return True
    haystack = answer.lower()
    # "FY2026-27" should be satisfied by "2026", "2026-27" or the full form.
    tokens = {effective_year.lower(), effective_year.lower().replace("fy", "")}
    tokens |= {part for part in effective_year.replace("FY", "").split("-") if len(part) == 4}
    return any(token and token in haystack for token in tokens)


def evaluate(
    query: str,
    answer: str,
    *,
    call_tool: Any = None,
    effective_year: str = "",
    faithfulness: float | None = None,
    faithfulness_floor: float = 0.50,
) -> Verdict:
    """Run the deterministic checks over a draft answer.

    Only the checks that can be settled without a model live here. The
    model-judged axes — tone, actionability — are the caller's to add
    from the evaluator tier, and default to passing so that an
    unavailable judge cannot block an otherwise sound answer.
    """
    unverified: list[str] = []
    detail: dict[str, Any] = {}

    numerically_consistent = True
    if call_tool is not None:
        verdict, money_detail = verify_money(query, answer, call_tool)
        detail["money"] = money_detail
        if verdict is None:
            unverified.append("numerically_consistent")
        else:
            numerically_consistent = verdict
    else:
        unverified.append("numerically_consistent")

    cites_year = verify_rate_currency(answer, effective_year)
    if cites_year is None:
        unverified.append("cites_effective_year")
        cites_year = True

    grounded = True
    if faithfulness is not None:
        grounded = faithfulness >= faithfulness_floor
        detail["faithfulness"] = faithfulness
    else:
        unverified.append("grounded")

    verdict = Verdict(
        grounded=grounded,
        numerically_consistent=numerically_consistent,
        cites_effective_year=cites_year,
        revision_note=_note(numerically_consistent, cites_year, grounded, detail),
        unverified=tuple(unverified),
        detail=detail,
    )
    if not verdict.accepted:
        logger.info("evaluator: rejected — %s", ", ".join(verdict.failures()))
    return verdict


def _note(numeric_ok: bool, year_ok: bool, grounded: bool, detail: dict[str, Any]) -> str:
    """Instruction for the revision pass, or empty when nothing failed.

    Phrased as what to *do*, not as what went wrong: a critique the
    reviser has to interpret is a second chance to get it wrong.
    """
    notes: list[str] = []
    if not numeric_ok:
        money = detail.get("money", {})
        expected = money.get("expected", {})
        if expected:
            figure = next(iter(expected.items()))
            notes.append(
                f"State the calculated figure. {money.get('tool', 'The calculator')} "
                f"returned {figure[0]}={figure[1]:,.0f}; use that number, not one of your own."
            )
        else:
            notes.append("Call the calculator and state the figure it returns.")
    if not year_ok:
        notes.append("Name the fiscal year the rate applies to; rates change on 1 July.")
    if not grounded:
        notes.append("Remove any claim not supported by a cited passage, or abstain.")
    return " ".join(notes)

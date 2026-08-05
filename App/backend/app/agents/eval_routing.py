"""Routing eval — measure the supervisor instead of spot-checking it.

The unit tests assert individual cases: this query goes to that route.
They cannot tell you the supervisor gets 70% of natural calculation
phrasings wrong, because a case nobody wrote a test for is invisible.

That gap was not hypothetical. Seven of ten ordinary ways of asking for
a tax figure routed to plain retrieval, where the model answered a
numeric question from memory. It was found by hand-probing the router,
which is not a thing anyone will remember to redo.

So this reports a *rate* and names what it got wrong, over a labelled
set. A regression shows up as a number moving, and a new blind spot
shows up as a miss listed by name rather than as silence.

Deterministic and offline — the supervisor is pure Python — so it runs
in CI on every change rather than nightly against sampled traffic like
:mod:`app.evaluation`.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .state import AgentRoute

#: (query, expected route, tool that must be offered or "").
#:
#: Phrasings are the ones taxpayers actually use, including the ones
#: that used to miss. Add a case here when a route is reported wrong;
#: that is what turns a one-off bug report into a permanent check.
GOLDEN_SET: list[tuple[str, AgentRoute, str]] = [
    # -- calculation, explicit verb --------------------------------
    ("How much VAT on UGX 50,000?", AgentRoute.TOOLS, "calculate_vat"),
    ("Calculate PAYE for 500k", AgentRoute.TOOLS, "calculate_paye"),
    ("How much corporation tax on 500M?", AgentRoute.TOOLS, "calculate_corporation_tax"),
    ("work out the withholding on 2 million for services", AgentRoute.TOOLS,
     "calculate_withholding"),
    # -- calculation, amount-first (the shape that used to miss) ----
    ("What's my take-home pay on a 2M salary?", AgentRoute.TOOLS, "calculate_paye"),
    ("VAT on 500000", AgentRoute.TOOLS, "calculate_vat"),
    ("net pay for 1.5m salary", AgentRoute.TOOLS, "calculate_paye"),
    ("what will I owe on 5m rental income", AgentRoute.TOOLS, "calculate_rental_tax"),
    ("capital gains on 12m", AgentRoute.TOOLS, "calculate_capital_gains"),
    # -- rate lookup, not a calculation ----------------------------
    ("What is the current corporation tax rate?", AgentRoute.TOOLS, "lookup_rate"),
    ("What's the applicable VAT rate?", AgentRoute.TOOLS, "lookup_rate"),
    ("List all tax rates", AgentRoute.TOOLS, "list_available_rates"),
    # -- learning, not a calculation -------------------------------
    ("What is VAT?", AgentRoute.TOOLS, "explain_tax_concept"),
    ("How does PAYE work?", AgentRoute.TOOLS, "explain_tax_concept"),
    ("Explain withholding tax for services", AgentRoute.TOOLS, "explain_tax_concept"),
    # -- temporal --------------------------------------------------
    ("What is today's date?", AgentRoute.TOOLS, "get_current_date"),
    # "my next filing" does not match the account-specific escalation
    # guard (`my filing`) because the words are not adjacent, so this is
    # a calendar question. "my return" and "my account" do match and
    # escalate — the two cases below pin that boundary.
    ("When is my next filing deadline?", AgentRoute.TOOLS, "get_next_deadlines"),
    ("When is the VAT filing deadline?", AgentRoute.TOOLS, "get_next_deadlines"),
    ("what is on my return", AgentRoute.ESCALATE, ""),
    ("show me my account balance", AgentRoute.ESCALATE, ""),
    # -- customs ---------------------------------------------------
    ("What documents do I need at port of entry?", AgentRoute.CUSTOMS_SPECIALIST, ""),
    ("Bill of lading requirements", AgentRoute.CUSTOMS_SPECIALIST, ""),
    ("Import clearance process for vehicles", AgentRoute.CUSTOMS_SPECIALIST, ""),
    # -- escalation must win over everything -----------------------
    ("I want to speak to a human", AgentRoute.ESCALATE, ""),
    ("I want to dispute my assessment", AgentRoute.ESCALATE, ""),
    ("I want to dispute my 5m assessment", AgentRoute.ESCALATE, ""),
    ("Calculate my VAT and I want to speak to a human", AgentRoute.ESCALATE, ""),
    ("my TIN is not working", AgentRoute.ESCALATE, ""),
    # -- greeting / clarify ----------------------------------------
    ("hello", AgentRoute.GREET, ""),
    ("good morning", AgentRoute.GREET, ""),
    ("help", AgentRoute.CLARIFY, ""),
    # -- factual, belongs on retrieval -----------------------------
    ("How do I register a business with URA?", AgentRoute.RAG, ""),
    ("What is EFRIS?", AgentRoute.RAG, ""),
    ("Who qualifies for a tax exemption?", AgentRoute.RAG, ""),
    ("how is PAYE calculated", AgentRoute.RAG, ""),
    # A figure with no tax type named — guessing a calculator would be
    # worse than retrieving guidance.
    ("I earn 3 million, what do I pay", AgentRoute.RAG, ""),
]


@dataclass
class RoutingMiss:
    query: str
    expected: str
    actual: str
    missing_tool: str = ""

    def describe(self) -> str:
        if self.missing_tool:
            return f"{self.query!r}: routed {self.actual} but did not offer {self.missing_tool}"
        return f"{self.query!r}: expected {self.expected}, got {self.actual}"


@dataclass
class RoutingReport:
    total: int
    correct: int
    duration_ms: float
    misses: list[RoutingMiss] = field(default_factory=list)
    by_route: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "correct": self.correct,
            "accuracy": round(self.accuracy, 4),
            "duration_ms": round(self.duration_ms, 2),
            "misses": [asdict(m) for m in self.misses],
            "by_route": self.by_route,
        }

    def to_prometheus(self) -> str:
        lines = [
            "# HELP ura_routing_accuracy Supervisor routing accuracy on the golden set",
            "# TYPE ura_routing_accuracy gauge",
            f"ura_routing_accuracy {self.accuracy:.4f}",
            "# HELP ura_routing_misses Golden-set cases routed wrongly",
            "# TYPE ura_routing_misses gauge",
            f"ura_routing_misses {len(self.misses)}",
        ]
        for route, counts in sorted(self.by_route.items()):
            total = counts.get("total", 0)
            correct = counts.get("correct", 0)
            rate = correct / total if total else 0.0
            lines.append(f'ura_routing_accuracy_by_route{{route="{route}"}} {rate:.4f}')
        return "\n".join(lines) + "\n"


def run_routing_eval(
    golden_set: list[tuple[str, AgentRoute, str]] | None = None,
) -> RoutingReport:
    """Score the supervisor against *golden_set*.

    A case counts as correct only if the route matches **and** the
    expected tool is offered. Routing to the right path while omitting
    the tool that answers the question is a miss the route alone would
    hide.
    """
    from .supervisor import supervisor

    cases = GOLDEN_SET if golden_set is None else golden_set
    started = time.perf_counter()
    misses: list[RoutingMiss] = []
    by_route: dict[str, dict[str, int]] = {}
    correct = 0

    for query, expected_route, expected_tool in cases:
        decision = supervisor.classify(query)
        bucket = by_route.setdefault(expected_route.value, {"total": 0, "correct": 0})
        bucket["total"] += 1

        if decision.route != expected_route:
            misses.append(
                RoutingMiss(query, expected_route.value, decision.route.value)
            )
            continue
        if expected_tool and expected_tool not in decision.suggested_tools:
            misses.append(
                RoutingMiss(query, expected_route.value, decision.route.value, expected_tool)
            )
            continue

        correct += 1
        bucket["correct"] += 1

    return RoutingReport(
        total=len(cases),
        correct=correct,
        duration_ms=(time.perf_counter() - started) * 1000,
        misses=misses,
        by_route=by_route,
    )


def main() -> int:  # pragma: no cover - CLI
    """Print the report; non-zero exit when anything is misrouted."""
    import json

    report = run_routing_eval()
    print(json.dumps(report.to_dict(), indent=2))
    for miss in report.misses:
        print(f"  MISS {miss.describe()}")
    return 0 if not report.misses else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

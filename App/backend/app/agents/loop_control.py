"""Agent-loop control primitives — budgets, thrash suppression, compaction.

A bounded *iteration* count is not a bounded *agent*.  Three failure
modes are the ones that actually bite agentic-RAG systems in
production, and none of them is stopped by an iteration cap alone:

**Tool storms / retrieval thrash.**  Each generation round can emit an
unbounded fan-out of tool calls, so ``max_iterations=3`` still permits
dozens of dispatches per turn.  :class:`ToolCallBudget` adds the three
ceilings that matter — total calls per turn, fan-out per round, and
calls to any single tool.

**Duplicate work.**  A model that re-asks ``lookup_rate(vat)`` in every
round pays for it every round.  The MCP client's replay cache is keyed
on an explicit idempotency key, so it does not fire here.  The budget
memoizes on the ``(name, arguments)`` fingerprint instead and serves the
repeat for free, flagged so the model can see it is going in circles.

**Context bloat.**  Slicing a serialized payload at a byte offset puts
*invalid JSON* in the model's context and discards by position rather
than by salience.  :func:`compact_observation` shrinks structurally —
long strings first, then whole keys in reverse priority order — and
always returns something parseable, with an ``_omitted`` list so the
model knows what it is not seeing.

Every ceiling is a constructor argument; the module-level defaults are
the ones the live loop uses.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

#: Total tool dispatches allowed in one turn, across every round.
DEFAULT_MAX_TOTAL_CALLS = 8
#: Fan-out ceiling for a single generation round.
DEFAULT_MAX_CALLS_PER_ITERATION = 4
#: How often one tool may be dispatched with *distinct* arguments.
DEFAULT_MAX_CALLS_PER_TOOL = 3
#: Characters of serialized observation fed back for one tool result.
DEFAULT_OBSERVATION_BUDGET_CHARS = 2000
#: Characters of observation the whole turn may accumulate.
DEFAULT_TURN_OBSERVATION_BUDGET_CHARS = 12000
#: Never compact below this — an empty observation teaches nothing.
MIN_OBSERVATION_CHARS = 240

#: Result keys worth keeping when a payload has to be shrunk, most
#: important first.  Anything unlisted is dropped before anything listed.
_PRIORITY_KEYS: tuple[str, ...] = (
    "ok",
    "error",
    "summary",
    "message",
    "human_readable",
    "answer",
    "amount",
    "total",
    "tax",
    "net",
    "rate",
    "rate_key",
    "currency",
    "explanation",
    "fiscal_year",
    "effective_from",
    "legal_basis",
    "verification_warning",
    # A lesson's payload is several times the default budget, and its
    # teaching machinery is the part with no fallback: an answer the
    # model can still summarise loses little, a check question that was
    # dropped is a question never asked.  These outrank the provenance
    # block for that reason.
    "check_question",
    "answer_withheld",
    "instruction",
    "worked_example",
    "common_mistakes",
    "title",
    "citations",
    "sources",
)
_PRIORITY_RANK = {key: i for i, key in enumerate(_PRIORITY_KEYS)}


class Admission(str, Enum):
    """What the budget decided about one proposed tool call."""

    ADMIT = "admit"
    #: Identical call already made this turn — serve the memoized result.
    REPEAT = "repeat"
    DENIED = "denied"


@dataclass(frozen=True)
class BudgetDecision:
    """Outcome of :meth:`ToolCallBudget.admit`.

    ``result`` carries the memoized payload on :attr:`Admission.REPEAT`
    and the refusal payload on :attr:`Admission.DENIED` — in both cases
    it is what should be fed back to the model in place of a dispatch.
    """

    admission: Admission
    reason: str = ""
    result: dict[str, Any] | None = None

    @property
    def should_dispatch(self) -> bool:
        return self.admission is Admission.ADMIT


def call_fingerprint(name: str, arguments: dict[str, Any] | None) -> str:
    """Stable identity for a ``(name, arguments)`` pair.

    Argument order must not matter — a model emitting the same call with
    its keys shuffled is making the same call — so the payload is
    canonicalised with ``sort_keys`` before hashing.  Unserialisable
    values fall back to ``repr`` rather than raising: a fingerprint that
    is merely coarse is better than a crashed agent loop.
    """
    try:
        canonical = json.dumps(arguments or {}, sort_keys=True, default=repr)
    except (TypeError, ValueError):  # pragma: no cover - default=repr is total
        canonical = repr(arguments)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{name}:{digest}"


@dataclass
class ToolCallBudget:
    """Per-turn spend ceilings plus a memo of what has already been run.

    Create one per user turn.  Call :meth:`admit` before dispatching and
    :meth:`record` after, then read :meth:`stats` for the trace — the
    ratio of dispatches to answers is the metric that makes a thrashing
    agent visible on a dashboard.
    """

    max_total_calls: int = DEFAULT_MAX_TOTAL_CALLS
    max_calls_per_iteration: int = DEFAULT_MAX_CALLS_PER_ITERATION
    max_calls_per_tool: int = DEFAULT_MAX_CALLS_PER_TOOL
    observation_budget_chars: int = DEFAULT_OBSERVATION_BUDGET_CHARS
    turn_observation_budget_chars: int = DEFAULT_TURN_OBSERVATION_BUDGET_CHARS

    dispatched: int = 0
    repeats: int = 0
    denied: int = 0
    observation_chars_spent: int = 0
    _per_tool: Counter[str] = field(default_factory=Counter)
    _per_iteration: Counter[int] = field(default_factory=Counter)
    _memo: dict[str, dict[str, Any]] = field(default_factory=dict)

    # -- Admission -----------------------------------------------------
    def admit(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        iteration: int = 0,
    ) -> BudgetDecision:
        """Decide whether *name* may be dispatched, consuming budget if so.

        Checks run cheapest-refusal-first: the fan-out cap for this round,
        then the memo, then the turn-wide and per-tool ceilings.  The memo
        is consulted *before* the spend ceilings on purpose — a repeat
        costs no execution, so refusing it for lack of budget would be
        charging for work nobody does.
        """
        if self._per_iteration[iteration] >= self.max_calls_per_iteration:
            return self._deny(
                name,
                f"fan-out limit reached for this round "
                f"({self.max_calls_per_iteration} calls); "
                f"use the results you already have",
            )

        fingerprint = call_fingerprint(name, arguments)
        if fingerprint in self._memo:
            self._per_iteration[iteration] += 1
            self.repeats += 1
            logger.info("tool budget: repeat of %s served from memo", name)
            memo = dict(self._memo[fingerprint])
            memo["repeated_call"] = True
            memo["budget_note"] = (
                "You already called this tool with these arguments this turn. "
                "This is the earlier result — do not call it again."
            )
            return BudgetDecision(Admission.REPEAT, reason="duplicate call", result=memo)

        if self.dispatched >= self.max_total_calls:
            return self._deny(
                name,
                f"tool-call budget for this turn is spent "
                f"({self.max_total_calls} calls); answer from what you have",
            )

        if self._per_tool[name] >= self.max_calls_per_tool:
            return self._deny(
                name,
                f"{name} has already run {self.max_calls_per_tool} times this "
                f"turn; a different tool or a final answer is needed",
            )

        self._per_iteration[iteration] += 1
        self._per_tool[name] += 1
        self.dispatched += 1
        return BudgetDecision(Admission.ADMIT)

    def _deny(self, name: str, reason: str) -> BudgetDecision:
        self.denied += 1
        logger.warning("tool budget: denied %s — %s", name, reason)
        return BudgetDecision(
            Admission.DENIED,
            reason=reason,
            result={"ok": False, "error": reason, "budget_exhausted": True},
        )

    def record(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        result: Any,
    ) -> None:
        """Memoize *result* so an identical later call is served for free.

        Only dict results are memoized — anything else has no reliable
        shape to annotate with the repeat marker, and re-running it is
        cheaper than guessing.
        """
        if not isinstance(result, dict):
            return
        self._memo[call_fingerprint(name, arguments)] = dict(result)

    # -- Observation budget --------------------------------------------
    def observation_allowance(self) -> int:
        """Characters the next observation may occupy.

        The per-call cap, clamped by whatever is left of the turn-wide
        budget, floored at :data:`MIN_OBSERVATION_CHARS` so a late tool
        result is short rather than absent.
        """
        remaining = self.turn_observation_budget_chars - self.observation_chars_spent
        return max(MIN_OBSERVATION_CHARS, min(self.observation_budget_chars, remaining))

    def spend_observation(self, text: str) -> None:
        self.observation_chars_spent += len(text)

    def compact(self, result: Any) -> str:
        """Compact *result* against the remaining turn budget and charge it."""
        text = compact_observation(result, budget_chars=self.observation_allowance())
        self.spend_observation(text)
        return text

    # -- Telemetry -----------------------------------------------------
    def exhausted(self) -> bool:
        return self.dispatched >= self.max_total_calls

    def stats(self) -> dict[str, Any]:
        """Trace-shaped summary — safe to log, no argument values."""
        return {
            "dispatched": self.dispatched,
            "repeats": self.repeats,
            "denied": self.denied,
            "distinct_tools": len(self._per_tool),
            "observation_chars": self.observation_chars_spent,
            "exhausted": self.exhausted(),
        }


# ---------------------------------------------------------------------------
# Observation compaction
# ---------------------------------------------------------------------------
def _truncate_str(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 0)] + "…"


def _shrink_values(payload: dict[str, Any], limit: int) -> dict[str, Any]:
    """Truncate long leaf strings and long lists, keeping structure intact."""
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            out[key] = _truncate_str(value, limit)
        elif isinstance(value, list):
            kept = value[:3]
            shrunk = [_truncate_str(v, limit) if isinstance(v, str) else v for v in kept]
            if len(value) > len(kept):
                shrunk.append(f"…{len(value) - len(kept)} more")
            out[key] = shrunk
        else:
            out[key] = value
    return out


def _drop_order(keys: list[str]) -> list[str]:
    """Keys in the order they should be dropped — least important first."""
    return sorted(
        keys,
        key=lambda k: (-_PRIORITY_RANK.get(k, len(_PRIORITY_KEYS)), k),
    )


def compact_observation(
    result: Any,
    *,
    budget_chars: int = DEFAULT_OBSERVATION_BUDGET_CHARS,
) -> str:
    """Serialize *result* to at most *budget_chars* of **valid** JSON.

    Shrinking happens in three passes, each preserving parseability:
    truncate long leaves, drop keys in reverse priority order, then fall
    back to a minimal ``{"ok": …, "_truncated": true}`` stub.  Dropped
    key names are reported in ``_omitted`` — a model that can see what
    was withheld can ask for it, one that gets a silently clipped
    payload cannot.
    """
    budget = max(budget_chars, MIN_OBSERVATION_CHARS)

    def dumps(value: Any) -> str:
        return json.dumps(value, default=str, ensure_ascii=False)

    try:
        blob = dumps(result)
    except (TypeError, ValueError):  # pragma: no cover - default=str is total
        blob = json.dumps(str(result))
    if len(blob) <= budget:
        return blob

    if not isinstance(result, dict):
        return dumps(_truncate_str(str(result), budget - 16))

    # Pass 1 — truncate long leaves, keeping the most generous per-value
    # limit that still fits.  A fixed fraction of the budget would clip a
    # single long field to a quarter of the space actually available.
    shrunk = result
    for divisor in (1, 2, 4, 8, 16):
        shrunk = _shrink_values(result, max(budget // divisor, 80))
        blob = dumps(shrunk)
        if len(blob) <= budget:
            return blob

    # Pass 2 — drop whole keys, least important first.
    omitted: list[str] = []
    for key in _drop_order(list(shrunk)):
        if len(shrunk) <= 1:
            break
        shrunk.pop(key, None)
        omitted.append(key)
        candidate = dict(shrunk)
        candidate["_omitted"] = omitted
        blob = dumps(candidate)
        if len(blob) <= budget:
            return blob

    # Pass 3 — nothing survived at size; emit a stub that still parses.
    stub = {
        "ok": result.get("ok", True),
        "_truncated": True,
        "_omitted": _drop_order(list(result)),
    }
    return _truncate_str(dumps(stub), budget)

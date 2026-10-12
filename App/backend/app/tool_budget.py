"""Keep tool schemas inside the model's context window.

A tool-calling request is the system prompt, the question, any passages,
*and* every tool's JSON schema.  The schemas are the part nobody budgets:
the local registry's 24 read tools cost about 7,100 tokens, against a
4,096-token window on the local Sunflower server.  vLLM answers an
oversized request with HTTP 400, the tool loop sees "no tool call", and
the turn quietly falls back to plain retrieval — which, for a taxpayer who
selected a connector, means the connector was never asked and the answer
was produced as if they had not chosen one.

:func:`fit_tool_specs` is the guard.  It is a pure function of the specs, a
token budget and a token counter, so it is testable without a model:

1. if the specs fit, they are returned unchanged;
2. otherwise each is *slimmed* — description cut to its first sentence,
   per-property prose dropped, names/types/enums/required kept, which is
   what the model needs to form a valid call;
3. if that still does not fit, specs are kept in priority order until the
   budget is spent.  Priority is the order given, so a caller puts the
   tools it cannot do without first.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

#: Tokens the chat template adds around a tool block ("# Tools … <tools>…").
TOOLS_BLOCK_OVERHEAD_TOKENS = 100
#: A tool description is cut to its first sentence, and to this many characters.
SLIM_DESCRIPTION_CHARS = 160

ToolSpec = dict[str, Any]
TokenCounter = Callable[[str], int]


def _dump(spec: ToolSpec) -> str:
    """*spec* as the chat template renders it (``tojson``: spaced separators).

    Measured against the served model: four tools cost 407 prompt tokens, the
    compact rendering (no spaces) estimated 357 and this one 437. An estimate
    that errs high only drops a tool sooner; one that errs low is a request
    the server refuses.
    """
    return json.dumps(spec, ensure_ascii=False)


def _first_sentence(text: str, limit: int = SLIM_DESCRIPTION_CHARS) -> str:
    collapsed = " ".join(str(text or "").split())
    if not collapsed:
        return ""
    end = collapsed.find(". ")
    sentence = collapsed if end < 0 else collapsed[: end + 1]
    if len(sentence) > limit:
        sentence = sentence[: limit - 1].rstrip() + "…"
    return sentence


def _strip_prose(schema: Any) -> Any:
    """A copy of *schema* without ``description``/``title``/``examples`` prose.

    Only keys that are JSON Schema *keywords* are removed: inside a
    ``properties`` mapping the keys are property names, and a property that
    happens to be called "description" must survive.
    """
    if isinstance(schema, list):
        return [_strip_prose(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    slimmed: dict[str, Any] = {}
    for key, value in schema.items():
        if key in ("description", "title", "examples", "$comment"):
            continue
        if key in ("properties", "$defs", "definitions", "patternProperties") and isinstance(value, dict):
            slimmed[key] = {name: _strip_prose(child) for name, child in value.items()}
        else:
            slimmed[key] = _strip_prose(value)
    return slimmed


def slim_tool_spec(spec: ToolSpec) -> ToolSpec:
    """The smallest form of *spec* from which the model can still form a valid call."""
    function = dict(spec.get("function") or {})
    function["description"] = _first_sentence(function.get("description", ""))
    if "parameters" in function:
        function["parameters"] = _strip_prose(function["parameters"])
    return {**spec, "function": function}


def tool_names(specs: list[ToolSpec]) -> list[str]:
    return [str((spec.get("function") or {}).get("name", "")) for spec in specs]


def fit_tool_specs(
    specs: list[ToolSpec],
    budget_tokens: int,
    count_tokens: TokenCounter,
) -> list[ToolSpec]:
    """The longest priority-ordered prefix of *specs* that fits *budget_tokens*.

    Specs are slimmed before any is dropped.  Returns ``[]`` when not even
    the first slimmed spec fits; the caller then answers without tools
    rather than sending a request the server will refuse.
    """
    if not specs or budget_tokens <= 0:
        return []

    def block_tokens(items: list[ToolSpec]) -> int:
        return count_tokens("\n".join(_dump(item) for item in items)) + TOOLS_BLOCK_OVERHEAD_TOKENS

    if block_tokens(specs) <= budget_tokens:
        return list(specs)

    slim = [slim_tool_spec(spec) for spec in specs]
    total_tokens = block_tokens(slim)
    if total_tokens <= budget_tokens:
        return slim

    # One count, then arithmetic: tokens per character for these schemas.
    total_chars = sum(len(_dump(spec)) for spec in slim) or 1
    per_char = (total_tokens - TOOLS_BLOCK_OVERHEAD_TOKENS) / total_chars
    kept: list[ToolSpec] = []
    used = TOOLS_BLOCK_OVERHEAD_TOKENS
    for spec in slim:
        cost = int(len(_dump(spec)) * per_char) + 1
        if used + cost > budget_tokens:
            break
        kept.append(spec)
        used += cost
    return kept

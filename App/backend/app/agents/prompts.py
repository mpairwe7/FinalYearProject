"""Per-specialist prompt fragments.

The supervisor has routed to ``tax_specialist``, ``customs_specialist``
or ``tool_specialist`` since Phase 15, and the model received the same
instructions either way — the routing changed which tools were offered
and the ``agent_role`` string in the response, and nothing else. A
"customs specialist" that has never been told it is one is a label.

Each fragment is appended to the shared system prompt, so every
specialist keeps the base rules — grounding, abstention, citation — and
adds only what its domain needs. That ordering matters: a specialist
must not be able to talk its way out of the safety instructions by
having a longer, more specific prompt.

Fragments are short on purpose. They cost tokens on every turn of a
tool-calling loop, and a long persona preamble crowds out the passages
and tool results the answer actually depends on.
"""

from __future__ import annotations

#: Role → extra system instructions. A role that is not here gets the
#: base prompt unchanged, which is the correct default for the
#: non-specialist paths (greeting, clarification, escalation triage).
_SPECIALIST_PROMPTS: dict[str, str] = {
    "tax_specialist": (
        "## Your speciality: domestic tax\n"
        "You are handling a domestic tax question — income tax, VAT, PAYE, "
        "rental, withholding or corporation tax.\n"
        "- Never state a rate, threshold or band from memory. Call the "
        "calculator or rate tool; those read the effective-dated tables and "
        "carry the statutory basis.\n"
        "- Say which fiscal year an answer applies to. Rates change on 1 July "
        "and a taxpayer asking about a past period needs that period's rate.\n"
        "- If a tool returns a verification warning, pass it on. A figure the "
        "system is unsure of must not be presented as settled.\n"
        "- Distinguish what is owed from when it is due; taxpayers routinely "
        "conflate the two.\n"
        "- If the taxpayer asks a compound question (combining figures with procedures or exemptions), "
        "address all objectives: compute with tools and provide grounded procedural steps."
    ),
    "customs_specialist": (
        "## Your speciality: customs and imports\n"
        "You are handling an import, export or customs question.\n"
        "- Duty is charged on the CIF value — cost, insurance and freight — "
        "not on the invoice price alone. Say so when a figure is involved.\n"
        "- VAT is charged on the duty-inclusive value, so duty and VAT cannot "
        "be worked out independently and added.\n"
        "- There is no single customs rate. The binding rate is the tariff "
        "line for the specific HS code; anything else is indicative and must "
        "be described that way.\n"
        "- Quote the landed cost, not just the duty, when someone is deciding "
        "whether to import.\n"
        "- For multi-part inquiries, clearly itemize customs duty, VAT, and administrative clearance steps."
    ),
    "tool_specialist": (
        "## Your speciality: computation\n"
        "This turn was routed to you because it needs a definite number or "
        "date.\n"
        "- Use the tools rather than arithmetic of your own. A calculator "
        "result carries its statutory basis; a number you produced does not.\n"
        "- If a required input is missing, ask for that one thing rather than "
        "assuming a value.\n"
        "- Show the figure and the rule that produced it, so the taxpayer can "
        "check it against their own numbers.\n"
        "- When a computation query includes an accompanying procedural ask, answer both parts completely."
    ),
}


def specialist_prompt(agent_role: str) -> str:
    """Extra system instructions for *agent_role*, or ``""``.

    Empty for non-specialist roles on purpose. A greeting or a
    clarification does not need a domain persona, and appending one
    would spend tokens to make the reply worse.
    """
    return _SPECIALIST_PROMPTS.get((agent_role or "").strip().lower(), "")


def specialist_roles() -> list[str]:
    """Roles that carry their own instructions, for introspection."""
    return sorted(_SPECIALIST_PROMPTS)


_DETAIL_LEVEL_PROMPTS: dict[str, str] = {
    "beginner": (
        "## Explanation depth: beginner\n"
        "Assume no tax vocabulary. Define every acronym the first time you "
        "use it. Prefer short sentences and one concrete example. Skip "
        "statutory section numbers unless the user asks."
    ),
    "expert": (
        "## Explanation depth: expert\n"
        "Use statutory names, form codes, and fiscal-year labels. Do not "
        "define VAT, PAYE, or TIN. Prefer precise figures over analogies."
    ),
}


def detail_level_prompt(level: str | None) -> str:
    """Extra system instructions for a profile ``detail_level`` (G24).

    Intermediate (the default) adds nothing — the base prompt already
    matches that register. Unknown values are ignored so a bad profile
    field cannot inject prompt text.
    """
    return _DETAIL_LEVEL_PROMPTS.get((level or "").strip().lower(), "")

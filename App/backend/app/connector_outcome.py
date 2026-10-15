"""What a taxpayer is told once a chosen service has been asked.

Live, against the real model, the answers that followed a lookup went wrong
in ways a prompt did not fix:

* the service said "no stamp with that code in the fixtures — this does not
  establish whether the product is genuine or counterfeit", and the model
  wrote "the stamp is invalid and likely counterfeit";
* the service said "no consignment IM7-0001", and the model wrote "I am
  unable to access the URA database" — a different claim about a different
  thing;
* a lookup that needed a TIN the taxpayer had not given was answered with
  "please ask the taxpayer for their TIN";
* a successful answer from a practice system read as a statement of fact.

A person acts on these sentences, so the ones that carry a *negative or
missing* outcome are not left to the model. Each connector call is
classified from its structured result into one of four outcomes, and the
words for the three that are not a record are the service's own (or a fixed
sentence), never a paraphrase. Only a record that was found is summarised by
the model — that is the part a language model is for — and the practice-
system notice is attached by code, so it cannot be forgotten.

Pure functions over the tool-call dicts :func:`app.llm.generate_with_tools`
returns; nothing here talks to a service or a model.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: The service returned a record.
FOUND = "found"
#: The service answered, and the answer is "nothing matched" or a refusal in its own words.
NO_RECORD = "no_record"
#: The taxpayer has not yet given something the lookup needs.
NEEDS_INPUT = "needs_input"
#: The service could not be asked, or what came back could not be used.
UNAVAILABLE = "unavailable"

#: Errors made by the client or the agent loop, not written by a service.
#: A service that *answered* ``ok: false`` is a NO_RECORD; these are not.
_SYNTHESIZED_ERROR_RE = re.compile(
    r"temporarily unavailable|did not match its output schema|too large to review|"
    r"withheld because|unreadable response|not selected or available|integrity check failed|"
    r"circuit open|^[A-Za-z_.]*(Error|Exception|Timeout)\b",
    re.IGNORECASE,
)
#: Keys that describe the call, not the record.
_NOISE_KEYS = frozenset(
    {
        "ok", "found", "mode", "live", "policy", "simulation_notice", "error", "message",
        "retryable", "resultType", "ttlMs", "cacheScope", "validation_errors",
    }
)
_TIN_LIKE_RE = re.compile(r"\d{10}")
_MAX_MESSAGE_CHARS = 400
_MAX_FACTS = 8
_LABEL_CHARS = 120

#: What a taxpayer calls the things the six bundled services ask for. A
#: schema's own ``description`` is preferred; this is for tools without one.
_ARGUMENT_NAMES = {
    "tin": "TIN",
    "prn": "Payment Registration Number (PRN)",
    "stamp_code": "stamp code",
    "entry_number": "entry number",
    "query": "name or number to search for",
}

CONTACT_LINE = (
    "Please try again in a moment, or contact URA at https://ura.go.ug or on the "
    "toll-free Contact Centre 0800 117 000 / 0800 217 000."
)
DEMO_NOTICE = "Demo data: this comes from a practice system, not a live URA record."
_DEMO_MENTION_RE = re.compile(r"\b(demo|sample|simulat\w*|practice|test) (data|system|record|result)", re.IGNORECASE)


@dataclass(frozen=True)
class Outcome:
    """One connector call, reduced to what a taxpayer needs to hear."""

    tool: str
    connector: str
    label: str
    #: ``simulation`` or ``live`` as the deployment declared it, else ``""``.
    mode: str
    kind: str
    #: The service's own words (NO_RECORD), or a fixed sentence (UNAVAILABLE).
    message: str = ""
    #: What the taxpayer has not given yet (NEEDS_INPUT), in their words.
    missing: tuple[str, ...] = ()
    #: A few scalar fields of the record, for the case the model wrote nothing.
    facts: tuple[tuple[str, str], ...] = ()


def argument_label(schema: Mapping[str, Any] | None, name: str) -> str:
    """The taxpayer-facing name of an argument: its description, else a known name."""
    properties = (schema or {}).get("properties") or {}
    definition = properties.get(name)
    if isinstance(definition, Mapping):
        description = " ".join(str(definition.get("description") or "").split())
        if description:
            sentence = description.split(". ")[0].rstrip(".")
            return sentence[:_LABEL_CHARS]
    return _ARGUMENT_NAMES.get(name, name.replace("_", " "))


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _sentence(text: str) -> str:
    text = _clean(text)[:_MAX_MESSAGE_CHARS].rstrip()
    return text if not text or text[-1] in ".!?" else text + "."


_ACRONYMS = {
    "vat": "VAT", "tin": "TIN", "prn": "PRN", "efris": "EFRIS", "sdc": "SDC", "brn": "BRN",
    "nin": "NIN", "ura": "URA", "ursb": "URSB", "dts": "DTS", "bwims": "BWIMS", "ugx": "UGX", "id": "ID",
}


def _humanise(key: str) -> str:
    """``is_vat_registered`` → ``VAT registered``; ``business_name`` → ``Business name``."""
    words = [word for word in re.split(r"[_\s]+", key.strip()) if word]
    if len(words) > 1 and words[0].lower() in ("is", "has"):
        words = words[1:]
    words = [_ACRONYMS.get(word.lower(), word.lower()) for word in words]
    if words and words[0] == words[0].lower():
        words[0] = words[0].capitalize()
    return " ".join(words)


def _scalar(value: Any) -> str | None:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str) and value.strip():
        text = _clean(value)[:120]
        # The taxpayer typed it; and a TIN in a reply is redacted anyway.
        return None if _TIN_LIKE_RE.fullmatch(text) else text
    return None


def _facts(result: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """Up to a few scalar fields of the record, one nesting level deep."""
    facts: list[tuple[str, str]] = []
    for key, value in result.items():
        if key in _NOISE_KEYS:
            continue
        if isinstance(value, Mapping):
            for inner_key, inner in value.items():
                text = _scalar(inner)
                if text is not None and inner_key not in _NOISE_KEYS:
                    facts.append((_humanise(inner_key), text))
        elif isinstance(value, list):
            items = [text for text in (_scalar(item) for item in value[:4]) if text]
            if items:
                facts.append((_humanise(key), ", ".join(items)))
        else:
            text = _scalar(value)
            if text is not None:
                facts.append((_humanise(key), text))
        if len(facts) >= _MAX_FACTS:
            break
    return tuple(facts[:_MAX_FACTS])


def classify(call: Mapping[str, Any], *, label_for: Any = None) -> Outcome | None:
    """The outcome of one tool call, or ``None`` when it was not a connector call."""
    connector = str(call.get("connector") or "")
    if not connector:
        return None
    label = (label_for(connector) if callable(label_for) else "") or connector.replace("_", " ").title()
    base = {
        "tool": str(call.get("name") or ""),
        "connector": connector,
        "label": label,
        "mode": str(call.get("connector_mode") or ""),
    }
    result = call.get("result")
    if not isinstance(result, Mapping):
        return Outcome(kind=UNAVAILABLE, **base)

    error = _clean(result.get("error"))
    if error == "argument_not_provided":
        wanted = result.get("argument_labels") or result.get("arguments") or ()
        missing = tuple(_clean(item) for item in wanted if _clean(item))
        return Outcome(kind=NEEDS_INPUT, missing=missing, **base)
    if error.lower().startswith("invalid arguments"):
        # The MCP client's own schema check. Never shown as it is written
        # ("(root): 'tin' is a required property"); a missing required value is
        # a question for the taxpayer, anything else the call was not usable.
        names = tuple(
            _ARGUMENT_NAMES.get(name, name.replace("_", " "))
            for name in re.findall(r"'([A-Za-z0-9_]+)' is a required property", error)
        )
        if names:
            return Outcome(kind=NEEDS_INPUT, missing=names, **base)
        return Outcome(kind=UNAVAILABLE, **base)

    if result.get("ok", True) is False:
        retryable = bool(result.get("retryable"))
        denied = isinstance(result.get("policy"), Mapping) and result["policy"].get("allowed") is False
        if retryable or denied or _SYNTHESIZED_ERROR_RE.search(error):
            return Outcome(kind=UNAVAILABLE, **base)
        text = _clean(result.get("message")) or error
        if text:
            return Outcome(kind=NO_RECORD, message=_sentence(text), **base)
        return Outcome(kind=UNAVAILABLE, **base)

    if result.get("found") is False:
        text = _clean(result.get("message")) or "Nothing matched that request."
        return Outcome(kind=NO_RECORD, message=_sentence(text), **base)

    return Outcome(kind=FOUND, facts=_facts(result), **base)


def classify_all(
    tool_calls: Iterable[Mapping[str, Any]] | None, *, label_for: Any = None
) -> list[Outcome]:
    """Outcomes of the connector calls in *tool_calls*, in order, one per call."""
    outcomes: list[Outcome] = []
    for call in tool_calls or ():
        outcome = classify(call, label_for=label_for)
        if outcome is not None:
            outcomes.append(outcome)
    return outcomes


def connectors_used(
    tool_calls: Iterable[Mapping[str, Any]] | None, *, label_for: Any = None
) -> list[dict[str, str]]:
    """The services a turn consulted, once each, with how the last lookup ended.

    What the client shows beside the answer and the case ledger records as a
    step — names, a mode and an outcome kind; never the record.
    """
    used: dict[str, dict[str, str]] = {}
    for outcome in classify_all(tool_calls, label_for=label_for):
        used[outcome.connector] = {
            "namespace": outcome.connector,
            "label": outcome.label,
            "mode": outcome.mode,
            "outcome": outcome.kind,
        }
    return list(used.values())


def deterministic(outcomes: Sequence[Outcome]) -> bool:
    """True when the reply is worded from the services' answers alone.

    One lookup that did not return a record is enough. A model asked to
    summarise a mixed result wrote "the business is registered with URSB"
    beside a URSB answer of "no registered entity", so a turn that has any
    outcome that is not a record is not summarised by a model at all.
    """
    return any(item.kind != FOUND for item in outcomes)


def _line(outcome: Outcome, *, named: bool) -> str:
    prefix = f"{outcome.label}: " if named else ""
    if outcome.kind == NEEDS_INPUT:
        wanted = " and ".join(outcome.missing) or "the details"
        return f"To look that up in {outcome.label} I need the {wanted}. Please send it and I'll check."
    if outcome.kind == NO_RECORD:
        return f"{prefix}{outcome.message}"
    if outcome.kind == UNAVAILABLE:
        return (
            f"I couldn't get an answer from {outcome.label} just now, so I haven't looked that up "
            "and I won't guess at the result."
        )
    if outcome.facts:
        # An explicit list: ``Label: value; Label: value`` is rewritten into
        # bullets by the output guard, and detached the first label.
        bullets = "\n".join(f"- {key}: {value}" for key, value in outcome.facts)
        return f"{outcome.label} returned this record:\n\n{bullets}"
    return f"{outcome.label} returned a record, but I couldn't summarise it just now."


def compose(outcomes: Sequence[Outcome], model_text: str = "", *, other_tools: bool = False) -> str:
    """The reply for a turn that consulted connectors, or ``""`` when there were none.

    * every outcome a record → the model's summary (or the record's fields if
      it wrote nothing);
    * any outcome that is not → the services' own words for every lookup, and
      the model's text is not used (see :func:`deterministic`);
    * ``other_tools``: the turn also used tools that are not connectors, so the
      model's text is the only account of those and is kept, ahead of the
      services' words for the lookups that returned no record.
    """
    if not outcomes:
        return ""
    found = [item for item in outcomes if item.kind == FOUND]
    others = [item for item in outcomes if item.kind != FOUND]
    named = len({item.connector for item in outcomes}) > 1
    text = (model_text or "").strip()

    parts: list[str] = []
    if not others:
        parts.append(text or "\n".join(_line(item, named=named) for item in found))
    elif other_tools and text:
        parts.append(text)
    else:
        parts.extend(_line(item, named=named) for item in found)
    seen: set[tuple[str, str]] = set()
    for item in others:
        line = _line(item, named=named)
        if (item.kind, line) not in seen:
            seen.add((item.kind, line))
            parts.append(line)
    if any(item.kind == UNAVAILABLE for item in others):
        parts.append(CONTACT_LINE)

    reply = "\n\n".join(part for part in parts if part)
    # Said only when a practice system actually answered: a question for the
    # missing TIN, or "could not be reached", returned no data to qualify.
    answered = any(item.kind in (FOUND, NO_RECORD) and item.mode == "simulation" for item in outcomes)
    if answered and not _DEMO_MENTION_RE.search(reply):
        reply = f"{reply}\n\n{DEMO_NOTICE}"
    return reply


#: What a result that is reported by code is replaced with in the model's prompt.
REPORTED_SEPARATELY = (
    '{"note": "The outcome of this lookup is reported to the taxpayer separately. '
    'Do not mention it."}'
)

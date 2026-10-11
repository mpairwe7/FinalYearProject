"""Decide whether a chat turn is a request to look something up in a chosen service.

A taxpayer who adds EFRIS to a chat has said they want EFRIS used. Three
things then stood between that and a lookup, and each was observed live:

* the deterministic answers (TIN-registration template, "situational
  advisory", rate and procedure replies) are matched on keywords and answer
  before the agent runs — "Is the taxpayer with TIN … registered? Check
  EFRIS" was answered with an invented *EFRIS Invoice Audit*;
* the model, under ``tool_choice=auto``, writes "I would use
  ``efris_taxpayer_status`` but cannot check" instead of calling it;
* and when neither happened it answered from passages with a status it had
  made up ("received at Mombasa Port on November 18").

:func:`detect_connector_intent` is the early, deterministic half of the
fix: it says *whether* the message asks one of the chosen services to look
something up, and which operation. The caller then keeps the templates out
of the way and makes the first model call a named function call. It does
not decide arguments — the model fills those under constrained decoding and
:func:`ungrounded_arguments` rejects any the taxpayer did not actually
write, so a forced call can never become an invented identifier.

It is deliberately conservative. A wrong *no* leaves the turn exactly as it
was; a wrong *yes* replaces a correct procedural answer ("how do I register
for a TIN?") with a lookup nobody asked for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

#: Words that appear in many tool names and many questions. They never make
#: an operation relevant on their own ("what is the *status* of my return").
_GENERIC = frozenset(
    {
        "get", "view", "check", "status", "verify", "search", "read", "list", "lookup", "look",
        "show", "details", "detail", "info", "information", "tax", "taxes", "uganda", "ura",
        "system", "service", "services", "registration", "record", "records",
        "the", "of", "for", "a", "an", "and", "or", "in", "on", "to", "by", "with",
    }
)
#: Words that name a *service*, so that saying one is saying "use that one".
#: Derived from the namespace and tool names for any namespace not listed;
#: this table adds what people actually call the six bundled systems.
_BRAND_WORDS: dict[str, frozenset[str]] = {
    "efris": frozenset({"efris", "fiscal", "receipting", "invoicing"}),
    "digital_tax_stamps": frozenset({"dts", "kakasa", "stamp", "stamps", "digital"}),
    "payment_system": frozenset({"payment", "payments", "prn"}),
    "tin_registration": frozenset({"registry"}),
    "ursb": frozenset({"ursb"}),
    "bwims": frozenset({"bwims", "bonded", "warehouse"}),
}
#: Words that point at a service but are also used in questions about every
#: other one (a TIN is quoted in an EFRIS question as readily as in a registry
#: one). They select a service only when no other service was named.
_WEAK_BRAND_WORDS: dict[str, frozenset[str]] = {"tin_registration": frozenset({"tin"})}

#: Scoring: naming a service outweighs naming what it reads, which outweighs
#: a word every tool shares.
_W_BRAND, _W_WEAK, _W_OBJECT, _W_GENERIC, _W_UNMATCHED = 3.0, 1.5, 1.0, 0.5, -0.25
#: A word of the question that the operation's own description also uses
#: ("stock" for an operation named ``warehouse_inventory``). A tie-breaker.
_W_DESCRIPTION, _MAX_DESCRIPTION_MATCHES = 0.4, 3
_SIMULATOR_PREFIX_RE = re.compile(r"^LOCAL SIMULATOR ONLY\.[^.]*\.\s*", re.IGNORECASE)
_STEM_CHARS = 5

#: The question is asking *how*, not asking for a record.
_PROCEDURAL_RE = re.compile(
    r"\b(how (do|can|could|should|would|to|does)|what (is|are) (a|an|the)? ?(procedure|process|requirement)"
    r"|steps?|procedure|requirements?|process|guide me|walk me|explain|why)\b",
    re.IGNORECASE,
)
#: The taxpayer is asking for a specific record to be read.
_LOOKUP_RE = re.compile(
    r"\b(look ?up|check|verify|validate|confirm|search|find|status|registered|is there|does .* (have|show)|show me)\b",
    re.IGNORECASE,
)
#: Something that names one record: a code with a digit in it, or a quoted name.
_IDENTIFIER_RE = re.compile(r"(?=[A-Za-z0-9\-/]*\d)[A-Za-z0-9][A-Za-z0-9\-/]{4,}|\"[^\"]{3,}\"|“[^”]{3,}”|'[^']{3,}'")
#: A fiscal year or a period is a number, not a record.
_PERIOD_RE = re.compile(r"^(fy)?\d{4}([-/]\d{2,4})?$", re.IGNORECASE)
_NAMED_RE = re.compile(r"\b(named|called)\s+[A-Z0-9]", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-z0-9]+")

#: At most this many operations are forced in one turn (one per service).
MAX_FORCED_OPERATIONS = 3

#: What a required argument is, judged from its name. ``free`` takes any text.
_TIN_ARGUMENT_WORDS = frozenset({"tin"})
_PRN_ARGUMENT_WORDS = frozenset({"prn"})
_FREE_ARGUMENT_WORDS = frozenset({"query", "name", "text", "search", "keyword", "term"})
_REFERENCE_ARGUMENT_WORDS = frozenset(
    {"number", "code", "id", "reference", "entry", "registration", "stamp", "warehouse", "vehicle", "invoice"}
)
_TIN_RE = re.compile(r"(?<![\w-])\d{10}(?![\w-])")
_PRN_RE = re.compile(r"(?<![\w-])\d{12,13}(?![\w-])")
#: A reference code has a digit and a letter or a separator: ``IM7-0001``,
#: ``WH-KLA-01``, ``URSB-CO-10001``. A bare run of digits is a TIN or a PRN.
_REFERENCE_RE = re.compile(
    r"(?=[A-Za-z0-9\-/]*\d)(?=[A-Za-z0-9\-/]*[A-Za-z\-/])[A-Za-z0-9][A-Za-z0-9\-/]{3,}"
)


@dataclass(frozen=True)
class ConnectorIntent:
    """The operations a turn should call first, best match first."""

    operations: tuple[str, ...]
    #: Why, for the log. Never shown to the taxpayer.
    reason: str = ""


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


def _has_identifier(text: str) -> bool:
    if _NAMED_RE.search(text):
        return True
    for match in _IDENTIFIER_RE.finditer(text):
        if not _PERIOD_RE.match(match.group(0).strip("\"“”'")):
            return True
    return False


def _stems(words: Iterable[str]) -> set[str]:
    """Words of four letters or more, cut to a stem (``registered`` ~ ``registration``)."""
    return {word[:_STEM_CHARS] for word in words if len(word) >= 4 and word not in _GENERIC}


def _description_stems(description: str, brand: set[str]) -> set[str]:
    text = _SIMULATOR_PREFIX_RE.sub("", " ".join(str(description or "").split()))
    return _stems(_words(text) - brand)


def _vocabulary(namespace: str, name: str) -> tuple[set[str], set[str], set[str], set[str]]:
    """``(strong brand, weak brand, object, generic)`` words for one operation."""
    weak = set(_WEAK_BRAND_WORDS.get(namespace, ()))
    brand = set(_BRAND_WORDS.get(namespace, ())) | (_words(namespace.replace("_", " ")) - _GENERIC - weak)
    name_words = _words(name.replace("_", " "))
    generic = name_words & _GENERIC
    objects = name_words - _GENERIC - brand - weak
    return brand, weak, objects, generic


def _required_inputs_present(descriptor: dict[str, Any], text: str) -> bool:
    """Whether *text* contains something of the kind each required argument takes.

    A call is forced only to a function the taxpayer gave the inputs for. A
    message naming a company and carrying a TIN does not contain the
    *registration number* ``ursb_compliance_status`` requires; the model,
    made to call it, invented ``URSB-CO-10001``. Judged from argument names
    alone, and generously: an argument that is not recognised takes anything.
    """
    schema = descriptor.get("inputSchema") or {}
    for argument in schema.get("required") or ():
        words = set(str(argument).lower().split("_"))
        if words & _TIN_ARGUMENT_WORDS:
            if not _TIN_RE.search(text):
                return False
        elif words & _PRN_ARGUMENT_WORDS:
            if not _PRN_RE.search(text):
                return False
        elif words & _FREE_ARGUMENT_WORDS:
            continue
        elif words & _REFERENCE_ARGUMENT_WORDS and not _REFERENCE_RE.search(text):
            return False
    return True


def detect_connector_intent(
    message: str,
    tools: Iterable[tuple[str, dict[str, Any]]],
) -> ConnectorIntent | None:
    """Return the operations *message* asks for, or ``None``.

    *tools* are ``(namespace, descriptor)`` pairs of what the taxpayer selected
    and may use. An operation counts only if the message **names its service**
    (or, failing any named service, uses the one word that points at it) and
    is a request for a record rather than for an explanation.
    """
    text = " ".join(str(message or "").split())
    if not text:
        return None
    message_words = _words(text)
    message_stems = _stems(message_words)
    has_identifier = _has_identifier(text)
    asks_lookup = bool(_LOOKUP_RE.search(text))
    procedural = bool(_PROCEDURAL_RE.search(text))
    # How a process works is not a request for a record, whatever digits it
    # carries ("How do I pay PRN 220000000001?"); a lookup verb in the same
    # question ("How do I check PRN …") says it is both.
    if procedural and not asks_lookup:
        return None
    # A code makes it a lookup whatever the verb; without one the taxpayer must
    # have asked for a record and not for how a process works.
    if not (has_identifier or (asks_lookup and not procedural)):
        return None

    scored: list[tuple[float, int, str, str, bool, bool]] = []  # score, -order, ns, name, named, inputs
    any_named = False
    for order, (namespace, descriptor) in enumerate(tools):
        name = str(descriptor.get("name", ""))
        if not name:
            continue
        brand, weak, objects, generic = _vocabulary(namespace, name)
        named = len(message_words & brand)
        pointed = len(message_words & weak)
        if named == 0 and pointed == 0:
            continue
        described = min(
            len(message_stems & _description_stems(descriptor.get("description", ""), brand | weak)),
            _MAX_DESCRIPTION_MATCHES,
        )
        score = (
            _W_BRAND * named
            + _W_WEAK * pointed
            + _W_OBJECT * len(message_words & objects)
            + _W_GENERIC * len(message_words & generic)
            + _W_UNMATCHED * len(objects - message_words)
            + _W_DESCRIPTION * described
        )
        any_named = any_named or named > 0
        scored.append((score, -order, namespace, name, named > 0, _required_inputs_present(descriptor, text)))

    # A weak word ("TIN") selects a service only when nothing else was named,
    # and then only for a message that carries a code to look up.
    candidates = [row for row in scored if row[4]] if any_named else (scored if has_identifier else [])
    if not candidates:
        return None
    # Within a service, an operation whose required inputs are in the message
    # outranks one whose are not; when none is, the best match stands and the
    # argument guard asks the taxpayer for what is missing.
    supplied = {row[2] for row in candidates if row[5]}
    best: dict[str, tuple[float, int, str]] = {}
    for score, neg_order, namespace, name, _named, inputs in candidates:
        if namespace in supplied and not inputs:
            continue
        if namespace not in best or (score, neg_order) > best[namespace][:2]:
            best[namespace] = (score, neg_order, name)
    ranked = sorted(best.values(), reverse=True)[:MAX_FORCED_OPERATIONS]
    return ConnectorIntent(
        operations=tuple(name for _score, _order, name in ranked),
        reason=f"identifier={has_identifier} lookup={asks_lookup} procedural={procedural}",
    )


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


_TIN_VALUE_RE = re.compile(r"\d{10}")
_PRN_VALUE_RE = re.compile(r"\d{12,13}")


def _wrong_kind(name: str, value: Any) -> bool:
    """A value the taxpayer wrote, of the wrong kind for the argument it was put in.

    Live, "Is my taxpayer registered on EFRIS?" after a turn about payment
    reference 220000000001 was answered "Taxpayer with TIN … is not
    registered": the PRN was in the conversation, so it was *grounded*, and the
    model put it in ``tin``. Judged from the argument's name alone, as for
    :func:`_required_inputs_present`; an argument that is not recognised takes
    anything.
    """
    if not isinstance(value, str) or not value.strip():
        return False
    words = set(str(name).lower().split("_"))
    digits_and_letters = _squash(value)
    if words & _TIN_ARGUMENT_WORDS:
        return not _TIN_VALUE_RE.fullmatch(digits_and_letters)
    if words & _PRN_ARGUMENT_WORDS:
        return not _PRN_VALUE_RE.fullmatch(digits_and_letters)
    return False


def missing_arguments(
    arguments: dict[str, Any] | None,
    schema: dict[str, Any] | None,
    taxpayer_text: str,
) -> list[str]:
    """Arguments the call cannot be made without: required and absent, or invented.

    A call forced onto a function must produce *something*. Live, after an
    earlier turn mentioned a TIN, the model returned ``{}`` for "Is my
    taxpayer registered on EFRIS?": nothing ungrounded, so nothing was asked
    for, and the client's schema check then answered with "'tin' is a required
    property". An absent required argument is the same situation as an
    invented one, and is reported the same way.
    """
    given = arguments or {}
    missing: list[str] = []
    for name in (schema or {}).get("required") or ():
        if _blank(given.get(name)) and name not in missing:
            missing.append(str(name))
    for name in ungrounded_arguments(given, schema, taxpayer_text):
        if name not in missing:
            missing.append(name)
    for name, value in given.items():
        if name not in missing and _wrong_kind(str(name), value):
            missing.append(str(name))
    return missing


def _squash(value: str) -> str:
    """Case, spacing and punctuation the way an identifier is retyped."""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def ungrounded_arguments(
    arguments: dict[str, Any],
    schema: dict[str, Any] | None,
    taxpayer_text: str,
) -> list[str]:
    """Names of arguments whose value the taxpayer never wrote.

    A model made to call a function must put *something* in every required
    field. If the taxpayer gave no TIN, it will supply one. A free-text
    argument is therefore accepted only if it appears in what they wrote
    (ignoring case, spaces and punctuation: ``1000 000 001`` is
    ``1000000001``). Values the schema constrains to an ``enum``, and values
    that are not strings, are the model's choice and are not checked.
    """
    haystack = _squash(taxpayer_text)
    properties = (schema or {}).get("properties") or {}
    missing: list[str] = []
    for name, value in (arguments or {}).items():
        if not isinstance(value, str) or not value.strip():
            continue
        definition = properties.get(name)
        if isinstance(definition, dict) and definition.get("enum"):
            continue
        needle = _squash(value)
        if needle and needle not in haystack:
            missing.append(name)
    return missing

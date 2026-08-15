"""Slot validators for YAML-driven workflows.

Each validator spec is a short string (e.g. ``enum[individual,company,ngo]``,
``regex[^\\d{9}$]``, ``boolean``) parsed at runtime.  The :func:`validate_slot`
dispatcher returns ``(is_valid, normalized_value, error_message)``.
"""

from __future__ import annotations

import re

from ..calculator_router import parse_ugx_amount

_ENUM_RE = re.compile(r"^enum\[(.+)]$")
_REGEX_RE = re.compile(r"^regex\[(.+)]$")
_NUMBER_RE = re.compile(r"^number(?:\[min=(\d+(?:\.\d+)?)])?$")


# Spelling and vocabulary the enum matcher should not be defeated by.
#
# Deliberately small and one-directional: each alias maps to a *word* that may
# appear in an option, never to a whole option. Mapping straight to options
# would let this table silently decide a tax classification; here it only
# rewrites a word, and the ordinary matching below still has to find it in
# exactly one option — so an alias that turns out to be ambiguous re-asks
# rather than guessing.
_WORD_ALIASES: dict[str, str] = {
    # US/British spelling is the single most common reason a reply was rejected:
    # "organization" was refused against "organisation".
    "organization": "organisation",
    "organizations": "organisation",
    "organisations": "organisation",
    "org": "organisation",
    "orgs": "organisation",
    # Uganda's mobile-money vocabulary. "momo" is what people actually type.
    "momo": "mobile money",
    # Plurals of option words that appear in the flows.
    "services": "services",
    "goods": "goods",
    "fees": "fees",
}

_FILLER = {
    "a", "an", "the", "as", "i", "im", "i'm", "am", "is", "it", "its", "my",
    "me", "we", "our", "please", "pls", "just", "for", "of", "to", "be",
    "registering", "register", "want", "would", "like", "think", "maybe",
    "prefer", "choose", "option", "one", "this", "that",
}


def _words(text: str) -> list[str]:
    """Lowercase word list, with punctuation and separators flattened."""
    cleaned = re.sub(r"[^a-z0-9\s]+", " ", text.lower().replace("_", " ").replace("-", " "))
    return [w for w in cleaned.split() if w]


def _canonical(text: str) -> list[str]:
    """Word list with aliases applied and filler dropped."""
    out: list[str] = []
    for w in _words(text):
        mapped = _WORD_ALIASES.get(w, w)
        out.extend(mapped.split())
    return out


def _contains_sequence(haystack: list[str], needle: list[str]) -> bool:
    """True when `needle` appears as a contiguous run of whole words."""
    if not needle or len(needle) > len(haystack):
        return False
    return any(
        haystack[i : i + len(needle)] == needle
        for i in range(len(haystack) - len(needle) + 1)
    )


def _validate_enum(value: str, spec: str) -> tuple[bool, str, str]:
    """Match a reply to one option, reading intent rather than demanding a token.

    The original compared the whole reply to each option with `==`, so an answer
    only worked if the person typed the option and nothing else. Both of these
    were refused, from one real conversation:

        "as an individual"  vs  individual    — ordinary sentence framing
        "organization"      vs  organisation  — US spelling of the same word

    and the flow re-asked the identical question, which reads as the assistant
    not listening. Matching now runs in widening steps, and stops at the first
    that identifies exactly one option:

      1. the reply, normalised, IS an option
      2. an option appears in the reply as a whole-word sequence
         ("as an individual" -> individual)
      3. the reply, stripped of filler words, is a prefix of one option
         ("corporation" -> corporation tax; "withholding" -> withholding tax)

    Ambiguity is never resolved by picking. A reply matching more than one
    option re-asks naming just those, because guessing here writes a taxpayer
    classification into the rest of the flow.
    """
    m = _ENUM_RE.match(spec)
    if not m:
        return False, value, "Invalid enum spec"
    options = [o.strip() for o in m.group(1).split(",") if o.strip()]
    if not options:
        return False, value, "Invalid enum spec"

    reply = _canonical(value)
    if not reply:
        return False, value, f"Please choose one of: {', '.join(options)}"

    opt_words = {opt: _canonical(opt) for opt in options}

    # 1. The reply is exactly an option.
    for opt, words in opt_words.items():
        if reply == words:
            return True, opt, ""

    # 2. An option appears inside the reply as whole words. Longest option
    #    first, so "corporation tax" wins over a bare "tax" if both are offered.
    hits = [
        opt
        for opt, words in sorted(opt_words.items(), key=lambda kv: -len(kv[1]))
        if words and _contains_sequence(reply, words)
    ]
    if len(hits) == 1:
        return True, hits[0], ""
    if len(hits) > 1:
        # Prefer a hit that is not contained in another hit — "corporation tax"
        # over "tax" — and only give up if genuinely distinct options match.
        longest = max(len(opt_words[o]) for o in hits)
        top = [o for o in hits if len(opt_words[o]) == longest]
        if len(top) == 1:
            return True, top[0], ""
        return False, value, f"Did you mean {' or '.join(top)}?"

    # 3. The reply is a distinctive prefix of exactly one option.
    meaningful = [w for w in reply if w not in _FILLER] or reply
    starts = [
        opt
        for opt, words in opt_words.items()
        if words[: len(meaningful)] == meaningful
        or (len(meaningful) == 1 and words and words[0].startswith(meaningful[0]) and len(meaningful[0]) >= 3)
    ]
    if len(starts) == 1:
        return True, starts[0], ""
    if len(starts) > 1:
        return False, value, f"Did you mean {' or '.join(starts)}?"

    return False, value, f"Please choose one of: {', '.join(options)}"


def _validate_regex(value: str, spec: str) -> tuple[bool, str, str]:
    m = _REGEX_RE.match(spec)
    if not m:
        return False, value, "Invalid regex spec"
    pattern = m.group(1)
    if re.match(pattern, value.strip()):
        return True, value.strip(), ""
    return False, value, f"Invalid format (expected pattern: {pattern})"


_AFFIRMATIVE = {
    "yes", "y", "yeah", "yep", "yup", "ya", "true", "1", "confirm", "confirmed",
    "ok", "okay", "sure", "correct", "right", "affirmative", "proceed",
    "continue", "go", "please", "definitely", "absolutely", "indeed",
}
_NEGATIVE = {
    "no", "n", "nope", "nah", "not", "false", "0", "cancel", "stop", "never",
    "negative", "dont", "don't", "skip",
}


def _validate_boolean(value: str) -> tuple[bool, bool | str, str]:
    """Yes/no, read from a sentence rather than a single token.

    Same failure as the enum matcher had: the whole reply was compared to a
    fixed set, so "yes please", "yeah", "that's right" and "no thanks" were all
    rejected and the question repeated. Now any word in the reply decides it,
    and a reply carrying both ("yes and no") re-asks rather than picking.
    """
    words = set(_words(value))
    if not words:
        return False, value, "Please answer yes or no."
    yes, no = words & _AFFIRMATIVE, words & _NEGATIVE
    if yes and not no:
        return True, True, ""
    if no and not yes:
        return True, False, ""
    if yes and no:
        return False, value, "Was that a yes or a no?"
    return False, value, "Please answer yes or no."


def _validate_text(value: str) -> tuple[bool, str, str]:
    v = value.strip()
    if v:
        return True, v, ""
    return False, v, "Please provide a non-empty value."


def _validate_number(value: str, spec: str) -> tuple[bool, float | str, str]:
    """Parse a UGX-style amount ("1,500,000", "1.5m", "500k")."""
    m = _NUMBER_RE.match(spec)
    minimum = float(m.group(1)) if m and m.group(1) is not None else None
    amount = parse_ugx_amount(value)
    if amount is None:
        return (
            False,
            value,
            "Please give me one amount in UGX — for example 1,500,000 or 1.5m.",
        )
    if minimum is not None and amount < minimum:
        return False, value, f"Please give an amount of at least UGX {minimum:,.0f}."
    if minimum is None and amount <= 0:
        return False, value, "Please give an amount greater than zero."
    return True, amount, ""


def validate_slot(value: str, validator_spec: str) -> tuple[bool, object, str]:
    """Dispatch validation based on *validator_spec*.

    Returns ``(is_valid, normalized_value, error_message)``.
    """
    spec = validator_spec.strip()

    if spec == "boolean":
        return _validate_boolean(value)
    if spec == "text" or not spec:
        return _validate_text(value)
    if spec.startswith("enum["):
        return _validate_enum(value, spec)
    if spec.startswith("regex["):
        return _validate_regex(value, spec)
    if spec.startswith("number"):
        return _validate_number(value, spec)

    return _validate_text(value)

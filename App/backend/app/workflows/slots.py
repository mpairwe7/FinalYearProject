"""Slot validators for YAML-driven workflows.

Each validator spec is a short string (e.g. ``enum[individual,company,ngo]``,
``regex[^\\d{9}$]``, ``boolean``) parsed at runtime.  The :func:`validate_slot`
dispatcher returns ``(is_valid, normalized_value, error_message)``.
"""

from __future__ import annotations

import re

_ENUM_RE = re.compile(r"^enum\[(.+)]$")
_REGEX_RE = re.compile(r"^regex\[(.+)]$")


def _validate_enum(value: str, spec: str) -> tuple[bool, str, str]:
    m = _ENUM_RE.match(spec)
    if not m:
        return False, value, "Invalid enum spec"
    options = [o.strip() for o in m.group(1).split(",")]
    normalised = value.strip().lower()
    for opt in options:
        if normalised == opt.lower():
            return True, opt, ""
    return False, value, f"Please choose one of: {', '.join(options)}"


def _validate_regex(value: str, spec: str) -> tuple[bool, str, str]:
    m = _REGEX_RE.match(spec)
    if not m:
        return False, value, "Invalid regex spec"
    pattern = m.group(1)
    if re.match(pattern, value.strip()):
        return True, value.strip(), ""
    return False, value, f"Invalid format (expected pattern: {pattern})"


def _validate_boolean(value: str) -> tuple[bool, bool | str, str]:
    normalised = value.strip().lower()
    if normalised in {"yes", "y", "true", "1", "confirm", "ok"}:
        return True, True, ""
    if normalised in {"no", "n", "false", "0", "cancel"}:
        return True, False, ""
    return False, value, "Please answer yes or no."


def _validate_text(value: str) -> tuple[bool, str, str]:
    v = value.strip()
    if v:
        return True, v, ""
    return False, v, "Please provide a non-empty value."


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

    return _validate_text(value)

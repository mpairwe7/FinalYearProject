"""JSON Schema validation for MCP tool arguments and results.

MCP 2026-07-28 lifts ``inputSchema``/``outputSchema`` to full JSON
Schema 2020-12.  Validating arguments *before* dispatch turns a model's
malformed tool call into a precise, actionable error ("amount: expected
number, got string") instead of a Python ``TypeError`` reported from
inside the tool, and stops unknown keys reaching a tool that would
silently ignore them.

``jsonschema`` is an existing transitive dependency, but this module
degrades to a small structural check if it is ever absent — validation
tightening a boundary must not be the thing that takes the boundary
down.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import-shape branch
    from jsonschema import Draft202012Validator

    _HAVE_JSONSCHEMA = True
except ImportError:  # pragma: no cover - fallback path
    Draft202012Validator = None  # type: ignore[assignment]
    _HAVE_JSONSCHEMA = False


#: JSON Schema primitive names → the Python types that satisfy them.
_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
}


def _type_error(key: str, value: Any, expected: Any) -> str | None:
    """Check one property against its declared ``type``, or ``None`` if fine."""
    names = [expected] if isinstance(expected, str) else list(expected or [])
    if not names:
        return None
    for name in names:
        allowed = _JSON_TYPES.get(name)
        if allowed is None:
            return None  # unknown type keyword — do not guess
        # bool is an int subclass; only "boolean" should accept it.
        if isinstance(value, bool) and name != "boolean":
            continue
        if isinstance(value, allowed):
            return None
    return f"{key}: {value!r} is not of type {' or '.join(names)}"


def _fallback_errors(schema: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    """Structural validation for hosts without ``jsonschema``.

    Covers the mistakes a model actually makes — a missing required
    argument, an invented one, a string where a number belongs, a value
    outside an enum. It is not full JSON Schema, but it must be strong
    enough that the boundary still *means* something when the optional
    dependency is absent; a fallback that only counts keys would report
    ``"a lot"`` as a valid salary.
    """
    errors: list[str] = []
    properties: dict[str, Any] = schema.get("properties", {}) or {}

    for key in schema.get("required", []):
        if key not in payload:
            errors.append(f"{key}: required property is missing")

    if schema.get("additionalProperties") is False:
        for key in payload:
            if key not in properties:
                errors.append(f"{key}: unexpected property")

    for key, value in payload.items():
        spec = properties.get(key)
        if not isinstance(spec, dict) or value is None:
            continue
        type_error = _type_error(key, value, spec.get("type"))
        if type_error:
            errors.append(type_error)
            continue
        choices = spec.get("enum")
        if choices and value not in choices:
            errors.append(f"{key}: {value!r} is not one of {choices}")
        if isinstance(value, int | float) and not isinstance(value, bool):
            minimum, maximum = spec.get("minimum"), spec.get("maximum")
            if minimum is not None and value < minimum:
                errors.append(f"{key}: {value} is less than the minimum of {minimum}")
            if maximum is not None and value > maximum:
                errors.append(f"{key}: {value} is greater than the maximum of {maximum}")
        if isinstance(value, str) and len(value) < (spec.get("minLength") or 0):
            errors.append(f"{key}: {value!r} should be non-empty")
    return errors


def validate_arguments(schema: dict[str, Any] | None, arguments: dict[str, Any]) -> list[str]:
    """Return human-readable validation errors for *arguments*, or ``[]``.

    Errors are phrased for a model to act on: each names the offending
    property path and what was wrong with it.
    """
    if not schema:
        return []
    if not _HAVE_JSONSCHEMA:
        return _fallback_errors(schema, arguments)

    validator = Draft202012Validator(schema)
    errors: list[str] = []
    for error in sorted(validator.iter_errors(arguments), key=lambda e: list(e.path)):
        path = ".".join(str(p) for p in error.path) or "(root)"
        errors.append(f"{path}: {error.message}")
    return errors


def result_matches_schema(schema: dict[str, Any] | None, result: Any) -> list[str]:
    """Validate a tool result against its declared ``outputSchema``.

    Mismatches are reported to the caller to log, never raised: a server
    that adds a field should not break a working answer, but the drift
    must be visible.
    """
    if not schema or not _HAVE_JSONSCHEMA:
        return []
    validator = Draft202012Validator(schema)
    return [
        f"{'.'.join(str(p) for p in e.path) or '(root)'}: {e.message}"
        for e in sorted(validator.iter_errors(result), key=lambda e: list(e.path))
    ]

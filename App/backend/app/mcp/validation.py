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


def _fallback_errors(schema: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    """Required-key and unknown-key check for hosts without ``jsonschema``."""
    errors: list[str] = []
    for key in schema.get("required", []):
        if key not in payload:
            errors.append(f"{key}: required property is missing")
    if schema.get("additionalProperties") is False:
        known = set(schema.get("properties", {}))
        for key in payload:
            if key not in known:
                errors.append(f"{key}: unexpected property")
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

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

import hashlib
import json
import logging
import re
from typing import Any

from .protocol import META_DESCRIPTOR_SHA256

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

    Mismatches are returned, never raised. The MCP client refuses to speak
    a drifted payload. A server that adds a field still passes when the
    schema allows additional properties.
    """
    if not schema or not _HAVE_JSONSCHEMA:
        return []
    validator = Draft202012Validator(schema)
    return [
        f"{'.'.join(str(p) for p in e.path) or '(root)'}: {e.message}"
        for e in sorted(validator.iter_errors(result), key=lambda e: list(e.path))
    ]


# ---------------------------------------------------------------------------
# OWASP MCP Top 10 (2026) / CSA CTMS / AISVS 10.4 Descriptor & Output Guards
# ---------------------------------------------------------------------------
_UNICODE_CONTROL_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]")
_BIDI_OVERRIDE_RE = re.compile(r"[\u202a-\u202e\u2066-\u2069]")

_TOOL_POISONING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "prompt_override_directive",
        re.compile(
            r"\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+)?(?:previous|prior|above|system)\s+"
            r"(?:instructions|prompts|rules|guardrails)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "chat_template_control_token",
        re.compile(r"(?:<\|im_start\|>|<\|im_end\|>|\[INST\]|\[/INST\]|<<SYS>>|<\|endoftext\|>)", re.IGNORECASE),
    ),
    (
        "covert_exfiltration_instruction",
        re.compile(
            r"(?:\b(?:do\s+not\s+tell\s+the\s+user|hide\s+this\s+from\s+the\s+user|"
            r"silently\s+(?:exfiltrate|forward|send))\b|\[SYSTEM\s+OVERRIDE\])",
            re.IGNORECASE,
        ),
    ),
    (
        "cross_tool_shadowing_directive",
        re.compile(
            r"\b(?:instead\s+of\s+calling\s+\w+|always\s+call\s+this\s+tool\s+before\s+\w+|"
            r"override\s+tool\s+\w+|shadow\s+tool\s+\w+)\b",
            re.IGNORECASE,
        ),
    ),
)

_CREDENTIAL_LEAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bearer_jwt_leak", re.compile(r"\bBearer\s+eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}")),
    ("api_secret_key_leak", re.compile(r"\b(?:sk-[A-Za-z0-9]{24,}|AKIA[0-9A-Z]{16})\b")),
)


def compute_descriptor_sha256(descriptor: dict[str, Any]) -> str:
    """Compute canonical SHA-256 over a tool descriptor's security-relevant fields.

    Follows the 2026 OWASP MCP Security Cheat Sheet & CSA Canonical Tool
    Metadata Schema (CTMS): hashes ``name``, ``description``, ``inputSchema``,
    ``outputSchema``, ``annotations`` (``readOnlyHint``, ``destructiveHint``,
    ``idempotentHint``, ``openWorldHint``), and security ``_meta`` fields so
    neither description poisoning nor a silent annotation flip can pass
    unnoticed.
    """
    raw_meta = descriptor.get("_meta")
    meta = raw_meta if isinstance(raw_meta, dict) else {}
    canonical_payload = {
        "name": str(descriptor.get("name", "")),
        "description": str(descriptor.get("description", "")),
        "inputSchema": descriptor.get("inputSchema") or {},
        "outputSchema": descriptor.get("outputSchema"),
        "annotations": descriptor.get("annotations") or {},
        "securityMeta": {
            "risk": meta.get("ug.go.ura.chatbot/risk", "low"),
            "requiredScopes": list(meta.get("ug.go.ura.chatbot/requiredScopes") or []),
            "allowedRoles": list(meta.get("ug.go.ura.chatbot/allowedRoles") or []),
            "scopeExemptRoles": list(meta.get("ug.go.ura.chatbot/scopeExemptRoles") or []),
            "requiresConfirmation": bool(meta.get("ug.go.ura.chatbot/requiresConfirmation", False)),
        },
    }
    encoded = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _iter_strings(value: Any, path: str = "$") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, str):
        found.append((path, value))
    elif isinstance(value, dict):
        for k, v in value.items():
            found.extend(_iter_strings(v, f"{path}.{k}"))
    elif isinstance(value, list | tuple):
        for idx, item in enumerate(value):
            found.extend(_iter_strings(item, f"{path}[{idx}]"))
    return found


def inspect_descriptor_integrity(descriptor: dict[str, Any]) -> list[str]:
    """Verify tool descriptor hash integrity and scan for tool-poisoning payloads."""
    if not isinstance(descriptor, dict):
        return ["descriptor_not_an_object"]
    violations: list[str] = []
    raw_meta = descriptor.get("_meta")
    meta = raw_meta if isinstance(raw_meta, dict) else {}
    declared_hash = meta.get(META_DESCRIPTOR_SHA256)
    if declared_hash is not None:
        expected_hash = compute_descriptor_sha256(descriptor)
        if not isinstance(declared_hash, str) or declared_hash != expected_hash:
            violations.append("descriptor_sha256_mismatch")

    for field_name in ("description", "inputSchema", "outputSchema", "annotations"):
        for path, text in _iter_strings(descriptor.get(field_name), f"$.{field_name}"):
            if _BIDI_OVERRIDE_RE.search(text):
                violations.append(f"tool_poisoning:bidi_control_smuggling@{path}")
            normalized_text = _UNICODE_CONTROL_RE.sub("", text)
            for label, pattern in _TOOL_POISONING_PATTERNS:
                if pattern.search(normalized_text):
                    violations.append(f"tool_poisoning:{label}@{path}")
    return violations


def screen_tool_result(result: Any) -> list[str]:
    """Screen MCP tool output for indirect prompt injection and raw credential leaks."""
    violations: list[str] = []
    for path, text in _iter_strings(result):
        if _BIDI_OVERRIDE_RE.search(text):
            violations.append(f"indirect_prompt_injection:bidi_control_smuggling@{path}")
        normalized_text = _UNICODE_CONTROL_RE.sub("", text)
        for label, pattern in _TOOL_POISONING_PATTERNS:
            if pattern.search(normalized_text):
                if normalized_text != text and label == "prompt_override_directive":
                    violations.append(f"indirect_prompt_injection:unicode_obfuscated_override@{path}")
                else:
                    violations.append(f"indirect_prompt_injection:{label}@{path}")
        for label, pattern in _CREDENTIAL_LEAK_PATTERNS:
            if pattern.search(normalized_text):
                violations.append(f"credential_leak:{label}@{path}")
    return violations


def scrub_prompt_overrides_in_result(value: Any) -> Any:
    """Replace natural-language prompt override phrases with ``[REDACTED_INSTRUCTION]``."""
    override_pattern = _TOOL_POISONING_PATTERNS[0][1]
    if isinstance(value, str):
        return override_pattern.sub("[REDACTED_INSTRUCTION]", value)
    if isinstance(value, dict):
        return {k: scrub_prompt_overrides_in_result(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_prompt_overrides_in_result(item) for item in value]
    return value

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

import difflib
import hashlib
import json
import logging
import math
import re
import urllib.parse
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
        if isinstance(value, list | tuple):
            min_items, max_items = spec.get("minItems"), spec.get("maxItems")
            if min_items is not None and len(value) < min_items:
                errors.append(f"{key}: array length {len(value)} is less than minItems {min_items}")
            if max_items is not None and len(value) > max_items:
                errors.append(f"{key}: array length {len(value)} exceeds maxItems {max_items}")
            items_spec = spec.get("items")
            if isinstance(items_spec, dict) and items_spec.get("type"):
                for idx, elem in enumerate(value):
                    elem_err = _type_error(f"{key}[{idx}]", elem, items_spec.get("type"))
                    if elem_err:
                        errors.append(elem_err)
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


_NUMERIC_STRING_RE = re.compile(
    r"^\s*(?:UGX|UShs?|Shs?\.?)?\s*([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"\s*(k|thousand|m|million|bn|b|billion)?\s*(?:UGX|UShs?|Shs?\.?|/=|/-)?\s*$",
    re.IGNORECASE,
)
_BANTU_PREFIX_NUMERIC_RE = re.compile(
    r"^\s*(?:UGX|UShs?|Shs?\.?|shilingi|shiringi|siringi|cilingi|ssente|sente|esente|cente|ensimbi|fedha|pesa|lim)?\s*"
    r"(mili+y?oni|bili+y?oni|el?ufu|elfu|tutemia|laki|o?bukadde|a?kakadde|o?bukaikuru|a?kakaikuru|o?buwumbi|a?kawumbi|e?mitwalo|o?mutwalo|e?mitwaro|o?mutwaro|e?nkumi|o?lukumi|o?rukumi)"
    r"\s+([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(?:UGX|UShs?|Shs?\.?|/=|/-)?\s*$",
    re.IGNORECASE,
)
_PERCENT_RATE_STRING_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*%\s*$"
)
_SCALE_MULTIPLIERS: dict[str, float] = {
    "k": 1e3,
    "thousand": 1e3,
    "m": 1e6,
    "million": 1e6,
    "b": 1e9,
    "bn": 1e9,
    "billion": 1e9,
    "milioni": 1e6,
    "miliyoni": 1e6,
    "bilioni": 1e9,
    "biliyoni": 1e9,
    "elfu": 1e3,
    "elufu": 1e3,
    "tutemia": 1e3,
    "laki": 1e5,
    "bukadde": 1e6,
    "obukadde": 1e6,
    "kakadde": 1e6,
    "akakadde": 1e6,
    "bukaikuru": 1e6,
    "obukaikuru": 1e6,
    "kakaikuru": 1e6,
    "akakaikuru": 1e6,
    "buwumbi": 1e9,
    "obuwumbi": 1e9,
    "kawumbi": 1e9,
    "akawumbi": 1e9,
    "mitwalo": 1e4,
    "emitwalo": 1e4,
    "mutwalo": 1e4,
    "omutwalo": 1e4,
    "mitwaro": 1e4,
    "emitwaro": 1e4,
    "mutwaro": 1e4,
    "omutwaro": 1e4,
    "nkumi": 1e3,
    "enkumi": 1e3,
    "lukumi": 1e3,
    "olukumi": 1e3,
    "rukumi": 1e3,
    "orukumi": 1e3,
}


def coerce_arguments_to_schema(
    schema: dict[str, Any] | None, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Losslessly coerce unambiguous LLM argument strings to declared JSON Schema types.

    Addresses the primary ToolAthlon (ICLR 2026) failure mode where models pass
    formatted currency/numeric strings (``"4,500,000"``, ``"UGX 1200000"``),
    boolean strings (``"true"``/``"false"``), or differently-cased enum literals.
    Never guesses on ambiguous strings — those remain untouched so
    :func:`validate_arguments` rejects them with a clear error.
    """
    if not schema or not isinstance(arguments, dict):
        return dict(arguments or {})
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return dict(arguments)

    coerced = dict(arguments)
    for key, value in list(coerced.items()):
        spec = properties.get(key)
        if not isinstance(spec, dict) or value is None:
            continue
        expected_type = spec.get("type")
        type_set = (
            {expected_type}
            if isinstance(expected_type, str)
            else set(expected_type)
            if isinstance(expected_type, list | tuple)
            else set()
        )
        choices = spec.get("enum")

        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and "string" in type_set
            and "integer" not in type_set
            and "number" not in type_set
            and key in ("tin", "taxpayer_tin", "buyer_tin", "importer_tin", "prn", "payment_registration_number")
        ):
            str_id = str(value)
            id_pattern = r"\d{10}|\d{12,14}" if key in ("prn", "payment_registration_number") else r"\d{10}"
            if re.fullmatch(id_pattern, str_id):
                coerced[key] = str_id
                continue

        if isinstance(value, str):
            stripped = value.strip()
            # 0. Hyphenated/spaced 10-digit TIN, 12-14 digit PRN, or 14-char NIN normalization on string properties
            if (
                "string" in type_set
                and key in ("tin", "taxpayer_tin", "buyer_tin", "importer_tin")
                and ("-" in stripped or " " in stripped)
            ):
                compact_tin = re.sub(r"[\s-]+", "", stripped)
                if re.fullmatch(r"\d{10}", compact_tin):
                    coerced[key] = compact_tin
                    continue
            if (
                "string" in type_set
                and key in ("prn", "payment_registration_number")
                and ("-" in stripped or " " in stripped)
            ):
                compact_prn = re.sub(r"[\s-]+", "", stripped)
                if re.fullmatch(r"\d{12,14}", compact_prn):
                    coerced[key] = compact_prn
                    continue
            if (
                "string" in type_set
                and key in ("nin", "national_id")
                and ("-" in stripped or " " in stripped)
            ):
                compact_nin = re.sub(r"[\s-]+", "", stripped).upper()
                if re.fullmatch(r"C[MFR][A-Z0-9]{12}", compact_nin):
                    coerced[key] = compact_nin
                    continue

            # 1. Case-insensitive enum match when string is not already in choices
            if isinstance(choices, list) and choices and value not in choices:
                ci_matches = [c for c in choices if isinstance(c, str) and c.lower() == stripped.lower()]
                if len(ci_matches) == 1:
                    coerced[key] = ci_matches[0]
                    continue
                if set(choices) == {"add", "extract"}:
                    low_val = stripped.lower().replace("-", "_")
                    if low_val in ("inclusive", "vat_inclusive"):
                        coerced[key] = "extract"
                        continue
                    if low_val in ("exclusive", "vat_exclusive"):
                        coerced[key] = "add"
                        continue

            # 2. Numeric coercion ("number" or "integer" when "string" is not declared)
            if ("number" in type_set or "integer" in type_set) and "string" not in type_set:
                if "number" in type_set and spec.get("maximum") == 1:
                    m_pct = _PERCENT_RATE_STRING_RE.match(stripped)
                    if m_pct:
                        try:
                            pct_val = float(m_pct.group(1)) / 100.0
                            if 0.0 <= pct_val <= 1.0:
                                coerced[key] = pct_val
                                continue
                        except (ValueError, OverflowError):
                            pass
                from ..number_words import normalize_vernacular_amount_words

                norm_stripped = normalize_vernacular_amount_words(stripped)
                m_bantu = _BANTU_PREFIX_NUMERIC_RE.match(norm_stripped)
                if m_bantu:
                    bantu_mult = _SCALE_MULTIPLIERS.get(m_bantu.group(1).lower(), 1.0)
                    raw_bantu_num = m_bantu.group(2).replace(",", "")
                    try:
                        parsed_bantu = float(raw_bantu_num) * bantu_mult
                        if math.isfinite(parsed_bantu):
                            if "integer" in type_set and "number" not in type_set:
                                if parsed_bantu.is_integer():
                                    coerced[key] = int(parsed_bantu)
                            else:
                                coerced[key] = parsed_bantu
                            continue
                    except (ValueError, OverflowError):
                        pass
                m = _NUMERIC_STRING_RE.match(stripped)
                if m:
                    raw_num = m.group(1).replace(",", "")
                    scale_suffix = (m.group(2) or "").lower()
                    scale_mult = _SCALE_MULTIPLIERS.get(scale_suffix, 1.0)
                    try:
                        parsed_float = float(raw_num) * scale_mult
                        if math.isfinite(parsed_float):
                            if "integer" in type_set and "number" not in type_set:
                                if parsed_float.is_integer():
                                    coerced[key] = int(parsed_float)
                            elif parsed_float.is_integer() and "." not in raw_num and scale_mult == 1.0:
                                coerced[key] = int(parsed_float)
                            else:
                                coerced[key] = parsed_float
                    except (ValueError, OverflowError):
                        pass
                continue

            # 3. Boolean coercion ("boolean" when "string" is not declared)
            if "boolean" in type_set and "string" not in type_set:
                low = stripped.lower()
                if low == "true":
                    coerced[key] = True
                elif low == "false":
                    coerced[key] = False
        elif isinstance(value, float) and "integer" in type_set and "number" not in type_set:
            if math.isfinite(value) and value.is_integer():
                coerced[key] = int(value)

    return coerced


_PARAM_ALIAS_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"tin", "taxpayer_tin", "buyer_tin", "importer_tin", "taxpayer_id"}),
    frozenset({"amount", "amount_ugx", "gross_amount", "payment_amount"}),
    frozenset({"brn", "ursb_brn", "ursb_registration_number", "registration_number"}),
    frozenset({"stamp_serial", "stamp_code", "stamp_serials"}),
    frozenset({"mobile", "mobile_number", "phone", "telephone"}),
)


def build_validation_repair_hints(
    schema: dict[str, Any] | None,
    arguments: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    """Build structured self-correction hints when tool arguments fail validation."""
    if not schema or not errors:
        return {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = [str(r) for r in (schema.get("required") or []) if r]
    missing_required = [r for r in required if r not in arguments or arguments.get(r) is None]
    unexpected_fields = (
        sorted(str(k) for k in arguments if k not in properties)
        if schema.get("additionalProperties") is False
        else []
    )
    allowed_enums: dict[str, list[Any]] = {}
    expected_types: dict[str, Any] = {}
    for prop_name, prop_spec in properties.items():
        if not isinstance(prop_spec, dict):
            continue
        if "type" in prop_spec:
            expected_types[prop_name] = prop_spec["type"]
        if isinstance(prop_spec.get("enum"), list):
            allowed_enums[prop_name] = list(prop_spec["enum"])

    suggested_renames: dict[str, str] = {}
    candidate_targets = missing_required or [str(p) for p in properties if p not in arguments]
    for bad_key in unexpected_fields:
        bad_low = bad_key.lower()
        matched_target = ""
        for group in _PARAM_ALIAS_GROUPS:
            if bad_low in group:
                for tgt in candidate_targets:
                    if tgt.lower() in group:
                        matched_target = tgt
                        break
            if matched_target:
                break
        if not matched_target and candidate_targets:
            best_ratio, best_tgt = max(
                (difflib.SequenceMatcher(None, bad_low, tgt.lower()).ratio(), tgt)
                for tgt in candidate_targets
            )
            if best_ratio >= 0.65:
                matched_target = best_tgt
        if matched_target:
            suggested_renames[bad_key] = matched_target

    hints: dict[str, Any] = {
        "missing_required": missing_required,
        "unexpected_fields": unexpected_fields,
        "expected_types": expected_types,
        "allowed_enums": allowed_enums,
    }
    if suggested_renames:
        hints["suggested_renames"] = suggested_renames
    return hints


def result_matches_schema(schema: dict[str, Any] | None, result: Any) -> list[str]:
    """Validate a tool result against its declared ``outputSchema``.

    Mismatches are returned, never raised. The MCP client refuses to speak
    a drifted payload. A server that adds a field still passes when the
    schema allows additional properties.
    """
    if not schema:
        return []
    if not _HAVE_JSONSCHEMA:
        return _fallback_errors(schema, result) if isinstance(result, dict) else []
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
    (
        "markdown_exfiltration_link",
        re.compile(
            r"!\[[^\]]*\]\(https?://[^\s)]+\?(?:[^)\s]*[?&])?"
            r"(?:tin|nin|prn|token|secret|password|key|auth|cookie|session)=[^)\s]+\)",
            re.IGNORECASE,
        ),
    ),
)

_CREDENTIAL_LEAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bearer_jwt_leak", re.compile(r"\bBearer\s+eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}")),
    ("api_secret_key_leak", re.compile(r"\b(?:sk-[A-Za-z0-9]{24,}|AKIA[0-9A-Z]{16})\b")),
    ("private_key_leak", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    (
        "db_connection_secret_leak",
        re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb|redis)://[^:\s/]+:[^@\s/]{4,}@"),
    ),
    (
        "vcs_or_hf_token_leak",
        re.compile(r"\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}|hf_[A-Za-z0-9]{32,})\b"),
    ),
)

ALLOWED_RESOURCE_SCHEMES: frozenset[str] = frozenset(
    {
        "ura",
        "efris",
        "dts",
        "digital_tax_stamps",
        "ursb",
        "bwims",
        "tin",
        "tin_registration",
        "payment",
        "payments",
        "payment_system",
        "wire",
    }
)


def validate_resource_uri(uri: str) -> str | None:
    """Return an error reason if *uri* is malformed, uses a disallowed scheme, or attempts path traversal."""
    if not isinstance(uri, str) or not uri.strip() or len(uri) > 512:
        return "invalid_resource_uri"
    if any(ord(c) < 0x20 or c in ("\\", "\x7f") for c in uri):
        return "control_or_backslash_in_resource_uri"
    if "://" not in uri:
        return "missing_resource_scheme"
    unquoted = urllib.parse.unquote(uri)
    if ".." in uri or ".." in unquoted:
        return "path_traversal_in_resource_uri"
    scheme = uri.split("://", 1)[0].strip().lower()
    if scheme not in ALLOWED_RESOURCE_SCHEMES:
        return f"disallowed_resource_scheme:{scheme}"
    return None


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
            if field_name in ("inputSchema", "outputSchema") and path.endswith((".$ref", ".$dynamicRef")):
                # MCP SEP-2106: reject external/network $ref / $dynamicRef URIs to prevent SSRF and Fetch-DoS
                if not text.strip().startswith("#"):
                    violations.append(f"tool_poisoning:external_schema_ref@{path}")
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

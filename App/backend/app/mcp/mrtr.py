"""Multi Round-Trip Requests (MCP 2026-07-28).

A server that needs more input returns ``resultType: "input_required"``
with ``inputRequests`` and an optional ``requestState``. The client
collects ``inputResponses`` and retries the original call once, echoing
the state. If ``requestState`` influences anything, the spec requires
integrity protection — we HMAC-SHA256 it when
``MCP_REQUEST_STATE_SECRET`` is set.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

_ALG = "hmac-sha256"
MIN_STATE_SECRET_CHARS = 32
DEFAULT_STATE_TTL_S = 1800.0


def _is_production() -> bool:
    return os.getenv("APP_ENV", "development").lower() == "production"


def _secret() -> bytes:
    raw = (os.getenv("MCP_REQUEST_STATE_SECRET") or "").strip()
    if _is_production() and len(raw) < MIN_STATE_SECRET_CHARS:
        return b""
    return raw.encode()


def _canonical_state_bytes(payload: dict[str, Any], binding: dict[str, Any] | None = None) -> bytes:
    if binding:
        envelope = {"payload": payload, "_binding": binding}
    else:
        envelope = payload
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def wrap_request_state(
    payload: dict[str, Any],
    *,
    tool_name: str = "",
    namespace: str = "",
    tenant_id: str = "",
    user_id: str = "",
    ttl_s: float | None = DEFAULT_STATE_TTL_S,
) -> dict[str, Any]:
    """Sign *payload* (and optional caller/tool context binding) so a retry proves origin and scope."""
    binding: dict[str, Any] = {}
    if tool_name:
        binding["tool_name"] = str(tool_name)
    if namespace:
        binding["namespace"] = str(namespace)
    if tenant_id:
        binding["tenant_id"] = str(tenant_id)
    if user_id:
        binding["user_id"] = str(user_id)
    if ttl_s is not None and ttl_s > 0:
        now = time.time()
        binding["iat"] = round(now, 3)
        binding["exp"] = round(now + float(ttl_s), 3)

    body_bytes = _canonical_state_bytes(payload, binding or None)
    secret = _secret()
    if not secret:
        if _is_production():
            raise RuntimeError("MCP_REQUEST_STATE_SECRET (>=32 chars) is required in production")
        out: dict[str, Any] = {"payload": payload, "alg": "none"}
        if binding:
            out["_binding"] = binding
        return out
    sig = hmac.new(secret, body_bytes, hashlib.sha256).hexdigest()
    out = {"payload": payload, "alg": _ALG, "sig": sig}
    if binding:
        out["_binding"] = binding
    return out


def unwrap_request_state(
    wrapped: Any,
    *,
    expected_tool: str = "",
    expected_namespace: str = "",
    expected_tenant: str = "",
    expected_user: str = "",
) -> dict[str, Any] | None:
    """Return the inner state, or ``None`` if signature, expiry, or context binding fails."""
    if not isinstance(wrapped, dict):
        return None
    payload = wrapped.get("payload")
    if not isinstance(payload, dict):
        return None
    raw_binding = wrapped.get("_binding")
    binding = raw_binding if isinstance(raw_binding, dict) else None
    secret = _secret()
    alg = str(wrapped.get("alg") or "none")
    if not secret:
        if _is_production():
            return None
        if alg != "none":
            return None
    else:
        if alg != _ALG or not wrapped.get("sig"):
            return None
        body_bytes = _canonical_state_bytes(payload, binding)
        expected = hmac.new(secret, body_bytes, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(str(wrapped["sig"]), expected):
            logger.warning("MCP requestState HMAC mismatch")
            return None

    now = time.time()
    for source in (binding or {}, payload):
        exp = source.get("exp") or source.get("expires_at")
        if isinstance(exp, int | float) and now > float(exp):
            logger.warning("MCP requestState expired")
            return None

    if binding:
        if expected_tool and binding.get("tool_name") and str(binding["tool_name"]) != expected_tool:
            logger.warning("MCP requestState tool_name mismatch")
            return None
        if (
            expected_namespace
            and binding.get("namespace")
            and str(binding["namespace"]) != expected_namespace
        ):
            logger.warning("MCP requestState namespace mismatch")
            return None
        if expected_tenant and binding.get("tenant_id") and str(binding["tenant_id"]) != expected_tenant:
            logger.warning("MCP requestState tenant_id mismatch")
            return None
        if expected_user and binding.get("user_id") and str(binding["user_id"]) != expected_user:
            logger.warning("MCP requestState user_id mismatch")
            return None

    return payload


def parse_input_required(result: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize an InputRequiredResult. Accepts the legacy ``elicitations`` key.

    The specification carries ``inputRequests`` as a map from a
    server-chosen key to the request, and the retry answers under the
    same keys.  The map is passed through as it arrived: flattening it
    would throw away the keys the answers have to be filed under.  A
    list — what pre-release servers sent — is passed through too.
    """
    requests = result.get("inputRequests")
    if requests is None:
        requests = result.get("elicitations")
    if result.get("resultType") != "input_required" and not requests:
        return None
    return {
        "ok": False,
        "input_required": True,
        "inputRequests": requests if isinstance(requests, dict) else list(requests or []),
        "requestState": result.get("requestState"),
    }

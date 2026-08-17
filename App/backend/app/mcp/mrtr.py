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
from typing import Any

logger = logging.getLogger(__name__)

_ALG = "hmac-sha256"


def _secret() -> bytes:
    return (os.getenv("MCP_REQUEST_STATE_SECRET") or "").encode()


def wrap_request_state(payload: dict[str, Any]) -> dict[str, Any]:
    """Sign *payload* so a retry can prove it was issued by this host."""
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    secret = _secret()
    if not secret:
        return {"payload": payload, "alg": "none"}
    sig = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    return {"payload": payload, "alg": _ALG, "sig": sig}


def unwrap_request_state(wrapped: Any) -> dict[str, Any] | None:
    """Return the inner state, or ``None`` if the signature is missing/bad."""
    if not isinstance(wrapped, dict):
        return None
    payload = wrapped.get("payload")
    if not isinstance(payload, dict):
        return None
    secret = _secret()
    alg = str(wrapped.get("alg") or "none")
    if not secret:
        return payload if alg == "none" else None
    if alg != _ALG or not wrapped.get("sig"):
        return None
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    expected = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(wrapped["sig"]), expected):
        logger.warning("MCP requestState HMAC mismatch")
        return None
    return payload


def parse_input_required(result: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize an InputRequiredResult. Accepts the legacy ``elicitations`` key."""
    requests = result.get("inputRequests")
    if requests is None:
        requests = result.get("elicitations")
    if result.get("resultType") != "input_required" and not requests:
        return None
    return {
        "ok": False,
        "input_required": True,
        "inputRequests": list(requests or []),
        "requestState": result.get("requestState"),
    }

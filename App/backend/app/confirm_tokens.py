"""HMAC-signed single-use confirmation tokens for HITL tool calls (Phase 4).

When the agentic loop produces a tool call whose schema declares
``requires_confirmation=True`` and the tool returns ``{"submitted": False,
"proposal": ...}``, the server emits a ``tool_call.confirmation_required``
event including a ``confirm_token``.  The client echoes the token back
with its confirmation decision.  The server verifies the token before
re-invoking the tool with ``submit=True``.

The token is HMAC-SHA256 over a canonical payload (call_id, name,
idempotency_key, expiry).  This binds the approval to a specific call —
a stale or copied token cannot approve a different action.

Tokens are *single-use*: the WS handler tracks the set of consumed
``call_id`` values per socket session and rejects replay attempts.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

_SECRET_ENV = "WS_CONFIRM_HMAC_SECRET"
# In production this MUST come from the environment.  Development falls
# back to a per-process random secret so tests are reproducible within a
# run but tokens never leak across deployments.
_DEV_FALLBACK = secrets.token_bytes(32)

# Confirmation grant validity window — 5 minutes is well below the 60-min
# session cap and comfortably above the typical user-think-time.
_DEFAULT_TTL_SECONDS = 300


def _secret() -> bytes:
    raw = os.getenv(_SECRET_ENV, "")
    if raw:
        return raw.encode("utf-8")
    return _DEV_FALLBACK


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def sign(
    *,
    call_id: str,
    tool_name: str,
    idempotency_key: str,
    session_id: str,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> str:
    """Mint a single-use confirmation token.

    The token encodes the binding payload as the first segment and the
    HMAC tag as the second, joined by ``.``.  Format: ``<b64-payload>.<b64-tag>``.
    """
    payload = {
        "call_id": call_id,
        "tool_name": tool_name,
        "idempotency_key": idempotency_key,
        "session_id": session_id,
        "exp": int(time.time()) + max(60, ttl_seconds),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    tag = hmac.new(_secret(), payload_bytes, hashlib.sha256).digest()
    return f"{_b64(payload_bytes)}.{_b64(tag)}"


def verify(
    token: str,
    *,
    expected_call_id: str | None = None,
    expected_session_id: str | None = None,
) -> dict[str, Any] | None:
    """Validate a confirmation token.

    Returns the decoded payload dict on success or ``None`` if the
    signature, expiry, or binding fields don't match.  Does not enforce
    single-use semantics — the WS handler tracks consumed call_ids.
    """
    if not isinstance(token, str) or "." not in token:
        return None
    try:
        payload_b64, tag_b64 = token.split(".", 1)
        payload_bytes = _b64d(payload_b64)
        expected_tag = _b64d(tag_b64)
    except (ValueError, base64.binascii.Error):
        return None

    actual_tag = hmac.new(_secret(), payload_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(actual_tag, expected_tag):
        return None

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    if not isinstance(payload, dict):
        return None
    if payload.get("exp", 0) < time.time():
        return None
    if expected_call_id is not None and payload.get("call_id") != expected_call_id:
        return None
    if expected_session_id is not None and payload.get("session_id") != expected_session_id:
        return None
    return payload

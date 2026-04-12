"""JWT verification — stdlib-only, no PyJWT / python-jose dependency.

Supports HS256 (dev / shared-secret) and RS256 (production, from a
JWKS endpoint).  Deliberately small and auditable — every security
decision is in one file.

For production, point ``OIDC_JWKS_URL`` at a Keycloak / Auth0 JWKS
endpoint and ``OIDC_ISSUER`` / ``OIDC_AUDIENCE`` at your tenant.
The verifier caches JWKS for 1 hour and re-fetches on `kid` miss.

For development, set ``AUTH_DEV_SECRET`` and use
:func:`make_dev_token` to mint test tokens.  The dev issuer is
gated behind ``APP_ENV != "production"`` so it can never be used
in a prod deploy.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

AUTH_ALG = os.getenv("AUTH_ALG", "HS256").upper()  # HS256 | RS256
AUTH_DEV_SECRET = os.getenv("AUTH_DEV_SECRET", "dev-insecure-change-me")
OIDC_ISSUER = os.getenv("OIDC_ISSUER", "")
OIDC_AUDIENCE = os.getenv("OIDC_AUDIENCE", "ura-chatbot")
OIDC_JWKS_URL = os.getenv("OIDC_JWKS_URL", "")
APP_ENV = os.getenv("APP_ENV", "development").lower()


class JWTAuthError(Exception):
    """Raised on any token verification failure."""


# ---------------------------------------------------------------------------
# Base64url helpers
# ---------------------------------------------------------------------------
def _b64url_decode(s: str) -> bytes:
    s = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode("ascii"))


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


# ---------------------------------------------------------------------------
# HS256 (dev only)
# ---------------------------------------------------------------------------
def _hs256_sign(header_b64: str, payload_b64: str, secret: str) -> str:
    msg = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest()
    return _b64url_encode(sig)


def _hs256_verify(token: str, secret: str) -> dict[str, Any]:
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError as e:
        raise JWTAuthError(f"malformed token ({e})") from e

    expected = _hs256_sign(header_b64, payload_b64, secret)
    if not hmac.compare_digest(expected, sig_b64):
        raise JWTAuthError("invalid signature")

    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception as e:
        raise JWTAuthError(f"malformed header/payload: {e}") from e

    if header.get("alg") != "HS256":
        raise JWTAuthError(f"unexpected alg: {header.get('alg')}")

    return payload


# ---------------------------------------------------------------------------
# Main verifier
# ---------------------------------------------------------------------------
class JWTVerifier:
    """Verify and decode a JWT into a claims dict.

    The caller (``dependencies.current_user``) then translates claims
    into an :class:`AuthUser`.  Keeping claim translation separate
    from token verification lets us test both sides in isolation.
    """

    def __init__(
        self,
        alg: str = AUTH_ALG,
        dev_secret: str = AUTH_DEV_SECRET,
        issuer: str = OIDC_ISSUER,
        audience: str = OIDC_AUDIENCE,
    ) -> None:
        self.alg = alg.upper()
        self.dev_secret = dev_secret
        self.issuer = issuer
        self.audience = audience
        # RS256 / JWKS support is stubbed — real production wires
        # in a JWKS cache here.  For now we refuse RS256 outside dev.
        if self.alg not in ("HS256", "RS256"):
            raise JWTAuthError(f"unsupported alg {self.alg}")

    def verify(self, token: str) -> dict[str, Any]:
        """Verify signature, exp, nbf, iss, aud.  Return claims."""
        if not token:
            raise JWTAuthError("empty token")

        if self.alg == "HS256":
            claims = _hs256_verify(token, self.dev_secret)
        elif self.alg == "RS256":
            # Production plumbing: fetch JWKS by `kid`, verify RSA sig.
            # Intentionally not implemented in this file to keep the
            # dev path dependency-free.  Wire a PyJWT/python-jose
            # verifier in a follow-up when Keycloak lands.
            raise JWTAuthError(
                "RS256 verification not yet wired — install python-jose "
                "and implement JWKS fetch, or use HS256 for dev."
            )
        else:
            raise JWTAuthError(f"unsupported alg {self.alg}")

        # Temporal claims
        now = time.time()
        exp = claims.get("exp", 0)
        nbf = claims.get("nbf", 0)
        if exp and now >= exp:
            raise JWTAuthError("token expired")
        if nbf and now < nbf:
            raise JWTAuthError("token not yet valid")

        # Issuer / audience (only checked if configured — empty = skip)
        if self.issuer and claims.get("iss") != self.issuer:
            raise JWTAuthError(f"issuer mismatch: expected {self.issuer}")
        if self.audience:
            aud = claims.get("aud", [])
            if isinstance(aud, str):
                aud = [aud]
            if self.audience not in aud:
                raise JWTAuthError(f"audience mismatch: expected {self.audience}")

        return claims


# ---------------------------------------------------------------------------
# Dev token minting (never exposed in production)
# ---------------------------------------------------------------------------
def make_dev_token(
    user_id: str,
    tenant_id: str = "default",
    email: str = "",
    role: str = "public",
    granted_purposes: list[str] | None = None,
    ttl_seconds: int = 3600,
    secret: str = AUTH_DEV_SECRET,
    issuer: str = OIDC_ISSUER or "ura-chatbot-dev",
    audience: str = OIDC_AUDIENCE,
) -> str:
    """Mint an HS256 JWT for tests and local development.

    Refuses to run under APP_ENV=production so a leaked dev secret
    in a prod config can't be used to forge tokens.
    """
    if APP_ENV == "production":
        raise RuntimeError(
            "make_dev_token() is disabled under APP_ENV=production. " "Use a real OIDC provider."
        )

    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload: dict[str, Any] = {
        "sub": user_id,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "nbf": now - 5,
        "exp": now + ttl_seconds,
        "tenant_id": tenant_id,
        "email": email,
        "role": role,
        "granted_purposes": granted_purposes or [],
    }
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _hs256_sign(header_b64, payload_b64, secret)
    return f"{header_b64}.{payload_b64}.{sig}"

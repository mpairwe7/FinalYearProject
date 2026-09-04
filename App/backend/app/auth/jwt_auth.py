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
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

AUTH_ALG = os.getenv("AUTH_ALG", "HS256").upper()  # HS256 | RS256
AUTH_DEV_SECRET = os.getenv("AUTH_DEV_SECRET", "dev-insecure-change-me")
OIDC_ISSUER = os.getenv("OIDC_ISSUER", "")
OIDC_AUDIENCE = os.getenv("OIDC_AUDIENCE", "ura-chatbot")
OIDC_JWKS_URL = os.getenv("OIDC_JWKS_URL", "")
OIDC_JWKS_CACHE_TTL_S = int(os.getenv("OIDC_JWKS_CACHE_TTL_S", "3600"))
OIDC_JWKS_TIMEOUT_S = float(os.getenv("OIDC_JWKS_TIMEOUT_S", "5"))
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


def _split_token(token: str) -> tuple[str, str, str]:
    parts = token.split(".")
    if len(parts) != 3:
        raise JWTAuthError("malformed token")
    return parts[0], parts[1], parts[2]


def _decode_unverified(token: str) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    header_b64, payload_b64, sig_b64 = _split_token(token)
    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        signature = _b64url_decode(sig_b64)
    except Exception as e:
        raise JWTAuthError(f"malformed header/payload: {e}") from e
    return header, payload, signing_input, signature


# ---------------------------------------------------------------------------
# HS256 (dev only)
# ---------------------------------------------------------------------------
def _hs256_sign(header_b64: str, payload_b64: str, secret: str) -> str:
    msg = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest()
    return _b64url_encode(sig)


def _hs256_verify(token: str, secret: str) -> dict[str, Any]:
    header_b64, payload_b64, sig_b64 = _split_token(token)

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


def _rsa_public_key_from_jwk(jwk: dict[str, Any]) -> Any:
    try:
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError as e:
        raise JWTAuthError("cryptography is required for RS256 verification") from e

    if jwk.get("kty") != "RSA":
        raise JWTAuthError(f"unsupported jwk kty: {jwk.get('kty')}")

    n = jwk.get("n")
    e = jwk.get("e")
    if not n or not e:
        raise JWTAuthError("RSA JWK missing modulus/exponent")

    try:
        modulus = int.from_bytes(_b64url_decode(n), "big")
        exponent = int.from_bytes(_b64url_decode(e), "big")
        return rsa.RSAPublicNumbers(exponent, modulus).public_key()
    except Exception as err:
        raise JWTAuthError(f"invalid RSA JWK: {err}") from err


def _fetch_jwks(url: str, timeout_s: float) -> dict[str, Any]:
    if not url:
        raise JWTAuthError("OIDC_JWKS_URL is required for RS256 verification")
    if APP_ENV == "production":
        if not url.startswith("https://"):
            raise JWTAuthError("OIDC_JWKS_URL must start with https:// in production")
    elif not (url.startswith("https://") or url.startswith("http://")):
        raise JWTAuthError("OIDC_JWKS_URL must start with https:// or http://")

    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:  # nosec B310 # noqa: S310 # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        raise JWTAuthError(f"failed to fetch JWKS: {err}") from err

    if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
        raise JWTAuthError("malformed JWKS payload")
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
        alg: str | None = None,
        dev_secret: str | None = None,
        issuer: str | None = None,
        audience: str | None = None,
        jwks_url: str | None = None,
        jwks_cache_ttl_s: int | None = None,
        jwks_timeout_s: float | None = None,
    ) -> None:
        self.alg = (alg or os.getenv("AUTH_ALG", AUTH_ALG)).upper()
        self.dev_secret = dev_secret or os.getenv("AUTH_DEV_SECRET", AUTH_DEV_SECRET)
        self.issuer = issuer if issuer is not None else os.getenv("OIDC_ISSUER", OIDC_ISSUER)
        self.audience = (
            audience if audience is not None else os.getenv("OIDC_AUDIENCE", OIDC_AUDIENCE)
        )
        self.jwks_url = jwks_url if jwks_url is not None else os.getenv("OIDC_JWKS_URL", OIDC_JWKS_URL)
        self.jwks_cache_ttl_s = jwks_cache_ttl_s if jwks_cache_ttl_s is not None else int(
            os.getenv("OIDC_JWKS_CACHE_TTL_S", str(OIDC_JWKS_CACHE_TTL_S))
        )
        self.jwks_timeout_s = jwks_timeout_s if jwks_timeout_s is not None else float(
            os.getenv("OIDC_JWKS_TIMEOUT_S", str(OIDC_JWKS_TIMEOUT_S))
        )
        self._jwks_by_kid: dict[str, dict[str, Any]] = {}
        self._jwks_fetched_at = 0.0
        self._last_forced_refresh_at = 0.0
        if self.alg not in ("HS256", "RS256"):
            raise JWTAuthError(f"unsupported alg {self.alg}")

    def _refresh_jwks(self, *, force: bool = False) -> None:
        if self.alg != "RS256":
            return
        now = time.time()
        if (
            not force
            and self._jwks_by_kid
            and (now - self._jwks_fetched_at) < self.jwks_cache_ttl_s
        ):
            return
        if force and (now - self._last_forced_refresh_at) < 10.0 and self._jwks_by_kid:
            # Rate-limit forced refreshes to prevent JWKS cache stampede / outbound DoS
            return

        jwks = _fetch_jwks(self.jwks_url, self.jwks_timeout_s)
        self._jwks_by_kid = {
            str(key.get("kid")): key
            for key in jwks["keys"]
            if isinstance(key, dict) and key.get("kid")
        }
        self._jwks_fetched_at = now
        if force:
            self._last_forced_refresh_at = now

    def _get_jwk(self, kid: str) -> dict[str, Any]:
        self._refresh_jwks()
        jwk = self._jwks_by_kid.get(kid)
        if jwk is None:
            self._refresh_jwks(force=True)
            jwk = self._jwks_by_kid.get(kid)
        if jwk is None:
            raise JWTAuthError(f"unknown key id: {kid}")
        return jwk

    def _rs256_verify(self, token: str) -> dict[str, Any]:
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding
        except ImportError as e:
            raise JWTAuthError("cryptography is required for RS256 verification") from e

        header, payload, signing_input, signature = _decode_unverified(token)
        if header.get("alg") != "RS256":
            raise JWTAuthError(f"unexpected alg: {header.get('alg')}")

        kid = str(header.get("kid") or "").strip()
        if not kid:
            raise JWTAuthError("missing key id (kid)")

        public_key = _rsa_public_key_from_jwk(self._get_jwk(kid))
        try:
            public_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature as err:
            raise JWTAuthError("invalid signature") from err
        except Exception as err:
            raise JWTAuthError(f"RS256 verification failed: {err}") from err

        return payload

    def verify(self, token: str) -> dict[str, Any]:
        """Verify signature, exp, nbf, iss, aud.  Return claims."""
        if not token:
            raise JWTAuthError("empty token")

        if self.alg == "HS256":
            claims = _hs256_verify(token, self.dev_secret)
        elif self.alg == "RS256":
            claims = self._rs256_verify(token)
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

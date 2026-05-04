"""Zero-trust identity, tenancy, and consent (Phase 14).

This package provides the authentication layer for the URA Chatbot.
Every `/v1/*` endpoint can optionally require a verified JWT via
:func:`dependencies.require_user`; every agentic path threads the
resolved ``AuthContext`` into the pipeline so tool visibility,
memory retrieval, and audit logging can all key on `user_id` and
`tenant_id`.

Design goals (2026 standard, see docs/URA_Chatbot_Roadmap_2026_Enhanced.md):

- **OIDC-ready** — the verifier accepts RS256 JWTs from an OIDC
  provider (Keycloak 26+) or HS256 tokens from a dev issuer.
- **DPoP-friendly** — the token envelope supports the optional
  ``cnf`` claim for token-binding; the verifier tolerates both
  bound and unbound tokens during rollout.
- **Tenant-first** — every token carries a ``tenant_id`` claim so
  row-level security can be enforced downstream.
- **Consent-aware** — `AuthContext` carries active consent
  purposes (loaded from ``consent_receipts``) so tools and memory
  layers can gate retrieval on explicit user consent.
- **No secrets in env** by default — production loads RS256
  public keys from a JWKS URL; dev uses an HS256 shared secret
  for local testing only.

Feature flags:
    FLAG_AUTH_REQUIRED  — if true, /v1/* endpoints reject unauthenticated
                          requests. Default false in dev, forced on by
                          production validation/defaults.
    FLAG_MULTI_TENANT   — if true, rows are scoped by tenant_id. Default false
                          in dev, forced on in production.
"""

from __future__ import annotations

from .dependencies import AuthContext, current_user, optional_user, require_role, require_user
from .jwt_auth import JWTAuthError, JWTVerifier, make_dev_token
from .models import AuthUser, ConsentReceipt, UserProfile

__all__ = [
    "AuthContext",
    "AuthUser",
    "ConsentReceipt",
    "JWTAuthError",
    "JWTVerifier",
    "UserProfile",
    "current_user",
    "make_dev_token",
    "optional_user",
    "require_role",
    "require_user",
]

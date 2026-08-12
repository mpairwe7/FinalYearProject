"""FastAPI dependencies for auth resolution and role gating.

Usage inside an endpoint::

    @app.get("/v1/me/profile")
    def get_profile(user: AuthContext = Depends(require_user)) -> UserProfile:
        ...

or for optional auth (public endpoint that personalizes if signed in)::

    @app.get("/v1/chat")
    def chat(ctx: AuthContext = Depends(current_user)) -> ChatResponse:
        if ctx.is_authenticated:
            # richer path
        ...
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, Header, HTTPException, Request

from ..flags import flags
from .jwt_auth import JWTAuthError, JWTVerifier
from .models import AuthUser

logger = logging.getLogger(__name__)

_verifier: JWTVerifier | None = None

# ---------------------------------------------------------------------------
# Role resolution
#
# Real providers do not put roles where our dev tokens do. Keycloak nests them
# under ``realm_access.roles`` (realm roles) or ``resource_access.<client>.roles``
# (client roles); Entra ID and Okta commonly use ``groups``. Only our own
# ``make_dev_token`` emits a flat ``role`` string.
#
# Probing the known shapes in order keeps a standards-compliant IdP working with
# no bespoke protocol mapper. Relying on a mapper instead would mean every new
# tenant needs custom provider config, and a missing mapper degrades every
# officer to "public" with nothing in the logs to say why.
#
# ``OIDC_ROLE_CLAIM`` overrides the probe with an explicit dot-path when a
# provider puts roles somewhere else entirely.
# ---------------------------------------------------------------------------
OIDC_ROLE_CLAIM = os.getenv("OIDC_ROLE_CLAIM", "")

_DEFAULT_ROLE_CLAIM_PATHS = (
    "role",  # our dev tokens
    "roles",  # Entra ID app roles, generic
    "realm_access.roles",  # Keycloak realm roles
    "groups",  # Entra ID / Okta group claim
    "permissions",  # Auth0 with RBAC "add permissions to access token"
)

# Mirrors the Literal on AuthUser.role. Anything outside this set is not a role
# we understand — a provider's own vocabulary ("offline_access", "default-roles-ura")
# must not reach the model, which would raise ValidationError and 500 the request.
_KNOWN_ROLES = ("public", "verified_taxpayer", "ura_staff", "ura_admin", "ura_auditor")

# A token can legitimately carry several of ours at once (an admin who is also on
# the queue). Resolve to the widest, so access does not depend on claim ordering.
_ROLE_PRECEDENCE = ("ura_admin", "ura_auditor", "ura_staff", "verified_taxpayer", "public")


def _claim_at_path(claims: dict[str, Any], path: str) -> Any:
    """Resolve a claim by name, or by dot-path for nested claims. Missing → None.

    A literal match is tried first because namespaced claim names contain dots:
    Auth0 emits roles as ``https://ura.go.ug/roles`` and splitting that on "."
    would look for a "https://ura" key. Only fall back to walking when the whole
    string is not itself a claim.
    """
    if path in claims:
        return claims[path]

    node: Any = claims
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
        if node is None:
            return None
    return node


def _role_candidates(claims: dict[str, Any], audience: str) -> list[str]:
    """Collect every role-ish string the token offers, in probe order."""
    paths: list[str] = []
    if OIDC_ROLE_CLAIM:
        paths.append(OIDC_ROLE_CLAIM)
    else:
        paths.extend(_DEFAULT_ROLE_CLAIM_PATHS)
        if audience:
            # Keycloak client roles live under the client id the token was issued for.
            paths.append(f"resource_access.{audience}.roles")

    found: list[str] = []
    for path in paths:
        value = _claim_at_path(claims, path)
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, (list, tuple)):
            found.extend(str(v) for v in value if isinstance(v, (str, int)))
    return found


def resolve_role(claims: dict[str, Any], audience: str = "") -> str:
    """Pick the app role a verified token grants. Unknown vocabulary → "public".

    Never raises: an authenticated user with roles we don't recognise is a
    legitimate public user, not a server error.
    """
    held = set()
    for raw in _role_candidates(claims, audience):
        # Keycloak and Entra emit group claims as paths ("/ura-admin"); hyphens
        # are also common where our enum uses underscores.
        name = str(raw).strip().lstrip("/").lower().replace("-", "_")
        if name in _KNOWN_ROLES:
            held.add(name)

    for role in _ROLE_PRECEDENCE:
        if role in held:
            return role
    return "public"


def _get_verifier() -> JWTVerifier:
    """Lazy-init singleton verifier so tests can override env before first use."""
    global _verifier
    if _verifier is None:
        _verifier = JWTVerifier()
    return _verifier


def reset_verifier() -> None:
    """Testing hook — forces re-reading env on next call."""
    global _verifier
    _verifier = None


# ---------------------------------------------------------------------------
# AuthContext — what endpoints actually receive
# ---------------------------------------------------------------------------
@dataclass
class AuthContext:
    """Request-scoped auth state.

    ``authenticated`` is False for anonymous requests (legal when
    ``FLAG_AUTH_REQUIRED`` is off).  ``user`` is None in that case.
    """

    authenticated: bool = False
    user: AuthUser | None = None
    # Raw claims for audit logging / debugging
    claims: dict[str, Any] = field(default_factory=dict)

    @property
    def user_id(self) -> str:
        return self.user.user_id if self.user else ""

    @property
    def tenant_id(self) -> str:
        return self.user.tenant_id if self.user else "default"

    @property
    def is_authenticated(self) -> bool:
        return self.authenticated

    @property
    def role(self) -> str:
        return self.user.role if self.user else "public"

    def has_purpose(self, purpose: str) -> bool:
        """Does the user have an active consent for this purpose?"""
        if not self.user:
            return False
        return purpose in self.user.granted_purposes


def _claims_to_user(claims: dict[str, Any]) -> AuthUser:
    """Map a JWT claims dict into an AuthUser."""
    granted = claims.get("granted_purposes", [])
    if not isinstance(granted, list):
        granted = []
    return AuthUser(
        user_id=str(claims.get("sub", "")),
        tenant_id=str(claims.get("tenant_id", "default")),
        email=str(claims.get("email", "")),
        role=resolve_role(claims, _get_verifier().audience),
        locale=str(claims.get("locale", "en")),
        granted_purposes=[str(p) for p in granted],
        token_issued_at=float(claims.get("iat", 0)),
        token_expires_at=float(claims.get("exp", 0)),
    )


def _anonymous_context(request: Request) -> AuthContext:
    ctx = AuthContext()
    request.state.auth = ctx
    return ctx


def _resolve_bearer_context(request: Request, authorization: str) -> AuthContext:
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = _get_verifier().verify(token)
    except JWTAuthError as e:
        logger.info("JWT rejected: %s", e)
        raise HTTPException(status_code=401, detail=f"invalid token: {e}") from e

    user = _claims_to_user(claims)
    ctx = AuthContext(authenticated=True, user=user, claims=claims)
    request.state.auth = ctx
    return ctx


# ---------------------------------------------------------------------------
# Dependency: optional_user (public endpoints with optional personalization)
# ---------------------------------------------------------------------------
def optional_user(
    request: Request,
    authorization: str | None = Header(None),
) -> AuthContext:
    """Resolve a bearer token if present, otherwise return anonymous context.

    Use this for public assistant endpoints that must remain usable without
    login. Invalid bearer tokens are still rejected so clients cannot silently
    proceed with a broken or spoofed identity.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return _anonymous_context(request)
    return _resolve_bearer_context(request, authorization)


# ---------------------------------------------------------------------------
# Dependency: current_user (auth-required aware)
# ---------------------------------------------------------------------------
def current_user(
    request: Request,
    authorization: str | None = Header(None),
) -> AuthContext:
    """Resolve auth context from the Authorization header.

    - No header → anonymous AuthContext when auth is optional.
    - No header with ``FLAG_AUTH_REQUIRED=true`` → 401.
    - Invalid token → 401.
    - Valid token → authenticated AuthContext bound to request.state.

    Raises on missing tokens when production/auth-required mode is enabled;
    use ``require_user`` for endpoints that are private in every environment.
    """
    ctx = AuthContext()

    if not authorization or not authorization.lower().startswith("bearer "):
        # Legacy path: the existing session_id header becomes the "user_id"
        # shim under `FLAG_AUTH_REQUIRED=false` so we don't break callers
        # that only send X-Session-ID.  This is temporary — remove after
        # OIDC rollout.
        if flags.is_enabled("auth_required"):
            raise HTTPException(status_code=401, detail="authentication required")
        return _anonymous_context(request)

    return _resolve_bearer_context(request, authorization)


# ---------------------------------------------------------------------------
# Dependency: require_user (enforced)
# ---------------------------------------------------------------------------
def require_user(ctx: AuthContext = Depends(current_user)) -> AuthContext:
    """Require an authenticated user context.

    Use this on endpoints that are strictly private (e.g.
    ``/v1/me/profile``). Public endpoints that want optional
    personalization should depend on ``current_user`` instead.
    """
    if not ctx.authenticated:
        raise HTTPException(status_code=401, detail="authentication required")
    return ctx


# ---------------------------------------------------------------------------
# Role-based access
# ---------------------------------------------------------------------------
def require_role(*roles: str):
    """Return a dependency that 403s unless the user holds one of *roles*.

    Example::

        @app.get("/v1/admin/tickets")
        def list_tickets(ctx = Depends(require_role("ura_staff", "ura_admin"))):
            ...
    """
    allowed = set(roles)

    def _dep(ctx: AuthContext = Depends(require_user)) -> AuthContext:
        if ctx.role not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"role '{ctx.role}' not in {sorted(allowed)}",
            )
        return ctx

    return _dep

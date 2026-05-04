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
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, Header, HTTPException, Request

from ..flags import flags
from .jwt_auth import JWTAuthError, JWTVerifier
from .models import AuthUser

logger = logging.getLogger(__name__)

_verifier: JWTVerifier | None = None


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
        role=str(claims.get("role", "public")),
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

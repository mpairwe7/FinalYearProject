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

from .jwt_auth import JWTAuthError, JWTVerifier
from .models import AuthUser

logger = logging.getLogger(__name__)

AUTH_REQUIRED = os.getenv("FLAG_AUTH_REQUIRED", "false").lower() == "true"

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


# ---------------------------------------------------------------------------
# Dependency: current_user (optional)
# ---------------------------------------------------------------------------
def current_user(
    request: Request,
    authorization: str | None = Header(None),
) -> AuthContext:
    """Resolve auth context from the Authorization header.

    - No header → anonymous AuthContext (fine for public endpoints).
    - Invalid token → 401.
    - Valid token → authenticated AuthContext bound to request.state.

    Never raises when no token is present; use ``require_user`` if
    the endpoint must enforce authentication.
    """
    ctx = AuthContext()

    if not authorization or not authorization.lower().startswith("bearer "):
        # Legacy path: the existing session_id header becomes the "user_id"
        # shim under `FLAG_AUTH_REQUIRED=false` so we don't break callers
        # that only send X-Session-ID.  This is temporary — remove after
        # OIDC rollout.
        request.state.auth = ctx
        return ctx

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
# Dependency: require_user (enforced)
# ---------------------------------------------------------------------------
def require_user(ctx: AuthContext = Depends(current_user)) -> AuthContext:
    """Like ``current_user`` but 403s if ``FLAG_AUTH_REQUIRED`` and no user.

    Use this on endpoints that are strictly private (e.g.
    ``/v1/me/profile``, ``/v1/admin/*``).  Public endpoints that want
    optional enrichment should depend on ``current_user`` instead.
    """
    if AUTH_REQUIRED and not ctx.authenticated:
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

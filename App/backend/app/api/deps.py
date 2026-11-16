"""Request dependencies shared by the routers and main.py: the loaded models and the operator checks."""

from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request

from ..auth import AuthContext, current_user
from ..rag.pipeline import ChatModel
from ..speech_service import SpeechModel



# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------
def get_model(request: Request) -> ChatModel:
    """Retrieve the ChatModel from app state; 503 if unavailable."""
    model = getattr(request.app.state, "model", None)
    if model is None:
        raise HTTPException(status_code=503, detail="Model not initialized")
    return model


def get_speech_model(request: Request) -> SpeechModel:
    """Retrieve the SpeechModel from app state; 503 if unavailable."""
    speech = getattr(request.app.state, "speech", None)
    if speech is None:
        raise HTTPException(
            status_code=503,
            detail="Speech pipeline disabled or failed to initialise (set SPEECH_ENABLED=true)",
        )
    return speech


def experiment_log_fields(user_id: str = "", locale: str = "") -> dict[str, str]:
    """Flag variants + locale persisted on each turn (G26)."""
    from ..config.flags import flags

    return flags.experiment_log_fields(subject=user_id or None, locale=locale)


INDEX_API_KEY = os.getenv("INDEX_API_KEY", "")


def has_valid_ops_key(request: Request) -> bool:
    """Return True when the request presents the configured ops API key."""
    if not INDEX_API_KEY:
        return False
    auth = request.headers.get("Authorization", "")
    return hmac.compare_digest(auth, f"Bearer {INDEX_API_KEY}")


def require_ops_key(request: Request) -> None:
    """Require the configured bearer token for operator-only endpoints."""
    if not INDEX_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="INDEX_API_KEY not configured for operator endpoint",
        )
    if not has_valid_ops_key(request):
        raise HTTPException(status_code=403, detail="Invalid or missing INDEX_API_KEY")


def require_admin_access(
    request: Request,
) -> AuthContext:
    """Require an authenticated staff user or a valid operator API key.

    Ticket triage endpoints should not be public. During OIDC rollout we
    still allow the existing operator key as a break-glass path, but only
    when it is explicitly configured.

    A request with no login is a 401 whether or not that key is set: a staff
    sign-in opens these endpoints either way. Without the key it used to be a
    503 "configure OIDC staff auth or INDEX_API_KEY", which on the Space (OIDC
    configured, no key) told a prober the endpoint was down (2026-11-12).
    """
    if has_valid_ops_key(request):
        return AuthContext()

    ctx = current_user(request, request.headers.get("Authorization"))
    if not ctx.is_authenticated:
        raise HTTPException(status_code=401, detail="authentication required")
    if ctx.user and ctx.user.is_staff:
        return ctx
    raise HTTPException(status_code=403, detail="staff role required")

"""System connectors (EFRIS, Digital Tax Stamps, etc.) routes."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response

if TYPE_CHECKING:
    from ..auth import AuthContext

logger = logging.getLogger(__name__)

router = APIRouter(tags=["connectors"])


def get_admin_access(request: Request) -> AuthContext:
    from ..main import require_admin_access

    return require_admin_access(request)


def _require_staff_writer(ctx: AuthContext) -> None:
    if ctx.user and ctx.role not in ("ura_staff", "ura_admin"):
        raise HTTPException(status_code=403, detail="read-only role")


@router.get("/v1/connectors")
def list_system_connectors(request: Request, response: Response) -> dict[str, Any]:
    """List the taxpayer-safe chat catalog or staff-only connector health."""
    response.headers["Cache-Control"] = "private, no-store"
    if request.query_params.get("view") == "chat":
        response.headers["Vary"] = "Authorization"
        from .. import database as db
        from ..auth.dependencies import optional_user
        from ..mcp import get_client
        from ..mcp.chat_connectors import chat_connector_catalog

        ctx = optional_user(request, request.headers.get("Authorization"))
        sess_id = (request.headers.get("X-Session-ID") or "").strip()
        is_authed = bool(getattr(ctx, "authenticated", False))
        owner_key = (
            db.conversation_state_key(
                sess_id or None,
                user_id=getattr(ctx, "user_id", None) if is_authed else None,
                tenant_id=getattr(ctx, "tenant_id", "default") or "default",
            )
            if (sess_id or is_authed)
            else ""
        )
        return chat_connector_catalog(
            get_client(),
            user_role=ctx.role,
            granted_purposes=ctx.user.granted_purposes if ctx.user else [],
            owner_key=owner_key,
        )

    get_admin_access(request)
    try:
        from ..plugins import get_orchestrator

        orchestrator = get_orchestrator()
        return {
            "ok": True,
            "live": False,
            "mode": "simulation",
            "connectors": orchestrator.get_connectors_summary(),
            "health": orchestrator.health_check(),
        }
    except Exception as exc:
        logger.exception("Failed to retrieve system connectors: %s", exc)
        return {
            "ok": True,
            "live": False,
            "mode": "simulation",
            "connectors": [],
            "health": {"all_healthy": True, "plugin_count": 0, "status": "degraded"},
        }


@router.get("/v1/connectors/diagnostics")
def connector_onboarding_diagnostics(
    response: Response,
    _ctx: AuthContext = Depends(get_admin_access),
) -> dict[str, Any]:
    """Why each bound MCP server is, or is not, offered in taxpayer chat.

    The staff "Add connector" flow cannot register a server (that is a
    reviewed deployment change), but it can say what the deployment did:
    which servers are bound, whether they answer, how the link
    authenticates, and — per operation — what keeps it out of the chat
    picker. Built from the same gate the chat uses; no URL, credential or
    schema leaves the server.
    """
    del _ctx
    response.headers["Cache-Control"] = "private, no-store"
    from ..mcp import get_client
    from ..mcp.chat_connectors import connector_diagnostics

    return connector_diagnostics(get_client())


@router.post("/v1/connectors/{name}/toggle")
def toggle_system_connector(
    name: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    ctx: AuthContext = Depends(get_admin_access),
) -> dict[str, Any]:
    """Enable or disable a built-in connector from the staff console."""
    _require_staff_writer(ctx)
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(
            status_code=409,
            detail="Local simulator connectors cannot be enabled in production.",
        )
    from ..plugins import get_orchestrator

    orchestrator = get_orchestrator()
    enable = payload.get("enable", True)
    if not isinstance(enable, bool):
        raise HTTPException(status_code=422, detail="enable must be a boolean")
    return orchestrator.toggle_connector(name, enable)


@router.get("/v1/connectors/{name}/records")
def inspect_connector_database(
    name: str,
    limit: int = Query(5, ge=1, le=50),
    _ctx: AuthContext = Depends(get_admin_access),
) -> dict[str, Any]:
    """Retire raw connector record inspection; use aggregate connector health instead."""
    del name, limit, _ctx
    raise HTTPException(
        status_code=410,
        detail="Raw connector records are not exposed. Use /v1/connectors for aggregate simulator health.",
    )


@router.post("/v1/connectors/{name}/test", dependencies=[Depends(get_admin_access)])
def test_system_connector(name: str) -> dict[str, Any]:
    """Run an active diagnostic ping and capability handshake on the connector."""
    from ..plugins import get_orchestrator

    orchestrator = get_orchestrator()
    res = orchestrator.test_connector(name)
    if not res.get("ok") and "not found" in res.get("error", "").lower():
        raise HTTPException(status_code=404, detail=res["error"])
    return res


@router.post("/v1/connectors/{name}/configure")
def configure_system_connector(
    name: str,
    ctx: AuthContext = Depends(get_admin_access),
) -> None:
    """Reject runtime connector changes; reviewed configuration is deployment-only."""
    _require_staff_writer(ctx)
    del name
    raise HTTPException(
        status_code=410,
        detail="Runtime connector configuration is disabled. Configure reviewed servers through deployment settings.",
    )


@router.post("/v1/connectors/register", dependencies=[Depends(get_admin_access)])
def register_external_connector() -> None:
    """Fail closed: reviewed connector servers are provisioned at deployment time."""
    raise HTTPException(
        status_code=410,
        detail="Dynamic connector registration is disabled. Configure a reviewed server through deployment settings.",
    )


_CONNECTOR_NS_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_AUTH_RATE_LOCK = threading.Lock()
_AUTH_RATE_BUCKETS: dict[str, list[float]] = {}
_AUTH_RATE_WINDOW_S = 60.0
_AUTH_RATE_MAX_ATTEMPTS = 25


def _check_connector_auth_rate_limit(owner_key: str) -> None:
    now = time.monotonic()
    cutoff = now - _AUTH_RATE_WINDOW_S
    with _AUTH_RATE_LOCK:
        bucket = [ts for ts in _AUTH_RATE_BUCKETS.get(owner_key, []) if ts >= cutoff]
        if len(bucket) >= _AUTH_RATE_MAX_ATTEMPTS:
            _AUTH_RATE_BUCKETS[owner_key] = bucket
            raise HTTPException(
                status_code=429,
                detail="Too many authentication attempts. Please wait a minute and try again.",
            )
        bucket.append(now)
        _AUTH_RATE_BUCKETS[owner_key] = bucket


@router.post("/v1/connectors/{name}/auth")
def authenticate_connector_account(
    name: str,
    request: Request,
    response: Response,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Sign in, sign up, or disconnect a local simulation account for a chat connector."""
    response.headers["Cache-Control"] = "private, no-store"
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(
            status_code=409,
            detail="Local simulator account authentication is disabled in production.",
        )
    if not _CONNECTOR_NS_RE.fullmatch(name):
        raise HTTPException(status_code=422, detail="Invalid connector namespace.")

    from .. import database as db
    from ..auth.dependencies import optional_user
    from ..mcp.connector_accounts import (
        disconnect_connector_account,
        get_all_linked_accounts,
        login_connector_account,
        signup_connector_account,
    )

    ctx = optional_user(request, request.headers.get("Authorization"))
    sess_id = (request.headers.get("X-Session-ID") or "").strip()
    is_authed = bool(getattr(ctx, "authenticated", False))
    if not sess_id and not is_authed:
        raise HTTPException(
            status_code=400,
            detail="Session or user identity is required to link a connector account.",
        )
    owner_key = db.conversation_state_key(
        sess_id or None,
        user_id=getattr(ctx, "user_id", None) if is_authed else None,
        tenant_id=getattr(ctx, "tenant_id", "default") or "default",
    )

    action = str(payload.get("action") or "login").strip().lower()
    if action in ("login", "signup"):
        _check_connector_auth_rate_limit(owner_key)
    link_all = bool(payload.get("link_all", True))
    try:
        if action == "disconnect":
            disconnect_connector_account(owner_key, name)
            return {
                "ok": True,
                "namespace": name,
                "connected": False,
                "account": None,
                "linked_accounts": get_all_linked_accounts(owner_key),
            }
        if action == "signup":
            acc = signup_connector_account(
                owner_key=owner_key,
                namespace=name,
                entity_name=str(payload.get("entity_name") or ""),
                email=str(payload.get("email") or ""),
                password=str(payload.get("password") or ""),
                category=str(payload.get("category") or "INDIVIDUAL"),
                tin=str(payload.get("tin") or ""),
                brn=str(payload.get("brn") or ""),
                nin_or_passport=str(payload.get("nin_or_passport") or ""),
                mobile=str(payload.get("mobile") or ""),
                district=str(payload.get("district") or "Kampala"),
                link_all=link_all,
            )
            return {
                "ok": True,
                "namespace": name,
                "connected": True,
                "account": acc,
                "linked_accounts": get_all_linked_accounts(owner_key),
            }
        if action == "login":
            acc = login_connector_account(
                owner_key=owner_key,
                namespace=name,
                identifier=str(payload.get("identifier") or ""),
                password=str(payload.get("password") or ""),
                link_all=link_all,
            )
            return {
                "ok": True,
                "namespace": name,
                "connected": True,
                "account": acc,
                "linked_accounts": get_all_linked_accounts(owner_key),
            }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    raise HTTPException(status_code=422, detail="action must be 'login', 'signup', or 'disconnect'.")


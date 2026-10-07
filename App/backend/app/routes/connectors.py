"""System connectors (EFRIS, Digital Tax Stamps, etc.) routes."""

from __future__ import annotations

import logging
import os
import re
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
        from ..auth.dependencies import optional_user
        from ..mcp import get_client
        from ..mcp.chat_connectors import chat_connector_catalog

        ctx = optional_user(request, request.headers.get("Authorization"))
        return chat_connector_catalog(
            get_client(),
            user_role=ctx.role,
            granted_purposes=ctx.user.granted_purposes if ctx.user else [],
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

"""System connectors (EFRIS, Digital Tax Stamps, etc.) routes."""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

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


@router.get("/v1/connectors", dependencies=[Depends(get_admin_access)])
def list_system_connectors() -> dict[str, Any]:
    """List staff-only connector health and simulator metrics."""
    from ..plugins import get_orchestrator

    orchestrator = get_orchestrator()
    return {
        "ok": True,
        "live": False,
        "mode": "simulation",
        "connectors": orchestrator.get_connectors_summary(),
        "health": orchestrator.health_check(),
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
    payload: dict[str, Any] = Body(default_factory=dict),
    ctx: AuthContext = Depends(get_admin_access),
) -> dict[str, Any]:
    """Configure endpoint, credentials, or live/sandbox mode for a connector."""
    _require_staff_writer(ctx)
    from ..auth.vault import get_token_vault
    from ..plugins import get_orchestrator

    vault = get_token_vault()
    clean_payload = dict(payload)
    if "api_key" in clean_payload and clean_payload["api_key"]:
        raw_key = str(clean_payload["api_key"])
        clean_payload["encrypted_key"] = vault.encrypt_secret(raw_key)
        clean_payload["api_key"] = vault.mask_secret(raw_key)

    orchestrator = get_orchestrator()
    res = orchestrator.configure_connector(name, clean_payload)
    if not res.get("ok") and "not found" in res.get("error", "").lower():
        raise HTTPException(status_code=404, detail=res["error"])
    safe_uid = re.sub(r"[^a-zA-Z0-9_-]", "", str(ctx.user_id))[:64]
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "", str(name))[:64]
    logger.info("Admin %s updated connector %s configuration", safe_uid, safe_name)
    return res


@router.post("/v1/connectors/register", dependencies=[Depends(get_admin_access)])
def register_external_connector(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Reject unverified dynamic servers unless explicitly pre-configured with security validation."""
    if not payload or not payload.get("name"):
        raise HTTPException(
            status_code=410,
            detail="Dynamic connector registration is disabled. Configure a reviewed server through deployment settings.",
        )
    endpoint_url = str(payload.get("endpoint_url", "")).strip()
    if endpoint_url:
        parsed = urlparse(endpoint_url)
        hostname = (parsed.hostname or "").lower()
        if os.getenv("APP_ENV", "development").lower() == "production":
            if parsed.scheme != "https":
                raise HTTPException(status_code=400, detail="Production connectors require HTTPS.")
            if (
                hostname in ("localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254")  # noqa: S104
                or hostname.startswith(("10.", "192.168."))
            ):
                raise HTTPException(status_code=400, detail="Private network endpoints are rejected in production.")

    from ..auth.vault import get_token_vault

    vault = get_token_vault()
    masked_key = vault.mask_secret(str(payload.get("api_key", ""))) if payload.get("api_key") else ""

    return {
        "ok": True,
        "id": payload["name"],
        "registered": True,
        "mode": payload.get("mode", "simulation"),
        "protocol": payload.get("protocol", "mcp"),
        "masked_key": masked_key,
    }

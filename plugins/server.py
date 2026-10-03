"""Standalone FastAPI server for local URA connector simulators.

Enables independent containerized deployment of EFRIS, DTS, URSB, BWIMS,
TIN Registration, and Payment Systems. The service is an isolated prototype; it
requires a service key and confirmation plus idempotency for simulated writes.
"""

from __future__ import annotations

import hmac
import logging
import os
import sys
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

# Ensure repo root is on sys.path
_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request, status  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from plugins.orchestrator import get_orchestrator  # noqa: E402

logger = logging.getLogger(__name__)

app = FastAPI(
    title="URA Enterprise Systems & Connectors Gateway",
    description="Protected internal REST gateway for local EFRIS, DTS, URSB, BWIMS, TIN, and payment simulators.",
    version="1.0.0",
)

_EXPECTED_API_KEY = os.getenv("PLUGINS_API_KEY", "").strip()
_MCP_REPLAY_LOCK = threading.Lock()
_MCP_REPLAY_CACHE: dict[str, dict[str, Any]] = {}
_SIMULATION_NOTICE = (
    "Local simulator result only. No live URA, NIRA, URSB, EFRIS, BWIMS, bank, or payment service was contacted or updated."
)


def _require_simulators_enabled() -> None:
    """Keep local mock tools disabled in production, regardless of feature flags."""
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Local simulator connectors are never enabled in production",
        )


def _simulation_result(result: Any) -> Any:
    """Mark connector output so callers do not present it as live data."""
    if isinstance(result, dict):
        return {
            **result,
            "mode": "simulation",
            "live": False,
            "simulation_notice": _SIMULATION_NOTICE,
        }
    return result


def _verify_auth(authorization: str | None = None) -> None:
    """Require a configured service credential for connector data and actions."""
    if not _EXPECTED_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PLUGINS_API_KEY is not configured",
        )
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip(), _EXPECTED_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )


@app.middleware("http")
async def protect_plugin_apis(request: Request, call_next):  # noqa: ANN001
    """Keep the manifest, tool calls, and direct APIs closed without a service key."""
    if request.method != "OPTIONS" and request.url.path.startswith(("/mcp/", "/api/v1/")):
        try:
            _verify_auth(request.headers.get("Authorization"))
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


def _run_confirmed_rest_action(
    operation: str,
    payload: dict[str, Any],
    execute: Callable[[dict[str, Any]], Any],
) -> dict[str, Any]:
    """Require explicit confirmation and replay protection for REST writes."""
    _require_simulators_enabled()
    key = str(payload.get("idempotency_key") or "").strip()
    arguments = {
        name: value
        for name, value in payload.items()
        if name not in {"confirmed", "idempotency_key"}
    }
    if payload.get("confirmed") is not True or not key:
        key = key or uuid.uuid4().hex
        return {
            "ok": True,
            "submitted": False,
            "requires_confirmation": True,
            "proposal": {**arguments, "idempotency_key": key},
            "idempotency_key": key,
            "mode": "simulation",
            "live": False,
            "simulation_notice": _SIMULATION_NOTICE,
            "message": "No connector action has run. Review and confirm the proposal before retrying.",
        }

    replay_key = f"rest:{operation}:{key}"
    with _MCP_REPLAY_LOCK:
        cached = _MCP_REPLAY_CACHE.get(replay_key)
        if cached is not None:
            return {**cached, "replayed": True}
        result = execute(arguments)
        response = {
            "ok": bool(result.get("ok", True)) if isinstance(result, dict) else True,
            "submitted": True,
            "mode": "simulation",
            "live": False,
            "simulation_notice": _SIMULATION_NOTICE,
            "result": _simulation_result(result),
        }
        if response["ok"]:
            if len(_MCP_REPLAY_CACHE) >= 512:
                _MCP_REPLAY_CACHE.pop(next(iter(_MCP_REPLAY_CACHE)))
            _MCP_REPLAY_CACHE[replay_key] = response
        return response


# ---------------------------------------------------------------------------
# Health & Internal Tool Manifest
# ---------------------------------------------------------------------------


@app.get("/health", tags=["system"])
def health_check() -> dict[str, Any]:
    """Report local simulator readiness and aggregate fixture database status."""
    orchestrator = get_orchestrator()
    return {
        "status": "healthy",
        "service": "ura-plugins-server",
        "version": "1.0.0",
        "mode": "simulation",
        "live": False,
        "health": orchestrator.health_check(),
    }


@app.get("/mcp/manifest", tags=["mcp"])
def get_mcp_manifest(authorization: str | None = Header(None)) -> dict[str, Any]:
    """Return an internal tool manifest; this route is not an MCP JSON-RPC transport."""
    _verify_auth(authorization)
    orchestrator = get_orchestrator()
    tools = []
    for tool in orchestrator.get_all_tools():
        if hasattr(tool, "to_mcp_tool"):
            tools.append(tool.to_mcp_tool())
        else:
            tools.append({
                "name": tool.schema.name,
                "description": tool.schema.description,
                "inputSchema": tool.schema.parameters,
            })

    return {
        "manifest_schema_version": "1.0.0",
        "transport": "internal-rest",
        "mcp_compatible": False,
        "mode": "simulation",
        "live": False,
        "server_name": "ura-enterprise-connectors",
        "version": "1.0.0",
        "tools": tools,
        "connectors": orchestrator.get_connectors_summary(),
    }


class MCPCallRequest(BaseModel):
    name: str = Field(..., description="Tool name to invoke")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool parameters")
    confirmed: bool = Field(False, description="Explicit operator approval for a mutating action")
    idempotency_key: str = Field("", description="Required for confirmed writes")


@app.post("/mcp/call", tags=["mcp"])
def call_mcp_tool(
    body: MCPCallRequest,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Internal REST tool dispatcher; not a standard MCP ``tools/call`` method."""
    _verify_auth(authorization)
    _require_simulators_enabled()
    orchestrator = get_orchestrator()
    tool_map = {t.schema.name: t for t in orchestrator.get_all_tools()}

    tool = tool_map.get(body.name)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown tool '{body.name}'. Available: {list(tool_map.keys())}",
        )

    supplied_key = body.idempotency_key.strip()
    if tool.schema.requires_confirmation and (not body.confirmed or not supplied_key):
        proposal_key = supplied_key or uuid.uuid4().hex
        return {
            "ok": True,
            "submitted": False,
            "requires_confirmation": True,
            "proposal": {**body.arguments, "idempotency_key": proposal_key},
            "idempotency_key": proposal_key,
            "mode": "simulation",
            "live": False,
            "simulation_notice": _SIMULATION_NOTICE,
            "message": "No connector action has run. Review and confirm the proposal before retrying.",
        }

    tool_arguments = dict(body.arguments)
    tool_properties = set((tool.schema.parameters.get("properties") or {}).keys())
    for control in ("idempotency_key", "confirmed"):
        if control not in tool_properties:
            tool_arguments.pop(control, None)
    if "submit" not in tool_properties:
        tool_arguments.pop("submit", None)

    replay_key = f"{body.name}:{body.idempotency_key.strip()}" if tool.schema.requires_confirmation else ""
    if replay_key:
        with _MCP_REPLAY_LOCK:
            cached = _MCP_REPLAY_CACHE.get(replay_key)
            if cached is not None:
                return {**cached, "replayed": True}
            try:
                result = tool.execute(**tool_arguments)
                response = {
                    "ok": True,
                    "tool_name": body.name,
                    "mode": "simulation",
                    "live": False,
                    "simulation_notice": _SIMULATION_NOTICE,
                    "result": _simulation_result(result),
                }
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error executing confirmed remote tool %s", body.name)
                response = {"ok": False, "tool_name": body.name, "error": type(exc).__name__}
            if response["ok"]:
                if len(_MCP_REPLAY_CACHE) >= 512:
                    _MCP_REPLAY_CACHE.pop(next(iter(_MCP_REPLAY_CACHE)))
                _MCP_REPLAY_CACHE[replay_key] = response
            return response

    try:
        result = tool.execute(**tool_arguments)
        return {
            "ok": True,
            "tool_name": body.name,
            "mode": "simulation",
            "live": False,
            "simulation_notice": _SIMULATION_NOTICE,
            "result": _simulation_result(result),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error executing remote tool %s", body.name)
        return {"ok": False, "tool_name": body.name, "error": str(exc)}


# ---------------------------------------------------------------------------
# Direct REST Endpoints for Enterprise Systems
# ---------------------------------------------------------------------------


# --- 1. EFRIS ---
@app.get("/api/v1/efris/taxpayers/{tin}", tags=["efris"])
def efris_taxpayer_profile(tin: str) -> dict[str, Any]:
    from plugins.efris import EfrisClient
    return _simulation_result(EfrisClient().get_taxpayer_profile(tin))


@app.post("/api/v1/efris/invoices/issue", tags=["efris"])
def efris_issue_invoice(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from plugins.efris import EfrisClient
    return _run_confirmed_rest_action(
        "efris_issue_invoice",
        payload,
        lambda arguments: EfrisClient().issue_invoice(**arguments),
    )


@app.get("/api/v1/efris/invoices/verify/{fdn}", tags=["efris"])
def efris_verify_invoice(fdn: str, verification_code: str | None = None) -> dict[str, Any]:
    from plugins.efris import EfrisClient
    return _simulation_result(EfrisClient().verify_invoice(fdn=fdn, verification_code=verification_code))


# --- 2. DTS ---
@app.get("/api/v1/dts/stamps/verify/{stamp_code}", tags=["dts"])
def dts_verify_stamp(stamp_code: str) -> dict[str, Any]:
    from plugins.digital_tax_stamps import DigitalTaxStampsClient
    return _simulation_result(DigitalTaxStampsClient().verify_stamp(stamp_code))


@app.post("/api/v1/dts/stamps/order", tags=["dts"])
def dts_order_stamps(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from plugins.digital_tax_stamps import DigitalTaxStampsClient
    return _run_confirmed_rest_action(
        "dts_order_stamps",
        payload,
        lambda arguments: DigitalTaxStampsClient().order_stamps(**arguments),
    )


# --- 3. URSB ---
@app.get("/api/v1/ursb/business/search", tags=["ursb"])
def ursb_search(query: str = Query(...)) -> dict[str, Any]:
    from plugins.ursb import UrsbClient
    return _simulation_result(UrsbClient().search_business(query))


@app.post("/api/v1/ursb/business/register", tags=["ursb"])
def ursb_register(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from plugins.ursb import UrsbClient
    return _run_confirmed_rest_action(
        "ursb_register",
        payload,
        lambda arguments: UrsbClient().register_business(**arguments),
    )


# --- 4. BWIMS ---
@app.get("/api/v1/bwims/consignments/{entry_number}", tags=["bwims"])
def bwims_consignment(entry_number: str) -> dict[str, Any]:
    from plugins.bwims import BwimsClient
    return _simulation_result(BwimsClient().get_consignment(entry_number))


@app.get("/api/v1/bwims/warehouses/{warehouse_code}/inventory", tags=["bwims"])
def bwims_inventory(warehouse_code: str, importer_tin: str | None = None) -> dict[str, Any]:
    from plugins.bwims import BwimsClient
    return _simulation_result(BwimsClient().get_inventory(warehouse_code=warehouse_code, importer_tin=importer_tin))


# --- 5. TIN Registration ---
@app.get("/api/v1/tin/taxpayers/search", tags=["tin"])
def tin_search(query: str = Query(...)) -> dict[str, Any]:
    from plugins.tin_registration import TinRegistrationClient
    return _simulation_result(TinRegistrationClient().search_taxpayer(query))


@app.post("/api/v1/tin/taxpayers/apply-instant", tags=["tin"])
def tin_apply_instant(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from plugins.tin_registration import TinRegistrationClient
    return _run_confirmed_rest_action(
        "tin_apply_instant",
        payload,
        lambda arguments: TinRegistrationClient().apply_instant_individual_tin(**arguments),
    )


# --- 6. Payment System ---
@app.post("/api/v1/payments/prn/generate", tags=["payments"])
def payments_generate_prn(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from plugins.payment_system import PaymentClient
    return _run_confirmed_rest_action(
        "payments_generate_prn",
        payload,
        lambda arguments: PaymentClient().generate_prn(**arguments),
    )


@app.get("/api/v1/payments/prn/{prn}/status", tags=["payments"])
def payments_prn_status(prn: str) -> dict[str, Any]:
    from plugins.payment_system import PaymentClient
    return _simulation_result(PaymentClient().view_status(prn))


@app.post("/api/v1/payments/checkout", tags=["payments"])
def payments_checkout(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from plugins.payment_system import PaymentClient
    return _run_confirmed_rest_action(
        "payments_checkout",
        payload,
        lambda arguments: PaymentClient().process_checkout(**arguments),
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8200"))
    uvicorn.run("plugins.server:app", host="0.0.0.0", port=port, reload=True)  # noqa: S104

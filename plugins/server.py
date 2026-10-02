"""Standalone FastAPI Server hosting URA Enterprise Systems & MCP Gateway.

Enables independent containerized deployment of EFRIS, DTS, URSB, BWIMS,
TIN Registration, and Payment Systems. Exposes REST and MCP (Model Context Protocol)
endpoints for remote agent registration (similar to Stripe & GitHub on Grok).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

# Ensure repo root is on sys.path
_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from fastapi import Body, FastAPI, Header, HTTPException, Query, status  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from plugins.orchestrator import get_orchestrator  # noqa: E402

logger = logging.getLogger(__name__)

app = FastAPI(
    title="URA Enterprise Systems & Connectors Gateway",
    description="Independent containerized API hosting EFRIS, DTS, URSB, BWIMS, TIN, and Payment services with MCP integration.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_EXPECTED_API_KEY = os.getenv("PLUGINS_API_KEY", "").strip()


def _verify_auth(authorization: str | None = None) -> None:
    """Verify Bearer token if PLUGINS_API_KEY is configured."""
    if not _EXPECTED_API_KEY:
        return
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )
    token = authorization.replace("Bearer ", "").strip()
    if token != _EXPECTED_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )


# ---------------------------------------------------------------------------
# Health & MCP Manifest Discovery
# ---------------------------------------------------------------------------


@app.get("/health", tags=["system"])
def health_check() -> dict[str, Any]:
    """Health check endpoint reporting status of all 6 independent databases."""
    orchestrator = get_orchestrator()
    return {
        "status": "healthy",
        "service": "ura-plugins-server",
        "version": "1.0.0",
        "health": orchestrator.health_check(),
    }


@app.get("/mcp/manifest", tags=["mcp"])
def get_mcp_manifest(authorization: str | None = Header(None)) -> dict[str, Any]:
    """Return the MCP tool manifest for remote registration with LLM agents (Grok/Claude/Qwen standard)."""
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
        "schema_version": "2026-07-28",
        "server_name": "ura-enterprise-connectors",
        "version": "1.0.0",
        "tools": tools,
        "connectors": orchestrator.get_connectors_summary(),
    }


class MCPCallRequest(BaseModel):
    name: str = Field(..., description="Tool name to invoke")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool parameters")


@app.post("/mcp/call", tags=["mcp"])
def call_mcp_tool(
    body: MCPCallRequest,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Remote MCP tool dispatch endpoint."""
    _verify_auth(authorization)
    orchestrator = get_orchestrator()
    tool_map = {t.schema.name: t for t in orchestrator.get_all_tools()}

    tool = tool_map.get(body.name)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown tool '{body.name}'. Available: {list(tool_map.keys())}",
        )

    try:
        result = tool.execute(**body.arguments)
        return {"ok": True, "tool_name": body.name, "result": result}
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
    return EfrisClient().get_taxpayer_profile(tin)


@app.post("/api/v1/efris/invoices/issue", tags=["efris"])
def efris_issue_invoice(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from plugins.efris import EfrisClient
    return EfrisClient().issue_invoice(**payload)


@app.get("/api/v1/efris/invoices/verify/{fdn}", tags=["efris"])
def efris_verify_invoice(fdn: str, verification_code: str | None = None) -> dict[str, Any]:
    from plugins.efris import EfrisClient
    return EfrisClient().verify_invoice(fdn=fdn, verification_code=verification_code)


# --- 2. DTS ---
@app.get("/api/v1/dts/stamps/verify/{stamp_code}", tags=["dts"])
def dts_verify_stamp(stamp_code: str) -> dict[str, Any]:
    from plugins.digital_tax_stamps import DigitalTaxStampsClient
    return DigitalTaxStampsClient().verify_stamp(stamp_code)


@app.post("/api/v1/dts/stamps/order", tags=["dts"])
def dts_order_stamps(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from plugins.digital_tax_stamps import DigitalTaxStampsClient
    return DigitalTaxStampsClient().order_stamps(**payload)


# --- 3. URSB ---
@app.get("/api/v1/ursb/business/search", tags=["ursb"])
def ursb_search(query: str = Query(...)) -> dict[str, Any]:
    from plugins.ursb import UrsbClient
    return UrsbClient().search_business(query)


@app.post("/api/v1/ursb/business/register", tags=["ursb"])
def ursb_register(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from plugins.ursb import UrsbClient
    return UrsbClient().register_business(**payload)


# --- 4. BWIMS ---
@app.get("/api/v1/bwims/consignments/{entry_number}", tags=["bwims"])
def bwims_consignment(entry_number: str) -> dict[str, Any]:
    from plugins.bwims import BwimsClient
    return BwimsClient().get_consignment(entry_number)


@app.get("/api/v1/bwims/warehouses/{warehouse_code}/inventory", tags=["bwims"])
def bwims_inventory(warehouse_code: str, importer_tin: str | None = None) -> dict[str, Any]:
    from plugins.bwims import BwimsClient
    return BwimsClient().get_inventory(warehouse_code=warehouse_code, importer_tin=importer_tin)


# --- 5. TIN Registration ---
@app.get("/api/v1/tin/taxpayers/search", tags=["tin"])
def tin_search(query: str = Query(...)) -> dict[str, Any]:
    from plugins.tin_registration import TinRegistrationClient
    return TinRegistrationClient().search_taxpayer(query)


@app.post("/api/v1/tin/taxpayers/apply-instant", tags=["tin"])
def tin_apply_instant(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from plugins.tin_registration import TinRegistrationClient
    return TinRegistrationClient().apply_instant_individual_tin(**payload)


# --- 6. Payment System ---
@app.post("/api/v1/payments/prn/generate", tags=["payments"])
def payments_generate_prn(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from plugins.payment_system import PaymentClient
    return PaymentClient().generate_prn(**payload)


@app.get("/api/v1/payments/prn/{prn}/status", tags=["payments"])
def payments_prn_status(prn: str) -> dict[str, Any]:
    from plugins.payment_system import PaymentClient
    return PaymentClient().view_status(prn)


@app.post("/api/v1/payments/checkout", tags=["payments"])
def payments_checkout(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from plugins.payment_system import PaymentClient
    return PaymentClient().process_checkout(**payload)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8200"))
    uvicorn.run("plugins.server:app", host="0.0.0.0", port=port, reload=True)  # noqa: S104

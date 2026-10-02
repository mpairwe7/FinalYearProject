"""Agentic connector bridging URA Digital Tax Stamps sample system into the URA agentic tool framework."""

from __future__ import annotations

import logging
from typing import Any

from plugins.base import SystemConnector, Tool, ToolSchema

from .client import DigitalTaxStampsClient
from .service import DigitalTaxStampsService

logger = logging.getLogger(__name__)


class DtsVerifyStampTool(Tool):
    """Tool for authenticating digital tax stamps on gazetted commodities (Kakasa protocol)."""

    def __init__(self, client: DigitalTaxStampsClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="dts_verify_stamp",
            description=(
                "Verify whether a physical or digital tax stamp affixed to excisable goods "
                "(beers, spirits, wines, bottled water, soda, tobacco, cement, sugar, cooking oil, juices) "
                "is authentic and tax-compliant via the URA Kakasa verification protocol."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "stamp_code": {
                        "type": "string",
                        "description": "Unique code or QR content scanned from the digital tax stamp.",
                    }
                },
                "required": ["stamp_code"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "stamp_code": {"type": "string"},
                    "is_authentic": {"type": "boolean"},
                    "status": {"type": "string"},
                    "product_category": {"type": "string"},
                    "brand_name": {"type": "string"},
                    "manufacturer_name": {"type": "string"},
                    "message": {"type": "string"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="low",
            namespace="digital_tax_stamps",
            read_only=True,
            idempotent=True,
            open_world=False,
        )

    def execute(self, stamp_code: str = "", **kwargs: Any) -> dict[str, Any]:
        return self._client.verify_stamp(stamp_code=stamp_code)


class DtsOrderStampsTool(Tool):
    """Tool for ordering digital tax stamps for gazetted products and generating PRN."""

    def __init__(self, client: DigitalTaxStampsClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="dts_order_stamps",
            description=(
                "Order digital tax stamps for gazetted products and generate a Payment Registration Number (PRN). "
                "Calculates official statutory stamp tariffs and designates pickup at SICPA Uganda (Ntinda)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "taxpayer_tin": {
                        "type": "string",
                        "description": "10-digit TIN of the registered manufacturer or importer.",
                    },
                    "product_category": {
                        "type": "string",
                        "enum": [
                            "BEER",
                            "SPIRITS",
                            "WINE",
                            "BOTTLED_WATER",
                            "SODA",
                            "TOBACCO",
                            "CEMENT",
                            "SUGAR",
                            "COOKING_OIL",
                            "JUICES",
                        ],
                        "description": "Gazetted product category requiring digital stamps.",
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "Number of stamps ordered.",
                    },
                    "packaging_type": {
                        "type": "string",
                        "enum": ["BOTTLE", "CAN", "PACKET", "BAG", "CARTON"],
                        "description": "Packaging unit format.",
                    },
                    "facility_location": {
                        "type": "string",
                        "description": "Factory line or customs bonded warehouse location.",
                    },
                },
                "required": ["taxpayer_tin", "product_category", "quantity"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "order_id": {"type": "string"},
                    "prn": {"type": "string"},
                    "total_amount_ugx": {"type": "number"},
                    "collection_point": {"type": "string"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="medium",
            namespace="digital_tax_stamps",
            required_scopes=("ura_account_access",),
            allowed_roles=("verified_taxpayer", "ura_staff", "ura_admin"),
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=False,
        )

    def execute(
        self,
        taxpayer_tin: str,
        product_category: str,
        quantity: int,
        packaging_type: str = "BOTTLE",
        facility_location: str = "MAIN_FACTORY",
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self._client.order_stamps(
            taxpayer_tin=taxpayer_tin,
            product_category=product_category,
            quantity=quantity,
            packaging_type=packaging_type,
            facility_location=facility_location,
        )


class DtsActivateStampsTool(Tool):
    """Tool for activating stamps on factory packaging lines or declaring damaged stamps."""

    def __init__(self, client: DigitalTaxStampsClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="dts_activate_stamps",
            description=(
                "Activate ordered digital tax stamps at packaging line controllers prior to market release, "
                "or declare damaged/spoiled stamps destroyed during packaging line jams for reconciliation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["activate", "declare_damaged"],
                        "description": "'activate' to commission stamps, or 'declare_damaged' to report spoiled rolls.",
                    },
                    "order_id": {
                        "type": "string",
                        "description": "DTS Order ID (required for 'activate').",
                    },
                    "line_id": {
                        "type": "string",
                        "description": "Packaging line applicator identifier.",
                    },
                    "stamp_serials": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of stamp serial numbers to activate.",
                    },
                    "taxpayer_tin": {
                        "type": "string",
                        "description": "10-digit TIN (required for 'declare_damaged').",
                    },
                    "damaged_serials": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of spoiled stamp serial numbers (required for 'declare_damaged').",
                    },
                    "incident_reason": {
                        "type": "string",
                        "description": "Reason for spoiled stamps (e.g., PACKAGING_LINE_JAM).",
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "action": {"type": "string"},
                    "data": {"type": "object"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="medium",
            namespace="digital_tax_stamps",
            required_scopes=("ura_account_access",),
            allowed_roles=("verified_taxpayer", "ura_staff", "ura_admin"),
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=False,
        )

    def execute(
        self,
        action: str,
        order_id: str = "",
        line_id: str = "",
        stamp_serials: list[str] | None = None,
        taxpayer_tin: str = "",
        damaged_serials: list[str] | None = None,
        incident_reason: str = "PACKAGING_LINE_JAM",
        **kwargs: Any,
    ) -> dict[str, Any]:
        action_clean = str(action or "").lower().strip()
        if action_clean == "activate":
            if not order_id or not line_id:
                return {"ok": False, "error": "order_id and line_id are required for activation"}
            if not stamp_serials:
                return {"ok": False, "error": "stamp_serials list is required for activation"}
            res = self._client.activate_stamps(
                order_id=order_id,
                line_id=line_id,
                stamp_serials=stamp_serials,
            )
            return {"ok": res.get("ok", False), "action": "activate", "data": res}
        elif action_clean == "declare_damaged":
            if not taxpayer_tin:
                return {"ok": False, "error": "taxpayer_tin is required for declaring damaged stamps"}
            if not damaged_serials:
                return {"ok": False, "error": "damaged_serials list is required"}
            res = self._client.declare_damaged_stamps(
                taxpayer_tin=taxpayer_tin,
                damaged_serials=damaged_serials,
                incident_reason=incident_reason,
                line_id=line_id or "DEFAULT_LINE",
            )
            return {"ok": res.get("ok", False), "action": "declare_damaged", "data": res}
        else:
            return {"ok": False, "error": f"Unknown action '{action}'. Use 'activate' or 'declare_damaged'."}


class DtsTaxpayerStatusTool(Tool):
    """Tool for checking taxpayer DTS registration, packaging lines, and active stamp quota."""

    def __init__(self, client: DigitalTaxStampsClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="dts_taxpayer_status",
            description=(
                "Query manufacturer or importer Digital Tax Stamps registration, accredited packaging lines, "
                "active stamp inventory, and compliance standing."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tin": {
                        "type": "string",
                        "description": "10-digit Tax Identification Number (TIN).",
                    }
                },
                "required": ["tin"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "tin": {"type": "string"},
                    "registered": {"type": "boolean"},
                    "profile": {"type": "object"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="low",
            namespace="digital_tax_stamps",
            read_only=True,
            idempotent=True,
            open_world=False,
        )

    def execute(self, tin: str = "", **kwargs: Any) -> dict[str, Any]:
        return self._client.get_taxpayer_profile(tin=tin)


class DigitalTaxStampsConnector(SystemConnector):
    """System connector for URA Digital Tax Stamps (DTS)."""

    def __init__(self, service: DigitalTaxStampsService | None = None) -> None:
        self._service = service or DigitalTaxStampsService()
        self._client = DigitalTaxStampsClient(service=self._service)
        self._tools: list[Tool] = [
            DtsVerifyStampTool(self._client),
            DtsOrderStampsTool(self._client),
            DtsActivateStampsTool(self._client),
            DtsTaxpayerStatusTool(self._client),
        ]
        self._healthy = False

    @property
    def system_name(self) -> str:
        return "digital_tax_stamps"

    @property
    def client(self) -> DigitalTaxStampsClient:
        return self._client

    @property
    def service(self) -> DigitalTaxStampsService:
        return self._service

    def initialize(self) -> bool:
        self._healthy = self._client.ping()
        logger.info("DigitalTaxStampsConnector initialized (healthy=%s)", self._healthy)
        return self._healthy

    def is_healthy(self) -> bool:
        return self._healthy and self._client.ping()

    def get_tools(self) -> list[Tool]:
        return list(self._tools)

    def get_status(self) -> dict[str, Any]:
        return {
            "system_name": self.system_name,
            "display_name": "Digital Tax Stamps (DTS) / Kakasa Track & Trace",
            "healthy": self.is_healthy(),
            "live_mode": self._client.is_live,
            "tool_count": len(self._tools),
            "tools": [t.schema.name for t in self._tools],
            "database": self._service.get_stats(),
        }

    def shutdown(self) -> None:
        self._healthy = False
        logger.info("DigitalTaxStampsConnector shut down")

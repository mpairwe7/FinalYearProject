"""Agentic connector bridging URA BWIMS sample system into the URA agentic tool framework."""

from __future__ import annotations

import logging
from typing import Any

from plugins.base import SystemConnector, Tool, ToolSchema

from .client import BwimsClient
from .service import BwimsService

logger = logging.getLogger(__name__)


class BwimsConsignmentStatusTool(Tool):
    """Tool for tracking customs bonded cargo under the IM7 regime."""

    def __init__(self, client: BwimsClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="bwims_consignment_status",
            description=(
                "Read sample consignment and warehouse fixtures from the local BWIMS simulator. It does not check a real "
                "customs entry, determine a legal deadline, or update customs status."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "entry_number": {
                        "type": "string",
                        "description": "Customs IM7 warehousing entry number (e.g. 2026-ASY-IM7-88912).",
                    }
                },
                "required": ["entry_number"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "found": {"type": "boolean"},
                    "consignment": {"type": "object"},
                    "days_in_storage": {"type": "integer"},
                    "is_overstayed": {"type": "boolean"},
                    "message": {"type": "string"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="low",
            namespace="bwims",
            read_only=True,
            idempotent=True,
            open_world=False,
        )

    def execute(self, entry_number: str = "", **kwargs: Any) -> dict[str, Any]:
        return self._client.get_consignment(entry_number=entry_number)


class BwimsWarehouseInventoryTool(Tool):
    """Tool for auditing stock balances in customs bonded warehouses."""

    def __init__(self, client: BwimsClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="bwims_warehouse_inventory",
            description=(
                "Query all bonded consignments and stock balances currently stored inside a licensed "
                "customs bonded warehouse (e.g., WH-KLA-001, WH-JJA-002, WH-EBB-003)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "warehouse_code": {
                        "type": "string",
                        "description": "Official customs warehouse code (e.g. WH-KLA-001).",
                    },
                    "importer_tin": {
                        "type": "string",
                        "description": "Optional 10-digit TIN to filter consignments for a specific importer.",
                    },
                },
                "required": ["warehouse_code"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "warehouse_code": {"type": "string"},
                    "total_consignments": {"type": "integer"},
                    "total_cif_ugx": {"type": "number"},
                    "consignments": {"type": "array", "items": {"type": "object"}},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="low",
            namespace="bwims",
            read_only=True,
            idempotent=True,
            open_world=False,
        )

    def execute(self, warehouse_code: str = "", importer_tin: str | None = None, **kwargs: Any) -> dict[str, Any]:
        return self._client.get_inventory(warehouse_code=warehouse_code, importer_tin=importer_tin)


class BwimsReleaseClearanceTool(Tool):
    """Tool for processing ex-warehouse entries for home consumption or re-export."""

    def __init__(self, client: BwimsClient) -> None:
        self._client = client

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="bwims_release_clearance",
            description=(
                "Process ex-warehouse customs clearance for bonded goods being released for home consumption (IM4) "
                "or re-exported under customs bond. Updates remaining stock in the warehouse."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "entry_number": {
                        "type": "string",
                        "description": "Original IM7 warehousing entry number.",
                    },
                    "importer_tin": {
                        "type": "string",
                        "description": "10-digit TIN of the clearing importer.",
                    },
                    "cleared_quantity": {
                        "type": "number",
                        "description": "Quantity of goods being ex-warehoused.",
                    },
                    "declaration_type": {
                        "type": "string",
                        "enum": ["IM4_HOME_CONSUMPTION", "RE_EXPORT"],
                        "description": "Customs clearance regime.",
                    },
                    "duty_paid_prn": {
                        "type": "string",
                        "description": "Payment Registration Number (PRN) proving customs duty payment.",
                    },
                },
                "required": ["entry_number", "importer_tin", "cleared_quantity"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "clearance_id": {"type": "string"},
                    "entry_number": {"type": "string"},
                    "cleared_quantity": {"type": "number"},
                    "remaining_quantity": {"type": "number"},
                    "status": {"type": "string"},
                    "message": {"type": "string"},
                    "error": {"type": ["string", "null"]},
                },
            },
            risk="medium",
            requires_confirmation=True,
            namespace="bwims",
            required_scopes=("ura_account_access",),
            allowed_roles=("verified_taxpayer", "ura_staff", "ura_admin"),
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=False,
        )

    def execute(
        self,
        entry_number: str,
        importer_tin: str,
        cleared_quantity: float,
        declaration_type: str = "IM4_HOME_CONSUMPTION",
        duty_paid_prn: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self._client.clear_ex_warehouse(
            entry_number=entry_number,
            importer_tin=importer_tin,
            cleared_quantity=cleared_quantity,
            declaration_type=declaration_type,
            duty_paid_prn=duty_paid_prn,
        )


class BwimsConnector(SystemConnector):
    """System connector for URA BWIMS."""

    def __init__(self, service: BwimsService | None = None) -> None:
        self._service = service or BwimsService()
        self._client = BwimsClient(service=self._service)
        self._tools: list[Tool] = [
            BwimsConsignmentStatusTool(self._client),
            BwimsWarehouseInventoryTool(self._client),
            BwimsReleaseClearanceTool(self._client),
        ]
        self._healthy = False

    @property
    def system_name(self) -> str:
        return "bwims"

    @property
    def client(self) -> BwimsClient:
        return self._client

    @property
    def service(self) -> BwimsService:
        return self._service

    def initialize(self) -> bool:
        self._healthy = self._client.ping()
        logger.info("BwimsConnector initialized (healthy=%s)", self._healthy)
        return self._healthy

    def is_healthy(self) -> bool:
        return self._healthy and self._client.ping()

    def get_tools(self) -> list[Tool]:
        return list(self._tools)

    def get_status(self) -> dict[str, Any]:
        return {
            "system_name": self.system_name,
            "display_name": "Bonded Warehouse Information Management System (BWIMS)",
            "healthy": self.is_healthy(),
            "live_mode": self._client.is_live,
            "tool_count": len(self._tools),
            "tools": [t.schema.name for t in self._tools],
            "database": self._service.get_stats(),
        }

    def shutdown(self) -> None:
        self._healthy = False
        logger.info("BwimsConnector shut down")

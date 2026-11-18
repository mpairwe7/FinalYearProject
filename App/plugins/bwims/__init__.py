"""URA Bonded Warehouse Information Management System (BWIMS) Plugin and Connector."""

from __future__ import annotations

from plugins.base import Plugin, PluginMetadata

from .client import BwimsClient
from .connector import (
    BwimsConnector,
    BwimsConsignmentStatusTool,
    BwimsReleaseClearanceTool,
    BwimsWarehouseInventoryTool,
)
from .database import BwimsDatabase
from .models import (
    BondedConsignment,
    BondedWarehouse,
    ConsignmentStatus,
    ConsignmentStatusRequest,
    ConsignmentStatusResponse,
    ExWarehouseClearanceRequest,
    ExWarehouseClearanceResponse,
    WarehouseInventoryRequest,
    WarehouseInventoryResponse,
    WarehouseType,
)
from .service import BwimsService


class BwimsPlugin(Plugin):
    """Plugin encapsulating the BWIMS customs bonded warehouse system and connector."""

    def __init__(self, service: BwimsService | None = None) -> None:
        metadata = PluginMetadata(
            name="bwims",
            display_name="Bonded Warehouse Information Management System (BWIMS)",
            version="1.0.0",
            description=(
                "Connects the agentic URA system to BWIMS for tracking customs bonded warehouses, "
                "IM7 warehousing declarations, statutory time-limits under EACCMA, and ex-warehouse IM4 clearances."
            ),
            author="URA Customs Modernization & DRMS",
            system_type="customs_warehousing",
            tags=("bwims", "customs", "bonded_warehouse", "im7", "im4", "transit"),
            documentation_url="https://ura.go.ug/en/bwims/",
        )
        connector = BwimsConnector(service=service)
        super().__init__(metadata=metadata, connector=connector)


__all__ = [
    "BwimsPlugin",
    "BwimsConnector",
    "BwimsService",
    "BwimsDatabase",
    "BwimsClient",
    "BwimsConsignmentStatusTool",
    "BwimsWarehouseInventoryTool",
    "BwimsReleaseClearanceTool",
    "BondedWarehouse",
    "BondedConsignment",
    "WarehouseType",
    "ConsignmentStatus",
    "ConsignmentStatusRequest",
    "ConsignmentStatusResponse",
    "WarehouseInventoryRequest",
    "WarehouseInventoryResponse",
    "ExWarehouseClearanceRequest",
    "ExWarehouseClearanceResponse",
]

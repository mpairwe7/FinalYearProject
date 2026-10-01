"""Data models and schemas for BWIMS (Bonded Warehouse Information Management System)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class WarehouseType(str, Enum):
    PRIVATE_BONDED_WAREHOUSE = "PRIVATE_BONDED_WAREHOUSE"
    PUBLIC_BONDED_WAREHOUSE = "PUBLIC_BONDED_WAREHOUSE"
    INLAND_CONTAINER_DEPOT = "INLAND_CONTAINER_DEPOT"


class ConsignmentStatus(str, Enum):
    BONDED_IN_STORAGE = "BONDED_IN_STORAGE"
    EX_WAREHOUSED_HOME_USE = "EX_WAREHOUSED_HOME_USE"
    RE_EXPORTED = "RE_EXPORTED"
    OVERSTAYED_AUCTION_RISK = "OVERSTAYED_AUCTION_RISK"


@dataclass
class BondedWarehouse:
    warehouse_code: str  # e.g., WH-KLA-001
    operator_name: str
    warehouse_type: WarehouseType
    location: str
    bond_security_amount_ugx: float
    status: str = "ACTIVE"
    licensed_expiry: str = "2026-12-31"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["warehouse_type"] = self.warehouse_type.value
        return d


@dataclass
class BondedConsignment:
    entry_number: str  # e.g., 2026-ASY-IM7-88912
    importer_tin: str
    importer_name: str
    warehouse_code: str
    goods_description: str
    quantity: float
    unit_of_measure: str
    cif_value_ugx: float
    warehousing_date: str
    statutory_expiry_date: str
    status: ConsignmentStatus
    hs_code: str = "8703.23.90"
    cleared_quantity: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class ConsignmentStatusRequest:
    entry_number: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConsignmentStatusResponse:
    ok: bool
    found: bool
    consignment: dict[str, Any] | None = None
    days_in_storage: int = 0
    is_overstayed: bool = False
    message: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WarehouseInventoryRequest:
    warehouse_code: str
    importer_tin: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WarehouseInventoryResponse:
    ok: bool
    warehouse_code: str
    total_consignments: int
    total_cif_ugx: float
    consignments: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExWarehouseClearanceRequest:
    entry_number: str
    importer_tin: str
    cleared_quantity: float
    declaration_type: str = "IM4_HOME_CONSUMPTION"  # IM4 or RE_EXPORT
    duty_paid_prn: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExWarehouseClearanceResponse:
    ok: bool
    clearance_id: str
    entry_number: str
    cleared_quantity: float
    remaining_quantity: float
    status: str
    message: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

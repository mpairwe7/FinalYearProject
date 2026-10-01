"""Data models and schemas for URA Digital Tax Stamps (DTS / Kakasa platform)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class GazettedCategory(str, Enum):
    BEER = "BEER"
    SPIRITS = "SPIRITS"
    WINE = "WINE"
    BOTTLED_WATER = "BOTTLED_WATER"
    SODA = "SODA"
    TOBACCO = "TOBACCO"
    CEMENT = "CEMENT"
    SUGAR = "SUGAR"
    COOKING_OIL = "COOKING_OIL"
    JUICES = "JUICES"


class StampStatus(str, Enum):
    GENUINE = "GENUINE"
    UNACTIVATED = "UNACTIVATED"
    EXPIRED = "EXPIRED"
    COUNTERFEIT = "COUNTERFEIT"
    SPOILED = "SPOILED"


class PackagingType(str, Enum):
    BOTTLE = "BOTTLE"
    CAN = "CAN"
    PACKET = "PACKET"
    BAG = "BAG"
    CARTON = "CARTON"


@dataclass
class StampRecord:
    stamp_code: str
    product_category: GazettedCategory
    brand_name: str
    manufacturer_tin: str
    manufacturer_name: str
    batch_number: str
    production_date: str
    status: StampStatus
    expiry_date: str | None = None
    line_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["product_category"] = self.product_category.value
        d["status"] = self.status.value
        return d


@dataclass
class StampVerificationRequest:
    stamp_code: str
    product_category: GazettedCategory | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.product_category:
            d["product_category"] = self.product_category.value
        return d


@dataclass
class StampVerificationResponse:
    ok: bool
    stamp_code: str
    is_authentic: bool
    status: StampStatus
    product_category: str
    brand_name: str
    manufacturer_name: str
    manufacturer_tin: str
    batch_number: str
    production_date: str
    expiry_date: str | None
    verification_method: str = "KAKASA_PLATFORM"
    message: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class StampOrderRequest:
    taxpayer_tin: str
    product_category: GazettedCategory
    quantity: int
    packaging_type: PackagingType = PackagingType.BOTTLE
    facility_location: str = "MAIN_FACTORY"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["product_category"] = self.product_category.value
        d["packaging_type"] = self.packaging_type.value
        return d


@dataclass
class StampOrderResponse:
    ok: bool
    order_id: str
    taxpayer_tin: str
    product_category: str
    quantity: int
    unit_fee_ugx: float
    total_amount_ugx: float
    prn: str
    payment_status: str  # PENDING, PAID
    collection_point: str
    order_status: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StampActivationRequest:
    order_id: str
    line_id: str
    stamp_serials: list[str]
    facility_location: str = "MAIN_FACTORY"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StampActivationResponse:
    ok: bool
    batch_id: str
    order_id: str
    line_id: str
    activated_count: int
    timestamp: str
    status: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DamagedStampDeclarationRequest:
    taxpayer_tin: str
    damaged_serials: list[str]
    incident_reason: str
    line_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DamagedStampDeclarationResponse:
    ok: bool
    declaration_id: str
    reconciled_count: int
    credit_allowable_ugx: float
    status: str
    message: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaxpayerDtsProfile:
    tin: str
    manufacturer_name: str
    taxpayer_type: str  # LOCAL_MANUFACTURER, IMPORTER
    gazetted_categories: list[str]
    packaging_lines: list[dict[str, Any]]
    dts_registration_date: str
    active_stamps_inventory: int
    is_compliant: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

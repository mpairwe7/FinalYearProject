"""Data models and schemas for URA Payment System (e-Services > Make a Payment suite)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class PaymentCategory(str, Enum):
    DOMESTIC_TAX = "DOMESTIC_TAX"
    NTR_ONTR = "NTR_ONTR"
    CUSTOMS_DUTY = "CUSTOMS_DUTY"
    UWA_PARK_FEES = "UWA_PARK_FEES"
    HOSPITAL_FEES = "HOSPITAL_FEES"
    ADVANCE_INCOME_TAX = "ADVANCE_INCOME_TAX"


class PaymentChannel(str, Enum):
    COMMERCIAL_BANK = "COMMERCIAL_BANK"
    MOBILE_MONEY = "MOBILE_MONEY"
    VISA_MASTERCARD = "VISA_MASTERCARD"


class PrnStatus(str, Enum):
    PENDING = "PENDING"
    CLEARED = "CLEARED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


@dataclass
class PaymentPrnRecord:
    prn: str  # 12-digit numeric code
    taxpayer_tin: str | None
    taxpayer_name: str
    payment_category: PaymentCategory
    tax_head: str
    amount_ugx: float
    payment_channel: PaymentChannel
    status: PrnStatus
    created_at: str
    expiry_date: str
    cleared_at: str | None = None
    bank_reference: str | None = None
    agency_details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["payment_category"] = self.payment_category.value
        d["payment_channel"] = self.payment_channel.value
        d["status"] = self.status.value
        return d


@dataclass
class GeneratePrnRequest:
    taxpayer_name: str
    amount_ugx: float
    taxpayer_tin: str | None = None
    payment_category: PaymentCategory = PaymentCategory.DOMESTIC_TAX
    tax_head: str = "VAT_STANDARD"
    payment_channel: PaymentChannel = PaymentChannel.COMMERCIAL_BANK
    agency_details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["payment_category"] = self.payment_category.value
        d["payment_channel"] = self.payment_channel.value
        return d


@dataclass
class GeneratePrnResponse:
    ok: bool
    prn: str
    taxpayer_name: str
    amount_ugx: float
    tax_head: str
    payment_channel: str
    expiry_date: str
    status: str
    bank_barcode: str
    payment_slip_url: str
    message: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReactivatePrnRequest:
    prn: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReactivatePrnResponse:
    ok: bool
    prn: str
    taxpayer_name: str
    amount_ugx: float
    previous_expiry: str
    new_expiry_date: str
    status: str
    message: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PaymentStatusRequest:
    prn: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PaymentStatusResponse:
    ok: bool
    prn: str
    taxpayer_name: str
    amount_ugx: float
    status: str
    is_cleared: bool
    cleared_at: str | None = None
    bank_reference: str | None = None
    receipt_url: str | None = None
    message: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PaymentCheckoutRequest:
    prn: str
    payment_method: str  # VISA, MASTERCARD, MTN_MOMO, AIRTEL_MONEY
    payer_identifier: str  # card last 4 or phone number (e.g., +256772111222)
    amount_paid_ugx: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PaymentCheckoutResponse:
    ok: bool
    prn: str
    transaction_id: str
    receipt_number: str
    amount_paid_ugx: float
    payment_method: str
    status: str
    cleared_at: str
    message: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AdvanceTaxVerifyRequest:
    vehicle_registration_number: str
    prn: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AdvanceTaxVerifyResponse:
    ok: bool
    vehicle_registration_number: str
    vehicle_type: str
    capacity: int
    rate_basis: str
    advance_tax_assessed_ugx: float
    is_compliant: bool
    prn: str
    valid_until: str
    message: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

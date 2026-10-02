"""Data models and schemas for the EFRIS (Electronic Fiscal Receipting and Invoicing System)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class InvoiceType(str, Enum):
    B2B = "B2B"
    B2C = "B2C"
    B2G = "B2G"


class DocumentStatus(str, Enum):
    ISSUED = "ISSUED"
    VERIFIED = "VERIFIED"
    ADJUSTED = "ADJUSTED"
    CANCELLED = "CANCELLED"


class CreditNoteReason(str, Enum):
    GOODS_RETURNED = "GOODS_RETURNED"
    PRICE_DISCOUNT = "PRICE_DISCOUNT"
    INVOICING_ERROR = "INVOICING_ERROR"


class TaxRateCategory(str, Enum):
    STANDARD = "STANDARD_18"
    ZERO_RATED = "ZERO_RATED_0"
    EXEMPT = "EXEMPT"


@dataclass
class InvoiceItem:
    commodity_code: str
    description: str
    quantity: float
    unit_price: float
    tax_rate: float = 0.18
    tax_category: TaxRateCategory = TaxRateCategory.STANDARD
    total_amount: float = 0.0
    tax_amount: float = 0.0
    net_amount: float = 0.0

    def __post_init__(self) -> None:
        if self.total_amount == 0.0 and self.quantity > 0:
            self.net_amount = round(self.quantity * self.unit_price, 2)
            if self.tax_category == TaxRateCategory.STANDARD:
                self.tax_amount = round(self.net_amount * self.tax_rate, 2)
            else:
                self.tax_amount = 0.0
            self.total_amount = round(self.net_amount + self.tax_amount, 2)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tax_category"] = self.tax_category.value
        return d


@dataclass
class FiscalInvoiceRequest:
    seller_tin: str
    items: list[InvoiceItem]
    buyer_tin: str | None = None
    buyer_name: str | None = None
    invoice_type: InvoiceType = InvoiceType.B2B
    currency: str = "UGX"
    branch_id: str = "MAIN_HQ"
    cashier_id: str = "CASHIER_01"
    offline_reference: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "seller_tin": self.seller_tin,
            "buyer_tin": self.buyer_tin,
            "buyer_name": self.buyer_name,
            "invoice_type": self.invoice_type.value,
            "currency": self.currency,
            "branch_id": self.branch_id,
            "cashier_id": self.cashier_id,
            "offline_reference": self.offline_reference,
            "items": [it.to_dict() for it in self.items],
        }


@dataclass
class FiscalInvoiceResponse:
    ok: bool
    fdn: str
    verification_code: str
    qr_code_url: str
    seller_tin: str
    buyer_tin: str | None
    currency: str
    net_amount: float
    tax_amount: float
    gross_amount: float
    status: DocumentStatus
    issued_at: str
    message: str = "Fiscal invoice generated successfully"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class InvoiceVerificationRequest:
    fdn: str
    verification_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InvoiceVerificationResponse:
    ok: bool
    fdn: str
    is_authentic: bool
    status: DocumentStatus
    seller_tin: str
    seller_name: str
    buyer_tin: str | None
    buyer_name: str | None
    issue_date: str
    net_amount: float
    tax_amount: float
    gross_amount: float
    currency: str
    items_count: int
    message: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class CreditNoteRequest:
    original_fdn: str
    seller_tin: str
    reason: CreditNoteReason
    adjusted_amount: float
    description: str = ""
    buyer_tin: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["reason"] = self.reason.value
        return d


@dataclass
class CreditNoteResponse:
    ok: bool
    credit_note_number: str
    original_fdn: str
    adjusted_gross: float
    adjusted_vat: float
    status: str
    message: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StockItem:
    commodity_code: str
    description: str
    unit_of_measure: str
    quantity_on_hand: float
    unit_cost: float
    category: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaxpayerEfrisProfile:
    tin: str
    business_name: str
    is_vat_registered: bool
    efris_status: str  # ACTIVE, PENDING, EXEMPT
    mandated_sector: str
    registration_date: str
    integration_mode: str  # E_INVOICING, EFD, SYSTEM_TO_SYSTEM
    active_terminals: list[str] = field(default_factory=list)
    sdc_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

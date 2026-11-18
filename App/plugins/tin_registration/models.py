"""Data models and schemas for the URA TIN Registration System."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class TaxpayerCategory(str, Enum):
    INDIVIDUAL = "INDIVIDUAL"
    NON_INDIVIDUAL_COMPANY = "NON_INDIVIDUAL_COMPANY"
    PARTNERSHIP = "PARTNERSHIP"
    TRUST_NGO = "TRUST_NGO"
    FOREIGN_NON_RESIDENT = "FOREIGN_NON_RESIDENT"


class TaxHeadType(str, Enum):
    INCOME_TAX_INDIVIDUAL = "INCOME_TAX_INDIVIDUAL"
    CORPORATION_TAX = "CORPORATION_TAX"
    PAYE = "PAYE"
    VAT_STANDARD = "VAT_STANDARD"
    LOCAL_EXCISE = "LOCAL_EXCISE"
    WITHHOLDING_TAX = "WITHHOLDING_TAX"
    RENTAL_TAX = "RENTAL_TAX"


@dataclass
class TaxObligation:
    tax_head: TaxHeadType
    effective_from: str
    status: str = "ACTIVE"
    filing_frequency: str = "MONTHLY"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tax_head"] = self.tax_head.value
        return d


@dataclass
class TaxpayerRecord:
    tin: str  # 10-digit numeric
    legal_name: str
    category: TaxpayerCategory
    nin_or_passport: str
    ursb_reg_no: str | None
    mobile: str
    email: str
    registered_office: str
    district: str
    registration_date: str
    status: str = "ACTIVE"  # ACTIVE, SUSPENDED, CANCELLED
    obligations: list[TaxObligation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["category"] = self.category.value
        d["obligations"] = [
            ob.to_dict() if hasattr(ob, "to_dict") else ob for ob in self.obligations
        ]
        return d


@dataclass
class InstantTinRequest:
    nin: str
    full_name: str
    date_of_birth: str
    mobile: str
    email: str
    district: str
    occupation: str = "Employed / Self-Employed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InstantTinResponse:
    ok: bool
    tin: str
    legal_name: str
    nin: str
    registration_date: str
    status: str
    default_obligations: list[str]
    certificate_reference: str
    message: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NonIndividualTinRequest:
    ursb_registration_number: str
    business_name: str
    category: TaxpayerCategory = TaxpayerCategory.NON_INDIVIDUAL_COMPANY
    directors_tins: list[str] = field(default_factory=list)
    registered_office: str = "Kampala Central"
    district: str = "Kampala"
    mobile: str = "+256700000000"
    email: str = "info@company.co.ug"
    requested_tax_heads: list[str] = field(default_factory=lambda: ["CORPORATION_TAX"])

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["category"] = self.category.value
        return d


@dataclass
class NonIndividualTinResponse:
    ok: bool
    tin: str
    business_name: str
    ursb_registration_number: str
    category: str
    registration_date: str
    status: str
    active_tax_heads: list[str]
    message: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TinSearchRequest:
    query: str  # 10-digit TIN, 14-char NIN, or URSB Number

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TinSearchResponse:
    ok: bool
    found: bool
    taxpayer: dict[str, Any] | None = None
    message: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaxObligationUpdateRequest:
    tin: str
    tax_head: TaxHeadType
    action: str = "ADD"  # ADD or REMOVE

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tax_head"] = self.tax_head.value
        return d


@dataclass
class TaxObligationUpdateResponse:
    ok: bool
    tin: str
    legal_name: str
    active_obligations: list[str]
    message: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

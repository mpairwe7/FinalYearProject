"""Data models and schemas for URSB (Uganda Registration Services Bureau)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class EntityType(str, Enum):
    LIMITED_COMPANY = "LIMITED_COMPANY"
    BUSINESS_NAME = "BUSINESS_NAME"
    PARTNERSHIP = "PARTNERSHIP"
    FOREIGN_COMPANY = "FOREIGN_COMPANY"


class EntityStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    PENDING_ANNUAL_RETURNS = "PENDING_ANNUAL_RETURNS"
    STRUCK_OFF = "STRUCK_OFF"


@dataclass
class DirectorInfo:
    full_name: str
    nin_or_passport: str
    nationality: str = "Ugandan"
    role: str = "DIRECTOR"
    tin: str | None = None
    shares_percentage: float = 50.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BusinessEntity:
    registration_number: str  # e.g., 80019284 or BRN
    business_name: str
    entity_type: EntityType
    registration_date: str
    status: EntityStatus
    registered_office: str
    district: str
    nature_of_business: str
    directors: list[DirectorInfo] = field(default_factory=list)
    latest_annual_returns_year: int = 2025
    form_20_registered: bool = True

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["entity_type"] = self.entity_type.value
        d["status"] = self.status.value
        d["directors"] = [dir_obj.to_dict() if hasattr(dir_obj, "to_dict") else dir_obj for dir_obj in self.directors]
        return d


@dataclass
class BusinessSearchRequest:
    query: str  # Registration number, BRN, or business name

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BusinessSearchResponse:
    ok: bool
    found: bool
    entity: dict[str, Any] | None = None
    message: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BusinessRegistrationRequest:
    business_name: str
    entity_type: EntityType
    nature_of_business: str
    registered_office: str
    district: str
    directors: list[dict[str, Any]]
    applicant_nin: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["entity_type"] = self.entity_type.value
        return d


@dataclass
class BusinessRegistrationResponse:
    ok: bool
    registration_number: str
    business_name: str
    entity_type: str
    registration_date: str
    status: str
    certificate_reference: str
    message: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ComplianceCheckResponse:
    ok: bool
    registration_number: str
    business_name: str
    is_legally_active: bool
    annual_returns_up_to_date: bool
    form_20_on_file: bool
    ready_for_ura_tin: bool
    status: str
    details: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

"""Client SDK for interacting with URA TIN Registration System."""

from __future__ import annotations

import logging
import os
from typing import Any

from .models import (
    InstantTinRequest,
    NonIndividualTinRequest,
    TaxHeadType,
    TaxObligationUpdateRequest,
    TaxpayerCategory,
    TinSearchRequest,
)
from .service import TinRegistrationService

logger = logging.getLogger(__name__)


class TinRegistrationClient:
    """Client for URA TIN operations, supporting local simulated engine or remote REST API."""

    def __init__(
        self,
        service: TinRegistrationService | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._service = service or TinRegistrationService()
        self._api_base = api_base or os.getenv("TIN_API_BASE", "")
        self._api_key = api_key or os.getenv("TIN_API_KEY", "")
        self._is_live = bool(self._api_base and self._api_key.startswith("tin_live_"))

    @property
    def is_live(self) -> bool:
        return self._is_live

    def ping(self) -> bool:
        """Health check for TIN Registration platform connectivity."""
        return True

    def search_taxpayer(self, query: str) -> dict[str, Any]:
        """Search taxpayer by 10-digit TIN, National ID (NIN), or URSB Number."""
        query = str(query or "").strip()
        if not query:
            return {"ok": False, "error": "Search query is required"}

        req = TinSearchRequest(query=query)
        resp = self._service.search_taxpayer(req)
        return resp.to_dict()

    def apply_instant_individual_tin(
        self,
        nin: str,
        full_name: str,
        date_of_birth: str = "1990-01-01",
        mobile: str = "+256700000000",
        email: str = "taxpayer@gmail.com",
        district: str = "Kampala",
        occupation: str = "Self-Employed",
    ) -> dict[str, Any]:
        """Apply for an Instant Individual TIN using citizen National ID (NIN)."""
        nin = str(nin or "").strip()
        full_name = str(full_name or "").strip()
        if not nin or not full_name:
            return {"ok": False, "error": "nin and full_name are required"}

        req = InstantTinRequest(
            nin=nin,
            full_name=full_name,
            date_of_birth=date_of_birth,
            mobile=mobile,
            email=email,
            district=district,
            occupation=occupation,
        )
        resp = self._service.apply_instant_individual_tin(req)
        return resp.to_dict()

    def apply_non_individual_tin(
        self,
        ursb_registration_number: str,
        business_name: str,
        category: str = "NON_INDIVIDUAL_COMPANY",
        directors_tins: list[str] | None = None,
        registered_office: str = "Kampala Central",
        district: str = "Kampala",
        mobile: str = "+256700000000",
        email: str = "info@company.co.ug",
        requested_tax_heads: list[str] | None = None,
    ) -> dict[str, Any]:
        """Apply for Non-Individual corporate TIN using URSB registration credentials."""
        ursb_no = str(ursb_registration_number or "").strip()
        business_name = str(business_name or "").strip()
        if not ursb_no or not business_name:
            return {"ok": False, "error": "ursb_registration_number and business_name are required"}

        cat_clean = category.upper()
        cat = TaxpayerCategory(cat_clean) if cat_clean in TaxpayerCategory._value2member_map_ else TaxpayerCategory.NON_INDIVIDUAL_COMPANY

        req = NonIndividualTinRequest(
            ursb_registration_number=ursb_no,
            business_name=business_name,
            category=cat,
            directors_tins=directors_tins or [],
            registered_office=registered_office,
            district=district,
            mobile=mobile,
            email=email,
            requested_tax_heads=requested_tax_heads or ["CORPORATION_TAX"],
        )
        resp = self._service.apply_non_individual_tin(req)
        return resp.to_dict()

    def add_tax_obligation(self, tin: str, tax_head: str) -> dict[str, Any]:
        """Register an additional tax head (e.g. VAT, PAYE, Local Excise) for an existing TIN."""
        tin = str(tin or "").strip()
        tax_head = str(tax_head or "").strip().upper()
        if not tin or not tax_head:
            return {"ok": False, "error": "tin and tax_head are required"}

        try:
            th = TaxHeadType(tax_head)
        except ValueError:
            valid = [h.value for h in TaxHeadType]
            return {"ok": False, "error": f"Invalid tax head '{tax_head}'. Valid tax heads: {valid}"}

        req = TaxObligationUpdateRequest(tin=tin, tax_head=th, action="ADD")
        resp = self._service.update_tax_obligation(req)
        return resp.to_dict()

    def get_stats(self) -> dict[str, Any]:
        """Return system and database statistics."""
        return self._service.get_stats()

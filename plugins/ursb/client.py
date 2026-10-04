"""Client SDK for interacting with URSB (Uganda Registration Services Bureau) system."""

from __future__ import annotations

import logging
from typing import Any

from .models import (
    BusinessRegistrationRequest,
    BusinessSearchRequest,
    EntityType,
)
from .service import UrsbService

logger = logging.getLogger(__name__)


class UrsbClient:
    """Client for the local URSB simulator; live registry calls are not implemented."""

    def __init__(
        self,
        service: UrsbService | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
    ) -> None:
        del api_base, api_key  # retained for compatibility; remote calls are not implemented
        self._service = service or UrsbService()

    @property
    def is_live(self) -> bool:
        return False

    def ping(self) -> bool:
        """Report local simulator readiness, not external URSB connectivity."""
        return True

    def search_business(self, query: str) -> dict[str, Any]:
        """Search registered company or business name."""
        query = str(query or "").strip()
        if not query:
            return {"ok": False, "error": "Search query is required"}

        req = BusinessSearchRequest(query=query)
        resp = self._service.search_business(req)
        return resp.to_dict()

    def register_business(
        self,
        business_name: str,
        entity_type: str = "LIMITED_COMPANY",
        nature_of_business: str = "General Commercial Services",
        registered_office: str = "Kampala Central",
        district: str = "Kampala",
        directors: list[dict[str, Any]] | None = None,
        applicant_nin: str = "CM000000000000",
    ) -> dict[str, Any]:
        """Register a new business name or company with URSB."""
        business_name = str(business_name or "").strip()
        if not business_name:
            return {"ok": False, "error": "business_name is required"}

        et_clean = entity_type.upper().replace(" ", "_")
        try:
            etype = EntityType(et_clean)
        except ValueError:
            etype = EntityType.LIMITED_COMPANY

        req = BusinessRegistrationRequest(
            business_name=business_name,
            entity_type=etype,
            nature_of_business=nature_of_business,
            registered_office=registered_office,
            district=district,
            directors=directors or [],
            applicant_nin=applicant_nin,
        )
        resp = self._service.register_business(req)
        return resp.to_dict()

    def check_compliance(self, registration_number: str) -> dict[str, Any]:
        """Check legal compliance, annual returns, and readiness for URA TIN generation."""
        reg_no = str(registration_number or "").strip()
        if not reg_no:
            return {"ok": False, "error": "registration_number is required"}

        resp = self._service.check_compliance(reg_no)
        return resp.to_dict()

    def get_stats(self) -> dict[str, Any]:
        """Return system and database statistics."""
        return self._service.get_stats()

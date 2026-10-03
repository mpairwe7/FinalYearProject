"""Client SDK for interacting with URA Digital Tax Stamps (DTS / Kakasa platform)."""

from __future__ import annotations

import logging
from typing import Any

from .models import (
    DamagedStampDeclarationRequest,
    GazettedCategory,
    PackagingType,
    StampActivationRequest,
    StampOrderRequest,
    StampVerificationRequest,
)
from .service import DigitalTaxStampsService

logger = logging.getLogger(__name__)


class DigitalTaxStampsClient:
    """Client for the local DTS simulator; live URA calls are not implemented."""

    def __init__(
        self,
        service: DigitalTaxStampsService | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
    ) -> None:
        del api_base, api_key  # retained for compatibility; remote calls are not implemented
        self._service = service or DigitalTaxStampsService()

    @property
    def is_live(self) -> bool:
        return False

    def ping(self) -> bool:
        """Report local simulator readiness, not external DTS connectivity."""
        return True

    def verify_stamp(self, stamp_code: str) -> dict[str, Any]:
        """Look up a local simulator fixture without authenticating a physical stamp."""
        stamp_code = str(stamp_code or "").strip()
        if not stamp_code:
            return {"ok": False, "error": "stamp_code is required"}

        req = StampVerificationRequest(stamp_code=stamp_code)
        resp = self._service.verify_stamp(req)
        return resp.to_dict()

    def order_stamps(
        self,
        taxpayer_tin: str,
        product_category: str,
        quantity: int,
        packaging_type: str = "BOTTLE",
        facility_location: str = "MAIN_FACTORY",
    ) -> dict[str, Any]:
        """Order digital tax stamps for gazetted products and generate PRN."""
        taxpayer_tin = str(taxpayer_tin or "").strip()
        if not taxpayer_tin:
            return {"ok": False, "error": "taxpayer_tin is required"}

        cat_upper = str(product_category or "").upper().replace(" ", "_")
        try:
            category = GazettedCategory(cat_upper)
        except ValueError:
            valid = [c.value for c in GazettedCategory]
            return {
                "ok": False,
                "error": f"Invalid product category '{product_category}'. Gazetted categories: {valid}",
            }

        pkg_upper = str(packaging_type or "BOTTLE").upper()
        try:
            pkg = PackagingType(pkg_upper)
        except ValueError:
            pkg = PackagingType.BOTTLE

        req = StampOrderRequest(
            taxpayer_tin=taxpayer_tin,
            product_category=category,
            quantity=int(quantity),
            packaging_type=pkg,
            facility_location=facility_location,
        )
        resp = self._service.order_stamps(req)
        return resp.to_dict()

    def activate_stamps(
        self,
        order_id: str,
        line_id: str,
        stamp_serials: list[str],
        facility_location: str = "MAIN_FACTORY",
    ) -> dict[str, Any]:
        """Activate ordered stamps at the factory packaging line or customs warehouse."""
        order_id = str(order_id or "").strip()
        line_id = str(line_id or "").strip()
        if not order_id or not line_id:
            return {"ok": False, "error": "order_id and line_id are required"}
        if not stamp_serials:
            return {"ok": False, "error": "stamp_serials list cannot be empty"}

        req = StampActivationRequest(
            order_id=order_id,
            line_id=line_id,
            stamp_serials=[str(s).strip() for s in stamp_serials if str(s).strip()],
            facility_location=facility_location,
        )
        resp = self._service.activate_stamps(req)
        return resp.to_dict()

    def declare_damaged_stamps(
        self,
        taxpayer_tin: str,
        damaged_serials: list[str],
        incident_reason: str,
        line_id: str = "DEFAULT_LINE",
    ) -> dict[str, Any]:
        """Reconcile stamps damaged during production packaging line jams."""
        taxpayer_tin = str(taxpayer_tin or "").strip()
        if not taxpayer_tin:
            return {"ok": False, "error": "taxpayer_tin is required"}
        if not damaged_serials:
            return {"ok": False, "error": "damaged_serials list cannot be empty"}

        req = DamagedStampDeclarationRequest(
            taxpayer_tin=taxpayer_tin,
            damaged_serials=[str(s).strip() for s in damaged_serials if str(s).strip()],
            incident_reason=incident_reason or "PACKAGING_LINE_JAM",
            line_id=line_id,
        )
        resp = self._service.declare_damaged_stamps(req)
        return resp.to_dict()

    def get_taxpayer_profile(self, tin: str) -> dict[str, Any]:
        """Query manufacturer or importer DTS registration details."""
        tin = str(tin or "").strip()
        if not tin:
            return {"ok": False, "error": "TIN is required"}

        profile = self._service.get_taxpayer_profile(tin)
        if not profile:
            return {
                "ok": False,
                "tin": tin,
                "registered": False,
                "message": f"Taxpayer with TIN '{tin}' is not registered as a DTS manufacturer or importer.",
            }
        return {
            "ok": True,
            "tin": tin,
            "registered": True,
            "profile": profile.to_dict(),
        }

    def list_tariffs(self) -> dict[str, Any]:
        """List statutory stamp fee tariffs for all gazetted products."""
        tariffs = self._service.list_gazetted_tariffs()
        return {
            "ok": True,
            "tariffs_ugx": tariffs,
            "collection_point": "SICPA Uganda Ltd, Henley Business Park, Ntinda Industrial Area, Kampala",
        }

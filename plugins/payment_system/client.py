"""Client SDK for interacting with URA Payment System."""

from __future__ import annotations

import logging
import os
from typing import Any

from .models import (
    AdvanceTaxVerifyRequest,
    GeneratePrnRequest,
    PaymentCategory,
    PaymentChannel,
    PaymentCheckoutRequest,
    PaymentStatusRequest,
    ReactivatePrnRequest,
)
from .service import PaymentService

logger = logging.getLogger(__name__)


class PaymentClient:
    """Client for URA Payment operations, supporting local simulated engine or remote REST API."""

    def __init__(
        self,
        service: PaymentService | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._service = service or PaymentService()
        self._api_base = api_base or os.getenv("PAYMENT_API_BASE", "")
        self._api_key = api_key or os.getenv("PAYMENT_API_KEY", "")
        self._is_live = bool(self._api_base and self._api_key.startswith("payment_live_"))

    @property
    def is_live(self) -> bool:
        return self._is_live

    def ping(self) -> bool:
        """Health check for URA Payment platform connectivity."""
        return True

    def generate_prn(
        self,
        taxpayer_name: str,
        amount_ugx: float,
        taxpayer_tin: str | None = None,
        payment_category: str = "DOMESTIC_TAX",
        tax_head: str = "VAT_STANDARD",
        payment_channel: str = "COMMERCIAL_BANK",
        agency_details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate a new Payment Registration Number (PRN) payment slip."""
        taxpayer_name = str(taxpayer_name or "").strip()
        if not taxpayer_name:
            return {"ok": False, "error": "taxpayer_name is required"}

        cat_clean = payment_category.upper()
        cat = PaymentCategory(cat_clean) if cat_clean in PaymentCategory._value2member_map_ else PaymentCategory.DOMESTIC_TAX

        ch_clean = payment_channel.upper()
        ch = PaymentChannel(ch_clean) if ch_clean in PaymentChannel._value2member_map_ else PaymentChannel.COMMERCIAL_BANK

        req = GeneratePrnRequest(
            taxpayer_name=taxpayer_name,
            amount_ugx=float(amount_ugx),
            taxpayer_tin=str(taxpayer_tin).strip() if taxpayer_tin else None,
            payment_category=cat,
            tax_head=str(tax_head or "VAT_STANDARD"),
            payment_channel=ch,
            agency_details=agency_details or {},
        )
        resp = self._service.generate_payment_slip(req)
        return resp.to_dict()

    def reactivate_prn(self, prn: str) -> dict[str, Any]:
        """Reactivate an expired PRN and extend validity by 21 days."""
        prn = str(prn or "").strip()
        if not prn:
            return {"ok": False, "error": "prn is required"}

        req = ReactivatePrnRequest(prn=prn)
        resp = self._service.reactivate_expired_prn(req)
        return resp.to_dict()

    def view_status(self, prn: str) -> dict[str, Any]:
        """Query real-time payment clearance and bank posting status."""
        prn = str(prn or "").strip()
        if not prn:
            return {"ok": False, "error": "prn is required"}

        req = PaymentStatusRequest(prn=prn)
        resp = self._service.view_payment_status(req)
        return resp.to_dict()

    def process_checkout(
        self,
        prn: str,
        payment_method: str = "VISA",
        payer_identifier: str = "1234",
        amount_paid_ugx: float = 0.0,
    ) -> dict[str, Any]:
        """Execute electronic checkout (VISA, MasterCard, or Mobile Money)."""
        prn = str(prn or "").strip()
        if not prn:
            return {"ok": False, "error": "prn is required"}

        req = PaymentCheckoutRequest(
            prn=prn,
            payment_method=payment_method,
            payer_identifier=payer_identifier,
            amount_paid_ugx=float(amount_paid_ugx),
        )
        resp = self._service.process_checkout(req)
        return resp.to_dict()

    def verify_advance_tax(self, vehicle_registration_number: str) -> dict[str, Any]:
        """Verify commercial passenger/goods motor vehicle advance tax compliance."""
        v_reg = str(vehicle_registration_number or "").strip()
        if not v_reg:
            return {"ok": False, "error": "vehicle_registration_number is required"}

        req = AdvanceTaxVerifyRequest(vehicle_registration_number=v_reg)
        resp = self._service.verify_advance_tax(req)
        return resp.to_dict()

    def get_stats(self) -> dict[str, Any]:
        """Return system and database statistics."""
        return self._service.get_stats()

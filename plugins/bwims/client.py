"""Client SDK for interacting with URA BWIMS (Bonded Warehouse Information Management System)."""

from __future__ import annotations

import logging
from typing import Any

from .models import (
    ConsignmentStatusRequest,
    ExWarehouseClearanceRequest,
    WarehouseInventoryRequest,
)
from .service import BwimsService

logger = logging.getLogger(__name__)


class BwimsClient:
    """Client for the local BWIMS simulator; live customs calls are not implemented."""

    def __init__(
        self,
        service: BwimsService | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
    ) -> None:
        del api_base, api_key  # retained for compatibility; remote calls are not implemented
        self._service = service or BwimsService()

    @property
    def is_live(self) -> bool:
        return False

    def ping(self) -> bool:
        """Report local simulator readiness, not external BWIMS connectivity."""
        return True

    def get_consignment(self, entry_number: str) -> dict[str, Any]:
        """Query bonded cargo consignment status and statutory expiry limit."""
        entry_number = str(entry_number or "").strip()
        if not entry_number:
            return {"ok": False, "error": "entry_number is required"}

        req = ConsignmentStatusRequest(entry_number=entry_number)
        resp = self._service.get_consignment_status(req)
        return resp.to_dict()

    def get_inventory(self, warehouse_code: str, importer_tin: str | None = None) -> dict[str, Any]:
        """Query inventory inside a customs bonded warehouse."""
        warehouse_code = str(warehouse_code or "").strip()
        if not warehouse_code:
            return {"ok": False, "error": "warehouse_code is required"}

        req = WarehouseInventoryRequest(
            warehouse_code=warehouse_code,
            importer_tin=str(importer_tin).strip() if importer_tin else None,
        )
        resp = self._service.get_warehouse_inventory(req)
        return resp.to_dict()

    def clear_ex_warehouse(
        self,
        entry_number: str,
        importer_tin: str,
        cleared_quantity: float,
        declaration_type: str = "IM4_HOME_CONSUMPTION",
        duty_paid_prn: str = "",
    ) -> dict[str, Any]:
        """Process an ex-warehouse release under home consumption or re-export."""
        entry_number = str(entry_number or "").strip()
        importer_tin = str(importer_tin or "").strip()
        if not entry_number or not importer_tin:
            return {"ok": False, "error": "entry_number and importer_tin are required"}

        req = ExWarehouseClearanceRequest(
            entry_number=entry_number,
            importer_tin=importer_tin,
            cleared_quantity=float(cleared_quantity),
            declaration_type=declaration_type,
            duty_paid_prn=duty_paid_prn,
        )
        resp = self._service.process_ex_warehouse_clearance(req)
        return resp.to_dict()

    def get_stats(self) -> dict[str, Any]:
        """Return system and database statistics."""
        return self._service.get_stats()

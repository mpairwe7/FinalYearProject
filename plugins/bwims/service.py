"""Sample system implementation for URA Bonded Warehouse Information Management System (BWIMS).

Simulates customs bonded cargo tracking under IM7 warehousing regime, statutory time-limit
monitoring (9 months max under EACCMA), and ex-warehouse IM4 clearances backed by an independent database.
"""

from __future__ import annotations

import datetime
import logging
import threading
from typing import TYPE_CHECKING, Any

from .database import BwimsDatabase
from .models import (
    ConsignmentStatus,
    ConsignmentStatusRequest,
    ConsignmentStatusResponse,
    ExWarehouseClearanceRequest,
    ExWarehouseClearanceResponse,
    WarehouseInventoryRequest,
    WarehouseInventoryResponse,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_UTC = getattr(datetime, "UTC", datetime.timezone.utc)  # noqa: UP017


class BwimsService:
    """Service simulator for BWIMS platform backed by an independent database."""

    def __init__(self, db: BwimsDatabase | None = None, db_path: Path | str | None = None) -> None:
        self._lock = threading.Lock()
        self._db = db or BwimsDatabase(db_path=db_path)

    @property
    def database(self) -> BwimsDatabase:
        return self._db

    def get_consignment_status(self, request: ConsignmentStatusRequest) -> ConsignmentStatusResponse:
        """Inspect bonded cargo status, days in warehouse, and overstay warning under EACCMA."""
        entry = self._db.get_consignment(request.entry_number)
        if not entry:
            return ConsignmentStatusResponse(
                ok=False,
                found=False,
                consignment=None,
                days_in_storage=0,
                is_overstayed=False,
                message=f"Consignment entry '{request.entry_number}' was not found in BWIMS registry.",
                error=f"Entry {request.entry_number} not found",
            )

        now = datetime.datetime.now(_UTC)
        try:
            wh_date = datetime.datetime.strptime(entry.warehousing_date, "%Y-%m-%d").replace(tzinfo=_UTC)
            days = (now - wh_date).days
        except Exception:
            days = 30

        is_overstayed = days > 270 or entry.status == ConsignmentStatus.OVERSTAYED_AUCTION_RISK

        msg = f"Consignment {entry.entry_number} is stored at {entry.warehouse_code} ({days} days in bond)."
        if is_overstayed:
            msg += " WARNING: Exceeded statutory 9-month warehousing limit under Section 67 EACCMA. At risk of public customs auction."

        return ConsignmentStatusResponse(
            ok=True,
            found=True,
            consignment=entry.to_dict(),
            days_in_storage=days,
            is_overstayed=is_overstayed,
            message=msg,
        )

    def get_warehouse_inventory(self, request: WarehouseInventoryRequest) -> WarehouseInventoryResponse:
        """Query all bonded stock inside a designated customs warehouse."""
        consignments = self._db.list_warehouse_consignments(
            warehouse_code=request.warehouse_code,
            importer_tin=request.importer_tin,
        )
        total_cif = sum(c.cif_value_ugx for c in consignments)
        return WarehouseInventoryResponse(
            ok=True,
            warehouse_code=request.warehouse_code,
            total_consignments=len(consignments),
            total_cif_ugx=round(total_cif, 2),
            consignments=[c.to_dict() for c in consignments],
        )

    def process_ex_warehouse_clearance(
        self, request: ExWarehouseClearanceRequest
    ) -> ExWarehouseClearanceResponse:
        """Process ex-warehouse entry for home consumption (IM4) or transit re-export."""
        with self._lock:
            entry = self._db.get_consignment(request.entry_number)
            if not entry:
                return ExWarehouseClearanceResponse(
                    ok=False,
                    clearance_id="",
                    entry_number=request.entry_number,
                    cleared_quantity=0.0,
                    remaining_quantity=0.0,
                    status="REJECTED",
                    message="Entry not found",
                    error=f"No bonded consignment found with entry '{request.entry_number}'",
                )

            available = entry.quantity - entry.cleared_quantity
            if request.cleared_quantity <= 0 or request.cleared_quantity > available:
                return ExWarehouseClearanceResponse(
                    ok=False,
                    clearance_id="",
                    entry_number=request.entry_number,
                    cleared_quantity=0.0,
                    remaining_quantity=available,
                    status="REJECTED",
                    message="Invalid clearance quantity",
                    error=f"Requested clearance quantity {request.cleared_quantity} exceeds available bonded stock {available}",
                )

            res = self._db.record_clearance(
                entry_number=request.entry_number,
                cleared_quantity=request.cleared_quantity,
                declaration_type=request.declaration_type,
                duty_paid_prn=request.duty_paid_prn,
            )

            return ExWarehouseClearanceResponse(
                ok=True,
                clearance_id=res["clearance_id"],
                entry_number=request.entry_number,
                cleared_quantity=request.cleared_quantity,
                remaining_quantity=res["remaining_quantity"],
                status=res["status"],
                message=f"Ex-warehouse clearance approved under {request.declaration_type}. Remaining bond quantity: {res['remaining_quantity']}",
            )

    def get_stats(self) -> dict[str, Any]:
        """Aggregate statistical metrics for BWIMS database."""
        return self._db.get_stats()

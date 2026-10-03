"""Sample system implementation for URA Digital Tax Stamps (DTS / Kakasa platform).

Simulates the track-and-trace monitoring system for excisable goods backed by an
independent SQLite database under Section 19B of the Excise Duty Act 2014.
"""

from __future__ import annotations

import datetime
import logging
import secrets
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from .database import DtsDatabase
from .models import (
    DamagedStampDeclarationRequest,
    DamagedStampDeclarationResponse,
    GazettedCategory,
    StampActivationRequest,
    StampActivationResponse,
    StampOrderRequest,
    StampOrderResponse,
    StampStatus,
    StampVerificationRequest,
    StampVerificationResponse,
    TaxpayerDtsProfile,
)

logger = logging.getLogger(__name__)

_UTC = getattr(datetime, "UTC", datetime.timezone.utc)  # noqa: UP017

# Statutory stamp fees per item in UGX
STAMP_UNIT_FEES: dict[GazettedCategory, float] = {
    GazettedCategory.BEER: 35.0,
    GazettedCategory.SPIRITS: 110.0,
    GazettedCategory.WINE: 100.0,
    GazettedCategory.BOTTLED_WATER: 15.0,
    GazettedCategory.SODA: 30.0,
    GazettedCategory.TOBACCO: 50.0,
    GazettedCategory.CEMENT: 135.0,
    GazettedCategory.SUGAR: 35.0,
    GazettedCategory.COOKING_OIL: 30.0,
    GazettedCategory.JUICES: 30.0,
}

SICPA_COLLECTION_POINT = (
    "SICPA Uganda Ltd, Henley Business Park, Ntinda Industrial Area, "
    "Kampala (Coordinates: 0.340632, 32.616436)"
)


class DigitalTaxStampsService:
    """Service simulator for URA DTS / Kakasa platform backed by an independent database."""

    def __init__(self, db: DtsDatabase | None = None, db_path: Path | str | None = None) -> None:
        self._lock = threading.Lock()
        self._db = db or DtsDatabase(db_path=db_path)

    @property
    def database(self) -> DtsDatabase:
        return self._db

    def _generate_prn(self) -> str:
        """Generate a 10-digit URA Payment Registration Number (PRN)."""
        now = datetime.datetime.now(_UTC)
        prefix = "2"  # 2 = Domestic Taxes / Excise
        return f"{prefix}{now.strftime('%y%m')}{10000 + secrets.randbelow(90000)}"

    def verify_stamp(self, request: StampVerificationRequest) -> StampVerificationResponse:
        """Look up a local sample status; this cannot authenticate a physical stamp."""
        stamp_code = request.stamp_code.strip()
        record = self._db.get_stamp(stamp_code)
        if not record:
            return StampVerificationResponse(
                ok=False,
                stamp_code=stamp_code,
                is_authentic=None,
                status=StampStatus.UNKNOWN,
                product_category="UNKNOWN",
                brand_name="UNVERIFIED COMMODITY",
                manufacturer_name="UNKNOWN",
                manufacturer_tin="",
                batch_number="",
                production_date="",
                expiry_date=None,
                error=(
                    f"Sample stamp code '{stamp_code}' is not in the local simulator fixtures. "
                    "This result does not establish whether a physical product is genuine or counterfeit. "
                    "Verify it through https://ura.go.ug or call URA on 0800 117 000 / 0800 217 000."
                ),
            )

        if record.status == StampStatus.UNACTIVATED:
            return StampVerificationResponse(
                ok=False,
                stamp_code=stamp_code,
                is_authentic=False,
                status=record.status,
                product_category=record.product_category.value,
                brand_name=record.brand_name,
                manufacturer_name=record.manufacturer_name,
                manufacturer_tin=record.manufacturer_tin,
                batch_number=record.batch_number,
                production_date=record.production_date,
                expiry_date=record.expiry_date,
                error="Stamp is registered but has NOT been activated on an accredited production line.",
            )

        if record.status == StampStatus.EXPIRED:
            return StampVerificationResponse(
                ok=True,
                stamp_code=stamp_code,
                is_authentic=True,
                status=record.status,
                product_category=record.product_category.value,
                brand_name=record.brand_name,
                manufacturer_name=record.manufacturer_name,
                manufacturer_tin=record.manufacturer_tin,
                batch_number=record.batch_number,
                production_date=record.production_date,
                expiry_date=record.expiry_date,
                message="Stamp is genuine but the product shelf life has EXPIRED.",
            )

        if record.status != StampStatus.GENUINE:
            return StampVerificationResponse(
                ok=False,
                stamp_code=stamp_code,
                is_authentic=False,
                status=record.status,
                product_category=record.product_category.value,
                brand_name=record.brand_name,
                manufacturer_name=record.manufacturer_name,
                manufacturer_tin=record.manufacturer_tin,
                batch_number=record.batch_number,
                production_date=record.production_date,
                expiry_date=record.expiry_date,
                message=f"Stamp status is {record.status.value}; verification failed for market release.",
                error=f"Stamp status is {record.status.value}; not authentic for retail circulation.",
            )

        return StampVerificationResponse(
            ok=True,
            stamp_code=stamp_code,
            is_authentic=True,
            status=record.status,
            product_category=record.product_category.value,
            brand_name=record.brand_name,
            manufacturer_name=record.manufacturer_name,
            manufacturer_tin=record.manufacturer_tin,
            batch_number=record.batch_number,
            production_date=record.production_date,
            expiry_date=record.expiry_date,
            message="Stamp verified as GENUINE and tax compliant under URA DTS regulations.",
        )

    def order_stamps(self, request: StampOrderRequest) -> StampOrderResponse:
        """Place an order for digital tax stamps and generate fee PRN."""
        with self._lock:
            tin = request.taxpayer_tin.strip()
            taxpayer = self._db.get_manufacturer(tin)
            if not taxpayer:
                return StampOrderResponse(
                    ok=False,
                    order_id="",
                    taxpayer_tin=tin,
                    product_category=request.product_category.value,
                    quantity=request.quantity,
                    unit_fee_ugx=0.0,
                    total_amount_ugx=0.0,
                    prn="",
                    payment_status="FAILED",
                    collection_point="",
                    order_status="REJECTED",
                    error=f"Taxpayer TIN '{tin}' is not registered for Digital Tax Stamps.",
                )

            if request.quantity <= 0:
                return StampOrderResponse(
                    ok=False,
                    order_id="",
                    taxpayer_tin=tin,
                    product_category=request.product_category.value,
                    quantity=request.quantity,
                    unit_fee_ugx=0.0,
                    total_amount_ugx=0.0,
                    prn="",
                    payment_status="FAILED",
                    collection_point="",
                    order_status="REJECTED",
                    error="Order quantity must be greater than zero.",
                )

            unit_fee = STAMP_UNIT_FEES.get(request.product_category, 30.0)
            total_amount = round(unit_fee * request.quantity, 2)
            prn = self._generate_prn()
            order_id = f"DTS-ORD-{datetime.datetime.now(_UTC).strftime('%y%m%d%H%M')}-{10000 + secrets.randbelow(90000)}"

            order_record = {
                "order_id": order_id,
                "taxpayer_tin": tin,
                "manufacturer_name": taxpayer.manufacturer_name,
                "product_category": request.product_category.value,
                "quantity": request.quantity,
                "unit_fee_ugx": unit_fee,
                "total_amount_ugx": total_amount,
                "prn": prn,
                "payment_status": "PENDING",
                "collection_point": SICPA_COLLECTION_POINT,
                "order_status": "AWAITING_PAYMENT",
                "created_at": datetime.datetime.now(_UTC).isoformat(),
            }
            self._db.insert_order(order_record)

            return StampOrderResponse(
                ok=True,
                order_id=order_id,
                taxpayer_tin=tin,
                product_category=request.product_category.value,
                quantity=request.quantity,
                unit_fee_ugx=unit_fee,
                total_amount_ugx=total_amount,
                prn=prn,
                payment_status="PENDING",
                collection_point=SICPA_COLLECTION_POINT,
                order_status="AWAITING_PAYMENT",
            )

    def activate_stamps(self, request: StampActivationRequest) -> StampActivationResponse:
        """Activate ordered stamps at factory line controller."""
        with self._lock:
            order = self._db.get_order(request.order_id.strip())
            if not order:
                return StampActivationResponse(
                    ok=False,
                    batch_id="",
                    order_id=request.order_id,
                    line_id=request.line_id,
                    activated_count=0,
                    timestamp="",
                    status="REJECTED",
                    error=f"Order ID '{request.order_id}' was not found.",
                )

            if order.get("order_status") == "ACTIVATED":
                return StampActivationResponse(
                    ok=False,
                    batch_id="",
                    order_id=request.order_id,
                    line_id=request.line_id,
                    activated_count=0,
                    timestamp="",
                    status="REJECTED",
                    error=f"Order '{request.order_id}' has already been activated.",
                )

            if order.get("payment_status") not in ("PAID", "PENDING"):
                return StampActivationResponse(
                    ok=False,
                    batch_id="",
                    order_id=request.order_id,
                    line_id=request.line_id,
                    activated_count=0,
                    timestamp="",
                    status="REJECTED",
                    error=f"Requisition order '{request.order_id}' has not been paid (current status: {order.get('payment_status')}). Payment is required before line activation.",
                )

            if len(request.stamp_serials) > order.get("quantity", 0):
                return StampActivationResponse(
                    ok=False,
                    batch_id="",
                    order_id=request.order_id,
                    line_id=request.line_id,
                    activated_count=0,
                    timestamp="",
                    status="REJECTED",
                    error=f"Submitted {len(request.stamp_serials)} stamps exceed ordered quantity of {order.get('quantity')}.",
                )

            line = self._db.get_packaging_line(request.line_id.strip())
            if line and line.get("manufacturer_tin") != order.get("taxpayer_tin"):
                return StampActivationResponse(
                    ok=False,
                    batch_id="",
                    order_id=request.order_id,
                    line_id=request.line_id,
                    activated_count=0,
                    timestamp="",
                    status="REJECTED",
                    error=f"Packaging line '{request.line_id}' does not belong to manufacturer TIN '{order.get('taxpayer_tin')}'.",
                )

            if not request.stamp_serials:
                return StampActivationResponse(
                    ok=False,
                    batch_id="",
                    order_id=request.order_id,
                    line_id=request.line_id,
                    activated_count=0,
                    timestamp="",
                    status="REJECTED",
                    error="No stamp serial numbers provided for activation.",
                )

            batch_id = f"ACT-BAT-{datetime.datetime.now(_UTC).strftime('%y%m%d%H%M')}-{100 + secrets.randbelow(900)}"
            now_iso = datetime.datetime.now(_UTC).isoformat()
            cat = GazettedCategory(order["product_category"])

            self._db.activate_batch(
                order_id=request.order_id.strip(),
                line_id=request.line_id.strip(),
                stamp_serials=request.stamp_serials,
                batch_id=batch_id,
                cat=cat,
                manufacturer_name=order["manufacturer_name"],
                taxpayer_tin=order["taxpayer_tin"],
            )

            return StampActivationResponse(
                ok=True,
                batch_id=batch_id,
                order_id=request.order_id,
                line_id=request.line_id,
                activated_count=len(request.stamp_serials),
                timestamp=now_iso,
                status="ACTIVATED",
            )

    def declare_damaged_stamps(
        self, request: DamagedStampDeclarationRequest
    ) -> DamagedStampDeclarationResponse:
        """Record damaged or spoiled stamps destroyed during packaging line jams."""
        with self._lock:
            tin = request.taxpayer_tin.strip()
            taxpayer = self._db.get_manufacturer(tin)
            if not taxpayer:
                return DamagedStampDeclarationResponse(
                    ok=False,
                    declaration_id="",
                    reconciled_count=0,
                    credit_allowable_ugx=0.0,
                    status="REJECTED",
                    message="",
                    error=f"Taxpayer TIN '{tin}' not found in the local DTS simulator fixtures.",
                )

            if not request.damaged_serials:
                return DamagedStampDeclarationResponse(
                    ok=False,
                    declaration_id="",
                    reconciled_count=0,
                    credit_allowable_ugx=0.0,
                    status="REJECTED",
                    message="",
                    error="At least one damaged stamp serial must be provided.",
                )

            valid_serials: list[str] = []
            for s in request.damaged_serials:
                st = self._db.get_stamp(s.strip())
                if st:
                    if st.manufacturer_tin != tin:
                        return DamagedStampDeclarationResponse(
                            ok=False,
                            declaration_id="",
                            reconciled_count=0,
                            credit_allowable_ugx=0.0,
                            status="REJECTED",
                            message="",
                            error=f"Stamp serial '{s}' does not belong to manufacturer TIN '{tin}'.",
                        )
                    if st.status == StampStatus.SPOILED:
                        return DamagedStampDeclarationResponse(
                            ok=False,
                            declaration_id="",
                            reconciled_count=0,
                            credit_allowable_ugx=0.0,
                            status="REJECTED",
                            message="",
                            error=f"Stamp serial '{s}' is already declared SPOILED.",
                        )
                valid_serials.append(s.strip())

            decl_id = f"DTS-DMG-{datetime.datetime.now(_UTC).strftime('%y%m%d%H%M')}-{1000 + secrets.randbelow(9000)}"
            unit_rate = 30.0  # standard base reconciliation allowance
            credit = round(len(valid_serials) * unit_rate, 2)

            decl_data = {
                "declaration_id": decl_id,
                "tin": tin,
                "damaged_count": len(valid_serials),
                "reason": request.incident_reason,
                "line_id": request.line_id,
                "credit_allowable_ugx": credit,
                "timestamp": datetime.datetime.now(_UTC).isoformat(),
            }
            self._db.insert_damaged_declaration(decl_data, valid_serials)

            return DamagedStampDeclarationResponse(
                ok=True,
                declaration_id=decl_id,
                reconciled_count=len(valid_serials),
                credit_allowable_ugx=credit,
                status="ACKNOWLEDGED",
                message=(
                    f"Declaration {decl_id} acknowledged. Submit physical remnants to URA excise station "
                    f"for final credit note reconciliation of {credit} UGX."
                ),
            )

    def get_taxpayer_profile(self, tin: str) -> TaxpayerDtsProfile | None:
        """Lookup manufacturer/importer DTS registration details."""
        return self._db.get_manufacturer(tin.strip())

    def list_gazetted_tariffs(self) -> dict[str, float]:
        """Return statutory unit fee tariff for all gazetted excisable goods."""
        return {k.value: v for k, v in STAMP_UNIT_FEES.items()}

    def get_stats(self) -> dict[str, Any]:
        """Return system and database statistics."""
        return self._db.get_stats()

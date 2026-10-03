"""Sample system implementation for URA Payment System (e-Services > Make a Payment suite).

Simulates PRN payment slip generation, expired PRN reactivation, card and mobile money checkout,
sample payment status verification, and advance motor vehicle tax scenarios backed by a local database.
"""

from __future__ import annotations

import datetime
import logging
import secrets
import threading
from typing import TYPE_CHECKING, Any

from .database import PaymentDatabase
from .models import (
    AdvanceTaxVerifyRequest,
    AdvanceTaxVerifyResponse,
    GeneratePrnRequest,
    GeneratePrnResponse,
    PaymentCheckoutRequest,
    PaymentCheckoutResponse,
    PaymentPrnRecord,
    PaymentStatusRequest,
    PaymentStatusResponse,
    PrnStatus,
    ReactivatePrnRequest,
    ReactivatePrnResponse,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_UTC = getattr(datetime, "UTC", datetime.timezone.utc)  # noqa: UP017


class PaymentService:
    """Service simulator for URA Payment System backed by an independent database."""

    def __init__(self, db: PaymentDatabase | None = None, db_path: Path | str | None = None) -> None:
        self._lock = threading.Lock()
        self._db = db or PaymentDatabase(db_path=db_path)

    @property
    def database(self) -> PaymentDatabase:
        return self._db

    def _generate_prn_number(self) -> str:
        """Generate a simulator-only 12-digit sample PRN starting with 2."""
        now = datetime.datetime.now(_UTC)
        prefix = f"2{now.strftime('%y%m')}"  # 5 digits: 2 + YYMM
        seq = f"{1000000 + secrets.randbelow(9000000)}"  # 7 digits
        return f"{prefix}{seq}"[:12]

    def generate_payment_slip(self, request: GeneratePrnRequest) -> GeneratePrnResponse:
        """Generate a new Payment Registration Number (PRN) payment slip with 21-day validity."""
        with self._lock:
            if request.amount_ugx <= 0:
                return GeneratePrnResponse(
                    ok=False,
                    prn="",
                    taxpayer_name=request.taxpayer_name,
                    amount_ugx=0.0,
                    tax_head=request.tax_head,
                    payment_channel=request.payment_channel.value,
                    expiry_date="",
                    status="REJECTED",
                    bank_barcode="",
                    payment_slip_url="",
                    message="Amount must be greater than zero",
                    error="Assessment payment amount must be greater than zero UGX",
                )

            prn = self._generate_prn_number()
            now = datetime.datetime.now(_UTC)
            now_iso = now.isoformat()
            expiry_date = (now + datetime.timedelta(days=21)).strftime("%Y-%m-%d")
            barcode = f"*URA-PRN-{prn}*"
            # Sample references must not link to the live URA payment portal.
            slip_url = ""

            record = PaymentPrnRecord(
                prn=prn,
                taxpayer_tin=request.taxpayer_tin,
                taxpayer_name=request.taxpayer_name,
                payment_category=request.payment_category,
                tax_head=request.tax_head,
                amount_ugx=round(request.amount_ugx, 2),
                payment_channel=request.payment_channel,
                status=PrnStatus.PENDING,
                created_at=now_iso,
                expiry_date=expiry_date,
                agency_details=request.agency_details,
            )
            self._db.insert_prn(record)

            logger.info("Generated PRN %s for '%s' (UGX %s)", prn, request.taxpayer_name, request.amount_ugx)
            return GeneratePrnResponse(
                ok=True,
                prn=prn,
                taxpayer_name=request.taxpayer_name,
                amount_ugx=record.amount_ugx,
                tax_head=request.tax_head,
                payment_channel=request.payment_channel.value,
                expiry_date=expiry_date,
                status="PENDING",
                bank_barcode=barcode,
                payment_slip_url=slip_url,
                message=(
                    f"Payment Registration Number (PRN) {prn} generated successfully. Valid until {expiry_date}. "
                    "Present this PRN at any commercial bank counter or pay via Mobile Money (*165# / *185#)."
                ),
            )

    def reactivate_expired_prn(self, request: ReactivatePrnRequest) -> ReactivatePrnResponse:
        """Renew validity on an expired PRN without having to re-declare tax liability."""
        with self._lock:
            prn_clean = request.prn.strip()
            record = self._db.get_prn(prn_clean)
            if not record:
                return ReactivatePrnResponse(
                    ok=False,
                    prn=prn_clean,
                    taxpayer_name="",
                    amount_ugx=0.0,
                    previous_expiry="",
                    new_expiry_date="",
                    status="NOT_FOUND",
                    message="PRN not found",
                    error=f"No payment registration record found for PRN '{prn_clean}'.",
                )

            if record.status == PrnStatus.CLEARED:
                return ReactivatePrnResponse(
                    ok=False,
                    prn=prn_clean,
                    taxpayer_name=record.taxpayer_name,
                    amount_ugx=record.amount_ugx,
                    previous_expiry=record.expiry_date,
                    new_expiry_date=record.expiry_date,
                    status="ALREADY_CLEARED",
                    message="PRN already settled",
                    error=f"Sample PRN {prn_clean} is already marked cleared in the local simulator on {record.cleared_at}.",
                )

            now = datetime.datetime.now(_UTC)
            new_expiry = (now + datetime.timedelta(days=21)).strftime("%Y-%m-%d")
            self._db.reactivate_prn(prn_clean, new_expiry)

            return ReactivatePrnResponse(
                ok=True,
                prn=prn_clean,
                taxpayer_name=record.taxpayer_name,
                amount_ugx=record.amount_ugx,
                previous_expiry=record.expiry_date,
                new_expiry_date=new_expiry,
                status="ACTIVE",
                message=f"PRN {prn_clean} has been successfully reactivated. New payment deadline is {new_expiry}.",
            )

    def view_payment_status(self, request: PaymentStatusRequest) -> PaymentStatusResponse:
        """Read sample clearance status from the local simulator database."""
        prn_clean = request.prn.strip()
        record = self._db.get_prn(prn_clean)
        if not record:
            return PaymentStatusResponse(
                ok=False,
                prn=prn_clean,
                taxpayer_name="",
                amount_ugx=0.0,
                status="NOT_FOUND",
                is_cleared=False,
                message=f"PRN '{prn_clean}' was not found in the local payment simulator.",
                error="PRN not found",
            )

        is_cleared = record.status == PrnStatus.CLEARED
        receipt_url = None

        msg = f"Sample PRN {prn_clean} for {record.taxpayer_name} is {record.status.value} in the local simulator."
        if is_cleared:
            msg += f" Simulated status date: {record.cleared_at}; no bank settlement was checked."
        else:
            msg += f" Sample expiry date: {record.expiry_date}."

        return PaymentStatusResponse(
            ok=True,
            prn=prn_clean,
            taxpayer_name=record.taxpayer_name,
            amount_ugx=record.amount_ugx,
            status=record.status.value,
            is_cleared=is_cleared,
            cleared_at=record.cleared_at,
            bank_reference=record.bank_reference,
            receipt_url=receipt_url,
            message=msg,
        )

    def process_checkout(self, request: PaymentCheckoutRequest) -> PaymentCheckoutResponse:
        """Process instant card (VISA/MasterCard) or Mobile Money checkout on the URA payment gateway."""
        with self._lock:
            prn_clean = request.prn.strip()
            record = self._db.get_prn(prn_clean)
            if not record:
                return PaymentCheckoutResponse(
                    ok=False,
                    prn=prn_clean,
                    transaction_id="",
                    receipt_number="",
                    amount_paid_ugx=0.0,
                    payment_method=request.payment_method,
                    status="FAILED",
                    cleared_at="",
                    message="PRN not found",
                    error=f"No payment record found matching PRN '{prn_clean}'",
                )

            if record.status == PrnStatus.CLEARED:
                return PaymentCheckoutResponse(
                    ok=False,
                    prn=prn_clean,
                    transaction_id=record.bank_reference or "",
                    receipt_number=f"REC-{prn_clean}",
                    amount_paid_ugx=record.amount_ugx,
                    payment_method=request.payment_method,
                    status="DUPLICATE_PAYMENT",
                    cleared_at=record.cleared_at or "",
                    message="PRN has already been cleared",
                    error="This PRN was already cleared. Duplicate payment refused.",
                )

            now = datetime.datetime.now(_UTC)
            now_iso = now.isoformat()
            tx_id = f"TX-URA-{now.strftime('%y%m%d%H%M')}-{100 + secrets.randbelow(900)}"
            receipt_num = f"URA-REC-{prn_clean}"

            self._db.record_checkout_transaction(
                prn=prn_clean,
                transaction_id=tx_id,
                payment_method=request.payment_method.upper(),
                payer_identifier=request.payer_identifier,
                amount_paid_ugx=request.amount_paid_ugx,
                receipt_number=receipt_num,
            )

            return PaymentCheckoutResponse(
                ok=True,
                prn=prn_clean,
                transaction_id=tx_id,
                receipt_number=receipt_num,
                amount_paid_ugx=request.amount_paid_ugx,
                payment_method=request.payment_method.upper(),
                status="CLEARED",
                cleared_at=now_iso,
                message=(
                    f"Simulation recorded UGX {request.amount_paid_ugx:,.2f} for {request.payment_method.upper()}; "
                    f"no settlement occurred. Sample transaction: {tx_id}. Sample receipt reference: {receipt_num}."
                ),
            )

    def verify_advance_tax(self, request: AdvanceTaxVerifyRequest) -> AdvanceTaxVerifyResponse:
        """Verify advance income tax payment on commercial passenger (PSV) or freight motor vehicles."""
        vehicle_reg = request.vehicle_registration_number.strip().upper()
        record = self._db.get_advance_tax(vehicle_reg)
        if not record:
            return AdvanceTaxVerifyResponse(
                ok=True,
                vehicle_registration_number=vehicle_reg,
                vehicle_type="COMMERCIAL_VEHICLE",
                capacity=14,
                rate_basis="UGX 20,000 per passenger seat or UGX 50,000 per freight tonne",
                advance_tax_assessed_ugx=280000.0,
                is_compliant=False,
                prn="",
                valid_until="",
                message=(
                    f"No advance income tax clearance found for vehicle '{vehicle_reg}'. "
                    "Commercial vehicles must settle advance tax before TLB route license renewal."
                ),
            )

        return AdvanceTaxVerifyResponse(
            ok=True,
            vehicle_registration_number=record["vehicle_registration_number"],
            vehicle_type=record["vehicle_type"],
            capacity=record["capacity"],
            rate_basis="Statutory Advance Tax Schedule under Income Tax Act",
            advance_tax_assessed_ugx=record["amount_ugx"],
            is_compliant=record["status"] == "COMPLIANT",
            prn=record["prn"],
            valid_until=record["valid_until"],
            message=f"Advance tax verified for {vehicle_reg}. Status: COMPLIANT until {record['valid_until']}.",
        )

    def get_stats(self) -> dict[str, Any]:
        """Aggregate statistical metrics for URA Payment System."""
        return self._db.get_stats()

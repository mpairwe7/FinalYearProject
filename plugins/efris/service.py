"""Sample system implementation for URA EFRIS (Electronic Fiscal Receipting and Invoicing System).

Simulates the core business logic, validation rules, and transactional persistence
of the official URA EFRIS platform backed by an independent SQLite database.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import secrets
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from .database import EfrisDatabase
from .models import (
    CreditNoteRequest,
    CreditNoteResponse,
    DocumentStatus,
    FiscalInvoiceRequest,
    FiscalInvoiceResponse,
    InvoiceVerificationRequest,
    InvoiceVerificationResponse,
    TaxpayerEfrisProfile,
)

logger = logging.getLogger(__name__)

_UTC = getattr(datetime, "UTC", datetime.timezone.utc)  # noqa: UP017


class EfrisService:
    """Service simulator for URA EFRIS platform backed by an independent database."""

    def __init__(self, db: EfrisDatabase | None = None, db_path: Path | str | None = None) -> None:
        self._lock = threading.Lock()
        self._db = db or EfrisDatabase(db_path=db_path)

    @property
    def database(self) -> EfrisDatabase:
        return self._db

    def _generate_fdn(self) -> str:
        """Generate official 20-digit Fiscal Document Number (FDN)."""
        now = datetime.datetime.now(_UTC)
        prefix = "01"  # 01 = Standard Fiscal Invoice
        date_part = now.strftime("%y%m%d")  # 6 digits: YYMMDD
        seq = f"{100000000000 + secrets.randbelow(900000000000)}"  # 12 digits
        return f"{prefix}{date_part}{seq}"[:20]

    def _generate_verification_code(self, fdn: str, total: float) -> str:
        """Generate a 6-character fiscal verification code."""
        digest = hashlib.sha256(f"{fdn}:{total}:ura_fiscal_salt".encode()).hexdigest()
        return digest[:6].upper()

    def get_taxpayer_profile(self, tin: str) -> TaxpayerEfrisProfile | None:
        """Lookup taxpayer EFRIS registration status."""
        return self._db.get_taxpayer(tin.strip())

    def issue_fiscal_invoice(self, request: FiscalInvoiceRequest) -> FiscalInvoiceResponse:
        """Generate and fiscalize an invoice in real-time."""
        with self._lock:
            seller_tin = request.seller_tin.strip()
            taxpayer = self._db.get_taxpayer(seller_tin)
            if not taxpayer:
                return FiscalInvoiceResponse(
                    ok=False,
                    fdn="",
                    verification_code="",
                    qr_code_url="",
                    seller_tin=seller_tin,
                    buyer_tin=request.buyer_tin,
                    currency=request.currency,
                    net_amount=0.0,
                    tax_amount=0.0,
                    gross_amount=0.0,
                    status=DocumentStatus.CANCELLED,
                    issued_at="",
                    error=f"Seller TIN {seller_tin} is not registered for EFRIS",
                )

            if not request.items:
                return FiscalInvoiceResponse(
                    ok=False,
                    fdn="",
                    verification_code="",
                    qr_code_url="",
                    seller_tin=seller_tin,
                    buyer_tin=request.buyer_tin,
                    currency=request.currency,
                    net_amount=0.0,
                    tax_amount=0.0,
                    gross_amount=0.0,
                    status=DocumentStatus.CANCELLED,
                    issued_at="",
                    error="Invoice must contain at least one item",
                )

            # Compute totals
            net_amount = 0.0
            tax_amount = 0.0
            for item in request.items:
                net_amount += item.net_amount
                tax_amount += item.tax_amount

            gross_amount = round(net_amount + tax_amount, 2)
            net_amount = round(net_amount, 2)
            tax_amount = round(tax_amount, 2)

            fdn = self._generate_fdn()
            verification_code = self._generate_verification_code(fdn, gross_amount)
            issued_at = datetime.datetime.now(_UTC).isoformat()
            qr_url = f"https://efris.ura.go.ug/verify?fdn={fdn}&code={verification_code}"

            # Stock balance deduction if inventory tracked for this seller
            for item in request.items:
                self._db.deduct_stock(seller_tin, item.commodity_code, item.quantity)

            buyer_name = request.buyer_name
            if not buyer_name and request.buyer_tin:
                buyer_tp = self._db.get_taxpayer(request.buyer_tin.strip())
                buyer_name = buyer_tp.business_name if buyer_tp else "Registered Taxpayer"

            record = {
                "fdn": fdn,
                "verification_code": verification_code,
                "qr_code_url": qr_url,
                "seller_tin": seller_tin,
                "seller_name": taxpayer.business_name,
                "buyer_tin": request.buyer_tin,
                "buyer_name": buyer_name,
                "currency": request.currency,
                "net_amount": net_amount,
                "tax_amount": tax_amount,
                "gross_amount": gross_amount,
                "status": DocumentStatus.ISSUED,
                "issued_at": issued_at,
                "items_count": len(request.items),
                "items": [it.to_dict() for it in request.items],
                "offline_ref": request.offline_reference,
            }
            self._db.insert_invoice(record)

            logger.info("EFRIS issued FDN %s for TIN %s (gross=%s UGX)", fdn, seller_tin, gross_amount)
            return FiscalInvoiceResponse(
                ok=True,
                fdn=fdn,
                verification_code=verification_code,
                qr_code_url=qr_url,
                seller_tin=seller_tin,
                buyer_tin=request.buyer_tin,
                currency=request.currency,
                net_amount=net_amount,
                tax_amount=tax_amount,
                gross_amount=gross_amount,
                status=DocumentStatus.ISSUED,
                issued_at=issued_at,
            )

    def verify_invoice(self, request: InvoiceVerificationRequest) -> InvoiceVerificationResponse:
        """Validate an FDN against the EFRIS fiscal registry."""
        fdn = request.fdn.strip()
        record = self._db.get_invoice(fdn)
        if not record:
            return InvoiceVerificationResponse(
                ok=False,
                fdn=fdn,
                is_authentic=False,
                status=DocumentStatus.CANCELLED,
                seller_tin="",
                seller_name="",
                buyer_tin=None,
                buyer_name=None,
                issue_date="",
                net_amount=0.0,
                tax_amount=0.0,
                gross_amount=0.0,
                currency="UGX",
                items_count=0,
                error=f"Fiscal Document Number (FDN) '{fdn}' was not found in URA EFRIS records.",
            )

        if request.verification_code:
            expected_code = record["verification_code"].upper()
            if request.verification_code.strip().upper() != expected_code:
                return InvoiceVerificationResponse(
                    ok=False,
                    fdn=fdn,
                    is_authentic=False,
                    status=DocumentStatus.CANCELLED,
                    seller_tin=record["seller_tin"],
                    seller_name=record["seller_name"],
                    buyer_tin=record["buyer_tin"],
                    buyer_name=record["buyer_name"],
                    issue_date=record["issued_at"],
                    net_amount=record["net_amount"],
                    tax_amount=record["tax_amount"],
                    gross_amount=record["gross_amount"],
                    currency=record["currency"],
                    items_count=record["items_count"],
                    error="Verification code mismatch — invoice may be forged or tampered with.",
                )

        return InvoiceVerificationResponse(
            ok=True,
            fdn=fdn,
            is_authentic=True,
            status=record["status"],
            seller_tin=record["seller_tin"],
            seller_name=record["seller_name"],
            buyer_tin=record["buyer_tin"],
            buyer_name=record["buyer_name"],
            issue_date=record["issued_at"],
            net_amount=record["net_amount"],
            tax_amount=record["tax_amount"],
            gross_amount=record["gross_amount"],
            currency=record["currency"],
            items_count=record["items_count"],
            message="Invoice successfully verified as authentic on URA EFRIS.",
        )

    def apply_credit_note(self, request: CreditNoteRequest) -> CreditNoteResponse:
        """Create a credit note against an existing fiscal invoice."""
        with self._lock:
            original_fdn = request.original_fdn.strip()
            record = self._db.get_invoice(original_fdn)
            if not record:
                return CreditNoteResponse(
                    ok=False,
                    credit_note_number="",
                    original_fdn=original_fdn,
                    adjusted_gross=0.0,
                    adjusted_vat=0.0,
                    status="REJECTED",
                    message="Original invoice not found",
                    error=f"No issued invoice matches FDN '{original_fdn}'",
                )

            if request.seller_tin.strip() != record["seller_tin"]:
                return CreditNoteResponse(
                    ok=False,
                    credit_note_number="",
                    original_fdn=original_fdn,
                    adjusted_gross=0.0,
                    adjusted_vat=0.0,
                    status="REJECTED",
                    message="TIN mismatch",
                    error="Credit note seller TIN does not match the original invoice seller TIN",
                )

            if request.adjusted_amount <= 0 or request.adjusted_amount > record["gross_amount"]:
                return CreditNoteResponse(
                    ok=False,
                    credit_note_number="",
                    original_fdn=original_fdn,
                    adjusted_gross=0.0,
                    adjusted_vat=0.0,
                    status="REJECTED",
                    message="Invalid adjustment amount",
                    error=(
                        f"Adjustment amount {request.adjusted_amount} must be between "
                        f"1 and the original gross amount {record['gross_amount']} UGX"
                    ),
                )

            cn_num = f"CN-{datetime.datetime.now(_UTC).strftime('%y%m%d')}-{1000 + secrets.randbelow(9000)}"
            # Standard VAT component adjustment (18/118 of gross)
            adjusted_vat = round((request.adjusted_amount / 1.18) * 0.18, 2)
            adjusted_gross = round(request.adjusted_amount, 2)

            cn_record = {
                "credit_note_number": cn_num,
                "original_fdn": original_fdn,
                "seller_tin": request.seller_tin,
                "reason": request.reason.value,
                "adjusted_gross": adjusted_gross,
                "adjusted_vat": adjusted_vat,
                "description": request.description,
                "status": "APPROVED",
                "created_at": datetime.datetime.now(_UTC).isoformat(),
            }
            self._db.insert_credit_note(cn_record)

            return CreditNoteResponse(
                ok=True,
                credit_note_number=cn_num,
                original_fdn=original_fdn,
                adjusted_gross=adjusted_gross,
                adjusted_vat=adjusted_vat,
                status="APPROVED",
                message=f"Credit note {cn_num} approved for {adjusted_gross} UGX under {request.reason.value}",
            )

    def get_stock_balance(self, tin: str, commodity_code: str | None = None) -> list[dict[str, Any]]:
        """Query inventory stock balances for an EFRIS registered taxpayer."""
        return self._db.get_stock_items(tin.strip(), commodity_code)

    def record_stock_in(
        self,
        tin: str,
        commodity_code: str,
        description: str,
        quantity: float,
        unit_cost: float,
        unit_of_measure: str = "PIECES",
        category: str = "General",
    ) -> dict[str, Any]:
        """Record local purchase or customs import stock-in."""
        with self._lock:
            tin = tin.strip()
            updated_qty = self._db.record_stock_in(
                tin=tin,
                commodity_code=commodity_code,
                description=description,
                quantity=quantity,
                unit_cost=unit_cost,
                unit_of_measure=unit_of_measure,
                category=category,
            )
            return {
                "ok": True,
                "tin": tin,
                "commodity_code": commodity_code,
                "updated_quantity": updated_qty,
                "message": f"Stock-in recorded successfully for commodity {commodity_code}",
            }

    def sync_offline_batch(self, offline_invoices: list[FiscalInvoiceRequest]) -> dict[str, Any]:
        """Process batch of invoices cached offline during network blackout (up to 5 days)."""
        synced: list[str] = []
        errors: list[dict[str, Any]] = []

        for req in offline_invoices:
            resp = self.issue_fiscal_invoice(req)
            if resp.ok:
                synced.append(resp.fdn)
            else:
                errors.append({"offline_ref": req.offline_reference, "error": resp.error})

        return {
            "ok": len(errors) == 0,
            "total_submitted": len(offline_invoices),
            "synced_count": len(synced),
            "error_count": len(errors),
            "synced_fdns": synced,
            "errors": errors,
        }

    def get_stats(self) -> dict[str, Any]:
        """Return system and database statistics."""
        return self._db.get_stats()

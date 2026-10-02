"""Sample system implementation for URA TIN Registration System.

Simulates instant individual TIN issuance with NIN verification, non-individual corporate
TIN generation linked to URSB, and tax obligations management backed by an independent SQLite database.
"""

from __future__ import annotations

import datetime
import logging
import secrets
import threading
from typing import TYPE_CHECKING, Any

from .database import TinDatabase
from .models import (
    InstantTinRequest,
    InstantTinResponse,
    NonIndividualTinRequest,
    NonIndividualTinResponse,
    TaxHeadType,
    TaxObligation,
    TaxObligationUpdateRequest,
    TaxObligationUpdateResponse,
    TaxpayerCategory,
    TaxpayerRecord,
    TinSearchRequest,
    TinSearchResponse,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_UTC = getattr(datetime, "UTC", datetime.timezone.utc)  # noqa: UP017


class TinRegistrationService:
    """Service simulator for URA TIN Registration platform backed by an independent database."""

    def __init__(self, db: TinDatabase | None = None, db_path: Path | str | None = None) -> None:
        self._lock = threading.Lock()
        self._db = db or TinDatabase(db_path=db_path)

    @property
    def database(self) -> TinDatabase:
        return self._db

    def _generate_tin(self) -> str:
        """Generate official 10-digit URA Tax Identification Number."""
        for _ in range(10):
            tin = f"1{100000000 + secrets.randbelow(900000000)}"
            if not self._db.search_taxpayer(tin, exact=True):
                return tin
        return f"1{100000000 + secrets.randbelow(900000000)}"

    def search_taxpayer(self, request: TinSearchRequest) -> TinSearchResponse:
        """Search taxpayer by 10-digit TIN, National ID (NIN), or URSB Number."""
        record = self._db.search_taxpayer(request.query)
        if not record:
            return TinSearchResponse(
                ok=True,
                found=False,
                taxpayer=None,
                message=f"No active taxpayer found matching '{request.query}'. Please verify your credentials or apply for a TIN.",
            )

        return TinSearchResponse(
            ok=True,
            found=True,
            taxpayer=record.to_dict(),
            message=f"Active taxpayer found: {record.legal_name} (TIN: {record.tin}).",
        )

    def apply_instant_individual_tin(self, request: InstantTinRequest) -> InstantTinResponse:
        """Process Instant Individual TIN application for citizens with National ID (NIN)."""
        with self._lock:
            nin_clean = request.nin.strip().upper()
            if len(nin_clean) != 14:
                return InstantTinResponse(
                    ok=False,
                    tin="",
                    legal_name=request.full_name,
                    nin=nin_clean,
                    registration_date="",
                    status="REJECTED",
                    default_obligations=[],
                    certificate_reference="",
                    message="Invalid NIN format",
                    error="National Identification Number (NIN) must be exactly 14 alphanumeric characters per NIRA standards.",
                )

            # Check if NIN already has a TIN
            existing = self._db.search_taxpayer(nin_clean, exact=True)
            if existing:
                return InstantTinResponse(
                    ok=False,
                    tin=existing.tin,
                    legal_name=existing.legal_name,
                    nin=nin_clean,
                    registration_date=existing.registration_date,
                    status="DUPLICATE",
                    default_obligations=[ob.tax_head.value for ob in existing.obligations],
                    certificate_reference="",
                    message="Taxpayer already registered",
                    error=f"An active TIN ({existing.tin}) is already linked to National ID {nin_clean}.",
                )

            tin = self._generate_tin()
            now_iso = datetime.datetime.now(_UTC).isoformat()
            date_str = now_iso[:10]
            cert_ref = f"TIN-CERT-{date_str.replace('-', '')}-{100 + secrets.randbelow(900)}"

            obligations = [
                TaxObligation(
                    tax_head=TaxHeadType.INCOME_TAX_INDIVIDUAL,
                    effective_from=date_str,
                    status="ACTIVE",
                    filing_frequency="ANNUAL",
                )
            ]

            record = TaxpayerRecord(
                tin=tin,
                legal_name=request.full_name,
                category=TaxpayerCategory.INDIVIDUAL,
                nin_or_passport=nin_clean,
                ursb_reg_no=None,
                mobile=request.mobile,
                email=request.email,
                registered_office=f"{request.district} Central",
                district=request.district,
                registration_date=date_str,
                status="ACTIVE",
                obligations=obligations,
            )
            self._db.insert_taxpayer(record)

            logger.info("Issued instant TIN %s to %s (NIN: %s)", tin, request.full_name, nin_clean)
            return InstantTinResponse(
                ok=True,
                tin=tin,
                legal_name=request.full_name,
                nin=nin_clean,
                registration_date=date_str,
                status="ACTIVE",
                default_obligations=["INCOME_TAX_INDIVIDUAL"],
                certificate_reference=cert_ref,
                message=f"Congratulations! Your Instant Individual TIN is {tin}. Certificate reference: {cert_ref}.",
            )

    def apply_non_individual_tin(self, request: NonIndividualTinRequest) -> NonIndividualTinResponse:
        """Process Non-Individual Company TIN registration linked to URSB."""
        with self._lock:
            ursb_no = request.ursb_registration_number.strip().upper()
            existing = self._db.search_taxpayer(ursb_no, exact=True)
            if existing:
                return NonIndividualTinResponse(
                    ok=False,
                    tin=existing.tin,
                    business_name=existing.legal_name,
                    ursb_registration_number=ursb_no,
                    category=existing.category.value,
                    registration_date=existing.registration_date,
                    status="DUPLICATE",
                    active_tax_heads=[ob.tax_head.value for ob in existing.obligations],
                    message="Entity already registered for taxes",
                    error=f"URSB Entity '{ursb_no}' is already registered under TIN {existing.tin}.",
                )

            tin = self._generate_tin()
            now_iso = datetime.datetime.now(_UTC).isoformat()
            date_str = now_iso[:10]

            obligations: list[TaxObligation] = []
            for head_str in request.requested_tax_heads:
                head_clean = head_str.upper()
                if head_clean in TaxHeadType._value2member_map_:
                    th = TaxHeadType(head_clean)
                    freq = "MONTHLY" if th in (TaxHeadType.VAT_STANDARD, TaxHeadType.PAYE, TaxHeadType.WITHHOLDING_TAX) else "ANNUAL"
                    obligations.append(
                        TaxObligation(
                            tax_head=th,
                            effective_from=date_str,
                            status="ACTIVE",
                            filing_frequency=freq,
                        )
                    )

            if not obligations:
                obligations.append(
                    TaxObligation(
                        tax_head=TaxHeadType.CORPORATION_TAX,
                        effective_from=date_str,
                        status="ACTIVE",
                        filing_frequency="ANNUAL",
                    )
                )

            record = TaxpayerRecord(
                tin=tin,
                legal_name=request.business_name,
                category=request.category,
                nin_or_passport=ursb_no,
                ursb_reg_no=ursb_no,
                mobile=request.mobile,
                email=request.email,
                registered_office=request.registered_office,
                district=request.district,
                registration_date=date_str,
                status="ACTIVE",
                obligations=obligations,
            )
            self._db.insert_taxpayer(record)

            active_heads = [ob.tax_head.value for ob in obligations]
            logger.info("Issued Non-Individual TIN %s to '%s' (URSB: %s)", tin, request.business_name, ursb_no)
            return NonIndividualTinResponse(
                ok=True,
                tin=tin,
                business_name=request.business_name,
                ursb_registration_number=ursb_no,
                category=request.category.value,
                registration_date=date_str,
                status="ACTIVE",
                active_tax_heads=active_heads,
                message=f"Non-Individual TIN {tin} registered successfully for '{request.business_name}'. Active tax obligations: {', '.join(active_heads)}.",
            )

    def update_tax_obligation(self, request: TaxObligationUpdateRequest) -> TaxObligationUpdateResponse:
        """Register or update statutory tax head obligations for a taxpayer."""
        with self._lock:
            record = self._db.search_taxpayer(request.tin.strip(), exact=True)
            if not record:
                return TaxObligationUpdateResponse(
                    ok=False,
                    tin=request.tin,
                    legal_name="",
                    active_obligations=[],
                    message="Taxpayer not found",
                    error=f"No taxpayer found with TIN '{request.tin}'",
                )

            if request.tax_head not in {ob.tax_head for ob in record.obligations}:
                self._db.add_obligation(record.tin, request.tax_head)
            updated = self._db.search_taxpayer(record.tin, exact=True)
            active_heads = [ob.tax_head.value for ob in (updated.obligations if updated else [])]

            return TaxObligationUpdateResponse(
                ok=True,
                tin=record.tin,
                legal_name=record.legal_name,
                active_obligations=active_heads,
                message=f"Tax head '{request.tax_head.value}' successfully registered for {record.legal_name} (TIN: {record.tin}).",
            )

    def get_stats(self) -> dict[str, Any]:
        """Aggregate statistical metrics for TIN Registration database."""
        return self._db.get_stats()

"""Sample system implementation for Uganda Registration Services Bureau (URSB).

Simulates the company registry, business names formalization, Form 20 director records,
and compliance checks backed by an independent SQLite database.
"""

from __future__ import annotations

import datetime
import logging
import secrets
import threading
from typing import TYPE_CHECKING, Any

from .database import UrsbDatabase
from .models import (
    BusinessEntity,
    BusinessRegistrationRequest,
    BusinessRegistrationResponse,
    BusinessSearchRequest,
    BusinessSearchResponse,
    ComplianceCheckResponse,
    DirectorInfo,
    EntityStatus,
    EntityType,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_UTC = getattr(datetime, "UTC", datetime.timezone.utc)  # noqa: UP017


class UrsbService:
    """Service simulator for URSB platform backed by an independent database."""

    def __init__(self, db: UrsbDatabase | None = None, db_path: Path | str | None = None) -> None:
        self._lock = threading.Lock()
        self._db = db or UrsbDatabase(db_path=db_path)

    @property
    def database(self) -> UrsbDatabase:
        return self._db

    def search_business(self, request: BusinessSearchRequest) -> BusinessSearchResponse:
        """Search registered company or business name by registration number or name."""
        entity = self._db.search_entity(request.query)
        if not entity:
            return BusinessSearchResponse(
                ok=True,
                found=False,
                entity=None,
                message=f"No registered entity found matching query '{request.query}'. Business may be informal or unverified.",
            )

        return BusinessSearchResponse(
            ok=True,
            found=True,
            entity=entity.to_dict(),
            message=f"Sample entity '{entity.business_name}' found in the local URSB simulator.",
        )

    def register_business(self, request: BusinessRegistrationRequest) -> BusinessRegistrationResponse:
        """Create a sample business record and registration reference in the local simulator."""
        with self._lock:
            existing = self._db.search_entity(request.business_name, exact=True)
            if existing:
                return BusinessRegistrationResponse(
                    ok=False,
                    registration_number="",
                    business_name=request.business_name,
                    entity_type=request.entity_type.value,
                    registration_date="",
                    status="REJECTED",
                    certificate_reference="",
                    message="Business name already exists",
                    error=f"The name '{request.business_name}' already exists in the local URSB simulator.",
                )

            prefix = "URSB-CO" if request.entity_type == EntityType.LIMITED_COMPANY else "URSB-BN"
            reg_no = f"{prefix}-{10000 + secrets.randbelow(90000)}"
            now_iso = datetime.datetime.now(_UTC).isoformat()
            date_str = now_iso[:10]
            cert_ref = f"CERT-{datetime.datetime.now(_UTC).strftime('%y%m%d')}-{100 + secrets.randbelow(900)}"

            directors = [
                DirectorInfo(
                    full_name=d.get("full_name", "Director"),
                    nin_or_passport=d.get("nin_or_passport", request.applicant_nin),
                    nationality=d.get("nationality", "Ugandan"),
                    role=d.get("role", "DIRECTOR"),
                    tin=d.get("tin"),
                    shares_percentage=float(d.get("shares_percentage", 100.0 / max(1, len(request.directors)))),
                )
                for d in request.directors
            ]
            if not directors:
                directors = [
                    DirectorInfo(
                        full_name="Primary Applicant",
                        nin_or_passport=request.applicant_nin,
                        nationality="Ugandan",
                        role="PROPRIETOR",
                        shares_percentage=100.0,
                    )
                ]

            entity = BusinessEntity(
                registration_number=reg_no,
                business_name=request.business_name,
                entity_type=request.entity_type,
                registration_date=date_str,
                status=EntityStatus.ACTIVE,
                registered_office=request.registered_office,
                district=request.district,
                nature_of_business=request.nature_of_business,
                directors=directors,
                latest_annual_returns_year=int(date_str[:4]),
                form_20_registered=True,
            )
            self._db.insert_entity(entity)

            logger.info("URSB registered '%s' under number %s", request.business_name, reg_no)
            return BusinessRegistrationResponse(
                ok=True,
                registration_number=reg_no,
                business_name=request.business_name,
                entity_type=request.entity_type.value,
                registration_date=date_str,
                status="ACTIVE",
                certificate_reference=cert_ref,
                message=(
                    f"Sample business record created in the local simulator. Reference: '{reg_no}'. "
                    "This is not a real URSB registration."
                ),
            )

    def check_compliance(self, registration_number: str) -> ComplianceCheckResponse:
        """Read sample compliance fields from the local simulator; this is not a legal-status check."""
        entity = self._db.search_entity(registration_number, exact=True)
        if not entity:
            return ComplianceCheckResponse(
                ok=False,
                registration_number=registration_number,
                business_name="",
                is_legally_active=False,
                annual_returns_up_to_date=False,
                form_20_on_file=False,
                ready_for_ura_tin=False,
                status="NOT_FOUND",
                details="Registration number not found in URSB database.",
                error=f"No entity registered under '{registration_number}'",
            )

        is_active = entity.status == EntityStatus.ACTIVE
        curr_year = datetime.datetime.now(_UTC).year
        annual_up_to_date = entity.latest_annual_returns_year >= (curr_year - 1)
        form_20 = entity.form_20_registered
        ready_for_tin = is_active and form_20 and annual_up_to_date

        details_parts = []
        if is_active:
            details_parts.append("Company is legally active.")
        else:
            details_parts.append(f"Company status is {entity.status.value}.")

        if form_20:
            details_parts.append(f"Form 20 registered with {len(entity.directors)} declared directors.")
        else:
            details_parts.append("Form 20 particulars of directors missing.")

        if annual_up_to_date:
            details_parts.append(f"Annual returns filed up to year {entity.latest_annual_returns_year}.")
        else:
            details_parts.append(
                f"Annual returns overdue (latest filed: {entity.latest_annual_returns_year}). File returns before URA registration."
            )

        return ComplianceCheckResponse(
            ok=True,
            registration_number=entity.registration_number,
            business_name=entity.business_name,
            is_legally_active=is_active,
            annual_returns_up_to_date=annual_up_to_date,
            form_20_on_file=form_20,
            ready_for_ura_tin=ready_for_tin,
            status=entity.status.value,
            details=" ".join(details_parts),
        )

    def get_stats(self) -> dict[str, Any]:
        """Aggregate statistical metrics for URSB database."""
        return self._db.get_stats()

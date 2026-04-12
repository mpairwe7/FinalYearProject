"""Pydantic v2 models for auth, profile, consent."""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Auth primitives
# ---------------------------------------------------------------------------
class AuthUser(BaseModel):
    """A user resolved from a verified JWT.

    This is the minimum we need to scope downstream operations:
    who they are, which tenant they belong to, what role they have,
    and (optionally) which consent purposes they've granted.
    """

    user_id: str = Field(..., description="Stable unique id from the OIDC `sub` claim")
    tenant_id: str = Field("default", description="Tenant the user belongs to (RLS scope)")
    email: str = Field("", description="Email claim if present")
    role: Literal["public", "verified_taxpayer", "ura_staff", "ura_admin", "ura_auditor"] = "public"
    locale: str = Field("en", description="ISO 639-1 preferred language")
    granted_purposes: list[str] = Field(
        default_factory=list,
        description="List of consent purposes the user has granted",
    )
    token_issued_at: float = Field(default_factory=time.time)
    token_expires_at: float = Field(default_factory=lambda: time.time() + 3600)

    @property
    def is_authenticated(self) -> bool:
        return bool(self.user_id)

    @property
    def is_staff(self) -> bool:
        return self.role in ("ura_staff", "ura_admin", "ura_auditor")


# ---------------------------------------------------------------------------
# User profile (stored in DB, editable by the user)
# ---------------------------------------------------------------------------
class UserProfile(BaseModel):
    """The user's tax/personalization profile.

    Stored in the ``user_profiles`` table (JSONB column), editable via
    ``PUT /v1/me/profile``.  Every field is used downstream to scope
    retrieval filters, tool visibility, prompt rendering, and memory
    personalization.
    """

    user_id: str
    taxpayer_type: Literal[
        "unknown",
        "individual",
        "sole_trader",
        "company",
        "partnership",
        "ngo",
        "non_resident",
    ] = "unknown"
    industry: str = Field("", max_length=100, description="Free-text industry or sector")
    primary_language: Literal["en", "lg"] = "en"
    detail_level: Literal["beginner", "intermediate", "expert"] = "intermediate"
    registered_tax_types: list[str] = Field(
        default_factory=list,
        description="e.g. ['vat', 'paye', 'cit']",
    )
    fiscal_year: str = Field("FY2025-26", description="Current FY the user files under")
    display_name: str = Field("", max_length=128)
    updated_at: float = Field(default_factory=time.time)


class ProfileUpdateRequest(BaseModel):
    """Patch payload — only fields set here are updated."""

    taxpayer_type: str | None = None
    industry: str | None = Field(None, max_length=100)
    primary_language: Literal["en", "lg"] | None = None
    detail_level: Literal["beginner", "intermediate", "expert"] | None = None
    registered_tax_types: list[str] | None = None
    display_name: str | None = Field(None, max_length=128)


# ---------------------------------------------------------------------------
# Consent receipts (UDPA 2019 + AU DPF 2022)
# ---------------------------------------------------------------------------
class ConsentReceipt(BaseModel):
    """A signed, versioned record of user consent to a processing purpose.

    One row per purpose per user per version.  Withdrawal creates a
    new row with ``withdrawn_at`` set — we never delete the original
    so the audit trail is preserved.
    """

    receipt_id: str
    user_id: str
    purpose: Literal[
        "personalization",  # memory, profile, tailored prompts
        "analytics",  # usage statistics, quality metrics
        "ticket_escalation",  # passing query + context to URA staff
        "long_term_storage",  # retention beyond default TTL
        "ura_account_access",  # tool access to URA account data
    ]
    version: str = Field(..., description="Version tag of the consent text, e.g. '2026-04'")
    granted_at: float = Field(default_factory=time.time)
    withdrawn_at: float | None = None
    legal_basis: Literal["consent", "public_task", "legal_obligation"] = "consent"

    @property
    def is_active(self) -> bool:
        return self.withdrawn_at is None


class ConsentGrantRequest(BaseModel):
    """Request payload for POST /v1/me/consents/grant."""

    purposes: list[str] = Field(..., min_length=1, description="Purposes to grant")
    version: str = Field(..., description="Current consent text version")


class ConsentWithdrawRequest(BaseModel):
    purposes: list[str] = Field(..., min_length=1)

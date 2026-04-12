"""Tests for Phase 14 — identity, tenancy, consent, subject rights."""

from __future__ import annotations

import time

import pytest

from app.auth.dependencies import (
    AuthContext,
    _claims_to_user,
    current_user,
    require_role,
    require_user,
    reset_verifier,
)
from app.auth.jwt_auth import JWTAuthError, JWTVerifier, make_dev_token
from app.auth.models import (
    AuthUser,
    ConsentGrantRequest,
    ConsentReceipt,
    ConsentWithdrawRequest,
    ProfileUpdateRequest,
    UserProfile,
)


# ---------------------------------------------------------------------------
# JWT verifier
# ---------------------------------------------------------------------------
class TestJWTVerifier:
    def test_roundtrip_happy_path(self):
        token = make_dev_token("alice@test", tenant_id="t1", role="verified_taxpayer")
        claims = JWTVerifier().verify(token)
        assert claims["sub"] == "alice@test"
        assert claims["tenant_id"] == "t1"
        assert claims["role"] == "verified_taxpayer"

    def test_empty_token_rejected(self):
        with pytest.raises(JWTAuthError, match="empty"):
            JWTVerifier().verify("")

    def test_malformed_token_rejected(self):
        with pytest.raises(JWTAuthError, match="malformed"):
            JWTVerifier().verify("not.a.jwt.really")

    def test_invalid_signature_rejected(self):
        token = make_dev_token("alice", secret="secret-a")
        with pytest.raises(JWTAuthError, match="signature"):
            JWTVerifier(dev_secret="secret-b").verify(token)

    def test_expired_token_rejected(self):
        token = make_dev_token("alice", ttl_seconds=-10)
        with pytest.raises(JWTAuthError, match="expired"):
            JWTVerifier().verify(token)

    def test_granted_purposes_default_empty_list(self):
        token = make_dev_token("alice")
        claims = JWTVerifier().verify(token)
        assert claims["granted_purposes"] == []

    def test_granted_purposes_roundtrip(self):
        token = make_dev_token(
            "alice",
            granted_purposes=["personalization", "analytics"],
        )
        claims = JWTVerifier().verify(token)
        assert claims["granted_purposes"] == ["personalization", "analytics"]

    def test_unsupported_alg_raises(self):
        with pytest.raises(JWTAuthError, match="unsupported"):
            JWTVerifier(alg="ES256")


class TestClaimsToUser:
    def test_basic_mapping(self):
        claims = {
            "sub": "alice",
            "tenant_id": "t1",
            "email": "a@b",
            "role": "verified_taxpayer",
            "locale": "lg",
            "granted_purposes": ["personalization"],
            "iat": 1000.0,
            "exp": 5000.0,
        }
        u = _claims_to_user(claims)
        assert u.user_id == "alice"
        assert u.tenant_id == "t1"
        assert u.email == "a@b"
        assert u.role == "verified_taxpayer"
        assert u.locale == "lg"
        assert u.is_authenticated
        assert u.is_staff is False

    def test_staff_role_detected(self):
        u = _claims_to_user({"sub": "x", "role": "ura_staff"})
        assert u.is_staff is True

    def test_default_role_public(self):
        u = _claims_to_user({"sub": "x"})
        assert u.role == "public"

    def test_non_list_granted_purposes_coerced(self):
        u = _claims_to_user({"sub": "x", "granted_purposes": "bogus"})
        assert u.granted_purposes == []


# ---------------------------------------------------------------------------
# AuthContext
# ---------------------------------------------------------------------------
class TestAuthContext:
    def test_unauthenticated_defaults(self):
        ctx = AuthContext()
        assert not ctx.is_authenticated
        assert ctx.user_id == ""
        assert ctx.tenant_id == "default"
        assert ctx.role == "public"
        assert not ctx.has_purpose("personalization")

    def test_authenticated_bound_user(self):
        user = AuthUser(
            user_id="alice",
            tenant_id="t1",
            role="verified_taxpayer",
            granted_purposes=["personalization"],
        )
        ctx = AuthContext(authenticated=True, user=user)
        assert ctx.is_authenticated
        assert ctx.user_id == "alice"
        assert ctx.tenant_id == "t1"
        assert ctx.role == "verified_taxpayer"
        assert ctx.has_purpose("personalization")
        assert not ctx.has_purpose("analytics")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class TestUserProfile:
    def test_defaults(self):
        p = UserProfile(user_id="alice")
        assert p.taxpayer_type == "unknown"
        assert p.primary_language == "en"
        assert p.detail_level == "intermediate"
        assert p.fiscal_year == "FY2025-26"

    def test_role_literal_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            UserProfile(user_id="alice", detail_level="expert_mode")

    def test_profile_update_request_partial(self):
        body = ProfileUpdateRequest(industry="retail", primary_language="lg")
        dumped = body.model_dump(exclude_unset=True)
        assert dumped == {"industry": "retail", "primary_language": "lg"}


class TestConsentModels:
    def test_grant_request_requires_nonempty(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ConsentGrantRequest(purposes=[], version="2026-04")

    def test_receipt_is_active_default(self):
        r = ConsentReceipt(
            receipt_id="r1",
            user_id="u1",
            purpose="personalization",
            version="2026-04",
        )
        assert r.is_active is True

    def test_receipt_withdrawn_not_active(self):
        r = ConsentReceipt(
            receipt_id="r1",
            user_id="u1",
            purpose="analytics",
            version="2026-04",
            withdrawn_at=time.time(),
        )
        assert r.is_active is False


# ---------------------------------------------------------------------------
# Database CRUD (uses tmp_db fixture)
# ---------------------------------------------------------------------------
class TestUserCRUD:
    def test_upsert_user_creates_row(self, tmp_db):
        row = tmp_db.upsert_user(
            external_id="alice@test",
            tenant_id="t1",
            email="a@b",
            role="verified_taxpayer",
        )
        assert row["external_id"] == "alice@test"
        assert row["tenant_id"] == "t1"
        assert row["role"] == "verified_taxpayer"
        assert len(row["id"]) == 36

    def test_upsert_is_idempotent_on_tenant_plus_external(self, tmp_db):
        row1 = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        row2 = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        assert row1["id"] == row2["id"]

    def test_upsert_same_external_different_tenant_distinct(self, tmp_db):
        r1 = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        r2 = tmp_db.upsert_user(external_id="alice", tenant_id="t2")
        assert r1["id"] != r2["id"]

    def test_get_user_roundtrip(self, tmp_db):
        row = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        fetched = tmp_db.get_user(row["id"])
        assert fetched["id"] == row["id"]

    def test_get_user_unknown(self, tmp_db):
        assert tmp_db.get_user("nonexistent") is None


class TestUserProfileCRUD:
    def test_first_upsert_creates_with_defaults(self, tmp_db):
        u = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        p = tmp_db.upsert_user_profile(u["id"], {})
        assert p["taxpayer_type"] == "unknown"
        assert p["primary_language"] == "en"
        assert p["fiscal_year"] == "FY2025-26"

    def test_update_patches_partial(self, tmp_db):
        u = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        tmp_db.upsert_user_profile(u["id"], {"taxpayer_type": "sole_trader"})
        p = tmp_db.upsert_user_profile(u["id"], {"industry": "retail"})
        # Previous taxpayer_type survives
        assert p["taxpayer_type"] == "sole_trader"
        assert p["industry"] == "retail"

    def test_registered_tax_types_list_roundtrip(self, tmp_db):
        u = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        tmp_db.upsert_user_profile(
            u["id"],
            {"registered_tax_types": ["vat", "paye"]},
        )
        p = tmp_db.get_user_profile(u["id"])
        assert p["registered_tax_types"] == ["vat", "paye"]

    def test_unknown_keys_dropped(self, tmp_db):
        u = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        p = tmp_db.upsert_user_profile(u["id"], {"malicious_field": "x"})
        assert "malicious_field" not in p


class TestConsentCRUD:
    def test_grant_consent(self, tmp_db):
        u = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        r = tmp_db.grant_consent(u["id"], "personalization", "2026-04")
        assert r["purpose"] == "personalization"
        assert r["withdrawn_at"] is None

    def test_grant_idempotent_on_same_version(self, tmp_db):
        u = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        r1 = tmp_db.grant_consent(u["id"], "personalization", "2026-04")
        r2 = tmp_db.grant_consent(u["id"], "personalization", "2026-04")
        # Same receipt returned the second time
        assert r1["receipt_id"] == r2["receipt_id"]

    def test_withdraw_and_has_active(self, tmp_db):
        u = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        tmp_db.grant_consent(u["id"], "personalization", "2026-04")
        assert tmp_db.has_active_consent(u["id"], "personalization")
        tmp_db.withdraw_consent(u["id"], "personalization")
        assert not tmp_db.has_active_consent(u["id"], "personalization")

    def test_withdraw_count(self, tmp_db):
        u = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        tmp_db.grant_consent(u["id"], "personalization", "2026-04")
        tmp_db.grant_consent(u["id"], "analytics", "2026-04")
        # withdraw_consent only touches one purpose
        count = tmp_db.withdraw_consent(u["id"], "personalization")
        assert count == 1
        # Analytics still active
        assert tmp_db.has_active_consent(u["id"], "analytics")

    def test_list_active_consents(self, tmp_db):
        u = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        tmp_db.grant_consent(u["id"], "personalization", "2026-04")
        tmp_db.grant_consent(u["id"], "analytics", "2026-04")
        actives = tmp_db.get_active_consents(u["id"])
        assert len(actives) == 2
        assert {a["purpose"] for a in actives} == {"personalization", "analytics"}


class TestSubjectRights:
    def test_export_returns_full_snapshot(self, tmp_db):
        u = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        tmp_db.upsert_user_profile(u["id"], {"taxpayer_type": "sole_trader"})
        tmp_db.grant_consent(u["id"], "personalization", "2026-04")
        exp = tmp_db.export_user_data(u["id"])
        assert exp["user"]["id"] == u["id"]
        assert exp["profile"]["taxpayer_type"] == "sole_trader"
        assert len(exp["consents"]) == 1

    def test_cascade_erasure(self, tmp_db):
        u = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        tmp_db.upsert_user_profile(u["id"], {"industry": "retail"})
        tmp_db.grant_consent(u["id"], "personalization", "2026-04")
        counts = tmp_db.delete_user_cascade(u["id"])
        assert counts["users"] == 1
        assert counts["user_profiles"] == 1
        assert counts["consent_receipts"] == 1
        # User really gone
        assert tmp_db.get_user(u["id"]) is None

    def test_cascade_erasure_empty_user_returns_zeroes(self, tmp_db):
        counts = tmp_db.delete_user_cascade("nonexistent-uuid")
        assert counts["users"] == 0

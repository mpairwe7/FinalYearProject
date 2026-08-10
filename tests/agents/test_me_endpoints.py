"""End-to-end integration tests for /v1/me/* endpoints.

Uses FastAPI ``TestClient`` so the full request stack is exercised
(middleware, CORS, auth dependencies, route handlers) without
needing a live uvicorn + Qdrant + Redis.  Runs offline in <1s.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from app.auth.dependencies import current_user, require_user, reset_verifier
from app.auth.jwt_auth import make_dev_token
from app.auth.models import ConsentGrantRequest, ConsentWithdrawRequest, ProfileUpdateRequest


@dataclass
class _Response:
    status_code: int
    body: dict[str, Any]

    def json(self) -> dict[str, Any]:
        return self.body


class _DirectMeClient:
    """Small handler-level client for /v1/me tests.

    It preserves JWT verification, `require_user`, Pydantic validation, and
    the real SQLite persistence layer while avoiding full ASGI lifespan
    startup in unit CI.
    """

    def _request(self, method: str, path: str, *, headers=None, json=None) -> _Response:
        from app import main

        headers = headers or {}
        auth = headers.get("Authorization")
        request = Request(
            {
                "type": "http",
                "method": method,
                "path": path,
                "headers": [],
                "client": ("testclient", 50000),
                "server": ("testserver", 80),
                "scheme": "http",
                "query_string": b"",
            }
        )

        try:
            ctx = current_user(request, auth)
            if path == "/v1/me" and method == "GET":
                return _Response(200, main.me_whoami(ctx))

            ctx = require_user(ctx)
            if path == "/v1/me/profile" and method == "GET":
                return _Response(200, main.me_get_profile(ctx))
            if path == "/v1/me/profile" and method == "PUT":
                return _Response(200, main.me_update_profile(ProfileUpdateRequest(**(json or {})), ctx))
            if path == "/v1/me/consents" and method == "GET":
                return _Response(200, main.me_list_consents(ctx))
            if path == "/v1/me/consents/grant" and method == "POST":
                return _Response(200, main.me_grant_consent(ConsentGrantRequest(**(json or {})), ctx))
            if path == "/v1/me/consents/withdraw" and method == "POST":
                return _Response(200, main.me_withdraw_consent(ConsentWithdrawRequest(**(json or {})), ctx))
            if path == "/v1/me/export" and method == "GET":
                return _Response(200, main.me_export(ctx))
            if path == "/v1/me" and method == "DELETE":
                return _Response(200, main.me_forget(ctx))
        except HTTPException as exc:
            return _Response(exc.status_code, {"detail": exc.detail})
        except ValidationError as exc:
            return _Response(422, {"detail": exc.errors()})

        return _Response(404, {"detail": "not found"})

    def get(self, path: str, *, headers=None) -> _Response:
        return self._request("GET", path, headers=headers)

    def put(self, path: str, *, headers=None, json=None) -> _Response:
        return self._request("PUT", path, headers=headers, json=json)

    def post(self, path: str, *, headers=None, json=None) -> _Response:
        return self._request("POST", path, headers=headers, json=json)

    def delete(self, path: str, *, headers=None) -> _Response:
        return self._request("DELETE", path, headers=headers)


@pytest.fixture
def client(tmp_db, monkeypatch):
    """Fresh handler-level client bound to the tmp in-memory DB."""
    reset_verifier()
    return _DirectMeClient()


def _mint_token(user_id: str, role: str = "verified_taxpayer", granted=None):
    # Use the verifier's default secret + audience so we don't need
    # to re-bind the singleton (see fixture docstring).
    return make_dev_token(
        user_id,
        tenant_id="default",
        email=f"{user_id}@test",
        role=role,
        granted_purposes=granted or [],
    )


# ---------------------------------------------------------------------------
# /v1/me — whoami
# ---------------------------------------------------------------------------
class TestWhoami:
    def test_anonymous_returns_public(self, client):
        r = client.get("/v1/me")
        assert r.status_code == 200
        body = r.json()
        assert body["authenticated"] is False
        assert body["role"] == "public"

    def test_authenticated_returns_user(self, client):
        token = _mint_token("alice", role="verified_taxpayer")
        r = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        body = r.json()
        assert body["authenticated"] is True
        assert body["external_id"] == "alice"
        assert body["role"] == "verified_taxpayer"
        assert "user_id" in body

    def test_invalid_token_rejected(self, client):
        r = client.get("/v1/me", headers={"Authorization": "Bearer not.a.jwt"})
        assert r.status_code == 401

    def test_expired_token_rejected(self, client):
        token = make_dev_token("alice", ttl_seconds=-10)
        r = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# /v1/me/profile — GET + PUT
# ---------------------------------------------------------------------------
class TestProfile:
    def test_get_profile_authenticated(self, client):
        token = _mint_token("alice")
        r = client.get("/v1/me/profile", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        body = r.json()
        # First-time profile is created with defaults
        assert body["taxpayer_type"] == "unknown"
        assert body["primary_language"] == "en"

    def test_put_profile_updates_fields(self, client):
        token = _mint_token("alice")
        r = client.put(
            "/v1/me/profile",
            headers={"Authorization": f"Bearer {token}"},
            json={"taxpayer_type": "sole_trader", "industry": "retail"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["taxpayer_type"] == "sole_trader"
        assert body["industry"] == "retail"

    def test_put_profile_partial_update_preserves_others(self, client):
        token = _mint_token("alice")
        client.put(
            "/v1/me/profile",
            headers={"Authorization": f"Bearer {token}"},
            json={"taxpayer_type": "sole_trader", "industry": "retail"},
        )
        r = client.put(
            "/v1/me/profile",
            headers={"Authorization": f"Bearer {token}"},
            json={"primary_language": "lg"},
        )
        body = r.json()
        assert body["taxpayer_type"] == "sole_trader"   # preserved
        assert body["industry"] == "retail"             # preserved
        assert body["primary_language"] == "lg"         # updated

    def test_put_profile_rejects_bad_literal(self, client):
        token = _mint_token("alice")
        r = client.put(
            "/v1/me/profile",
            headers={"Authorization": f"Bearer {token}"},
            json={"detail_level": "expert_mode"},    # not in Literal
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# /v1/me/consents — grant + list + withdraw
# ---------------------------------------------------------------------------
class TestConsents:
    def test_initial_list_empty(self, client):
        token = _mint_token("alice")
        r = client.get("/v1/me/consents", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["consents"] == []

    def test_grant_returns_receipts(self, client):
        token = _mint_token("alice")
        r = client.post(
            "/v1/me/consents/grant",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "purposes": ["personalization", "analytics"],
                "version": "2026-04",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["granted"]) == 2
        purposes = {g["purpose"] for g in body["granted"]}
        assert purposes == {"personalization", "analytics"}

    def test_grant_then_list_shows_active(self, client):
        token = _mint_token("alice")
        client.post(
            "/v1/me/consents/grant",
            headers={"Authorization": f"Bearer {token}"},
            json={"purposes": ["personalization"], "version": "2026-04"},
        )
        r = client.get("/v1/me/consents", headers={"Authorization": f"Bearer {token}"})
        consents = r.json()["consents"]
        assert len(consents) == 1
        assert consents[0]["purpose"] == "personalization"
        assert consents[0]["withdrawn_at"] is None

    def test_withdraw_returns_counts(self, client):
        token = _mint_token("alice")
        client.post(
            "/v1/me/consents/grant",
            headers={"Authorization": f"Bearer {token}"},
            json={"purposes": ["personalization", "analytics"], "version": "2026-04"},
        )
        r = client.post(
            "/v1/me/consents/withdraw",
            headers={"Authorization": f"Bearer {token}"},
            json={"purposes": ["personalization"]},
        )
        assert r.status_code == 200
        assert r.json()["withdrawn"] == {"personalization": 1}
        # List now shows only analytics
        r2 = client.get("/v1/me/consents", headers={"Authorization": f"Bearer {token}"})
        active = r2.json()["consents"]
        assert len(active) == 1
        assert active[0]["purpose"] == "analytics"

    def test_grant_empty_purposes_rejected(self, client):
        token = _mint_token("alice")
        r = client.post(
            "/v1/me/consents/grant",
            headers={"Authorization": f"Bearer {token}"},
            json={"purposes": [], "version": "2026-04"},
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# /v1/me/export — UDPA data portability
# ---------------------------------------------------------------------------
class TestExport:
    def test_export_returns_full_snapshot(self, client):
        token = _mint_token("alice")
        # Populate profile + consent first
        client.put(
            "/v1/me/profile",
            headers={"Authorization": f"Bearer {token}"},
            json={"taxpayer_type": "sole_trader", "industry": "retail"},
        )
        client.post(
            "/v1/me/consents/grant",
            headers={"Authorization": f"Bearer {token}"},
            json={"purposes": ["personalization"], "version": "2026-04"},
        )
        r = client.get("/v1/me/export", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        body = r.json()
        assert body["user"]["external_id"] == "alice"
        assert body["profile"]["taxpayer_type"] == "sole_trader"
        assert len(body["consents"]) == 1


# ---------------------------------------------------------------------------
# DELETE /v1/me — UDPA right to erasure
# ---------------------------------------------------------------------------
class TestErasure:
    def test_delete_cascades(self, client):
        token = _mint_token("alice")
        client.put(
            "/v1/me/profile",
            headers={"Authorization": f"Bearer {token}"},
            json={"industry": "retail"},
        )
        client.post(
            "/v1/me/consents/grant",
            headers={"Authorization": f"Bearer {token}"},
            json={"purposes": ["personalization"], "version": "2026-04"},
        )
        r = client.delete("/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        counts = r.json()["deleted"]
        assert counts["users"] == 1
        assert counts["user_profiles"] == 1
        assert counts["consent_receipts"] == 1

    def test_delete_then_whoami_creates_fresh_user(self, client):
        token = _mint_token("alice")
        # Create + delete
        client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
        client.delete("/v1/me", headers={"Authorization": f"Bearer {token}"})
        # New whoami upserts a fresh row
        r = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["external_id"] == "alice"


# ---------------------------------------------------------------------------
# End-to-end workflow — the full subject-rights cycle
# ---------------------------------------------------------------------------
class TestFullFlow:
    def test_onboard_consent_update_export_delete(self, client):
        token = _mint_token("bob")

        # 1. Whoami (creates user row)
        r = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert r.json()["external_id"] == "bob"

        # 2. Set profile
        r = client.put(
            "/v1/me/profile",
            headers={"Authorization": f"Bearer {token}"},
            json={"taxpayer_type": "company", "industry": "import_export"},
        )
        assert r.json()["taxpayer_type"] == "company"

        # 3. Grant consents
        client.post(
            "/v1/me/consents/grant",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "purposes": ["personalization", "analytics", "ticket_escalation"],
                "version": "2026-04",
            },
        )

        # 4. Verify list
        r = client.get("/v1/me/consents", headers={"Authorization": f"Bearer {token}"})
        assert len(r.json()["consents"]) == 3

        # 5. Export
        r = client.get("/v1/me/export", headers={"Authorization": f"Bearer {token}"})
        body = r.json()
        assert body["user"]["external_id"] == "bob"
        assert len(body["consents"]) == 3

        # 6. Withdraw one consent
        client.post(
            "/v1/me/consents/withdraw",
            headers={"Authorization": f"Bearer {token}"},
            json={"purposes": ["analytics"]},
        )
        r = client.get("/v1/me/consents", headers={"Authorization": f"Bearer {token}"})
        active = {c["purpose"] for c in r.json()["consents"]}
        assert active == {"personalization", "ticket_escalation"}

        # 7. Erasure
        r = client.delete("/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200

        # 8. Post-erasure whoami creates fresh user
        r = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        # New user_id differs from the first whoami
        assert r.json()["external_id"] == "bob"

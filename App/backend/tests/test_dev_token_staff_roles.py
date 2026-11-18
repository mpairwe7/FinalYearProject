"""Outside development and test, a development token cannot make anyone staff.

Any ``APP_ENV`` other than ``production`` used to mint and accept HS256
development tokens, and ``POST /v1/auth/dev-token`` handed out ``ura_admin`` to
an unauthenticated request. The HF Space runs with such an ``APP_ENV``.
"""

from __future__ import annotations

import os

os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("SPEECH_ENABLED", "false")
os.environ.setdefault("QDRANT_ENABLED", "false")
os.environ.setdefault("ANALYTICS_BACKEND", "sqlite")
os.environ.setdefault("OTEL_ENABLED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.auth import jwt_auth  # noqa: E402
from app.main import app  # noqa: E402

DEMO = {"user_id": "10000" + "23456", "role": "verified_taxpayer", "tenant_id": "default"}


@pytest.fixture
def staging(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setattr(jwt_auth, "APP_ENV", "staging")


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.parametrize("body", [{"role": "ura_admin"}, {"email": "admin@ura.go.ug"}, {"role": "ura_staff"}])
def test_outside_development_the_endpoint_mints_no_staff_token(staging, client, body):
    assert client.post("/v1/auth/dev-token", json=body).status_code == 403


def test_outside_development_only_the_portal_demo_taxpayer_is_minted(staging, client):
    demo = client.post("/v1/auth/dev-token", json=DEMO)
    assert demo.status_code == 200 and demo.json()["role"] == "verified_taxpayer"
    other = {**DEMO, "user_id": "someone-else"}
    assert client.post("/v1/auth/dev-token", json=other).status_code == 403


def test_in_development_a_staff_dev_token_is_still_minted(monkeypatch, client):
    monkeypatch.setenv("APP_ENV", "development")
    response = client.post("/v1/auth/dev-token", json={"role": "ura_admin"})
    assert response.status_code == 200 and response.json()["role"] == "ura_admin"


def test_outside_development_a_dev_signed_staff_token_is_refused(staging):
    staff = jwt_auth.make_dev_token(user_id="u1", role="ura_admin")
    with pytest.raises(jwt_auth.JWTAuthError, match="staff"):
        jwt_auth._hs256_verify(staff, jwt_auth.AUTH_DEV_SECRET)
    taxpayer = jwt_auth.make_dev_token(user_id="u1", role="verified_taxpayer")
    assert jwt_auth._hs256_verify(taxpayer, jwt_auth.AUTH_DEV_SECRET)["sub"] == "u1"


def test_in_development_a_dev_signed_staff_token_is_accepted(monkeypatch):
    monkeypatch.setattr(jwt_auth, "APP_ENV", "development")
    staff = jwt_auth.make_dev_token(user_id="u1", role="ura_admin")
    assert jwt_auth._hs256_verify(staff, jwt_auth.AUTH_DEV_SECRET)["sub"] == "u1"

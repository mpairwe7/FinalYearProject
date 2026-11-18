"""Outside development and test, a development token cannot make anyone staff.

Any ``APP_ENV`` other than ``production`` used to mint and accept HS256
development tokens, and ``POST /v1/auth/dev-token`` handed out ``ura_admin`` to
an unauthenticated request. The HF Space runs with such an ``APP_ENV``.
"""

from __future__ import annotations

import json
import os
import time

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


def _signed(claims: dict) -> str:
    header = jwt_auth._b64url_encode(b'{"alg":"HS256","typ":"JWT"}')
    payload = jwt_auth._b64url_encode(json.dumps(claims).encode())
    return f"{header}.{payload}.{jwt_auth._hs256_sign(header, payload, jwt_auth.AUTH_DEV_SECRET)}"


def test_outside_development_a_dev_signed_staff_token_is_refused(staging):
    staff = jwt_auth.make_dev_token(user_id=DEMO["user_id"], role="ura_admin")
    with pytest.raises(jwt_auth.JWTAuthError, match="staff"):
        jwt_auth._hs256_verify(staff, jwt_auth.AUTH_DEV_SECRET)
    demo = jwt_auth.make_dev_token(user_id=DEMO["user_id"], role="verified_taxpayer")
    assert jwt_auth._hs256_verify(demo, jwt_auth.AUTH_DEV_SECRET)["sub"] == DEMO["user_id"]


def test_outside_development_a_dev_token_cannot_name_another_taxpayer(staging):
    forged = jwt_auth.make_dev_token(user_id="someone-else", role="verified_taxpayer")
    with pytest.raises(jwt_auth.JWTAuthError, match="demo taxpayer"):
        jwt_auth._hs256_verify(forged, jwt_auth.AUTH_DEV_SECRET)


def test_outside_development_a_staff_role_under_the_audience_is_refused(staging):
    """The gate reads roles as _claims_to_user does, audience-scoped paths included."""
    from app.auth.dependencies import _get_verifier

    audience = _get_verifier().audience or jwt_auth.OIDC_AUDIENCE
    now = int(time.time())
    hidden = _signed({
        "sub": DEMO["user_id"], "iat": now, "exp": now + 600, "role": "public",
        "resource_access": {audience: {"roles": ["ura_admin"]}},
    })
    with pytest.raises(jwt_auth.JWTAuthError, match="staff"):
        jwt_auth._hs256_verify(hidden, jwt_auth.AUTH_DEV_SECRET)


def test_outside_development_an_expired_dev_token_is_refused(staging):
    now = int(time.time())
    expired = _signed({"sub": DEMO["user_id"], "iat": now - 7200, "exp": now - 3600, "role": "verified_taxpayer"})
    with pytest.raises(jwt_auth.JWTAuthError, match="expired"):
        jwt_auth._hs256_verify(expired, jwt_auth.AUTH_DEV_SECRET)


def test_in_development_a_dev_signed_staff_token_is_accepted(monkeypatch):
    monkeypatch.setattr(jwt_auth, "APP_ENV", "development")
    staff = jwt_auth.make_dev_token(user_id="u1", role="ura_admin")
    assert jwt_auth._hs256_verify(staff, jwt_auth.AUTH_DEV_SECRET)["sub"] == "u1"

import pytest
from fastapi.testclient import TestClient
from app.main import app

def test_gzip_compression_on_large_payloads():
    client = TestClient(app)
    # Request openapi.json (large payload) with gzip accepted
    response = client.get("/openapi.json", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"

def test_no_gzip_when_client_does_not_accept_gzip():
    client = TestClient(app)
    response = client.get("/openapi.json", headers={"Accept-Encoding": "identity"})
    assert response.status_code == 200
    assert response.headers.get("content-encoding") is None

def test_no_gzip_for_small_payloads():
    client = TestClient(app)
    # /health is tiny (< 500 bytes)
    response = client.get("/health", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers.get("content-encoding") is None

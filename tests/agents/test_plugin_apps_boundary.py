from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

from plugins.apps.common_ui import install_simulator_safety_boundary


def _simulator_test_app() -> FastAPI:
    app = FastAPI()
    install_simulator_safety_boundary(app)

    @app.get("/")
    async def index():
        return PlainTextResponse("SIMULATION ONLY")

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok"})

    @app.get("/mcp/manifest")
    async def legacy_mcp():
        return JSONResponse({"tools": []})

    @app.post("/api/v1/returns/file")
    async def simulated_write():
        return JSONResponse({"success": True})

    return app


@pytest.mark.asyncio
async def test_legacy_app_boundary_marks_simulations_and_retires_fake_mcp(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    transport = httpx.ASGITransport(app=_simulator_test_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/")
        assert page.status_code == 200
        assert page.text == "SIMULATION ONLY"
        assert page.headers["X-Connector-Mode"] == "simulation"
        assert page.headers["X-Connector-Live"] == "false"

        legacy_mcp = await client.get("/mcp/manifest")
        assert legacy_mcp.status_code == 410
        assert "not an MCP JSON-RPC transport" in legacy_mcp.json()["detail"]


@pytest.mark.asyncio
async def test_production_boundary_allows_health_only(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    transport = httpx.ASGITransport(app=_simulator_test_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        root = await client.get("/")
        assert root.status_code == 503
        post = await client.post("/api/v1/returns/file")
        assert post.status_code == 503

        health = await client.get("/health")
        assert health.status_code == 200
        assert health.headers["X-Connector-Mode"] == "simulation"

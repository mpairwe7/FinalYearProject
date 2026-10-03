"""TIN attachment privacy behavior and the standalone connector server."""

from __future__ import annotations

import pytest
from app.documents import DocumentRecord
from app.service import ChatModel
from app.vision.ocr import (
    extract_national_id_card_data,
    extract_nin_numbers,
    extract_phone_numbers,
)
from fastapi.testclient import TestClient

from plugins.server import app as plugins_server_app


# ---------------------------------------------------------------------------
# National ID OCR Extraction Tests
# ---------------------------------------------------------------------------
class TestNationalIdOcrExtraction:
    def test_extract_nin_numbers(self):
        text = "REPUBLIC OF UGANDA NATIONAL ID CARD NO: CM950019284KLA GIVEN NAMES: SARAH"
        nins = extract_nin_numbers(text)
        assert len(nins) == 1
        assert nins[0] == "CM950019284KLA"

    def test_extract_phone_numbers(self):
        text = "Please reach me on +256772123456 or 0701998877 for my registration."
        phones = extract_phone_numbers(text)
        assert len(phones) >= 1
        assert any("256772123456" in p or "0772123456" in p for p in phones)

    def test_extract_national_id_card_data(self):
        raw_ocr = (
            "THE REPUBLIC OF UGANDA \n"
            "NATIONAL IDENTIFICATION CARD \n"
            "Surname: NAMUBIRU \n"
            "Given Names: SARAH \n"
            "NIN: CF920019284HJA \n"
            "Date of Birth: 14/08/1992 \n"
            "District of Birth: MUKONO \n"
            "Card No: 009812481"
        )
        data = extract_national_id_card_data(raw_ocr)
        assert data["is_national_id"] is True
        assert data["nin"] == "CF920019284HJA"
        assert "NAMUBIRU" in data["full_name"]
        assert "14/08/1992" in data["date_of_birth"]
        assert "MUKONO" in data["district"].upper()


# ---------------------------------------------------------------------------
# Autonomous Agent Task Execution: National ID + Phone -> TIN Application
# ---------------------------------------------------------------------------
class TestAutonomousAgentTinExecution:
    def test_id_attachment_gets_portal_guidance_without_creating_a_tin(self):
        import secrets

        test_nin = f"CF94{1000000 + secrets.randbelow(9000000)}GUL"
        doc = DocumentRecord(
            doc_id="doc_nid_auto_1",
            filename="national_id_card.png",
            kind="image",
            size_bytes=2048,
            doc_type="national_id",
            confidence=0.97,
            matched_keywords=["national identity", "nira"],
            text=(
                "REPUBLIC OF UGANDA NATIONAL IDENTIFICATION CARD \n"
                "Name: Grace Akello \n"
                f"NIN: {test_nin} \n"
                "DOB: 20/06/1994 \n"
                "District: Gulu"
            ),
            truncated=False,
            fields={"nins": [test_nin]},
            tables=[],
            meta={},
            summary="Ugandan National ID Card for Grace Akello",
            warnings=[],
            created_at=0.0,
        )

        # Exercise the pure guidance helper without initializing the full
        # retrieval/model stack, which is unrelated to this privacy boundary.
        model = ChatModel.__new__(ChatModel)
        res = model._maybe_handle_autonomous_tin_registration(
            message="Here is my national ID card. My phone number is +256782112233. Please apply for my TIN.",
            attachments=[doc],
            thread_id="auto-tin-conv-1",
            locale="en",
        )

        assert res is not None
        assert res["retrieval_mode"] == "tin_registration_guidance"
        assert res["agent_role"] == "registration_specialist"
        assert "has not been sent to URA" in res["reply"]
        assert "https://ura.go.ug" in res["reply"]
        assert test_nin not in res["reply"]
        assert "Grace Akello" not in res["reply"]
        assert "+256782112233" not in res["reply"]
        from plugins.tin_registration import TinRegistrationClient

        assert not TinRegistrationClient().search_taxpayer(test_nin).get("found", False)

    def test_id_attachment_never_prompts_for_phone_or_repeats_the_nin(self):
        doc = DocumentRecord(
            doc_id="doc_nid_auto_2",
            filename="my_id.png",
            kind="image",
            size_bytes=2048,
            doc_type="national_id",
            confidence=0.97,
            matched_keywords=["national identity"],
            text="REPUBLIC OF UGANDA \n Name: Robert Okello \n NIN: CM930039182LIR \n DOB: 10/10/1993",
            truncated=False,
            fields={"nins": ["CM930039182LIR"]},
            tables=[],
            meta={},
            summary="National ID",
            warnings=[],
            created_at=0.0,
        )

        model = ChatModel.__new__(ChatModel)
        res = model._maybe_handle_autonomous_tin_registration(
            message="Please register my TIN using this attached National ID.",
            attachments=[doc],
            thread_id="auto-tin-conv-2",
            locale="en",
        )

        assert res is not None
        assert res["retrieval_mode"] == "tin_registration_guidance"
        assert "CM930039182LIR" not in res["reply"]
        assert "mobile telephone number" not in res["reply"].lower()
        assert "submit TIN applications" in res["reply"]


# ---------------------------------------------------------------------------
# Standalone Plugins Microservice Server Tests (FastAPI + MCP)
# ---------------------------------------------------------------------------
class TestStandalonePluginsServer:
    @pytest.fixture(autouse=True)
    def _isolate_orchestrator(self, monkeypatch):
        import plugins.server as server_module
        from plugins.orchestrator import reset_orchestrator

        monkeypatch.setattr(server_module, "_EXPECTED_API_KEY", "e2e-plugins-key")
        for env_name in (
            "EFRIS_DB_PATH",
            "DTS_DB_PATH",
            "URSB_DB_PATH",
            "BWIMS_DB_PATH",
            "TIN_DB_PATH",
            "PAYMENT_DB_PATH",
        ):
            monkeypatch.setenv(env_name, ":memory:")
        server_module._MCP_REPLAY_CACHE.clear()
        reset_orchestrator()
        yield
        reset_orchestrator()

    def test_plugins_server_health(self):
        client = TestClient(plugins_server_app)
        res = client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"
        assert data["service"] == "ura-plugins-server"
        assert data["health"]["all_healthy"] is True
        assert data["health"]["plugin_count"] == 6

    def test_mcp_manifest_discovery(self):
        client = TestClient(plugins_server_app)
        res = client.get("/mcp/manifest", headers={"Authorization": "Bearer e2e-plugins-key"})
        assert res.status_code == 200
        data = res.json()
        assert data["server_name"] == "ura-enterprise-connectors"
        assert data["manifest_schema_version"] == "1.0.0"
        assert data["transport"] == "internal-rest"
        assert data["mcp_compatible"] is False
        assert data["mode"] == "simulation"
        assert data["live"] is False
        assert len(data["tools"]) == 23
        assert len(data["connectors"]) == 6
        assert all("LOCAL SIMULATOR ONLY" in tool["description"] for tool in data["tools"])

    def test_mcp_tool_call_remote_dispatch(self):
        client = TestClient(plugins_server_app)
        # Call EFRIS verification tool over remote MCP call
        res = client.post(
            "/mcp/call",
            json={
                "name": "efris_fiscal_invoice",
                "arguments": {
                    "action": "verify",
                    "fdn": "01240000000000001001",
                    "verification_code": "A9F23B",
                },
                "confirmed": True,
                "idempotency_key": "e2e-verification-once",
            },
            headers={"Authorization": "Bearer e2e-plugins-key"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["mode"] == "simulation"
        assert data["live"] is False
        assert data["tool_name"] == "efris_fiscal_invoice"
        assert data["result"]["ok"] is True
        assert data["result"]["mode"] == "simulation"
        assert data["result"]["data"]["is_authentic"] is True

    def test_mutating_mcp_call_proposes_then_executes_once_after_confirmation(self):
        client = TestClient(plugins_server_app)
        headers = {"Authorization": "Bearer e2e-plugins-key"}
        proposal = client.post(
            "/mcp/call",
            json={
                "name": "payment_generate_prn",
                "arguments": {"taxpayer_name": "E2E Sample", "amount_ugx": 25000},
            },
            headers=headers,
        )
        assert proposal.status_code == 200
        proposal_body = proposal.json()
        assert proposal_body["submitted"] is False
        assert proposal_body["requires_confirmation"] is True
        assert proposal_body["mode"] == "simulation"
        assert proposal_body["live"] is False

        confirmed_payload = {
            "name": "payment_generate_prn",
            "arguments": proposal_body["proposal"],
            "confirmed": True,
            "idempotency_key": proposal_body["idempotency_key"],
        }
        first = client.post("/mcp/call", json=confirmed_payload, headers=headers).json()
        second = client.post("/mcp/call", json=confirmed_payload, headers=headers).json()
        assert first["ok"] is True
        assert first["mode"] == "simulation"
        assert first["result"]["mode"] == "simulation"
        assert second["replayed"] is True
        assert first["result"]["prn"] == second["result"]["prn"]

    def test_direct_payment_api_requires_confirmation_and_replays(self):
        client = TestClient(plugins_server_app)
        headers = {"Authorization": "Bearer e2e-plugins-key"}
        proposal = client.post(
            "/api/v1/payments/prn/generate",
            json={"taxpayer_name": "E2E REST Sample", "amount_ugx": 17000},
            headers=headers,
        ).json()
        assert proposal["submitted"] is False
        assert proposal["requires_confirmation"] is True
        assert proposal["live"] is False

        payload = {
            **proposal["proposal"],
            "confirmed": True,
            "idempotency_key": proposal["idempotency_key"],
        }
        first = client.post("/api/v1/payments/prn/generate", json=payload, headers=headers).json()
        second = client.post("/api/v1/payments/prn/generate", json=payload, headers=headers).json()
        assert first["submitted"] is True
        assert first["mode"] == "simulation"
        assert first["result"]["mode"] == "simulation"
        assert second["replayed"] is True
        assert first["result"]["prn"] == second["result"]["prn"]

    def test_plugin_apis_fail_closed_without_a_configured_key(self, monkeypatch):
        import plugins.server as server_module

        monkeypatch.setattr(server_module, "_EXPECTED_API_KEY", "")
        client = TestClient(plugins_server_app)
        assert client.get("/mcp/manifest").status_code == 503
        assert client.get("/api/v1/tin/taxpayers/search", params={"query": "sample"}).status_code == 503

    def test_production_simulator_writes_stay_disabled_even_with_flag(self, monkeypatch):
        client = TestClient(plugins_server_app)
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("FLAG_ENTERPRISE_CONNECTORS", "true")
        headers = {"Authorization": "Bearer e2e-plugins-key"}

        direct = client.post(
            "/api/v1/payments/prn/generate",
            json={"taxpayer_name": "E2E Production Sample", "amount_ugx": 10},
            headers=headers,
        )
        assert direct.status_code == 503

        mcp = client.post(
            "/mcp/call",
            json={"name": "payment_generate_prn", "arguments": {"taxpayer_name": "E2E", "amount_ugx": 10}},
            headers=headers,
        )
        assert mcp.status_code == 503

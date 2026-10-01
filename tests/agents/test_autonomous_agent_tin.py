"""Test suite for autonomous agent task execution (National ID + Phone -> TIN Application) and standalone plugins server."""

from __future__ import annotations

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
    def test_autonomous_tin_registration_success(self):
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

        model = ChatModel()
        res = model._maybe_handle_autonomous_tin_registration(
            message="Here is my national ID card. My phone number is +256782112233. Please apply for my TIN.",
            attachments=[doc],
            thread_id="auto-tin-conv-1",
            locale="en",
        )

        assert res is not None
        assert res["retrieval_mode"] == "autonomous_agent_tin"
        assert res["agent_role"] == "registration_specialist"
        assert "Autonomous TIN Registration Completed" in res["reply"]
        assert test_nin in res["reply"]
        assert "Grace Akello" in res["reply"]
        assert "Assigned 10-Digit TIN" in res["reply"]

    def test_autonomous_tin_prompts_for_missing_phone(self):
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

        model = ChatModel()
        res = model._maybe_handle_autonomous_tin_registration(
            message="Please register my TIN using this attached National ID.",
            attachments=[doc],
            thread_id="auto-tin-conv-2",
            locale="en",
        )

        assert res is not None
        assert "National ID Scanned & Verified" in res["reply"]
        assert "CM930039182LIR" in res["reply"]
        assert "active mobile telephone number" in res["reply"].lower()


# ---------------------------------------------------------------------------
# Standalone Plugins Microservice Server Tests (FastAPI + MCP)
# ---------------------------------------------------------------------------
class TestStandalonePluginsServer:
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
        res = client.get("/mcp/manifest")
        assert res.status_code == 200
        data = res.json()
        assert data["server_name"] == "ura-enterprise-connectors"
        assert len(data["tools"]) == 23
        assert len(data["connectors"]) == 6

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
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["tool_name"] == "efris_fiscal_invoice"
        assert data["result"]["ok"] is True
        assert data["result"]["data"]["is_authentic"] is True

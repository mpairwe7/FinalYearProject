"""Comprehensive test suite for URA Agentic System plugins, EFRIS, DTS connectors, and orchestrator."""

from __future__ import annotations

import pytest
from app.mcp import get_client, reset_client

from plugins import (
    PluginOrchestrator,
    PluginStatus,
    reset_orchestrator,
)
from plugins.digital_tax_stamps import (
    DigitalTaxStampsClient,
    DigitalTaxStampsService,
)
from plugins.efris import (
    EfrisClient,
    EfrisService,
)


@pytest.fixture(autouse=True)
def _clean_orchestrator():
    reset_orchestrator()
    yield
    reset_orchestrator()


# ---------------------------------------------------------------------------
# EFRIS Sample System Tests
# ---------------------------------------------------------------------------
class TestEfrisSampleSystem:
    def test_taxpayer_profile_lookup(self):
        service = EfrisService(db_path=":memory:")
        client = EfrisClient(service=service)

        # Existing seeded taxpayer (Kakira Sugar)
        resp = client.get_taxpayer_profile("1000000001")
        assert resp["ok"] is True
        assert resp["registered"] is True
        assert resp["profile"]["business_name"] == "Kakira Sugar Limited"
        assert resp["profile"]["is_vat_registered"] is True
        assert resp["profile"]["integration_mode"] == "SYSTEM_TO_SYSTEM"

        # Nonexistent taxpayer
        resp_unknown = client.get_taxpayer_profile("9999999999")
        assert resp_unknown["ok"] is False
        assert resp_unknown["registered"] is False

    def test_issue_fiscal_invoice_and_fdn_generation(self):
        service = EfrisService(db_path=":memory:")
        client = EfrisClient(service=service)

        items = [
            {
                "commodity_code": "50202301",
                "description": "Mineral Water 500ml",
                "quantity": 100,
                "unit_price": 1000,
                "tax_category": "STANDARD_18",
            }
        ]

        # B2B Invoice from Kampala Supermarket to Kakira Sugar
        resp = client.issue_invoice(
            seller_tin="1000000005",
            buyer_tin="1000000001",
            items=items,
            invoice_type="B2B",
        )

        assert resp["ok"] is True
        assert len(resp["fdn"]) == 20
        assert resp["fdn"].startswith("01")
        assert len(resp["verification_code"]) == 6
        assert "efris.ura.go.ug/verify" in resp["qr_code_url"]
        assert resp["net_amount"] == 100000.0
        assert resp["tax_amount"] == 18000.0
        assert resp["gross_amount"] == 118000.0
        assert resp["status"] == "ISSUED"

    def test_verify_invoice_authenticity(self):
        service = EfrisService(db_path=":memory:")
        client = EfrisClient(service=service)

        # 1. Verify pre-seeded valid invoice
        res = client.verify_invoice("01240000000000001001", verification_code="A9F23B")
        assert res["ok"] is True
        assert res["is_authentic"] is True
        assert res["seller_tin"] == "1000000005"
        assert res["gross_amount"] == 118000.0

        # 2. Check tampering / bad verification code
        tampered = client.verify_invoice("01240000000000001001", verification_code="WRONG9")
        assert tampered["ok"] is False
        assert tampered["is_authentic"] is False
        assert "tampered" in tampered["error"].lower()

        # 3. Check nonexistent FDN
        missing = client.verify_invoice("99990000000000009999")
        assert missing["ok"] is False
        assert missing["is_authentic"] is False

    def test_credit_note_workflow(self):
        service = EfrisService(db_path=":memory:")
        client = EfrisClient(service=service)

        fdn = "01240000000000001001"
        seller_tin = "1000000005"

        # Apply valid credit note for goods returned (59,000 UGX)
        resp = client.create_credit_note(
            original_fdn=fdn,
            seller_tin=seller_tin,
            reason="GOODS_RETURNED",
            adjusted_amount=59000.0,
            description="50 damaged bottles returned",
        )
        assert resp["ok"] is True
        assert resp["status"] == "APPROVED"
        assert resp["adjusted_gross"] == 59000.0
        assert resp["adjusted_vat"] > 0
        assert resp["credit_note_number"].startswith("CN-")

        # Exceeding original invoice amount should be rejected
        excess = client.create_credit_note(
            original_fdn=fdn,
            seller_tin=seller_tin,
            reason="GOODS_RETURNED",
            adjusted_amount=5000000.0,
        )
        assert excess["ok"] is False
        assert "must be between" in excess["error"]

    def test_stock_inventory_deduction_and_stock_in(self):
        service = EfrisService(db_path=":memory:")
        client = EfrisClient(service=service)
        tin = "1000000005"

        # Check initial water stock (seeded at 5000)
        stock_init = client.get_stock(tin=tin, commodity_code="50202301")
        assert stock_init["ok"] is True
        init_qty = stock_init["stock_items"][0]["quantity_on_hand"]
        assert init_qty == 5000.0

        # Issue invoice for 200 bottles -> stock should deduct
        client.issue_invoice(
            seller_tin=tin,
            items=[{"commodity_code": "50202301", "description": "Water", "quantity": 200, "unit_price": 1000}],
        )
        stock_after = client.get_stock(tin=tin, commodity_code="50202301")
        assert stock_after["stock_items"][0]["quantity_on_hand"] == 4800.0

        # Record stock-in of 500 bottles from local purchase
        client.record_stock_in(
            tin=tin,
            commodity_code="50202301",
            description="Water",
            quantity=500,
            unit_cost=800,
        )
        stock_restocked = client.get_stock(tin=tin, commodity_code="50202301")
        assert stock_restocked["stock_items"][0]["quantity_on_hand"] == 5300.0


# ---------------------------------------------------------------------------
# Digital Tax Stamps (DTS) Sample System Tests
# ---------------------------------------------------------------------------
class TestDigitalTaxStampsSystem:
    def test_verify_genuine_stamp(self):
        service = DigitalTaxStampsService(db_path=":memory:")
        client = DigitalTaxStampsClient(service=service)

        # Seeded genuine stamp (Nile Special Lager)
        res = client.verify_stamp("DTS-UG-BEV-990182746")
        assert res["ok"] is True
        assert res["is_authentic"] is True
        assert res["status"] == "GENUINE"
        assert res["product_category"] == "BEER"
        assert res["manufacturer_name"] == "Nile Breweries Limited"

    def test_verify_expired_stamp(self):
        service = DigitalTaxStampsService(db_path=":memory:")
        client = DigitalTaxStampsClient(service=service)

        res = client.verify_stamp("DTS-UG-BEV-EXPIRED-001")
        assert res["ok"] is True
        assert res["status"] == "EXPIRED"
        assert "EXPIRED" in res["message"]

    def test_verify_counterfeit_stamp(self):
        service = DigitalTaxStampsService(db_path=":memory:")
        client = DigitalTaxStampsClient(service=service)

        res = client.verify_stamp("DTS-FAKE-999999999")
        assert res["ok"] is False
        assert res["is_authentic"] is False
        assert res["status"] == "COUNTERFEIT"
        assert "0800 117 000" in res["error"]

    def test_order_stamps_and_prn_generation(self):
        service = DigitalTaxStampsService(db_path=":memory:")
        client = DigitalTaxStampsClient(service=service)

        # Order 10,000 beer stamps (UGX 35 tariff per unit)
        res = client.order_stamps(
            taxpayer_tin="1000000002",
            product_category="BEER",
            quantity=10000,
        )
        assert res["ok"] is True
        assert res["unit_fee_ugx"] == 35.0
        assert res["total_amount_ugx"] == 350000.0
        assert len(res["prn"]) == 10
        assert res["prn"].startswith("2")
        assert "Henley Business Park, Ntinda" in res["collection_point"]

    def test_activate_stamps_on_production_line(self):
        service = DigitalTaxStampsService(db_path=":memory:")
        client = DigitalTaxStampsClient(service=service)

        # Place order
        order = client.order_stamps(
            taxpayer_tin="1000000002",
            product_category="BEER",
            quantity=2,
        )
        order_id = order["order_id"]

        serials = ["DTS-NBL-LINE1-001", "DTS-NBL-LINE1-002"]
        act_res = client.activate_stamps(
            order_id=order_id,
            line_id="LINE-01-BOTTLING",
            stamp_serials=serials,
        )
        assert act_res["ok"] is True
        assert act_res["activated_count"] == 2
        assert act_res["status"] == "ACTIVATED"

        # Now verify newly activated stamp
        verify_new = client.verify_stamp("DTS-NBL-LINE1-001")
        assert verify_new["ok"] is True
        assert verify_new["is_authentic"] is True
        assert verify_new["status"] == "GENUINE"

    def test_damaged_stamps_declaration(self):
        service = DigitalTaxStampsService(db_path=":memory:")
        client = DigitalTaxStampsClient(service=service)

        decl_res = client.declare_damaged_stamps(
            taxpayer_tin="1000000002",
            damaged_serials=["DTS-SPOIL-01", "DTS-SPOIL-02", "DTS-SPOIL-03"],
            incident_reason="PACKAGING_LINE_JAM",
        )
        assert decl_res["ok"] is True
        assert decl_res["reconciled_count"] == 3
        assert decl_res["credit_allowable_ugx"] == 90.0  # 3 * 30 UGX
        assert decl_res["status"] == "ACKNOWLEDGED"


# ---------------------------------------------------------------------------
# Plugin Orchestrator Tests
# ---------------------------------------------------------------------------
class TestPluginOrchestrator:
    def test_orchestrator_initializes_default_plugins(self):
        orchestrator = PluginOrchestrator()
        orchestrator.initialize_default_plugins()

        plugins = orchestrator.list_plugins()
        plugin_names = [p["name"] for p in plugins]
        assert "efris" in plugin_names
        assert "digital_tax_stamps" in plugin_names

        # Connectors check
        efris_conn = orchestrator.get_connector("efris")
        assert efris_conn is not None
        assert efris_conn.is_healthy() is True

        dts_conn = orchestrator.get_connector("digital_tax_stamps")
        assert dts_conn is not None
        assert dts_conn.is_healthy() is True

    def test_orchestrator_health_check(self):
        orchestrator = PluginOrchestrator()
        orchestrator.initialize_default_plugins()

        health = orchestrator.health_check()
        assert health["all_healthy"] is True
        assert health["plugin_count"] == 6
        assert "efris" in health["systems"]
        assert "digital_tax_stamps" in health["systems"]
        assert "ursb" in health["systems"]
        assert "bwims" in health["systems"]
        assert "tin_registration" in health["systems"]
        assert "payment_system" in health["systems"]

    def test_orchestrator_wires_and_unwires_tools(self, fresh_registry):
        orchestrator = PluginOrchestrator()
        orchestrator.initialize_default_plugins()

        wired = orchestrator.wire_tools(registry=fresh_registry)
        assert "efris_fiscal_invoice" in wired
        assert "dts_verify_stamp" in wired
        assert "ursb_verify_business" in wired
        assert "bwims_consignment_status" in wired
        assert "tin_search_verify" in wired
        assert "payment_generate_prn" in wired
        assert len(wired) == 23

        # Assert present in ToolRegistry
        for name in wired:
            assert fresh_registry.get(name) is not None

        # Unwire tools
        unwired = orchestrator.unwire_tools(registry=fresh_registry)
        assert set(unwired) == set(wired)
        for name in unwired:
            assert fresh_registry.get(name) is None


# ---------------------------------------------------------------------------
# Connector Agentic Tool Execution & MCP Tests
# ---------------------------------------------------------------------------
class TestConnectorAgenticTools:
    def test_efris_fiscal_invoice_tool_via_registry(self, fresh_registry):
        # Verification action
        res = fresh_registry.call(
            "efris_fiscal_invoice",
            {"action": "verify", "fdn": "01240000000000001001", "verification_code": "A9F23B"},
        )
        assert res["ok"] is True
        assert res["action"] == "verify"
        assert res["data"]["is_authentic"] is True

        # Issuance action
        issue_res = fresh_registry.call(
            "efris_fiscal_invoice",
            {
                "action": "issue",
                "seller_tin": "1000000005",
                "items": [{"description": "Water", "quantity": 10, "unit_price": 1000}],
            },
        )
        assert issue_res["ok"] is True
        assert issue_res["action"] == "issue"
        assert len(issue_res["data"]["fdn"]) == 20

    def test_dts_verify_stamp_tool_via_registry(self, fresh_registry):
        res = fresh_registry.call("dts_verify_stamp", {"stamp_code": "DTS-UG-BEV-990182746"})
        assert res["ok"] is True
        assert res["is_authentic"] is True
        assert res["status"] == "GENUINE"
        assert res["product_category"] == "BEER"

    def test_mcp_client_roundtrip_efris_and_dts(self, fresh_registry):
        reset_client()
        client = get_client()

        # EFRIS taxpayer status tool call via MCP
        efris_mcp = client.call_tool(
            "efris_taxpayer_status",
            {"tin": "1000000001"},
            tenant_id="default",
            user_id="officer_01",
        )
        assert efris_mcp.ok is True
        assert efris_mcp.result["registered"] is True
        assert efris_mcp.result["profile"]["business_name"] == "Kakira Sugar Limited"

        # DTS verify stamp tool call via MCP
        dts_mcp = client.call_tool(
            "dts_verify_stamp",
            {"stamp_code": "DTS-UG-WTR-554433221"},
            tenant_id="default",
            user_id="taxpayer_user",
        )
        assert dts_mcp.ok is True
        assert dts_mcp.result["is_authentic"] is True
        assert dts_mcp.result["brand_name"] == "Rwenzori Pure Natural Mineral Water 500ml"

    def test_efris_edge_cases_and_offline_sync(self):
        service = EfrisService()
        client = EfrisClient(service=service)

        # Empty items error
        err1 = client.issue_invoice(seller_tin="1000000005", items=[])
        assert err1["ok"] is False

        # Non-registered seller TIN
        err2 = client.issue_invoice(
            seller_tin="9999999999",
            items=[{"commodity_code": "001", "description": "Item", "quantity": 1, "unit_price": 500}],
        )
        assert err2["ok"] is False

        # Offline batch sync
        from plugins.efris.models import FiscalInvoiceRequest, InvoiceItem
        batch = [
            FiscalInvoiceRequest(
                seller_tin="1000000005",
                items=[InvoiceItem(commodity_code="50202301", description="Water", quantity=2, unit_price=1000)],
                offline_reference="OFFLINE-001",
            ),
            FiscalInvoiceRequest(
                seller_tin="1000000005",
                items=[InvoiceItem(commodity_code="50202301", description="Water", quantity=5, unit_price=1000)],
                offline_reference="OFFLINE-002",
            ),
        ]
        sync_res = service.sync_offline_batch(batch)
        assert sync_res["ok"] is True
        assert sync_res["synced_count"] == 2
        assert len(sync_res["synced_fdns"]) == 2

    def test_dts_edge_cases_and_tariffs(self):
        client = DigitalTaxStampsClient()

        # Invalid category
        inv_cat = client.order_stamps(
            taxpayer_tin="1000000002",
            product_category="SPACESHIP",
            quantity=10,
        )
        assert inv_cat["ok"] is False
        assert "Invalid product category" in inv_cat["error"]

        # Negative quantity
        neg_qty = client.order_stamps(
            taxpayer_tin="1000000002",
            product_category="BEER",
            quantity=-5,
        )
        assert neg_qty["ok"] is False

        # List tariffs
        tariffs = client.list_tariffs()
        assert tariffs["ok"] is True
        assert tariffs["tariffs_ugx"]["CEMENT"] == 135.0
        assert tariffs["tariffs_ugx"]["BEER"] == 35.0
        assert "Ntinda" in tariffs["collection_point"]

    def test_plugin_enable_disable_lifecycle(self):
        orchestrator = PluginOrchestrator()
        orchestrator.initialize_default_plugins()

        efris_p = orchestrator.get_plugin("efris")
        assert efris_p.status == PluginStatus.ACTIVE

        orchestrator.disable_plugin("efris")
        assert efris_p.status == PluginStatus.DISABLED

        orchestrator.enable_plugin("efris")
        assert efris_p.status == PluginStatus.ACTIVE

    def test_mcp_policy_denies_unauthorized_elevated_tool(self, fresh_registry):
        reset_client()
        client = get_client()

        # Public unauthenticated user attempting elevated high-risk credit note
        res = client.call_tool(
            "efris_credit_note",
            {
                "original_fdn": "01240000000000001001",
                "seller_tin": "1000000005",
                "reason": "GOODS_RETURNED",
                "adjusted_amount": 1000,
            },
            tenant_id="default",
            user_id="",  # unauthenticated
            user_role="public",
        )
        assert res.ok is False
        assert res.result.get("error") == "policy_denied"
        reasons = res.result.get("policy", {}).get("reasons", [])
        assert any("authenticated user required" in r for r in reasons)

    def test_tool_rag_selection_matches_efris_and_dts(self, fresh_registry):
        from app.mcp.tool_rag import ToolRAGSelector

        selector = ToolRAGSelector()
        all_tool_names = fresh_registry.names()

        # Query about EFRIS invoice
        efris_query = "efris fiscal invoice"
        selected_efris = selector.select(efris_query, all_tool_names, k=5)
        assert "efris_fiscal_invoice" in selected_efris

        # Query about digital tax stamp verification
        dts_query = "digital tax stamp verify"
        selected_dts = selector.select(dts_query, all_tool_names, k=5)
        assert "dts_verify_stamp" in selected_dts


# ---------------------------------------------------------------------------
# URSB System Tests
# ---------------------------------------------------------------------------
class TestUrsbSystem:
    def test_search_business_found_and_not_found(self):
        from plugins.ursb import UrsbClient, UrsbService

        service = UrsbService(db_path=":memory:")
        client = UrsbClient(service=service)

        # Search existing seeded entity
        res = client.search_business("Kakira Sugar Limited")
        assert res["ok"] is True
        assert res["found"] is True
        assert res["entity"]["registration_number"] == "URSB-CO-10001"
        assert len(res["entity"]["directors"]) >= 2

        # Search nonexistent entity
        missing = client.search_business("Nonexistent Fictional Enterprise")
        assert missing["ok"] is True
        assert missing["found"] is False

    def test_register_business_and_compliance(self):
        from plugins.ursb import UrsbClient, UrsbService

        service = UrsbService(db_path=":memory:")
        client = UrsbClient(service=service)

        # Register new entity
        reg = client.register_business(
            business_name="AfriTech Innovations Ltd",
            entity_type="LIMITED_COMPANY",
            nature_of_business="Software Engineering & Cloud Services",
            registered_office="Plot 10, Acacia Avenue",
            district="Kampala",
            applicant_nin="CM950019284KLA",
        )
        assert reg["ok"] is True
        assert reg["registration_number"].startswith("URSB-CO-")
        assert reg["status"] == "ACTIVE"

        # Check compliance for URA TIN readiness
        comp = client.check_compliance(reg["registration_number"])
        assert comp["ok"] is True
        assert comp["is_legally_active"] is True
        assert comp["ready_for_ura_tin"] is True


# ---------------------------------------------------------------------------
# BWIMS System Tests
# ---------------------------------------------------------------------------
class TestBwimsSystem:
    def test_consignment_status_and_overstay_alert(self):
        from plugins.bwims import BwimsClient, BwimsService

        service = BwimsService(db_path=":memory:")
        client = BwimsClient(service=service)

        # 1. Normal active consignment
        res = client.get_consignment("2026-ASY-IM7-88912")
        assert res["ok"] is True
        assert res["found"] is True
        assert res["consignment"]["goods_description"].startswith("100 Motor Vehicles")
        assert res["consignment"]["warehouse_code"] == "WH-KLA-001"

        # 2. Overstayed consignment (> 270 days)
        overstay = client.get_consignment("2025-ASY-IM7-00912")
        assert overstay["ok"] is True
        assert overstay["is_overstayed"] is True
        assert "EACCMA" in overstay["message"]

    def test_warehouse_inventory_and_ex_warehouse_clearance(self):
        from plugins.bwims import BwimsClient, BwimsService

        service = BwimsService(db_path=":memory:")
        client = BwimsClient(service=service)

        # Inspect warehouse stock
        inv = client.get_inventory("WH-KLA-001")
        assert inv["ok"] is True
        assert inv["total_consignments"] >= 2
        assert inv["total_cif_ugx"] > 0

        # Partial ex-warehouse clearance (clear 20 of 100 vehicles)
        clr = client.clear_ex_warehouse(
            entry_number="2026-ASY-IM7-88912",
            importer_tin="1000000007",
            cleared_quantity=20,
            declaration_type="IM4_HOME_CONSUMPTION",
            duty_paid_prn="PRN-CUSTOMS-2026-001",
        )
        assert clr["ok"] is True
        assert clr["cleared_quantity"] == 20.0
        assert clr["remaining_quantity"] == 80.0


# ---------------------------------------------------------------------------
# TIN Registration System Tests
# ---------------------------------------------------------------------------
class TestTinRegistrationSystem:
    def test_search_taxpayer_by_tin_and_nin(self):
        from plugins.tin_registration import TinRegistrationClient, TinRegistrationService

        service = TinRegistrationService(db_path=":memory:")
        client = TinRegistrationClient(service=service)

        # By 10-digit TIN
        res_tin = client.search_taxpayer("1000000001")
        assert res_tin["ok"] is True
        assert res_tin["found"] is True
        assert res_tin["taxpayer"]["legal_name"] == "Kakira Sugar Limited"
        assert len(res_tin["taxpayer"]["obligations"]) >= 3

        # By individual NIN
        res_nin = client.search_taxpayer("CM910029384GUL")
        assert res_nin["ok"] is True
        assert res_nin["found"] is True
        assert res_nin["taxpayer"]["legal_name"] == "David Ochieng"

    def test_apply_instant_individual_tin(self):
        from plugins.tin_registration import TinRegistrationClient, TinRegistrationService

        service = TinRegistrationService(db_path=":memory:")
        client = TinRegistrationClient(service=service)

        # Valid 14-char NIN
        resp = client.apply_instant_individual_tin(
            nin="CM980019284HJA",
            full_name="Sarah Namubiru",
            mobile="+256772998877",
            email="snamubiru@gmail.com",
            district="Wakiso",
        )
        assert resp["ok"] is True
        assert len(resp["tin"]) == 10
        assert resp["tin"].startswith("1")
        assert resp["status"] == "ACTIVE"
        assert "INCOME_TAX_INDIVIDUAL" in resp["default_obligations"]

        # Duplicate NIN rejection
        dup = client.apply_instant_individual_tin(
            nin="CM980019284HJA",
            full_name="Sarah Namubiru",
        )
        assert dup["ok"] is False
        assert "already registered" in dup["message"]

    def test_apply_non_individual_tin_and_add_obligation(self):
        from plugins.tin_registration import TinRegistrationClient, TinRegistrationService

        service = TinRegistrationService(db_path=":memory:")
        client = TinRegistrationClient(service=service)

        # Apply company TIN linked to URSB
        resp = client.apply_non_individual_tin(
            ursb_registration_number="URSB-CO-88129",
            business_name="Great Lakes Logistics Co Ltd",
            requested_tax_heads=["CORPORATION_TAX", "PAYE"],
        )
        assert resp["ok"] is True
        assert len(resp["tin"]) == 10
        new_tin = resp["tin"]

        # Add VAT obligation
        vat_res = client.add_tax_obligation(tin=new_tin, tax_head="VAT_STANDARD")
        assert vat_res["ok"] is True
        assert "VAT_STANDARD" in vat_res["active_obligations"]


# ---------------------------------------------------------------------------
# Multi-System Orchestration (5 systems)
# ---------------------------------------------------------------------------
class TestMultiSystemOrchestration:
    def test_all_five_connectors_active_and_healthy(self):
        orchestrator = PluginOrchestrator()
        orchestrator.initialize_default_plugins()

        connectors = orchestrator.get_connectors_summary()
        conn_ids = [c["id"] for c in connectors]
        assert "efris" in conn_ids
        assert "digital_tax_stamps" in conn_ids
        assert "ursb" in conn_ids
        assert "bwims" in conn_ids
        assert "tin_registration" in conn_ids
        assert "payment_system" in conn_ids
        assert len(connectors) == 6

        # Verify all databases report healthy online status
        for c in connectors:
            assert c["healthy"] is True
            assert c["database"]["status"] == "ONLINE"

    def test_tool_registry_wires_all_systems(self, fresh_registry):
        orchestrator = PluginOrchestrator()
        orchestrator.initialize_default_plugins()
        orchestrator.wire_tools(fresh_registry)

        # Verify sample tools from each of the 6 systems
        assert fresh_registry.get("efris_fiscal_invoice") is not None
        assert fresh_registry.get("dts_verify_stamp") is not None
        assert fresh_registry.get("ursb_verify_business") is not None
        assert fresh_registry.get("bwims_consignment_status") is not None
        assert fresh_registry.get("tin_search_verify") is not None
        assert fresh_registry.get("tin_apply_individual") is not None
        assert fresh_registry.get("payment_generate_prn") is not None
        assert fresh_registry.get("payment_view_status") is not None

        # Execute URSB tool via ToolRegistry
        u_res = fresh_registry.call("ursb_verify_business", {"query": "Nile Breweries Limited"})
        assert u_res["ok"] is True
        assert u_res["found"] is True

        # Execute BWIMS tool via ToolRegistry
        b_res = fresh_registry.call("bwims_consignment_status", {"entry_number": "2026-ASY-IM7-88912"})
        assert b_res["ok"] is True
        assert b_res["found"] is True

        # Execute TIN tool via ToolRegistry
        t_res = fresh_registry.call("tin_search_verify", {"query": "1000000002"})
        assert t_res["ok"] is True
        assert t_res["found"] is True

        # Execute Payment tool via ToolRegistry
        p_res = fresh_registry.call("payment_view_status", {"prn": "226030001001"})
        assert p_res["ok"] is True
        assert p_res["is_cleared"] is True


# ---------------------------------------------------------------------------
# URA Payment System Tests (Make a Payment Suite)
# ---------------------------------------------------------------------------
class TestPaymentSystem:
    def test_generate_payment_slip_prn(self):
        from plugins.payment_system import PaymentClient, PaymentService

        service = PaymentService(db_path=":memory:")
        client = PaymentClient(service=service)

        resp = client.generate_prn(
            taxpayer_name="Mukwano Enterprises Limited",
            amount_ugx=500000.0,
            taxpayer_tin="1000000004",
            tax_head="VAT_STANDARD",
            payment_channel="COMMERCIAL_BANK",
        )
        assert resp["ok"] is True
        assert len(resp["prn"]) == 12
        assert resp["prn"].startswith("2")
        assert resp["amount_ugx"] == 500000.0
        assert resp["status"] == "PENDING"
        assert "URA-PRN-" in resp["bank_barcode"]

    def test_view_payment_status_cleared_and_pending(self):
        from plugins.payment_system import PaymentClient, PaymentService

        service = PaymentService(db_path=":memory:")
        client = PaymentClient(service=service)

        # Seeded cleared PRN
        cleared = client.view_status("226030001001")
        assert cleared["ok"] is True
        assert cleared["is_cleared"] is True
        assert cleared["status"] == "CLEARED"
        assert cleared["bank_reference"] == "BK-STANBIC-99018"

        # Seeded pending PRN
        pending = client.view_status("226030003003")
        assert pending["ok"] is True
        assert pending["is_cleared"] is False
        assert pending["status"] == "PENDING"

    def test_reactivate_expired_prn(self):
        from plugins.payment_system import PaymentClient, PaymentService

        service = PaymentService(db_path=":memory:")
        client = PaymentClient(service=service)

        # Seeded expired PRN (225090004004)
        react = client.reactivate_prn("225090004004")
        assert react["ok"] is True
        assert react["status"] == "ACTIVE"
        assert react["new_expiry_date"] > react["previous_expiry"]

        # View status confirms it is no longer expired
        st = client.view_status("225090004004")
        assert st["ok"] is True
        assert st["status"] == "PENDING"

    def test_electronic_checkout_settlement(self):
        from plugins.payment_system import PaymentClient, PaymentService

        service = PaymentService(db_path=":memory:")
        client = PaymentClient(service=service)

        # Settle pending PRN 226030003003 via VISA
        checkout = client.process_checkout(
            prn="226030003003",
            payment_method="VISA",
            payer_identifier="4129",
            amount_paid_ugx=1500000.0,
        )
        assert checkout["ok"] is True
        assert checkout["status"] == "CLEARED"
        assert checkout["transaction_id"].startswith("TX-URA-")
        assert "REC-226030003003" in checkout["receipt_number"]

        # Re-check via status
        st = client.view_status("226030003003")
        assert st["is_cleared"] is True

    def test_verify_advance_tax_commercial_vehicle(self):
        from plugins.payment_system import PaymentClient, PaymentService

        service = PaymentService(db_path=":memory:")
        client = PaymentClient(service=service)

        # Seeded commercial bus UBK 412A
        res = client.verify_advance_tax("UBK 412A")
        assert res["ok"] is True
        assert res["is_compliant"] is True
        assert res["advance_tax_assessed_ugx"] == 280000.0
        assert res["capacity"] == 14


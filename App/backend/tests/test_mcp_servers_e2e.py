from __future__ import annotations

import unittest
from app.mcp.protocol import request_meta
from app.mcp.servers.efris.server import handle_request as handle_efris
from app.mcp.servers.tax_calculator.server import handle_request as handle_calc
from app.mcp.servers.ura_account.server import handle_request as handle_account


class TestStandaloneMCPServersE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.meta = request_meta(tenant_id="default", user_id="test-auditor", user_role="ura_admin")

    def test_tax_calculator_server_info_and_tools(self) -> None:
        info = handle_calc({"jsonrpc": "2.0", "id": "1", "method": "server/info", "params": {"_meta": self.meta}})
        self.assertEqual(info["result"]["name"], "mcp_tax_calculator")
        self.assertEqual(info["result"]["protocolVersion"], "2026-07-28")

        tools_res = handle_calc({"jsonrpc": "2.0", "id": "2", "method": "tools/list", "params": {"_meta": self.meta}})
        tools = tools_res["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        self.assertIn("calculate_vat", tool_names)
        self.assertIn("calculate_paye", tool_names)

    def test_ura_account_server_methods(self) -> None:
        info = handle_account({"jsonrpc": "2.0", "id": "1", "method": "server/info", "params": {"_meta": self.meta}})
        self.assertEqual(info["result"]["name"], "mcp_ura_account")

        res_list = handle_account({"jsonrpc": "2.0", "id": "2", "method": "resources/list", "params": {"_meta": self.meta}})
        uris = [r["uri"] for r in res_list["result"]["resources"]]
        self.assertIn("ura://account/profile-spec", uris)

        res_read = handle_account({
            "jsonrpc": "2.0",
            "id": "3",
            "method": "resources/read",
            "params": {"uri": "ura://account/profile-spec", "_meta": self.meta},
        })
        self.assertEqual(res_read["result"]["contents"][0]["uri"], "ura://account/profile-spec")

    def test_efris_server_tools_and_resources(self) -> None:
        info = handle_efris({"jsonrpc": "2.0", "id": "1", "method": "server/info", "params": {"_meta": self.meta}})
        self.assertEqual(info["result"]["name"], "mcp_efris")

        tools_res = handle_efris({"jsonrpc": "2.0", "id": "2", "method": "tools/list", "params": {"_meta": self.meta}})
        tools = tools_res["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        self.assertIn("efris_fiscal_invoice", tool_names)
        self.assertIn("efris_taxpayer_status", tool_names)

        res_list = handle_efris({"jsonrpc": "2.0", "id": "3", "method": "resources/list", "params": {"_meta": self.meta}})
        uris = [r["uri"] for r in res_list["result"]["resources"]]
        self.assertIn("efris://taxpayers/profile", uris)
        self.assertIn("efris://inventory/stock", uris)


if __name__ == "__main__":
    unittest.main()

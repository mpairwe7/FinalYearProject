"""Protocol tests for the ``mcp_tax_calculator`` MCP server."""

from __future__ import annotations

import unittest

from app.mcp.protocol import request_meta
from app.mcp.servers.tax_calculator import server


def _rpc(method: str, params: dict | None = None, request_id: int | None = 1, headers=None):
    body: dict = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        body["id"] = request_id
    merged = dict(params or {})
    if method in ("tools/list", "tools/call", "server/info", "resources/list", "resources/read", "prompts/list", "prompts/get"):
        meta = dict(merged.get("_meta") or {})
        for key, value in request_meta().items():
            meta.setdefault(key, value)
        merged["_meta"] = meta
    if merged:
        body["params"] = merged
    return server.handle_request(body, headers)


class ServerInfoTests(unittest.TestCase):
    def test_reports_the_2026_protocol_version(self) -> None:
        info = _rpc("server/info")["result"]
        self.assertEqual(info["protocolVersion"], "2026-07-28")
        self.assertEqual(info["name"], "mcp_tax_calculator")

    def test_initialize_is_refused_with_an_explanation(self) -> None:
        error = _rpc("initialize")["error"]
        self.assertEqual(error["code"], server.METHOD_NOT_FOUND)
        self.assertIn("stateless", error["message"])

    def test_unknown_method_is_a_protocol_error(self) -> None:
        self.assertEqual(_rpc("tools/teleport")["error"]["code"], server.METHOD_NOT_FOUND)

    def test_ping_returns_empty_object(self) -> None:
        response = _rpc("ping")
        self.assertNotIn("error", response)
        self.assertEqual(response["result"], {})


class ResourcesTests(unittest.TestCase):
    def test_resources_list_and_read(self) -> None:
        listed = _rpc("resources/list")["result"]
        self.assertIn("resources", listed)
        uris = [r["uri"] for r in listed["resources"]]
        self.assertIn("ura://rates/current", uris)
        self.assertIn("ura://calendar/deadlines", uris)

        rates = _rpc("resources/read", {"uri": "ura://rates/current"})["result"]
        self.assertEqual(rates["contents"][0]["uri"], "ura://rates/current")
        self.assertIn("FY2026-27", rates["contents"][0]["text"])

        deadlines = _rpc("resources/read", {"uri": "ura://calendar/deadlines"})["result"]
        self.assertEqual(deadlines["contents"][0]["uri"], "ura://calendar/deadlines")

    def test_unknown_resource_raises_not_found(self) -> None:
        err = _rpc("resources/read", {"uri": "ura://secret/keys"})["error"]
        self.assertEqual(err["code"], server.METHOD_NOT_FOUND)


class PromptsTests(unittest.TestCase):
    def test_prompts_list_and_get(self) -> None:
        prompts = _rpc("prompts/list")["result"]["prompts"]
        names = [p["name"] for p in prompts]
        self.assertIn("vat_calculation_guide", names)
        self.assertIn("paye_withholding_guide", names)

        rendered = _rpc("prompts/get", {"name": "vat_calculation_guide", "arguments": {"amount": "5000000"}})["result"]
        self.assertIn("5000000", rendered["messages"][0]["content"]["text"])


class BatchRequestTests(unittest.TestCase):
    def test_batch_request_returns_list_of_responses(self) -> None:
        batch = [
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "server/info", "params": {"_meta": request_meta()}},
        ]
        res = server.handle_request(batch)
        self.assertIsInstance(res, list)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["id"], 1)
        self.assertEqual(res[1]["id"], 2)


class ToolsListTests(unittest.TestCase):
    def test_serves_only_its_own_namespace(self) -> None:
        names = {t["name"] for t in _rpc("tools/list")["result"]["tools"]}
        self.assertIn("calculate_paye", names)
        self.assertIn("check_vat_registration", names)
        self.assertNotIn("ura_account_profile", names)
        self.assertNotIn("lookup_rate", names)

    def test_list_is_cacheable(self) -> None:
        result = _rpc("tools/list")["result"]
        self.assertEqual(result["ttlMs"], server.LIST_TTL_MS)
        self.assertEqual(result["cacheScope"], "server")

    def test_descriptors_carry_schemas_and_annotations(self) -> None:
        tool = next(
            t for t in _rpc("tools/list")["result"]["tools"] if t["name"] == "calculate_paye"
        )
        self.assertEqual(tool["inputSchema"]["type"], "object")
        self.assertIn("monthly_gross", tool["inputSchema"]["properties"])
        self.assertIn("outputSchema", tool)
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        self.assertEqual(tool["_meta"]["ug.go.ura.chatbot/risk"], "low")


class ToolsCallTests(unittest.TestCase):
    def test_successful_call_returns_content_and_structured_content(self) -> None:
        result = _rpc(
            "tools/call",
            {"name": "calculate_vat", "arguments": {"amount": 1_000_000, "fiscal_year": "FY2025-26"}},
        )["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["vat"], 180_000.0)
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertIn("180,000", result["content"][0]["text"])

    def test_tool_level_failure_is_a_result_not_a_protocol_error(self) -> None:
        # The model must be able to read the reason and retry; a
        # JSON-RPC error would give it nothing to act on.
        response = _rpc(
            "tools/call", {"name": "calculate_vat", "arguments": {"amount": -5}}
        )
        self.assertNotIn("error", response)
        self.assertTrue(response["result"]["isError"])
        self.assertIn("non-negative", response["result"]["structuredContent"]["error"])

    def test_a_tool_from_another_namespace_is_not_served(self) -> None:
        error = _rpc("tools/call", {"name": "ura_account_profile", "arguments": {}})["error"]
        self.assertEqual(error["code"], server.METHOD_NOT_FOUND)

    def test_non_object_arguments_are_invalid_params(self) -> None:
        error = _rpc("tools/call", {"name": "calculate_vat", "arguments": [1, 2]})["error"]
        self.assertEqual(error["code"], server.INVALID_PARAMS)

    def test_identity_is_read_from_request_meta(self) -> None:
        result = _rpc(
            "tools/call",
            {
                "name": "calculate_vat",
                "arguments": {"amount": 1000, "fiscal_year": "FY2025-26"},
                "_meta": {
                    "ug.go.ura.chatbot/tenantId": "t-9",
                    "ug.go.ura.chatbot/userRole": "verified_taxpayer",
                },
            },
        )["result"]
        self.assertFalse(result["isError"])


class EnvelopeTests(unittest.TestCase):
    def test_header_method_must_match_the_body(self) -> None:
        error = server.handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {"Mcp-Method": "tools/call"},
        )["error"]
        self.assertEqual(error["code"], server.INVALID_REQUEST)

    def test_header_name_must_match_the_body_tool(self) -> None:
        error = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "calculate_vat",
                    "arguments": {"amount": 1},
                    "_meta": request_meta(),
                },
            },
            {"Mcp-Method": "tools/call", "Mcp-Name": "ura_action_proposal"},
        )["error"]
        self.assertEqual(error["code"], server.INVALID_REQUEST)
        self.assertIn("Mcp-Name", error["message"])

    def test_missing_required_meta_is_invalid_params(self) -> None:
        error = server.handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )["error"]
        self.assertEqual(error["code"], server.INVALID_PARAMS)
        self.assertIn("io.modelcontextprotocol/protocolVersion", error["message"])

    def test_matching_header_is_accepted(self) -> None:
        response = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {"_meta": request_meta()},
            },
            {"mcp-method": "tools/list"},
        )
        self.assertIn("result", response)

    def test_notifications_get_no_response(self) -> None:
        self.assertIsNone(_rpc("notifications/progress", {}, request_id=None))

    def test_non_object_body_is_an_invalid_request(self) -> None:
        self.assertEqual(
            server.handle_request(["not", "a", "request"])["error"]["code"],
            server.INVALID_REQUEST,
        )

    def test_bad_params_type_is_rejected(self) -> None:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": "nope"}
        self.assertEqual(
            server.handle_request(body)["error"]["code"], server.INVALID_PARAMS
        )


class ParityTests(unittest.TestCase):
    """The remote path must not be able to disagree with the local one."""

    def test_server_and_registry_produce_identical_arithmetic(self) -> None:
        from app.tools import ToolRegistry

        args = {"monthly_gross": 1_234_567, "fiscal_year": "FY2026-27"}
        via_server = _rpc("tools/call", {"name": "calculate_paye", "arguments": args})["result"]
        via_registry = ToolRegistry.call("calculate_paye", args)
        self.assertEqual(via_server["structuredContent"]["paye"], via_registry["paye"])

    def test_audit_tax_document_mcp_call(self) -> None:
        text = (
            "URA EFRIS INVOICE\n"
            "TIN: 1001234567\n"
            "PRN: 20261122334455\n"
            "Subtotal: UGX 1,000,000\n"
            "VAT (18%): UGX 180,000\n"
            "Total Amount: UGX 1,180,000\n"
        )
        res = _rpc("tools/call", {"name": "audit_tax_document", "arguments": {"text": text}})["result"]
        self.assertFalse(res["isError"])
        self.assertEqual(res["structuredContent"]["status"], "verified")
        self.assertEqual(res["structuredContent"]["effective_rate"], 0.18)


if __name__ == "__main__":
    unittest.main()

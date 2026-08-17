"""2026-08-17 MCP hardening: spec _meta, MRTR, list TTL, schema tightness."""

from __future__ import annotations

import time
import unittest
from typing import Any
from unittest.mock import patch

from app.mcp.mrtr import parse_input_required, unwrap_request_state, wrap_request_state
from app.mcp.protocol import (
    META_CLIENT_CAPABILITIES,
    META_PROTOCOL_VERSION,
    MCP_PROTOCOL_VERSION,
    missing_required_meta,
    request_meta,
)
from app.mcp.transport import HttpTransport
from app.tools import ToolRegistry


class SpecMetaTests(unittest.TestCase):
    def test_request_meta_uses_reserved_spec_keys(self) -> None:
        meta = request_meta(tenant_id="t", user_id="u", user_role="public", call_id="c")
        self.assertEqual(meta[META_PROTOCOL_VERSION], MCP_PROTOCOL_VERSION)
        self.assertIsInstance(meta[META_CLIENT_CAPABILITIES], dict)
        self.assertFalse(missing_required_meta(meta))

    def test_empty_meta_is_malformed(self) -> None:
        self.assertTrue(missing_required_meta({}))
        self.assertTrue(missing_required_meta(None))


class MrtrTests(unittest.TestCase):
    def test_parse_prefers_input_requests_over_legacy_elicitations(self) -> None:
        parsed = parse_input_required(
            {
                "resultType": "input_required",
                "inputRequests": [{"method": "elicitation/create"}],
                "elicitations": ["legacy"],
            }
        )
        assert parsed is not None
        self.assertEqual(parsed["inputRequests"][0]["method"], "elicitation/create")
        self.assertNotIn("elicitations", parsed)

    def test_hmac_round_trip(self) -> None:
        with patch.dict("os.environ", {"MCP_REQUEST_STATE_SECRET": "test-secret"}):
            wrapped = wrap_request_state({"tool": "calculate_vat", "n": 1})
            self.assertEqual(wrapped["alg"], "hmac-sha256")
            self.assertEqual(unwrap_request_state(wrapped), {"tool": "calculate_vat", "n": 1})
            tampered = dict(wrapped)
            tampered["sig"] = "0" * 64
            self.assertIsNone(unwrap_request_state(tampered))

    def test_unsigned_state_is_accepted_only_without_a_secret(self) -> None:
        with patch.dict("os.environ", {"MCP_REQUEST_STATE_SECRET": ""}):
            wrapped = wrap_request_state({"n": 1})
            self.assertEqual(wrapped["alg"], "none")
            self.assertEqual(unwrap_request_state(wrapped), {"n": 1})


class ListTtlTests(unittest.TestCase):
    def test_http_transport_honours_ttl_ms(self) -> None:
        transport = HttpTransport("tax_calculator", "https://mcp.example/rpc")
        calls = {"n": 0}

        def fake_request(method: str, params: dict[str, Any], **_: Any) -> dict[str, Any]:
            calls["n"] += 1
            return {"tools": [{"name": "calculate_vat"}], "ttlMs": 50}

        transport._request = fake_request  # type: ignore[method-assign]
        self.assertEqual(len(transport.list_tools()), 1)
        self.assertEqual(len(transport.list_tools()), 1)
        self.assertEqual(calls["n"], 1)
        transport._tools_expires_at = time.monotonic() - 1
        self.assertEqual(len(transport.list_tools()), 1)
        self.assertEqual(calls["n"], 2)


class SchemaTightnessTests(unittest.TestCase):
    def test_every_registered_tool_rejects_unknown_arguments(self) -> None:
        missing = [
            tool.schema.name
            for tool in ToolRegistry.all()
            if (tool.schema.parameters or {}).get("additionalProperties") is not False
        ]
        self.assertEqual(missing, [])

    def test_audit_named_tools_declare_an_output_schema(self) -> None:
        named = {
            "get_current_date",
            "get_next_deadlines",
            "escalate_to_human",
            "search_ura_knowledge_base",
            "ura_account_profile",
            "ura_action_proposal",
        }
        missing = [
            tool.schema.name
            for tool in ToolRegistry.all()
            if tool.schema.name in named and not tool.schema.output_schema
        ]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()

"""Tests for MCP routing, argument validation, replay and circuit breaking."""

from __future__ import annotations

import unittest
from typing import Any

from app.mcp import reset_client
from app.mcp.client import MCPClient
from app.mcp.transport import (
    MCP_PROTOCOL_VERSION,
    HttpTransport,
    InProcessTransport,
    TransportError,
    build_transports,
    request_meta,
)
from app.mcp.validation import _fallback_errors
from app.tools import ToolRegistry


class RecordingTransport:
    """A stand-in remote server that records what it was asked to do."""

    def __init__(self, namespace: str = "tax_calculator", fail: bool = False) -> None:
        self.name = f"recording:{namespace}"
        self.namespace = namespace
        self.fail = fail
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def _descriptors(self) -> list[dict[str, Any]]:
        return ToolRegistry.mcp_tools(namespace=self.namespace)

    def list_tools(self) -> list[dict[str, Any]]:
        return self._descriptors()

    def describe(self, tool_name: str) -> dict[str, Any] | None:
        return next((d for d in self._descriptors() if d["name"] == tool_name), None)

    def call(
        self, tool_name: str, arguments: dict[str, Any], *, meta: dict[str, Any], timeout_s: float
    ) -> dict[str, Any]:
        self.calls.append((tool_name, arguments, meta))
        if self.fail:
            raise TransportError("remote server unreachable")
        return {"ok": True, "routed_to": self.name, "explanation": "stub"}


class RoutingTests(unittest.TestCase):
    def test_default_bindings_are_all_in_process(self) -> None:
        transports = build_transports()
        self.assertTrue(transports)
        self.assertTrue(all(isinstance(t, InProcessTransport) for t in transports.values()))
        self.assertIn("tax_calculator", transports)

    def test_a_bound_namespace_wins_over_the_local_registry(self) -> None:
        remote = RecordingTransport()
        client = MCPClient({"tax_calculator": remote, "core": InProcessTransport()})
        result = client.call_tool("calculate_vat", {"amount": 1000})
        self.assertTrue(result.ok)
        self.assertEqual(result.result["routed_to"], remote.name)
        self.assertEqual(result.transport, remote.name)
        self.assertEqual([c[0] for c in remote.calls], ["calculate_vat"])

    def test_other_namespaces_stay_in_process(self) -> None:
        remote = RecordingTransport()
        client = MCPClient({"tax_calculator": remote, "rates": InProcessTransport()})
        result = client.call_tool("lookup_rate", {"tax_type": "corporation_tax"})
        self.assertEqual(result.namespace, "rates")
        self.assertEqual(result.transport, "in_process")
        self.assertEqual(remote.calls, [])

    def test_identity_travels_in_request_meta(self) -> None:
        remote = RecordingTransport()
        client = MCPClient({"tax_calculator": remote})
        client.call_tool(
            "calculate_vat", {"amount": 1000}, tenant_id="t-1", user_id="u-1", user_role="ura_staff"
        )
        _name, _args, meta = remote.calls[0]
        self.assertEqual(meta["protocolVersion"], MCP_PROTOCOL_VERSION)
        self.assertEqual(meta["ug.go.ura.chatbot/tenantId"], "t-1")
        self.assertEqual(meta["ug.go.ura.chatbot/userRole"], "ura_staff")

    def test_health_reports_bindings(self) -> None:
        client = MCPClient({"tax_calculator": RecordingTransport()})
        health = client.health()
        self.assertEqual(health["protocol_version"], MCP_PROTOCOL_VERSION)
        self.assertEqual(
            health["namespaces"]["tax_calculator"]["transport"], "recording:tax_calculator"
        )


class ArgumentValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_client()
        self.client = MCPClient()

    def test_wrong_type_is_rejected_before_dispatch(self) -> None:
        result = self.client.call_tool("calculate_paye", {"monthly_gross": "a lot"})
        self.assertFalse(result.ok)
        self.assertIn("monthly_gross", result.result["error"])
        self.assertTrue(result.result["validation_errors"])

    def test_missing_required_argument_is_rejected(self) -> None:
        result = self.client.call_tool("calculate_paye", {})
        self.assertFalse(result.ok)
        self.assertIn("monthly_gross", result.result["error"])

    def test_unknown_argument_is_rejected(self) -> None:
        result = self.client.call_tool("calculate_vat", {"amount": 1000, "vat_rate": 0.2})
        self.assertFalse(result.ok)
        self.assertIn("vat_rate", result.result["error"])

    def test_enum_violation_is_rejected(self) -> None:
        result = self.client.call_tool(
            "calculate_withholding", {"payment_type": "vibes", "amount": 1000}
        )
        self.assertFalse(result.ok)
        self.assertIn("payment_type", result.result["error"])

    def test_valid_arguments_reach_the_tool(self) -> None:
        result = self.client.call_tool(
            "calculate_paye", {"monthly_gross": 1_000_000, "fiscal_year": "FY2025-26"}
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.result["paye"], 202_000.0)

    def test_unknown_tool_lists_what_exists(self) -> None:
        result = self.client.call_tool("calculate_moon_phase", {})
        self.assertFalse(result.ok)
        self.assertIn("calculate_vat", result.result["available_tools"])


class FallbackValidatorTests(unittest.TestCase):
    """The structural fallback must still be a real boundary.

    ``jsonschema`` is optional, and it is genuinely absent from some
    environments. A fallback that only counted keys would report "a lot"
    as a valid salary, so the tightening would silently not apply
    exactly where the dependency is missing.
    """

    _SCHEMA = {
        "type": "object",
        "properties": {
            "amount": {"type": "number", "minimum": 0, "maximum": 100},
            "mode": {"type": "string", "enum": ["add", "extract"]},
            "note": {"type": "string", "minLength": 1},
            "flag": {"type": "boolean"},
        },
        "required": ["amount"],
        "additionalProperties": False,
    }

    def _errors(self, payload: dict[str, Any]) -> list[str]:
        return _fallback_errors(self._SCHEMA, payload)

    def test_wrong_type_is_caught(self) -> None:
        self.assertIn("amount", self._errors({"amount": "a lot"})[0])

    def test_missing_required_is_caught(self) -> None:
        self.assertIn("required", self._errors({})[0])

    def test_unknown_property_is_caught(self) -> None:
        self.assertIn("unexpected", self._errors({"amount": 1, "bogus": 2})[0])

    def test_enum_violation_is_caught(self) -> None:
        self.assertIn("is not one of", self._errors({"amount": 1, "mode": "sideways"})[0])

    def test_bounds_are_checked(self) -> None:
        self.assertIn("minimum", self._errors({"amount": -1})[0])
        self.assertIn("maximum", self._errors({"amount": 500})[0])

    def test_bool_is_not_a_number(self) -> None:
        # bool subclasses int, so a naive isinstance check accepts True
        # as a salary.
        self.assertTrue(self._errors({"amount": True}))

    def test_bool_is_still_a_boolean(self) -> None:
        self.assertEqual(self._errors({"amount": 1, "flag": True}), [])

    def test_empty_string_fails_min_length(self) -> None:
        self.assertIn("non-empty", self._errors({"amount": 1, "note": ""})[0])

    def test_valid_payload_passes(self) -> None:
        self.assertEqual(self._errors({"amount": 50, "mode": "add", "note": "ok"}), [])


class IdempotencyTests(unittest.TestCase):
    def test_a_repeated_key_replays_instead_of_re_executing(self) -> None:
        remote = RecordingTransport()
        client = MCPClient({"tax_calculator": remote})
        args = {"amount": 1000}
        first = client.call_tool("calculate_vat", args, idempotency_key="k-1")
        second = client.call_tool("calculate_vat", args, idempotency_key="k-1")
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(len(remote.calls), 1)
        self.assertEqual(first.result["routed_to"], second.result["routed_to"])

    def test_replay_is_scoped_per_tenant(self) -> None:
        remote = RecordingTransport()
        client = MCPClient({"tax_calculator": remote})
        client.call_tool("calculate_vat", {"amount": 1000}, idempotency_key="k", tenant_id="a")
        result = client.call_tool(
            "calculate_vat", {"amount": 1000}, idempotency_key="k", tenant_id="b"
        )
        self.assertFalse(result.replayed)
        self.assertEqual(len(remote.calls), 2)

    def test_calls_without_a_key_always_execute(self) -> None:
        remote = RecordingTransport()
        client = MCPClient({"tax_calculator": remote})
        client.call_tool("calculate_vat", {"amount": 1000})
        client.call_tool("calculate_vat", {"amount": 1000})
        self.assertEqual(len(remote.calls), 2)


class CircuitBreakerTests(unittest.TestCase):
    def test_a_failing_namespace_opens_and_stops_being_called(self) -> None:
        remote = RecordingTransport(fail=True)
        client = MCPClient({"tax_calculator": remote})
        for _ in range(3):
            result = client.call_tool("calculate_vat", {"amount": 1000})
            self.assertFalse(result.ok)
        self.assertEqual(len(remote.calls), 3)

        blocked = client.call_tool("calculate_vat", {"amount": 1000})
        self.assertFalse(blocked.ok)
        self.assertIn("circuit open", blocked.result["error"])
        self.assertTrue(blocked.result["retryable"])
        self.assertEqual(len(remote.calls), 3, "open circuit must not reach the transport")

    def test_one_namespace_failing_does_not_block_another(self) -> None:
        failing = RecordingTransport(fail=True)
        client = MCPClient({"tax_calculator": failing, "rates": InProcessTransport()})
        for _ in range(4):
            client.call_tool("calculate_vat", {"amount": 1000})
        healthy = client.call_tool("lookup_rate", {"tax_type": "corporation_tax"})
        self.assertTrue(healthy.ok)


class DiscoveryDispatchParityTests(unittest.TestCase):
    """Whatever discovery offers, dispatch must accept — and vice versa."""

    def setUp(self) -> None:
        reset_client()
        self.client = MCPClient()

    def test_public_discovery_matches_public_dispatch(self) -> None:
        offered = set(self.client.available_for("public"))
        for descriptor in self.client.list_mcp_tools():
            name = descriptor["name"]
            result = self.client.call_tool(name, {})
            denied = result.result.get("error") == "policy_denied"
            with self.subTest(tool=name):
                self.assertEqual(name in offered, not denied)

    def test_consent_opens_the_ura_namespaces(self) -> None:
        offered = self.client.available_for(
            "verified_taxpayer", ["ura_account_access", "ura_actions"]
        )
        self.assertIn("ura_account_profile", offered)
        self.assertIn("ura_action_proposal", offered)

    def test_missing_consent_keeps_them_closed(self) -> None:
        offered = self.client.available_for("verified_taxpayer", [])
        self.assertNotIn("ura_account_profile", offered)
        self.assertNotIn("ura_action_proposal", offered)


class HttpTransportTests(unittest.TestCase):
    def test_headers_carry_method_and_name_but_never_the_token(self) -> None:
        transport = HttpTransport(
            "tax_calculator", "https://mcp.example/rpc", token="s3cret"  # noqa: S106 - test stub
        )
        headers = transport._headers("tools/call", "calculate_vat")
        self.assertEqual(headers["Mcp-Method"], "tools/call")
        self.assertEqual(headers["Mcp-Name"], "calculate_vat")
        self.assertEqual(headers["MCP-Protocol-Version"], MCP_PROTOCOL_VERSION)
        self.assertNotIn("s3cret", str({k: v for k, v in headers.items() if k != "Authorization"}))

    def test_request_meta_is_self_describing(self) -> None:
        meta = request_meta(tenant_id="t", user_id="u", user_role="public", call_id="c")
        self.assertEqual(meta["protocolVersion"], MCP_PROTOCOL_VERSION)
        self.assertIn("clientInfo", meta)


class AuditTests(unittest.TestCase):
    def test_audit_record_hashes_arguments_instead_of_storing_them(self) -> None:
        client = MCPClient()
        result = client.call_tool(
            "calculate_paye", {"monthly_gross": 4_321_000, "fiscal_year": "FY2025-26"}
        )
        record = result.to_audit_dict()
        self.assertEqual(len(record["arguments_sha256"]), 64)
        self.assertNotIn("4321000", str(record))
        self.assertEqual(record["namespace"], "tax_calculator")


if __name__ == "__main__":
    unittest.main()

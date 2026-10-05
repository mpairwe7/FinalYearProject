from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.mcp import get_client, reset_client
from app.mcp.policy import authorize_tool_call, reset_velocity_tracker
from app.tools import ToolRegistry


class MCPPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_client()

    def test_public_user_cannot_call_ura_account_tool(self) -> None:
        result = get_client().call_tool("ura_account_profile", {"taxpayer_id": "123"})
        self.assertFalse(result.ok)
        self.assertEqual(result.result["error"], "policy_denied")
        self.assertIn("authenticated user required", result.result["policy"]["reasons"])

    def test_critical_action_requires_confirmation_and_idempotency(self) -> None:
        policy = authorize_tool_call(
            name="ura_action_proposal",
            risk="critical",
            user_role="verified_taxpayer",
            granted_purposes=["ura_account_access", "ura_actions"],
            user_id="user-1",
            tenant_id="tenant-1",
        )
        self.assertFalse(policy["allowed"])
        self.assertIn("explicit user confirmation required", policy["reasons"])
        self.assertIn("idempotency_key required", policy["reasons"])

    def test_confirmed_critical_action_reaches_fail_closed_tool(self) -> None:
        result = get_client().call_tool(
            "ura_action_proposal",
            {
                "action_type": "tin_update",
                "payload": {"field": "email"},
                "idempotency_key": "idem-1",
                "submit": True,
            },
            user_id="user-1",
            tenant_id="tenant-1",
            user_role="verified_taxpayer",
            granted_purposes=["ura_account_access", "ura_actions"],
            confirmed=True,
            idempotency_key="idem-1",
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.result["error"], "URA action API is not configured")
        self.assertFalse(result.result["configured"])

    def test_transaction_ceiling_caps_enforced_for_non_admin(self) -> None:
        reset_velocity_tracker()
        policy = authorize_tool_call(
            name="payment_checkout_settle",
            risk="critical",
            user_role="verified_taxpayer",
            granted_purposes=["ura_account_access", "ura_actions"],
            user_id="user-1",
            confirmed=True,
            idempotency_key="k-1",
            arguments={"amount_ugx": 90000000.0},
        )
        self.assertFalse(policy["allowed"])
        self.assertTrue(any("exceeds session ceiling" in r for r in policy["reasons"]))

        # Admin is exempt from transaction ceiling
        admin_policy = authorize_tool_call(
            name="payment_checkout_settle",
            risk="critical",
            user_role="ura_admin",
            granted_purposes=["ura_account_access", "ura_actions"],
            user_id="admin-1",
            confirmed=True,
            idempotency_key="k-admin",
            arguments={"amount_ugx": 90000000.0},
        )
        self.assertTrue(admin_policy["allowed"])

    def test_critical_action_velocity_limit(self) -> None:
        reset_velocity_tracker()
        for i in range(10):
            res = authorize_tool_call(
                name="ura_action_proposal",
                risk="critical",
                user_role="verified_taxpayer",
                granted_purposes=["ura_account_access", "ura_actions"],
                user_id="busy-user",
                confirmed=True,
                idempotency_key=f"idem-{i}",
            )
            self.assertTrue(res["allowed"], f"Call {i} should be allowed")

        # 11th call exceeds hourly limit
        exceeded = authorize_tool_call(
            name="ura_action_proposal",
            risk="critical",
            user_role="verified_taxpayer",
            granted_purposes=["ura_account_access", "ura_actions"],
            user_id="busy-user",
            confirmed=True,
            idempotency_key="idem-11",
        )
        self.assertFalse(exceeded["allowed"])
        self.assertTrue(any("velocity limit exceeded" in r for r in exceeded["reasons"]))


class DeclarationDrivenPolicyTests(unittest.TestCase):
    """Permissions come from the tool's declarations, not from its name."""

    def test_declared_scopes_are_enforced_individually(self) -> None:
        policy = authorize_tool_call(
            name="anything",
            risk="critical",
            user_role="verified_taxpayer",
            granted_purposes=["ura_account_access"],
            user_id="u-1",
            confirmed=True,
            idempotency_key="k",
            required_scopes=("ura_account_access", "ura_actions"),
            allowed_roles=("verified_taxpayer",),
        )
        self.assertFalse(policy["allowed"])
        self.assertIn("ura_actions consent required", policy["reasons"])
        self.assertNotIn("ura_account_access consent required", policy["reasons"])

    def test_a_tool_not_named_ura_still_gets_its_scope_enforced(self) -> None:
        # The old rule keyed off a `ura_` name prefix, so a URA-touching
        # tool named otherwise was authorized as if it needed nothing.
        policy = authorize_tool_call(
            name="fetch_taxpayer_ledger",
            risk="high",
            user_role="verified_taxpayer",
            granted_purposes=[],
            user_id="u-1",
            required_scopes=("ura_account_access",),
        )
        self.assertFalse(policy["allowed"])
        self.assertIn("ura_account_access consent required", policy["reasons"])

    def test_role_exemption_covers_staff_acting_under_their_mandate(self) -> None:
        for role, allowed in (("ura_staff", True), ("public", False)):
            with self.subTest(role=role):
                policy = authorize_tool_call(
                    name="escalate_to_human",
                    risk="medium",
                    user_role=role,
                    granted_purposes=[],
                    user_id="u-1" if role != "public" else "",
                    required_scopes=("ticket_escalation",),
                    scope_exempt_roles=("ura_staff", "ura_admin"),
                )
                self.assertEqual(policy["allowed"], allowed, policy["reasons"])

    def test_unknown_risk_tier_is_treated_as_elevated(self) -> None:
        policy = authorize_tool_call(name="mystery", risk="spicy", user_role="public")
        self.assertFalse(policy["allowed"])
        self.assertIn("unknown risk tier 'spicy'", policy["reasons"])
        self.assertIn("authenticated user required", policy["reasons"])

    def test_low_risk_tools_stay_open_to_the_public(self) -> None:
        policy = authorize_tool_call(name="calculate_vat", risk="low", user_role="public")
        self.assertTrue(policy["allowed"])


class ToolDeclarationTests(unittest.TestCase):
    def test_simulated_connector_tools_stay_closed_in_production_even_with_flag(self) -> None:
        with patch.dict(
            os.environ,
            {"APP_ENV": "production", "FLAG_ENTERPRISE_CONNECTORS": "true"},
            clear=False,
        ):
            result = ToolRegistry.call("payment_view_status", {"prn": "226030001001"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["mode"], "simulation")
        self.assertIn("never enabled in production", result["error"])

    def test_every_elevated_tool_declares_its_requirements(self) -> None:
        for tool in ToolRegistry.all():
            schema = tool.schema
            if schema.risk in ("high", "critical"):
                with self.subTest(tool=schema.name):
                    self.assertTrue(
                        schema.required_scopes,
                        f"{schema.name} is {schema.risk} risk but declares no scopes",
                    )
                    self.assertTrue(schema.allowed_roles)

    def test_write_tools_are_not_annotated_read_only(self) -> None:
        for tool in ToolRegistry.all():
            schema = tool.schema
            if schema.risk == "critical":
                with self.subTest(tool=schema.name):
                    self.assertFalse(schema.read_only)
                    self.assertTrue(schema.destructive)

    def test_connector_write_tools_require_confirmation(self) -> None:
        connector_namespaces = {
            "efris",
            "payment_system",
            "bwims",
            "tin_registration",
            "digital_tax_stamps",
            "ursb",
        }
        writes = [
            tool.schema
            for tool in ToolRegistry.all()
            if tool.schema.namespace in connector_namespaces and not tool.schema.read_only
        ]
        self.assertTrue(writes)
        for schema in writes:
            with self.subTest(tool=schema.name):
                self.assertTrue(schema.requires_confirmation)

    def test_every_registered_tool_declares_output_schema(self) -> None:
        tools = ToolRegistry.all()
        self.assertTrue(tools)
        for tool in tools:
            schema = tool.schema
            with self.subTest(tool=schema.name):
                self.assertIsNotNone(schema.output_schema, f"Tool {schema.name} missing output_schema")
                self.assertEqual(schema.output_schema.get("type"), "object")
                self.assertIn("ok", schema.output_schema.get("required", []))


if __name__ == "__main__":
    unittest.main()

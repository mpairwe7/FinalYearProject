from __future__ import annotations

import unittest

from app.mcp import get_client, reset_client
from app.mcp.policy import authorize_tool_call


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


if __name__ == "__main__":
    unittest.main()

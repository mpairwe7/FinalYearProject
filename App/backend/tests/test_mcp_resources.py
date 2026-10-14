from __future__ import annotations

import unittest
from app.mcp import get_client, reset_client


class MCPResourcesAndPromptsTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_client()
        self.client = get_client()

    def test_list_resources(self) -> None:
        resources = self.client.list_resources()
        self.assertGreaterEqual(len(resources), 2)
        uris = [r.get("uri") for r in resources]
        self.assertIn("ura://rates/current", uris)
        self.assertIn("ura://calendar/deadlines", uris)

    def test_read_statutory_rates_resource(self) -> None:
        res = self.client.read_resource("ura://rates/current")
        self.assertIn("contents", res)
        self.assertEqual(len(res["contents"]), 1)
        self.assertEqual(res["contents"][0]["uri"], "ura://rates/current")
        self.assertIn("FY2026-27", res["contents"][0]["text"])

    def test_read_calendar_deadlines_resource(self) -> None:
        res = self.client.read_resource("ura://calendar/deadlines")
        self.assertIn("contents", res)
        self.assertEqual(len(res["contents"]), 1)
        self.assertEqual(res["contents"][0]["uri"], "ura://calendar/deadlines")

    def test_read_unknown_resource_raises_lookup_error(self) -> None:
        with self.assertRaises(LookupError):
            self.client.read_resource("unknown://uri/does-not-exist")

    def test_list_and_get_prompts(self) -> None:
        prompts = self.client.list_prompts()
        self.assertGreaterEqual(len(prompts), 2)
        names = [p.get("name") for p in prompts]
        self.assertIn("vat_calculation_guide", names)
        self.assertIn("tcc_tender_checklist", names)

        inst = self.client.get_prompt("vat_calculation_guide", {"amount": "500,000"})
        self.assertIn("500,000", inst["description"])
        self.assertEqual(len(inst["messages"]), 1)
        self.assertEqual(inst["messages"][0]["role"], "user")


if __name__ == "__main__":
    unittest.main()

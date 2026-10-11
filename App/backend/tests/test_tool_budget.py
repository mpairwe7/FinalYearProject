"""Tool schemas must fit the served context window (``app.tool_budget``).

The failure this guards: a turn that selected a connector carried the whole
local tool registry, ~7,100 tokens of schemas against a 4,096-token window,
so the server refused the request and the turn was answered without ever
reaching the connector. The guard is a pure function of specs, a budget and
a counter, so it is tested without a model.
"""

from __future__ import annotations

import json
import unittest

from app.tool_budget import (
    SLIM_DESCRIPTION_CHARS,
    TOOLS_BLOCK_OVERHEAD_TOKENS,
    fit_tool_specs,
    slim_tool_spec,
    tool_names,
)


def _spec(name: str, *, description: str = "Does a thing. It does it carefully and at length.", props: int = 2) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    f"field_{i}": {
                        "type": "string",
                        "description": "A long explanation of this field that costs tokens " * 3,
                    }
                    for i in range(props)
                },
                "required": ["field_0"],
                "additionalProperties": False,
            },
        },
    }


def _chars_over_four(text: str) -> int:
    return len(text) // 4


class SlimTests(unittest.TestCase):
    def test_keeps_what_a_valid_call_needs(self) -> None:
        slim = slim_tool_spec(_spec("efris_taxpayer_status"))
        function = slim["function"]
        self.assertEqual(function["name"], "efris_taxpayer_status")
        self.assertEqual(function["description"], "Does a thing.")
        parameters = function["parameters"]
        self.assertEqual(parameters["required"], ["field_0"])
        self.assertEqual(parameters["additionalProperties"], False)
        self.assertEqual(set(parameters["properties"]), {"field_0", "field_1"})
        self.assertEqual(parameters["properties"]["field_0"], {"type": "string"})

    def test_a_property_called_description_survives(self) -> None:
        spec = {
            "type": "function",
            "function": {
                "name": "t",
                "description": "x",
                "parameters": {
                    "type": "object",
                    "properties": {"description": {"type": "string", "description": "prose"}},
                },
            },
        }
        properties = slim_tool_spec(spec)["function"]["parameters"]["properties"]
        self.assertEqual(properties, {"description": {"type": "string"}})

    def test_enums_and_nested_objects_are_kept(self) -> None:
        spec = _spec("t")
        spec["function"]["parameters"]["properties"]["kind"] = {
            "type": "string",
            "enum": ["a", "b"],
            "description": "prose",
        }
        spec["function"]["parameters"]["properties"]["nested"] = {
            "type": "object",
            "properties": {"inner": {"type": "integer", "description": "prose"}},
        }
        properties = slim_tool_spec(spec)["function"]["parameters"]["properties"]
        self.assertEqual(properties["kind"], {"type": "string", "enum": ["a", "b"]})
        self.assertEqual(properties["nested"]["properties"]["inner"], {"type": "integer"})

    def test_long_descriptions_are_bounded(self) -> None:
        slim = slim_tool_spec(_spec("t", description="word " * 200))
        self.assertLessEqual(len(slim["function"]["description"]), SLIM_DESCRIPTION_CHARS)

    def test_does_not_mutate_its_input(self) -> None:
        spec = _spec("t")
        before = json.dumps(spec, sort_keys=True)
        slim_tool_spec(spec)
        self.assertEqual(json.dumps(spec, sort_keys=True), before)


class FitTests(unittest.TestCase):
    def test_specs_that_fit_are_returned_untouched(self) -> None:
        specs = [_spec("a"), _spec("b")]
        self.assertEqual(fit_tool_specs(specs, 100_000, _chars_over_four), specs)

    def test_slimming_comes_before_dropping(self) -> None:
        specs = [_spec(f"tool_{i}", props=3) for i in range(10)]
        full = _chars_over_four("\n".join(json.dumps(s, separators=(",", ":")) for s in specs))
        fitted = fit_tool_specs(specs, full // 2, _chars_over_four)
        self.assertEqual(tool_names(fitted), tool_names(specs), "every tool kept, only slimmed")
        self.assertLess(
            len(json.dumps(fitted)), len(json.dumps(specs)), "and the schemas really are smaller"
        )

    def test_when_slimming_is_not_enough_the_first_specs_win(self) -> None:
        specs = [_spec(f"tool_{i}", props=3) for i in range(10)]
        slim_cost = _chars_over_four(
            "\n".join(json.dumps(slim_tool_spec(s), ensure_ascii=False, separators=(",", ":")) for s in specs)
        )
        budget = TOOLS_BLOCK_OVERHEAD_TOKENS + slim_cost // 2
        fitted = fit_tool_specs(specs, budget, _chars_over_four)
        names = tool_names(fitted)
        self.assertTrue(0 < len(names) < len(specs))
        self.assertEqual(names, tool_names(specs)[: len(names)], "priority order is preserved")

    def test_nothing_fits_returns_empty_not_a_request_the_server_will_refuse(self) -> None:
        self.assertEqual(fit_tool_specs([_spec("a")], TOOLS_BLOCK_OVERHEAD_TOKENS, _chars_over_four), [])
        self.assertEqual(fit_tool_specs([_spec("a")], 0, _chars_over_four), [])
        self.assertEqual(fit_tool_specs([], 5_000, _chars_over_four), [])

    def test_the_result_actually_fits(self) -> None:
        specs = [_spec(f"tool_{i}", props=4) for i in range(24)]
        for budget in (400, 800, 1_500, 3_000):
            with self.subTest(budget=budget):
                fitted = fit_tool_specs(specs, budget, _chars_over_four)
                cost = _chars_over_four("\n".join(json.dumps(s, ensure_ascii=False, separators=(",", ":")) for s in fitted))
                self.assertLessEqual(cost + TOOLS_BLOCK_OVERHEAD_TOKENS, budget)


if __name__ == "__main__":
    unittest.main()

"""The agent loop and its caller, once a chosen service has answered.

``test_connector_outcome.py`` pins the words. This pins where they are used:
the loop does not spend a round paraphrasing "no record", a failed lookup is
kept out of what the model is shown in a mixed turn, the tool schemas are
fitted again once results are in the prompt (a second lookup overflowed a
4,096-token window live), and the shared funnel puts the composed text — not
the model's — into the reply.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from app import llm
from app import service as service_module
from app.connector_outcome import DEMO_NOTICE
from app.flags import flags
from app.mcp import MCPClient
from app.mcp.transport import InProcessTransport

QUERY = "Check TIN 1000000001 on EFRIS and the business Sample Traders Limited on URSB."
FOUND_PROFILE = {
    "ok": True,
    "tin": "1000000001",
    "registered": True,
    "profile": {"business_name": "Kampala City Supermarket", "efris_status": "ACTIVE"},
    "mode": "simulation",
}
NO_BUSINESS = {
    "ok": True,
    "found": False,
    "entity": None,
    "message": "No registered entity found matching query 'Sample Traders Limited'.",
    "mode": "simulation",
}


class Remote:
    """A bound server with one read operation and a fixed answer."""

    name = "static-remote"

    def __init__(self, namespace: str, tool: str, argument: str, result: dict[str, Any]) -> None:
        self.namespace = namespace
        self.result = result
        self.calls: list[dict[str, Any]] = []
        self.descriptors = [
            {
                "name": tool,
                "description": f"Look something up in {namespace}.",
                "inputSchema": {
                    "type": "object",
                    "properties": {argument: {"type": "string", "description": "What to look up."}},
                    "required": [argument],
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": True, "destructiveHint": False},
                "_meta": {
                    "ug.go.ura.chatbot/risk": "low",
                    "ug.go.ura.chatbot/requiredScopes": [],
                    "ug.go.ura.chatbot/requiresConfirmation": False,
                },
            }
        ]

    def list_tools(self) -> list[dict[str, Any]]:
        return self.descriptors

    def describe(self, tool_name: str) -> dict[str, Any] | None:
        return next((d for d in self.descriptors if d["name"] == tool_name), None)

    def call(self, tool_name: str, arguments: dict[str, Any], **_: Any) -> dict[str, Any]:
        self.calls.append(arguments)
        return dict(self.result)


@pytest.fixture
def stack(monkeypatch):
    efris = Remote("efris", "efris_taxpayer_status", "tin", FOUND_PROFILE)
    ursb = Remote("ursb", "ursb_verify_business", "query", NO_BUSINESS)
    client = MCPClient({"core": InProcessTransport(), "efris": efris, "ursb": ursb})
    monkeypatch.setattr(flags, "is_enabled", lambda name: name == "enterprise_connectors")
    monkeypatch.setattr(llm, "LLM_BACKEND", "vllm")
    monkeypatch.setattr(llm, "_vllm_ready", lambda: True)
    monkeypatch.setattr("app.mcp.get_client", lambda: client)
    monkeypatch.setattr(llm, "_select_tools_for_query", lambda _query, names, **_options: names)
    monkeypatch.setattr(llm, "_count_prompt_tokens", lambda messages: 10 * len(messages))
    return efris, ursb


class Model:
    """A model that makes the forced call it is told to, then writes a summary."""

    ARGUMENTS = {
        "efris_taxpayer_status": {"tin": "1000000001"},
        "ursb_verify_business": {"query": "Sample Traders Limited"},
    }

    def __init__(self, summary: str = "EFRIS lists this taxpayer as registered.") -> None:
        self.rounds: list[dict[str, Any]] = []
        self.summary = summary

    def __call__(self, messages, *, tools=None, tool_choice=None, **_kwargs):
        self.rounds.append({"messages": [dict(m) for m in messages], "tools": tools, "tool_choice": tool_choice})
        if isinstance(tool_choice, dict):
            name = tool_choice["function"]["name"]
            return {"content": "", "tool_calls": [{"name": name, "arguments": self.ARGUMENTS[name]}]}
        return {"content": self.summary, "tool_calls": []}


def run(monkeypatch, model: Model, *, namespaces, forced, events=None):
    monkeypatch.setattr(llm, "_vllm_chat_completion", model)
    return llm.generate_with_tools(
        query=QUERY,
        passages=[],
        locale="en",
        connector_namespaces=namespaces,
        forced_operations=forced,
        event_callback=events.append if events is not None else None,
    )


class TestNoRecord:
    def test_no_round_is_spent_paraphrasing_a_missing_record(self, stack, monkeypatch):
        model = Model()
        events: list[dict[str, Any]] = []

        result = run(monkeypatch, model, namespaces=["ursb"], forced=["ursb_verify_business"], events=events)

        assert len(model.rounds) == 1, "the forced call is the only model round"
        assert result["text"] == ""
        call = result["tool_calls"][0]
        assert (call["connector"], call["connector_mode"]) == ("ursb", "simulation")
        assert call["result"]["found"] is False
        assert events[-1]["type"] == "iteration.final"

    def test_a_record_still_gets_the_model_s_summary(self, stack, monkeypatch):
        model = Model()

        result = run(monkeypatch, model, namespaces=["efris"], forced=["efris_taxpayer_status"])

        assert len(model.rounds) == 2
        assert result["text"] == "EFRIS lists this taxpayer as registered."


class TestMixedTurn:
    def test_a_turn_with_one_missing_record_is_not_summarised_by_the_model(self, stack, monkeypatch):
        model = Model("The business Sample Traders Limited is registered with URSB.")

        result = run(
            monkeypatch,
            model,
            namespaces=["efris", "ursb"],
            forced=["efris_taxpayer_status", "ursb_verify_business"],
        )

        # Two forced calls and no summarising round: the words come from the
        # services (see test_connector_outcome), not from a model that, given
        # this exact mix, said the business was registered.
        assert [r["tool_choice"] is not None for r in model.rounds] == [True, True]
        assert result["text"] == ""
        assert [call["connector"] for call in result["tool_calls"]] == ["efris", "ursb"]
        assert result["tool_calls"][1]["result"]["message"].startswith("No registered entity")

    def test_each_forced_call_is_scoped_to_its_own_service(self, stack, monkeypatch):
        model = Model()

        run(
            monkeypatch,
            model,
            namespaces=["efris", "ursb"],
            forced=["efris_taxpayer_status", "ursb_verify_business"],
        )

        def question(round_: dict[str, Any]) -> str:
            return next(m["content"] for m in reversed(round_["messages"]) if m["role"] == "user")

        assert "about EFRIS" in question(model.rounds[0])
        assert "about URSB" in question(model.rounds[1])
        assert "do not reuse one given for another service" in question(model.rounds[1])


class TestARecordInATranslatedReply:
    def test_the_model_does_not_write_the_record_up_when_the_reply_is_translated(self, stack, monkeypatch):
        model = Model()
        monkeypatch.setattr(llm, "_vllm_chat_completion", model)

        result = llm.generate_with_tools(
            query=QUERY,
            passages=[],
            locale="lg",
            connector_namespaces=["efris"],
            forced_operations=["efris_taxpayer_status"],
            summarise_records=False,
        )

        assert len(model.rounds) == 1, "only the forced call: no Luganda prose to invent from"
        assert result["text"] == ""
        assert result["tool_calls"][0]["connector"] == "efris"

    def test_english_keeps_the_models_summary(self, stack, monkeypatch):
        model = Model()
        monkeypatch.setattr(llm, "_vllm_chat_completion", model)

        result = llm.generate_with_tools(
            query=QUERY,
            passages=[],
            locale="en",
            connector_namespaces=["efris"],
            forced_operations=["efris_taxpayer_status"],
        )

        assert len(model.rounds) == 2
        assert result["text"] == "EFRIS lists this taxpayer as registered."


class TestToolsAreFittedEachRound:
    def test_a_round_that_follows_results_offers_only_what_still_fits(self, stack, monkeypatch):
        # Every message costs 1,000 tokens, and so does the tool block. In a
        # 4,096 window with 512 kept for the answer: the first prompt (system
        # prompt + question) leaves room for the tools; after the model's call
        # and the result are in it, nothing does.
        monkeypatch.setattr(llm, "_served_context_window", lambda: 4096)
        monkeypatch.setattr(llm, "LLM_CONTEXT_WINDOW", 4096)
        monkeypatch.setattr(llm, "LLM_MAX_TOKENS", 512)
        monkeypatch.setattr(llm, "_count_prompt_tokens", lambda messages: 1000 * len(messages))
        model = Model()

        run(monkeypatch, model, namespaces=["efris"], forced=["efris_taxpayer_status"])

        assert model.rounds[0]["tool_choice"] is not None
        final = model.rounds[-1]
        assert final["tool_choice"] is None
        assert not final["tools"], "no tool schemas beside results that already fill the window"

    def test_tools_are_offered_when_there_is_room(self, stack, monkeypatch):
        model = Model()

        run(monkeypatch, model, namespaces=["efris"], forced=["efris_taxpayer_status"])

        assert {spec["function"]["name"] for spec in model.rounds[-1]["tools"]} >= {"efris_taxpayer_status"}


class TestMissingIdentifier:
    def test_the_loop_says_what_to_ask_for_in_the_services_own_words(self, stack, monkeypatch):
        efris, _ = stack

        def invents(messages, *, tools=None, tool_choice=None, **_kwargs):
            if isinstance(tool_choice, dict):
                return {"content": "", "tool_calls": [{"name": "efris_taxpayer_status", "arguments": {"tin": "1000000009"}}]}
            return {"content": "unused", "tool_calls": []}

        monkeypatch.setattr(llm, "_vllm_chat_completion", invents)
        result = llm.generate_with_tools(
            query="Is my taxpayer registered on EFRIS?",
            passages=[],
            locale="en",
            connector_namespaces=["efris"],
            forced_operations=["efris_taxpayer_status"],
        )

        call = result["tool_calls"][0]
        assert call["result"]["error"] == "argument_not_provided"
        assert call["result"]["argument_labels"] == ["What to look up"]
        assert efris.calls == [], "nothing was asked of the service with an invented identifier"
        assert result["text"] == ""


class TestAnEmptyForcedCall:
    def test_a_call_that_leaves_out_the_identifier_is_asked_about_and_not_sent(self, stack, monkeypatch):
        efris, _ = stack

        def leaves_it_out(messages, *, tools=None, tool_choice=None, **_kwargs):
            if isinstance(tool_choice, dict):
                return {"content": "", "tool_calls": [{"name": "efris_taxpayer_status", "arguments": {}}]}
            return {"content": "unused", "tool_calls": []}

        monkeypatch.setattr(llm, "_vllm_chat_completion", leaves_it_out)
        result = llm.generate_with_tools(
            query="Is my taxpayer registered on EFRIS?",
            passages=[],
            locale="en",
            connector_namespaces=["efris"],
            forced_operations=["efris_taxpayer_status"],
        )

        call = result["tool_calls"][0]
        assert call["result"]["error"] == "argument_not_provided"
        assert call["result"]["arguments"] == ["tin"]
        assert efris.calls == []
        assert result["text"] == ""


class TestTheFunnelWordsTheReply:
    @pytest.fixture(autouse=True)
    def _breaker(self, monkeypatch):
        monkeypatch.setattr(service_module._LLM_CIRCUIT, "allow_request", lambda: True)

    def _funnel(self, monkeypatch, agent_result: dict[str, Any]) -> dict[str, Any]:
        monkeypatch.setattr(service_module.llm_module, "generate_with_tools", lambda **_kw: agent_result)
        return service_module._call_llm_agentic(
            query=QUERY,
            passages=[],
            conversation_history=None,
            locale="en",
            connector_namespaces=["efris", "ursb"],
        )

    def test_a_missing_record_is_worded_by_the_service_not_the_model(self, monkeypatch):
        call = {
            "name": "dts_verify_stamp",
            "arguments": {"stamp_code": "X"},
            "result": {"ok": False, "error": "Not in the fixtures. This does not establish whether it is genuine."},
            "connector": "digital_tax_stamps",
            "connector_mode": "simulation",
        }

        result = self._funnel(monkeypatch, {"text": "Likely counterfeit!", "tool_calls": [call], "iterations": 1})

        assert "counterfeit" not in result["text"]
        assert result["text"].startswith("Not in the fixtures. This does not establish whether it is genuine.")
        assert result["text"].endswith(DEMO_NOTICE)

    def test_an_empty_model_text_for_a_missing_record_is_not_a_failed_turn(self, monkeypatch):
        call = {
            "name": "ursb_verify_business",
            "arguments": {"query": "X"},
            "result": NO_BUSINESS,
            "connector": "ursb",
            "connector_mode": "simulation",
        }

        result = self._funnel(monkeypatch, {"text": "", "tool_calls": [call], "iterations": 1})

        assert result["text"].startswith("No registered entity found")

    @pytest.mark.parametrize(("locale", "summarise"), [("en", True), ("", True), ("lg", False), ("sw", False)])
    def test_only_a_translated_reply_leaves_the_record_to_be_worded_by_code(self, monkeypatch, locale, summarise):
        seen: dict[str, Any] = {}

        def agent(**kwargs):
            seen.update(kwargs)
            return {"text": "", "tool_calls": [], "iterations": 1}

        monkeypatch.setattr(service_module.llm_module, "generate_with_tools", agent)

        service_module._call_llm_agentic(
            query=QUERY,
            passages=[],
            conversation_history=None,
            locale=locale,
            connector_namespaces=["efris"],
        )

        assert seen["summarise_records"] is summarise

    def test_a_record_in_a_translated_reply_is_rendered_from_its_fields(self, monkeypatch):
        call = {
            "name": "efris_taxpayer_status",
            "arguments": {"tin": "1000000001"},
            "result": FOUND_PROFILE,
            "connector": "efris",
            "connector_mode": "simulation",
        }

        result = self._funnel(monkeypatch, {"text": "", "tool_calls": [call], "iterations": 1})

        assert result["text"].startswith("EFRIS returned this record:\n\n- Registered: yes")
        assert "- Business name: Kampala City Supermarket" in result["text"]
        assert result["text"].endswith(DEMO_NOTICE)

    def test_a_turn_without_a_connector_call_is_untouched(self, monkeypatch):
        local = {"name": "calculate_vat", "arguments": {}, "result": {"ok": True}}
        original = {"text": "VAT on that is 180,000.", "tool_calls": [local], "iterations": 2}

        assert self._funnel(monkeypatch, dict(original))["text"] == original["text"]

    def test_the_composed_reply_survives_json_round_tripping(self, monkeypatch):
        call = {
            "name": "efris_taxpayer_status",
            "arguments": {"tin": "1000000001"},
            "result": FOUND_PROFILE,
            "connector": "efris",
            "connector_mode": "simulation",
        }

        result = self._funnel(monkeypatch, {"text": "Registered.", "tool_calls": [call], "iterations": 2})

        assert json.loads(json.dumps(result))["text"] == f"Registered.\n\n{DEMO_NOTICE}"

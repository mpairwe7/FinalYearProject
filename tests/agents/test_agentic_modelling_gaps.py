"""Tests for Issue #441: agentic modelling gaps.

Covers:
1. vLLM Tool-Calling support and XML tag parsing
2. Decoupled tool execution & pipeline inversion (no premature passage abstention)
3. Graph argument binding for calculation and rate tools
4. Epistemic false-premise rejection for fictitious statutory instruments (G43)
5. LangGraph runtime feature flag integration
6. Streaming parity with force_agentic
7. Workflow resumption cues in next_actions
"""

from __future__ import annotations

import asyncio
import json
import unittest.mock as mock
from typing import Any

import pytest
from app.agents.graphs import AgentGraphState, GraphOutcome
from app.agents.graphs.main_graph import bind_arguments, build_main_graph
from app.premise_guard import check_false_premise
from app.service import ChatModel, _call_llm_agentic, run_chat_turn


# ---------------------------------------------------------------------------
# 1. vLLM Tool Calling & XML Parsing
# ---------------------------------------------------------------------------
class TestVLLMToolCalling:
    def test_vllm_chat_completion_with_openai_tool_calls(self, monkeypatch):
        from app import llm

        monkeypatch.setattr(llm, "LLM_BACKEND", "vllm")
        monkeypatch.setattr(llm, "VLLM_BASE_URL", "http://vllm-mock:8000/v1")

        fake_response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_123",
                                "type": "function",
                                "function": {
                                    "name": "calculate_vat",
                                    "arguments": json.dumps({"amount": 100000.0}),
                                },
                            }
                        ],
                    }
                }
            ]
        }

        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = json.dumps(fake_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with mock.patch("urllib.request.urlopen", return_value=mock_resp):
            res = llm._vllm_chat_completion(
                messages=[{"role": "user", "content": "calculate vat"}],
                tools=[{"type": "function", "function": {"name": "calculate_vat"}}],
            )
            calls = res["tool_calls"]
            assert calls is not None
            assert len(calls) == 1
            assert calls[0]["name"] == "calculate_vat"
            assert calls[0]["arguments"] == {"amount": 100000.0}

    def test_vllm_chat_completion_with_xml_tool_call(self, monkeypatch):
        from app import llm

        monkeypatch.setattr(llm, "LLM_BACKEND", "vllm")
        monkeypatch.setattr(llm, "VLLM_BASE_URL", "http://vllm-mock:8000/v1")

        fake_xml_content = (
            "Let me calculate that.\n"
            "<tool_call>{\"name\": \"calculate_vat\", \"arguments\": {\"amount\": 500000}}</tool_call>"
        )
        fake_response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": fake_xml_content,
                    }
                }
            ]
        }

        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = json.dumps(fake_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with mock.patch("urllib.request.urlopen", return_value=mock_resp):
            res = llm._vllm_chat_completion(
                messages=[{"role": "user", "content": "calculate vat on 500k"}],
            )
            calls = res["tool_calls"]
            assert calls is not None
            assert len(calls) == 1
            assert calls[0]["name"] == "calculate_vat"
            assert calls[0]["arguments"] == {"amount": 500000}


# ---------------------------------------------------------------------------
# 2. Pipeline Inversion & Decoupled Tool Execution
# ---------------------------------------------------------------------------
class TestDecoupledToolExecution:
    def test_agentic_tool_use_succeeds_even_with_empty_hits(
        self, fresh_registry, clean_flags, monkeypatch
    ):
        """When tool use is active and tools succeed, empty hits do not cause abstention."""
        model = ChatModel()
        monkeypatch.setattr(model, "_llm_available", True)
        monkeypatch.setattr(model, "_retriever_ready", False)

        def mock_agentic(*args, **kwargs):
            return {
                "text": "The VAT on UGX 1,000,000 at 18% is UGX 180,000.",
                "tool_calls": [{"name": "calculate_vat", "args": {"amount": 1000000.0}}],
                "iterations": 1,
                "truncated": False,
            }

        monkeypatch.setattr("app.service._call_llm_agentic", mock_agentic)
        monkeypatch.setattr("app.service.plan_calculation", lambda *args, **kwargs: None)

        clean_flags.set("tool_use", True)
        result = model.generate(  # nosemgrep: ura-llm01-raw-user-input-to-llm
            "Calculate VAT on 1,000,000 UGX",
            user_id="test-user",
        )
        assert result["retrieval_mode"] == "agentic"
        assert "180,000" in result["reply"]
        assert result["escalation_required"] is False
        assert result["reply"] != "I don't have enough verified information to answer this reliably."


# ---------------------------------------------------------------------------
# 3. Graph Argument Binding
# ---------------------------------------------------------------------------
class TestGraphArgumentBinding:
    def test_bind_arguments_extracts_calculation_params(self, fresh_registry):
        state_vat = AgentGraphState(query="Calculate VAT on 1,000,000 UGX")
        bound_vat = bind_arguments("calculate_vat", state_vat)
        assert bound_vat is not None
        assert bound_vat.get("amount") == 1000000.0

        state_rate = AgentGraphState(query="What is the VAT rate in Uganda?")
        bound_rate = bind_arguments("lookup_rate", state_rate)
        assert bound_rate is not None
        assert bound_rate.get("tax_type") in ("vat", "vat_standard")

    def test_bind_arguments_returns_none_when_required_param_absent(self, fresh_registry):
        state_incomplete = AgentGraphState(query="What is the formula for VAT?")
        assert bind_arguments("calculate_vat", state_incomplete) is None


# ---------------------------------------------------------------------------
# 4. Epistemic False Premise Rejection (G43)
# ---------------------------------------------------------------------------
class TestEpistemicFalsePremiseRejection:
    def test_check_false_premise_detects_invented_taxes(self):
        res1 = check_false_premise("What is the URA Digital Nomad Levy?")
        assert res1.is_false_premise is True
        assert "Digital Nomad Levy" in res1.concept
        assert "there is no official" in res1.reply

        res2 = check_false_premise("How much is the Uganda Space Exploration Tax?")
        assert res2.is_false_premise is True
        assert "Space Exploration Tax" in res2.concept

    def test_check_false_premise_allows_legitimate_taxes(self):
        assert check_false_premise("What is the rate of PAYE tax?").is_false_premise is False
        assert check_false_premise("How do I calculate VAT duty?").is_false_premise is False
        assert check_false_premise("Tell me about withholding tax").is_false_premise is False
        assert check_false_premise("What is customs duty on cars?").is_false_premise is False

    def test_generate_rejects_false_premise_without_hallucination(self, fresh_registry):
        model = ChatModel()
        result = model.generate("What is the URA Digital Nomad Levy?")  # nosemgrep: ura-llm01-raw-user-input-to-llm
        assert result["retrieval_mode"] == "false_premise_rejected"
        assert "there is no official" in result["reply"]
        assert result["faithfulness_score"] == 1.0
        assert result["agent_role"] == "epistemic_guard"


# ---------------------------------------------------------------------------
# 5. LangGraph Feature Flag Integration
# ---------------------------------------------------------------------------
class TestLangGraphFeatureFlag:
    def test_langgraph_flag_routes_through_build_main_graph(
        self, fresh_registry, clean_flags, monkeypatch
    ):
        model = ChatModel()
        clean_flags.set("langgraph", True)
        result = model.generate("What is the URA Contact Centre phone number?")  # nosemgrep: ura-llm01-raw-user-input-to-llm
        assert result["retrieval_mode"].startswith("graph_")
        assert result["agent_role"] == "graph_agent"


# ---------------------------------------------------------------------------
# 6. Streaming Parity with force_agentic
# ---------------------------------------------------------------------------
class TestStreamingAgenticParity:
    @pytest.mark.asyncio
    async def test_streaming_routes_agentic_even_with_empty_hits(
        self, fresh_registry, clean_flags, monkeypatch
    ):
        model = ChatModel()
        from app import llm

        monkeypatch.setattr(llm, "is_available", lambda: True)

        async def fake_stream_agentic(*args, **kwargs):
            yield ("tool.started", {"tool": "calculate_vat"})
            yield ("tool.completed", {"tool": "calculate_vat", "ok": True})
            yield ("_full_reply", "The VAT on UGX 1,000,000 is UGX 180,000.")

        monkeypatch.setattr("app.service._stream_agentic_turn", fake_stream_agentic)

        # generate_retrieval_only returning force_agentic=True with empty hits
        def fake_retrieval(*args, **kwargs):
            return {
                "reply": "",
                "sources": [],
                "citations": [],
                "_hits": [],
                "_history": [],
                "_rewritten": "calculate vat on 1000000",
                "_force_agentic": True,
                "_force_tool_whitelist": ["calculate_vat"],
                "agent_role": "tool_specialist",
                "retrieval_mode": "keyword",
            }

        monkeypatch.setattr(model, "generate_retrieval_only", fake_retrieval)

        events = []
        async for event in run_chat_turn(
            model=model,
            message="calculate vat on 1000000",
            conversation_id="conv-stream",
            top_k=4,
            locale="en",
            session_id="sess-stream",
            request_id="req-stream",
            user_id="u123",
            tenant_id="default",
        ):
            events.append(event)

        event_types = [e[0] for e in events]
        assert "tool.started" in event_types
        assert "tool.completed" in event_types
        assert "grounding" in event_types


# ---------------------------------------------------------------------------
# 7. Workflow Resumption Next Actions
# ---------------------------------------------------------------------------
class TestWorkflowResumptionNextActions:
    def test_active_workflow_reflected_in_next_actions_on_subject_change(
        self, fresh_registry, clean_flags, monkeypatch
    ):
        from app import database as db

        thread_id = "test-thread-resume"
        # Seed an active workflow session
        db.upsert_workflow_session(
            conversation_id=thread_id,
            workflow_id="tin_procedure_help",
            current_step_idx=0,
            slots={},
            status="active",
        )

        model = ChatModel()
        clean_flags.set("workflows", True)
        result = model.generate(  # nosemgrep: ura-llm01-raw-user-input-to-llm
            "What is the VAT rate in Uganda?",
            conversation_id=thread_id,
        )
        # Next actions should provide a resumption cue
        actions = result.get("next_actions", [])
        assert any("Resume" in a for a in actions)

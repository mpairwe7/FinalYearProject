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

    def test_vllm_ready_and_generate_with_tools(self, monkeypatch):
        from app import llm

        monkeypatch.setattr(llm, "LLM_BACKEND", "vllm")
        monkeypatch.setattr(llm, "LLM_ENABLED", True)
        monkeypatch.setattr(llm, "VLLM_BASE_URL", "http://vllm-mock:8000/v1")

        assert llm._vllm_ready() is True

        fake_chat = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "The VAT on 1,000,000 is 180,000.",
                        "tool_calls": [],
                    }
                }
            ]
        }
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = json.dumps(fake_chat).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with mock.patch("urllib.request.urlopen", return_value=mock_resp):
            out = llm.generate_with_tools(
                query="What is the VAT on 1,000,000?",
                passages=[],
                conversation_history=None,
                locale="en",
            )
            assert "180,000" in out["text"]
            assert out["iterations"] == 1

    def test_vllm_chat_completion_http_error_graceful_handling(self, monkeypatch):
        import urllib.error
        from app import llm

        monkeypatch.setattr(llm, "LLM_BACKEND", "vllm")
        monkeypatch.setattr(llm, "VLLM_BASE_URL", "http://vllm-mock:8000/v1")

        http_err = urllib.error.HTTPError(
            url="http://vllm-mock:8000/v1/chat/completions",
            code=400,
            msg="Bad Request",
            hdrs={},  # type: ignore
            fp=mock.MagicMock(read=lambda: b'{"error": "tool error"}'),
        )

        with mock.patch("urllib.request.urlopen", side_effect=http_err):
            res = llm._vllm_chat_completion(messages=[{"role": "user", "content": "hi"}])
            assert res == {"content": "", "tool_calls": []}


# ---------------------------------------------------------------------------
# 2. Pipeline Inversion & Decoupled Tool Execution
# ---------------------------------------------------------------------------
class TestDecoupledToolExecution:
    def test_agentic_tool_use_succeeds_even_with_empty_hits(
        self, fresh_registry, clean_flags, monkeypatch, tmp_db
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

    def test_bind_arguments_calendar_and_user_binding(self, fresh_registry):
        state_cal = AgentGraphState(query="What is today's date?")
        bound_cal = bind_arguments("get_current_date", state_cal)
        assert bound_cal == {}

        state_user = AgentGraphState(query="Check my account", user_id="1000012345")
        bound_user = bind_arguments("ura_account_profile", state_user)
        assert bound_user is not None
        assert bound_user.get("taxpayer_id") == "1000012345"

    def test_bind_arguments_fallback_to_raw_query(self, fresh_registry):
        state_query = AgentGraphState(
            query="Calculate VAT on 500,000 UGX",
            rewritten_query="calculate general value added tax without number",
        )
        bound = bind_arguments("calculate_vat", state_query)
        assert bound is not None
        assert bound.get("amount") == 500000.0

    def test_bind_arguments_returns_none_when_required_param_absent(self, fresh_registry):
        state_incomplete = AgentGraphState(query="What is the formula for VAT?")
        assert bind_arguments("calculate_vat", state_incomplete) is None

    def test_graph_synthesizes_prose_for_rate_and_calendar_tools(self, fresh_registry):
        from app.agents.graphs.main_graph import _format_observation_prose

        rate_obs = {
            "ok": True,
            "tax_type": "vat_standard",
            "display_name": "VAT (standard rate)",
            "formatted": "18%",
            "fiscal_year": "FY2026-27",
        }
        prose_rate = _format_observation_prose(rate_obs)
        assert "18%" in prose_rate
        assert "VAT" in prose_rate

        cal_obs = {
            "ok": True,
            "today": "2026-09-05",
            "day_of_week": "Saturday",
            "fiscal_year": "FY2026-27",
        }
        prose_cal = _format_observation_prose(cal_obs)
        assert "Saturday" in prose_cal
        assert "2026-09-05" in prose_cal


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
        assert check_false_premise("What is property tax in Uganda?").is_false_premise is False
        assert check_false_premise("What is rental income tax?").is_false_premise is False

    def test_conversational_and_action_queries_not_false_premises(self):
        """Conversational action queries ('pay tax', 'file tax') are not fictitious statutory instruments."""
        conversational = [
            "I want to pay tax",
            "How do I pay tax",
            "Can I pay tax online",
            "Where do I pay my tax",
            "Help me register for tax",
            "I need to file my tax return",
            "Calculate tax on my salary",
        ]
        for query in conversational:
            res = check_false_premise(query)
            assert res.is_false_premise is False, f"Expected False for '{query}', got {res}"

    def test_false_premise_not_bypassed_by_partial_word_hits(self):
        """Fuzzy hits mentioning 'digital tracking' must NOT bypass false-premise rejection for Digital Nomad Levy."""
        fuzzy_hits = [
            {"text": "The Digital Tracking System (DTS) was introduced by URA for excise duty."},
            {"text": "Uganda has vast nature reserve exploration and space for agriculture."},
        ]
        res = check_false_premise("What is the URA Digital Nomad Levy?", hits=fuzzy_hits)
        assert res.is_false_premise is True
        assert "Digital Nomad Levy" in res.concept

    def test_false_premise_allows_concept_when_explicitly_in_corpus(self):
        """When the concept is officially defined in the knowledge base, it is not rejected."""
        valid_hits = [
            {"text": "Under statutory notice, the Digital Nomad Levy applies to non-resident freelancers."}
        ]
        res = check_false_premise("What is the URA Digital Nomad Levy?", hits=valid_hits)
        assert res.is_false_premise is False

    def test_generate_rejects_false_premise_without_hallucination(self, fresh_registry, tmp_db):
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
        self, fresh_registry, clean_flags, monkeypatch, tmp_db
    ):
        model = ChatModel()
        clean_flags.set("langgraph", True)
        result = model.generate("What is the URA Contact Centre phone number?")  # nosemgrep: ura-llm01-raw-user-input-to-llm
        assert result["retrieval_mode"].startswith("graph_")
        assert result["agent_role"] == "graph_agent"

    def test_langgraph_routes_specialist_role_in_result(
        self, fresh_registry, clean_flags, monkeypatch, tmp_db
    ):
        model = ChatModel()
        clean_flags.set("langgraph", True)
        result = model.generate("What are the customs clearance requirements for export?")  # nosemgrep: ura-llm01-raw-user-input-to-llm
        assert result["retrieval_mode"].startswith("graph_")
        assert result["agent_role"] in ("tool_specialist", "customs_specialist", "tax_specialist")


# ---------------------------------------------------------------------------
# 6. Streaming Parity with force_agentic
# ---------------------------------------------------------------------------
class TestStreamingAgenticParity:
    def test_streaming_routes_agentic_even_with_empty_hits(
        self, fresh_registry, clean_flags, monkeypatch
    ):
        async def _run():
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

        asyncio.run(_run())

    def test_streaming_runs_langgraph_when_flag_enabled(
        self, fresh_registry, clean_flags, monkeypatch, tmp_db
    ):
        async def _run():
            model = ChatModel()
            clean_flags.set("langgraph", True)

            events = []
            async for event in run_chat_turn(
                model=model,
                message="What is the URA Contact Centre phone number?",
                conversation_id="conv-lg-stream",
                top_k=4,
                locale="en",
                session_id="sess-lg-stream",
                request_id="req-lg-stream",
                user_id="u123",
                tenant_id="default",
            ):
                events.append(event)

            event_types = [e[0] for e in events]
            assert "metadata" in event_types
            assert "token" in event_types
            assert "done" in event_types
            meta = [e[1] for e in events if e[0] == "metadata"][0]
            assert meta["retrieval_mode"].startswith("graph_")

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# 7. Workflow Resumption Next Actions
# ---------------------------------------------------------------------------
class TestWorkflowResumptionNextActions:
    def test_active_workflow_reflected_in_next_actions_on_subject_change(
        self, fresh_registry, clean_flags, monkeypatch, tmp_db
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

    def test_workflow_resume_keyword_triggers_prompt_recap(
        self, fresh_registry, clean_flags, monkeypatch, tmp_db
    ):
        from app import database as db

        thread_id = "test-thread-resume-keyword"
        db.upsert_workflow_session(
            conversation_id=thread_id,
            workflow_id="tin_procedure_help",
            current_step_idx=0,
            slots={},
            status="active",
        )

        model = ChatModel()
        clean_flags.set("workflows", True)
        # User says 'resume'
        result = model.generate(  # nosemgrep: ura-llm01-raw-user-input-to-llm
            "resume",
            conversation_id=thread_id,
        )
        assert result["retrieval_mode"] == "workflow"
        assert "Resuming" in result["reply"]
        assert result["workflow"]["status"] == "active"

    def test_interrogative_prefix_stripped_cleanly(self):
        from app.premise_guard import _extract_candidate_tax_concepts, check_false_premise

        candidates = _extract_candidate_tax_concepts("Is there a plastic bag levy in Uganda?")
        assert len(candidates) >= 1
        assert candidates[0][0] == "plastic bag"
        assert "is there" not in candidates[0][0]

    def test_psf_levy_in_corpus_not_rejected(self):
        from app.premise_guard import check_false_premise

        hits = [{"text": "The PSF levy is an administrative charge for private sector development."}]
        res = check_false_premise("What is the PSF levy in Uganda?", hits=hits)
        assert res.is_false_premise is False

    def test_default_next_actions_prepends_resume_and_keeps_base_actions(self):
        model = ChatModel()
        actions_handoff = model._default_next_actions(
            agent_role="rag_answerer",
            handoff={"summary": "help"},
            suspended_workflow="TIN Application",
        )
        assert len(actions_handoff) >= 2
        assert "Resume TIN Application" in actions_handoff[0]
        assert any("officer" in a for a in actions_handoff[1:])

        actions_clarify = model._default_next_actions(
            agent_role="clarification_agent",
            suspended_workflow="PAYE Filing",
        )
        assert len(actions_clarify) == 2
        assert "Resume PAYE Filing" in actions_clarify[0]
        assert "Reply with the missing detail" in actions_clarify[1]

    def test_langgraph_errored_fails_over_to_standard_retrieval(self, clean_flags, monkeypatch, tmp_db):
        from unittest.mock import MagicMock
        from app.agents.graphs.state import AgentGraphState, GraphOutcome

        clean_flags.set("langgraph", True)
        mock_graph = MagicMock()
        mock_state = AgentGraphState(query="What is VAT?")
        mock_state.outcome = GraphOutcome.ERRORED
        mock_state.reply = ""
        mock_graph.run.return_value = mock_state

        monkeypatch.setattr("app.agents.graphs.main_graph.build_main_graph", lambda: mock_graph)

        model = ChatModel()
        # Should gracefully fail over to standard retrieval
        result = model.generate("What is VAT in Uganda?")  # nosemgrep: ura-llm01-raw-user-input-to-llm
        assert result["retrieval_mode"] != "graph_error"
        assert result.get("reply")


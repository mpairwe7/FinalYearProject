"""Unit tests for Gemini Live receptionist integration, tool calling, and pipeline fallback."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app import database as db
from app.flags import flags
from app.receptionist.config import (
    get_gemini_live_model,
    get_gemini_live_voice,
    get_receptionist_engine,
)
from app.receptionist.gemini_live import (
    GeminiLiveReceptionistBrain,
    build_gemini_live_pipeline,
    is_gemini_live_available,
)
from app.receptionist.state import CallRoom, CallState
from app.receptionist.store import init_receptionist_schema, list_turns


class TestReceptionistGeminiLive(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import uuid
        db.init_db()
        init_receptionist_schema()

        self.call_id = f"call_gemini_{uuid.uuid4().hex[:8]}"
        self.state = CallState(
            call_id=self.call_id,
            conversation_id=f"conv_{self.call_id}",
            user_id="taxpayer_gemini",
            mode="ai",
        )
        self.room = CallRoom(call_id=self.call_id, state=self.state)

    def test_config_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_receptionist_engine(), "cascaded")
            self.assertEqual(get_gemini_live_model(), "models/gemini-2.5-flash-native-audio-latest")
            self.assertEqual(get_gemini_live_voice(), "Aoede")

    def test_is_gemini_live_available_without_key(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            self.assertFalse(is_gemini_live_available())

    def test_build_gemini_live_pipeline_requires_api_key(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            ws_mock = MagicMock()
            with self.assertRaises(ValueError) as ctx:
                build_gemini_live_pipeline(self.room, ws_mock)
            self.assertIn("GEMINI_API_KEY", str(ctx.exception))

    async def test_brain_say_greeting(self):
        brain = GeminiLiveReceptionistBrain(
            room=self.room,
            service=MagicMock(),
            chat_model=MagicMock(),
        )
        await brain.say_greeting()
        turns = list_turns(self.call_id)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["speaker"], "assistant")
        self.assertTrue(turns[0]["text"].startswith("Hi, thanks for contacting URA."))

    async def test_rag_tool_invocation(self):
        import sys
        chat_model = MagicMock()
        chat_model.generate.return_value = {
            "reply": "Income tax rate is 30% for corporations.",
            "sources": ["Income Tax Act Cap 340"],
            "faithfulness_score": 0.98,
        }

        mock_pipecat = MagicMock()
        mock_modules = {
            "pipecat": mock_pipecat,
            "pipecat.adapters": mock_pipecat.adapters,
            "pipecat.adapters.schemas": mock_pipecat.adapters.schemas,
            "pipecat.adapters.schemas.function_schema": mock_pipecat.adapters.schemas.function_schema,
            "pipecat.pipeline": mock_pipecat.pipeline,
            "pipecat.pipeline.pipeline": mock_pipecat.pipeline.pipeline,
            "pipecat.pipeline.task": mock_pipecat.pipeline.task,
            "pipecat.processors": mock_pipecat.processors,
            "pipecat.processors.aggregators": mock_pipecat.processors.aggregators,
            "pipecat.processors.aggregators.llm_context": mock_pipecat.processors.aggregators.llm_context,
            "pipecat.processors.aggregators.llm_response_universal": mock_pipecat.processors.aggregators.llm_response_universal,
            "pipecat.processors.frame_processor": mock_pipecat.processors.frame_processor,
            "pipecat.services": mock_pipecat.services,
            "pipecat.services.google": mock_pipecat.services.google,
            "pipecat.services.google.gemini_live": mock_pipecat.services.google.gemini_live,
            "pipecat.services.google.gemini_live.llm": mock_pipecat.services.google.gemini_live.llm,
            "pipecat.transports": mock_pipecat.transports,
            "pipecat.transports.websocket": mock_pipecat.transports.websocket,
            "pipecat.transports.websocket.fastapi": mock_pipecat.transports.websocket.fastapi,
        }

        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "AIza-test-mock"}),  # pragma: allowlist secret
            patch.dict(sys.modules, mock_modules),
        ):
            from app.receptionist.gemini_live import build_gemini_live_pipeline
            task, transport, brain = build_gemini_live_pipeline(
                self.room, MagicMock(), chat_model=chat_model
            )
            self.assertIsNotNone(brain)

    async def test_request_human_officer_ticket_queue_invariant(self):
        # Verify ticket_queue flag invariant
        with patch.object(flags, "is_enabled", return_value=False):
            self.assertFalse(flags.is_enabled("ticket_queue"))


class TestGeminiLiveMultilingual(unittest.IsolatedAsyncioTestCase):
    """Swahili on Gemini Live: instruction, English retrieval, transfers, language tracking."""

    async def asyncSetUp(self):
        import uuid

        import pytest

        pytest.importorskip("pipecat")
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_gml_{uuid.uuid4().hex[:8]}"
        self.state = CallState(call_id=self.call_id, conversation_id=f"conv_{self.call_id}", user_id="u", mode="ai")
        self.room = CallRoom(call_id=self.call_id, state=self.state)
        self.chat_model = MagicMock()
        self.chat_model.generate.return_value = {"reply": "VAT is 18%.", "sources": ["VAT Act"]}
        self.chat_model._build_handoff_packet.return_value = {"priority": "normal"}
        self.chat_model._maybe_create_ticket.return_value = "TICK-GML-1"

    def tools(self, service_ref=None, may_act=None, on_luganda=None):
        from app.receptionist.gemini_live import build_gemini_tools

        built = build_gemini_tools(self.room, self.chat_model, service_ref or {}, may_act, on_luganda)
        return {t.name: t.handler for t in built}

    def test_instruction_for_english_and_swahili(self):
        from app.receptionist.gemini_live import build_gemini_system_instruction

        text = build_gemini_system_instruction(("en", "sw"))
        self.assertIn("The call starts in English", text)
        self.assertIn("English or Swahili", text)
        self.assertIn("translated into English", text)
        self.assertIn("exactly as the tool returns it", text)
        self.assertIn("reply briefly in English", text)
        self.assertIn("caller_language", text)
        self.assertIn("word for word", text)
        self.assertIn("query_ura_tax_knowledge", text)

    def test_instruction_hands_luganda_over_when_it_can(self):
        from app.receptionist.gemini_live import build_gemini_system_instruction

        text = build_gemini_system_instruction(("en", "sw"), luganda_handover=True)
        self.assertIn("call `hand_over_to_luganda`", text)
        self.assertIn("Never tell a caller you cannot speak Luganda", text)
        self.assertIn("never request an officer because of the caller's language", text)
        self.assertNotIn("hand_over_to_luganda", build_gemini_system_instruction(("en", "sw")))

    async def test_the_luganda_tool_moves_the_call(self):
        from types import SimpleNamespace

        heard: list[bool] = []

        def on_luganda() -> bool:
            heard.append(True)
            return True

        tools = self.tools(on_luganda=on_luganda)
        params = SimpleNamespace(arguments={"heard": "Gyebale ko nnyabo"}, result_callback=AsyncMock())
        await tools["hand_over_to_luganda"](params)
        self.assertEqual(heard, [True])
        self.assertTrue(params.result_callback.await_args.args[0]["handed_over"])
        self.assertNotIn("hand_over_to_luganda", self.tools())  # single-engine calls have no Luganda colleague

    async def test_the_luganda_tool_respects_an_on_screen_choice(self):
        from types import SimpleNamespace

        params = SimpleNamespace(arguments={}, result_callback=AsyncMock())
        await self.tools(on_luganda=lambda: False)["hand_over_to_luganda"](params)
        self.assertFalse(params.result_callback.await_args.args[0]["handed_over"])

    def test_gemini_hears_the_caller_start_talking_readily(self):
        from app.receptionist import config

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_LIVE_START_SENSITIVITY", None)
            self.assertEqual(config.get_gemini_vad_start_sensitivity(), "high")
        with patch.dict(os.environ, {"GEMINI_LIVE_START_SENSITIVITY": "LOW", "GEMINI_LIVE_SILENCE_MS": "500"}):
            self.assertEqual(config.get_gemini_vad_start_sensitivity(), "low")
            self.assertEqual(config.get_gemini_vad_silence_ms(), 500)
        with patch.dict(os.environ, {"GEMINI_LIVE_START_SENSITIVITY": "loudest"}):
            self.assertEqual(config.get_gemini_vad_start_sensitivity(), "high")

    def test_instruction_for_english_only(self):
        from app.receptionist.gemini_live import build_gemini_system_instruction

        text = build_gemini_system_instruction(("en",))
        self.assertIn("Always speak English.", text)
        self.assertNotIn("Swahili", text)

    def test_languages_follow_the_engine_table(self):
        from app.receptionist.gemini_live import gemini_languages

        with patch.dict(os.environ, {"RECEPTIONIST_ENGINE_BY_LANGUAGE": "en:gemini_live,sw:cascaded"}):
            self.assertEqual(gemini_languages(), ("en",))
        with patch.dict(os.environ, {"RECEPTIONIST_ENGINE_BY_LANGUAGE": ""}):
            self.assertEqual(gemini_languages(), ("en", "sw"))

    async def test_retrieval_runs_in_english_and_is_logged_in_the_caller_s_language(self):
        from types import SimpleNamespace

        from app.receptionist import gemini_live as gl

        self.state.locale = "sw"
        params = SimpleNamespace(arguments={"query": "What is the VAT rate?"}, result_callback=AsyncMock())
        with patch.object(gl.db, "log_conversation") as log:
            await self.tools()["query_ura_tax_knowledge"](params)
        self.assertEqual(self.chat_model.generate.call_args.kwargs["locale"], "en")
        self.assertEqual(log.call_args.kwargs["locale"], "sw")
        self.assertEqual(log.call_args.kwargs["sources"], '["VAT Act"]')  # a JSON string, not a list
        result = params.result_callback.await_args.args[0]
        self.assertEqual(result["official_answer"], "VAT is 18%.")
        self.assertEqual(result["caller_language"], "Swahili")  # what our language id heard

    async def test_transfer_creates_a_real_ticket_and_tells_the_officers(self):
        from types import SimpleNamespace

        from app.receptionist import transfer as transfer_mod

        self.state.locale = "sw"
        self.state.last_caller_text = "Naomba msaada wa binadamu"
        service = MagicMock()
        service.push_frame = AsyncMock()
        params = SimpleNamespace(arguments={"reason": "caller_requested"}, result_callback=AsyncMock())
        with patch.object(flags, "is_enabled", side_effect=lambda name, **_kw: name == "ticket_queue"), \
                patch.object(transfer_mod.hub, "publish_lobby") as lobby:
            await self.tools({"service": service})["request_human_officer"](params)
        self.state.transfer_timer_task.cancel()
        kwargs = self.chat_model._maybe_create_ticket.call_args.kwargs
        self.assertEqual(kwargs["user_query"], "Naomba msaada wa binadamu")
        self.assertEqual(kwargs["modality"], "voice")
        self.assertEqual(kwargs["handoff"]["language"], "sw")
        self.assertEqual((self.state.mode, self.state.ticket_id), ("transferring", "TICK-GML-1"))
        lobby.assert_called_once()
        self.assertEqual(lobby.call_args.args[0], "call.transfer_requested")
        self.assertEqual(lobby.call_args.args[1]["language"], "sw")
        self.assertEqual(service.push_frame.await_args.args[0].message["status"], "transferring")
        self.assertTrue(params.result_callback.await_args.args[0]["transferred"])

    async def test_transfer_is_refused_without_the_ticket_queue(self):
        from types import SimpleNamespace

        params = SimpleNamespace(arguments={"reason": "caller_requested"}, result_callback=AsyncMock())
        with patch.object(flags, "is_enabled", return_value=False):
            await self.tools()["request_human_officer"](params)
        self.chat_model._maybe_create_ticket.assert_not_called()
        self.assertFalse(params.result_callback.await_args.args[0]["transferred"])
        self.assertEqual(self.state.mode, "ai")

    async def test_a_call_leaving_gemini_ignores_its_tools(self):
        # A switch to Luganda waits for the caller's turn to end; Gemini still
        # hears the caller meanwhile and once opened a transfer "because the
        # caller speaks Luganda". Neither a ticket nor a logged answer may come of it.
        from types import SimpleNamespace

        from app.receptionist import gemini_live as gl

        tools = self.tools(may_act=lambda: False)
        transfer = SimpleNamespace(arguments={"reason": "Caller speaking Luganda"}, result_callback=AsyncMock())
        ask = SimpleNamespace(arguments={"query": "VAT rate"}, result_callback=AsyncMock())
        with patch.object(flags, "is_enabled", return_value=True), \
                patch.object(gl.db, "log_conversation") as log:
            await tools["request_human_officer"](transfer)
            await tools["query_ura_tax_knowledge"](ask)
        self.chat_model._maybe_create_ticket.assert_not_called()
        self.chat_model.generate.assert_not_called()
        log.assert_not_called()
        self.assertEqual(self.state.mode, "ai")
        self.assertFalse(transfer.result_callback.await_args.args[0]["transferred"])
        self.assertEqual(ask.result_callback.await_args.args[0]["official_answer"], "")

    async def test_the_officer_button_becomes_a_request_gemini_acts_on(self):
        from app.receptionist.gemini_live import OfficerRequestBridge
        from app.receptionist.serializer import RequestOfficerFrame
        from pipecat.frames.frames import InputTextRawFrame

        bridge = OfficerRequestBridge()
        pushed = []

        async def push_frame(frame, direction=None):
            pushed.append(frame)

        bridge.push_frame = push_frame
        await bridge.process_frame(RequestOfficerFrame())
        self.assertEqual(len(pushed), 1)
        self.assertIsInstance(pushed[0], InputTextRawFrame)
        self.assertIn("request_human_officer", pushed[0].text)

    async def test_caller_turns_are_recorded_from_gemini_s_upstream_transcription(self):
        from app.receptionist.gemini_live import GeminiCallerTap
        from pipecat.frames.frames import TranscriptionFrame
        from pipecat.processors.frame_processor import FrameDirection

        tap = GeminiCallerTap(room=self.room)
        tap.push_frame = AsyncMock()
        await tap.process_frame(
            TranscriptionFrame(text="What is the VAT rate?", user_id="u", timestamp="0"), FrameDirection.UPSTREAM
        )
        turns = list_turns(self.call_id)
        self.assertEqual([(t["speaker"], t["text"]) for t in turns], [("caller", "What is the VAT rate?")])
        self.assertEqual(self.state.last_caller_text, "What is the VAT rate?")
        self.assertEqual(tap.push_frame.await_args.args[1], FrameDirection.UPSTREAM)  # passed on unchanged

    async def test_a_language_switch_drops_what_gemini_said_while_held(self):
        # Gemini started answering the Luganda question before the switch; that
        # reply was discarded, and its words must not open Gemini's next caption.
        from app.receptionist.gemini_live import GeminiLiveTranscriptTap
        from pipecat.frames.frames import InterruptionFrame, LLMFullResponseEndFrame, TTSTextFrame
        from pipecat.processors.frame_processor import FrameDirection

        tap = GeminiLiveTranscriptTap(room=self.room)
        passed = []
        tap.on_switch_interrupt = lambda: passed.append(True)
        tap.push_frame = AsyncMock()
        self.state.engine = "gemini_live"

        await tap.process_frame(TTSTextFrame(text="I see you are speaking Luganda.", aggregated_by="sentence"))
        switch = InterruptionFrame()
        switch.metadata["language_switch"] = True
        await tap.process_frame(switch, FrameDirection.DOWNSTREAM)
        await tap.process_frame(TTSTextFrame(text="To register for a TIN, visit ura.go.ug.", aggregated_by="sentence"))
        await tap.process_frame(LLMFullResponseEndFrame())

        self.assertEqual(passed, [True])
        turns = [t["text"] for t in list_turns(self.call_id) if t["speaker"] == "assistant"]
        self.assertEqual(turns, ["To register for a TIN, visit ura.go.ug."])

    async def test_single_engine_calls_follow_swahili_from_the_transcript(self):
        from app.receptionist.gemini_live import GeminiCallerTap
        from pipecat.frames.frames import OutputTransportMessageUrgentFrame, TranscriptionFrame
        from pipecat.processors.frame_processor import FrameDirection

        tap = GeminiCallerTap(room=self.room, track_language=True)
        pushed = []

        async def push_frame(frame, direction=None):
            pushed.append(frame)

        tap.push_frame = push_frame
        await tap.process_frame(
            TranscriptionFrame(text="Naomba kujua kodi ya mapato", user_id="u", timestamp="0"), FrameDirection.UPSTREAM
        )
        self.assertEqual(self.state.locale, "sw")
        messages = [f.message for f in pushed if isinstance(f, OutputTransportMessageUrgentFrame)]
        self.assertIn({"type": "language", "language": "sw", "source": "auto"}, messages)
        kinds = [t["kind"] for t in list_turns(self.call_id)]
        self.assertIn("language", kinds)

    async def test_a_multilingual_call_on_the_other_engine_is_not_logged_here(self):
        from app.receptionist.gemini_live import GeminiCallerTap
        from pipecat.frames.frames import TranscriptionFrame
        from pipecat.processors.frame_processor import FrameDirection

        self.state.engine = "cascaded"
        tap = GeminiCallerTap(room=self.room)
        tap.push_frame = AsyncMock()
        await tap.process_frame(
            TranscriptionFrame(text="Omusolo gwa VAT guli ki?", user_id="u", timestamp="0"), FrameDirection.UPSTREAM
        )
        self.assertEqual(list_turns(self.call_id), [])


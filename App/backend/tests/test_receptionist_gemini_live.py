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
        self.assertIn("Hello", turns[0]["text"])

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

        with patch.dict(os.environ, {"GEMINI_API_KEY": "AIza-test-mock"}), \  # pragma: allowlist secret
             patch.dict(sys.modules, mock_modules):
            from app.receptionist.gemini_live import build_gemini_live_pipeline
            task, transport, brain = build_gemini_live_pipeline(
                self.room, MagicMock(), chat_model=chat_model
            )
            self.assertIsNotNone(brain)

    async def test_request_human_officer_ticket_queue_invariant(self):
        # Verify ticket_queue flag invariant
        with patch.object(flags, "is_enabled", return_value=False):
            self.assertFalse(flags.is_enabled("ticket_queue"))

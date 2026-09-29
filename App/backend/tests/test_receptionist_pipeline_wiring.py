"""The call pipeline as built: the wiring the unit tests of each part cannot see."""

from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("pipecat")

from app import database as db  # noqa: E402
from app.receptionist.state import CallRoom, CallState  # noqa: E402
from app.receptionist.store import create_call, init_receptionist_schema  # noqa: E402


def _room() -> CallRoom:
    call_id = f"call_wiring_{uuid.uuid4().hex[:8]}"
    create_call(call_id, conversation_id=f"conv_{call_id}")
    return CallRoom(call_id=call_id, state=CallState(call_id=call_id, conversation_id=f"conv_{call_id}"))


class CascadedBranchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()

    async def test_caller_silence_reaches_the_brain(self):
        from app.receptionist.pipeline import build_cascaded_branch

        speech = MagicMock()
        with patch.dict(os.environ, {"RECEPTIONIST_IDLE_REPROMPT_S": "9"}):
            processors, _assistant, brain = build_cascaded_branch(_room(), speech, MagicMock())
        user_aggregator = next(p for p in processors if type(p).__name__ == "LLMUserAggregator")
        self.assertEqual(user_aggregator._params.user_idle_timeout, 9.0)

        brain.on_caller_idle = AsyncMock()
        # What Pipecat's UserIdleController does when the timer runs out.
        for handler in user_aggregator._event_handlers["on_user_turn_idle"].handlers:
            await handler(user_aggregator)
        brain.on_caller_idle.assert_awaited_once_with()
        self.assertIs(brain.speech_model, speech)  # the goodbye is spoken with it


class MultilingualPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()

    async def test_one_local_engine_with_the_router_wired_to_its_brain(self):
        from app.receptionist.multilingual import build_multilingual_pipeline
        from app.receptionist.router import LanguageRouter

        room = _room()
        room.state.locale = "sw"  # the chat's language: a hint only
        with patch.dict(os.environ, {"RECEPTIONIST_MEDIA_TRANSPORT": "websocket"}):
            _task, _transport, brain = build_multilingual_pipeline(room, MagicMock(), MagicMock(), MagicMock())
        self.assertEqual((room.state.locale, room.state.preferred_locale), ("en", "sw"))
        router = brain.turn_claimed.__self__
        self.assertIsInstance(router, LanguageRouter)
        self.assertIs(router.brain, brain)
        self.assertEqual(brain.on_switch_interrupt, router.switch_passed)
        self.assertIsNotNone(router.interrupt)
        self.assertIsNotNone(router.barge_in)


if __name__ == "__main__":
    unittest.main()

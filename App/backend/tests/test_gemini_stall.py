"""Gemini silence hands the turn to the local cascade. No Pipecat, no network."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.receptionist.gemini_stall import GeminiStallGuard
from app.receptionist.lexicon import normalize_luganda_tax_query, repair_asr_entities
from app.receptionist.router import EngineSelector, LanguageRouter


class RepairBeforeRetrieval(unittest.TestCase):
    def test_ttiimu_and_mu_era_become_tin_and_ura(self):
        text = repair_asr_entities("okufuna ttiimu yange okuva mu era")
        self.assertIn("TIN", text)
        self.assertIn("mu URA", text)
        self.assertNotIn("ttiimu", text)
        self.assertNotIn("mu era", text)
        heard = repair_asr_entities("okufuna ttiimu yange okuva mu ora")
        self.assertIn("TIN", heard)
        self.assertIn("mu URA", heard)
        self.assertNotIn("mu ora", heard)
        # The confirm gate also treats the preposition itself as the frame.
        bare = repair_asr_entities("okufuna ttiimu okuva ora")
        self.assertIn("okuva URA", bare)
        self.assertNotIn("ora", bare)

    def test_vat_rate_idiom_survives_normalization(self):
        normalized = normalize_luganda_tax_query("Vat yange ntya okugisasula era ebitundu bimeeka")
        self.assertIn("VAT", normalized)
        self.assertIn("what percentage", normalized)
        # "era" as "and" stays. It is not URA.
        self.assertIn("era", normalized)


class StallGuard(unittest.IsolatedAsyncioTestCase):
    async def test_hearing_audio_cancels_the_fallback(self):
        called: list[str] = []

        async def on_stall(text: str) -> None:
            called.append(text)

        guard = GeminiStallGuard(0.05, on_stall)
        guard.arm("What is the VAT rate?")
        guard.heard()
        await asyncio.sleep(0.08)
        self.assertEqual(called, [])

    async def test_silence_calls_the_fallback_once(self):
        called: list[str] = []

        async def on_stall(text: str) -> None:
            called.append(text)

        guard = GeminiStallGuard(0.02, on_stall)
        guard.arm("Habari. Ninawezaje kujisajili?")
        await asyncio.sleep(0.06)
        self.assertEqual(called, ["Habari. Ninawezaje kujisajili?"])


class LocalFallback(unittest.IsolatedAsyncioTestCase):
    async def test_stall_moves_the_call_onto_the_cascade(self):
        room = SimpleNamespace(
            call_id="call_test",
            state=SimpleNamespace(mode="ai", generation_id=0, engine="gemini_live"),
        )
        brain = SimpleNamespace(handle_external_question=AsyncMock())
        hold = SimpleNamespace(discard=AsyncMock())
        router = LanguageRouter(
            room,
            MagicMock(),
            EngineSelector("gemini_live"),
            {"en": "gemini_live", "lg": "cascaded"},
            speech_model=None,
            brain=brain,
            hold_gate=hold,
        )
        await router.fallback_after_gemini_stall("What is the standard VAT rate?")
        self.assertEqual(router.selector.active, "cascaded")
        self.assertEqual(room.state.engine, "cascaded")
        self.assertEqual(room.state.generation_id, 1)
        hold.discard.assert_awaited()
        brain.handle_external_question.assert_awaited()

    async def test_no_cascade_leaves_the_engine_alone(self):
        room = SimpleNamespace(
            call_id="call_test",
            state=SimpleNamespace(mode="ai", generation_id=0, engine="gemini_live"),
        )
        router = LanguageRouter(
            room,
            MagicMock(),
            EngineSelector("gemini_live"),
            {"en": "gemini_live"},
            speech_model=None,
        )
        await router.fallback_after_gemini_stall("hello")
        self.assertEqual(router.selector.active, "gemini_live")

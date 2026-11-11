"""Starts safely, fails visibly: the receptionist's startup, gates and readiness.

Regressions from phase 3 of the November 2026 call-receptionist review
(docs/plans/receptionist-assurance-2026-11-11.md, ADR-0041).
"""

from __future__ import annotations

import asyncio
import time
import unittest
import uuid
from unittest.mock import patch

import pytest

from app import database as db
from app import receptionist
from app.receptionist import config, livekit, ws
from app.receptionist.store import create_call, get_call, init_receptionist_schema, update_call

PRODUCTION_CALLS = {
    "FLAG_VOICE_RECEPTIONIST": "true",
    "LIVEKIT_URL": "wss://rtc.example.test",
    "LIVEKIT_API_KEY": "server-key-for-tests",
    "LIVEKIT_API_SECRET": "s" * 40,
    "RECEPTIONIST_MEDIA_TRANSPORT": "livekit",
    "VOICE_RECEPTIONIST_REPLICAS": "1",
    "VOICE_RECEPTIONIST_SINGLE_REPLICA_ACK": "true",
}
WORKER_VARIABLES = ("WORKERS", "UVICORN_WORKERS", "WEB_CONCURRENCY")


@pytest.fixture
def production_calls(monkeypatch):
    for name, value in PRODUCTION_CALLS.items():
        monkeypatch.setenv(name, value)
    for name in WORKER_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# ---------------------------------------------------------------------------
# Settings that are set but unusable
# ---------------------------------------------------------------------------

DURATIONS = (
    ("RECEPTIONIST_TRANSFER_TIMEOUT_S", config.get_transfer_timeout_s, 90.0),
    ("RECEPTIONIST_CLAIM_TIMEOUT_S", config.get_claim_timeout_s, 20.0),
    ("RECEPTIONIST_OFFICER_RECONNECT_GRACE_S", config.get_officer_reconnect_grace_s, 30.0),
    ("RECEPTIONIST_PRESENCE_TTL_S", config.get_presence_ttl_s, 60.0),
    ("RECEPTIONIST_TURN_TIMEOUT_S", config.get_turn_timeout_s, 1.0),
    ("RECEPTIONIST_HOLD_UPDATE_S", config.get_hold_update_s, 30.0),
)


@pytest.mark.parametrize("name,getter,default", DURATIONS)
@pytest.mark.parametrize("value", ["nan", "inf", "-30", "soon", "1e12"])
def test_an_unusable_duration_stops_validation_and_is_never_used(monkeypatch, name, getter, default, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(config.ReceptionistConfigError) as refused:
        config.validate()

    assert name in str(refused.value)
    # A call already running reads the default, not the bad value.
    assert getter() == default


def test_a_transfer_timeout_of_zero_is_refused(monkeypatch):
    # Zero ended the wait at once: every transfer became "officers are busy".
    monkeypatch.setenv("RECEPTIONIST_TRANSFER_TIMEOUT_S", "0")

    with pytest.raises(config.ReceptionistConfigError):
        config.validate()
    assert config.get_transfer_timeout_s() == 90.0


def test_zero_still_switches_hold_updates_off(monkeypatch):
    monkeypatch.setenv("RECEPTIONIST_HOLD_UPDATE_S", "0")

    config.validate()

    assert config.get_hold_update_s() == 0.0


def test_usable_durations_are_used(monkeypatch):
    monkeypatch.setenv("RECEPTIONIST_TRANSFER_TIMEOUT_S", "45")
    monkeypatch.setenv("RECEPTIONIST_CLAIM_TIMEOUT_S", "12.5")

    config.validate()

    assert (config.get_transfer_timeout_s(), config.get_claim_timeout_s()) == (45.0, 12.5)


def test_a_clarify_limit_below_zero_is_refused(monkeypatch):
    monkeypatch.setenv("RECEPTIONIST_MAX_CLARIFY_ATTEMPTS", "-3")

    with pytest.raises(config.ReceptionistConfigError):
        config.validate()
    assert config.get_max_clarify_attempts() == 2


# ---------------------------------------------------------------------------
# One process owns every call
# ---------------------------------------------------------------------------


def worker_errors() -> list[str]:
    return [e for e in livekit.production_errors() if "WORKERS" in e or "WEB_CONCURRENCY" in e]


def test_the_gate_reads_the_variable_the_image_starts_uvicorn_with(production_calls):
    # The runbook's WORKERS=1 passed the gate while supervisord started two
    # workers from UVICORN_WORKERS: half of all handoffs reached the wrong process.
    production_calls.setenv("WORKERS", "1")
    production_calls.setenv("UVICORN_WORKERS", "2")

    errors = worker_errors()

    assert len(errors) == 1
    assert "UVICORN_WORKERS" in errors[0]


@pytest.mark.parametrize("name", WORKER_VARIABLES)
def test_one_worker_named_by_any_of_them_passes(production_calls, name):
    production_calls.setenv(name, "1")

    assert worker_errors() == []


def test_no_worker_count_at_all_is_refused(production_calls):
    assert any("WORKERS=1" in e for e in worker_errors())


def test_every_variable_that_is_set_must_say_one(production_calls):
    production_calls.setenv("WORKERS", "1")
    production_calls.setenv("WEB_CONCURRENCY", "4")

    assert any("WEB_CONCURRENCY" in e for e in worker_errors())


# ---------------------------------------------------------------------------
# A flag switched on at runtime is held to the same gate
# ---------------------------------------------------------------------------


def test_the_local_engine_gate_applies_to_a_flag_switched_on_by_override(monkeypatch):
    monkeypatch.setenv("FLAG_VOICE_RECEPTIONIST", "false")
    monkeypatch.setenv("SPEECH_ENABLED", "false")
    monkeypatch.delenv("ORPHEUS_TTS_URL", raising=False)

    assert config.local_engine_errors() == []  # the environment says calls are off
    assert config.local_engine_errors(enabled_override=True)  # an admin override says on


def test_calls_stay_closed_in_production_when_the_engine_is_not_configured(production_calls):
    production_calls.setenv("APP_ENV", "production")
    production_calls.setenv("WORKERS", "1")
    production_calls.setenv("SPEECH_ENABLED", "false")
    production_calls.delenv("ORPHEUS_TTS_URL", raising=False)

    with patch.object(ws.flags, "is_enabled", return_value=True):
        assert ws._voice_receptionist_enabled() is False


# ---------------------------------------------------------------------------
# A receptionist that cannot start is unavailable, and says so
# ---------------------------------------------------------------------------


class AvailabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        receptionist.mark_available()
        self.addCleanup(receptionist.mark_available)

    def test_calls_are_refused_while_the_receptionist_is_unavailable(self):
        with patch.object(ws.flags, "is_enabled", return_value=True):
            self.assertTrue(ws._voice_receptionist_enabled())
            receptionist.mark_unavailable("Pipecat is not installed")
            self.assertFalse(ws._voice_receptionist_enabled())

    def test_its_status_says_why(self):
        with patch("app.flags.flags.is_enabled", return_value=True):
            self.assertEqual(receptionist.status(), "ready")
            receptionist.mark_unavailable("Pipecat is not installed")
            self.assertEqual(receptionist.status(), "unavailable: Pipecat is not installed")
        with patch("app.flags.flags.is_enabled", return_value=False):
            self.assertEqual(receptionist.status(), "off")

    def start(self, **patches):
        """Run the startup with a working stack, except for *patches*."""
        defaults = {
            "is_available": patch.object(receptionist, "is_available", return_value=True),
            "validate": patch("app.receptionist.config.validate"),
            "schema": patch("app.receptionist.store.init_receptionist_schema"),
            "sweep": patch("app.receptionist.store.end_stale_calls", return_value=0),
        }
        defaults.update(patches)
        with defaults["is_available"], defaults["validate"], defaults["schema"], defaults["sweep"] as sweep:
            reason = receptionist.start(speech_model=object())
        return reason, sweep

    def test_a_working_stack_starts(self):
        reason, sweep = self.start()

        self.assertEqual(reason, "")
        self.assertEqual(receptionist.unavailable_reason(), "")
        sweep.assert_called_once()

    def test_a_missing_pipeline_does_not_raise(self):
        reason, _ = self.start(is_available=patch.object(receptionist, "is_available", return_value=False))

        self.assertIn("Pipecat", reason)
        self.assertEqual(receptionist.unavailable_reason(), reason)

    def test_an_unusable_setting_does_not_raise(self):
        bad = config.ReceptionistConfigError("RECEPTIONIST_TRANSFER_TIMEOUT_S must be a positive number of seconds")
        reason, _ = self.start(validate=patch("app.receptionist.config.validate", side_effect=bad))

        self.assertIn("RECEPTIONIST_TRANSFER_TIMEOUT_S", reason)

    def test_a_schema_that_cannot_be_created_makes_calls_unavailable(self):
        # It used to be logged and ignored: every call then failed at its first write.
        broken = patch("app.receptionist.store.init_receptionist_schema", side_effect=RuntimeError("disk full"))
        reason, _ = self.start(schema=broken)

        self.assertIn("call tables", reason)

    def test_no_speech_model_makes_calls_unavailable(self):
        with (
            patch.object(receptionist, "is_available", return_value=True),
            patch("app.receptionist.config.validate"),
            patch("app.receptionist.store.init_receptionist_schema"),
            patch("app.receptionist.store.end_stale_calls", return_value=0),
        ):
            reason = receptionist.start(speech_model=None)

        self.assertIn("speech", reason)
        with patch.object(ws.flags, "is_enabled", return_value=True):
            self.assertFalse(ws._voice_receptionist_enabled())

    def test_a_stopped_application_leaves_nothing_behind_for_the_next_start(self):
        receptionist.mark_unavailable("Pipecat is not installed")

        receptionist.reset()

        self.assertEqual(receptionist.unavailable_reason(), "")

    def test_the_readiness_probe_reports_it(self):
        from starlette.testclient import TestClient

        from app.main import app, get_model
        from app.service import ChatModel

        model = ChatModel()
        app.dependency_overrides[get_model] = lambda: model
        self.addCleanup(app.dependency_overrides.pop, get_model, None)
        receptionist.mark_unavailable("Pipecat is not installed")
        with patch("app.flags.flags.is_enabled", side_effect=lambda name, **_kw: name == "voice_receptionist"):
            body = TestClient(app).get("/ready").json()

        self.assertEqual(body["capabilities"]["voice_receptionist"], "unavailable: Pipecat is not installed")


class SchemaInitTests(unittest.TestCase):
    def test_strict_initialisation_raises_what_it_used_to_swallow(self):
        with patch("app.receptionist.store.db.execute_script", side_effect=RuntimeError("disk full")):
            init_receptionist_schema()  # lenient, as every existing caller expects
            with self.assertRaises(RuntimeError):
                init_receptionist_schema(strict=True)


# ---------------------------------------------------------------------------
# Rows a dead process left behind
# ---------------------------------------------------------------------------


class StaleCallTests(unittest.TestCase):
    def setUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        self.now = time.time()

    def call(self, status: str, age_s: float) -> str:
        call_id = f"call_stale_{uuid.uuid4().hex[:8]}"
        create_call(call_id, conversation_id=f"conv_{call_id}", status=status, started_at=self.now - age_s)
        return call_id

    def sweep(self, **kwargs) -> int:
        from app.receptionist.store import end_stale_calls

        return end_stale_calls(now=self.now, ceiling_s=900.0, **kwargs)

    def test_a_call_older_than_the_ceiling_cannot_be_live(self):
        waiting = self.call("transferring", age_s=3600)
        with_ai = self.call("ai", age_s=3600)

        self.assertGreaterEqual(self.sweep(), 2)

        row = get_call(waiting)
        self.assertEqual((row["status"], row["end_reason"]), ("ended", "process_lost"))
        self.assertEqual((row["needs_callback"], row["callback_reason"]), (1, "service_interrupted"))
        self.assertEqual(get_call(with_ai)["needs_callback"], 0)

    def test_a_call_within_the_ceiling_is_left_alone(self):
        young = self.call("bridged", age_s=60)

        self.sweep()

        self.assertEqual(get_call(young)["status"], "bridged")

    def test_a_single_process_that_has_just_started_owns_no_calls(self):
        young = self.call("bridged", age_s=60)

        self.sweep(everything=True)

        row = get_call(young)
        self.assertEqual((row["status"], row["end_reason"], row["needs_callback"]), ("ended", "process_lost", 1))

    def test_an_ended_call_keeps_the_reason_it_ended_with(self):
        ended = self.call("ai", age_s=3600)
        update_call(ended, status="ended", end_reason="caller_hangup", ended_at=self.now - 3500)

        self.sweep(everything=True)

        self.assertEqual(get_call(ended)["end_reason"], "caller_hangup")

    def test_the_officer_of_a_lost_call_is_not_left_on_it(self):
        from app.receptionist import presence

        officer = f"okello-{uuid.uuid4().hex[:6]}"
        lost = self.call("bridged", age_s=3600)
        presence.upsert_presence(officer, status="available")
        presence.set_on_call(officer, lost)
        self.addCleanup(presence.upsert_presence, officer, status="offline")

        self.sweep()

        after = presence.get_presence(officer)
        self.assertEqual((after["raw_status"], after["current_call_id"]), ("available", ""))


# ---------------------------------------------------------------------------
# A call that cannot hear or speak says so, and goes to a person
# ---------------------------------------------------------------------------


def counter(name: str, **labels: str) -> float:
    """The current value of one exported counter series (0 when it does not exist yet)."""
    from app.analytics import metrics

    pairs = ",".join(f'{key}="{value}"' for key, value in sorted(labels.items()))
    key = f"{name}{{{pairs}}}" if pairs else name
    return float(metrics.snapshot()["counters"].get(key, 0))


class BrokenSpeech:
    """A speech model whose recognizer and voice both raise."""

    def transcribe(self, *_args, **_kwargs):
        raise RuntimeError("the recognizer is down")

    def synthesize(self, *_args, **_kwargs):
        raise RuntimeError("the voice is down")


class CallCase(unittest.IsolatedAsyncioTestCase):
    """A call with the AI, on the real store, with a brain whose output is captured."""

    async def asyncSetUp(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from app.receptionist.brain import UraReceptionistBrain
        from app.receptionist.state import CallRoom, CallState

        staffed = patch("app.receptionist.transfer.is_staff_available", return_value=True)
        staffed.start()
        self.addCleanup(staffed.stop)
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_speech_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}", status="ai")
        self.state = CallState(call_id=self.call_id, conversation_id=f"conv_{self.call_id}", mode="ai")
        self.room = CallRoom(call_id=self.call_id, state=self.state)
        self.brain = UraReceptionistBrain(room=self.room, chat_model=MagicMock(), speech_model=MagicMock())
        self.brain.push_frame = AsyncMock()
        self.brain._transfer = AsyncMock()
        self.room.brain = self.brain

    def said(self) -> list[str]:
        from app.receptionist.store import list_turns

        return [t["text"] for t in list_turns(self.call_id) if t["speaker"] == "assistant"]


class UnheardCallerTests(CallCase):
    async def test_a_caller_who_was_not_heard_is_asked_to_say_it_again(self):
        from app.receptionist.phrases import phrase

        await self.brain.on_speech_failure("stt")

        self.assertEqual(self.said(), [phrase("clarify_repeat", "en")])
        self.brain._transfer.assert_not_awaited()

    async def test_a_second_failure_in_a_row_goes_to_a_person(self):
        await self.brain.on_speech_failure("stt")
        await self.brain.on_speech_failure("stt")

        self.brain._transfer.assert_awaited_once_with("system_error")

    async def test_the_call_is_handed_over_once_however_many_failures_follow(self):
        for _ in range(5):
            await self.brain.on_speech_failure("stt")

        self.brain._transfer.assert_awaited_once()

    async def test_a_voice_that_fails_twice_goes_to_a_person_without_asking_for_a_repeat(self):
        await self.brain.on_speech_failure("tts")
        self.assertEqual(self.said(), [])  # nothing more can be said aloud
        await self.brain.on_speech_failure("tts")

        self.brain._transfer.assert_awaited_once_with("system_error")

    async def test_an_officer_s_call_is_left_alone(self):
        self.state.mode = "bridged"

        await self.brain.on_speech_failure("stt")
        await self.brain.on_speech_failure("stt")

        self.assertEqual(self.said(), [])
        self.brain._transfer.assert_not_awaited()

    async def test_silence_after_an_unheard_utterance_is_not_recorded_as_the_caller_s(self):
        from unittest.mock import AsyncMock

        hang_up = AsyncMock()
        with (
            patch("app.receptionist.desk.say_to_caller", AsyncMock(return_value=0.0)),
            patch("app.receptionist.desk.hang_up_caller", hang_up),
            patch.dict("os.environ", {"RECEPTIONIST_IDLE_REPROMPTS": "0"}),
        ):
            await self.brain.on_speech_failure("stt")
            await self.brain.on_caller_idle()

        hang_up.assert_awaited_once_with(self.room, "speech_failed")

    async def test_a_caller_who_is_heard_again_starts_afresh(self):
        await self.brain.on_speech_failure("stt")
        self.assertTrue(self.state.unheard)

        self.brain.heard_caller()

        self.assertFalse(self.state.unheard)
        await self.brain.on_speech_failure("stt")
        self.brain._transfer.assert_not_awaited()


class SpeechServiceFailureTests(CallCase):
    async def asyncSetUp(self) -> None:
        from unittest.mock import AsyncMock

        await super().asyncSetUp()
        self.brain.on_speech_failure = AsyncMock()

    async def transcribe(self, speech) -> list:
        from app.receptionist.stt import UraWhisperSTT

        stt = UraWhisperSTT(speech_model=speech, room=self.room)
        return [frame async for frame in stt.run_stt(b"\x01\x00" * 1600)]

    async def test_a_recognizer_that_raises_is_counted_and_reported_to_the_brain(self):
        before = counter("receptionist_call_failures_total", stage="stt")

        frames = await self.transcribe(BrokenSpeech())

        self.assertEqual(frames, [])
        self.assertEqual(counter("receptionist_call_failures_total", stage="stt") - before, 1)
        self.brain.on_speech_failure.assert_awaited_once_with("stt")

    async def test_a_recognizer_that_works_reports_nothing(self):
        from app.speech_service import TranscribeResult

        class Working:
            def transcribe(self, *_args, **_kwargs):
                return TranscribeResult(text="How do I file a return?", language="en", duration_s=1.0, latency_s=0.1)

        frames = await self.transcribe(Working())

        self.assertEqual([f.text for f in frames], ["How do I file a return?"])
        self.brain.on_speech_failure.assert_not_awaited()

    async def test_a_brain_that_cannot_take_the_report_does_not_break_the_recognizer(self):
        self.brain.on_speech_failure.side_effect = RuntimeError("brain is gone")

        self.assertEqual(await self.transcribe(BrokenSpeech()), [])

    async def speak(self, tts, text: str = "Your return is due on the fifteenth.") -> list:
        return [frame async for frame in tts._voice(text, "ctx")]

    async def test_a_voice_that_raises_is_counted_and_reported_to_the_brain(self):
        from app.receptionist.tts import UraSpeechTTS

        before = counter("receptionist_call_failures_total", stage="tts")
        tts = UraSpeechTTS(speech_model=BrokenSpeech(), room=self.room)
        with patch.object(UraSpeechTTS, "_should_stream", return_value=False):
            self.assertEqual(await self.speak(tts), [])

        self.assertEqual(counter("receptionist_call_failures_total", stage="tts") - before, 1)
        self.brain.on_speech_failure.assert_awaited_once_with("tts")

    async def test_a_voice_that_returns_no_audio_has_failed_too(self):
        from app.receptionist.tts import UraSpeechTTS

        class Silent:
            def synthesize(self, *_args, **_kwargs):
                return None

        tts = UraSpeechTTS(speech_model=Silent(), room=self.room)
        with patch.object(UraSpeechTTS, "_should_stream", return_value=False):
            await self.speak(tts)

        self.brain.on_speech_failure.assert_awaited_once_with("tts")

    async def test_a_line_that_is_voiced_clears_the_count(self):
        import numpy as np

        from app.receptionist.tts import UraSpeechTTS
        from app.speech_service import SynthesizeResult

        class Working:
            def synthesize(self, *_args, **_kwargs):
                return SynthesizeResult(
                    audio=b"wav", sample_rate=16000, num_samples=1600, duration_s=0.1, latency_s=0.0,
                    backend="spark", voice="",
                )

        self.state.voice_failures = 1
        tts = UraSpeechTTS(speech_model=Working(), room=self.room)
        pcm = np.zeros(1600, dtype=np.int16).tobytes()
        with (
            patch.object(UraSpeechTTS, "_should_stream", return_value=False),
            patch("app.receptionist.tts.decode_to_pcm16", return_value=pcm),
        ):
            voiced = await self.speak(tts)

        self.assertTrue(voiced)
        self.assertEqual(self.state.voice_failures, 0)
        self.brain.on_speech_failure.assert_not_awaited()

    async def test_a_line_spoken_outside_the_pipeline_is_counted_too(self):
        from app.receptionist.tts import synthesize_pcm16

        before = counter("receptionist_call_failures_total", stage="tts")

        self.assertEqual(synthesize_pcm16(BrokenSpeech(), "An officer has joined the call.", "en"), b"")

        self.assertEqual(counter("receptionist_call_failures_total", stage="tts") - before, 1)


# ---------------------------------------------------------------------------
# What the call path reports
# ---------------------------------------------------------------------------


class CallTelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()

    async def room(self):
        from app.receptionist.state import CallRoom, CallState

        call_id = f"call_tel_{uuid.uuid4().hex[:8]}"
        create_call(call_id, conversation_id=f"conv_{call_id}", status="ai")
        return CallRoom(call_id=call_id, state=CallState(call_id=call_id, conversation_id=f"conv_{call_id}", mode="ai"))

    async def test_every_ended_call_is_counted_by_its_reason(self):
        before = counter("receptionist_calls_ended_total", reason="caller_hangup")
        room = await self.room()

        await room.end("caller_hangup")
        await room.end("caller_hangup")  # ending twice is one call

        self.assertEqual(counter("receptionist_calls_ended_total", reason="caller_hangup") - before, 1)

    async def test_a_reason_outside_the_vocabulary_cannot_add_a_series(self):
        before = counter("receptionist_calls_ended_total", reason="other")
        room = await self.room()

        await room.end("something\nnobody declared")

        self.assertEqual(counter("receptionist_calls_ended_total", reason="other") - before, 1)

    async def test_an_end_that_could_not_be_recorded_is_counted_not_hidden(self):
        before = counter("receptionist_call_failures_total", stage="record")
        room = await self.room()

        with (
            patch("app.receptionist.state.update_call", side_effect=RuntimeError("database is locked")),
            self.assertLogs("app.receptionist.state", level="WARNING"),
        ):
            await room.end("caller_hangup")

        self.assertEqual(counter("receptionist_call_failures_total", stage="record") - before, 1)

    def test_every_series_an_alert_reads_exists_before_the_first_failure(self):
        # increase() sees nothing in a series that first appears at 1: the
        # first failure after a restart would not count toward its alert.
        from app.analytics import metrics
        from app.receptionist import telemetry

        telemetry.declare()

        counters = metrics.snapshot()["counters"]
        for stage in telemetry.FAILURE_STAGES:
            self.assertIn(f'receptionist_call_failures_total{{stage="{stage}"}}', counters)
        for reason in telemetry.END_REASONS:
            self.assertIn(f'receptionist_calls_ended_total{{reason="{reason}"}}', counters)

    def test_every_reason_the_code_ends_a_call_with_is_in_the_vocabulary(self):
        import re
        from pathlib import Path

        from app.receptionist import telemetry

        package = Path(telemetry.__file__).parent
        used: set[str] = set()
        for path in package.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            used.update(re.findall(r"\.end\(\"([a-z_]+)\"\)", text))
            used.update(re.findall(r"hang_up_caller\([a-z_.]+, \"([a-z_]+)\"\)", text))
            used.update(re.findall(r"_end_call_after\([^\n]*, \"([a-z_]+)\"\)", text))
        self.assertGreaterEqual(len(used), 8)
        self.assertEqual(used - set(telemetry.END_REASONS), set())

    def test_the_startup_sweep_counts_the_calls_it_ended(self):
        before = counter("receptionist_calls_lost_total")
        with (
            patch.object(receptionist, "is_available", return_value=True),
            patch("app.receptionist.config.validate"),
            patch("app.receptionist.store.init_receptionist_schema"),
            patch("app.receptionist.store.end_stale_calls", return_value=3),
        ):
            receptionist.start(speech_model=object())
        receptionist.mark_available()

        self.assertEqual(counter("receptionist_calls_lost_total") - before, 3)


class RefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        receptionist.mark_available()
        self.addCleanup(receptionist.mark_available)

    def test_a_refused_call_says_which_gate_refused_it(self):
        with patch.object(ws.flags, "is_enabled", return_value=False):
            self.assertEqual(ws._refusal_reason(), "off")
        with patch.object(ws.flags, "is_enabled", return_value=True):
            self.assertEqual(ws._refusal_reason(), "")
            receptionist.mark_unavailable("Pipecat is not installed")
            self.assertEqual(ws._refusal_reason(), "unavailable")

    def test_production_without_its_media_path_is_not_configured(self):
        with (
            patch.object(ws.flags, "is_enabled", return_value=True),
            patch.dict("os.environ", {"APP_ENV": "production", "LIVEKIT_URL": ""}),
        ):
            self.assertEqual(ws._refusal_reason(), "not_configured")


class OnlyWorkerTests(unittest.TestCase):
    """Ending every live row is for the one process that can own them."""

    def started_with(self, env: dict[str, str]) -> bool:
        with (
            patch.dict("os.environ", env),
            patch.object(receptionist, "is_available", return_value=True),
            patch("app.receptionist.config.validate"),
            patch("app.receptionist.store.init_receptionist_schema"),
            patch("app.receptionist.store.end_stale_calls", return_value=0) as sweep,
        ):
            for name in (*WORKER_VARIABLES, "VOICE_RECEPTIONIST_REPLICAS"):
                if name not in env:
                    __import__("os").environ.pop(name, None)
            receptionist.start(speech_model=object())
        receptionist.mark_available()
        return bool(sweep.call_args.kwargs["everything"])

    def test_one_worker_of_one_replica_ends_every_live_row(self):
        self.assertTrue(self.started_with({"UVICORN_WORKERS": "1", "VOICE_RECEPTIONIST_REPLICAS": "1"}))

    def test_one_worker_of_an_unknown_number_of_replicas_does_not(self):
        # Another replica on the same database may be carrying those calls.
        self.assertFalse(self.started_with({"UVICORN_WORKERS": "1"}))

    def test_two_workers_do_not(self):
        self.assertFalse(self.started_with({"UVICORN_WORKERS": "2", "VOICE_RECEPTIONIST_REPLICAS": "1"}))


# ---------------------------------------------------------------------------
# Low-bandwidth mode and call-drop resume: nothing is offered that cannot be delivered
# ---------------------------------------------------------------------------


def flags_on(*names: str):
    from app.flags import flags

    class _On:
        def __enter__(self):
            for name in names:
                flags.set(name, True)

        def __exit__(self, *_exc):
            for name in names:
                flags.clear(name)

    return _On()


SLOW_NETWORK = {"type": "network_quality", "effective_type": "2g", "rtt_ms": 820, "save_data": True}


class LowBandwidthTests(unittest.IsolatedAsyncioTestCase):
    def room(self, **state):
        from app.receptionist.state import CallRoom, CallState

        call_id = f"call_lbw_{uuid.uuid4().hex[:8]}"
        return CallRoom(call_id=call_id, state=CallState(call_id=call_id, conversation_id=f"conv_{call_id}", **state))

    def test_a_codec_the_server_cannot_encode_is_not_agreed_to(self):
        from app.receptionist.low_bandwidth import negotiate_low_bandwidth_mode

        with flags_on("low_bandwidth_voice_codec"):
            profile = negotiate_low_bandwidth_mode({**SLOW_NETWORK, "audio_codec": "opus_12k"})

        # The serializer speaks PCM16 and mu-law; an acknowledged Opus stream was sent as raw PCM.
        self.assertEqual((profile.codec, profile.sample_rate), ("mulaw_8k", 8000))

    def test_a_call_whose_audio_is_in_the_media_room_is_told_nothing_changed(self):
        from app.receptionist.low_bandwidth import apply_network_telemetry_to_room

        room = self.room(livekit_room="ura-call-1")
        with flags_on("low_bandwidth_voice_codec"):
            ack = apply_network_telemetry_to_room(room, SLOW_NETWORK)

        # This socket carries no audio there, so it cannot change the codec or stop the sound.
        self.assertEqual((ack["enabled"], ack["codec"], ack["caption_only"]), (False, "pcm16_16k", False))
        self.assertEqual((room.state.audio_codec, room.state.caption_only_degraded), ("pcm16_16k", False))

    async def test_the_checkpoint_is_written_off_the_event_loop(self):
        import json
        import threading

        from app.receptionist.serializer import BrowserCallSerializer

        db.init_db()
        loop_thread = threading.current_thread()
        written_on_loop: list[bool] = []
        real = db.upsert_working_memory

        def record(*args, **kwargs):
            written_on_loop.append(threading.current_thread() is loop_thread)
            return real(*args, **kwargs)

        room = self.room()
        with (
            flags_on("low_bandwidth_voice_codec", "call_drop_resume"),
            patch.dict("os.environ", {"RECEPTIONIST_RESUME_SECRET": "r" * 40}),
            patch.object(db, "upsert_working_memory", side_effect=record),
        ):
            frame = await BrowserCallSerializer(room=room).deserialize(json.dumps(SLOW_NETWORK))
            # Written in the background: the acknowledgement does not wait for it.
            from app.receptionist import serializer as serializer_module

            await asyncio.gather(*serializer_module._CHECKPOINTS)

        self.assertTrue(frame.message["resume_ticket"])
        self.assertEqual(written_on_loop, [False])

    async def test_telemetry_is_rate_limited_like_any_control_message(self):
        import json

        from app.receptionist import serializer as serializer_module
        from app.receptionist.serializer import BrowserCallSerializer

        serializer = BrowserCallSerializer(room=self.room())
        with flags_on("low_bandwidth_voice_codec"):
            frames = [
                await serializer.deserialize(json.dumps(SLOW_NETWORK))
                for _ in range(serializer_module._MAX_CONTROL_FRAMES_PER_WINDOW + 5)
            ]

        self.assertEqual(sum(f is not None for f in frames), serializer_module._MAX_TELEMETRY_FRAMES_PER_WINDOW)


class ResumeSigningTests(unittest.TestCase):
    def mint(self, env: dict[str, str]) -> str:
        from app.receptionist.low_bandwidth import mint_call_resume_ticket

        cleared = {"RECEPTIONIST_RESUME_SECRET": "", "AUTH_DEV_SECRET": "", "APP_ENV": "development", **env}
        with flags_on("call_drop_resume"), patch.dict("os.environ", cleared):
            return mint_call_resume_ticket("call_1", "conv_1", user_id="taxpayer_1")

    def test_production_does_not_sign_with_the_development_secret(self):
        self.assertEqual(self.mint({"APP_ENV": "production", "AUTH_DEV_SECRET": "dev-secret-everyone-knows"}), "")

    def test_production_signs_with_its_own_secret(self):
        self.assertTrue(self.mint({"APP_ENV": "production", "RECEPTIONIST_RESUME_SECRET": "r" * 40}))

    def test_development_may_fall_back_to_the_development_secret(self):
        self.assertTrue(self.mint({"AUTH_DEV_SECRET": "dev-secret-everyone-knows"}))

    def test_startup_says_when_resume_is_on_and_cannot_issue_tickets(self):
        from app.flags import flags

        def enabled(name, **_kw):
            return name in ("voice_receptionist", "call_drop_resume")

        with (
            patch.object(flags, "is_enabled", side_effect=enabled),
            patch.dict("os.environ", {"APP_ENV": "production", "RECEPTIONIST_RESUME_SECRET": "", "UVICORN_WORKERS": "1"}),
            patch.object(receptionist, "is_available", return_value=True),
            patch("app.receptionist.config.validate"),
            patch("app.receptionist.store.init_receptionist_schema"),
            patch("app.receptionist.store.end_stale_calls", return_value=0),
            self.assertLogs("app.receptionist", level="WARNING") as logs,
        ):
            reason = receptionist.start(speech_model=object())
        receptionist.mark_available()

        self.assertEqual(reason, "")  # calls still work; only resume does not
        self.assertTrue(any("RECEPTIONIST_RESUME_SECRET" in line for line in logs.output))


# ---------------------------------------------------------------------------
# Smaller reliability items
# ---------------------------------------------------------------------------


class SmallReliabilityTests(unittest.IsolatedAsyncioTestCase):
    def test_the_signer_trims_the_credentials_the_gate_trimmed(self):
        import sys
        import types

        seen: dict[str, str] = {}

        class AccessToken:
            def __init__(self, key: str, secret: str) -> None:
                seen.update(key=key, secret=secret)

            def with_identity(self, *_a):
                return self

            with_name = with_grants = with_ttl = with_identity

            def to_jwt(self) -> str:
                return "jwt"

        api = types.SimpleNamespace(AccessToken=AccessToken, VideoGrants=lambda **_kw: object())
        package = types.ModuleType("livekit")
        package.api = api
        env = {"LIVEKIT_URL": "wss://rtc.example.test", "LIVEKIT_API_KEY": " server-key \n", "LIVEKIT_API_SECRET": "\ts3cret-value \n"}
        with patch.dict("os.environ", env), patch.dict(sys.modules, {"livekit": package, "livekit.api": api}):
            livekit.mint_token(room_name="ura-call-1", identity="caller-1", name="URA taxpayer")

        # A secret pasted with its newline passed the gate and then signed
        # tokens the media server refused.
        self.assertEqual(seen, {"key": "server-key", "secret": "s3cret-value"})

    async def test_synthesised_audio_is_decoded_off_the_event_loop(self):
        import threading

        import numpy as np

        from app.receptionist.state import CallRoom, CallState
        from app.receptionist.tts import UraSpeechTTS
        from app.speech_service import SynthesizeResult

        loop_thread = threading.current_thread()
        decoded_on_loop: list[bool] = []

        def decode(_model, _audio):
            decoded_on_loop.append(threading.current_thread() is loop_thread)
            return np.zeros(1600, dtype=np.int16).tobytes()

        class Speech:
            def synthesize(self, *_a, **_k):
                return SynthesizeResult(
                    audio=b"wav", sample_rate=16000, num_samples=1600, duration_s=0.1, latency_s=0.0,
                    backend="spark", voice="",
                )

        room = CallRoom("call_dec", CallState(call_id="call_dec", conversation_id="conv_dec"))
        tts = UraSpeechTTS(speech_model=Speech(), room=room)
        with (
            patch.object(UraSpeechTTS, "_should_stream", return_value=False),
            patch("app.receptionist.tts.decode_to_pcm16", side_effect=decode),
        ):
            frames = [f async for f in tts._voice("Your return is due on the fifteenth.", "ctx")]

        self.assertTrue(frames)
        self.assertEqual(decoded_on_loop, [False])

    async def test_the_live_caption_buffer_stops_growing_past_what_it_can_decode(self):
        from unittest.mock import MagicMock

        from app.receptionist.taps import LivePartialTranscriptTap

        tap = LivePartialTranscriptTap(room=MagicMock(), speech_model=MagicMock(), max_audio_s=1.0)
        tap._speaking = True
        second = b"\x00\x01" * 16000  # one second of 16 kHz PCM16
        for _ in range(30):  # a caller who talks, or a VAD that never closes the turn, for half a minute
            tap._keep(second)
        if tap._pending is not None:
            await tap._pending

        self.assertLessEqual(len(tap._buffer), tap._max_bytes + len(second))

    def test_a_call_opens_only_in_a_language_calls_are_held_in(self):
        with patch.dict("os.environ", {"RECEPTIONIST_LANGUAGES": "en,sw", "RECEPTIONIST_DEFAULT_LANGUAGE": "en"}):
            self.assertEqual(ws._call_start_locales({"locale": "lg"}), ("en", "lg"))
            self.assertEqual(ws._call_start_locales({"locale": "sw"}), ("sw", "sw"))
            self.assertEqual(ws._call_start_locales({"locale": "xx", "preferred_locale": "zz"}), ("en", "en"))

    async def test_the_language_router_is_closed_when_the_call_ends(self):
        from unittest.mock import AsyncMock

        from app.receptionist.state import CallRoom, CallState

        db.init_db()
        init_receptionist_schema()
        call_id = f"call_rt_{uuid.uuid4().hex[:8]}"
        create_call(call_id, conversation_id=f"conv_{call_id}", status="ai")
        room = CallRoom(call_id=call_id, state=CallState(call_id=call_id, conversation_id=f"conv_{call_id}", mode="ai"))
        room.language_router = AsyncMock()

        await room.end("caller_hangup")

        room.language_router.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Found in review of this change
# ---------------------------------------------------------------------------


class StartReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        receptionist.mark_available()
        self.addCleanup(receptionist.reset)

    def test_a_restart_without_pipecat_still_ends_the_calls_its_predecessor_left(self):
        with (
            patch.object(receptionist, "is_available", return_value=False),
            patch("app.receptionist.config.validate"),
            patch("app.receptionist.store.init_receptionist_schema"),
            patch("app.receptionist.store.end_stale_calls", return_value=2) as sweep,
        ):
            reason = receptionist.start(speech_model=object())

        self.assertIn("Pipecat", reason)
        sweep.assert_called_once()

    def test_the_refusal_counters_exist_even_when_the_start_fails(self):
        from app.analytics import metrics

        with (
            patch.object(receptionist, "is_available", return_value=False),
            patch("app.receptionist.store.init_receptionist_schema"),
            patch("app.receptionist.store.end_stale_calls", return_value=0),
            patch.object(metrics, "inc", wraps=metrics.inc) as inc,
        ):
            receptionist.start(speech_model=object())

        declared = {c.args[0] for c in inc.call_args_list if len(c.args) > 1 and c.args[1] == 0}
        self.assertIn("receptionist_calls_refused_total", declared)

    def test_calls_switched_on_after_the_api_started_are_refused_in_production(self):
        receptionist.reset()  # this process never ran the receptionist's start
        with (
            patch.object(ws.flags, "is_enabled", return_value=True),
            patch.dict("os.environ", {"APP_ENV": "production"}),
            patch.object(ws.livekit, "production_errors", return_value=[]),
            patch.object(ws, "local_engine_errors", return_value=[]),
        ):
            self.assertEqual(ws._refusal_reason(), "unavailable")
        with patch("app.flags.flags.is_enabled", return_value=True):
            self.assertIn("restart", receptionist.status())

    def test_a_long_turn_timeout_that_used_to_work_still_does(self):
        with patch.dict("os.environ", {"RECEPTIONIST_TURN_TIMEOUT_S": "20"}):
            config.validate()
            self.assertEqual(config.get_turn_timeout_s(), 20.0)


class EscalationReviewTests(CallCase):
    async def test_a_handover_that_raised_can_be_tried_again(self):
        from unittest.mock import AsyncMock

        self.brain._transfer = AsyncMock(side_effect=[RuntimeError("ticket store down"), None])
        with self.assertRaises(RuntimeError):
            await self.brain.on_speech_failure("tts")
            await self.brain.on_speech_failure("tts")

        await self.brain.on_speech_failure("tts")

        self.assertEqual(self.brain._transfer.await_count, 2)

    async def test_a_caller_still_unheard_after_a_handover_that_did_not_take_is_asked_again(self):
        from app.receptionist.phrases import phrase

        await self.brain.on_speech_failure("stt")
        await self.brain.on_speech_failure("stt")  # handed over; the queue was off, so the call stays with the AI
        before = len(self.said())

        await self.brain.on_speech_failure("stt")

        self.assertEqual(self.said()[before:], [phrase("clarify_repeat", "en")])
        self.brain._transfer.assert_awaited_once()


class SpeechReviewTests(CallCase):
    async def asyncSetUp(self) -> None:
        from unittest.mock import AsyncMock

        await super().asyncSetUp()
        self.brain.on_speech_failure = AsyncMock()

    async def transcribe(self, speech) -> list:
        from app.receptionist.stt import UraWhisperSTT

        stt = UraWhisperSTT(speech_model=speech, room=self.room)
        return [frame async for frame in stt.run_stt(b"\x01\x00" * 1600)]

    async def speak(self, tts, text: str = "Your return is due on the fifteenth.") -> list:
        return [frame async for frame in tts._voice(text, "ctx")]

    async def test_a_recognizer_that_answers_with_an_error_has_failed(self):
        from app.speech_service import TranscribeResult

        class AllBackendsDown:
            def transcribe(self, *_a, **_k):
                return TranscribeResult(text="", backend="unavailable", error="All ASR backends failed")

        self.assertEqual(await self.transcribe(AllBackendsDown()), [])
        self.brain.on_speech_failure.assert_awaited_once_with("stt")

    async def test_a_recognizer_that_heard_nothing_has_not_failed(self):
        from app.speech_service import TranscribeResult

        class Quiet:
            def transcribe(self, *_a, **_k):
                return TranscribeResult(text="", backend="sunbird", error=None)

        self.assertEqual(await self.transcribe(Quiet()), [])
        self.brain.on_speech_failure.assert_not_awaited()

    @unittest.skipUnless(receptionist.is_available(), "streams through Pipecat's TTSService")
    async def test_an_orpheus_sentence_cut_short_is_not_a_voiced_line(self):
        from app import orpheus_tts
        from app.receptionist.tts import UraSpeechTTS

        async def cut_short(_text, _language, *, voice=None):
            yield b"\x00\x10" * 2400
            raise orpheus_tts.OrpheusIncomplete("HTTP 503")

        self.state.voice_failures = 1
        tts = UraSpeechTTS(speech_model=BrokenSpeech(), room=self.room)
        tts._sample_rate = 16000  # normally set when the pipeline starts
        with (
            patch.object(UraSpeechTTS, "_should_stream", return_value=True),
            patch.object(orpheus_tts, "stream", cut_short),
        ):
            await self.speak(tts)

        self.brain.on_speech_failure.assert_awaited_once_with("tts")

    async def test_a_line_said_outside_the_pipeline_that_cannot_be_voiced_reaches_the_brain(self):
        from app.receptionist import desk

        before = counter("receptionist_call_failures_total", stage="tts")
        await desk.say_to_caller(self.room, "Are you still there?", BrokenSpeech())

        self.brain.on_speech_failure.assert_awaited_once_with("tts")
        self.assertEqual(counter("receptionist_call_failures_total", stage="tts") - before, 1)

    async def test_a_luganda_line_only_the_english_stand_in_could_read_is_a_failure_to_speak(self):
        # The real voices failed; the policy refuses the English reading, so
        # the caller hears nothing. Two of those in a row is a call that cannot speak.
        import numpy as np

        from app.receptionist.tts import UraSpeechTTS
        from app.speech_service import SynthesizeResult

        class StandInOnly:
            def synthesize(self, *_a, **_k):
                return SynthesizeResult(
                    audio=b"wav", sample_rate=16000, num_samples=1600, duration_s=0.1, latency_s=0.0,
                    backend="edge_tts", voice="en-KE-AsiliaNeural",
                )

        self.state.locale = "lg"
        tts = UraSpeechTTS(speech_model=StandInOnly(), room=self.room)
        with (
            patch.object(UraSpeechTTS, "_should_stream", return_value=False),
            patch("app.receptionist.tts.allow_edge_standin_lg", return_value=False),
            patch("app.receptionist.tts.decode_to_pcm16", return_value=np.zeros(1600, dtype=np.int16).tobytes()),
        ):
            self.assertEqual(await self.speak(tts, "Omusolo gwa VAT guli 18%."), [])

        self.brain.on_speech_failure.assert_awaited_once_with("tts")

    async def test_telemetry_does_not_use_up_the_callers_controls(self):
        import json as _json

        from app.receptionist import serializer as serializer_module
        from app.receptionist.serializer import BrowserCallSerializer

        serializer = BrowserCallSerializer(room=self.room)
        with flags_on("low_bandwidth_voice_codec"):
            for _ in range(serializer_module._MAX_CONTROL_FRAMES_PER_WINDOW):
                await serializer.deserialize(_json.dumps(SLOW_NETWORK))
            question = await serializer.deserialize(_json.dumps({"type": "text_turn", "text": "What is the VAT rate?"}))

        self.assertIsNotNone(question)


class ResumedLanguageTests(unittest.TestCase):
    def test_a_resumed_call_opens_only_in_a_language_calls_are_held_in(self):
        with patch.dict("os.environ", {"RECEPTIONIST_LANGUAGES": "en,sw", "RECEPTIONIST_DEFAULT_LANGUAGE": "en"}):
            self.assertEqual(ws._resumed_locale({"locale": "lg"}, "en"), "en")
            self.assertEqual(ws._resumed_locale({"locale": "sw"}, "en"), "sw")
            self.assertEqual(ws._resumed_locale(None, "en"), "en")


class G711Tests(unittest.TestCase):
    """mu-law on the wire is ITU-T G.711, which any telephony decoder reads."""

    def test_silence_is_the_g711_silence_byte(self):
        from app.receptionist.low_bandwidth import pcm16_16k_to_mulaw_8k

        self.assertEqual(set(pcm16_16k_to_mulaw_8k(b"\x00\x00" * 320)), {0xFF})

    def test_the_g711_reference_points_decode_as_the_standard_says(self):
        import numpy as np

        from app.receptionist.low_bandwidth import mulaw_8k_to_pcm16_16k

        decoded = np.frombuffer(mulaw_8k_to_pcm16_16k(bytes([0xFF, 0x7F, 0x00, 0x80])), dtype=np.int16)[0::2]
        self.assertEqual(decoded.tolist(), [0, 0, -32124, 32124])

    def test_a_round_trip_keeps_the_signal(self):
        import numpy as np

        from app.receptionist.low_bandwidth import mulaw_8k_to_pcm16_16k, pcm16_16k_to_mulaw_8k

        tone = (np.sin(2 * np.pi * 440 * np.arange(16000) / 16000) * 12000).astype(np.int16)
        back = np.frombuffer(mulaw_8k_to_pcm16_16k(pcm16_16k_to_mulaw_8k(tone.tobytes())), dtype=np.int16)
        self.assertEqual(back.size, tone.size)
        error = np.abs(back[0::2].astype(np.int32) - tone[0::2].astype(np.int32))
        self.assertLess(int(error.max()), 12000 // 16)


class ResumeReplayTests(unittest.TestCase):
    def test_a_ticket_is_not_accepted_twice_even_when_the_used_list_is_full(self):
        from app.receptionist import low_bandwidth as lb

        with (
            flags_on("call_drop_resume"),
            patch.dict("os.environ", {"RECEPTIONIST_RESUME_SECRET": "r" * 40}),
            patch.object(lb, "_MAX_CONSUMED_NONCES", 2),
            patch.dict(lb._CONSUMED_RESUME_NONCES, {}, clear=True),
        ):
            first = lb.mint_call_resume_ticket("call_1", "conv_1", user_id="u1")
            self.assertIsNotNone(lb.verify_call_resume_ticket(first, expected_user_id="u1"))
            for n in range(2):
                other = lb.mint_call_resume_ticket(f"call_x{n}", "conv", user_id="u1")
                lb.verify_call_resume_ticket(other, expected_user_id="u1")

            self.assertIsNone(lb.verify_call_resume_ticket(first, expected_user_id="u1"))


if __name__ == "__main__":
    unittest.main()

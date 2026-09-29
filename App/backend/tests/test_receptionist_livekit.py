"""Fail-closed LiveKit configuration, room tokens, and shared-room audio isolation."""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import types
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.receptionist import livekit
from app.receptionist.state import CallRoom, CallState, registry
from app.receptionist.taps import CallerAudioTap, InputAudioRawFrame, UserAudioRawFrame


def test_production_livekit_gate_rejects_http_and_multiple_workers(monkeypatch) -> None:
    monkeypatch.setenv("FLAG_VOICE_RECEPTIONIST", "true")
    monkeypatch.setenv("LIVEKIT_URL", "http://rtc.example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret")
    monkeypatch.setenv("RECEPTIONIST_MEDIA_TRANSPORT", "websocket")
    monkeypatch.setenv("WORKERS", "2")
    monkeypatch.setenv("VOICE_RECEPTIONIST_REPLICAS", "2")
    monkeypatch.setenv("VOICE_RECEPTIONIST_SINGLE_REPLICA_ACK", "false")

    errors = "\n".join(livekit.production_errors())
    assert "LIVEKIT_URL must be a secure wss://" in errors
    assert "RECEPTIONIST_MEDIA_TRANSPORT=livekit" in errors
    assert "explicitly set WORKERS=1" in errors
    assert "VOICE_RECEPTIONIST_REPLICAS=1" in errors
    assert "SINGLE_REPLICA_ACK=true" in errors


def test_production_gate_rejects_livekit_development_credentials(monkeypatch) -> None:
    monkeypatch.setenv("FLAG_VOICE_RECEPTIONIST", "true")
    monkeypatch.setenv("LIVEKIT_URL", "wss://rtc.example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "devkey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret")
    monkeypatch.setenv("RECEPTIONIST_MEDIA_TRANSPORT", "livekit")
    monkeypatch.setenv("WORKERS", "1")
    monkeypatch.setenv("VOICE_RECEPTIONIST_REPLICAS", "1")
    monkeypatch.setenv("VOICE_RECEPTIONIST_SINGLE_REPLICA_ACK", "true")

    errors = "\n".join(livekit.production_errors())
    assert "development/example key" in errors
    assert "at least 32 characters" in errors


def test_minted_token_is_scoped_to_room_identity_and_audio_permissions(monkeypatch) -> None:
    class FakeAccessToken:
        def __init__(self, key, secret):
            self.claims = {"key": key, "secret": secret}

        def with_identity(self, identity):
            self.claims["identity"] = identity
            return self

        def with_name(self, name):
            self.claims["name"] = name
            return self

        def with_grants(self, grant):
            self.claims["grant"] = grant
            return self

        def with_ttl(self, ttl):
            self.claims["ttl"] = ttl
            return self

        def to_jwt(self):
            return self.claims

    api = types.SimpleNamespace(
        AccessToken=FakeAccessToken,
        VideoGrants=lambda **kwargs: kwargs,
    )
    fake_livekit = types.ModuleType("livekit")
    fake_livekit.api = api
    monkeypatch.setitem(sys.modules, "livekit", fake_livekit)
    monkeypatch.setenv("LIVEKIT_URL", "wss://rtc.example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "server-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "server-secret")

    caller = livekit.mint_token(
        room_name="ura-call_123", identity="caller-call_123", name="URA taxpayer"
    )
    observer = livekit.mint_token(
        room_name="ura-call_123", identity="observer-abc", name="Officer", can_publish=False
    )
    agent = livekit.mint_token(
        room_name="ura-call_123",
        identity="agent-call_123",
        name="URA AI receptionist",
        can_publish_data=True,
    )

    assert caller["identity"] == "caller-call_123"
    assert caller["grant"] == {
        "room_join": True,
        "room": "ura-call_123",
        "can_publish": True,
        "can_subscribe": True,
        "can_publish_data": False,
        "can_publish_sources": ["microphone"],
    }
    assert caller["ttl"] == timedelta(minutes=5)
    assert observer["grant"]["can_publish"] is False
    assert observer["grant"]["can_publish_data"] is False
    assert agent["grant"]["can_publish_data"] is True


def test_revoke_participant_updates_permissions_before_removing(monkeypatch) -> None:
    class FakeClient:
        def __init__(self):
            self.calls = []
            self.room = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def update_participant(self, request):
            self.calls.append(("update", request))

        async def remove_participant(self, request):
            self.calls.append(("remove", request))

        async def delete_room(self, request):
            self.calls.append(("delete", request))

    client = FakeClient()
    api = types.SimpleNamespace(
        LiveKitAPI=lambda: client,
        UpdateParticipantRequest=lambda **kwargs: kwargs,
        ParticipantPermission=lambda **kwargs: kwargs,
        RoomParticipantIdentity=lambda **kwargs: kwargs,
        DeleteRoomRequest=lambda **kwargs: kwargs,
    )
    fake_livekit = types.ModuleType("livekit")
    fake_livekit.api = api
    monkeypatch.setitem(sys.modules, "livekit", fake_livekit)
    monkeypatch.setenv("LIVEKIT_URL", "wss://rtc.example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "server-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "server-secret")

    async def exercise() -> None:
        await livekit.revoke_participant("ura-call_123", "officer-old")
        await livekit.close_room("ura-call_123")

    asyncio.run(exercise())
    assert [kind for kind, _ in client.calls] == ["update", "remove", "delete"]
    assert client.calls[0][1]["permission"] == {
        "can_publish": False,
        "can_subscribe": False,
        "can_publish_data": False,
    }
    assert client.calls[-1][1] == {"room": "ura-call_123"}


def test_call_end_closes_livekit_room_once(monkeypatch) -> None:
    room = CallRoom(
        "call_close_livekit",
        CallState(call_id="call_close_livekit", conversation_id="conv_close_livekit"),
    )
    room.state.livekit_room = "ura-call_close_livekit"
    close_room = AsyncMock()
    monkeypatch.setattr("app.receptionist.state.update_call", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(livekit, "close_room", close_room)

    async def exercise() -> None:
        await room.end("caller_hangup")
        await room.end("caller_hangup")

    asyncio.run(exercise())
    close_room.assert_awaited_once_with("ura-call_close_livekit")


def test_livekit_identities_do_not_expose_staff_account_ids() -> None:
    identity = livekit.participant_identity("officer", "staff.member@ura.go.ug")
    assert identity.startswith("officer-")
    assert "staff.member" not in identity
    assert "@" not in identity


def test_participant_allowlist_rejects_transferred_officer_and_unknown_identity() -> None:
    state = CallState(call_id="call_allowlist", conversation_id="conv_allowlist")
    state.livekit_caller_identity = "caller-current"
    state.livekit_agent_identity = "agent-current"
    state.livekit_officer_identity = "officer-new"
    state.livekit_observer_identities.add("observer-current")

    assert livekit.is_authorized_participant(state, "caller-current")
    assert livekit.is_authorized_participant(state, "agent-current")
    assert livekit.is_authorized_participant(state, "officer-new")
    assert livekit.is_authorized_participant(state, "observer-current")
    assert not livekit.is_authorized_participant(state, "officer-transferred")
    assert not livekit.is_authorized_participant(state, "unissued")


def test_shared_room_audio_tap_drops_officer_and_normalizes_caller_audio() -> None:
    class RecordingTap(CallerAudioTap):
        def __init__(self, room):
            super().__init__(room)
            self.frames = []

        async def push_frame(self, frame, direction=None):
            self.frames.append(frame)

    async def exercise() -> None:
        room = await registry.create("call_livekit_tap", "conv_livekit_tap")
        room.state.livekit_room = "ura-call_livekit_tap"
        room.state.livekit_caller_identity = "caller-opaque"
        room.state.livekit_officer_identity = "officer-opaque"
        tap = RecordingTap(room)

        officer_frame = UserAudioRawFrame(
            user_id="officer-opaque", audio=b"\x01\x00", sample_rate=16000, num_channels=1
        )
        await tap.process_frame(officer_frame)
        assert tap.frames == []

        caller_frame = UserAudioRawFrame(
            user_id="caller-opaque", audio=b"\x02\x00", sample_rate=16000, num_channels=1
        )
        await tap.process_frame(caller_frame)
        assert len(tap.frames) == 1
        assert isinstance(tap.frames[0], InputAudioRawFrame)
        assert tap.frames[0].audio == b"\x02\x00"
        await registry.remove(room.call_id)

    asyncio.run(exercise())


# ---------------------------------------------------------------------------
# Review follow-ups (PR #515)
# ---------------------------------------------------------------------------
_G36_READY = {
    "FLAG_VOICE_RECEPTIONIST": "true",
    "LIVEKIT_URL": "wss://rtc.example.test",
    "LIVEKIT_API_KEY": "production-key",  # pragma: allowlist secret
    "LIVEKIT_API_SECRET": "production-secret-value-that-is-long-enough",  # pragma: allowlist secret
    "RECEPTIONIST_MEDIA_TRANSPORT": "livekit",
    "WORKERS": "1",
    "VOICE_RECEPTIONIST_REPLICAS": "1",
    "VOICE_RECEPTIONIST_SINGLE_REPLICA_ACK": "true",
}


@pytest.mark.parametrize("raw", ["15m", "0", "-30", "nan", "inf"])
def test_an_invalid_call_duration_is_a_configuration_error(monkeypatch, raw) -> None:
    from app.receptionist import config

    monkeypatch.setenv("RECEPTIONIST_MAX_CALL_S", raw)
    for read in (config.get_max_call_s, config.get_gemini_session_timeout_s, config.validate):
        with pytest.raises(config.ReceptionistConfigError, match="RECEPTIONIST_MAX_CALL_S"):
            read()


def test_call_duration_defaults_and_the_gemini_ceiling(monkeypatch) -> None:
    from app.receptionist import config

    monkeypatch.delenv("RECEPTIONIST_MAX_CALL_S", raising=False)
    assert (config.get_max_call_s(), config.get_gemini_session_timeout_s()) == (900.0, 480.0)
    monkeypatch.setenv("RECEPTIONIST_MAX_CALL_S", "  ")
    assert config.get_max_call_s() == 900.0
    monkeypatch.setenv("RECEPTIONIST_MAX_CALL_S", "300")
    assert (config.get_max_call_s(), config.get_gemini_session_timeout_s()) == (300.0, 300.0)
    monkeypatch.setenv("RECEPTIONIST_MAX_CALL_S", "1200")
    assert (config.get_max_call_s(), config.get_gemini_session_timeout_s()) == (1200.0, 480.0)


def test_production_gate_reports_an_invalid_call_duration(monkeypatch) -> None:
    for key, value in _G36_READY.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("RECEPTIONIST_MAX_CALL_S", "15m")
    errors = livekit.production_errors()
    assert errors == ["G36: RECEPTIONIST_MAX_CALL_S must be a number of seconds, got '15m'"]


def test_agent_identity_in_the_allowlist_is_the_one_its_token_carries(monkeypatch) -> None:
    minted: dict = {}

    def fake_mint(**kwargs):
        minted.update(kwargs)
        return "agent-jwt"

    fake_transport = types.ModuleType("pipecat.transports.livekit.transport")
    fake_transport.LiveKitParams = lambda **kw: SimpleNamespace(**kw)
    fake_transport.LiveKitTransport = lambda **kw: SimpleNamespace(**kw)
    monkeypatch.setitem(sys.modules, "pipecat.transports.livekit.transport", fake_transport)
    monkeypatch.setattr(livekit, "enabled", lambda: True)
    monkeypatch.setattr(livekit, "mint_token", fake_mint)
    monkeypatch.setenv("LIVEKIT_URL", "wss://rtc.example.test")

    from app.receptionist.pipeline import build_transport

    state = CallState(call_id="call odd/id", conversation_id="conv")
    state.livekit_room = "ura-call_odd_id"
    transport = build_transport(CallRoom(call_id="call odd/id", state=state))

    assert minted["identity"] == state.livekit_agent_identity == "ura-agent-call_odd_id"
    assert livekit.is_authorized_participant(state, minted["identity"])
    assert transport.token == "agent-jwt"


def test_log_ref_hides_the_identity_and_any_control_characters() -> None:
    forged = "caller-1\n2026-09-29 INFO forged line"
    ref = livekit.log_ref(forged)
    assert re.fullmatch(r"[0-9a-f]{12}", ref)
    assert ref == livekit.log_ref(forged)
    assert ref != livekit.log_ref("caller-1")


def test_officer_caption_is_skipped_when_the_caller_socket_is_gone(caplog) -> None:
    from app import database as db
    from app.receptionist.store import init_receptionist_schema, list_turns

    db.init_db()
    init_receptionist_schema()
    speech = SimpleNamespace(
        transcribe=lambda wav, rate, lang, with_words=False: SimpleNamespace(text="Hello, it is Okello.", latency_s=0.2)
    )

    async def exercise() -> list[dict]:
        room = await registry.create("call_livekit_caption", "conv_livekit_caption")
        room.caller_ws = None
        room.state.livekit_room = "ura-call_livekit_caption"
        tap = CallerAudioTap(room, speech_model=speech)
        with caplog.at_level(logging.ERROR, logger="app.receptionist.taps"):
            await tap._transcribe_officer(b"\x00\x00" * 1600)
        turns = list_turns(room.call_id)
        await registry.remove(room.call_id)
        return turns

    turns = asyncio.run(exercise())
    assert [(t["speaker"], t["text"]) for t in turns][-1] == ("officer", "Hello, it is Okello.")
    assert "Failed transcribing officer utterance" not in caplog.text


def test_ending_a_call_stops_its_media_pipeline() -> None:
    """A backstop for every end path (officer End, timeouts), not only the caller's loop."""

    async def exercise() -> None:
        room = await registry.create("call_livekit_end", "conv_livekit_end")
        stop = asyncio.Event()

        async def run() -> None:
            await stop.wait()

        room.pipeline_runner_task = asyncio.create_task(run())
        room.pipeline_task = SimpleNamespace(cancel=AsyncMock(side_effect=lambda: stop.set()))
        await room.end("officer_ended")
        room.pipeline_task.cancel.assert_awaited_once()
        await asyncio.wait_for(room.pipeline_runner_task, timeout=1)
        # A finished pipeline is left alone on a second end().
        await room.end("officer_ended")
        room.pipeline_task.cancel.assert_awaited_once()
        await registry.remove(room.call_id)

    asyncio.run(exercise())

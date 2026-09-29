"""Fail-closed LiveKit configuration, room tokens, and shared-room audio isolation."""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import timedelta
from unittest.mock import AsyncMock

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

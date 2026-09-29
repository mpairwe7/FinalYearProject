"""Self-hosted LiveKit configuration and room-scoped participant tokens.

The browser never receives the LiveKit API secret. Tokens are minted for one
room and one identity, expire quickly, and are issued only after the normal
URA authentication/claim checks have succeeded.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import timedelta
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def configured() -> bool:
    return bool(
        os.getenv("LIVEKIT_URL", "").strip()
        and os.getenv("LIVEKIT_API_KEY", "").strip()
        and os.getenv("LIVEKIT_API_SECRET", "").strip()
    )


def production_errors(*, enabled_override: bool | None = None) -> list[str]:
    """Return fail-closed G36 errors when production receptionist is requested."""
    enabled = enabled_override if enabled_override is not None else os.getenv(
        "FLAG_VOICE_RECEPTIONIST", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return []

    errors: list[str] = []
    raw_url = os.getenv("LIVEKIT_URL", "").strip()
    parsed = urlparse(raw_url)
    if (
        parsed.scheme != "wss"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        errors.append("G36: LIVEKIT_URL must be a secure wss:// URL in production.")
    api_key = os.getenv("LIVEKIT_API_KEY", "").strip()
    if not api_key:
        errors.append(
            "G36: LIVEKIT_API_KEY is required when the production receptionist is enabled."
        )
    elif api_key.lower() in {"devkey", "changeme", "change-me", "your-api-key"}:
        errors.append("G36: LIVEKIT_API_KEY must not use a LiveKit development/example key.")
    api_secret = os.getenv("LIVEKIT_API_SECRET", "").strip()
    if not api_secret:
        errors.append(
            "G36: LIVEKIT_API_SECRET is required when the production receptionist is enabled."
        )
    elif len(api_secret) < 32 or api_secret.lower() in {"secret", "changeme", "change-me"}:
        errors.append(
            "G36: LIVEKIT_API_SECRET must be a production secret of at least 32 characters."
        )
    if os.getenv("RECEPTIONIST_MEDIA_TRANSPORT", "").strip().lower() != "livekit":
        errors.append("G36: RECEPTIONIST_MEDIA_TRANSPORT=livekit is required in production.")
    if os.getenv("WORKERS", "").strip() != "1":
        errors.append(
            "G36: explicitly set WORKERS=1 while active call state remains process-local."
        )
    if os.getenv("VOICE_RECEPTIONIST_REPLICAS", "").strip() != "1":
        errors.append(
            "G36: VOICE_RECEPTIONIST_REPLICAS=1 is required while calls are process-local."
        )
    if os.getenv("VOICE_RECEPTIONIST_SINGLE_REPLICA_ACK", "false").strip().lower() not in {
        "1", "true", "yes", "on"
    }:
        errors.append(
            "G36: VOICE_RECEPTIONIST_SINGLE_REPLICA_ACK=true is required; "
            "active calls are not yet resilient to API process loss."
        )
    return errors


def enabled() -> bool:
    return (
        os.getenv("RECEPTIONIST_MEDIA_TRANSPORT", "").strip().lower() == "livekit"
        and configured()
    )


def _identity(value: str) -> str:
    # LiveKit participant identities are opaque, but bound length and charset
    # so a caller-controlled string can never become a malformed identity.
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", value)[:120]
    if not safe:
        raise ValueError("LiveKit identity is required")
    return safe


def mint_token(
    *,
    room_name: str,
    identity: str,
    name: str,
    can_publish: bool = True,
    can_publish_data: bool = False,
) -> str:
    """Mint a short-lived JWT restricted to one room and participant identity."""
    if not configured():
        raise RuntimeError("LiveKit is not configured")
    try:
        from livekit import api
    except ImportError as exc:
        raise RuntimeError("Install the receptionist LiveKit dependencies") from exc

    grant = api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=can_publish,
        can_subscribe=True,
        can_publish_data=can_publish_data,
        can_publish_sources=["microphone"] if can_publish else None,
    )
    return (
        api.AccessToken(
            os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"]
        )
        .with_identity(_identity(identity))
        .with_name(name[:120])
        .with_grants(grant)
        .with_ttl(timedelta(minutes=5))
        .to_jwt()
    )


async def revoke_participant(room_name: str, identity: str) -> None:
    """Restrict active room permissions, then disconnect the participant.

    Self-hosted LiveKit does not revoke existing JWTs. Updating permissions
    stops live publication/subscription; removing disconnects the participant.
    The app also removes stale identities after a reconnect event, and tokens
    are short-lived. Strict pre-join token revocation requires LiveKit Cloud.
    """
    if not configured():
        raise RuntimeError("LiveKit is not configured")
    try:
        from livekit import api
    except ImportError as exc:
        raise RuntimeError("Install the receptionist LiveKit dependencies") from exc

    async with api.LiveKitAPI() as client:
        update_error: Exception | None = None
        try:
            await client.room.update_participant(
                api.UpdateParticipantRequest(
                    room=room_name,
                    identity=identity,
                    permission=api.ParticipantPermission(
                        can_publish=False,
                        can_subscribe=False,
                        can_publish_data=False,
                    ),
                ),
            )
        except Exception as exc:
            update_error = exc
        try:
            await client.room.remove_participant(
                api.RoomParticipantIdentity(room=room_name, identity=identity)
            )
        except Exception:
            if update_error is not None:
                raise RuntimeError(
                    "LiveKit participant permissions and removal both failed"
                ) from update_error
            # The successful permission change has already disabled media.
            logger.warning(
                "LiveKit participant %s remains connected without media permission",
                identity,
            )


async def close_room(room_name: str) -> None:
    """Close a call's LiveKit room during terminal call teardown."""
    if not configured():
        return
    try:
        from livekit import api
    except ImportError as exc:
        raise RuntimeError("Install the receptionist LiveKit dependencies") from exc

    async with api.LiveKitAPI() as client:
        await client.room.delete_room(api.DeleteRoomRequest(room=room_name))


def room_name(call_id: str) -> str:
    return f"ura-{_identity(call_id)}"


def participant_identity(role: str, user_id: str) -> str:
    opaque_id = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:24]
    return _identity(f"{role}-{opaque_id}")


def is_authorized_participant(state: object, identity: str) -> bool:
    """Check the call's current server-side participant allowlist."""
    if not identity:
        return False
    known = {
        value
        for value in (
            getattr(state, "livekit_caller_identity", ""),
            getattr(state, "livekit_agent_identity", ""),
            getattr(state, "livekit_officer_identity", ""),
        )
        if value
    }
    observers = getattr(state, "livekit_observer_identities", set())
    return identity in known or identity in observers

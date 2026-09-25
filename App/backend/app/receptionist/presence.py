"""Staff officer presence tracking for the Call Desk.

Tracks available, busy, away, on_call, wrap_up, and offline states across
officers (docs/plans/officer-call-desk-plan.md §5, §6.4). An officer's status
is computed as offline on read when their last heartbeat is older than
``RECEPTIONIST_PRESENCE_TTL_S`` (60 s).

Status transitions are automatic:
- Claim / bridge: on_call
- End / transfer: wrap_up
- Wrap-up saved: back to pre-call status (default: available)
- Heartbeat: updates last_seen without spamming lobby events
- Status / display_name change: publishes ``officer.presence`` to the lobby.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .. import database as db
from ..escalation_notify import known_teams
from .config import get_presence_ttl_s
from .hub import hub

logger = logging.getLogger(__name__)

VALID_STATUSES = frozenset({"available", "busy", "away", "on_call", "wrap_up", "offline"})


def _effective_status(status: str, last_seen: float, now: float) -> str:
    """An officer who has not checked in within presence TTL is offline."""
    if status == "offline":
        return "offline"
    ttl = get_presence_ttl_s()
    if now - last_seen > ttl:
        return "offline"
    return status


def get_presence(user_id: str) -> dict[str, Any] | None:
    """Return presence record for an officer with computed status."""
    rows = db.query_all("SELECT * FROM officer_presence WHERE user_id = ?", (user_id,))
    if not rows:
        return None
    row = dict(rows[0])
    now = time.time()
    last_seen = float(row.get("last_seen") or 0.0)
    raw_status = str(row.get("status") or "offline")
    row["status"] = _effective_status(raw_status, last_seen, now)
    row["raw_status"] = raw_status
    try:
        row["languages"] = json.loads(row.get("languages_json") or '["en"]')
    except Exception:
        row["languages"] = ["en"]
    try:
        row["teams"] = json.loads(row.get("teams_json") or "[]")
    except Exception:
        row["teams"] = []
    return row


def upsert_presence(
    user_id: str,
    *,
    display_name: str | None = None,
    status: str | None = None,
    languages: list[str] | None = None,
    teams: list[str] | None = None,
    current_call_id: str | None = None,
    previous_status: str | None = None,
) -> dict[str, Any]:
    """Upsert presence for an officer, updating last_seen.

    Emits ``officer.presence`` to the lobby ONLY if the effective status or
    display name changed, avoiding heartbeat spam.
    """
    now = time.time()
    existing = get_presence(user_id)
    prev_effective = existing["status"] if existing else "offline"
    prev_name = existing["display_name"] if existing else ""

    target_display = display_name if display_name is not None else (existing["display_name"] if existing else user_id)
    target_status = status if status in VALID_STATUSES else (existing["raw_status"] if existing else "available")
    target_languages = languages if languages is not None else (existing["languages"] if existing else ["en"])
    target_teams = teams if teams is not None else (existing["teams"] if existing else [])
    target_call_id = current_call_id if current_call_id is not None else (existing.get("current_call_id", "") if existing else "")
    target_prev_status = previous_status if previous_status is not None else (existing.get("previous_status", "available") if existing else "available")

    lang_json = json.dumps(target_languages)
    teams_json = json.dumps(target_teams)

    if existing is not None:
        db.execute(
            """
            UPDATE officer_presence
            SET display_name = ?, status = ?, languages_json = ?, teams_json = ?,
                current_call_id = ?, previous_status = ?, last_seen = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (target_display, target_status, lang_json, teams_json, target_call_id, target_prev_status, now, now, user_id),
        )
    else:
        db.execute(
            """
            INSERT INTO officer_presence (
                user_id, display_name, status, languages_json, teams_json,
                current_call_id, previous_status, last_seen, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, target_display, target_status, lang_json, teams_json, target_call_id, target_prev_status, now, now),
        )

    current_effective = _effective_status(target_status, now, now)

    # Publish lobby event only if status or name changed
    if prev_effective != current_effective or prev_name != target_display:
        hub.publish_lobby(
            "officer.presence",
            {
                "user_id": user_id,
                "display_name": target_display,
                "status": current_effective,
            },
        )

    return {
        "user_id": user_id,
        "display_name": target_display,
        "status": current_effective,
        "raw_status": target_status,
        "languages": target_languages,
        "teams": target_teams,
        "current_call_id": target_call_id,
        "last_seen": now,
        "updated_at": now,
    }


def list_officers() -> list[dict[str, Any]]:
    """List all officers with computed current status."""
    rows = db.query_all("SELECT * FROM officer_presence ORDER BY updated_at DESC")
    now = time.time()
    results: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        last_seen = float(d.get("last_seen") or 0.0)
        raw_status = str(d.get("status") or "offline")
        d["status"] = _effective_status(raw_status, last_seen, now)
        try:
            d["languages"] = json.loads(d.get("languages_json") or '["en"]')
        except Exception:
            d["languages"] = ["en"]
        try:
            d["teams"] = json.loads(d.get("teams_json") or "[]")
        except Exception:
            d["teams"] = []
        results.append(d)
    return results


def get_presence_board() -> dict[str, Any]:
    """Retrieve full presence board including list of officers and available teams."""
    return {
        "officers": list_officers(),
        "teams": known_teams(),
    }


def set_on_call(user_id: str, call_id: str, display_name: str = "") -> None:
    """Transition an officer to 'on_call'."""
    existing = get_presence(user_id)
    prev = "available"
    if existing and existing.get("raw_status") in ("available", "busy", "away"):
        prev = existing["raw_status"]
    name = display_name or (existing["display_name"] if existing else "")
    upsert_presence(user_id, display_name=name or None, status="on_call", current_call_id=call_id, previous_status=prev)


def set_wrap_up(user_id: str, call_id: str = "") -> None:
    """Transition an officer to 'wrap_up'."""
    upsert_presence(user_id, status="wrap_up", current_call_id=call_id)


def finish_wrap_up(user_id: str) -> None:
    """Restore an officer's presence to their pre-call status."""
    existing = get_presence(user_id)
    target = existing.get("previous_status", "available") if existing else "available"
    if target not in ("available", "busy", "away"):
        target = "available"
    upsert_presence(user_id, status=target, current_call_id="")

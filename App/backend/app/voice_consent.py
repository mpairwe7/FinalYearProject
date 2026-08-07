"""Voice consent & governance layer (2026).

Extends the existing consent_receipts infrastructure with voice-specific
purposes, retention policies, and an immutable audit log for regulatory
compliance (NDPA 2019, EU AI Act voice provisions).

Privacy design:

* Raw audio is **never stored** by default — only a SHA-256 hash is
  recorded in the audit trail for tamper-evidence.
* ``VOICE_STORE_RAW_AUDIO=true`` enables temporary storage with TTL
  cleanup (default 24h).
* Transcripts follow the existing ``CONVERSATION_TTL_DAYS`` policy.
* Voice audit entries are retained for ``ANALYTICS_TTL_DAYS`` (365d).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass

from .flags import flags

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VOICE_RAW_AUDIO_TTL_H = int(os.getenv("VOICE_RAW_AUDIO_TTL_H", "24"))
VOICE_TRANSCRIPT_TTL_DAYS = int(os.getenv("VOICE_TRANSCRIPT_TTL_DAYS", "90"))
VOICE_ANALYTICS_TTL_DAYS = int(os.getenv("VOICE_ANALYTICS_TTL_DAYS", "365"))
VOICE_STORE_RAW_AUDIO = os.getenv("VOICE_STORE_RAW_AUDIO", "false").lower() == "true"

# Consent purposes used by voice features
VOICE_CONSENT_PURPOSES = ("voice_recording", "voice_analytics")

# Valid event types for the voice audit log
VOICE_EVENT_TYPES = frozenset({
    "recording_start",
    "recording_end",
    "transcript_stored",
    "audio_deleted",
    "consent_checked",
    "consent_denied",
    "retention_cleanup",
    "session_start",
    "session_end",
    "barge_in",
})


@dataclass(frozen=True)
class VoiceRetentionPolicy:
    """Retention windows for voice data."""

    raw_audio_ttl_hours: int = VOICE_RAW_AUDIO_TTL_H
    transcript_ttl_days: int = VOICE_TRANSCRIPT_TTL_DAYS
    analytics_ttl_days: int = VOICE_ANALYTICS_TTL_DAYS


# Module-level retention policy
retention_policy = VoiceRetentionPolicy()


# ---------------------------------------------------------------------------
# Schema init
# ---------------------------------------------------------------------------


def init_voice_consent_schema() -> None:
    """Create the ``voice_audit_log`` table if it doesn't exist.

    Called from the FastAPI lifespan after ``init_db()``.
    """
    try:
        from . import database as db

        db.execute_script(
            """
            CREATE TABLE IF NOT EXISTS voice_audit_log (
                id              TEXT PRIMARY KEY,
                user_id         TEXT DEFAULT '',
                session_id      TEXT NOT NULL,
                event_type      TEXT NOT NULL,
                metadata_json   TEXT DEFAULT '{}',
                audio_hash      TEXT DEFAULT '',
                tenant_id       TEXT DEFAULT 'default',
                created_at      DOUBLE PRECISION NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_voice_audit_session
                ON voice_audit_log(session_id);
            CREATE INDEX IF NOT EXISTS idx_voice_audit_user
                ON voice_audit_log(user_id);
            CREATE INDEX IF NOT EXISTS idx_voice_audit_created
                ON voice_audit_log(created_at);
            """
        )
        logger.info("Voice audit schema ready")
    except Exception:
        logger.exception("Failed to init voice consent schema")


# ---------------------------------------------------------------------------
# Consent checks
# ---------------------------------------------------------------------------


def require_voice_consent(user_id: str) -> bool:
    """Check if user has granted ``voice_recording`` consent.

    Returns ``True`` if:
    - ``FLAG_VOICE_CONSENT`` is disabled (consent not enforced), OR
    - The user has an active ``voice_recording`` consent receipt.

    Returns ``False`` if consent is required but not granted.
    """
    if not flags.is_enabled("voice_consent"):
        return True

    if not user_id:
        # Anonymous users cannot have consent records
        return False

    try:
        from . import database as db

        return db.has_active_consent(user_id, "voice_recording")
    except Exception:
        logger.exception("Consent check failed for user=%s", user_id)
        return False


def grant_voice_consent(
    user_id: str,
    purpose: str = "voice_recording",
    version: str = "1.0",
    tenant_id: str = "default",
) -> str | None:
    """Grant voice-specific consent.

    Returns the consent receipt ID, or None on failure.
    """
    if purpose not in VOICE_CONSENT_PURPOSES:
        logger.warning("Invalid voice consent purpose: %s", purpose)
        return None

    try:
        from . import database as db

        receipt_id = db.grant_consent(
            user_id=user_id,
            purpose=purpose,
            version=version,
            tenant_id=tenant_id,
        )
        log_voice_event(
            user_id=user_id,
            session_id="consent_grant",
            event_type="consent_checked",
            metadata={"purpose": purpose, "version": version, "granted": True},
            tenant_id=tenant_id,
        )
        return receipt_id
    except Exception:
        logger.exception("Failed to grant voice consent")
        return None


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


def log_voice_event(
    user_id: str,
    session_id: str,
    event_type: str,
    metadata: dict | None = None,
    audio_hash: str = "",
    tenant_id: str = "default",
) -> str | None:
    """Write an entry to the voice_audit_log table.

    Also chains into the existing AuditLedger (if the ``audit_ledger``
    flag is enabled) for tamper-evident audit trails.

    Returns the voice_audit_log row ID, or None on failure.
    """
    if event_type not in VOICE_EVENT_TYPES:
        logger.warning("Unknown voice event type: %s", event_type)

    row_id = str(uuid.uuid4())
    now = time.time()
    meta_json = json.dumps(metadata or {})

    try:
        from . import database as db

        db.execute(
            """
            INSERT INTO voice_audit_log
                (id, user_id, session_id, event_type, metadata_json,
                 audio_hash, tenant_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (row_id, user_id, session_id, event_type, meta_json, audio_hash, tenant_id, now),
        )
    except Exception:
        logger.exception("Failed to write voice audit event")
        return None

    # Chain into AuditLedger if available
    if flags.is_enabled("audit_ledger"):
        try:
            from .audit import get_ledger

            get_ledger().append(
                event_type=f"voice_{event_type}",
                payload={
                    "voice_audit_id": row_id,
                    "session_id": session_id,
                    "user_id": user_id,
                    "audio_hash": audio_hash,
                    **(metadata or {}),
                },
                tenant_id=tenant_id,
            )
        except Exception:
            logger.debug("AuditLedger chain failed for voice event", exc_info=True)

    return row_id


def hash_audio(audio_bytes: bytes) -> str:
    """Compute SHA-256 hash of audio for audit trail."""
    return hashlib.sha256(audio_bytes).hexdigest()


# ---------------------------------------------------------------------------
# Audit queries
# ---------------------------------------------------------------------------


def get_voice_audit_log(
    user_id: str | None = None,
    session_id: str | None = None,
    since: float | None = None,
    limit: int = 100,
) -> list[dict]:
    """Query voice audit entries for the admin endpoint."""
    try:
        from . import database as db

        clauses: list[str] = []
        params: list = []

        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if since:
            clauses.append("created_at >= ?")
            params.append(since)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT id, user_id, session_id, event_type,
                   metadata_json, audio_hash, tenant_id, created_at
            FROM voice_audit_log
            {where}
            ORDER BY created_at DESC
            LIMIT ?
        """
        params.append(limit)

        rows = db.query_all(query, tuple(params))
        return [
            {
                "id": r["id"],
                "user_id": r["user_id"],
                "session_id": r["session_id"],
                "event_type": r["event_type"],
                "metadata": json.loads(r["metadata_json"] or "{}"),
                "audio_hash": r["audio_hash"],
                "tenant_id": r["tenant_id"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    except Exception:
        logger.exception("Failed to query voice audit log")
        return []


def voice_audit_stats(days: int = 30) -> dict:
    """Aggregate voice audit statistics for the admin dashboard."""
    try:
        from . import database as db

        cutoff = time.time() - (days * 86400)

        total_row = db.query_one(
            "SELECT COUNT(*) AS n FROM voice_audit_log WHERE created_at >= ?",
            (cutoff,),
        )
        total = int(total_row["n"]) if total_row else 0

        by_type = db.query_all(
            """
            SELECT event_type, COUNT(*) AS n
            FROM voice_audit_log
            WHERE created_at >= ?
            GROUP BY event_type
            ORDER BY COUNT(*) DESC
            """,
            (cutoff,),
        )

        sessions_row = db.query_one(
            "SELECT COUNT(DISTINCT session_id) AS n FROM voice_audit_log "
            "WHERE created_at >= ?",
            (cutoff,),
        )
        users_row = db.query_one(
            "SELECT COUNT(DISTINCT user_id) AS n FROM voice_audit_log "
            "WHERE created_at >= ? AND user_id != ''",
            (cutoff,),
        )

        return {
            "period_days": days,
            "total_events": total,
            "unique_sessions": int(sessions_row["n"]) if sessions_row else 0,
            "unique_users": int(users_row["n"]) if users_row else 0,
            "events_by_type": {r["event_type"]: r["n"] for r in by_type},
        }
    except Exception:
        logger.exception("Failed to compute voice audit stats")
        return {"period_days": days, "total_events": 0}


# ---------------------------------------------------------------------------
# Retention cleanup
# ---------------------------------------------------------------------------


def cleanup_expired_voice_data() -> dict[str, int]:
    """Delete voice audit entries older than the analytics TTL.

    Returns counts of deleted rows.
    """
    deleted = {"audit_entries": 0}
    try:
        from . import database as db

        cutoff = time.time() - (retention_policy.analytics_ttl_days * 86400)

        deleted["audit_entries"] = db.execute(
            "DELETE FROM voice_audit_log WHERE created_at < ?",
            (cutoff,),
        )

        if deleted["audit_entries"] > 0:
            logger.info("Voice retention cleanup: deleted %d audit entries", deleted["audit_entries"])
            log_voice_event(
                user_id="system",
                session_id="retention_cleanup",
                event_type="retention_cleanup",
                metadata=deleted,
            )
    except Exception:
        logger.exception("Voice retention cleanup failed")

    return deleted

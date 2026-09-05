"""Analytics persistence layer.

Two backends are available; selected via ``ANALYTICS_BACKEND``:

- **sqlite** (default) — thread-safe via thread-local connections with
  WAL journaling.  Fine for single-node / single-worker deploys; will
  lock-contend across replicas.
- **postgres** — see :mod:`postgres`.  Correct choice for multi-replica
  deploys.  Enable with ``ANALYTICS_BACKEND=postgres`` and ``POSTGRES_DSN``.

All write operations are wrapped in try/except to prevent data loss.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
import uuid
import json
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backend selection — import early so the dispatch module aliases below pick
# up the right implementation.  Falls back to SQLite if psycopg missing.
# ---------------------------------------------------------------------------
ANALYTICS_BACKEND = os.getenv("ANALYTICS_BACKEND", "sqlite").lower()

from ._root import PROJECT_ROOT as _PROJECT_ROOT
_DB_DIR = Path(os.getenv("ANALYTICS_DB_DIR", str(_PROJECT_ROOT / "data_store")))
_DB_PATH = _DB_DIR / "analytics.db"

# Retention TTLs (days) — enforced by cleanup_expired_data()
_CONVERSATION_TTL_DAYS = int(os.getenv("CONVERSATION_TTL_DAYS", "7"))
_ANALYTICS_TTL_DAYS = int(os.getenv("ANALYTICS_TTL_DAYS", "365"))
_FEEDBACK_TTL_DAYS = int(os.getenv("FEEDBACK_TTL_DAYS", "90"))
_SESSION_TTL_DAYS = int(os.getenv("SESSION_TTL_DAYS", "30"))
_TICKET_TTL_DAYS = int(os.getenv("TICKET_TTL_DAYS", "90"))

# Thread-local storage for connections with a lock for init safety
_local = threading.local()
_init_lock = threading.Lock()


def _get_connection() -> sqlite3.Connection:
    """Return a thread-local SQLite connection (safe per-thread singleton)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        with _init_lock:
            # Double-check after acquiring lock
            conn = getattr(_local, "conn", None)
            if conn is None:
                _DB_DIR.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(_DB_PATH), timeout=10)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                _local.conn = conn
    return conn


# ---------------------------------------------------------------------------
# Backend-agnostic query helpers
#
# Modules that own their own tables — the audit ledger, semantic and
# episodic memory, voice consent — reached ``_get_connection()`` directly
# and wrote SQLite SQL against it.  That bypasses the dispatch block
# entirely, so on Postgres deployments those tables stayed on a
# per-replica file no matter what the backend setting said.  The audit
# ledger is the sharp case: a hash chain split across pods cannot be
# verified, which is the one thing it exists to do.
#
# These helpers are the seam.  Callers write ``?`` placeholders and get
# dicts back; the Postgres path rewrites the placeholders and reuses the
# pool.  One implementation of each query, whichever backend is live.
# ---------------------------------------------------------------------------
def _pg_module() -> Any | None:
    """The postgres module when it is the active backend, else ``None``."""
    if ANALYTICS_BACKEND != "postgres":
        return None
    try:
        from . import postgres as _pg
    except Exception:  # pragma: no cover - import guarded at dispatch too
        return None
    return _pg if _pg._get_pool() is not None else None


def _to_pg_placeholders(sql: str) -> str:
    """Rewrite ``?`` placeholders to ``%s``.

    Only bare ``?`` is used as a placeholder in this codebase; a literal
    question mark inside a quoted string would need escaping, and none
    of the call sites have one.  :func:`test_sql_portability` asserts
    that stays true.
    """
    return sql.replace("?", "%s")


def query_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Run a SELECT and return rows as dicts, on whichever backend is live."""
    pg = _pg_module()
    if pg is None:
        rows = _get_connection().execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    pool = pg._get_pool()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(_to_pg_placeholders(sql), params)
        columns = [d[0] for d in cur.description or []]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def query_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    rows = query_all(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    """Run a write and return the affected row count, committing."""
    pg = _pg_module()
    if pg is None:
        conn = _get_connection()
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.rowcount
        except Exception:
            conn.rollback()
            raise
    pool = pg._get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_to_pg_placeholders(sql), params)
            affected = cur.rowcount
        conn.commit()
    return affected


def execute_script(sql: str) -> None:
    """Run a multi-statement DDL script (schema bootstrap).

    SQLite needs ``executescript``; psycopg accepts several statements in
    one ``execute``.  Types differ between the dialects, so a caller with
    backend-specific DDL should pass the portable subset — INTEGER, TEXT
    and REAL/DOUBLE PRECISION all parse on both.
    """
    pg = _pg_module()
    if pg is None:
        conn = _get_connection()
        conn.executescript(sql)
        conn.commit()
        return
    pool = pg._get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    ddl: str,
) -> None:
    """Add *column* to *table* if it is missing.

    SQLite only gained ``ADD COLUMN IF NOT EXISTS`` recently, so we do
    the compatibility check ourselves to support older runtimes.
    """
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()  # noqa: S608 - fixed table name
    names = {row["name"] if isinstance(row, sqlite3.Row) else row[1] for row in rows}
    if column in names:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")  # noqa: S608 - fixed identifiers
    logger.info("Added missing column %s.%s", table, column)


def consent_purposes() -> tuple[str, ...]:
    """The allowed consent purposes, from the one place that defines them.

    The schema used to repeat this list inside a CHECK constraint, so
    adding a purpose to the enum left the database rejecting it. Derived
    here instead: the enum is the source of truth, and the constraint
    follows it.
    """
    from typing import get_args

    from .auth.models import ConsentPurpose

    return tuple(str(p) for p in get_args(ConsentPurpose))


def _CONSENT_PURPOSE_SQL(ddl: str) -> str:  # noqa: N802 - reads as a constant substitution
    quoted = ",".join(f"'{p}'" for p in consent_purposes())
    return ddl.replace("__CONSENT_PURPOSES__", quoted)


def _refresh_consent_purpose_check(conn: sqlite3.Connection) -> None:
    """Rebuild consent_receipts when its CHECK list has fallen behind.

    SQLite cannot alter a CHECK constraint, so an existing database keeps
    whichever list it was created with and rejects any purpose added
    later. Rebuild-and-copy is the standard remedy; it runs only when the
    stored DDL is actually stale, so the normal path costs one PRAGMA.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='consent_receipts'"
    ).fetchone()
    if row is None:
        return
    stored = row["sql"] if isinstance(row, sqlite3.Row) else row[0]
    missing = [p for p in consent_purposes() if f"'{p}'" not in (stored or "")]
    if not missing:
        return
    logger.info("Rebuilding consent_receipts CHECK for new purposes: %s", missing)
    try:
        conn.executescript(
            _CONSENT_PURPOSE_SQL("""
            PRAGMA foreign_keys=OFF;
            CREATE TABLE consent_receipts__new (
                receipt_id    TEXT PRIMARY KEY,
                user_id       TEXT NOT NULL
                              REFERENCES users(id) ON DELETE CASCADE,
                purpose       TEXT NOT NULL
                              CHECK(purpose IN (__CONSENT_PURPOSES__)),
                version       TEXT NOT NULL,
                granted_at    REAL NOT NULL,
                withdrawn_at  REAL,
                legal_basis   TEXT NOT NULL DEFAULT 'consent'
                              CHECK(legal_basis IN ('consent','public_task','legal_obligation'))
            );
            INSERT INTO consent_receipts__new
                SELECT receipt_id, user_id, purpose, version, granted_at,
                       withdrawn_at, legal_basis FROM consent_receipts;
            DROP TABLE consent_receipts;
            ALTER TABLE consent_receipts__new RENAME TO consent_receipts;
            PRAGMA foreign_keys=ON;
            """)
        )
        conn.commit()
    except Exception:
        logger.exception("consent_receipts CHECK rebuild failed")
        conn.rollback()


def init_db() -> None:
    """Create tables if they don't exist."""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = _get_connection()

    conn.executescript(_CONSENT_PURPOSE_SQL("""
        CREATE TABLE IF NOT EXISTS feedback (
            id          TEXT PRIMARY KEY,
            message_id  TEXT NOT NULL,
            session_id  TEXT,
            user_id     TEXT DEFAULT '',
            rating      TEXT NOT NULL CHECK(rating IN ('up', 'down')),
            comment     TEXT DEFAULT '',
            user_query  TEXT DEFAULT '',
            bot_reply   TEXT DEFAULT '',
            created_at  REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS analytics_events (
            id          TEXT PRIMARY KEY,
            session_id  TEXT,
            user_id     TEXT DEFAULT '',
            event_type  TEXT NOT NULL,
            event_data  TEXT DEFAULT '{}',
            created_at  REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id              TEXT PRIMARY KEY,
            user_id         TEXT DEFAULT '',
            started_at      REAL NOT NULL,
            last_active_at  REAL NOT NULL,
            message_count   INTEGER DEFAULT 0,
            user_agent      TEXT DEFAULT '',
            platform        TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id              TEXT PRIMARY KEY,
            conversation_id TEXT,
            session_id      TEXT,
            user_message    TEXT NOT NULL,
            bot_reply       TEXT NOT NULL,
            sources         TEXT DEFAULT '[]',
            response_time_ms REAL DEFAULT 0,
            confidence      REAL DEFAULT 0,
            topic_tag       TEXT DEFAULT '',
            created_at      REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS conversation_topics (
            conversation_id TEXT PRIMARY KEY,
            topic_id        TEXT NOT NULL,
            label           TEXT NOT NULL,
            tax_type        TEXT DEFAULT '',
            confidence      REAL DEFAULT 0,
            updated_at      REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workflow_sessions (
            conversation_id TEXT PRIMARY KEY,
            workflow_id     TEXT NOT NULL,
            status          TEXT NOT NULL
                            CHECK(status IN ('active','completed','cancelled'))
                            DEFAULT 'active',
            current_step_idx INTEGER NOT NULL DEFAULT 0,
            slots_json      TEXT DEFAULT '{}',
            last_prompt     TEXT DEFAULT '',
            created_at      REAL NOT NULL,
            updated_at      REAL NOT NULL
        );

        -- Phase 14 (2026) — identity, tenancy, and consent.
        -- Tenants are a first-class concept for RLS and
        -- multi-tenant isolation.  One tenant per URA deployment
        -- or partner agency (KCCA, NSSF, etc).
        CREATE TABLE IF NOT EXISTS tenants (
            id            TEXT PRIMARY KEY,
            display_name  TEXT NOT NULL,
            created_at    REAL NOT NULL
        );

        -- Users map from OIDC `sub` claim to an internal id.
        -- One row per (tenant_id, external_id).  Role is the
        -- primary RBAC key.
        CREATE TABLE IF NOT EXISTS users (
            id            TEXT PRIMARY KEY,
            tenant_id     TEXT NOT NULL DEFAULT 'default',
            external_id   TEXT NOT NULL,
            email         TEXT DEFAULT '',
            role          TEXT NOT NULL DEFAULT 'public'
                          CHECK(role IN ('public','verified_taxpayer','ura_staff','ura_admin','ura_auditor')),
            created_at    REAL NOT NULL,
            last_seen_at  REAL NOT NULL,
            UNIQUE(tenant_id, external_id)
        );

        -- User profile — JSON blob for easy evolution, indexed via
        -- SQL only on fields we actually filter on.  Stored as TEXT
        -- in SQLite; JSONB in postgres.py mirror.
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id            TEXT PRIMARY KEY
                               REFERENCES users(id) ON DELETE CASCADE,
            taxpayer_type      TEXT DEFAULT 'unknown',
            industry           TEXT DEFAULT '',
            primary_language   TEXT DEFAULT 'en',
            detail_level       TEXT DEFAULT 'intermediate',
            registered_tax_types TEXT DEFAULT '[]',
            fiscal_year        TEXT DEFAULT 'FY2025-26',
            display_name       TEXT DEFAULT '',
            updated_at         REAL NOT NULL
        );

        -- Consent receipts — append-only.  Withdrawal is a new row
        -- with withdrawn_at set.  One active row per (user_id, purpose, version).
        CREATE TABLE IF NOT EXISTS consent_receipts (
            receipt_id    TEXT PRIMARY KEY,
            user_id       TEXT NOT NULL
                          REFERENCES users(id) ON DELETE CASCADE,
            purpose       TEXT NOT NULL
                          CHECK(purpose IN (__CONSENT_PURPOSES__)),
            version       TEXT NOT NULL,
            granted_at    REAL NOT NULL,
            withdrawn_at  REAL,
            legal_basis   TEXT NOT NULL DEFAULT 'consent'
                          CHECK(legal_basis IN ('consent','public_task','legal_obligation'))
        );

        -- Phase 14-D — ticket queue for escalations.  Each ticket is
        -- one human-required conversation the supervisor routed out
        -- of the automated pipeline.  Staff work them via the admin
        -- endpoints in main.py (/v1/admin/tickets).
        CREATE TABLE IF NOT EXISTS tickets (
            id             TEXT PRIMARY KEY,
            conversation_id TEXT,
            session_id     TEXT,
            status         TEXT NOT NULL
                           CHECK(status IN ('open','assigned','resolved','wontfix'))
                           DEFAULT 'open',
            priority       TEXT NOT NULL
                           CHECK(priority IN ('low','normal','high','urgent'))
                           DEFAULT 'normal',
            reason         TEXT DEFAULT '',
            user_query     TEXT DEFAULT '',
            bot_reply      TEXT DEFAULT '',
            handoff_json   TEXT DEFAULT '{}',
            response_judge_json TEXT DEFAULT '{}',
            -- Snapshot of the conversation at the moment of escalation.
            -- Not a join to `conversations`: that table is purged after
            -- CONVERSATION_TTL_DAYS (7), while a ticket can sit in the
            -- queue far longer, so a live join would hand the officer an
            -- empty transcript for any week-old ticket.
            transcript_json TEXT DEFAULT '[]',
            -- Erasure used to reach a ticket only via `conversations`,
            -- which is purged after CONVERSATION_TTL_DAYS — so a ticket
            -- older than that survived an NDPA erasure request while
            -- still holding the taxpayer's transcript.  Stamping the
            -- subject here makes erasure independent of that purge.
            user_id        TEXT DEFAULT '',
            -- Which team owns this escalation.  `_handoff_topic` already
            -- classified every ticket; nothing acted on it, so officers
            -- triaged a mixed queue by reading each row.
            team           TEXT DEFAULT '',
            -- Phase 18 round trip: what the officer wants the taxpayer
            -- to see, and whether they have seen it.  Without this the
            -- escalation loop is one-way — a resolved ticket never
            -- reaches the person who raised it.
            officer_reply  TEXT DEFAULT '',
            reply_at       REAL DEFAULT 0,
            reply_delivered_at REAL DEFAULT 0,
            -- SLA: first officer touch, and resolution.
            first_response_at  REAL DEFAULT 0,
            resolved_at        REAL DEFAULT 0,
            assignee       TEXT DEFAULT '',
            staff_note     TEXT DEFAULT '',
            created_at     REAL NOT NULL,
            updated_at     REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ticket_presence (
            ticket_id  TEXT NOT NULL,
            viewer     TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (ticket_id, viewer)
        );

        CREATE TABLE IF NOT EXISTS flag_overrides (
            name       TEXT PRIMARY KEY,
            enabled    INTEGER NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reminder_inbox (
            id            TEXT PRIMARY KEY,
            user_id       TEXT NOT NULL,
            deadline_name TEXT NOT NULL,
            due_date      TEXT NOT NULL,
            message       TEXT NOT NULL,
            created_at    REAL NOT NULL,
            read_at       REAL DEFAULT 0,
            UNIQUE(user_id, deadline_name, due_date)
        );

        CREATE TABLE IF NOT EXISTS notification_outbox (
            id         TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL,
            channel    TEXT NOT NULL,
            provider   TEXT NOT NULL DEFAULT 'mock',
            payload    TEXT NOT NULL,
            status     TEXT NOT NULL DEFAULT 'queued',
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS answer_overrides (
            id           TEXT PRIMARY KEY,
            match_query  TEXT NOT NULL UNIQUE,
            reply        TEXT NOT NULL,
            source_url   TEXT DEFAULT '',
            created_by   TEXT DEFAULT '',
            enabled      INTEGER NOT NULL DEFAULT 1,
            updated_at   REAL NOT NULL
        );

        -- Memory + audit tables (memory/semantic.py, memory/episodic.py,
        -- audit/ledger.py). Part of the shared analytics schema: those
        -- stores are process-wide singletons that only create their tables
        -- on the connection active at FIRST construction, so every freshly
        -- initialised DB must already carry them or /v1/me export/erasure
        -- and audit reads break on any other connection.
        CREATE TABLE IF NOT EXISTS user_facts (
            fact_id          TEXT PRIMARY KEY,
            user_id          TEXT NOT NULL,
            tenant_id        TEXT NOT NULL DEFAULT 'default',
            category         TEXT NOT NULL,
            subject          TEXT NOT NULL DEFAULT 'user',
            predicate        TEXT NOT NULL,
            object_value     TEXT NOT NULL,
            confidence       REAL NOT NULL DEFAULT 0.5,
            extracted_at     REAL NOT NULL,
            conversation_id  TEXT DEFAULT '',
            turn_id          TEXT DEFAULT '',
            extractor_model  TEXT DEFAULT '',
            superseded_by    TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_facts_user
            ON user_facts(user_id);
        CREATE INDEX IF NOT EXISTS idx_facts_user_category
            ON user_facts(user_id, category);
        CREATE INDEX IF NOT EXISTS idx_facts_extracted
            ON user_facts(extracted_at);

        CREATE TABLE IF NOT EXISTS episodic_summaries (
            summary_id      TEXT PRIMARY KEY,
            user_id         TEXT NOT NULL,
            tenant_id       TEXT NOT NULL DEFAULT 'default',
            conversation_id TEXT NOT NULL,
            summary         TEXT NOT NULL,
            topic_tag       TEXT DEFAULT '',
            sentiment       TEXT DEFAULT 'neutral',
            turn_count      INTEGER DEFAULT 0,
            created_at      REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_episodic_user
            ON episodic_summaries(user_id);
        CREATE INDEX IF NOT EXISTS idx_episodic_created
            ON episodic_summaries(created_at);
        CREATE INDEX IF NOT EXISTS idx_episodic_topic
            ON episodic_summaries(user_id, topic_tag);

        -- Long-running MCP work.  The 2026-07-28 protocol is stateless —
        -- no session, any request may land on any replica — so work that
        -- outlives one request cannot hold a connection and must be
        -- addressable by id from anywhere.  That is what this table is:
        -- the state a `tasks/get` poll reads, not a job queue.
        CREATE TABLE IF NOT EXISTS mcp_tasks (
            id             TEXT PRIMARY KEY,
            tenant_id      TEXT NOT NULL DEFAULT 'default',
            user_id        TEXT DEFAULT '',
            kind           TEXT NOT NULL,
            status         TEXT NOT NULL
                           CHECK(status IN ('pending','running','succeeded','failed','cancelled'))
                           DEFAULT 'pending',
            progress       REAL NOT NULL DEFAULT 0.0,
            args_json      TEXT DEFAULT '{}',
            result_json    TEXT DEFAULT '{}',
            error          TEXT DEFAULT '',
            -- Tenant-scoped, matching the MCP client's replay cache: a
            -- retried submission must return the first task rather than
            -- start a second, and two tenants must never collide on the
            -- same caller-chosen key.
            --
            -- NULL, not '', when the caller supplies no key. Both SQLite
            -- and Postgres treat NULLs as distinct in a UNIQUE index, so
            -- unkeyed tasks coexist; an empty string would make them all
            -- collide on ('default','') and allow exactly one per tenant.
            idempotency_key TEXT DEFAULT NULL,
            created_at     REAL NOT NULL,
            updated_at     REAL NOT NULL,
            UNIQUE(tenant_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS idx_mcp_tasks_status
            ON mcp_tasks(tenant_id, status, created_at);

        CREATE TABLE IF NOT EXISTS audit_events (
            event_id     TEXT PRIMARY KEY,
            event_type   TEXT NOT NULL,
            tenant_id    TEXT NOT NULL DEFAULT 'default',
            user_id      TEXT DEFAULT '',
            payload      TEXT NOT NULL,
            ts           REAL NOT NULL,
            seq          INTEGER NOT NULL,
            prev_hash    TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            row_hash     TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(ts);
        CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_events(user_id);
        CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_events(tenant_id, ts);
        CREATE INDEX IF NOT EXISTS idx_audit_seq ON audit_events(seq);

        CREATE TABLE IF NOT EXISTS audit_anchors (
            anchor_id    TEXT PRIMARY KEY,
            tenant_id    TEXT NOT NULL DEFAULT 'default',
            first_seq    INTEGER NOT NULL,
            last_seq     INTEGER NOT NULL,
            merkle_root  TEXT NOT NULL,
            created_at   REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_audit_anchors_created
            ON audit_anchors(created_at);
        CREATE INDEX IF NOT EXISTS idx_feedback_message ON feedback(message_id);
        CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at);
        CREATE INDEX IF NOT EXISTS idx_events_type ON analytics_events(event_type);
        CREATE INDEX IF NOT EXISTS idx_events_session ON analytics_events(session_id);
        CREATE INDEX IF NOT EXISTS idx_events_created ON analytics_events(created_at);
        CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(last_active_at);
        CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id);
        CREATE INDEX IF NOT EXISTS idx_conversations_created ON conversations(created_at);
        CREATE INDEX IF NOT EXISTS idx_workflow_status ON workflow_sessions(status);
        CREATE INDEX IF NOT EXISTS idx_workflow_updated ON workflow_sessions(updated_at);
        CREATE INDEX IF NOT EXISTS idx_tickets_status    ON tickets(status);
        CREATE INDEX IF NOT EXISTS idx_tickets_priority  ON tickets(priority);
        CREATE INDEX IF NOT EXISTS idx_tickets_created   ON tickets(created_at);
        CREATE INDEX IF NOT EXISTS idx_users_tenant      ON users(tenant_id);
        CREATE INDEX IF NOT EXISTS idx_users_last_seen   ON users(last_seen_at);
        CREATE INDEX IF NOT EXISTS idx_consent_user      ON consent_receipts(user_id);
        CREATE INDEX IF NOT EXISTS idx_consent_active    ON consent_receipts(user_id, purpose, withdrawn_at);
    """))

    # Forward-compatible schema migrations for existing DBs.
    _refresh_consent_purpose_check(conn)
    _ensure_column(conn, "conversations", "conversation_id", "TEXT")
    conn.execute(
        "UPDATE conversations SET conversation_id = id WHERE conversation_id IS NULL OR conversation_id = ''"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_thread ON conversations(conversation_id)")
    _ensure_column(conn, "tickets", "handoff_json", "TEXT DEFAULT '{}'")
    _ensure_column(conn, "tickets", "response_judge_json", "TEXT DEFAULT '{}'")
    _ensure_column(conn, "tickets", "transcript_json", "TEXT DEFAULT '[]'")
    _ensure_column(conn, "tickets", "user_id", "TEXT DEFAULT ''")
    _ensure_column(conn, "tickets", "officer_reply", "TEXT DEFAULT ''")
    _ensure_column(conn, "tickets", "reply_at", "REAL DEFAULT 0")
    _ensure_column(conn, "tickets", "reply_delivered_at", "REAL DEFAULT 0")
    _ensure_column(conn, "tickets", "first_response_at", "REAL DEFAULT 0")
    _ensure_column(conn, "tickets", "resolved_at", "REAL DEFAULT 0")
    _ensure_column(conn, "tickets", "team", "TEXT DEFAULT ''")
    # P0-2: persist the top-k retrieved passage texts per turn so the eval
    # harness scores faithfulness against the real context, not the answer.
    _ensure_column(conn, "conversations", "contexts", "TEXT DEFAULT '[]'")
    # Phase 14 — link chat history to the authenticated user (OIDC `sub`) so
    # /v1/me export + erasure can reach it.  Empty string for anonymous turns.
    _ensure_column(conn, "conversations", "user_id", "TEXT DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id)")
    _ensure_column(conn, "conversations", "flag_variants", "TEXT DEFAULT '{}'")
    _ensure_column(conn, "conversations", "locale", "TEXT DEFAULT ''")
    _ensure_column(conn, "feedback", "user_id", "TEXT DEFAULT ''")
    _ensure_column(conn, "analytics_events", "user_id", "TEXT DEFAULT ''")
    _ensure_column(conn, "sessions", "user_id", "TEXT DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_user ON feedback(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_user ON analytics_events(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")

    # Seed the default tenant if missing
    conn.execute(
        "INSERT OR IGNORE INTO tenants (id, display_name, created_at) VALUES (?, ?, ?)",
        ("default", "URA Default Tenant", time.time()),
    )
    conn.commit()
    logger.info("Analytics database initialised at %s", _DB_PATH)

    # Run cleanup on startup
    cleanup_expired_data()


# ---------------------------------------------------------------------------
# Retention TTL enforcement
# ---------------------------------------------------------------------------
def cleanup_expired_data() -> dict[str, int]:
    """Delete rows older than configured TTLs.  Returns counts deleted."""
    conn = _get_connection()
    now = time.time()
    deleted: dict[str, int] = {}

    ttls = [
        ("conversations", _CONVERSATION_TTL_DAYS),
        ("analytics_events", _ANALYTICS_TTL_DAYS),
        ("feedback", _FEEDBACK_TTL_DAYS),
        ("sessions", _SESSION_TTL_DAYS),
        ("workflow_sessions", _CONVERSATION_TTL_DAYS),
        ("conversation_topics", _CONVERSATION_TTL_DAYS),
        ("ticket_presence", 1),
        ("tickets", _TICKET_TTL_DAYS),
    ]
    ts_col = {
        "conversations": "created_at",
        "analytics_events": "created_at",
        "feedback": "created_at",
        "sessions": "last_active_at",
        "workflow_sessions": "updated_at",
        "conversation_topics": "updated_at",
        "ticket_presence": "updated_at",
        "tickets": "resolved_at",
    }

    for table, ttl_days in ttls:
        cutoff = now - (ttl_days * 86400)
        col = ts_col[table]
        try:
            if table == "tickets":
                cursor = conn.execute(
                    "DELETE FROM tickets WHERE status IN ('resolved', 'wontfix') "
                    "AND resolved_at > 0 AND resolved_at < ?",
                    (cutoff,),
                )
            else:
                cursor = conn.execute(
                    f"DELETE FROM {table} WHERE {col} < ?",  # noqa: S608 — table/col are hardcoded above
                    (cutoff,),
                )
            conn.commit()
            deleted[table] = cursor.rowcount
            if cursor.rowcount > 0:
                logger.info(
                    "TTL cleanup: deleted %d rows from %s (>%dd)", cursor.rowcount, table, ttl_days
                )
        except Exception:
            logger.exception("TTL cleanup failed for %s", table)
            conn.rollback()
            deleted[table] = 0

    return deleted


# ---------------------------------------------------------------------------
# Feedback CRUD
# ---------------------------------------------------------------------------
def save_feedback(
    message_id: str,
    rating: str,
    comment: str = "",
    session_id: str | None = None,
    user_query: str = "",
    bot_reply: str = "",
    user_id: str = "",
) -> dict[str, Any]:
    """Persist a feedback entry and return it."""
    from .guardrails import redact_pii_text

    comment = redact_pii_text(comment)
    user_query = redact_pii_text(user_query)
    bot_reply = redact_pii_text(bot_reply)
    conn = _get_connection()
    fb_id = str(uuid.uuid4())
    now = time.time()
    try:
        conn.execute(
            """INSERT INTO feedback (id, message_id, session_id, user_id, rating, comment,
               user_query, bot_reply, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fb_id, message_id, session_id, user_id or "", rating, comment, user_query, bot_reply, now),
        )
        conn.commit()
    except Exception:
        logger.exception("Failed to save feedback for message_id=%s", message_id)
        conn.rollback()
        raise
    return {"id": fb_id, "message_id": message_id, "rating": rating, "created_at": now}


def update_feedback_comment(message_id: str, comment: str, user_id: str = "") -> bool:
    """Update the comment on an existing feedback entry (for follow-up comments)."""
    conn = _get_connection()
    from .guardrails import redact_pii_text

    try:
        cursor = conn.execute(
            """UPDATE feedback SET comment = ?
               WHERE id = (
                 SELECT id FROM feedback
                 WHERE message_id = ? AND user_id = ? AND comment = ''
                 ORDER BY created_at DESC LIMIT 1
               )""",
            (redact_pii_text(comment), message_id, user_id or ""),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        logger.exception("Failed to update feedback comment for message_id=%s", message_id)
        conn.rollback()
        return False


def get_feedback_summary(days: int = 30) -> dict[str, Any]:
    """Aggregate feedback stats for the last N days."""
    conn = _get_connection()
    cutoff = time.time() - (days * 86400)
    row = conn.execute(
        """SELECT
             COUNT(*) as total,
             COALESCE(SUM(CASE WHEN rating='up' THEN 1 ELSE 0 END), 0) as thumbs_up,
             COALESCE(SUM(CASE WHEN rating='down' THEN 1 ELSE 0 END), 0) as thumbs_down
           FROM feedback WHERE created_at >= ?""",
        (cutoff,),
    ).fetchone()

    total = row["total"] or 0
    up = row["thumbs_up"] or 0
    down = row["thumbs_down"] or 0
    satisfaction = round(up / total * 100, 1) if total > 0 else 0.0

    recent = conn.execute(
        # user_query is what the analytics table shows under "Taxpayer
        # question". It is stored already redacted (see save_feedback), so
        # selecting it here adds no new disclosure.
        """SELECT id, message_id, rating, comment, user_query, created_at
           FROM feedback WHERE created_at >= ? ORDER BY created_at DESC LIMIT 20""",
        (cutoff,),
    ).fetchall()

    return {
        "period_days": days,
        "total": total,
        "thumbs_up": up,
        "thumbs_down": down,
        "satisfaction_pct": satisfaction,
        "recent": [dict(r) for r in recent],
    }


# ---------------------------------------------------------------------------
# Analytics events
# ---------------------------------------------------------------------------
def track_event(
    event_type: str,
    event_data: str = "{}",
    session_id: str | None = None,
    user_id: str = "",
) -> None:
    """Record an analytics event."""
    conn = _get_connection()
    from .guardrails import redact_pii_text

    try:
        conn.execute(
            """INSERT INTO analytics_events (id, session_id, user_id, event_type, event_data, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                session_id,
                user_id or "",
                event_type,
                redact_pii_text(event_data),
                time.time(),
            ),
        )
        conn.commit()
    except Exception:
        logger.exception("Failed to track event type=%s", event_type)
        conn.rollback()


def get_event_counts(days: int = 30) -> dict[str, int]:
    """Count events by type for the last N days."""
    conn = _get_connection()
    cutoff = time.time() - (days * 86400)
    rows = conn.execute(
        """SELECT event_type, COUNT(*) as cnt
           FROM analytics_events WHERE created_at >= ?
           GROUP BY event_type ORDER BY cnt DESC""",
        (cutoff,),
    ).fetchall()
    return {r["event_type"]: r["cnt"] for r in rows}


# ---------------------------------------------------------------------------
# Session tracking
# ---------------------------------------------------------------------------
def upsert_session(
    session_id: str,
    user_agent: str = "",
    platform: str = "",
    user_id: str = "",
) -> None:
    """Create or update a session record."""
    conn = _get_connection()
    now = time.time()
    try:
        conn.execute(
            """INSERT INTO sessions (id, user_id, started_at, last_active_at, message_count, user_agent, platform)
               VALUES (?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 user_id = CASE WHEN excluded.user_id <> '' THEN excluded.user_id ELSE sessions.user_id END,
                 last_active_at = excluded.last_active_at,
                 message_count = sessions.message_count + 1""",
            (session_id, user_id or "", now, now, user_agent, platform),
        )
        conn.commit()
    except Exception:
        logger.exception("Failed to upsert session id=%s", session_id)
        conn.rollback()


def get_session_stats(days: int = 30) -> dict[str, Any]:
    """Session analytics for the last N days."""
    conn = _get_connection()
    cutoff = time.time() - (days * 86400)
    row = conn.execute(
        """SELECT
             COUNT(*) as total_sessions,
             COALESCE(AVG(message_count), 0) as avg_messages,
             COALESCE(MAX(message_count), 0) as max_messages
           FROM sessions WHERE last_active_at >= ?""",
        (cutoff,),
    ).fetchone()
    return {
        "period_days": days,
        "total_sessions": row["total_sessions"] or 0,
        "avg_messages_per_session": round(row["avg_messages"] or 0, 1),
        "max_messages_in_session": row["max_messages"] or 0,
    }


def delete_user_analytics(user_id: str) -> dict[str, int]:
    """Delete analytics-derived records for an authenticated subject.

    The app stores the verified OIDC subject only after explicit analytics
    consent.  Keeping it on each analytics table means withdrawal and erasure
    are complete even after a session row would otherwise have expired.
    """
    if not user_id:
        return {"analytics_events": 0, "sessions": 0, "feedback": 0}
    counts: dict[str, int] = {}
    for table in ("analytics_events", "sessions", "feedback"):
        try:
            counts[table] = execute(
                f"DELETE FROM {table} WHERE user_id = ?",  # noqa: S608 — fixed table names
                (user_id,),
            )
        except Exception:
            logger.exception("delete_user_analytics: %s", table)
            counts[table] = -1
    return counts


# ---------------------------------------------------------------------------
# Conversation logging
# ---------------------------------------------------------------------------
def log_conversation(
    session_id: str | None,
    conversation_id: str | None,
    user_message: str,
    bot_reply: str,
    sources: str = "[]",
    contexts: str = "[]",
    response_time_ms: float = 0,
    confidence: float = 0,
    topic_tag: str = "",
    user_id: str = "",
    flag_variants: str = "{}",
    locale: str = "",
) -> str:
    """Log a conversation turn and return the stable thread id.

    ``user_id`` is the authenticated OIDC ``sub`` (empty for anonymous turns) —
    it links the turn to the user for /v1/me export + erasure.
    """
    conn = _get_connection()
    from .guardrails import redact_pii_text

    user_message = redact_pii_text(user_message)
    bot_reply = redact_pii_text(bot_reply)
    row_id = str(uuid.uuid4())
    thread_id = conversation_id or row_id
    try:
        conn.execute(
            """INSERT INTO conversations
               (id, conversation_id, session_id, user_message, bot_reply, sources,
                contexts, response_time_ms, confidence, topic_tag, created_at, user_id,
                flag_variants, locale)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row_id,
                thread_id,
                session_id,
                user_message,
                bot_reply,
                sources,
                contexts,
                response_time_ms,
                confidence,
                topic_tag,
                time.time(),
                user_id,
                flag_variants or "{}",
                locale or "",
            ),
        )
        conn.commit()
    except Exception:
        logger.exception("Failed to log conversation")
        conn.rollback()
        raise
    return thread_id


def get_recent_turns(
    session_id: str | None = None,
    conversation_id: str | None = None,
    limit: int = 5,
    user_id: str | None = None,
) -> list[dict[str, str]]:
    """Retrieve the most recent conversation turns for a session (multi-turn memory).

    Returns a list of dicts with ``user_message`` and ``bot_reply`` keys,
    ordered oldest-first (chronological) for prompt injection.
    """
    if conversation_id:
        if user_id:
            sql = """SELECT user_message, bot_reply FROM conversations
                     WHERE conversation_id = ? AND user_id = ?
                     ORDER BY created_at DESC LIMIT ?"""
            args: tuple[Any, ...] = (conversation_id, user_id, limit)
        else:
            sql = """SELECT user_message, bot_reply FROM conversations
                     WHERE conversation_id = ?
                     ORDER BY created_at DESC LIMIT ?"""
            args = (conversation_id, limit)
    elif session_id:
        if user_id:
            sql = """SELECT user_message, bot_reply FROM conversations
                     WHERE session_id = ? AND user_id = ?
                     ORDER BY created_at DESC LIMIT ?"""
            args = (session_id, user_id, limit)
        else:
            sql = """SELECT user_message, bot_reply FROM conversations
                     WHERE session_id = ?
                     ORDER BY created_at DESC LIMIT ?"""
            args = (session_id, limit)
    else:
        return []

    conn = _get_connection()
    rows = conn.execute(sql, args).fetchall()
    # Reverse to chronological order
    return [
        {"user_message": r["user_message"], "bot_reply": r["bot_reply"]} for r in reversed(rows)
    ]


def get_conversation_context(
    session_id: str | None = None,
    conversation_id: str | None = None,
    recent_limit: int = 6,
    max_history: int = 25,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Retrieve multi-turn conversation history and build rolling context & summary."""
    from .context_manager import RollingContextManager, context_manager

    turns = get_recent_turns(
        session_id=session_id,
        conversation_id=conversation_id,
        limit=max_history,
        user_id=user_id,
    )
    mgr = RollingContextManager(recent_limit=recent_limit) if recent_limit != context_manager.recent_limit else context_manager
    ctx = mgr.build_context(
        turns,
        conversation_id=conversation_id or session_id or "",
    )
    return {
        "recent_turns": ctx.recent_turns,
        "context_summary": ctx.context_summary,
        "active_entities": ctx.active_entities,
        "total_turns": ctx.total_turns,
        "all_turns": ctx.all_turns,
    }


def get_conversation_topic(conversation_id: str) -> dict[str, Any] | None:
    """Return the persisted current task for *conversation_id*, or None."""
    cid = (conversation_id or "").strip()
    if not cid:
        return None
    conn = _get_connection()
    row = conn.execute(
        """SELECT conversation_id, topic_id, label, tax_type, confidence, updated_at
           FROM conversation_topics WHERE conversation_id = ?""",
        (cid,),
    ).fetchone()
    if row is None:
        return None
    return {
        "conversation_id": row["conversation_id"],
        "topic_id": row["topic_id"],
        "label": row["label"],
        "tax_type": row["tax_type"] or "",
        "confidence": float(row["confidence"] or 0),
        "updated_at": float(row["updated_at"] or 0),
    }


def upsert_conversation_topic(
    conversation_id: str,
    *,
    topic_id: str,
    label: str,
    tax_type: str = "",
    confidence: float = 0.0,
) -> None:
    cid = (conversation_id or "").strip()
    if not cid or not topic_id:
        return
    conn = _get_connection()
    conn.execute(
        """INSERT INTO conversation_topics
           (conversation_id, topic_id, label, tax_type, confidence, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(conversation_id) DO UPDATE SET
             topic_id = excluded.topic_id,
             label = excluded.label,
             tax_type = excluded.tax_type,
             confidence = excluded.confidence,
             updated_at = excluded.updated_at""",
        (cid, topic_id, label, tax_type or "", float(confidence), time.time()),
    )
    conn.commit()


def clear_conversation_topic(conversation_id: str) -> None:
    cid = (conversation_id or "").strip()
    if not cid:
        return
    conn = _get_connection()
    conn.execute("DELETE FROM conversation_topics WHERE conversation_id = ?", (cid,))
    conn.commit()


def get_conversation_transcript(
    conversation_id: str | None = None,
    session_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return the whole conversation, both sides, oldest first.

    Distinct from :func:`get_recent_turns`, which exists to seed a prompt
    and so returns the last few turns only.  A human officer needs the
    conversation, not a window on it: the point of attaching it to a
    ticket is that the taxpayer does not have to explain themselves
    again.  Timestamps are included so the officer can see where the
    conversation stalled.
    """
    if conversation_id:
        where, key = "conversation_id = ?", conversation_id
    elif session_id:
        where, key = "session_id = ?", session_id
    else:
        return []
    limit = max(1, min(int(limit), 1000))
    conn = _get_connection()
    rows = conn.execute(
        f"""SELECT user_message, bot_reply, created_at, sources, topic_tag
            FROM conversations WHERE {where}
            ORDER BY created_at DESC LIMIT ?""",  # noqa: S608 - `where` is a fixed literal
        (key, limit),
    ).fetchall()
    return [
        {
            "user_message": r["user_message"],
            "bot_reply": r["bot_reply"],
            "created_at": r["created_at"],
            "sources": _json_loads(r["sources"], []),
            "topic_tag": r["topic_tag"] or "",
        }
        for r in reversed(rows)
    ]


def get_workflow_session(conversation_id: str) -> dict[str, Any] | None:
    """Return the persisted workflow session for a conversation, if any."""
    if not conversation_id:
        return None
    conn = _get_connection()
    row = conn.execute(
        """SELECT conversation_id, workflow_id, status, current_step_idx,
                  slots_json, last_prompt, created_at, updated_at
           FROM workflow_sessions WHERE conversation_id = ?""",
        (conversation_id,),
    ).fetchone()
    if not row:
        return None
    try:
        slots = json.loads(row["slots_json"] or "{}")
        if not isinstance(slots, dict):
            slots = {}
    except Exception:
        slots = {}
    return {
        "conversation_id": row["conversation_id"],
        "workflow_id": row["workflow_id"],
        "status": row["status"],
        "current_step_idx": int(row["current_step_idx"] or 0),
        "slots": slots,
        "last_prompt": row["last_prompt"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def upsert_workflow_session(
    conversation_id: str,
    workflow_id: str,
    current_step_idx: int,
    slots: dict[str, Any] | None = None,
    *,
    status: str = "active",
    last_prompt: str = "",
) -> None:
    """Create or update a durable workflow session."""
    if not conversation_id or not workflow_id:
        return
    if status not in {"active", "completed", "cancelled"}:
        status = "active"
    conn = _get_connection()
    now = time.time()
    slots_json = json.dumps(slots or {}, ensure_ascii=True)
    try:
        conn.execute(
            """INSERT INTO workflow_sessions
               (conversation_id, workflow_id, status, current_step_idx,
                slots_json, last_prompt, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(conversation_id) DO UPDATE SET
                 workflow_id = excluded.workflow_id,
                 status = excluded.status,
                 current_step_idx = excluded.current_step_idx,
                 slots_json = excluded.slots_json,
                 last_prompt = excluded.last_prompt,
                 updated_at = excluded.updated_at""",
            (
                conversation_id,
                workflow_id,
                status,
                max(0, int(current_step_idx)),
                slots_json,
                last_prompt[:2000],
                now,
                now,
            ),
        )
        conn.commit()
    except Exception:
        logger.exception("Failed to upsert workflow session")
        conn.rollback()
        raise


def complete_workflow_session(
    conversation_id: str,
    *,
    status: str = "completed",
) -> bool:
    """Mark a workflow session as completed or cancelled."""
    if not conversation_id:
        return False
    if status not in {"completed", "cancelled"}:
        status = "completed"
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "UPDATE workflow_sessions SET status = ?, updated_at = ? WHERE conversation_id = ?",
            (status, time.time(), conversation_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        logger.exception("Failed to complete workflow session")
        conn.rollback()
        return False


def get_conversation_stats(days: int = 30) -> dict[str, Any]:
    """Conversation analytics for the last N days."""
    conn = _get_connection()
    cutoff = time.time() - (days * 86400)
    row = conn.execute(
        """SELECT
             COUNT(*) as total,
             COALESCE(AVG(response_time_ms), 0) as avg_response_ms,
             COALESCE(AVG(confidence), 0) as avg_confidence
           FROM conversations WHERE created_at >= ?""",
        (cutoff,),
    ).fetchone()

    top_topics = conn.execute(
        """SELECT topic_tag, COUNT(*) as cnt
           FROM conversations WHERE created_at >= ? AND topic_tag != ''
           GROUP BY topic_tag ORDER BY cnt DESC LIMIT 10""",
        (cutoff,),
    ).fetchall()

    return {
        "period_days": days,
        "total_conversations": row["total"] or 0,
        "avg_response_time_ms": round(row["avg_response_ms"] or 0, 1),
        "avg_confidence": round(row["avg_confidence"] or 0, 3),
        "top_topics": [{"tag": r["topic_tag"], "count": r["cnt"]} for r in top_topics],
    }


# ---------------------------------------------------------------------------
# Feedback review export (feedback loop)
# ---------------------------------------------------------------------------
def export_review_feedback(days: int = 30) -> list[dict[str, Any]]:
    """Export negative feedback and low-confidence conversations for review.

    Returns entries suitable for retriever/reranker tuning and regression tests.
    """
    conn = _get_connection()
    cutoff = time.time() - (days * 86400)

    # Thumbs-down feedback
    down_rows = conn.execute(
        """SELECT f.message_id, f.user_query, f.bot_reply, f.comment, f.created_at,
                  'thumbs_down' as review_reason
           FROM feedback f
           WHERE f.rating = 'down' AND f.created_at >= ?
           ORDER BY f.created_at DESC""",
        (cutoff,),
    ).fetchall()

    # Low-confidence conversations
    low_conf_rows = conn.execute(
        """SELECT id as message_id, user_message as user_query, bot_reply,
                  '' as comment, created_at, 'low_confidence' as review_reason
           FROM conversations
           WHERE confidence < 0.3 AND confidence > 0 AND created_at >= ?
           ORDER BY created_at DESC LIMIT 200""",
        (cutoff,),
    ).fetchall()

    return [dict(r) for r in down_rows] + [dict(r) for r in low_conf_rows]


def export_eval_samples(days: int = 30, limit: int = 200) -> list[dict[str, Any]]:
    """Export recent turns that persisted their retrieved contexts (P0-2).

    Unlike :func:`export_review_feedback` (review/tuning, no contexts), these
    rows carry the actual top-k retrieved passages, letting the eval harness
    score faithfulness against real context instead of the answer itself.
    """
    cutoff = time.time() - (days * 86400)
    return query_all(
        """SELECT user_message AS user_query, bot_reply, contexts, created_at,
                  topic_tag, locale, flag_variants
           FROM conversations
           WHERE created_at >= ?
             AND contexts IS NOT NULL
             AND contexts NOT IN ('', '[]')
           ORDER BY created_at DESC LIMIT ?""",
        (cutoff, limit),
    )


# ---------------------------------------------------------------------------
# Ticket queue (Phase 14-D)
# ---------------------------------------------------------------------------
def _json_dumps(value: Any, default: str) -> str:
    try:
        return json.dumps(value if value is not None else json.loads(default))
    except Exception:
        return default


def _json_loads(value: Any, default: Any) -> Any:
    if value in ("", None):
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _redact_ticket_value(value: Any) -> Any:
    """Redact PII recursively before a staff-facing ticket is stored or read.

    Service-layer redaction is the normal path.  Keeping this guard at the
    repository boundary covers admin tools, migrations, and future callers
    that create or update a ticket without going through ``ChatModel``.
    """
    if isinstance(value, str):
        from .guardrails import redact_pii_text

        return redact_pii_text(value)
    if isinstance(value, list):
        return [_redact_ticket_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_ticket_value(item) for key, item in value.items()}
    return value


def _hydrate_ticket(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    ticket = dict(row)
    for field in ("reason", "user_query", "bot_reply", "staff_note", "officer_reply"):
        if field in ticket:
            ticket[field] = _redact_ticket_value(ticket[field])
    ticket["handoff"] = _redact_ticket_value(_json_loads(ticket.pop("handoff_json", "{}"), {}))
    ticket["response_judge"] = _redact_ticket_value(
        _json_loads(ticket.pop("response_judge_json", "{}"), {})
    )
    ticket["transcript"] = _redact_ticket_value(_json_loads(ticket.pop("transcript_json", "[]"), []))
    return ticket


def create_ticket(
    reason: str,
    user_query: str = "",
    bot_reply: str = "",
    session_id: str | None = None,
    conversation_id: str | None = None,
    priority: str = "normal",
    handoff: dict[str, Any] | None = None,
    response_judge: dict[str, Any] | None = None,
    transcript: list[dict[str, Any]] | None = None,
    user_id: str = "",
    team: str = "",
) -> dict[str, Any]:
    """Create a new escalation ticket and return it.

    Called from the ``escalate_to_human`` tool and from the
    supervisor's ESCALATE route.  Priority must be one of
    'low', 'normal', 'high', 'urgent' — invalid values are coerced
    to 'normal' with a warning.
    """
    if priority not in ("low", "normal", "high", "urgent"):
        logger.warning("create_ticket: invalid priority %r → 'normal'", priority)
        priority = "normal"
    reason = _redact_ticket_value(reason)
    user_query = _redact_ticket_value(user_query)
    bot_reply = _redact_ticket_value(bot_reply)
    handoff = _redact_ticket_value(handoff or {})
    response_judge = _redact_ticket_value(response_judge or {})
    transcript = _redact_ticket_value(transcript or [])
    conn = _get_connection()
    ticket_id = str(uuid.uuid4())
    now = time.time()
    try:
        conn.execute(
            """INSERT INTO tickets (id, conversation_id, session_id, status, priority,
                                    reason, user_query, bot_reply,
                                    handoff_json, response_judge_json, transcript_json,
                                    user_id, team, assignee, staff_note,
                                    created_at, updated_at)
               VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, ?)""",
            (
                ticket_id,
                conversation_id,
                session_id,
                priority,
                reason,
                user_query,
                bot_reply,
                _json_dumps(handoff, "{}"),
                _json_dumps(response_judge, "{}"),
                _json_dumps(transcript, "[]"),
                user_id,
                team,
                now,
                now,
            ),
        )
        conn.commit()
        logger.info("ticket %s created (priority=%s reason_length=%d)", ticket_id, priority, len(reason))
    except Exception:
        logger.exception("Failed to create ticket")
        conn.rollback()
        raise
    return {
        "id": ticket_id,
        "status": "open",
        "priority": priority,
        "reason": reason,
        "handoff": handoff,
        "response_judge": response_judge,
        "transcript": transcript,
        "team": team,
        "created_at": now,
    }


def list_tickets(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    priority: str | None = None,
    team: str | None = None,
) -> list[dict[str, Any]]:
    """List tickets, urgent first then oldest within a priority.

    Recency ordering meant a computed priority changed nothing about
    what an officer saw: an urgent ticket raised this morning sat below
    a low-priority one raised at lunch. Within a priority the *oldest*
    comes first, so a waiting taxpayer moves up rather than being buried
    by newer arrivals.
    """
    conn = _get_connection()
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    sql = (
        # Queue view — every column except transcript_json, which the
        # detail view carries.  A column added to the table but not to
        # this list vanishes from every queue row; see
        # TestTicketColumnParity, which now checks both backends.
        "SELECT id, conversation_id, session_id, status, priority, reason, "
        "       user_query, bot_reply, handoff_json, response_judge_json, "
        "       assignee, staff_note, created_at, updated_at, user_id, team, "
        "       officer_reply, reply_at, reply_delivered_at, "
        "       first_response_at, resolved_at "
        "FROM tickets"
    )
    params: list[Any] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    if priority:
        sql += " AND priority = ?" if (status or params) else " WHERE priority = ?"
        params.append(priority)
    if team:
        sql += " AND team = ?" if (status or params) else " WHERE team = ?"
        params.append(team)
    sql += (
        " ORDER BY CASE priority"
        "   WHEN 'urgent' THEN 0 WHEN 'high' THEN 1"
        "   WHEN 'normal' THEN 2 ELSE 3 END,"
        " created_at ASC LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    return [_hydrate_ticket(r) for r in rows]


def pending_officer_reply(conversation_id: str) -> dict[str, Any] | None:
    """An officer reply for this conversation the taxpayer has not seen.

    Closes the loop the escalation pipeline left open: a resolved ticket
    used to reach nobody. The taxpayer is told a human will follow up,
    so when they come back the follow-up should be waiting rather than
    sitting in a queue they cannot see.
    """
    if not conversation_id:
        return None
    conn = _get_connection()
    row = conn.execute(
        """SELECT id, officer_reply, reply_at, assignee, status FROM tickets
           WHERE conversation_id = ?
             AND officer_reply != ''
             AND reply_delivered_at = 0
           ORDER BY reply_at ASC LIMIT 1""",
        (conversation_id,),
    ).fetchone()
    return dict(row) if row else None


def mark_reply_delivered(ticket_id: str) -> bool:
    """Record that the taxpayer has been shown the officer's reply.

    Separate from writing the reply so a delivery failure re-delivers
    instead of silently dropping it — the taxpayer seeing it twice is a
    far smaller harm than never seeing it.
    """
    if not ticket_id:
        return False
    conn = _get_connection()
    try:
        cur = conn.execute(
            "UPDATE tickets SET reply_delivered_at = ? WHERE id = ? AND reply_delivered_at = 0",
            (time.time(), ticket_id),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        logger.exception("failed to mark officer reply delivered")
        conn.rollback()
        return False


#: First-response and next-reply clocks use the same public-sector day.
SLA_BREACH_SECONDS = 24 * 3600
PRESENCE_TTL_SECONDS = 45


def compose_sla_stats(
    *,
    period_rows: list[Any],
    open_rows: list[Any],
    days: int,
    now: float | None = None,
) -> dict[str, Any]:
    """Build the SLA payload from already-fetched rows.

    Medians stay period-scoped. Breach / awaiting-next counts use the
    live open+assigned population so a 20-row queue sample cannot
    under-count.
    """
    now = now if now is not None else time.time()

    def _get(row: Any, key: str, index: int) -> float:
        if isinstance(row, dict):
            return float(row.get(key) or 0)
        try:
            return float(row[key] or 0)
        except (KeyError, TypeError, IndexError):
            return float(row[index] or 0)

    def _get_text(row: Any, key: str, index: int) -> str:
        """Same lookup as :func:`_get` for a column that is not a number.

        A row that predates the column entirely (a stubbed test row, a
        short tuple) reads as empty rather than raising — the same
        fail-soft the numeric accessor gives.
        """
        if isinstance(row, dict):
            return str(row.get(key) or "")
        try:
            return str(row[key] or "")
        except (KeyError, TypeError, IndexError):
            pass
        try:
            return str(row[index] or "")
        except (KeyError, TypeError, IndexError):
            return ""

    def _median(values: list[float]) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return round(ordered[mid], 1)
        return round((ordered[mid - 1] + ordered[mid]) / 2, 1)

    response = [
        _get(r, "first_response_at", 1) - _get(r, "created_at", 0)
        for r in period_rows
        if _get(r, "first_response_at", 1)
    ]
    resolution = [
        _get(r, "resolved_at", 2) - _get(r, "created_at", 0)
        for r in period_rows
        if _get(r, "resolved_at", 2)
    ]
    next_replies = [
        _get(r, "reply_at", 3) - _get(r, "first_response_at", 1)
        for r in period_rows
        if _get(r, "reply_at", 3) and _get(r, "first_response_at", 1)
        and _get(r, "reply_at", 3) > _get(r, "first_response_at", 1)
    ]

    breaching_first = 0
    breaching_next = 0
    awaiting_next = 0
    unassigned = 0
    for r in open_rows:
        created = _get(r, "created_at", 0)
        first = _get(r, "first_response_at", 1)
        reply = _get(r, "reply_at", 2)
        # "Waiting to be claimed" belongs with the other live counts rather
        # than in ticket_stats: it is a property of the queue right now, not
        # of what was raised in a window, and the overview reads it beside
        # the breach counts that share this population.
        if not _get_text(r, "assignee", 4):
            unassigned += 1
        if not first:
            if now - created >= SLA_BREACH_SECONDS:
                breaching_first += 1
            continue
        awaiting_next += 1
        last_touch = reply or first
        if now - last_touch >= SLA_BREACH_SECONDS:
            breaching_next += 1

    return {
        "period_days": days,
        "tickets": len(period_rows),
        "responded": len(response),
        "resolved": len(resolution),
        "awaiting_first_response": sum(1 for r in open_rows if not _get(r, "first_response_at", 1)),
        "awaiting_next_response": awaiting_next,
        "unassigned": unassigned,
        "median_response_seconds": _median(response),
        "median_resolution_seconds": _median(resolution),
        "median_next_reply_seconds": _median(next_replies),
        "breaching_first_response": breaching_first,
        "breaching_next_reply": breaching_next,
        "breaching": breaching_first + breaching_next,
    }


def sla_stats(days: int = 30) -> dict[str, Any]:
    """Time-to-first-response, next-reply, and live breach counts."""
    conn = _get_connection()
    now = time.time()
    cutoff = now - (days * 86400)
    period = conn.execute(
        """SELECT created_at, first_response_at, resolved_at, reply_at
           FROM tickets WHERE created_at >= ?""",
        (cutoff,),
    ).fetchall()
    opened = conn.execute(
        # assignee last: compose_sla_stats reads the first four positionally
        # for the Postgres tuples, so a new column has to go on the end.
        """SELECT created_at, first_response_at, reply_at, status, assignee
           FROM tickets WHERE status IN ('open', 'assigned')""",
    ).fetchall()
    return compose_sla_stats(period_rows=period, open_rows=opened, days=days, now=now)


def heartbeat_ticket_presence(ticket_id: str, viewer: str) -> None:
    """Record that *viewer* has this case open. TTL is enforced on read."""
    cid = (ticket_id or "").strip()
    who = (viewer or "").strip()[:128]
    if not cid or not who:
        return
    conn = _get_connection()
    now = time.time()
    conn.execute(
        """INSERT INTO ticket_presence (ticket_id, viewer, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(ticket_id, viewer) DO UPDATE SET updated_at = excluded.updated_at""",
        (cid, who, now),
    )
    conn.commit()


def list_ticket_viewers(ticket_id: str, max_age: float = PRESENCE_TTL_SECONDS) -> list[str]:
    cid = (ticket_id or "").strip()
    if not cid:
        return []
    conn = _get_connection()
    cutoff = time.time() - max_age
    rows = conn.execute(
        """SELECT viewer FROM ticket_presence
           WHERE ticket_id = ? AND updated_at >= ?
           ORDER BY updated_at DESC""",
        (cid, cutoff),
    ).fetchall()
    return [str(r["viewer"] if not isinstance(r, tuple) else r[0]) for r in rows]


def load_flag_overrides() -> dict[str, bool]:
    """Durable flag overrides, propagating a failure to read them.

    A query error here is a persistence failure, not an empty override
    set.  Returning ``{}`` made the two indistinguishable, so startup
    replayed nothing and still reported success — a production replica
    whose override state was unreadable looked exactly like one that had
    no overrides.  The Postgres backend already propagates; both must
    fail closed the same way.
    """
    conn = _get_connection()
    rows = conn.execute("SELECT name, enabled FROM flag_overrides").fetchall()
    out: dict[str, bool] = {}
    for row in rows:
        name = str(row["name"] if not isinstance(row, tuple) else row[0])
        enabled = row["enabled"] if not isinstance(row, tuple) else row[1]
        out[name] = bool(int(enabled))
    return out


def save_flag_override(name: str, enabled: bool) -> None:
    key = (name or "").strip()
    if not key:
        return
    conn = _get_connection()
    conn.execute(
        """INSERT INTO flag_overrides (name, enabled, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(name) DO UPDATE SET enabled = excluded.enabled, updated_at = excluded.updated_at""",
        (key, 1 if enabled else 0, time.time()),
    )
    conn.commit()


def clear_flag_override(name: str) -> None:
    key = (name or "").strip()
    if not key:
        return
    conn = _get_connection()
    conn.execute("DELETE FROM flag_overrides WHERE name = ?", (key,))
    conn.commit()


def upsert_reminder_inbox(
    user_id: str,
    deadline_name: str,
    due_date: str,
    message: str,
) -> dict[str, Any]:
    uid = (user_id or "").strip()
    if not uid or not deadline_name or not due_date:
        return {}
    conn = _get_connection()
    now = time.time()
    row = conn.execute(
        """SELECT id FROM reminder_inbox
           WHERE user_id = ? AND deadline_name = ? AND due_date = ?""",
        (uid, deadline_name, due_date),
    ).fetchone()
    if row:
        rid = str(row["id"] if not isinstance(row, tuple) else row[0])
        conn.execute(
            "UPDATE reminder_inbox SET message = ? WHERE id = ?",
            (message, rid),
        )
        conn.commit()
        return {"id": rid, "user_id": uid, "deadline_name": deadline_name, "due_date": due_date, "message": message}
    rid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO reminder_inbox
           (id, user_id, deadline_name, due_date, message, created_at, read_at)
           VALUES (?, ?, ?, ?, ?, ?, 0)""",
        (rid, uid, deadline_name, due_date, message, now),
    )
    conn.commit()
    return {"id": rid, "user_id": uid, "deadline_name": deadline_name, "due_date": due_date, "message": message}


def list_reminder_inbox(user_id: str) -> list[dict[str, Any]]:
    uid = (user_id or "").strip()
    if not uid:
        return []
    conn = _get_connection()
    rows = conn.execute(
        """SELECT id, user_id, deadline_name, due_date, message, created_at, read_at
           FROM reminder_inbox WHERE user_id = ? ORDER BY due_date ASC""",
        (uid,),
    ).fetchall()
    out = []
    for row in rows:
        if isinstance(row, dict):
            out.append(dict(row))
        else:
            out.append(
                {
                    "id": row[0],
                    "user_id": row[1],
                    "deadline_name": row[2],
                    "due_date": row[3],
                    "message": row[4],
                    "created_at": row[5],
                    "read_at": row[6],
                }
            )
    return out


def enqueue_notification(
    user_id: str,
    channel: str,
    payload: dict[str, Any],
    *,
    provider: str = "mock",
) -> dict[str, Any]:
    uid = (user_id or "").strip()
    ch = (channel or "").strip().lower()
    if not uid or not ch:
        return {}
    conn = _get_connection()
    nid = str(uuid.uuid4())
    now = time.time()
    body = json.dumps(payload or {}, ensure_ascii=True)
    conn.execute(
        """INSERT INTO notification_outbox
           (id, user_id, channel, provider, payload, status, created_at)
           VALUES (?, ?, ?, ?, ?, 'queued', ?)""",
        (nid, uid, ch, provider, body, now),
    )
    conn.commit()
    return {
        "id": nid,
        "user_id": uid,
        "channel": ch,
        "provider": provider,
        "status": "queued",
        "live": False,
    }


def list_notification_outbox(user_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
    conn = _get_connection()
    if user_id:
        rows = conn.execute(
            """SELECT id, user_id, channel, provider, payload, status, created_at
               FROM notification_outbox WHERE user_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, max(1, min(limit, 200))),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, user_id, channel, provider, payload, status, created_at
               FROM notification_outbox ORDER BY created_at DESC LIMIT ?""",
            (max(1, min(limit, 200)),),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row) if isinstance(row, dict) else {
            "id": row[0],
            "user_id": row[1],
            "channel": row[2],
            "provider": row[3],
            "payload": row[4],
            "status": row[5],
            "created_at": row[6],
        }
        raw = item.get("payload")
        if isinstance(raw, str):
            try:
                item["payload"] = json.loads(raw)
            except json.JSONDecodeError:
                pass
        out.append(item)
    return out


def get_answer_override(match_query: str) -> dict[str, Any] | None:
    key = (match_query or "").strip()
    if not key:
        return None
    conn = _get_connection()
    row = conn.execute(
        """SELECT id, match_query, reply, source_url, created_by, enabled, updated_at
           FROM answer_overrides WHERE match_query = ? AND enabled = 1""",
        (key,),
    ).fetchone()
    if not row:
        return None
    return dict(row) if isinstance(row, dict) else {
        "id": row[0],
        "match_query": row[1],
        "reply": row[2],
        "source_url": row[3],
        "created_by": row[4],
        "enabled": bool(row[5]),
        "updated_at": row[6],
    }


def upsert_answer_override(
    match_query: str,
    reply: str,
    *,
    source_url: str = "",
    created_by: str = "",
    enabled: bool = True,
) -> dict[str, Any]:
    key = (match_query or "").strip()
    body = (reply or "").strip()
    conn = _get_connection()
    now = time.time()
    existing = conn.execute(
        "SELECT id FROM answer_overrides WHERE match_query = ?", (key,)
    ).fetchone()
    if existing:
        oid = str(existing["id"] if not isinstance(existing, tuple) else existing[0])
        conn.execute(
            """UPDATE answer_overrides
               SET reply = ?, source_url = ?, created_by = ?, enabled = ?, updated_at = ?
               WHERE id = ?""",
            (body, source_url, created_by, 1 if enabled else 0, now, oid),
        )
    else:
        oid = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO answer_overrides
               (id, match_query, reply, source_url, created_by, enabled, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (oid, key, body, source_url, created_by, 1 if enabled else 0, now),
        )
    conn.commit()
    return {
        "id": oid,
        "match_query": key,
        "reply": body,
        "source_url": source_url,
        "created_by": created_by,
        "enabled": enabled,
    }


def list_answer_overrides(limit: int = 100) -> list[dict[str, Any]]:
    conn = _get_connection()
    rows = conn.execute(
        """SELECT id, match_query, reply, source_url, created_by, enabled, updated_at
           FROM answer_overrides ORDER BY updated_at DESC LIMIT ?""",
        (max(1, min(limit, 200)),),
    ).fetchall()
    out = []
    for row in rows:
        out.append(dict(row) if isinstance(row, dict) else {
            "id": row[0],
            "match_query": row[1],
            "reply": row[2],
            "source_url": row[3],
            "created_by": row[4],
            "enabled": bool(row[5]),
            "updated_at": row[6],
        })
    return out


def delete_answer_override(override_id: str) -> bool:
    oid = (override_id or "").strip()
    if not oid:
        return False
    conn = _get_connection()
    cur = conn.execute("DELETE FROM answer_overrides WHERE id = ?", (oid,))
    conn.commit()
    return cur.rowcount > 0


def find_open_ticket(conversation_id: str) -> dict[str, Any] | None:
    """Return the newest unresolved ticket for *conversation_id*, if any.

    A taxpayer who asks for a human three times in one conversation
    wants one officer, not three tickets. Resolved and wontfix tickets
    are excluded so a genuinely new problem later in the same
    conversation still opens its own.
    """
    if not conversation_id:
        return None
    conn = _get_connection()
    row = conn.execute(
        """SELECT * FROM tickets
           WHERE conversation_id = ? AND status IN ('open', 'assigned')
           ORDER BY created_at DESC LIMIT 1""",
        (conversation_id,),
    ).fetchone()
    return _hydrate_ticket(row) if row else None


# ---------------------------------------------------------------------------
# Long-running MCP tasks
# ---------------------------------------------------------------------------
#: Statuses a task can no longer move out of.
TASK_TERMINAL = ("succeeded", "failed", "cancelled")


def create_task(
    kind: str,
    args: dict[str, Any] | None = None,
    *,
    tenant_id: str = "default",
    user_id: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Create a task, or return the existing one for *idempotency_key*.

    Returning the existing task rather than raising is the whole point:
    a retried filing submission must observe the first attempt, not
    start a second. The check is a read before the insert *and* a
    recovery on the unique-constraint violation, because two replicas
    can pass the read at the same time — the index is what actually
    enforces it.
    """
    key = idempotency_key.strip() or None
    if key:
        existing = query_one(
            "SELECT * FROM mcp_tasks WHERE tenant_id = ? AND idempotency_key = ?",
            (tenant_id, key),
        )
        if existing:
            return _task_row(existing, replayed=True)

    task_id = str(uuid.uuid4())
    now = time.time()
    try:
        execute(
            """INSERT INTO mcp_tasks (id, tenant_id, user_id, kind, status, progress,
                                      args_json, idempotency_key, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'pending', 0.0, ?, ?, ?, ?)""",
            (task_id, tenant_id, user_id, kind, json.dumps(args or {}), key, now, now),
        )
    except Exception:
        # Lost the race on the unique index — the winner's task is the
        # answer, and it is the same answer this call would have given.
        if key:
            existing = query_one(
                "SELECT * FROM mcp_tasks WHERE tenant_id = ? AND idempotency_key = ?",
                (tenant_id, key),
            )
            if existing:
                return _task_row(existing, replayed=True)
        raise
    return _task_row(
        query_one("SELECT * FROM mcp_tasks WHERE id = ?", (task_id,)) or {}
    )


def get_task(task_id: str, *, tenant_id: str = "default") -> dict[str, Any] | None:
    """Fetch a task, scoped to its tenant.

    The tenant is part of the lookup, not a check afterwards: a task id
    is a bearer token to whoever holds it, and cross-tenant reads are
    the failure mode that matters here.
    """
    row = query_one(
        "SELECT * FROM mcp_tasks WHERE id = ? AND tenant_id = ?", (task_id, tenant_id)
    )
    return _task_row(row) if row else None


def update_task(
    task_id: str,
    *,
    tenant_id: str = "default",
    status: str | None = None,
    progress: float | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    """Advance a task. Terminal states are final.

    A task that has already succeeded, failed or been cancelled cannot
    be moved again — otherwise a late worker could overwrite a
    cancellation the taxpayer already saw acted on.
    """
    current = get_task(task_id, tenant_id=tenant_id)
    if current is None:
        return None
    if current["status"] in TASK_TERMINAL:
        return current

    # Every column is written every time, with the current value standing
    # in for anything the caller did not pass. The SQL is therefore a
    # constant.
    #
    # Assembling a SET list and joining it is the obvious way to write a
    # partial update, and it is what this did first — but the resulting
    # f-string reads as a SQL-injection sink to Bandit and CodeQL no
    # matter that every fragment is a literal from three lines above.
    # `providers.routing` already settled this argument for the codebase:
    # prefer the shape static analysis can *prove* safe over the one that
    # needs a reviewer to accept an argument about taint. The row is
    # already loaded for the terminal-state check, so merging costs
    # nothing.
    new_status = current["status"] if status is None else status
    new_progress = (
        current["progress"] if progress is None else max(0.0, min(1.0, float(progress)))
    )
    new_result = current.get("result") or {} if result is None else result
    new_error = current.get("error", "") if error is None else error

    execute(
        """UPDATE mcp_tasks
              SET updated_at = ?, status = ?, progress = ?, result_json = ?, error = ?
            WHERE id = ? AND tenant_id = ?""",
        (
            time.time(),
            new_status,
            new_progress,
            json.dumps(new_result),
            new_error,
            task_id,
            tenant_id,
        ),
    )
    return get_task(task_id, tenant_id=tenant_id)


def cancel_task(task_id: str, *, tenant_id: str = "default") -> dict[str, Any] | None:
    """Cancel a task that has not already finished."""
    return update_task(task_id, tenant_id=tenant_id, status="cancelled")


def list_tasks(
    *, tenant_id: str = "default", status: str = "", limit: int = 50
) -> list[dict[str, Any]]:
    """Recent tasks for a tenant, newest first."""
    limit = max(1, min(int(limit), 200))
    if status:
        rows = query_all(
            """SELECT * FROM mcp_tasks WHERE tenant_id = ? AND status = ?
               ORDER BY created_at DESC LIMIT ?""",
            (tenant_id, status, limit),
        )
    else:
        rows = query_all(
            "SELECT * FROM mcp_tasks WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
            (tenant_id, limit),
        )
    return [_task_row(r) for r in rows]


def _task_row(row: dict[str, Any], *, replayed: bool = False) -> dict[str, Any]:
    """Normalise a DB row into the shape the MCP tool returns."""
    if not row:
        return {}
    out = {
        "task_id": row.get("id", ""),
        "kind": row.get("kind", ""),
        "status": row.get("status", "pending"),
        "progress": float(row.get("progress") or 0.0),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
    for field, target in (("args_json", "args"), ("result_json", "result")):
        raw = row.get(field) or "{}"
        try:
            out[target] = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (ValueError, TypeError):
            out[target] = {}
    if row.get("error"):
        out["error"] = row["error"]
    if replayed:
        out["replayed"] = True
    return out



def get_ticket(ticket_id: str) -> dict[str, Any] | None:
    conn = _get_connection()
    row = conn.execute(
        "SELECT * FROM tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    return _hydrate_ticket(row) if row else None


def update_ticket(
    ticket_id: str,
    status: str | None = None,
    assignee: str | None = None,
    staff_note: str | None = None,
    priority: str | None = None,
    officer_reply: str | None = None,
) -> bool:
    """Update mutable ticket fields.  Returns True if a row was touched.

    ``officer_reply`` is what the taxpayer will actually be shown when
    they next open the conversation — distinct from ``staff_note``,
    which stays internal.  Keeping them separate matters: an officer
    writing "caller is being obstructive, escalate to audit" into a
    field the taxpayer can read would be a serious incident.

    SLA stamps (``first_response_at``, ``resolved_at``) are set here
    rather than computed on read, so they survive later edits and cannot
    drift if the definition of "responded" changes.
    """
    conn = _get_connection()
    sets: list[str] = []
    params: list[Any] = []
    now = time.time()
    existing = get_ticket(ticket_id)
    if existing is None:
        return False
    # First officer touch — assignment, a note, a reply, or moving it off
    # 'open'. Whichever happens first is the response time.
    is_touch = any(
        v is not None for v in (assignee, staff_note, officer_reply)
    ) or (status is not None and status != "open")
    if is_touch and not existing.get("first_response_at"):
        sets.append("first_response_at = ?")
        params.append(now)
    if status in ("resolved", "wontfix") and not existing.get("resolved_at"):
        sets.append("resolved_at = ?")
        params.append(now)
    if officer_reply is not None:
        sets.append("officer_reply = ?")
        params.append(_redact_ticket_value(officer_reply)[:4000])
        sets.append("reply_at = ?")
        params.append(now)
    if status is not None:
        if status not in ("open", "assigned", "resolved", "wontfix"):
            return False
        sets.append("status = ?")
        params.append(status)
    if assignee is not None:
        sets.append("assignee = ?")
        params.append(assignee[:128])
    if staff_note is not None:
        sets.append("staff_note = ?")
        params.append(_redact_ticket_value(staff_note)[:2000])
    if priority is not None:
        if priority not in ("low", "normal", "high", "urgent"):
            return False
        sets.append("priority = ?")
        params.append(priority)
    if not sets:
        return False
    sets.append("updated_at = ?")
    params.append(now)
    params.append(ticket_id)
    try:
        cursor = conn.execute(
            f"UPDATE tickets SET {', '.join(sets)} WHERE id = ?",  # noqa: S608
            params,
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        logger.exception("update_ticket failed")
        conn.rollback()
        return False


# ---------------------------------------------------------------------------
# Phase 14 (2026) — identity / profile / consent CRUD
# ---------------------------------------------------------------------------
def upsert_user(
    external_id: str,
    tenant_id: str = "default",
    email: str = "",
    role: str = "public",
) -> dict[str, Any]:
    """Create or refresh a user row from verified JWT claims.

    Returns the full row as a dict (with the internal ``id``).
    Idempotent — calling repeatedly with the same (tenant_id,
    external_id) updates ``last_seen_at`` only.
    """
    conn = _get_connection()
    now = time.time()
    row = conn.execute(
        "SELECT * FROM users WHERE tenant_id = ? AND external_id = ?",
        (tenant_id, external_id),
    ).fetchone()

    if row is not None:
        try:
            conn.execute(
                "UPDATE users SET last_seen_at = ?, email = ?, role = ? WHERE id = ?",
                (now, email or row["email"], role or row["role"], row["id"]),
            )
            conn.commit()
        except Exception:
            logger.exception("upsert_user update failed")
            conn.rollback()
        return {
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "external_id": row["external_id"],
            "email": email or row["email"],
            "role": role or row["role"],
            "created_at": row["created_at"],
            "last_seen_at": now,
        }

    user_id = str(uuid.uuid4())
    try:
        conn.execute(
            """INSERT INTO users (id, tenant_id, external_id, email, role, created_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, tenant_id, external_id, email, role, now, now),
        )
        conn.commit()
    except Exception:
        logger.exception("upsert_user insert failed")
        conn.rollback()
        raise
    return {
        "id": user_id,
        "tenant_id": tenant_id,
        "external_id": external_id,
        "email": email,
        "role": role,
        "created_at": now,
        "last_seen_at": now,
    }


def get_user(user_id: str) -> dict[str, Any] | None:
    conn = _get_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_profile(user_id: str) -> dict[str, Any] | None:
    conn = _get_connection()
    row = conn.execute(
        "SELECT * FROM user_profiles WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    try:
        import json as _json

        d["registered_tax_types"] = _json.loads(d.get("registered_tax_types", "[]"))
    except Exception:
        d["registered_tax_types"] = []
    return d


def upsert_user_profile(user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Create or patch a profile row.

    Unknown keys in *updates* are silently dropped — the Pydantic
    model above is the source of truth for allowed fields.
    """
    import json as _json

    allowed = {
        "taxpayer_type",
        "industry",
        "primary_language",
        "detail_level",
        "registered_tax_types",
        "fiscal_year",
        "display_name",
    }
    updates = {k: v for k, v in updates.items() if k in allowed}
    if "registered_tax_types" in updates and isinstance(updates["registered_tax_types"], list):
        updates["registered_tax_types"] = _json.dumps(updates["registered_tax_types"])

    conn = _get_connection()
    existing = conn.execute(
        "SELECT user_id FROM user_profiles WHERE user_id = ?", (user_id,)
    ).fetchone()
    now = time.time()

    if existing is None:
        # First-time insert — fall back to defaults for any missing field
        defaults = {
            "taxpayer_type": "unknown",
            "industry": "",
            "primary_language": "en",
            "detail_level": "intermediate",
            "registered_tax_types": "[]",
            "fiscal_year": "FY2025-26",
            "display_name": "",
        }
        defaults.update(updates)
        try:
            conn.execute(
                """INSERT INTO user_profiles
                   (user_id, taxpayer_type, industry, primary_language,
                    detail_level, registered_tax_types, fiscal_year,
                    display_name, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    defaults["taxpayer_type"],
                    defaults["industry"],
                    defaults["primary_language"],
                    defaults["detail_level"],
                    defaults["registered_tax_types"],
                    defaults["fiscal_year"],
                    defaults["display_name"],
                    now,
                ),
            )
            conn.commit()
        except Exception:
            logger.exception("upsert_user_profile insert failed")
            conn.rollback()
            raise
    else:
        if not updates:
            return get_user_profile(user_id) or {}
        sets = ", ".join(f"{k} = ?" for k in updates) + ", updated_at = ?"
        params = list(updates.values()) + [now, user_id]
        try:
            conn.execute(
                f"UPDATE user_profiles SET {sets} WHERE user_id = ?",  # noqa: S608
                params,
            )
            conn.commit()
        except Exception:
            logger.exception("upsert_user_profile update failed")
            conn.rollback()
            raise

    return get_user_profile(user_id) or {}


def grant_consent(
    user_id: str,
    purpose: str,
    version: str,
    legal_basis: str = "consent",
) -> dict[str, Any]:
    """Issue a new consent receipt for (user, purpose, version).

    If an active (not-withdrawn) row already exists for this
    (user, purpose, version) we return it unchanged — consents are
    idempotent.  Withdrawal of an older version is caller's job.
    """
    conn = _get_connection()
    existing = conn.execute(
        """SELECT * FROM consent_receipts
           WHERE user_id = ? AND purpose = ? AND version = ? AND withdrawn_at IS NULL""",
        (user_id, purpose, version),
    ).fetchone()
    if existing is not None:
        return dict(existing)

    receipt_id = str(uuid.uuid4())
    now = time.time()
    try:
        conn.execute(
            """INSERT INTO consent_receipts
               (receipt_id, user_id, purpose, version, granted_at, withdrawn_at, legal_basis)
               VALUES (?, ?, ?, ?, ?, NULL, ?)""",
            (receipt_id, user_id, purpose, version, now, legal_basis),
        )
        conn.commit()
    except Exception:
        logger.exception("grant_consent failed")
        conn.rollback()
        raise

    return {
        "receipt_id": receipt_id,
        "user_id": user_id,
        "purpose": purpose,
        "version": version,
        "granted_at": now,
        "withdrawn_at": None,
        "legal_basis": legal_basis,
    }


def withdraw_consent(user_id: str, purpose: str) -> int:
    """Mark all active consents for (user, purpose) as withdrawn.

    Returns the number of rows touched.  The caller is responsible
    for cascading cleanup (memory purge, etc.).
    """
    conn = _get_connection()
    now = time.time()
    try:
        cursor = conn.execute(
            """UPDATE consent_receipts
               SET withdrawn_at = ?
               WHERE user_id = ? AND purpose = ? AND withdrawn_at IS NULL""",
            (now, user_id, purpose),
        )
        conn.commit()
        return cursor.rowcount
    except Exception:
        logger.exception("withdraw_consent failed")
        conn.rollback()
        return 0


def get_active_consents(user_id: str) -> list[dict[str, Any]]:
    conn = _get_connection()
    rows = conn.execute(
        """SELECT * FROM consent_receipts
           WHERE user_id = ? AND withdrawn_at IS NULL
           ORDER BY granted_at DESC""",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _resolve_internal_user_id(external_id: str, tenant_id: str = "default") -> str | None:
    """Map an external OIDC ``sub`` to the internal ``users.id`` (None if unknown)."""
    if not external_id:
        return None
    conn = _get_connection()
    row = conn.execute(
        "SELECT id FROM users WHERE tenant_id = ? AND external_id = ?",
        (tenant_id, external_id),
    ).fetchone()
    return row["id"] if row else None


def has_active_consent(user_id: str, purpose: str, tenant_id: str = "default") -> bool:
    """True when the user has an active (not-withdrawn) receipt for *purpose*.

    Accepts EITHER the internal user UUID or the external OIDC ``sub``. Consent
    receipts are keyed by the internal UUID, but the chat/voice runtime only holds
    the ``sub`` — so when a direct match fails we resolve ``sub`` → internal id and
    retry. This single bridge fixes the gate that otherwise left personalization
    memory and voice consent permanently denied for authenticated users.
    """
    if not user_id:
        return False
    conn = _get_connection()
    sql = (
        "SELECT 1 FROM consent_receipts "
        "WHERE user_id = ? AND purpose = ? AND withdrawn_at IS NULL LIMIT 1"
    )
    if conn.execute(sql, (user_id, purpose)).fetchone() is not None:
        return True
    internal_id = _resolve_internal_user_id(user_id, tenant_id)
    if internal_id and internal_id != user_id:
        return conn.execute(sql, (internal_id, purpose)).fetchone() is not None
    return False


def export_user_data(user_id: str, external_id: str = "") -> dict[str, Any]:
    """GET /v1/me/export — subject right to data portability (UDPA 2019).

    ``user_id`` is the internal UUID (users/profiles/consents); ``external_id`` is
    the OIDC ``sub`` that chat history is keyed by. Conversations (and their
    escalation tickets, linked by ``conversation_id``) are returned under it.
    ``facts`` is filled by the caller from the memory service.
    """
    conversations: list[dict[str, Any]] = []
    tickets: list[dict[str, Any]] = []
    analytics_events: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    feedback: list[dict[str, Any]] = []
    if external_id:
        conversations = query_all(
            "SELECT * FROM conversations WHERE user_id = ? ORDER BY created_at DESC LIMIT 1000",
            (external_id,),
        )
        conv_ids = [c["conversation_id"] for c in conversations if c.get("conversation_id")]
        if conv_ids:
            ph = ",".join("?" * len(conv_ids))
            tickets = query_all(
                f"SELECT * FROM tickets WHERE conversation_id IN ({ph})",  # noqa: S608 — ?-placeholders
                tuple(conv_ids),
            )
        analytics_events = query_all(
            "SELECT * FROM analytics_events WHERE user_id = ? ORDER BY created_at DESC LIMIT 1000",
            (external_id,),
        )
        sessions = query_all(
            "SELECT * FROM sessions WHERE user_id = ? ORDER BY last_active_at DESC LIMIT 1000",
            (external_id,),
        )
        feedback = query_all(
            "SELECT * FROM feedback WHERE user_id = ? ORDER BY created_at DESC LIMIT 1000",
            (external_id,),
        )
    return {
        "user": get_user(user_id),
        "profile": get_user_profile(user_id),
        "consents": get_active_consents(user_id),
        "conversations": conversations,
        "tickets": tickets,
        "analytics_events": analytics_events,
        "sessions": sessions,
        "feedback": feedback,
        "facts": [],  # filled by the caller from the memory service (export_user)
    }


def delete_user_cascade(user_id: str, external_id: str = "") -> dict[str, int]:
    """DELETE /v1/me — right to erasure.

    Cascades through every table that holds user data.  The audit ledger is
    INTENTIONALLY not touched — erasure must be cryptographically marked, not the
    log rewritten (per UDPA + EU precedent for audit integrity).

    ``user_id`` is the internal UUID (users/profiles/consents). ``external_id`` is
    the OIDC ``sub`` that chat history is keyed by — conversations (and their
    escalation tickets, linked by ``conversation_id``) are erased under it. Memory
    facts are erased by the caller via the memory service.
    """
    counts: dict[str, int] = {}

    # External-id-keyed: chat history + the escalation tickets linked to it.
    if external_id:
        for store, count in delete_user_analytics(external_id).items():
            counts[store] = count
        conv_ids = [
            r["conversation_id"]
            for r in query_all(
                "SELECT conversation_id FROM conversations "
                "WHERE user_id = ? AND conversation_id IS NOT NULL",
                (external_id,),
            )
        ]
        # Delete by user_id AND by conversation_id: the first reaches
        # tickets whose conversation has already been purged, the second
        # reaches tickets raised before user_id was stamped on the row.
        try:
            deleted = execute("DELETE FROM tickets WHERE user_id = ?", (external_id,))
            if conv_ids:
                ph = ",".join("?" * len(conv_ids))
                deleted += execute(
                    f"DELETE FROM tickets WHERE conversation_id IN ({ph})",  # noqa: S608 — ?-placeholders
                    tuple(conv_ids),
                )
            counts["tickets"] = deleted
        except Exception:
            logger.exception("delete_user_cascade: tickets")
            counts["tickets"] = -1
        try:
            if conv_ids:
                ph = ",".join("?" * len(conv_ids))
                execute(
                    f"DELETE FROM conversation_topics WHERE conversation_id IN ({ph})",  # noqa: S608
                    tuple(conv_ids),
                )
        except Exception:
            logger.exception("delete_user_cascade: conversation_topics")
        try:
            counts["conversations"] = execute(
                "DELETE FROM conversations WHERE user_id = ?", (external_id,)
            )
        except Exception:
            logger.exception("delete_user_cascade: conversations")
            counts["conversations"] = -1

    # Internal-UUID-keyed: identity, profile, consent receipts.
    for table, col in (("consent_receipts", "user_id"), ("user_profiles", "user_id"), ("users", "id")):
        try:
            counts[table] = execute(
                f"DELETE FROM {table} WHERE {col} = ?",  # noqa: S608 — hardcoded list
                (user_id,),
            )
        except Exception:
            logger.exception("delete_user_cascade: %s", table)
            counts[table] = -1
    return counts


def ticket_stats(days: int = 30) -> dict[str, Any]:
    """Aggregate ticket statistics for the admin dashboard."""
    conn = _get_connection()
    cutoff = time.time() - (days * 86400)
    row = conn.execute(
        """SELECT
             COUNT(*) as total,
             COALESCE(SUM(CASE WHEN status='open'     THEN 1 ELSE 0 END), 0) as open_count,
             COALESCE(SUM(CASE WHEN status='assigned' THEN 1 ELSE 0 END), 0) as assigned_count,
             COALESCE(SUM(CASE WHEN status='resolved' THEN 1 ELSE 0 END), 0) as resolved_count,
             COALESCE(SUM(CASE WHEN status='wontfix'  THEN 1 ELSE 0 END), 0) as wontfix_count
           FROM tickets
           WHERE created_at >= ?""",
        (cutoff,),
    ).fetchone()
    by_priority = conn.execute(
        """SELECT priority, COUNT(*) as cnt FROM tickets
           WHERE created_at >= ?
           GROUP BY priority ORDER BY cnt DESC""",
        (cutoff,),
    ).fetchall()
    return {
        "period_days": days,
        "total": row["total"] or 0,
        "open": row["open_count"] or 0,
        "assigned": row["assigned_count"] or 0,
        "resolved": row["resolved_count"] or 0,
        "wontfix": row["wontfix_count"] or 0,
        "by_priority": {r["priority"]: r["cnt"] for r in by_priority},
    }


# ---------------------------------------------------------------------------
# Backend dispatch — when ANALYTICS_BACKEND=postgres, re-bind the module's
# public names to the postgres implementations so callers can continue to
# `from .database import log_conversation, ...` unchanged.
# ---------------------------------------------------------------------------
if ANALYTICS_BACKEND == "postgres":
    try:
        from . import postgres as _pg

        init_db = _pg.init_db  # type: ignore
        cleanup_expired_data = _pg.cleanup_expired_data  # type: ignore
        save_feedback = _pg.save_feedback  # type: ignore
        update_feedback_comment = _pg.update_feedback_comment  # type: ignore
        get_feedback_summary = _pg.get_feedback_summary  # type: ignore
        track_event = _pg.track_event  # type: ignore
        get_event_counts = _pg.get_event_counts  # type: ignore
        upsert_session = _pg.upsert_session  # type: ignore
        get_session_stats = _pg.get_session_stats  # type: ignore
        log_conversation = _pg.log_conversation  # type: ignore
        get_recent_turns = _pg.get_recent_turns  # type: ignore
        get_conversation_context = _pg.get_conversation_context  # type: ignore
        get_conversation_topic = _pg.get_conversation_topic  # type: ignore
        upsert_conversation_topic = _pg.upsert_conversation_topic  # type: ignore
        clear_conversation_topic = _pg.clear_conversation_topic  # type: ignore
        get_conversation_transcript = _pg.get_conversation_transcript  # type: ignore
        get_conversation_stats = _pg.get_conversation_stats  # type: ignore
        export_review_feedback = _pg.export_review_feedback  # type: ignore
        # Tickets were missing from this list, so production —  where
        # ANALYTICS_BACKEND=postgres is mandatory — wrote every
        # escalation to a per-replica SQLite file: invisible to an
        # officer whose request landed on another pod, gone on restart,
        # and referencing a conversation in a different database.
        create_ticket = _pg.create_ticket  # type: ignore
        list_tickets = _pg.list_tickets  # type: ignore
        get_ticket = _pg.get_ticket  # type: ignore
        update_ticket = _pg.update_ticket  # type: ignore
        ticket_stats = _pg.ticket_stats  # type: ignore
        find_open_ticket = _pg.find_open_ticket  # type: ignore
        pending_officer_reply = _pg.pending_officer_reply  # type: ignore
        mark_reply_delivered = _pg.mark_reply_delivered  # type: ignore
        sla_stats = _pg.sla_stats  # type: ignore
        heartbeat_ticket_presence = _pg.heartbeat_ticket_presence  # type: ignore
        list_ticket_viewers = _pg.list_ticket_viewers  # type: ignore
        load_flag_overrides = _pg.load_flag_overrides  # type: ignore
        save_flag_override = _pg.save_flag_override  # type: ignore
        clear_flag_override = _pg.clear_flag_override  # type: ignore
        upsert_reminder_inbox = _pg.upsert_reminder_inbox  # type: ignore
        list_reminder_inbox = _pg.list_reminder_inbox  # type: ignore
        enqueue_notification = _pg.enqueue_notification  # type: ignore
        list_notification_outbox = _pg.list_notification_outbox  # type: ignore
        get_answer_override = _pg.get_answer_override  # type: ignore
        upsert_answer_override = _pg.upsert_answer_override  # type: ignore
        list_answer_overrides = _pg.list_answer_overrides  # type: ignore
        delete_answer_override = _pg.delete_answer_override  # type: ignore
        # Identity, consent and workflow state.  Left on SQLite these
        # are per-replica: a consent withdrawal reaches one pod while
        # every other keeps processing the taxpayer as consenting.
        upsert_user = _pg.upsert_user  # type: ignore
        get_user = _pg.get_user  # type: ignore
        get_user_profile = _pg.get_user_profile  # type: ignore
        upsert_user_profile = _pg.upsert_user_profile  # type: ignore
        grant_consent = _pg.grant_consent  # type: ignore
        withdraw_consent = _pg.withdraw_consent  # type: ignore
        get_active_consents = _pg.get_active_consents  # type: ignore
        has_active_consent = _pg.has_active_consent  # type: ignore
        get_workflow_session = _pg.get_workflow_session  # type: ignore
        upsert_workflow_session = _pg.upsert_workflow_session  # type: ignore
        complete_workflow_session = _pg.complete_workflow_session  # type: ignore
        logger.info("Analytics backend: postgres")
    except Exception:
        logger.exception("Postgres backend requested but import failed; falling back to sqlite")
else:
    logger.info("Analytics backend: sqlite")

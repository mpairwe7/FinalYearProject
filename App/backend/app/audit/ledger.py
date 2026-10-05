"""Append-only, hash-chained audit ledger.

Every agentic turn in :mod:`service` writes one :class:`AuditEvent`
via :meth:`AuditLedger.append`.  The ledger guarantees:

- ``prev_hash`` on every row points at the previous row's
  ``row_hash`` — tampering is detectable by rewalking.
- The first row anchors to ``GENESIS_HASH``.
- The row hash commits to the envelope as well as the payload (hash
  format v2): who acted, what kind of event, when, and where in the
  chain. Before v2 only the payload was hashed, so a row's actor could
  be swapped without breaking the chain.
- One row per ``(tenant_id, seq)``: a unique index turns two replicas
  appending at once into a retry instead of a forked chain.
- Seals (Merkle anchors) record the range's Merkle root *and* the chain
  head hash, so rewriting a sealed range consistently — every hash
  recomputed — still shows up against the seal.
- Erasures (UDPA right-to-erasure) write a tombstone event
  whose payload references the erased user — the original rows
  remain intact so the chain stays verifiable.

Schema is created on first use via ``init()``.  Lives in the
shared analytics DB so SQLite dev and Postgres prod both work
via the existing :mod:`database` dispatch.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .merkle import compute_merkle_root, sha256_hex

logger = logging.getLogger(__name__)

GENESIS_HASH = "0" * 64

#: Row-hash formats. v1 hashed ``prev_hash + payload_hash`` only; v2 adds
#: the envelope hash. Rows keep the format they were written with.
HASH_V1 = 1
HASH_V2 = 2
CURRENT_HASH_VERSION = HASH_V2

#: How many times an append re-reads the chain head after losing its
#: sequence number to another writer.
_APPEND_ATTEMPTS = 5


def envelope_hash(
    *, event_id: str, event_type: str, tenant_id: str, user_id: str, ts: float, seq: int
) -> str:
    """Hash of everything about a row except its payload."""
    return sha256_hex(
        json.dumps(
            {
                "event_id": event_id,
                "event_type": event_type,
                "seq": int(seq),
                "tenant_id": tenant_id,
                "ts": float(ts),
                "user_id": user_id or "",
            },
            sort_keys=True,
        )
    )


def chain_hash(prev_hash: str, payload_hash: str, envelope: str, version: int) -> str:
    """The row hash for *version*; v1 ignores the envelope."""
    if version <= HASH_V1:
        return sha256_hex(prev_hash + payload_hash)
    return sha256_hex(prev_hash + payload_hash + envelope)


def is_duplicate_key(exc: BaseException) -> bool:
    """Whether *exc* is a unique-constraint violation on either backend."""
    return isinstance(exc, sqlite3.IntegrityError) or getattr(exc, "sqlstate", None) == "23505"


@dataclass
class AuditEvent:
    """One row in the audit ledger.

    ``payload`` is the structured content of the event — typically
    the output of ``MCPCallResult.to_audit_dict()`` merged with
    request context (tenant, user, model_rev, policy_version,
    index_snapshot_id, etc.).  It's serialised deterministically
    (sorted keys) before hashing so the same logical event always
    produces the same ``payload_hash``.
    """

    event_id: str
    event_type: str  # "generate", "tool_call", "escalate", "erasure_tombstone", ...
    tenant_id: str
    user_id: str
    payload: dict[str, Any]
    ts: float = field(default_factory=time.time)
    seq: int = 0  # monotonic sequence within the ledger
    prev_hash: str = ""
    payload_hash: str = ""
    row_hash: str = ""  # chain_hash(prev_hash, payload_hash, envelope, hash_version)
    hash_version: int = CURRENT_HASH_VERSION

    def compute_hashes(self) -> None:
        """Fill ``payload_hash`` and ``row_hash`` from the other fields."""
        self.payload_hash = sha256_hex(json.dumps(self.payload, sort_keys=True, default=str))
        envelope = envelope_hash(
            event_id=self.event_id,
            event_type=self.event_type,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            ts=self.ts,
            seq=self.seq,
        )
        self.row_hash = chain_hash(self.prev_hash, self.payload_hash, envelope, self.hash_version)

    def to_row(self) -> tuple[Any, ...]:
        return (
            self.event_id,
            self.event_type,
            self.tenant_id,
            self.user_id,
            json.dumps(self.payload, sort_keys=True, default=str),
            self.ts,
            self.seq,
            self.prev_hash,
            self.payload_hash,
            self.row_hash,
            self.hash_version,
        )


def request_rfc3161_timestamp(imprint_sha256: str, tsa_url: str = "") -> str | None:
    """Request an RFC 3161 cryptographic timestamp token for an audit seal hash."""
    url = tsa_url or os.getenv("AUDIT_TSA_URL", "")
    if not url:
        return None
    try:
        import httpx

        resp = httpx.post(
            url,
            json={"hash": imprint_sha256, "algorithm": "sha256", "version": "1"},
            headers={"Content-Type": "application/json"},
            timeout=3.0,
        )
        if resp.status_code in (200, 201):
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            return str(data.get("token") or data.get("timestamp_token") or resp.text[:256])
    except Exception as exc:  # noqa: BLE001
        logger.debug("RFC 3161 TSA timestamp witness unavailable: %s", exc)
    return None


class AuditLedger:
    """SQLite-backed append-only hash-chained audit log.

    Thread-safe via a single mutex around append — the critical
    section is short (one INSERT).  Across processes and replicas the
    unique ``(tenant_id, seq)`` index is the arbiter: the writer that
    loses a sequence number re-reads the head and chains onto it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        from .. import database as db

        db.execute_script(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id     TEXT PRIMARY KEY,
                event_type   TEXT NOT NULL,
                tenant_id    TEXT NOT NULL DEFAULT 'default',
                user_id      TEXT DEFAULT '',
                payload      TEXT NOT NULL,
                ts           DOUBLE PRECISION NOT NULL,
                seq          INTEGER NOT NULL,
                prev_hash    TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                row_hash     TEXT NOT NULL,
                hash_version INTEGER NOT NULL DEFAULT 1
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
                created_at   DOUBLE PRECISION NOT NULL,
                head_hash    TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_audit_anchors_created
                ON audit_anchors(created_at);
            """
        )
        self._add_column("audit_events", "hash_version", "INTEGER NOT NULL DEFAULT 1")
        self._add_column("audit_anchors", "head_hash", "TEXT NOT NULL DEFAULT ''")
        # Created separately: a ledger written before these existed may
        # already hold a forked chain (two rows with one seq). That must not
        # stop the service; the verifier reports the fork instead.
        for name, table, columns in (
            ("idx_audit_tenant_seq", "audit_events", "tenant_id, seq"),
            ("idx_audit_anchor_start", "audit_anchors", "tenant_id, first_seq"),
        ):
            try:
                db.execute_script(f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table}({columns})")
            except Exception:
                logger.exception("audit ledger: cannot create unique index %s; run the verifier", name)

    @staticmethod
    def _has_column(table: str, column: str) -> bool:
        from .. import database as db

        try:
            db.query_all(f"SELECT {column} FROM {table} LIMIT 1")  # noqa: S608 - fixed identifiers
        except Exception:
            return False
        return True

    def _add_column(self, table: str, column: str, ddl: str) -> None:
        """Add *column* to a table created before it existed; safe to race."""
        from .. import database as db

        if self._has_column(table, column):
            return
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        except Exception:
            if not self._has_column(table, column):  # not a lost race: a real failure
                raise

    # -- Append --------------------------------------------------------
    def append(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        tenant_id: str = "default",
        user_id: str = "",
    ) -> AuditEvent:
        """Append a new event, chaining it to the previous row.

        Runs under a lock so two concurrent callers can't both read
        the same prev_hash + insert with the same seq.
        """
        from .. import database as db

        with self._lock:
            attempt = 0
            while True:
                attempt += 1
                # Chains are per tenant, so tenants verify independently.
                row = db.query_one(
                    """SELECT seq, row_hash FROM audit_events
                       WHERE tenant_id = ?
                       ORDER BY seq DESC LIMIT 1""",
                    (tenant_id,),
                )
                event = AuditEvent(
                    event_id=str(uuid.uuid4()),
                    event_type=event_type,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    payload=payload,
                    ts=time.time(),
                    seq=(int(row["seq"]) + 1) if row else 1,
                    prev_hash=str(row["row_hash"]) if row else GENESIS_HASH,
                )
                event.compute_hashes()
                try:
                    db.execute(
                        """INSERT INTO audit_events
                           (event_id, event_type, tenant_id, user_id, payload,
                            ts, seq, prev_hash, payload_hash, row_hash, hash_version)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        event.to_row(),
                    )
                except Exception as exc:
                    if attempt < _APPEND_ATTEMPTS and is_duplicate_key(exc):
                        # Another replica took this seq first: chain onto its row.
                        logger.warning("audit append lost seq %d to another writer; retrying", event.seq)
                        continue
                    logger.exception("audit append failed")
                    raise
                return event

    # -- Reads ---------------------------------------------------------
    def read(
        self,
        tenant_id: str = "default",
        user_id: str | None = None,
        since_seq: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        from .. import database as db

        sql = "SELECT * FROM audit_events WHERE tenant_id = ? AND seq > ?"
        params: list[Any] = [tenant_id, since_seq]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        sql += " ORDER BY seq ASC LIMIT ?"
        params.append(limit)
        rows = db.query_all(sql, tuple(params))
        out: list[dict[str, Any]] = []
        for d in rows:
            try:
                d["payload"] = json.loads(d["payload"])
            except Exception:
                pass
            out.append(d)
        return out

    def query(
        self,
        tenant_id: str = "default",
        *,
        event_type_prefix: str = "",
        user_id: str = "",
        since_ts: float | None = None,
        until_ts: float | None = None,
        before_seq: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Newest-first page of events for the auditor's audit-trail view.

        Filters are all optional and combine with AND. ``event_type_prefix``
        matches the start of the type ("staff." selects every staff action).
        ``before_seq`` pages backwards: pass the smallest ``seq`` of the
        previous page. ``limit`` is clamped to 1..200 so one request can never
        pull the whole ledger.
        """
        from .. import database as db

        sql = "SELECT * FROM audit_events WHERE tenant_id = ?"
        params: list[Any] = [tenant_id]
        if event_type_prefix:
            sql += " AND event_type LIKE ?"
            escaped = event_type_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(escaped + "%")
            sql += " ESCAPE '\\'"
        if user_id:
            sql += " AND user_id = ?"
            params.append(user_id)
        if since_ts is not None:
            sql += " AND ts >= ?"
            params.append(since_ts)
        if until_ts is not None:
            sql += " AND ts < ?"
            params.append(until_ts)
        if before_seq is not None:
            sql += " AND seq < ?"
            params.append(before_seq)
        sql += " ORDER BY seq DESC LIMIT ?"
        params.append(max(1, min(int(limit), 200)))
        out: list[dict[str, Any]] = []
        for row in db.query_all(sql, tuple(params)):
            try:
                row["payload"] = json.loads(row["payload"])
            except (TypeError, ValueError):
                row["payload"] = {}
            out.append(row)
        return out

    def latest_anchor(self, tenant_id: str = "default") -> dict[str, Any] | None:
        """The seal covering the newest rows for *tenant_id*, if any."""
        from .. import database as db

        return db.query_one(
            """SELECT anchor_id, first_seq, last_seq, merkle_root, head_hash, created_at
               FROM audit_anchors WHERE tenant_id = ?
               ORDER BY last_seq DESC LIMIT 1""",
            (tenant_id,),
        )

    def anchors(self, tenant_id: str = "default") -> list[dict[str, Any]]:
        """Every seal for *tenant_id*, oldest range first."""
        from .. import database as db

        return db.query_all(
            """SELECT anchor_id, first_seq, last_seq, merkle_root, head_hash, created_at
               FROM audit_anchors WHERE tenant_id = ?
               ORDER BY first_seq ASC""",
            (tenant_id,),
        )

    def head_seq(self, tenant_id: str = "default") -> int:
        """The newest sequence number for *tenant_id*; 0 when it has no rows."""
        from .. import database as db

        row = db.query_one("SELECT MAX(seq) AS n FROM audit_events WHERE tenant_id = ?", (tenant_id,))
        return int(row["n"] or 0) if row else 0

    def tenants(self) -> list[str]:
        """Every tenant with at least one event."""
        from .. import database as db

        return [str(r["tenant_id"]) for r in db.query_all("SELECT DISTINCT tenant_id FROM audit_events")]

    def count(self, tenant_id: str = "default") -> int:
        from .. import database as db

        row = db.query_one(
            "SELECT COUNT(*) AS n FROM audit_events WHERE tenant_id = ?",
            (tenant_id,),
        )
        return int(row["n"] if row else 0)

    def last_row_hash(self, tenant_id: str = "default") -> str:
        from .. import database as db

        row = db.query_one(
            """SELECT row_hash FROM audit_events
               WHERE tenant_id = ?
               ORDER BY seq DESC LIMIT 1""",
            (tenant_id,),
        )
        return str(row["row_hash"]) if row else GENESIS_HASH

    # -- Erasure tombstone (UDPA-compliant) ---------------------------
    def erasure_tombstone(
        self,
        user_id: str,
        reason: str = "subject_right_erasure",
        tenant_id: str = "default",
    ) -> AuditEvent:
        """Append a tombstone noting erasure of *user_id*.

        The tombstone does NOT delete prior events — the chain stays
        intact.  This is the EU/Uganda DPA precedent for reconciling
        right-to-erasure with audit integrity.
        """
        return self.append(
            "erasure_tombstone",
            payload={
                "erased_user_id_sha256": sha256_hex(user_id),
                "reason": reason,
                "erased_at": time.time(),
            },
            tenant_id=tenant_id,
        )

    # -- Anchoring (seals) ---------------------------------------------
    def anchor_range(
        self,
        first_seq: int,
        last_seq: int,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        """Seal [first_seq, last_seq]: record its Merkle root and chain head.

        The Merkle root covers the payload hashes in the range; the head hash
        is the stored ``row_hash`` at ``last_seq``, which commits to every row
        up to it. Raises a unique-constraint error when another writer already
        sealed a range starting at ``first_seq`` (see :meth:`seal_pending`).
        """
        from .. import database as db

        rows = db.query_all(
            """SELECT seq, payload_hash, row_hash FROM audit_events
               WHERE tenant_id = ? AND seq BETWEEN ? AND ?
               ORDER BY seq ASC""",
            (tenant_id, first_seq, last_seq),
        )
        head = rows[-1] if rows and int(rows[-1]["seq"]) == last_seq else None
        merkle = compute_merkle_root(r["payload_hash"] for r in rows)
        anchor = {
            "anchor_id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "first_seq": first_seq,
            "last_seq": last_seq,
            "merkle_root": merkle,
            "head_hash": str(head["row_hash"]) if head else "",
            "created_at": time.time(),
        }
        tsa_url = os.getenv("AUDIT_TSA_URL", "")
        tsa_token = request_rfc3161_timestamp(merkle, tsa_url) if tsa_url else None
        if tsa_token:
            anchor["tsa_token"] = tsa_token
        try:
            db.execute(
                """INSERT INTO audit_anchors
                   (anchor_id, tenant_id, first_seq, last_seq, merkle_root, head_hash, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    anchor["anchor_id"],
                    anchor["tenant_id"],
                    anchor["first_seq"],
                    anchor["last_seq"],
                    anchor["merkle_root"],
                    anchor["head_hash"],
                    anchor["created_at"],
                ),
            )
        except Exception as exc:
            if not is_duplicate_key(exc):
                logger.exception("anchor_range failed")
            raise
        # The witness line: shipped with the logs, it puts the seal outside
        # this database, so rewriting the ledger and its seal table together
        # still disagrees with what the log pipeline recorded.
        logger.info(
            "audit seal tenant=%s seq=%d..%d merkle_root=%s head_hash=%s",
            tenant_id,
            first_seq,
            last_seq,
            anchor["merkle_root"],
            anchor["head_hash"],
        )
        return anchor

    def seal_pending(self, tenant_id: str = "default") -> dict[str, Any] | None:
        """Seal every row after the last seal; ``None`` when nothing is new.

        Safe to run on every replica at once: seals are unique by their first
        sequence number, so the replicas that lose the race get ``None``.
        """
        latest = self.latest_anchor(tenant_id)
        first = int(latest["last_seq"]) + 1 if latest else 1
        last = self.head_seq(tenant_id)
        if last < first:
            return None
        try:
            return self.anchor_range(first, last, tenant_id)
        except Exception as exc:
            if is_duplicate_key(exc):
                return None
            raise


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_ledger: AuditLedger | None = None


def get_ledger() -> AuditLedger:
    global _ledger
    if _ledger is None:
        _ledger = AuditLedger()
    return _ledger


def reset_ledger() -> None:
    global _ledger
    _ledger = None

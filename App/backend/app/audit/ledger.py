"""Append-only, hash-chained audit ledger.

Every agentic turn in :mod:`service` writes one :class:`AuditEvent`
via :meth:`AuditLedger.append`.  The ledger guarantees:

- ``prev_hash`` on every row points at the previous row's
  ``payload_hash`` — tampering is detectable by rewalking.
- The first row anchors to ``GENESIS_HASH``.
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
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .merkle import compute_merkle_root, sha256_hex

logger = logging.getLogger(__name__)

GENESIS_HASH = "0" * 64


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
    row_hash: str = ""  # sha256(prev_hash + payload_hash)

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
        )


class AuditLedger:
    """SQLite-backed append-only hash-chained audit log.

    Thread-safe via a single mutex around append/rewalk — the
    critical section is short (one INSERT).  Multi-process
    coordination relies on SQLite's own WAL locking; for Postgres
    the same pattern works with advisory locks.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        from .. import database as db

        conn = db._get_connection()
        conn.executescript(
            """
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
            """
        )
        conn.commit()

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
            conn = db._get_connection()
            # Last row for this tenant (or global if tenant-scoped chains
            # aren't used yet).  We chain per-tenant so tenants can be
            # verified independently.
            row = conn.execute(
                """SELECT seq, row_hash FROM audit_events
                   WHERE tenant_id = ?
                   ORDER BY seq DESC LIMIT 1""",
                (tenant_id,),
            ).fetchone()

            prev_hash = row["row_hash"] if row else GENESIS_HASH
            seq = (row["seq"] + 1) if row else 1

            event = AuditEvent(
                event_id=str(uuid.uuid4()),
                event_type=event_type,
                tenant_id=tenant_id,
                user_id=user_id,
                payload=payload,
                ts=time.time(),
                seq=seq,
                prev_hash=prev_hash,
            )
            event.payload_hash = sha256_hex(json.dumps(event.payload, sort_keys=True, default=str))
            event.row_hash = sha256_hex(prev_hash + event.payload_hash)

            try:
                conn.execute(
                    """INSERT INTO audit_events
                       (event_id, event_type, tenant_id, user_id, payload,
                        ts, seq, prev_hash, payload_hash, row_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    event.to_row(),
                )
                conn.commit()
            except Exception:
                logger.exception("audit append failed")
                conn.rollback()
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

        conn = db._get_connection()
        sql = "SELECT * FROM audit_events WHERE tenant_id = ? AND seq > ?"
        params: list[Any] = [tenant_id, since_seq]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        sql += " ORDER BY seq ASC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"])
            except Exception:
                pass
            out.append(d)
        return out

    def count(self, tenant_id: str = "default") -> int:
        from .. import database as db

        conn = db._get_connection()
        row = conn.execute(
            "SELECT COUNT(*) as n FROM audit_events WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        return int(row["n"] if row else 0)

    def last_row_hash(self, tenant_id: str = "default") -> str:
        from .. import database as db

        conn = db._get_connection()
        row = conn.execute(
            """SELECT row_hash FROM audit_events
               WHERE tenant_id = ?
               ORDER BY seq DESC LIMIT 1""",
            (tenant_id,),
        ).fetchone()
        return row["row_hash"] if row else GENESIS_HASH

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

    # -- Anchoring -----------------------------------------------------
    def anchor_range(
        self,
        first_seq: int,
        last_seq: int,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        """Compute a Merkle root over [first_seq, last_seq] and record it.

        Called from a nightly worker; returns the anchor row.
        """
        from .. import database as db

        conn = db._get_connection()
        rows = conn.execute(
            """SELECT payload_hash FROM audit_events
               WHERE tenant_id = ? AND seq BETWEEN ? AND ?
               ORDER BY seq ASC""",
            (tenant_id, first_seq, last_seq),
        ).fetchall()
        leaves = [r["payload_hash"] for r in rows]
        root = compute_merkle_root(leaves)
        anchor = {
            "anchor_id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "first_seq": first_seq,
            "last_seq": last_seq,
            "merkle_root": root,
            "created_at": time.time(),
        }
        try:
            conn.execute(
                """INSERT INTO audit_anchors
                   (anchor_id, tenant_id, first_seq, last_seq, merkle_root, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    anchor["anchor_id"],
                    anchor["tenant_id"],
                    anchor["first_seq"],
                    anchor["last_seq"],
                    anchor["merkle_root"],
                    anchor["created_at"],
                ),
            )
            conn.commit()
        except Exception:
            logger.exception("anchor_range failed")
            conn.rollback()
            raise
        return anchor


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

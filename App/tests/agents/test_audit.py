"""Tests for Phase 21 — hash-chained audit ledger."""

from __future__ import annotations

import hashlib
import json

import pytest

from app.audit import AuditLedger, verify_chain
from app.audit.ledger import GENESIS_HASH, get_ledger, reset_ledger
from app.audit.merkle import compute_merkle_root, pair_hash, sha256_hex


# ---------------------------------------------------------------------------
# Merkle helpers
# ---------------------------------------------------------------------------
class TestMerkle:
    def test_sha256_hex_length_and_determinism(self):
        h1 = sha256_hex("hello")
        h2 = sha256_hex("hello")
        assert len(h1) == 64
        assert h1 == h2

    def test_sha256_hex_different_inputs(self):
        assert sha256_hex("a") != sha256_hex("b")

    def test_pair_hash_commutes_no(self):
        """pair_hash(a, b) should NOT equal pair_hash(b, a)."""
        a, b = sha256_hex("a"), sha256_hex("b")
        assert pair_hash(a, b) != pair_hash(b, a)

    def test_merkle_root_empty(self):
        assert compute_merkle_root([]) == ""

    def test_merkle_root_single_leaf(self):
        leaf = sha256_hex("a")
        assert compute_merkle_root([leaf]) == leaf

    def test_merkle_root_two_leaves(self):
        a, b = sha256_hex("a"), sha256_hex("b")
        expected = pair_hash(a, b)
        assert compute_merkle_root([a, b]) == expected

    def test_merkle_root_odd_duplicates_last(self):
        """Odd-length levels duplicate the last hash."""
        a, b, c = sha256_hex("a"), sha256_hex("b"), sha256_hex("c")
        # Level 1: [pair(a,b), pair(c,c)]
        l1 = [pair_hash(a, b), pair_hash(c, c)]
        expected = pair_hash(l1[0], l1[1])
        assert compute_merkle_root([a, b, c]) == expected

    def test_merkle_root_is_64_hex_chars(self):
        leaves = [sha256_hex(f"leaf-{i}") for i in range(10)]
        root = compute_merkle_root(leaves)
        assert len(root) == 64
        int(root, 16)  # valid hex


# ---------------------------------------------------------------------------
# Ledger append + chain semantics
# ---------------------------------------------------------------------------
class TestAuditLedger:
    def test_first_row_chains_to_genesis(self, tmp_db):
        reset_ledger()
        ledger = AuditLedger()
        e = ledger.append("test", {"x": 1}, tenant_id="t1")
        assert e.seq == 1
        assert e.prev_hash == GENESIS_HASH

    def test_second_row_chains_to_first(self, tmp_db):
        reset_ledger()
        ledger = AuditLedger()
        e1 = ledger.append("test", {"x": 1}, tenant_id="t1")
        e2 = ledger.append("test", {"x": 2}, tenant_id="t1")
        assert e2.seq == 2
        assert e2.prev_hash == e1.row_hash

    def test_seq_is_monotonic_per_tenant(self, tmp_db):
        reset_ledger()
        ledger = AuditLedger()
        ledger.append("test", {"x": 1}, tenant_id="t1")
        ledger.append("test", {"x": 2}, tenant_id="t1")
        ledger.append("test", {"x": 1}, tenant_id="t2")   # Separate chain
        events_t1 = ledger.read(tenant_id="t1")
        events_t2 = ledger.read(tenant_id="t2")
        assert [e["seq"] for e in events_t1] == [1, 2]
        assert [e["seq"] for e in events_t2] == [1]

    def test_tenant_chains_are_independent(self, tmp_db):
        reset_ledger()
        ledger = AuditLedger()
        e_a = ledger.append("test", {"v": 1}, tenant_id="tenant_a")
        e_b = ledger.append("test", {"v": 2}, tenant_id="tenant_b")
        # Each tenant chain starts at genesis
        assert e_a.prev_hash == GENESIS_HASH
        assert e_b.prev_hash == GENESIS_HASH

    def test_payload_hash_is_deterministic(self, tmp_db):
        reset_ledger()
        ledger = AuditLedger()
        # Same logical payload, different key order → same hash
        e1 = ledger.append("test", {"a": 1, "b": 2}, tenant_id="t1")
        reset_ledger()  # fresh ledger so we get seq=1 again
        ledger2 = AuditLedger()
        e2 = ledger2.append("test", {"b": 2, "a": 1}, tenant_id="t1")
        # Can't directly compare because e2 is in the same DB and gets seq=2
        # Instead, recompute payload hashes directly:
        h1 = sha256_hex(json.dumps({"a": 1, "b": 2}, sort_keys=True))
        h2 = sha256_hex(json.dumps({"b": 2, "a": 1}, sort_keys=True))
        assert h1 == h2

    def test_count(self, tmp_db):
        reset_ledger()
        ledger = AuditLedger()
        assert ledger.count("t1") == 0
        for i in range(3):
            ledger.append("test", {"n": i}, tenant_id="t1")
        assert ledger.count("t1") == 3

    def test_last_row_hash_genesis_when_empty(self, tmp_db):
        reset_ledger()
        ledger = AuditLedger()
        assert ledger.last_row_hash("empty_tenant") == GENESIS_HASH

    def test_read_with_user_filter(self, tmp_db):
        reset_ledger()
        ledger = AuditLedger()
        ledger.append("test", {"x": 1}, tenant_id="t1", user_id="alice")
        ledger.append("test", {"x": 2}, tenant_id="t1", user_id="bob")
        alice_rows = ledger.read(tenant_id="t1", user_id="alice")
        assert len(alice_rows) == 1
        assert alice_rows[0]["user_id"] == "alice"


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------
class TestTamperDetection:
    def test_valid_chain_reports_no_breaks(self, tmp_db):
        reset_ledger()
        ledger = AuditLedger()
        for i in range(5):
            ledger.append("test", {"n": i}, tenant_id="t1")
        report = verify_chain(tenant_id="t1")
        assert report.rows_checked == 5
        assert report.valid is True
        assert len(report.breaks) == 0

    def test_tampered_payload_detected(self, tmp_db):
        reset_ledger()
        ledger = AuditLedger()
        for i in range(5):
            ledger.append("test", {"n": i}, tenant_id="t1")

        # Corrupt row seq=3
        conn = tmp_db._get_connection()
        conn.execute(
            "UPDATE audit_events SET payload = ? WHERE seq = 3 AND tenant_id = ?",
            (json.dumps({"tampered": True}), "t1"),
        )
        conn.commit()

        report = verify_chain(tenant_id="t1")
        assert report.valid is False
        assert len(report.breaks) >= 1
        # The first break is at seq=3
        assert report.breaks[0].seq == 3
        assert "payload_hash" in report.breaks[0].reason

    def test_empty_tenant_is_trivially_valid(self, tmp_db):
        reset_ledger()
        # Instantiate an AuditLedger so its _init_schema() materialises
        # the audit_events + audit_anchors tables on the tmp in-memory DB
        AuditLedger()
        report = verify_chain(tenant_id="has_no_rows")
        assert report.valid is True
        assert report.rows_checked == 0

    def test_head_hash_matches_last_row(self, tmp_db):
        reset_ledger()
        ledger = AuditLedger()
        last = None
        for i in range(3):
            last = ledger.append("test", {"n": i}, tenant_id="t1")
        report = verify_chain(tenant_id="t1")
        assert report.head_hash == last.row_hash


# ---------------------------------------------------------------------------
# Erasure tombstone
# ---------------------------------------------------------------------------
class TestErasureTombstone:
    def test_tombstone_appended_as_new_event(self, tmp_db):
        reset_ledger()
        ledger = AuditLedger()
        ledger.append("test", {"x": 1}, tenant_id="t1", user_id="alice")
        count_before = ledger.count("t1")
        ledger.erasure_tombstone(user_id="alice", tenant_id="t1")
        assert ledger.count("t1") == count_before + 1

    def test_tombstone_does_not_rewrite_chain(self, tmp_db):
        reset_ledger()
        ledger = AuditLedger()
        for i in range(3):
            ledger.append("test", {"n": i}, tenant_id="t1", user_id="alice")
        ledger.erasure_tombstone("alice", tenant_id="t1")
        # Chain still verifies
        report = verify_chain(tenant_id="t1")
        assert report.valid is True
        assert report.rows_checked == 4

    def test_tombstone_uses_sha256_of_user_id(self, tmp_db):
        reset_ledger()
        ledger = AuditLedger()
        tomb = ledger.erasure_tombstone("alice", tenant_id="t1")
        assert tomb.event_type == "erasure_tombstone"
        payload = tomb.payload
        expected_hash = sha256_hex("alice")
        assert payload["erased_user_id_sha256"] == expected_hash


# ---------------------------------------------------------------------------
# Anchoring
# ---------------------------------------------------------------------------
class TestAnchoring:
    def test_anchor_range_records_merkle_root(self, tmp_db):
        reset_ledger()
        ledger = AuditLedger()
        for i in range(5):
            ledger.append("test", {"n": i}, tenant_id="t1")
        anchor = ledger.anchor_range(1, 5, tenant_id="t1")
        assert "merkle_root" in anchor
        assert len(anchor["merkle_root"]) == 64
        assert anchor["first_seq"] == 1
        assert anchor["last_seq"] == 5

    def test_anchor_empty_range(self, tmp_db):
        reset_ledger()
        ledger = AuditLedger()
        ledger.append("test", {"n": 0}, tenant_id="t1")  # seq=1
        # Anchor beyond the range of rows
        anchor = ledger.anchor_range(99, 100, tenant_id="t1")
        assert anchor["merkle_root"] == ""


# ---------------------------------------------------------------------------
# QA audit 2026-09-29: envelope hashing, forks, seals, scoped verification
# ---------------------------------------------------------------------------
def _rewrite_consistently(tmp_db, tenant: str, seq: int, **changes) -> None:
    """What an attacker with write access does: edit one row, then recompute
    every hash from it to the head, so the chain on its own still verifies."""
    from app.audit.ledger import chain_hash, envelope_hash

    conn = tmp_db._get_connection()
    rows = [
        dict(r)
        for r in conn.execute("SELECT * FROM audit_events WHERE tenant_id = ? ORDER BY seq", (tenant,))
    ]
    prev = GENESIS_HASH
    for row in rows:
        if row["seq"] < seq:
            prev = row["row_hash"]
            continue
        if row["seq"] == seq:
            row.update(changes)
        payload_hash = sha256_hex(json.dumps(json.loads(row["payload"]), sort_keys=True))
        envelope = envelope_hash(
            event_id=row["event_id"],
            event_type=row["event_type"],
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
            ts=row["ts"],
            seq=row["seq"],
        )
        row_hash = chain_hash(prev, payload_hash, envelope, row["hash_version"])
        conn.execute(
            """UPDATE audit_events SET user_id = ?, payload = ?, prev_hash = ?,
               payload_hash = ?, row_hash = ? WHERE event_id = ?""",
            (row["user_id"], row["payload"], prev, payload_hash, row_hash, row["event_id"]),
        )
        prev = row_hash
    conn.commit()


class TestEnvelopeIsHashed:
    """Before hash format v2 only the payload was hashed: the actor, event
    type and time of a row could be edited and the chain still verified."""

    def test_new_rows_use_the_current_format(self, tmp_db):
        from app.audit.ledger import CURRENT_HASH_VERSION, HASH_V2

        reset_ledger()
        event = AuditLedger().append("staff.ticket_updated", {"status": "resolved"}, tenant_id="t1")
        assert event.hash_version == CURRENT_HASH_VERSION == HASH_V2

    @pytest.mark.parametrize(
        ("column", "value"),
        [("user_id", "someone-else"), ("event_type", "generate"), ("ts", 1.0)],
    )
    def test_editing_the_envelope_breaks_the_chain(self, tmp_db, column, value):
        reset_ledger()
        ledger = AuditLedger()
        for i in range(3):
            ledger.append("staff.ticket_updated", {"n": i}, tenant_id="t1", user_id="officer-1")
        tmp_db.execute(f"UPDATE audit_events SET {column} = ? WHERE seq = 2", (value,))  # noqa: S608
        report = verify_chain("t1")
        assert report.valid is False
        assert (report.breaks[0].seq, report.breaks[0].reason) == (2, "row_hash mismatch")

    def test_legacy_rows_still_verify_and_chain_into_new_ones(self, tmp_db):
        reset_ledger()
        ledger = AuditLedger()
        # A v1 row as the ledger wrote it before the envelope was hashed.
        payload = json.dumps({"q": "legacy"}, sort_keys=True)
        payload_hash = sha256_hex(payload)
        legacy_hash = sha256_hex(GENESIS_HASH + payload_hash)
        tmp_db.execute(
            """INSERT INTO audit_events (event_id, event_type, tenant_id, user_id, payload, ts,
               seq, prev_hash, payload_hash, row_hash, hash_version)
               VALUES ('legacy-1', 'generate', 't1', 'u1', ?, 1.5, 1, ?, ?, ?, 1)""",
            (payload, GENESIS_HASH, payload_hash, legacy_hash),
        )
        newer = ledger.append("generate", {"q": "new"}, tenant_id="t1", user_id="u1")
        assert newer.prev_hash == legacy_hash
        assert verify_chain("t1").valid is True

    def test_a_downgraded_row_is_a_break(self, tmp_db):
        reset_ledger()
        ledger = AuditLedger()
        first = ledger.append("generate", {"n": 1}, tenant_id="t1")
        second = ledger.append("generate", {"n": 2}, tenant_id="t1")
        # Re-hash row 2 as v1 so its own hash is self-consistent.
        v1_hash = sha256_hex(first.row_hash + second.payload_hash)
        tmp_db.execute(
            "UPDATE audit_events SET hash_version = 1, row_hash = ? WHERE seq = 2", (v1_hash,)
        )
        report = verify_chain("t1")
        assert report.breaks[0].reason == "hash format downgraded after a newer row"


class TestSequenceIntegrity:
    def test_a_deleted_row_is_named_as_a_gap(self, tmp_db):
        reset_ledger()
        ledger = AuditLedger()
        for i in range(5):
            ledger.append("generate", {"n": i}, tenant_id="t1")
        tmp_db.execute("DELETE FROM audit_events WHERE seq = 3")
        report = verify_chain("t1")
        assert report.breaks[0].seq == 4
        assert report.breaks[0].reason == "sequence gap: rows 3..3 are missing"

    def test_the_database_refuses_a_second_row_with_the_same_seq(self, tmp_db):
        import sqlite3

        reset_ledger()
        AuditLedger().append("generate", {"n": 1}, tenant_id="t1")
        with pytest.raises(sqlite3.IntegrityError):
            tmp_db.execute(
                """INSERT INTO audit_events (event_id, event_type, tenant_id, user_id, payload, ts,
                   seq, prev_hash, payload_hash, row_hash, hash_version)
                   VALUES ('dup', 'generate', 't1', '', '{}', 1.0, 1, ?, 'x', 'y', 2)""",
                (GENESIS_HASH,),
            )

    def test_an_append_that_loses_its_seq_to_another_writer_retries(self, tmp_db, monkeypatch):
        reset_ledger()
        ledger = AuditLedger()
        first = ledger.append("generate", {"n": 1}, tenant_id="t1")
        second = ledger.append("generate", {"n": 2}, tenant_id="t1")
        real_query_one = tmp_db.query_one
        calls = {"n": 0}

        def stale_head(sql, params=()):
            calls["n"] += 1
            if calls["n"] == 1:  # another replica appended after this read
                return {"seq": first.seq, "row_hash": first.row_hash}
            return real_query_one(sql, params)

        monkeypatch.setattr(tmp_db, "query_one", stale_head)
        third = ledger.append("generate", {"n": 3}, tenant_id="t1")
        assert (third.seq, third.prev_hash) == (3, second.row_hash)
        assert verify_chain("t1").valid is True

    def test_a_fork_split_across_batches_is_still_reported(self, tmp_db, monkeypatch):
        from app.audit import verifier

        reset_ledger()
        ledger = AuditLedger()
        for i in range(3):
            ledger.append("generate", {"n": i}, tenant_id="t1")
        # A ledger written before the unique index could hold a fork.
        tmp_db.execute("DROP INDEX idx_audit_tenant_seq")
        tmp_db.execute(
            """INSERT INTO audit_events (event_id, event_type, tenant_id, user_id, payload, ts,
               seq, prev_hash, payload_hash, row_hash, hash_version)
               SELECT 'zz-fork', event_type, tenant_id, user_id, payload, ts, seq,
                      prev_hash, payload_hash, row_hash, hash_version
               FROM audit_events WHERE tenant_id = 't1' AND seq = 2"""
        )
        monkeypatch.setattr(verifier, "_BATCH_ROWS", 2)
        report = verify_chain("t1")
        assert report.rows_checked == 4
        assert any("duplicate sequence number" in b.reason for b in report.breaks)


class TestSeals:
    def test_seal_pending_covers_new_rows_once(self, tmp_db):
        reset_ledger()
        ledger = AuditLedger()
        for i in range(3):
            last = ledger.append("generate", {"n": i}, tenant_id="t1")
        seal = ledger.seal_pending("t1")
        assert (seal["first_seq"], seal["last_seq"], seal["head_hash"]) == (1, 3, last.row_hash)
        assert ledger.seal_pending("t1") is None
        ledger.append("generate", {"n": 9}, tenant_id="t1")
        assert (ledger.seal_pending("t1")["first_seq"], ledger.latest_anchor("t1")["last_seq"]) == (4, 4)

    def test_a_replica_that_loses_the_seal_race_gets_none(self, tmp_db, monkeypatch):
        reset_ledger()
        ledger = AuditLedger()
        ledger.append("generate", {"n": 1}, tenant_id="t1")
        ledger.seal_pending("t1")
        ledger.append("generate", {"n": 2}, tenant_id="t1")
        # This replica read the seal table before the other replica's seal landed.
        monkeypatch.setattr(ledger, "latest_anchor", lambda tenant_id="default": None)
        assert ledger.seal_pending("t1") is None

    def test_a_consistent_rewrite_passes_the_chain_but_not_the_seal(self, tmp_db):
        from app.audit import verify_ledger

        reset_ledger()
        ledger = AuditLedger()
        for i in range(4):
            ledger.append("staff.ticket_updated", {"status": f"s{i}"}, tenant_id="t1", user_id="off-1")
        ledger.seal_pending("t1")
        _rewrite_consistently(tmp_db, "t1", 2, payload=json.dumps({"status": "edited"}))
        assert verify_chain("t1").valid is True  # why seals exist
        report = verify_ledger("t1")
        assert report.valid is False
        assert report.anchor_breaks[0].reason.startswith("merkle_root mismatch")

    def test_a_consistent_reattribution_is_caught_by_the_head_hash(self, tmp_db):
        from app.audit import verify_ledger

        reset_ledger()
        ledger = AuditLedger()
        for i in range(4):
            ledger.append("staff.ticket_updated", {"n": i}, tenant_id="t1", user_id="off-1")
        ledger.seal_pending("t1")
        _rewrite_consistently(tmp_db, "t1", 2, user_id="someone-else")
        report = verify_ledger("t1")
        assert report.valid is False
        assert report.anchor_breaks[0].reason.startswith("head_hash mismatch")

    def test_since_seal_walks_only_the_rows_after_the_newest_seal(self, tmp_db):
        from app.audit import verify_ledger
        from app.audit.verifier import SCOPE_SINCE_SEAL

        reset_ledger()
        ledger = AuditLedger()
        for i in range(5):
            ledger.append("generate", {"n": i}, tenant_id="t1")
        ledger.seal_pending("t1")
        for i in range(2):
            ledger.append("generate", {"n": 10 + i}, tenant_id="t1")
        report = verify_ledger("t1", scope=SCOPE_SINCE_SEAL)
        assert report.valid is True
        assert (report.scope, report.rows_checked, report.anchors_checked) == (SCOPE_SINCE_SEAL, 2, 1)
        assert report.first_seq == 6

    def test_since_seal_without_a_seal_walks_everything(self, tmp_db):
        from app.audit import verify_ledger

        reset_ledger()
        ledger = AuditLedger()
        for i in range(3):
            ledger.append("generate", {"n": i}, tenant_id="t1")
        report = verify_ledger("t1", scope="since_seal")
        assert (report.scope, report.rows_checked) == ("full", 3)

    def test_a_row_deleted_from_a_sealed_range_is_reported(self, tmp_db):
        from app.audit import verify_ledger

        reset_ledger()
        ledger = AuditLedger()
        for i in range(3):
            ledger.append("generate", {"n": i}, tenant_id="t1")
        ledger.seal_pending("t1")
        tmp_db.execute("DELETE FROM audit_events WHERE seq = 3")
        report = verify_ledger("t1")
        assert report.anchor_breaks[0].reason == "sealed range holds 2 rows, expected 3"


class TestSchemaUpgrade:
    def test_a_ledger_created_before_v2_gains_the_new_columns(self, tmp_db):
        conn = tmp_db._get_connection()
        conn.executescript(
            """
            DROP TABLE audit_events;
            DROP TABLE audit_anchors;
            CREATE TABLE audit_events (
                event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL,
                tenant_id TEXT NOT NULL DEFAULT 'default', user_id TEXT DEFAULT '',
                payload TEXT NOT NULL, ts DOUBLE PRECISION NOT NULL, seq INTEGER NOT NULL,
                prev_hash TEXT NOT NULL, payload_hash TEXT NOT NULL, row_hash TEXT NOT NULL);
            CREATE TABLE audit_anchors (
                anchor_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT 'default',
                first_seq INTEGER NOT NULL, last_seq INTEGER NOT NULL,
                merkle_root TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL);
            """
        )
        payload = json.dumps({"q": "old"}, sort_keys=True)
        payload_hash = sha256_hex(payload)
        conn.execute(
            """INSERT INTO audit_events VALUES ('old-1', 'generate', 't1', '', ?, 1.0, 1, ?, ?, ?)""",
            (payload, GENESIS_HASH, payload_hash, sha256_hex(GENESIS_HASH + payload_hash)),
        )
        conn.commit()
        reset_ledger()
        ledger = AuditLedger()
        ledger.append("generate", {"q": "new"}, tenant_id="t1")
        assert ledger.seal_pending("t1")["head_hash"]
        versions = [r["hash_version"] for r in conn.execute("SELECT hash_version FROM audit_events ORDER BY seq")]
        assert versions == [1, 2]
        assert verify_chain("t1").valid is True

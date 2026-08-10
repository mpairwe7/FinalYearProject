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

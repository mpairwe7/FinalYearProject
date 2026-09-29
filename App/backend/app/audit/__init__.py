"""Immutable audit ledger (Phase 21 subset).

Implements a hash-chained append-only log of every agentic action
so the URA Chatbot can satisfy **regulatory replay**: reconstruct
exactly what the bot told taxpayer X on date Y, with which sources,
under which policy version, and which model revision.

Design invariants (see docs/URA_Chatbot_Roadmap_2026_Enhanced.md §6):

- **Append-only** — there is no UPDATE or DELETE on audit_events.
- **Hash-chained** — every row carries sha256(prev_hash + payload_hash
  + envelope_hash), so tampering with what a row says *or* who, when
  and where it happened is detectable by re-computing the chain (rows
  written before hash format v2 omit the envelope; see ``ledger.py``).
- **Merkle-anchored (sealed)** — the API seals new rows every
  ``AUDIT_SEAL_INTERVAL_SECONDS`` (and on demand from /admin/audit):
  a Merkle root plus the chain head hash go to audit_anchors and to
  the log pipeline, which is the witness outside this database.
- **Cryptographic tombstones** — UDPA right-to-erasure marks an
  entry as erased, it does NOT rewrite the chain.
- **Deterministic payloads** — arguments and results are hashed
  via sha256(sorted-json) so the same logical call always produces
  the same hash.

Feature flag: ``FLAG_AUDIT_LEDGER`` — when on, every agentic turn
in service.generate() appends an event.  Default false so the
compute overhead is opt-in during rollout.
"""

from __future__ import annotations

from .ledger import AuditEvent, AuditLedger, get_ledger
from .merkle import compute_merkle_root
from .verifier import VerificationReport, verify_chain, verify_ledger

__all__ = [
    "AuditEvent",
    "AuditLedger",
    "VerificationReport",
    "compute_merkle_root",
    "get_ledger",
    "verify_chain",
    "verify_ledger",
]

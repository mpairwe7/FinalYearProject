"""Immutable audit ledger (Phase 21 subset).

Implements a hash-chained append-only log of every agentic action
so the URA Chatbot can satisfy **regulatory replay**: reconstruct
exactly what the bot told taxpayer X on date Y, with which sources,
under which policy version, and which model revision.

Design invariants (see docs/URA_Chatbot_Roadmap_2026_Enhanced.md §6):

- **Append-only** — there is no UPDATE or DELETE on audit_events.
- **Hash-chained** — every row carries sha256(prev_hash + payload_hash)
  so tampering is detectable by re-computing the chain.
- **Merkle-anchored** — an hourly / nightly worker computes a
  Merkle root over a batch of rows and writes it to audit_anchors
  (future: anchor to an external immutable store).
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
from .verifier import VerificationReport, verify_chain

__all__ = [
    "AuditEvent",
    "AuditLedger",
    "VerificationReport",
    "compute_merkle_root",
    "get_ledger",
    "verify_chain",
]

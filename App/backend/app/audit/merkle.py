"""Tiny Merkle-tree helpers for audit anchoring.

Used by :mod:`ledger` to compute the root of a batch of audit
events for hourly/nightly anchoring.  A full Merkle tree is
overkill for a single process; but the root + leaf hashes are
cheap to compute and gives an external verifier a single scalar
to compare against.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def pair_hash(left: str, right: str) -> str:
    """Combine two hex hashes into the parent hash.

    Uses sha256 of the concatenated hex strings (not the raw bytes),
    so external verifiers can reproduce the computation without
    knowing the implementation's byte order.
    """
    return sha256_hex(left + right)


def compute_merkle_root(leaf_hashes: Iterable[str]) -> str:
    """Compute the Merkle root of *leaf_hashes*.

    Leaves must already be sha256 hex strings (the caller owns
    payload hashing).  Returns the empty string if the iterable
    is empty (well-defined "no data" marker).  Odd-length levels
    duplicate the last hash to the right (standard Bitcoin-style
    Merkle).
    """
    level = [h for h in leaf_hashes if h]
    if not level:
        return ""
    while len(level) > 1:
        next_level: list[str] = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else left
            next_level.append(pair_hash(left, right))
        level = next_level
    return level[0]

"""Chain integrity verifier.

Re-computes every ``payload_hash`` and ``row_hash`` in the ledger
for a tenant, re-checks every seal (Merkle anchor) against the rows it
covers, and reports any tampering.  Called from:

- ``GET /v1/admin/audit/verify`` — the auditor's integrity check
- ``python -m app.audit.verifier`` — CLI for scheduled integrity
  checks + alerting (exit 1 on any break)
- Phase 21 full: nightly GitHub Action that runs against a
  production snapshot and pages on failure

Reports are simple dataclasses so they can be serialised to JSON
for Prometheus / Grafana.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from .ledger import GENESIS_HASH, HASH_V1, chain_hash, envelope_hash
from .merkle import compute_merkle_root, sha256_hex

logger = logging.getLogger(__name__)

#: Rows read per query while walking a chain, so memory stays flat however
#: long the ledger grows.
_BATCH_ROWS = 5000

#: Verification scopes. ``full`` walks every row and re-checks every seal;
#: ``since_seal`` re-checks the newest seal and walks only the rows after
#: it, starting from the chain head the seal recorded.
SCOPE_FULL = "full"
SCOPE_SINCE_SEAL = "since_seal"


@dataclass
class ChainBreak:
    seq: int
    event_id: str
    reason: str
    expected_payload_hash: str
    actual_payload_hash: str
    expected_row_hash: str
    actual_row_hash: str


@dataclass
class AnchorBreak:
    anchor_id: str
    first_seq: int
    last_seq: int
    reason: str


@dataclass
class VerificationReport:
    tenant_id: str
    rows_checked: int = 0
    valid: bool = True
    first_seq: int = 0
    last_seq: int = 0
    head_hash: str = ""
    breaks: list[ChainBreak] = field(default_factory=list)
    scope: str = SCOPE_FULL
    anchors_checked: int = 0
    anchor_breaks: list[AnchorBreak] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "breaks": [asdict(b) for b in self.breaks],
            "anchor_breaks": [asdict(b) for b in self.anchor_breaks],
        }


def _payload_hash(payload_json: str) -> str:
    try:
        canonical = json.dumps(json.loads(payload_json), sort_keys=True, default=str)
    except (TypeError, ValueError):
        canonical = payload_json
    return sha256_hex(canonical)


def _row_break_reason(
    row: dict[str, Any],
    *,
    expected_seq: int,
    prev_row_hash: str,
    newest_version: int,
    expected_payload_hash: str,
    expected_row_hash: str,
) -> str | None:
    """Why *row* does not follow the chain so far, or ``None`` if it does."""
    seq = int(row["seq"])
    if seq < expected_seq:
        return "duplicate sequence number (two writers forked the chain)"
    if seq > expected_seq:
        return f"sequence gap: rows {expected_seq}..{seq - 1} are missing"
    if expected_payload_hash != row["payload_hash"]:
        return "payload_hash mismatch"
    if row["prev_hash"] != prev_row_hash:
        return "prev_hash does not match previous row_hash"
    if int(row.get("hash_version") or HASH_V1) < newest_version:
        return "hash format downgraded after a newer row"
    if expected_row_hash != row["row_hash"]:
        return "row_hash mismatch"
    return None


def verify_chain(
    tenant_id: str = "default",
    *,
    after_seq: int = 0,
    start_hash: str = GENESIS_HASH,
) -> VerificationReport:
    """Walk the audit chain for *tenant_id* and verify every hash.

    Returns a structured report — ``valid=False`` if any row's computed
    hash does not match its stored hash, if any row's ``prev_hash`` does
    not match the previous row's ``row_hash``, or if sequence numbers skip
    or repeat. Each row is checked by the hash format it was written with,
    and a row in an older format after a newer one is itself a break.

    ``after_seq``/``start_hash`` resume from a trusted checkpoint (a seal's
    last sequence number and head hash) instead of the genesis row.
    """
    from .. import database as db

    report = VerificationReport(tenant_id=tenant_id)
    prev_row_hash = start_hash
    expected_seq = after_seq + 1
    newest_version = HASH_V1
    # Keyset on (seq, event_id): a forked chain repeats a seq, and paging on
    # seq alone would drop the second row of a pair split across batches.
    # The first page starts strictly after the checkpoint.
    after: tuple[Any, ...] = ("seq > ?", (after_seq,))
    while True:
        rows = db.query_all(
            f"""SELECT seq, event_id, event_type, tenant_id, user_id, ts, payload,
                       prev_hash, payload_hash, row_hash, hash_version
                FROM audit_events
                WHERE tenant_id = ? AND {after[0]}
                ORDER BY seq ASC, event_id ASC
                LIMIT ?""",  # noqa: S608 - the fragment is one of two constants
            (tenant_id, *after[1], _BATCH_ROWS),
        )
        for row in rows:
            report.rows_checked += 1
            if report.first_seq == 0:
                report.first_seq = int(row["seq"])
            report.last_seq = int(row["seq"])
            version = int(row.get("hash_version") or HASH_V1)
            expected_payload_hash = _payload_hash(row["payload"])
            envelope = envelope_hash(
                event_id=row["event_id"],
                event_type=row["event_type"],
                tenant_id=row["tenant_id"],
                user_id=row.get("user_id") or "",
                ts=row["ts"],
                seq=row["seq"],
            )
            expected_row_hash = chain_hash(prev_row_hash, expected_payload_hash, envelope, version)
            reason = _row_break_reason(
                row,
                expected_seq=expected_seq,
                prev_row_hash=prev_row_hash,
                newest_version=newest_version,
                expected_payload_hash=expected_payload_hash,
                expected_row_hash=expected_row_hash,
            )
            if reason:
                report.valid = False
                report.breaks.append(
                    ChainBreak(
                        seq=int(row["seq"]),
                        event_id=row["event_id"],
                        reason=reason,
                        expected_payload_hash=expected_payload_hash,
                        actual_payload_hash=row["payload_hash"],
                        expected_row_hash=expected_row_hash,
                        actual_row_hash=row["row_hash"],
                    )
                )
            newest_version = max(newest_version, version)
            expected_seq = int(row["seq"]) + 1
            prev_row_hash = row["row_hash"]
        if len(rows) < _BATCH_ROWS:
            break
        last_seq, last_id = int(rows[-1]["seq"]), str(rows[-1]["event_id"])
        after = ("(seq > ? OR (seq = ? AND event_id > ?))", (last_seq, last_seq, last_id))

    report.head_hash = prev_row_hash
    return report


def verify_anchor(anchor: dict[str, Any], tenant_id: str = "default") -> AnchorBreak | None:
    """Re-check one seal against the rows it covers; ``None`` when it holds."""
    from .. import database as db

    first, last = int(anchor["first_seq"]), int(anchor["last_seq"])
    rows = db.query_all(
        """SELECT seq, payload_hash, row_hash FROM audit_events
           WHERE tenant_id = ? AND seq BETWEEN ? AND ?
           ORDER BY seq ASC""",
        (tenant_id, first, last),
    )

    def _break(reason: str) -> AnchorBreak:
        return AnchorBreak(anchor_id=str(anchor["anchor_id"]), first_seq=first, last_seq=last, reason=reason)

    if len(rows) != last - first + 1:
        return _break(f"sealed range holds {len(rows)} rows, expected {last - first + 1}")
    if compute_merkle_root(r["payload_hash"] for r in rows) != anchor["merkle_root"]:
        return _break("merkle_root mismatch: a sealed row's content changed")
    head_hash = anchor.get("head_hash") or ""
    if head_hash and rows[-1]["row_hash"] != head_hash:
        return _break("head_hash mismatch: the chain was rewritten under the seal")
    tsa_token = str(anchor.get("tsa_token") or "").strip()
    if tsa_token:
        from .tsa import TimestampError, seal_statement, verify_stored_token

        statement = seal_statement(
            tenant_id=tenant_id,
            first_seq=first,
            last_seq=last,
            merkle_root=str(anchor["merkle_root"]),
            head_hash=head_hash,
        )
        try:
            # None: a value stored before RFC 3161 support, which proves nothing
            # and is ignored rather than trusted.
            verify_stored_token(tsa_token, statement)
        except TimestampError as exc:
            return _break(f"timestamp token does not verify: {exc}")
    return None


def verify_ledger(tenant_id: str = "default", *, scope: str = SCOPE_FULL) -> VerificationReport:
    """Verify the chain and its seals at *scope*.

    ``since_seal`` falls back to ``full`` when there is no seal carrying a
    head hash to resume from (none yet, or only seals made before seals
    recorded one).
    """
    from .ledger import get_ledger

    ledger = get_ledger()
    anchors = ledger.anchors(tenant_id)
    resume = next((a for a in reversed(anchors) if a.get("head_hash")), None)
    if scope == SCOPE_SINCE_SEAL and resume is not None:
        to_check = [resume]
        report = verify_chain(tenant_id, after_seq=int(resume["last_seq"]), start_hash=str(resume["head_hash"]))
        report.scope = SCOPE_SINCE_SEAL
    else:
        to_check = anchors
        report = verify_chain(tenant_id)
        report.scope = SCOPE_FULL
    for anchor in to_check:
        report.anchors_checked += 1
        problem = verify_anchor(anchor, tenant_id)
        if problem is not None:
            report.valid = False
            report.anchor_breaks.append(problem)
    return report


# ---------------------------------------------------------------------------
# CLI entry — python -m App.backend.app.audit.verifier
# ---------------------------------------------------------------------------
def main() -> int:
    import argparse
    import json as _json

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Verify audit ledger integrity")
    parser.add_argument("--tenant", default="default")
    parser.add_argument("--out", default="audit_report.json")
    parser.add_argument("--scope", choices=(SCOPE_FULL, SCOPE_SINCE_SEAL), default=SCOPE_FULL)
    args = parser.parse_args()

    report = verify_ledger(tenant_id=args.tenant, scope=args.scope)
    payload = report.to_dict()
    print(_json.dumps(payload, indent=2, default=str))
    with open(args.out, "w") as fh:
        _json.dump(payload, fh, indent=2, default=str)
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())

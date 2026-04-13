"""Chain integrity verifier.

Re-computes every ``payload_hash`` and ``row_hash`` in the ledger
for a tenant and reports any tampering.  Called from:

- ``scripts/verify_audit_chain.py`` — CLI for scheduled integrity
  checks + alerting
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

from .ledger import GENESIS_HASH
from .merkle import sha256_hex

logger = logging.getLogger(__name__)


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
class VerificationReport:
    tenant_id: str
    rows_checked: int = 0
    valid: bool = True
    first_seq: int = 0
    last_seq: int = 0
    head_hash: str = ""
    breaks: list[ChainBreak] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "breaks": [asdict(b) for b in self.breaks],
        }


def verify_chain(tenant_id: str = "default") -> VerificationReport:
    """Walk the audit chain for *tenant_id* and verify every hash.

    Returns a structured report — ``valid=False`` if any row's
    computed hash does not match its stored hash, or if any row's
    ``prev_hash`` does not match the previous row's ``row_hash``.
    """
    from .. import database as db

    report = VerificationReport(tenant_id=tenant_id)

    conn = db._get_connection()
    rows = conn.execute(
        """SELECT seq, event_id, payload, prev_hash, payload_hash, row_hash
           FROM audit_events
           WHERE tenant_id = ?
           ORDER BY seq ASC""",
        (tenant_id,),
    ).fetchall()

    prev_row_hash = GENESIS_HASH
    for row in rows:
        report.rows_checked += 1
        if report.first_seq == 0:
            report.first_seq = row["seq"]
        report.last_seq = row["seq"]

        # Recompute payload_hash from the stored payload
        payload_json = row["payload"]
        try:
            parsed = json.loads(payload_json)
            canonical = json.dumps(parsed, sort_keys=True, default=str)
        except Exception:
            canonical = payload_json
        expected_payload_hash = sha256_hex(canonical)
        expected_row_hash = sha256_hex(prev_row_hash + expected_payload_hash)

        if expected_payload_hash != row["payload_hash"]:
            report.valid = False
            report.breaks.append(
                ChainBreak(
                    seq=row["seq"],
                    event_id=row["event_id"],
                    reason="payload_hash mismatch",
                    expected_payload_hash=expected_payload_hash,
                    actual_payload_hash=row["payload_hash"],
                    expected_row_hash=expected_row_hash,
                    actual_row_hash=row["row_hash"],
                )
            )
        elif row["prev_hash"] != prev_row_hash:
            report.valid = False
            report.breaks.append(
                ChainBreak(
                    seq=row["seq"],
                    event_id=row["event_id"],
                    reason="prev_hash does not match previous row_hash",
                    expected_payload_hash=expected_payload_hash,
                    actual_payload_hash=row["payload_hash"],
                    expected_row_hash=expected_row_hash,
                    actual_row_hash=row["row_hash"],
                )
            )
        elif expected_row_hash != row["row_hash"]:
            report.valid = False
            report.breaks.append(
                ChainBreak(
                    seq=row["seq"],
                    event_id=row["event_id"],
                    reason="row_hash mismatch",
                    expected_payload_hash=expected_payload_hash,
                    actual_payload_hash=row["payload_hash"],
                    expected_row_hash=expected_row_hash,
                    actual_row_hash=row["row_hash"],
                )
            )

        prev_row_hash = row["row_hash"]

    report.head_hash = prev_row_hash
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
    args = parser.parse_args()

    report = verify_chain(tenant_id=args.tenant)
    payload = report.to_dict()
    print(_json.dumps(payload, indent=2, default=str))
    with open(args.out, "w") as fh:
        _json.dump(payload, fh, indent=2, default=str)
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Live canary for the audit trail: reads, integrity checks and seals.

Runs INSIDE the api container, so the break-glass operator key is read from
that process's environment (``INDEX_API_KEY``) and sent in a header — it is
never printed, written or passed on a command line::

    docker exec -i -w /app ura-app-api python - < scripts/probe_audit_trail.py

The api must run with ``FLAG_AUDIT_LEDGER=true``. That flag is protected (no
API call may switch it), so turn it on for the container, not at runtime; see
``docs/runbooks/audit-trail.md`` ("Verify on the local stack"). The probe adds
a chat turn, reads and checks the trail, and makes two seals. It changes no
row. Exit status 1 means at least one check failed.

Role separation (an auditor cannot change a ticket, an officer cannot read or
seal the trail) needs per-role tokens; ``tests/test_auditor_controls.py``
covers it against the same code.
"""

from __future__ import annotations

import os
import sys
from typing import Any
from urllib.parse import urlsplit

import httpx

BASE = os.getenv("AUDIT_PROBE_BASE", "http://127.0.0.1:8000")


def _request(
    method: str, path: str, *, auth: bool = True, body: dict[str, Any] | None = None
) -> tuple[int, Any]:
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {os.environ['INDEX_API_KEY']}"
    response = httpx.request(
        method,
        f"{BASE.rstrip('/')}{path}",
        json=body,
        headers=headers,
        timeout=120,
    )
    if not response.is_success:
        return response.status_code, None
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, None


class Probe:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def main() -> int:
    parsed_base = urlsplit(BASE)
    if (
        parsed_base.scheme not in ("http", "https")
        or not parsed_base.netloc
        or parsed_base.username is not None
        or parsed_base.password is not None
    ):
        print("AUDIT_PROBE_BASE must be an http(s) URL without embedded credentials", file=sys.stderr)
        return 2
    # The probe sends INDEX_API_KEY as a bearer token: plain http only to this machine.
    if parsed_base.scheme == "http" and parsed_base.hostname not in ("localhost", "127.0.0.1", "::1"):
        print("AUDIT_PROBE_BASE must use https unless it is localhost", file=sys.stderr)
        return 2
    if not os.getenv("INDEX_API_KEY"):
        print(
            "INDEX_API_KEY is not set in this process; run inside the api container",
            file=sys.stderr,
        )
        return 2
    p = Probe()

    _, flags = _request("GET", "/v1/admin/flags")
    on = next(
        (f["enabled"] for f in (flags or {}).get("flags", []) if f["name"] == "audit_ledger"), None
    )
    p.check(
        "audit_ledger is on",
        on is True,
        "start the api with FLAG_AUDIT_LEDGER=true" if on is not True else "",
    )
    p.check(
        "audit_ledger cannot be switched by API",
        _request("PATCH", "/v1/admin/flags/audit_ledger?enabled=false")[0] == 400,
    )
    p.check(
        "unauthenticated trail read refused",
        _request("GET", "/v1/admin/audit/events", auth=False)[0] == 401,
    )
    p.check(
        "unauthenticated seal refused",
        _request("POST", "/v1/admin/audit/seal", auth=False)[0] == 401,
    )

    chat, _ = _request(
        "POST",
        "/v1/chat",
        auth=False,
        body={"message": "How do I register for a TIN?", "locale": "en"},
    )
    _, gen = _request("GET", "/v1/admin/audit/events?event_type=generate&limit=5")
    p.check(
        "a chat turn lands on the chain",
        chat == 200 and bool(gen and gen["events"]),
        f"chat={chat}",
    )

    _, before = _request("GET", "/v1/admin/audit/verify")
    p.check(
        "chain intact",
        bool(before and before["valid"]),
        f"scope={before and before['scope']} rows={before and before['rows_checked']}",
    )
    _request("GET", "/v1/admin/audit/events?event_type=staff.")

    status, sealed = _request("POST", "/v1/admin/audit/seal")
    anchor = (sealed or {}).get("anchor") or {}
    p.check(
        "seal records a Merkle root and chain head",
        status == 200
        and len(anchor.get("merkle_root", "")) == 64
        and len(anchor.get("head_hash", "")) == 64,
        f"#{anchor.get('first_seq')}..#{anchor.get('last_seq')}",
    )
    _, full = _request("GET", "/v1/admin/audit/verify?scope=full")
    p.check(
        "verify re-checks the seals",
        bool(full and full["valid"] and full["anchors_checked"] >= 1),
        f"rows={full and full['rows_checked']} seals={full and full['anchors_checked']}",
    )
    _, recent = _request("GET", "/v1/admin/audit/verify?scope=since_seal")
    p.check(
        "since_seal starts after the newest seal",
        bool(
            recent
            and recent["scope"] == "since_seal"
            and recent["first_seq"] == anchor.get("last_seq", -1) + 1
        ),
        f"from #{recent and recent['first_seq']}",
    )
    p.check(
        "unknown scope rejected",
        _request("GET", "/v1/admin/audit/verify?scope=everything")[0] == 422,
    )

    _, own = _request("GET", "/v1/admin/audit/events?event_type=audit.&limit=20")
    kinds = {e["event_type"] for e in (own or {}).get("events", [])}
    p.check(
        "reads, checks and seals of the trail are recorded",
        {"audit.trail_viewed", "audit.chain_verified", "audit.sealed"} <= kinds,
        ",".join(sorted(kinds)),
    )
    _request("POST", "/v1/admin/audit/seal")
    _, after = _request("GET", "/v1/admin/audit/verify?scope=full")
    p.check(
        "second seal, full walk still intact",
        bool(after and after["valid"] and after["anchors_checked"] >= 2),
    )

    print(f"\n{p.passed}/{p.passed + p.failed} audit-trail checks passed against {BASE}")
    return 1 if p.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

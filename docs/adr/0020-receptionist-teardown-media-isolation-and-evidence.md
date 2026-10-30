# ADR-0020: Receptionist teardown, media isolation and evidence integrity

## Status

Accepted for implementation, 2026-10-30; production acceptance pending.

## Context and Problem Statement

Call finalization mixed resource cleanup with database writes and telemetry.
A reporting failure could leak the call's admission slot. A timer could cancel
its own teardown. Reconnect expiry advertised an available call before
revoking the old media participant. Draft wrap-ups asserted outcomes without
evidence; ticket creation omitted tenant context and the queue switch.

## Decision Outcome

End live resources before best-effort reporting; always remove registry and
admission state in nested `finally` blocks. Cancel other timers, never the
executing timer. Bound cooperative media/cancellation waits. Revoke media
before making a call claimable; terminate on uncertain isolation. Preserve
tenant/conversation/locale on wrap-up tickets and honor `ticket_queue` before
mutations. Draft notes must distinguish recorded speech from verified advice,
resolution and taxpayer understanding.

## Consequences

Media-service failure can end a call instead of transferring it. This is an
intentional confidentiality tradeoff. Persistence failures remain visible in
logs; this does not provide durable audit reconciliation. Room deletion can
also fail, and self-hosted LiveKit JWT replay remains an open release gate.
No models, routes, flags or database schema were added. Pipecat is pinned to
1.15.0 so the speech profile resolves together with the backend's Pillow 13
floor; the previous 1.12.0 profile could not be installed with that floor.

## Confirmation

Fault injection in `App/backend/tests/test_receptionist_assurance.py`, existing
claims/actions/review/WebSocket regressions, and real wrap-up route assertions
in `tests/test_answer_integrity_integration.py`. See the
[review and release gates](../plans/receptionist-assurance-2026-10-30.md), which
links the verified OWASP, NIST, W3C and LiveKit sources. Staging media and
multilingual acceptance remain required.

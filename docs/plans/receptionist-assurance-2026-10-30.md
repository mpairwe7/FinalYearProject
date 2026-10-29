# Call receptionist assurance review — 30 October 2026

Status: phased code fixes implemented; production acceptance remains open.
Baseline: `4f4f53bd`, branch `fix/receptionist-assurance-2026-10-30`. Existing
uncommitted retrieval work was preserved. This is a targeted lifecycle,
handoff and evidence-integrity review, not a complete penetration test or a
legal compliance certification. The earlier [CX audit](receptionist-cx-audit-2026-10-plan.md)
remains historical evidence, not proof of the present deployment.

## Verified reference basis

Primary sources checked online on 2026-10-30. Publication dates are distinct
from the verification date; older guidance remains applicable. These are
engineering references, not a claim that foreign regulations bind URA.

| Reference | Application to this review |
| --- | --- |
| [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) (published December 2025) | Identity/privilege abuse, cascading failures and trust exploitation inform the media, cleanup and draft-evidence checks. |
| [OWASP WebSocket Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/WebSocket_Security_Cheat_Sheet.html) | Authenticate connections, authorize actions, validate origins, bound consumption and expire long-lived sessions. Initial authentication alone does not settle session lifetime. |
| [LiveKit tokens and grants](https://docs.livekit.io/frontends/reference/tokens-grants/) | Token expiry governs connection admission; self-hosted removal/permission changes do not invalidate existing tokens. An application allowlist checked after joining cannot guarantee zero media exposure before eviction. |
| [NIST AI 600-1](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf) (July 2024), MS-2.5-001/003, MS-2.6-005, MANAGE 4.1 | Verify claims and sources, document limits of tests, monitor failures and provide incident recovery. No unsupported resolution claims in operational records. |
| [NIST AI Agent Standards Initiative](https://www.nist.gov/artificial-intelligence/ai-agent-standards-initiative) (2026) | Current direction: agent identity, security, interoperability and evaluation. An initiative is not a completed mandatory standard or certification. |
| [W3C WCAG 2.2 timing guidance](https://www.w3.org/WAI/WCAG22/Understanding/timing-adjustable.html) | Evaluate warning and extension behavior, with applicable exceptions assessed explicitly; a timed disconnect alone does not demonstrate accessibility. |
| [Pipecat releases](https://github.com/pipecat-ai/pipecat/releases), [published metadata](https://pypi.org/pypi/pipecat-ai/1.15.0/json) | October 24's 1.14 widened the Pillow ceiling; 1.15 was released October 29. The installed 1.15 metadata and combined resolver verify compatibility with the backend's Pillow 13 floor. |

## Phases applied

| Phase / gap | Severity and evidence | Result |
| --- | --- | --- |
| A / G298: teardown reliability | High. `ws.call_stream_endpoint` performed callback persistence before ending the room, then audit before releasing the registry/slot. Any exception could skip cleanup. `accept()` also sat outside the slot's `finally`. | End resources before reporting; isolate reporting failures; nested `finally` removes room and slot even if finalization fails. Acceptance failure releases its slot. Reporting errors are logged once, not replayed. |
| A / G299: timer cancellation and shutdown | High. `CallRoom.end` could cancel the transfer timer executing that very teardown; claim/reconnect timers survived. `_stop_pipeline` bounded waiting for the runner but not its cancellation request. | Do not cancel the executing timer, including review settlement; cancel remaining timers. Apply cooperative five-second timeouts to cancellation, officer closure and room deletion. |
| B / G300: reconnect media isolation | High. Reconnect expiry released the claim and announced a new transfer before best-effort revocation. Failure allowed the call to continue with uncertain media access. | Revoke under the desk lock before release; wait at most five seconds cooperatively. On failure, end with `media_revocation_failed` and attempt room deletion; do not requeue. Observer cleanup and rejected-participant cleanup share the terminal failure policy. |
| C / G301: unsupported draft evidence | High. Empty transcripts produced “Inquiry addressed and taxpayer confirmed understanding”; any officer utterance implied statutory guidance. Both reproduced by failing tests. | Drafts explicitly leave resolution/understanding unverified and refer reviewers to recorded officer speech. No inferred success from missing follow-up fields. |
| C / G302: wrap-up handoff isolation | High. `desk.wrapup` omitted tenant/conversation/locale from `create_ticket`, and bypassed `ticket_queue`. | Preserve the call's tenant, conversation and locale; reject ticket actions with 409 when disabled, before changing the wrap-up. Reject unknown ticket actions with 400. |
| D / G308: installable speech profile | High. Resolving the CPU and receptionist requirements together failed: Pipecat 1.12.0 required Pillow below 13, while the backend requires Pillow 13 or newer. | Pin Pipecat 1.15.0; resolve and install both profiles together, retaining Pillow 13.0.0, then run the optional pipeline/LiveKit suites. |

Five initial regression cases failed before fixes. Added fault tests also cover
multiple simultaneous reporting failures, a stuck cancellation request,
failed WebSocket acceptance, ticket routing and the disabled queue. API-level
ticket assertions live in `tests/test_answer_integrity_integration.py`.
No new routes, flags, models or dependency names were added to the product;
the existing Pipecat dependency was upgraded. SDK deprecation warnings remain
for the existing PipelineTask/PipelineRunner APIs; plan their migration before
Pipecat 2.0 rather than relying on deprecated compatibility indefinitely.

## Remaining release work

| Gate | Evidence / risk | Required acceptance |
| --- | --- | --- |
| G303 — media enforcement | Revocation and room deletion can both fail. Self-hosted issued JWTs remain reusable; allowlist eviction occurs after joining. | Media/security owner: prove denied rejoin and no media delivery after revocation, including refreshed tokens, using the deployed SFU. Establish supported server-side admission/revocation or isolate each handoff into a new room. Alert and quarantine the affected room when deletion fails. Local tests do not prove this. |
| G304 — session lifetime and resource budgets | Staff sockets authenticate once; lobby/audio admission has no equivalent of the caller's `try_acquire`. Officer STT creates a task per completed utterance. | Backend owner: session-expiry/logout tests on every socket, per-principal/global caps, frame/rate limits, bounded transcription queues and slow-reader disconnects. Do not apply speech-frame limits copied from text-message guidance without measuring audio framing. |
| G305 — authoritative wrap-up and partial writes | Same-tenant officers can submit another officer's wrap-up; ticket failures are caught after wrap-up persistence. `_save_draft_wrapup` checks then writes without a conditional update. | Desk/storage owner: explicit ownership/delegation policy including transferred calls, atomic outcome/ticket operation or durable outbox, idempotency and conditional draft writes. Test simultaneous officer edits and database failure; never imply that a failed ticket action succeeded. |
| G306 — availability and audit durability | Registry, hub and call ownership are process-local. Ending the room can succeed while its database row remains stale. | SRE owner: process-loss and drain drills, reconciliation of stale rows and audit failures, measured recovery objectives, durable ownership before increasing workers/replicas. Keep the existing one-worker/one-replica gate. |
| G307 — operational and accessibility acceptance | No real microphone/SFU, impaired network, ASR/TTS quality, accent or assisted-accessibility tests were run for this change. | QA/speech/service owners: reviewed en/lg/sw/nyn/ach calls, including crisis while waiting, number fidelity, overlapping speech, interrupted tax answers and after-hours callbacks; packet loss/jitter/slow-client load; keyboard/screen-reader/caption and timeout-extension checks. Measure p50/p95/p99 turn latency, abandonment, failed handoffs, figure errors and recovery by locale. Set release thresholds before collecting results. |

Prioritize G303–G305 before expanding exposure. G306 gates scale-out; G307 gates
service acceptance. Optimization should start with measured queueing and tail
latency, then bounded work and cached fixed phrases. Faster speech generation
does not compensate for incorrect tax figures or unverified handoffs.

## Verification and rollout

Measured on 2026-10-30 with Python 3.11.11, Pipecat 1.15.0 and Pillow 13.0.0:

- Full receptionist suite, answer-integrity integration, architecture and runtime
  boundary checks on an isolated checkout of the staged PR contents: **509
  passed, no skips**, in 27.01 seconds. This excludes the unrelated uncommitted
  retrieval changes from validation.
- The same suite in the shared checkout passed 508 tests and failed its
  workspace-hygiene assertion rejecting `.agents` (26.54 seconds). This
  environment supplies that directory as a read-only mount. It was not removed
  or exempted; all checks passed when the staged files were checked out into
  `/tmp/receptionist-review-tree` without that environment mount.
- Governance compliance, threat-model validation, blocking Ruff checks and
  `git diff --check`: passed.
- The mandatory freshness command was run and exited 2: no snapshot exists at
  the configured `App/Model/index_freshness.json`. Do not fabricate a new
  snapshot to turn that check green; the index owner must reconcile the serving
  index with the intended corpus paths.
- The combined CPU/receptionist requirements installed successfully. This fixed
  an initially reproduced resolver failure on Pipecat 1.12.0/Pillow 13.
- Runtime facts re-derived for documentation review: 72 tools, 18 namespaces,
  86 flags, five active locales (`ach`, `en`, `lg`, `nyn`, `sw`). This patch adds
  no tools or flags and changes no active languages.

Tests run against local service doubles and installed media libraries; real
SFU admission/replay, microphone audio, provider quality and production
accessibility remain the acceptance work above. SDK deprecation warnings are
recorded for migration before Pipecat 2.0. GitHub identity verified as `mpairwe7`.

Use the repository CPU requirements in an isolated Python 3.11 environment.
Disable hosted model, Qdrant and speech calls for deterministic tests:

```bash
PYTHONPATH=App/backend LLM_ENABLED=false QDRANT_ENABLED=false SPEECH_ENABLED=false \
  python -m pytest App/backend/tests/test_receptionist_assurance.py \
  App/backend/tests/test_receptionist_claims.py App/backend/tests/test_receptionist_officer_actions.py \
  App/backend/tests/test_receptionist_safety_integrity.py App/backend/tests/test_receptionist_ws.py \
  App/backend/tests/test_receptionist_review.py -q
PYTHONPATH=App/backend python -m pytest tests/test_answer_integrity_integration.py \
  -k call_wrapup_ticket -q
python governance/compliance_check.py
python threat-model/validate_threats.py
```

Before rollout, run the receptionist extras suite and staging media tests;
local transport stubs are not media assurance. Release by reviewed PR, then
internal staffed canary. Inspect `media_revocation_failed`, call-finalization
errors and failed room deletion in logs. If isolation is uncertain, suspend
new receptionist calls using the existing feature control, reconcile active
SFU rooms, and keep the staffed text channel available. Do not roll back the
isolation checks just to restore throughput. No production deployment was made.

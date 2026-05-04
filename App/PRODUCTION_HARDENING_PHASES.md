# Production Hardening Phases

This tracks the critical-gap fixes for the URA assistant App codebase.

## Phase 1 - Runtime And Deployment Guardrails

Status: applied.

- Serialize local HuggingFace generation around mutable LoRA adapter state.
- Enforce production startup checks for OIDC, auth, tenancy, audit, Postgres, Redis credentials, Qdrant API key, pinned model revision, CORS, and raw-prompt minimisation.
- Make auth-required mode reject anonymous calls that use `current_user`, `require_user`, or `require_role`.
- Protect feedback summaries, analytics dashboards, metrics, evaluation results, offline bundle APIs, account/action tools, and admin surfaces under auth/admin gates.
- Pass tenant/user context into chat generation and audit ledger writes.
- Keep public assistant chat anonymous-capable via optional auth; speech health, TTS, translation, and consented voice processing also work without login.
- Require explicit voice consent for anonymous ASR/voice processing via `X-Voice-Consent: true` or WebSocket `voice_consent_accepted=true`.
- Add Qdrant API-key support in retrieval and indexing clients.
- Fix chat metadata rerendering for citations, faithfulness, escalation, and feedback context.

## Phase 2 - Authority And Answer Quality

Status: applied as deterministic production gates.

- Added hash-checked authority manifest validation with production fail-closed startup checks.
- Rate tools now attach authority status and refuse to return static rates when `REQUIRE_FRESH_AUTHORITY=true` and the manifest is stale/missing.
- Added deterministic claim-level citation verification and wired it into the response judge so unsupported claims revise/escalate.
- Added local release-gate coverage for backend syntax/tests, frontend build, compose overlay, and authority preflight.

## Phase 3 - Real Agentic Operations

Status: applied as fail-closed interfaces; live URA integration still needs real API credentials.

- Added dispatch-time MCP authorization policy for role, consent, confirmation, and idempotency.
- Added `ura_account_profile` and `ura_action_proposal` tool interfaces that call configured URA APIs only when credentials are present.
- Tool discovery and dispatch now both deny high/critical tools for unauthorized users.
- Frontend requests now propagate stored bearer tokens when present, but normal chat does not require a token; WebSocket voice uses `access_token` query auth only when logged in.

## Phase 4 - Scale And Resilience

Status: partially applied; external infra drills remain.

- Production compose overlay now requires authority freshness and exposes URA API configuration points.
- Qwen3-8B now supports BitsAndBytes NF4 4-bit local loading so the agentic LLM no longer monopolizes GPU memory.
- Compose mounts Qwen and Whisper LoRA adapters from `fine-tuning/adapters` read-only and pins Whisper to CPU for GPU isolation.
- Live smoke and deploy preflight scripts cover anonymous chat, TIN guided workflow behavior, speech health, and protected admin denial.
- Remaining work: Postgres migration tooling, Redis/Qdrant TLS verification, load/canary tests, disaster-recovery drills, and real-device mobile PWA test matrix.

## CI / Security Workflow Stabilization

Status: applied for PR validation.

- Frontend PR gates now run ESLint, TypeScript, Vitest, Lighthouse accessibility, and Next.js build without a blocking coverage threshold in the unit-test step.
- Backend PR tests run with deterministic local isolation for analytics, Qdrant, and speech.
- The ML pipeline keeps a 35% Python coverage ratchet while focused backend/agentic coverage is expanded.
- Trivy and Checkov still scan on PRs and upload artifacts; GitHub Security SARIF upload is limited to non-PR events.
- OWASP ZAP and OSSF Scorecard intentionally skip on PRs and run on push/schedule/manual contexts.

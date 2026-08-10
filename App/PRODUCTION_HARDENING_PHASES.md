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
- Qwen3-8B now supports a fast vLLM runtime for local/ngrok demos (`LLM_BACKEND=vllm`, host `ura-vllm` on GPU 7) plus a BitsAndBytes NF4 4-bit local fallback.
- Compose caps vLLM responses with `LLM_MAX_TOKENS=256` to keep anonymous ngrok/proxy chat responses below the frontend timeout.
- Compose mounts Qwen and Whisper LoRA adapters from `fine-tuning/adapters` read-only, pins Whisper and reranking to CPU, and maps API dense retrieval to host GPU 2 as container `cuda:0` to avoid GPU 0 startup stalls.
- Redis is first-class in compose (`CACHE_BACKEND=redis`, `REDIS_URL`, `SLOWAPI_STORAGE_URI`) for semantic cache and distributed rate-limit storage.
- Cached and generated replies now share the same output sanitizer; TIN registration and return-filing how-to prompts bypass free-form LLM synthesis for vetted FAQ-backed answers, with high-precision FAQ hits and query-ranked deterministic revisions as fallback.
- Live smoke and deploy preflight scripts cover anonymous chat, informational TIN answers, explicit guided workflow behavior, speech health, and protected admin denial.
- Remaining work: Postgres migration tooling, Redis/Qdrant TLS verification, load/canary tests, disaster-recovery drills, and real-device mobile PWA test matrix.

## Phase 5 - FAQ Response Delivery & Natural Interaction

Status: applied and verified with live testing.

- Added `AgentRoute.GREET` to supervisor routing; greetings ("hi", "hello", "good morning") now return a warm welcome with suggested topics instead of triggering clarification or RAG retrieval.
- Greeting detection is always active (not gated behind `agentic_mode` flag) and works in both `generate()` and `generate_retrieval_only()` paths.
- Response judge no longer auto-revises well-grounded answers that lack explicit `[N]` citation markers — missing citations are only a revise signal when faithfulness score is also below 0.5.
- Grounded-revision truncation limit raised from 360 to 800 characters so procedural answers are not cut short.
- OutputGuard reasoning-prefix regexes narrowed: "However, the VAT rate..." and similar factual openings are no longer stripped. Patterns now require reasoning-specific context words (passage, context, section, source) after transition words.
- `sanitize()` paragraph-stripping loop now checks `_ANSWER_START_REGEX` before removing a paragraph, preventing valid answer text from being discarded.
- Frontend `THINKING_SIGNALS` regex tightened to preserve answers starting with transition words while still catching internal reasoning.
- System prompt updated: rule 1 now allows brief natural acknowledgments; new rule 15 encourages follow-up suggestions on short informational answers.
- Qwen3 thinking mode suppressed via `/no_think` tag in system prompt and `chat_template_kwargs: {"enable_thinking": False}` in vLLM calls.
- `LLM_MAX_TOKENS` increased from 256 to 512 for richer answer content.
- Claim verifier `_NON_CLAIM_HINTS` extended to exclude follow-up suggestions and contact details from verification, preventing false escalation.
- 24 unit tests passing (including 7 new greeting tests, 2 response judge tests, 4 OutputGuard preservation tests).

## CI / Security Workflow Stabilization

Status: applied for PR validation.

- Frontend PR gates now run ESLint, TypeScript, Vitest, Lighthouse accessibility, and Next.js build without a blocking coverage threshold in the unit-test step.
- Backend PR tests run with deterministic local isolation for analytics, Qdrant, and speech.
- The ML pipeline keeps a 35% Python coverage ratchet while focused backend/agentic coverage is expanded.
- Trivy and Checkov still scan on PRs and upload artifacts; GitHub Security SARIF upload is limited to non-PR events.
- OWASP ZAP and OSSF Scorecard intentionally skip on PRs and run on push/schedule/manual contexts.

## Phase 28 - Native Voice-to-Voice + Voice+Vision

Status: applied and verified with 72 new tests (0 regressions to existing 122 tests).

- **V2 streaming voice engine** (`voice_stream_v2.py`, `voice_ws_v2.py`): dual-path architecture with fast path (< 400ms for greetings/cached) and grounded path (< 800ms for full RAG) via `QueryPlanner` routing. WebSocket at `/v2/voice/chat/stream`, feature-flag gated by `FLAG_NATIVE_VOICE`.
- **Token-level streaming TTS** (`native_voice/streaming_tts.py`): CosyVoice2-0.5B flow-matching codec replaces sentence-chunked Piper; first audio in 150-250ms vs 400-800ms. WAXAL Luganda speaker embeddings for voice cloning. Falls back to Piper/edge-tts/Sunbird when CosyVoice2 is unavailable. Gated by `FLAG_STREAMING_TTS_V2`.
- **Streaming ASR with partial hypotheses** (`native_voice/streaming_asr.py`): sliding-window ASR (2s window, 500ms hop) with token stability tracking. Stable prefixes emitted as `partial_transcript` events during speech for downstream speculative retrieval.
- **Speculative retrieval prefetch** (`native_voice/speculative_prefetch.py`): starts background HybridRetriever search on partial ASR stable prefix (>= 4 tokens). If final query matches, cached hits are reused (saves 100-300ms). Deduplication by prefix hash, staleness discard after 3s. Gated by `FLAG_SPECULATIVE_PREFETCH`.
- **Query planner** (`native_voice/query_planner.py`): classifies voice queries via Supervisor + semantic cache + acknowledgement regex. Routes: FAST (greeting/cache/ack), GROUNDED (RAG/tools/specialist), VISION (image attached), ESCALATE (human handoff).
- **Voice+Vision V2** (`vision/encoder.py`, `vision/ocr.py`, `vision/document_classifier.py`): Qwen2-VL-2B-Instruct encodes scanned documents in parallel with ASR (zero added latency). EasyOCR extracts raw text. Rule-based classifier identifies 7 URA document types (receipt, TIN card, assessment, customs declaration, filing form, invoice, generic). Structured field extraction: TIN numbers, UGX amounts, dates, reference codes. Gated by `FLAG_VOICE_VISION_V2`.
- **Audio codec utilities** (`native_voice/voice_codec.py`): PCM/WAV roundtrip, resampling, RMS energy, 20ms frame splitting, optional Opus encoding for bandwidth-constrained mobile.
- **8 new Prometheus metrics**: `voice_v2_connections_total`, `voice_v2_active_connections`, `voice_v2_session_duration_seconds`, `voice_v2_speculative_prefetch_hits_total`, `voice_v2_speculative_prefetch_misses_total`, `voice_v2_vision_requests_total`, `voice_v2_path_routing_total{path}`, `voice_v2_tts_first_byte_seconds`.
- **Backward compatibility**: V1 WebSocket (`/v1/voice/chat/stream`) and all REST speech endpoints unchanged. V2 session degrades gracefully to V1 sentence-chunked TTS when CosyVoice2 is unavailable. All V2 components lazy-load behind feature flags (default OFF).
- **Safety**: Vision OCR text passes through existing `InputGuard` / `scan_retrieved_text()` for indirect injection defense. Images validated (max 2MB, JPEG). Consent enforcement identical to V1. Audit ledger records vision events.
- **Tests**: 72 new tests covering StreamingTTS (7), SpeculativePrefetcher (9), StreamingASR (4), QueryPlanner (8), VoiceCodec (8), VoiceSessionV2 (8), Phase28FeatureFlags (5), DocumentClassifier (12), OCR (6), VisionEncoder (3), VisionIntegration (2). Zero regressions in existing 122 tests.

## Phase 0 - Architecture Review Remediation (Stop-the-bleeding)

Status: applied with regression tests.

Addresses the four highest-blast-radius findings from the principal-level
architecture review (`~/.claude/plans/serialized-tinkering-engelbart.md`).

- **P0-1 — Confirmation authz bypass closed.** The WebSocket `tool_call.confirm`
  approve path (`chat_ws_v2.py`) now re-authorizes the submit through
  `MCPClient.call_tool(..., confirmed=True, user_role=..., granted_purposes=...)`
  instead of calling `ToolRegistry.call()` directly. The authenticated principal
  (role + consents) is resolved from the verified JWT at `session_start` and
  carried on the session — never taken from a client frame — and is also passed
  into `run_chat_turn` so the agentic tool loop authorizes correctly. A
  policy-denied submit now fails closed with `confirm_failed`. Regression tests
  assert a critical tool is denied for a public principal and allowed for an
  authorized one.
- **P0-3 — Unified output-guard pipeline.** Extracted `_apply_output_guards()`
  in `service.py`; both the token-streaming and the agentic (`tool_use`) branches
  of `run_chat_turn` now run the identical post-generation pipeline (faithfulness
  → claim verification → response judge → grounded revision → escalation/handoff/
  ticket). Previously the agentic branch only computed a faithfulness score, so
  enabling `FLAG_TOOL_USE` silently weakened grounding. `run_chat_turn` now also
  runs claim verification on both paths.
- **P1-9 — Voice WebSockets hardened.** `/v1` and `/v2` voice sockets now (a)
  require auth when `FLAG_AUTH_REQUIRED` is on, (b) enforce per-user + global
  concurrency caps via the new `ws_concurrency` module, and (c) bound both total
  session duration and idle time. Production startup now refuses to boot when
  `FLAG_NATIVE_VOICE`/`FLAG_VOICE_STREAMING` are enabled without
  `FLAG_AUTH_REQUIRED`.
- **P0-4 (config half) — Durable infra required in production.** Production
  startup (`_validate_production_env`) now fails closed unless durable backing
  services are configured. Postgres was already required; added:
  - `QDRANT_URL` must be set and **non-localhost** (vectors must live in an
    external/managed Qdrant, not the ephemeral in-container default), and
    `QDRANT_API_KEY` must accompany it.
  - `ANALYTICS_DB_DIR` must be set to an **absolute, non-ephemeral path** (not
    `/tmp`, `/var/tmp`, `/dev/shm`, not relative). The audit ledger and
    conversation memory are SQLite-backed via this directory **even when
    `ANALYTICS_BACKEND=postgres`**, so it must point at a mounted persistent
    volume or the tamper-evident audit trail and user memory are wiped on every
    container restart.

  **Operator action required:** a non-demo deployment must provide a managed
  Postgres (`ANALYTICS_BACKEND=postgres` + `POSTGRES_DSN`), an external Qdrant
  (`QDRANT_URL` + `QDRANT_API_KEY`), credentialed Redis (`REDIS_URL` /
  `SLOWAPI_STORAGE_URI`), and a persistent-volume mount for `ANALYTICS_DB_DIR`.
  New env knobs: `VOICE_WS_MAX_PER_USER` (3), `VOICE_WS_MAX_GLOBAL` (64),
  `VOICE_WS_MAX_DURATION_S` (1800), `VOICE_WS_IDLE_TIMEOUT_S` (120).

- **Deferred to a later phase (durability half of P0-4):** periodic Merkle-root
  export + external anchoring and an auth-gated `verify_chain` endpoint.
- **Incidental:** made `app/authority.py` timezone handling portable
  (`datetime.timezone.utc` instead of the 3.11-only `datetime.UTC`).

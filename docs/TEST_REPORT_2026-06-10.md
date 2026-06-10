# Regression + E2E Test Report — 2026-06-10

**Scope.** Consolidated regression pass over `dev` (f482a4b06e) after three freshly merged
feature waves: the Cloudflare/Gemini cloud-fallback layer (PR #95), the DNS-over-HTTPS
resolver (PR #97), and the empty-reply→cloud-fallback fix (PR #98). Four suites executed,
every failure triaged, the provider-fallback layer audited for coverage gaps, and the P1
gaps closed on branch `test/fallback-regression-gaps`.

## 1. Suite results

| Suite | Command | Before | After this branch |
|---|---|---|---|
| Backend regression (`App/backend/tests/`, 24 files) | `cd App/backend && PYTHONPATH=. ../../.venv/bin/python -m pytest tests/ -q` (Py 3.12) | **267 passed** | **295 passed** (+28 new) |
| Repo-root CI suite (`tests/`) — what GitHub Actions runs | `pytest tests/ -q` from repo root | **461 passed / 13 skipped** | **461 passed / 13 skipped** (unchanged; skips are deliberate lifespan-smoke + heavy-ML-deps guards) |
| Frontend unit (Vitest) | `cd App/frontend && bun run test` | **40 passed** | **40 passed** |
| Playwright E2E (66 test instances: chromium 31, mobile-chrome 31, a11y 4) | per-test protocol, see §5 | **54 passed / 12 failed** (5 unique tests) | **66 passed / 0 failed** |
| Lint | `ruff check ml/ App/backend/ --select=E9,F63,F7,F82` (CI-blocking set) + `eslint .` | clean | clean |

## 2. E2E failure triage (5 unique failures → all resolved)

| Test | Verdict | Resolution |
|---|---|---|
| `a11y.spec.ts:17` axe-core audit | **Real product bugs (2 WCAG violations)** | Fixed in product — see §3 |
| `a11y.spec.ts:43` ARIA landmarks | Stale selector (`hasText: /Speak/i` vs. the redesigned mic button whose accessible name is the aria-label "Start speaking") | Selector updated to `getByRole("button", { name: /speak/i })` |
| `smoke.spec.ts` ×3 (greeting, starter-prompt click, chat-area) | Stale spec — written 2026-04-13 against the pre-redesign UI (seeded greeting bubble, old selectors, no consent banner). Same drift class as the `tests/agents/` GREET fixes in PR #93. | Modernized to the current landing shell (hero + suggested questions), using the shared `seedConsent`/`clearChatStore`/`mockBackend` helpers so the consent dialog cannot intercept clicks |

Distinct from real failures: running many Playwright tests in one **batch** crashes
single-process Chromium in this sandbox (tests fail in batch, pass solo). That is a sandbox
artifact, not an app bug — CI and real browsers run batched normally. See §5.

## 3. Product issues found and fixed

1. **Sync agentic path skipped the cloud fallback** — `ChatModel.generate`
   (`App/backend/app/service.py`): when the supervisor or `FLAG_TOOL_USE` routed a request
   agentic and the primary LLM produced no text (vLLM down, breaker open, deadline),
   the code dropped straight to the extractive best-hit answer — bypassing the
   Cloudflare/Gemini fallback that PR #98 wired into the **non-agentic** path, and that the
   **streaming** path already gets by falling through to `stream_llm_tokens`. On the Crane
   Cloud profile this silently degraded "smart" answers to FAQ extracts even with the cloud
   fallback fully configured. Fixed by routing an empty agentic reply through
   `_call_llm_with_deadline` (primary retry → cloud fallback → best-hit). Regression test
   proves the chain (`AgenticCloudFallbackChainTest`, mutation-checked: fails without the
   fix).
2. **[critical] WCAG 1.3.1 `aria-required-children`** — `ConversationRail.tsx` rendered
   `role="list"` with no `listitem` children in the empty state. The role (and its
   `aria-label`) is now applied only when conversation items are rendered.
3. **[serious] WCAG 1.4.3 colour contrast** — the `--text-3` muted-text token (`#6B7280`)
   measured 3.9–4.1:1 as body text on the dark surfaces (rail empty state, composer hint
   `.composer-hint`), below the 4.5:1 AA minimum. Token raised to `#848D9C` (≈5.7:1),
   fixing all ~15 usages at once. axe-core audit now passes.

## 4. Coverage gaps in the provider-fallback layer

### P1 — closed on this branch (+28 backend tests)

| Gap (file:symbol) | Risk if untested | New tests |
|---|---|---|
| `service.stream_llm_tokens` fallback triggers (empty stream / raised stream / breaker OPEN / LLM unavailable) | The SSE/WS twin of the PR #98 bug could regress silently | `StreamFallbackTest` (4) |
| `service._call_llm_agentic` soft-failure contract (empty dict + breaker bookkeeping) | Callers rely on this contract to engage their own fallback | `AgenticDeadlineContractTest` (3) |
| Sync agentic → plain-chain fallback (the §3.1 fix) | The exact silent-degradation bug above | `AgenticCloudFallbackChainTest` (2, drives the real `ChatModel.generate`) |
| `resilience.CircuitBreaker` state machine (CLOSED→OPEN threshold, HALF_OPEN probe, exponential back-off, `max_timeout` cap) | Every provider tier trusts this one class; oscillation or stuck-open states were unvalidated | `tests/test_resilience.py` (7, fake-clock driven) |
| `speech_service._cf_whisper_transcribe` (Workers AI Whisper, tier ⑤) | Documented as "Phase 4 DONE" but had **zero** tests | `CfWhisperSTTTest` (5) |
| `main._validate_production_env` Cloudflare branch | Misconfigured prod could boot with a silently no-op fallback | 4 tests in `test_production_hardening.py` |
| `retriever._search_vectorize` error paths (breaker OPEN / budget exhausted / gateway error) | Dense-fallback failures must degrade to BM25, not crash or spend | 3 tests in `RetrieverVectorizeModeTest` |

### P2 — known, deliberate follow-ups *(closed same-day — see Addendum)*

- **`providers/r2.py` has zero tests** (`get_object`/`put_object`/`object_exists`) — becomes
  load-bearing when BM25-state durability / TTS cache land.
- **`scripts/reindex_vectorize.py` untested** (~150-line CLI: batch embed, NDJSON,
  wrangler upsert, `--verify`).
- **Budget guard edge cases** (`providers/budget.py`): Redis→in-process failover, UTC
  midnight rollover, concurrent consumption.
- **DoH resolver**: malformed DNS payloads and cache-TTL expiry are untested
  (`doh_resolver.py`; happy path + failure→system-resolver are covered).
- **End-to-end fallback integration tests**: no test drives `/v1/chat` or
  `/v1/chat/stream` with the primary LLM mocked down to assert the cloud answer reaches
  the HTTP/SSE surface.

### P3 — observations / latent inconsistencies

- `stream_llm_tokens`' LLM-unavailable→cloud-fallback branch is currently unreachable from
  `stream_chat_turn`, which gates on `llm_module.is_available()` *before* calling it
  (`service.py` streaming section). Harmless today (with `LLM_BACKEND=vllm`,
  `is_available()` is always `True`), but the gate means a deployment with **no** LLM
  configured never engages the cloud fallback even when configured. Decide whether that is
  policy or a bug.
- After an agentic failure, the sync path now retries the primary once more inside
  `_call_llm_with_deadline` before going to the cloud (worst case ≈ agentic deadline +
  plain deadline). This mirrors the streaming path's fall-through semantics — acceptable,
  but worth revisiting if p99 latency matters under outage.
- `docs/CLOUDFLARE_FALLBACKS.md` claimed "17 provider tests" / "Phase 4 STT DONE" while the
  STT tier had no tests — the docs over-claimed test coverage (now true: 36 provider tests).

## 5. How to re-run (sandbox-specific)

- **Backend/root suites**: use the repo-local Python 3.12 venv (`FinalYearProject/.venv`);
  the sandbox system Python is 3.10 and cannot run the authority/production-hardening tests
  (`datetime.UTC`). Do **not** point the analytics DB at `/tmp` (tmpfs breaks SQLite WAL).
- **Playwright**: port 3000 is occupied by an unrelated app in this sandbox and
  `reuseExistingServer: true` will silently test it. Start the app on a free port
  (`bun run next dev -p 3100`) and export `BASE_URL=http://localhost:3100`. Set
  `PW_SINGLE_PROCESS=1` (config opt-in adds `--single-process --no-zygote`); single-process
  Chromium crashes on large batches, so validate per test:
  `playwright test <file>:<line> --project=<chromium|mobile-chrome|a11y> --workers=1`.
- **CI parity**: the GitHub Actions unit-test step runs the **repo-root** `tests/` tree;
  the blocking lint step is `ruff check ml/ App/backend/ --select=E9,F63,F7,F82`.

---

## Addendum (2026-06-10, same branch): P2/P3 closure + CI coverage incident

**CI incident.** The first push of this branch failed the ML Pipeline workflow — all
470 CI tests passed, but the coverage gate tripped: `total of 34 is less than
fail-under=35`. Root cause is structural: CI measures `--cov=App/backend` while running
only the repo-root suite (the 295-test backend suite never runs in CI), and
`service.py` is one of the few backend runtime modules **not** in the coverage omit
list — so the 17 new (backend-suite-covered) fix lines tipped the rounding. Resolved by
the new root-level integration tests below, which lift the measured total to **≈38%**
(`service.py` 52%) — and which were owed anyway as the P2 endpoint-level gap.

**P2 gaps — all closed:**

| Gap | Closure |
|---|---|
| Endpoint-level fallback integration | `tests/test_fallback_integration.py` (repo root, **runs in CI**): `/v1/chat` with empty primary reply → cloud answer over HTTP; `/v1/chat/stream` with empty stream → cloud tokens over SSE; unconfigured fallback still degrades to best-hit; LLM-unavailable case below |
| `providers/r2.py` zero tests | `R2Test` (7): unconfigured no-op (client never built), get/put/head argv + body round-trip, error paths → None/False |
| `reindex_vectorize.py` untested | `tests/test_reindex_vectorize.py` (6): NDJSON row shape + deterministic ids + 2000-char metadata truncation, strict-zip length mismatch raises, embed batching honours `INDEX_BATCH_SIZE`, fixed wrangler argv with `check=True`, unconfigured `sys.exit` guard, `--verify` round-trip |
| Budget guard edges | `BudgetTest` +3: UTC-midnight window reset, broken-Redis degradation to in-process counters, 32-thread burst grants exactly the budget |
| DoH untested paths | +4: malformed DNS payload → `socket.gaierror` → system-resolver fallback, valid-wire-no-A-record → gaierror, cache re-resolves after the 60s TTL |

**P3-1 — fixed (was "decide whether policy or bug": bug).** New
`service._cloud_llm_ready()` widens the availability gates in `ChatModel.generate` and
`stream_chat_turn`: when the local LLM is entirely unavailable but the flag-gated cloud
tier is configured, generation now routes to Cloudflare/Gemini instead of silently
degrading to FAQ extracts. The agentic branch still requires the local model (tool
calling is local-only). Covered by `CloudLlmReadyTest` (5), a `ChatModel.generate`
chain test, and the endpoint-level `test_llm_unavailable_still_serves_cloud_answer`
(mutation-checked: fails with the gate fix reverted). P3-2 (post-agentic primary retry
latency) stands as documented behaviour; P3-3 resolved by updating
`docs/CLOUDFLARE_FALLBACKS.md` to the real test counts.

**Updated totals:** backend suite **321 passed** (+26 over the morning run); repo-root
suite **465 passed / 13 skipped** (+4); CI coverage gate restored with ~3-point
headroom.

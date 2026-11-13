# Intelligent UI integration verification

Updated 2026-11-13 for phase 1 of the
[13 November audit](../plans/intelligent-ui-integration-audit-2026-11-13.md)
(ADR-0047, G618–G624). The 2026-10-30 baseline (`87460457`, ADR-0018) and its
[findings](../plans/intelligent-ui-integration-audit-2026-10.md) follow below.

## Local gates

From `App/frontend`, use the Node version in `.nvmrc` and Bun in `.bun-version`:

```bash
bun install --frozen-lockfile
bun run test
bunx tsc --noEmit --incremental false
bun run lint
bun run test:e2e e2e/chat-flow.spec.ts --project=chromium --workers=1
bun run test:a11y --grep 'every required surface'
bun audit
```

From the repository root:

```bash
python3 governance/compliance_check.py
python3 threat-model/validate_threats.py
PYTHONPATH=App/backend python3 -m pytest tests/test_monorepo_architecture_governance.py App/backend/tests/test_runtime_package_boundary.py -q
PYTHONPATH=App/backend python3 -m app.freshness --check
```

A passing source/artifact gate does not prove a control's effectiveness against
live adversarial traffic.

## Local results — 13 November 2026

Phase 1 (ADR-0047) on `fix/intelligent-ui-p1-figures-and-disclosures-2026-11`, from
`dev` at `882a99b5`; Node 24.19.0, Bun 1.3.14 (the repository pins 1.4.3; CI uses
the pin), Python 3.11 (`.venv`), Playwright Chromium 1248 against `next dev` on
`127.0.0.1:3100` (`CI=1 BASE_URL=http://127.0.0.1:3100`). The laptop was on battery.

| Gate | Result |
|---|---|
| `bun run test` | 97 files, 937 tests passed (baseline 96 / 921) |
| `tsc --noEmit`, `bun run lint` | Passed |
| `e2e/chat-flow.spec.ts`, chromium | 16 passed, 3 of them new (deadline stop, disclosure kept, stream abstention) |
| `test:a11y`, every required surface | Passed; the new offline-page scan passes in light, dark and Acholi |
| `pytest tests/` (root, one process) | 4809 passed; 1 failed (`test_workspace_hygiene…`: an empty, ignored local `.agents` directory; not in CI's checkout); 1 collection error (`test_cx_500_complex_ngrok_schema.py`: `requests` not in `.venv`) |
| `App/backend`: `pytest tests/` (one process, `VLLM_BASE_URL=http://127.0.0.1:1/v1`) | 4169 passed, 25 skipped; 1 collection error (`test_tracing_genai.py`: `opentelemetry` not in `.venv`) |
| `compliance_check.py`, `validate_threats.py` | Passed; STRIDE 6/6, OWASP LLM 10/10 |
| `bun audit` | 28 findings (7 high, 17 moderate, 4 low), unchanged by this phase (G276) |
| Mutation checks | Reverting the G620 footer or judge band, the G622 grounding fields, or the G621 deadline handling fails the new tests |

## Local results — 30 October 2026

Working tree of `fix/intelligent-ui-integration-audit-2026-10` on `87460457`;
Node 24.14.0, Bun 1.4.3, Python 3.12.9, Playwright Chromium headless shell 1248.
Browser runs reused a `next dev` server on `127.0.0.1:3100`
(`CI=1 BASE_URL=http://127.0.0.1:3100`), so Playwright did not start its own.

| Gate | Result |
|---|---|
| `bun install --frozen-lockfile` | No changes |
| `bun run test` | 89 files, 811 tests passed |
| `tsc --noEmit`, `bun run lint` | Passed |
| `e2e/chat-flow.spec.ts`, chromium | 13 passed against the mocked backend |
| `test:a11y`, every required surface | Passed in both themes |
| `bun audit` | 25 findings (7 high, 14 moderate, 4 low); dependencies unchanged here (UI-12) |
| `compliance_check.py`, `validate_threats.py` | Passed; STRIDE 6/6, OWASP LLM 10/10 |
| Architecture and package-boundary pytest | 11 passed, including workspace hygiene |
| `app.freshness --check` | Exit 2: `App/Model/index_freshness.json` absent (UI-13) |

Registry facts are unchanged by this frontend change: 72 tools in 18 namespaces,
86 flags and 5 locales.

## Staging probes before production

1. Through the actual reverse proxy, cut a chat stream after several tokens.
   Expect retained text and an incomplete status, no action chips, no automatic
   narration and no second POST. Repeat with an `error` frame and a timeout.
2. Return 405 for the streaming route. Expect one REST fallback with the same
   inferred objective, sources and workflow. Return 429 or 503 and expect no
   automatic fallback. Authentication rejection must still follow server policy.
3. Start a response, then switch chats. No old text, metadata, locale update or
   confirmation may appear in the newly opened conversation. Reopening the old
   chat must show it as incomplete, never as an actionable completed answer.
4. Advance a workflow and return to its earlier card. Earlier options must not
   submit. Reload, or reopen the chat from the conversation list: stored
   workflow and sign-in controls require refreshed server guidance. Check this
   alongside officer takeover and release of the conversation.
5. In a portal confirmation, edit a value and interrupt preparation. Preserve
   the edit and show an error; no chat submission follows. An empty/unrecognised
   execution response must not display success. With Web Crypto unavailable,
   approval must not be sent; cancellation must remain possible.
6. Use keyboard and screen reader to review all of the above. Check each supported
   language with a human reviewer and confirm that domain validation retains all
   configured legitimate URA navigation links.

## Phase 1 checks on the Space (13 November 2026)

What the taxpayer sees about a figure is true (ADR-0047). Run after the roll, against
`https://landwind22-ura-chatbot.hf.space`, in a private window:

1. **Offline page (G618).** Load `/offline.html` directly. It shows no rate, amount,
   calculator or fiscal year; the five language buttons switch the text and `lang`.
   In DevTools → Application → Cache Storage the cache is `ura-v8-<12 hex>`, and the
   hex equals `sha256sum App/frontend/public/offline.html | cut -c1-12` for the
   deployed commit; no `ura-v7` cache remains after a reload.
2. **Unconfirmed rate (G620).** Ask "What is the withholding tax rate on royalties?".
   The reply says the figure has not been confirmed against a gazetted schedule, and
   the badge reads "Rate not yet confirmed: check with URA", not "Official Statutory
   Calculator". Ask "What is the current VAT rate in Uganda?": the badge is
   "Official Statutory Calculator". With `curl -N` on `/api/v1/chat/stream`, the
   first answer's `grounding` frame has `response_judge.confidence_band: "medium"`.
3. **Reminder (G619).** Ask "When are PAYE returns due?". If the answer states the
   15th of the following month, the reminder names PAYE, says it is for the month
   just ended, and its date is the next 15th (today if today is the 15th). Ask how to
   object to an assessment: no reminder appears.
4. **Translated badges.** Switch to Luganda and ask a question; the badge under the
   answer is in Luganda.

The deadline and abstention paths (G621, G622) need a forced condition, so they are
covered by `e2e/chat-flow.spec.ts` against a mocked stream rather than probed live.

## Evidence and rollback

Record build/commit, browser, locale, endpoint status, test IDs and redacted
request counts. Do not put taxpayer text or portal credentials in release logs.
Use the existing `chat_fetch_failed` event and existing service metrics for the
canary comparison; this change introduces no new telemetry series or thresholds.

Rollback the frontend artifact if valid server replies routinely fail the new
completion contract or current workflow/portal controls cannot be used. Preserve
the incident evidence and correct the contract mismatch before widening rollout.
Do not bypass server flags, officer authorization, signature checks or production
gates to make a UI probe pass.

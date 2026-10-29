# Intelligent UI integration verification

Updated 2026-10-30 against baseline `87460457` and the ADR-0018 change.
[Findings, sources and residual risks](../plans/intelligent-ui-integration-audit-2026-10.md).

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

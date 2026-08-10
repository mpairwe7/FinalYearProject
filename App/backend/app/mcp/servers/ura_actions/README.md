# `mcp_ura_actions` — URA DMZ write actions (Phase 17)

> **Risk tier: CRITICAL** · **Status: scaffolded, do NOT implement
> without full security review.**
>
> Every tool in this server performs a **write** against URA's
> production API — filing a return, making a payment, lodging an
> objection.  Failures or misuse have **financial and legal
> consequences** for the user.

## Hard requirements before any code ships

1. **2-factor user confirmation** on every call:
   - Form-preview dialog shown to the user via the frontend
   - WebAuthn re-authentication required immediately before the
     call is dispatched
   - The LLM CANNOT trigger a call from prose alone — the agent
     can only *propose* the action
2. **Idempotency keys** on every request, persisted in the audit
   ledger before the URA API is called
3. **Durable workflow wrapper** (Temporal.io) so mid-flight
   failures can be compensated
4. **Post-confirm read** — after every write, the agent runs
   `mcp_ura_account.get_filing_status` to verify the write took
   effect; if not, the ledger records both "attempted" and
   "confirmed_failed" entries
5. **Full audit replay** — reconstruct the exact request, user,
   time, policy version, model revision, and URA API response
6. **Policy sign-off** from URA legal + compliance before
   production rollout

## Proposed tools (NOT exhaustive, to be scoped with URA)

| Tool | Risk | Confirmation | Durable |
|---|:---:|:---:|:---:|
| `file_vat_return` | critical | 2FA | ✓ |
| `file_paye_return` | critical | 2FA | ✓ |
| `make_payment` | critical | 2FA | ✓ |
| `lodge_objection` | high | 2FA | ✓ |
| `update_registration_details` | high | 2FA | ✓ |

## Dependencies

- Phase 14 (auth + consent + WebAuthn)
- Phase 15 Lite (MCP)
- Phase 17 `mcp_ura_account` (for post-confirm reads)
- Phase 21 audit ledger — **non-negotiable** for this server
- Temporal.io cluster

## Effort estimate

8+ weeks after `mcp_ura_account` is live and stable.

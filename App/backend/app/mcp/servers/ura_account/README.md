# `mcp_ura_account` — URA DMZ read-only account access (Phase 17)

> **Risk tier:** high · **Status:** scaffolded (not yet implemented)
>
> **This server does not run in the core namespace.** It runs in
> URA's DMZ, mutually authenticated via SPIRE/SPIFFE workload identity,
> speaks mTLS to the URA production API, and is rate-limited per-user.

## What it exposes

Five read-only tools that let an authenticated taxpayer see their
own URA account state via the agent:

| Tool | Input | Output | URA API |
|---|---|---|---|
| `get_tin_status` | — | `{active, issued_at, type}` | `GET /v1/taxpayer/tin` |
| `get_filing_status` | `fiscal_year` | `{returns_filed[], returns_due[]}` | `GET /v1/taxpayer/filings?fy=...` |
| `get_balance` | — | `{assessed, paid, outstanding}` | `GET /v1/taxpayer/balance` |
| `list_returns_due` | `within_days` | list of `{tax_type, due_date, amount_est}` | composition of above |
| `get_registered_tax_types` | — | `list[str]` | `GET /v1/taxpayer/registrations` |

## Auth & access control

- **Delegated user JWT** — the agent forwards the user's OIDC
  access token (DPoP-bound) so URA's API can log the actual
  taxpayer making the request.
- **SPIRE/SPIFFE workload identity** — the MCP server itself
  proves it is `spiffe://ura.go.ug/mcp/ura_account` before URA's
  API accepts the connection.
- **mTLS** — client certs rotated every 24h via cert-manager.
- **Consent gate** — every call is pre-checked for an active
  `ura_account_access` consent receipt (see Phase 14).
- **Rate limit** — per-user, not per-IP, to stop shared-IP hosts
  from DOS'ing legitimate users.

## Audit

Every call writes to the hash-chained ledger with:

```json
{
  "tool": "get_filing_status",
  "user_sha256": "...",      // pseudonymised
  "arguments_sha256": "...",
  "result_sha256": "...",
  "ura_api_latency_ms": 142,
  "spire_cert_fp": "...",
  "policy_version": "2026-04-opa-r1"
}
```

## Dependencies

- Phase 14 (auth + consent) — required
- Phase 15 Lite (MCP client) — required  
- URA production API contract — **blocking external dependency**
- SPIRE cluster deployed in both core namespace and URA DMZ
- cert-manager + internal CA
- Vault or equivalent for URA API credentials

## Effort estimate

6 weeks, including URA integration calendar (not just engineering time).

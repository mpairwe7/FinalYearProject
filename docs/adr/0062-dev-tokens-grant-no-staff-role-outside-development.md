# ADR-0062: A development token grants no staff role outside development

## Status

Accepted, 2026-11-18. Found by the repository audit and confirmed on the HF Space,
which runs with an `APP_ENV` other than `production`. Closes G671.

## Context and Problem Statement

Every `APP_ENV` other than `production` was treated as development:

- `POST /v1/auth/dev-token` answered anyone. A body of `{"role": "ura_admin"}`, or an
  `@ura.go.ug` email, returned a signed `ura_admin` token. No password was involved.
- The REST and WebSocket entry points accepted an HS256 token signed with
  `AUTH_DEV_SECRET` even under an RS256 verifier, and that secret defaults to the
  public text `dev-insecure-change-me`.

The Space's startup log has no "Production environment validation passed" line, and
its dev-token endpoint returns tokens, so both paths were open on the live service.
The portal's demo quick sign-in uses the endpoint for one demo taxpayer, so it could
not simply be switched off.

## Considered Options

1. Allow staff roles in development tokens only where `APP_ENV` is `development` or
   `test`; elsewhere the endpoint mints only the portal demo taxpayer, and a
   dev-signed token that resolves to a staff role is refused.
2. Set `APP_ENV=production` on the Space.
3. Remove the dev-token endpoint and the HS256 fallback.

Option 2 would stop the Space: the production gate also requires a rotated dev
secret, DPIA and PDPO attestations and more. Option 3 breaks the portal demo and
local development. Option 1 closes the escalation and keeps both.

## Decision Outcome

Option 1. `jwt_auth.DEV_TOKEN_STAFF_ENVS` is `{"development", "test"}`.
`_hs256_verify`, which every dev-token path goes through (the HS256 verifier and the
three RS256 fallbacks), refuses a token whose `resolve_role` is `ura_*` outside those
environments. Outside them, `/v1/auth/dev-token` mints only the demo taxpayer
`verified_taxpayer` the portal asks for, and answers 403 otherwise.

## Consequences

- Staff on the Space sign in through the OIDC provider only, as they already do.
- A token forged with the default secret can still claim a taxpayer role outside
  development. Setting `AUTH_DEV_SECRET` to a random value on each deployment
  closes that, and the production gate already requires it.

## Confirmation

- `App/backend/tests/test_dev_token_staff_roles.py`: under `APP_ENV=staging` the
  endpoint refuses staff bodies and any user but the demo taxpayer, and a dev-signed
  staff token is refused. Under `development` both still work.

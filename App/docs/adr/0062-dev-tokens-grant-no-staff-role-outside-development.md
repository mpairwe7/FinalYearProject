# ADR-0062: A development token grants no staff role outside development

## Status

Accepted, 2026-11-18. Found by the repository audit and confirmed on the HF Space,
which runs with an `APP_ENV` other than `production`. Closes G671.

Amended 2026-11-18: the first rule was live on the Space and changed nothing there,
because the Space's `APP_ENV` secret reads `development` and it sets no
`AUTH_DEV_SECRET`. The trust rule now also requires a private secret.

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

Option 1, with one more condition. `jwt_auth.dev_token_is_trusted(secret)` holds only
when `APP_ENV` is in `jwt_auth.DEV_TOKEN_STAFF_ENVS` (`{"development", "test"}`) and the
signing secret is neither empty nor the documented default `PUBLIC_DEV_SECRET`. Anyone
can sign with those two, so `development` on the default is as open as `staging`.
`_hs256_verify`, which every dev-token path goes through (the HS256 verifier and the
three RS256 fallbacks), applies three rules wherever a token is not trusted. The token must
name the portal demo taxpayer (`jwt_auth.PORTAL_DEMO_TAXPAYER_ID`). Its role, read with
the verifier's audience exactly as `_claims_to_user` reads it, must not be `ura_*`. And
its `exp` and `nbf` must hold, which the fallbacks did not check before. The claims it
returns come from a fixed demo template (tenant `default`, the demo email, no granted
purposes); only `iss`, `aud`, `iat`, `nbf` and `exp` are kept from the token. Where
`dev_token_is_trusted(AUTH_DEV_SECRET)` fails, `/v1/auth/dev-token` mints only that demo
taxpayer as `verified_taxpayer`, and answers 403 otherwise.

## Consequences

- Staff on the Space sign in through the OIDC provider only, as they already do.
- Local staff dev tokens need `AUTH_DEV_SECRET` set to a random value (`.env.example`).
  The test suites set one per run in `App/tests/conftest.py` and
  `App/backend/tests/conftest.py`.
- A token forged with the default secret can name only the demo taxpayer outside
  development, whose account holds demo data. A random `AUTH_DEV_SECRET` on each
  deployment is still advised; the production gate already requires it.

## Confirmation

- `App/backend/tests/test_dev_token_staff_roles.py`: under `APP_ENV=staging` the
  endpoint refuses staff bodies and any user but the demo taxpayer, and a dev-signed
  token is refused when it names another taxpayer, carries a staff role (flat or under
  `resource_access.<audience>.roles`), or has expired. Under `development` on the
  public default or an empty secret, a dev-signed staff token is refused and the
  endpoint mints only the demo taxpayer. Under `development` on a private secret staff
  dev tokens still work. Removing any of the rules, the secret condition included,
  fails its test.
- On the HF Space, after the roll: `{"role": "ura_admin"}` to `/api/v1/auth/dev-token`
  answers 403, and the portal demo body answers 200.

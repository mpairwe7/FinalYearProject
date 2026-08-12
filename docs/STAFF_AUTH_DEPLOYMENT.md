# Staff Auth — Deployed Configuration

Audit reference for how staff sign-in is wired on the live Hugging Face Space.
Setup instructions live in `docs/PROJECT_SETUP.md` → *Staff Sign-In (OIDC)*; this
file records **what is actually configured**, where each value lives, and why.

Last updated: 2026-08-13.

## Topology

```
browser ──1── /signin  (Next.js, public client, no secret)
        ──2── Auth0 /authorize      (redirect, PKCE S256 + audience)
        ──3── /signin/callback      (code → token, direct to Auth0 /oauth/token)
        ──4── /api/v1/me            (same-origin rewrite → FastAPI)
                 └── verifies RS256 against Auth0 JWKS, resolves role
```

Leg 3 is the only browser call that does **not** go through the `/api/*` rewrite.
A public client holds no secret, so there is nothing for a server-side proxy to
protect, and the backend issues no tokens of its own. That is why the provider
origin must appear in `connect-src` — see *CSP* below.

## Identity provider

| | |
|---|---|
| Provider | Auth0 (free tier) |
| Tenant | `dev-s16d7m00eyrksjy2.us.auth0.com` |
| Issuer (`iss`) | `https://dev-s16d7m00eyrksjy2.us.auth0.com/` — **trailing slash is significant** |
| Discovery | `https://dev-s16d7m00eyrksjy2.us.auth0.com/.well-known/openid-configuration` |
| Authorization endpoint | `/authorize` |
| Token endpoint | `/oauth/token` |
| JWKS | `/.well-known/jwks.json` |
| Application type | Single Page Application (public client, PKCE `S256`) |
| API audience | `https://ura-chatbot/api` |
| Signing | RS256 |

Endpoints are **discovered at runtime**, not hardcoded — Auth0's paths differ from
Keycloak's (`/protocol/openid-connect/{auth,token}`), and Entra/Okta differ again.

## Where each value lives

Nothing here is a secret. A public client's issuer, client id and audience are
visible in the shipped bundle by design, and JWKS is a public endpoint. **There is
no client secret anywhere in this system.** If one appears to be required, the
Auth0 application was created as the wrong type.

### GitHub repository *variables* — build time

`NEXT_PUBLIC_*` is inlined by `next build`; it cannot be supplied at runtime.
Set under Settings → Secrets and variables → Actions → Variables, consumed by
`ura-chatbot-build-push.yml` as Docker build args (see `App/Dockerfile.cranecloud`).

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_OIDC_ISSUER` | `https://dev-s16d7m00eyrksjy2.us.auth0.com/` |
| `NEXT_PUBLIC_OIDC_CLIENT_ID` | the SPA client id |
| `NEXT_PUBLIC_OIDC_AUDIENCE` | `https://ura-chatbot/api` |

Changing any of these requires a **rebuild and a Space roll**, not just a restart.

### HF Space *variables* — runtime

Set on `landwind22/ura-chatbot` (Settings → Variables and secrets). Read by the
FastAPI process at start; changing them restarts the Space but needs no rebuild.

| Variable | Value |
|---|---|
| `AUTH_ALG` | `RS256` |
| `OIDC_ISSUER` | `https://dev-s16d7m00eyrksjy2.us.auth0.com/` |
| `OIDC_AUDIENCE` | `https://ura-chatbot/api` |
| `OIDC_JWKS_URL` | `https://dev-s16d7m00eyrksjy2.us.auth0.com/.well-known/jwks.json` |
| `OIDC_ROLE_CLAIM` | *(only if roles are not in a probed claim — see below)* |

## Roles

The backend probes these claims in order and takes the widest role it recognises
(`resolve_role` in `App/backend/app/auth/dependencies.py`):

`role` → `roles` → `realm_access.roles` → `groups` → `permissions` →
`resource_access.<audience>.roles`

Auth0 needs **RBAC enabled on the API** with *Add Permissions in the Access Token*,
which lands them in `permissions`. Alternatively a Post-Login Action can set a
namespaced claim — note the claim *name* then contains dots, which is handled (a
literal claim name is matched before dot-path walking):

```js
exports.onExecutePostLogin = async (event, api) => {
  api.accessToken.setCustomClaim("https://ura.go.ug/roles", event.authorization?.roles ?? []);
};
```
```
OIDC_ROLE_CLAIM=https://ura.go.ug/roles
```

Role names must be exactly `ura_admin`, `ura_staff`, `ura_auditor`. Hyphens and
group-path prefixes (`/ura-admin`) are normalised; anything unrecognised resolves
to `public` and the dashboards refuse it — deliberately, rather than raising.

`event.authorization.roles` is only populated when the request carries an
`audience` for an API with RBAC enabled. Without it the claim is empty and every
officer resolves to `public`.

## CSP

`next.config.mjs` derives the provider origin from `NEXT_PUBLIC_OIDC_ISSUER` and
adds it to `connect-src`, and only when a provider is configured, so a deployment
without OIDC keeps the tighter policy. Without this the leg-3 token exchange is
blocked and sign-in fails with an opaque `NetworkError`.

## Failure modes, by symptom

| Symptom | Cause |
|---|---|
| `NetworkError` on the callback | provider origin absent from `connect-src`, or whitespace in the issuer |
| `invalid token: malformed token` | Auth0 returned an **opaque** token — the `audience` was not sent |
| `invalid token: issuer mismatch` | `OIDC_ISSUER` missing the trailing slash |
| `invalid token: audience mismatch` | `OIDC_AUDIENCE` ≠ the API identifier |
| `invalid token: unexpected alg: HS256` | expected in RS256 mode; also confirms `AUTH_ALG=RS256` is live |
| Officer refused with role `public` | roles not in a probed claim, or RBAC not enabled on the API |
| `unauthorized_client` at the token endpoint | app registered as Regular Web App instead of SPA |

## Verification

Confirmed against a live Keycloak 26 and re-run after every refactor — 15/15
checks for both an admin and a staff identity, covering the PKCE redirect, the
provider login, the code exchange, RS256 verification against the live JWKS, role
resolution, and the landing page. Vocabulary and temporal cases are pinned in
`App/backend/tests/test_oidc_role_claims.py`; discovery in
`App/frontend/src/__tests__/lib/oidc.test.ts`; the dashboards' layout in
`App/frontend/e2e/staff-ui.spec.ts` (chromium + mobile-chrome in CI).

Quick live check that RS256 mode is active — an HS256-signed token must be refused:

```bash
curl -s https://landwind22-ura-chatbot.hf.space/api/v1/me \
  -H "Authorization: Bearer <any HS256 token>"
# {"detail":"invalid token: unexpected alg: HS256"}
```

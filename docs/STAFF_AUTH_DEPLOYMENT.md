# Staff Auth — Deployed Configuration

Audit reference for how staff sign-in is wired on the live Hugging Face Space.
Setup instructions live in `docs/PROJECT_SETUP.md` → *Staff Sign-In (OIDC)*; this
file records **what is actually configured**, where each value lives, and why.

Last updated: 2026-08-13.

**Current status:** deployed on image `sha-9c69489` and verified 16/16 against the
live Space — the staff pages serve, the CSP carries the provider origin, the
backend runs RS256 against the tenant's JWKS, and the sign-in redirect reaches
Auth0's Universal Login with PKCE S256, the audience and no client secret. Auth0
accepts the authorization request and serves its login form.

The one leg not covered by automation is entering a user's password, which
belongs to the operator. To finish the check by hand: sign in as a user holding
`ura_admin` and confirm the landing on `/admin`; then as `ura_staff` and confirm
`/agent`. If either lands but the dashboard refuses with role `public`, the roles
are not reaching a probed claim — see *Roles* above.

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

`OIDC_ROLE_CLAIM` is authoritative **when that claim is present in the token**,
including when it is present but empty (a real answer: "this user holds no
roles"). When the claim is *absent* the defaults are probed anyway, and a warning
is logged naming the variable. That is deliberate: the variable is usually set at
the same time as the provider mapping meant to emit it, and if the mapping is
missing or misspelled, an exclusive override would resolve every officer to
`public` and lock all staff out — to defend against claim injection that the
signed token already prevents.

### Getting roles into an Auth0 token

Assigning a role in Auth0 does **not** put it in the access token, and the RBAC
toggle adds *permissions* (`read:tickets`-style), not role names. Two routes:

**Post-Login Action (recommended — sends role names).** Enable RBAC on the API so
`event.authorization` is populated, then Actions → Library → Build Custom →
Login/Post Login:

```js
exports.onExecutePostLogin = async (event, api) => {
  api.accessToken.setCustomClaim("https://ura.go.ug/roles", event.authorization?.roles ?? []);
};
```
```
OIDC_ROLE_CLAIM=https://ura.go.ug/roles
```

The Action must be **Deployed** *and* dragged onto **Actions → Triggers →
post-login** and applied. An action that is built but not attached to the flow
never runs, and the claim silently never appears.

**Permissions shortcut (no Action).** Name the API's permissions literally
`ura_admin` / `ura_staff` / `ura_auditor`, assign them to the matching roles, and
enable RBAC + *Add Permissions in the Access Token*. They land in `permissions`,
which is probed by default, so `OIDC_ROLE_CLAIM` stays unset. It works, but it
uses permissions to carry role names, which is not what they are for.

Users must live in the **`Username-Password-Authentication`** connection to sign
in with a password; a Google-federated user has no database password at all.

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
| `invalid_request` — *Client "…" is not authorized to access resource server "…"* | the API exists but the application is not authorized for it (Auth0 side) |
| `access_denied` — *Service not found: <audience>* | no API registered with that identifier |

## Diagnosing an authorization-request failure

The redirect leg can be probed without a browser or a user password, which
separates *our* configuration from the provider's. Three requests to `/authorize`
distinguish the cases above:

```bash
HOST=https://<tenant>; CID=<client id>; RU=<app-origin>/signin/callback
CH=$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=')

probe () {   # $1 = audience
  curl -s -o /dev/null -D - -G "$HOST/authorize" \
    --data-urlencode "client_id=$CID" --data-urlencode "response_type=code" \
    --data-urlencode "scope=openid profile email" --data-urlencode "redirect_uri=$RU" \
    --data-urlencode "state=probe1234567890" --data-urlencode "code_challenge=$CH" \
    --data-urlencode "code_challenge_method=S256" --data-urlencode "audience=$1" \
  | grep -i '^location:'
}

probe "<your API identifier>"      # the real one
probe "https://not-registered/xyz" # control
probe ""                           # no audience at all
```

Read it as:

- **No-audience request serves a login page** → the application itself is correct:
  client id, redirect URI, PKCE and the SPA registration all check out. Any
  failure is then about the API, not the app.
- **Control returns `Service not found`** while the real one returns
  `not authorized to access resource server` → the API *does* exist; the missing
  piece is the application→API authorization in Auth0.
- **Both return `Service not found`** → the API identifier is wrong or the API
  was never created. Note the identifier is an opaque string, not a URL that has
  to resolve — but it must match `OIDC_AUDIENCE` byte-for-byte.

A redirect back to `/signin/callback` carrying `error=` (rather than an Auth0
error page) is itself a positive signal: it means the redirect URI is registered.

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

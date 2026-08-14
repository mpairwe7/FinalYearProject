# Staff-auth OIDC provider (Keycloak)

The staff dashboards (`/admin`, `/agent`) authenticate against an OIDC provider —
the backend **verifies** tokens but never issues them, so a real identity
provider is required (there is no password form to fall back on except the
opt-in dev-token panel, which is not authentication).

`ura-realm.json` is a reference realm, verified end-to-end against **Keycloak 26**
with OAuth 2.1 authorization-code + PKCE S256 and RS256 tokens. It defines the
public client, the three staff roles, the audience mapper, and two demo officers.

## What the app needs from the provider

| Concern | Requirement | Fails as |
| --- | --- | --- |
| Client type | Public client, PKCE `S256` (no secret in the browser bundle) | redirect refused |
| Redirect URI | `<app-origin>/signin/callback`, exactly — `/signup` returns through the same one | `invalid redirect_uri` |
| Audience | Access token `aud` must include `ura-chatbot` (audience mapper) | backend 401 `audience mismatch` |
| Roles | `ura_admin` / `ura_staff` / `ura_auditor` in `realm_access.roles` | officer degraded to `public`, dashboard refuses |
| Registration | *User registration* on (Realm settings → Login) for `/signup` to be useful | "Sign up" lands on the login screen with no way to register |

Any provider that meets these works — the backend probes Keycloak
(`realm_access.roles`), Entra/Okta (`roles`, `groups`) and a flat `role` by
default, and `OIDC_ROLE_CLAIM` overrides the path for anything else.

## Run it

```bash
# 1. Set real passwords in ura-realm.json (replace CHANGE_ME_*) and put your
#    deployment origin in redirectUris / webOrigins (replace YOUR-SPACE.hf.space).

# 2. Boot Keycloak with the realm imported.
docker run -d --name ura-kc -p 8180:8080 \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=admin \
  -e KC_BOOTSTRAP_ADMIN_PASSWORD='<admin-pass>' \
  -v "$PWD/ura-realm.json:/opt/keycloak/data/import/ura-realm.json:ro" \
  quay.io/keycloak/keycloak:26.0 start-dev --import-realm --http-port=8080
```

Behind TLS (any real deploy), Keycloak must emit its public issuer, so also set
`KC_HOSTNAME=https://<idp-host>` and `KC_PROXY_HEADERS=xforwarded`. The issuer in
the discovery document must match `OIDC_ISSUER` **byte-for-byte** on the backend —
do not mix `localhost` and `127.0.0.1`.

## Wire the app to it

**Frontend — build-time** (inlined into the image; see `App/Dockerfile.cranecloud`
build args, set from repo variables in `ura-chatbot-build-push.yml`):

```
NEXT_PUBLIC_OIDC_ISSUER=https://<idp-host>/realms/ura
NEXT_PUBLIC_OIDC_CLIENT_ID=ura-chatbot
```

**Backend — runtime** (Space/Crane Cloud secrets):

```
AUTH_ALG=RS256
OIDC_ISSUER=https://<idp-host>/realms/ura
OIDC_AUDIENCE=ura-chatbot
OIDC_JWKS_URL=https://<idp-host>/realms/ura/protocol/openid-connect/certs
```

The provider origin is added to the frontend's `connect-src` CSP automatically,
derived from `NEXT_PUBLIC_OIDC_ISSUER` at server start (`next.config.mjs`) — the
sign-in callback exchanges its code with the provider directly, and without that
origin the exchange is blocked with an opaque `NetworkError`.

`/signup` sends `prompt=create` and `screen_hint=signup` on the same request, so
Keycloak opens its registration form when *User registration* is enabled and
falls back to the login form when it is not. Nothing else differs between the two
entry points.

See `docs/PROJECT_SETUP.md` → *Sign-In and Sign-Up (OIDC)* for the full
walkthrough and the dev-token fallback.

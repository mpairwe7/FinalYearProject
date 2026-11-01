"""Authentication for the link between the chat backend and an MCP server.

A server reachable on its own address cannot rely on the caller having
authorized itself.  Until this module existed the connector servers
accepted any caller and then read the caller's *role* out of the request
body, so anyone who could reach the port could claim to be an
administrator.

Two things follow from that, and both live here:

1. **The server authenticates every HTTP request**, as an OAuth 2.1
   resource server when an issuer is configured (signature, expiry,
   issuer and — the one that matters most — *audience*: a token minted
   for another service is refused, which is what stops a stolen token
   being replayed across servers), or against a deployment-managed
   shared token for an internal prototype link.  With neither it is
   *locked*, not open.
2. **Identity in the body is only as trustworthy as the caller.**  The
   ``ug.go.ura.chatbot/*`` fields are assertions by the chat backend
   about the taxpayer it is acting for.  They are honoured only on an
   authenticated request; an anonymous caller is always ``public``.

The client half obtains tokens with the OAuth client-credentials grant,
which is how MCP 2026-07-28 expects a service with no user at the
keyboard to authenticate (``io.modelcontextprotocol/oauth-client-credentials``).
The token endpoint is configured, never discovered from a server
response: following a URL that the server chose is a request-forgery
vector this link does not need.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: A shared token shorter than this is treated as absent.  It is the only
#: thing standing between the port and the tools, so it has to be a
#: generated secret (``openssl rand -hex 32``), not a memorable word.
MIN_SHARED_TOKEN_CHARS = 32

_SERVER_TOKEN_PREFIX = "MCP_SERVER_TOKEN_"  # noqa: S105 - an env-var name, not a secret
_TRUTHY = {"1", "true", "yes", "on"}

#: The extension a client declares, and a server advertises, when the
#: client-credentials grant is in use on the link.
OAUTH_CLIENT_CREDENTIALS_EXTENSION = "io.modelcontextprotocol/oauth-client-credentials"


def _split(value: str) -> tuple[str, ...]:
    return tuple(part for part in value.replace(",", " ").split() if part)


def _scoped(env: Mapping[str, str], name: str, namespace: str) -> str:
    """``NAME_<NAMESPACE>`` wins over ``NAME`` so one process can host several servers."""
    if namespace:
        specific = env.get(f"{name}_{namespace.upper()}", "").strip()
        if specific:
            return specific
    return env.get(name, "").strip()


def _is_production(env: Mapping[str, str]) -> bool:
    return env.get("APP_ENV", "development").lower() == "production"


def protected_resource_metadata_url(resource: str) -> str:
    """Where RFC 9728 says the metadata for *resource* lives.

    The well-known segment is inserted between the host and the path, so
    a server at ``https://host/efris`` publishes at
    ``https://host/.well-known/oauth-protected-resource/efris``.
    """
    parts = urllib.parse.urlsplit(resource)
    path = parts.path.rstrip("/")
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, f"/.well-known/oauth-protected-resource{path}", "", "")
    )


@dataclass(frozen=True)
class AuthOutcome:
    """What the server learned from one request's credentials."""

    ok: bool
    #: False for an anonymous development request: the call may proceed,
    #: but nothing the body says about the caller is believed.
    authenticated: bool = False
    status: int = 200
    error: str = ""
    description: str = ""
    subject: str = ""
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ServerAuth:
    """How one MCP server authenticates its callers.

    ``mode`` is derived from configuration, never set directly:

    ``oauth``      issuer + JWKS configured; bearer JWTs, audience-bound.
    ``token``      a deployment-managed shared token.
    ``anonymous``  explicit opt-in, refused in production.
    ``locked``     nothing usable is configured; every request is refused.
    """

    mode: str = "locked"
    namespace: str = ""
    resource: str = ""
    token_digest: bytes = b""
    issuer: str = ""
    jwks_url: str = ""
    authorization_servers: tuple[str, ...] = ()
    required_scopes: tuple[str, ...] = ()
    reason: str = ""

    @classmethod
    def from_env(cls, namespace: str = "", env: Mapping[str, str] | None = None) -> ServerAuth:
        """Read the server's authentication from its environment.

        The shared token uses the *same* variable the client reads,
        ``MCP_SERVER_TOKEN_<NAMESPACE>``, so one secret definition wires
        both ends of a link and the two cannot be given different names.
        """
        source = os.environ if env is None else env
        resource = _scoped(source, "MCP_SERVER_RESOURCE", namespace).rstrip("/")
        issuer = _scoped(source, "MCP_SERVER_OAUTH_ISSUER", namespace)
        jwks_url = _scoped(source, "MCP_SERVER_OAUTH_JWKS_URL", namespace)
        scopes = _split(_scoped(source, "MCP_SERVER_OAUTH_SCOPES", namespace))
        servers = _split(_scoped(source, "MCP_SERVER_OAUTH_AUTHORIZATION_SERVERS", namespace))
        token = (
            source.get(f"{_SERVER_TOKEN_PREFIX}{namespace.upper()}", "").strip()
            if namespace
            else ""
        )

        if issuer or jwks_url:
            if not (issuer and jwks_url and resource):
                return cls(
                    namespace=namespace,
                    reason=(
                        "OAuth needs MCP_SERVER_OAUTH_ISSUER, MCP_SERVER_OAUTH_JWKS_URL "
                        "and MCP_SERVER_RESOURCE together"
                    ),
                )
            return cls(
                mode="oauth",
                namespace=namespace,
                resource=resource,
                issuer=issuer,
                jwks_url=jwks_url,
                authorization_servers=servers or (issuer,),
                required_scopes=scopes,
            )
        if token:
            if len(token) < MIN_SHARED_TOKEN_CHARS:
                return cls(
                    namespace=namespace,
                    reason=f"the shared token is shorter than {MIN_SHARED_TOKEN_CHARS} characters",
                )
            return cls(
                mode="token",
                namespace=namespace,
                resource=resource,
                token_digest=hashlib.sha256(token.encode("utf-8")).digest(),
            )
        if source.get("MCP_SERVER_ALLOW_ANONYMOUS", "").strip().lower() in _TRUTHY:
            if _is_production(source):
                return cls(
                    namespace=namespace, reason="anonymous access is never allowed in production"
                )
            return cls(mode="anonymous", namespace=namespace, resource=resource)
        return cls(namespace=namespace, reason="no credentials are configured for this server")

    # -- verification --------------------------------------------------
    def verify(self, authorization: str | None) -> AuthOutcome:
        """Authenticate one request from its ``Authorization`` header."""
        if self.mode == "anonymous":
            return AuthOutcome(ok=True)
        if self.mode == "locked":
            return AuthOutcome(
                ok=False,
                status=401,
                error="invalid_token",
                description="This server has no credentials configured and accepts no requests.",
            )

        scheme, _, credential = (authorization or "").strip().partition(" ")
        credential = credential.strip()
        if scheme.lower() != "bearer" or not credential:
            # No error code: RFC 6750 §3.1 reserves them for a request
            # that actually carried a token.
            return AuthOutcome(ok=False, status=401)

        if self.mode == "token":
            presented = hashlib.sha256(credential.encode("utf-8")).digest()
            if hmac.compare_digest(presented, self.token_digest):
                return AuthOutcome(ok=True, authenticated=True, subject="shared-token")
            return AuthOutcome(
                ok=False,
                status=401,
                error="invalid_token",
                description="The access token is not valid.",
            )
        return self._verify_jwt(credential)

    def _verify_jwt(self, token: str) -> AuthOutcome:
        from ..auth.jwt_auth import JWTAuthError, JWTVerifier

        def rejected(description: str) -> AuthOutcome:
            return AuthOutcome(ok=False, status=401, error="invalid_token", description=description)

        try:
            # ``audience=""`` skips the user-login verifier's own audience
            # check, which also accepts this API's audiences.  Accepting
            # those here would be exactly the token passthrough the
            # specification forbids, so the audience is checked below.
            claims = JWTVerifier(
                alg="RS256",
                issuer=self.issuer,
                audience="",
                jwks_url=self.jwks_url,
            ).verify(token)
        except JWTAuthError as exc:
            logger.info("MCP caller rejected namespace=%s (%s)", self.namespace or "-", exc)
            return rejected("The access token is not valid.")

        if not claims.get("exp"):
            return rejected("The access token has no expiry.")
        audiences = claims.get("aud", [])
        if isinstance(audiences, str):
            audiences = [audiences]
        if not isinstance(audiences, list) or self.resource not in {
            str(audience).rstrip("/") for audience in audiences
        }:
            logger.info("MCP caller rejected namespace=%s (audience)", self.namespace or "-")
            return rejected("The access token was not issued for this server.")

        granted = claims.get("scope", claims.get("scp", ""))
        scopes = tuple(granted) if isinstance(granted, list) else _split(str(granted or ""))
        missing = [scope for scope in self.required_scopes if scope not in scopes]
        if missing:
            return AuthOutcome(
                ok=False,
                status=403,
                error="insufficient_scope",
                description="The access token does not carry the scope this server requires.",
                scopes=scopes,
            )
        return AuthOutcome(
            ok=True,
            authenticated=True,
            subject=str(claims.get("azp") or claims.get("client_id") or claims.get("sub") or ""),
            scopes=scopes,
        )

    # -- what the server tells a caller that failed --------------------
    def challenge(self, outcome: AuthOutcome) -> str:
        """The ``WWW-Authenticate`` value for a refused request (RFC 6750 §3)."""
        params: list[str] = []
        if self.mode == "oauth":
            params.append(f'resource_metadata="{protected_resource_metadata_url(self.resource)}"')
            if self.required_scopes:
                params.append(f'scope="{" ".join(self.required_scopes)}"')
        else:
            params.append('realm="mcp"')
        if outcome.error:
            params.append(f'error="{outcome.error}"')
            if outcome.description:
                params.append(f'error_description="{outcome.description}"')
        return "Bearer " + ", ".join(params)

    def protected_resource_metadata(self) -> dict[str, Any] | None:
        """The RFC 9728 document, or ``None`` when this server is not using OAuth."""
        if self.mode != "oauth":
            return None
        document: dict[str, Any] = {
            "resource": self.resource,
            "authorization_servers": list(self.authorization_servers),
            "bearer_methods_supported": ["header"],
        }
        if self.required_scopes:
            document["scopes_supported"] = list(self.required_scopes)
        return document


# ---------------------------------------------------------------------------
# Client side
# ---------------------------------------------------------------------------
class TokenError(RuntimeError):
    """The client could not obtain an access token.  Never carries a secret."""


#: ``(url, form, headers, timeout_s) -> (status, json_body)``; injectable so
#: the grant can be tested without a socket.
TokenPost = Callable[[str, dict[str, str], dict[str, str], float], tuple[int, Any]]


def _httpx_token_post(
    url: str, form: dict[str, str], headers: dict[str, str], timeout_s: float
) -> tuple[int, Any]:
    import httpx

    # Redirects are not followed: a redirected token request would carry
    # the client secret to wherever the response pointed.
    response = httpx.post(
        url, data=form, headers=headers, timeout=timeout_s, follow_redirects=False
    )
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, None


class ClientCredentialsToken:
    """An access token for one MCP server, from the client-credentials grant.

    The ``resource`` parameter (RFC 8707) is sent on every request so the
    token is bound to the one server it is for.  Tokens are cached until
    shortly before they expire; the grant has no refresh token, so
    "refresh" is simply asking again.
    """

    #: Ask for a new token this long before the old one expires, so a
    #: request already in flight does not arrive with a dead token.
    EXPIRY_SKEW_S = 30.0

    def __init__(  # noqa: PLR0913 - the grant's parameters
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        resource: str,
        scope: str = "",
        timeout_s: float = 5.0,
        post: TokenPost | None = None,
    ) -> None:
        self.token_url = token_url
        self.resource = resource.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._timeout_s = timeout_s
        self._post = post or _httpx_token_post
        self._lock = threading.Lock()
        self._token = ""
        self._expires_at = 0.0

    def _basic_credentials(self) -> str:
        # RFC 6749 §2.3.1: both parts are form-urlencoded before Basic.
        pair = ":".join(
            urllib.parse.quote(part, safe="") for part in (self._client_id, self._client_secret)
        )
        return base64.b64encode(pair.encode("utf-8")).decode("ascii")

    def invalidate(self) -> None:
        """Forget the cached token, e.g. after the server refused it."""
        with self._lock:
            self._token = ""
            self._expires_at = 0.0

    def get(self) -> str:
        with self._lock:
            if self._token and time.monotonic() < self._expires_at:
                return self._token
            form = {"grant_type": "client_credentials", "resource": self.resource}
            if self._scope:
                form["scope"] = self._scope
            headers = {
                "Accept": "application/json",
                "Authorization": f"Basic {self._basic_credentials()}",
            }
            try:
                status, body = self._post(self.token_url, form, headers, self._timeout_s)
            except Exception as exc:  # noqa: BLE001 - reported without the request
                raise TokenError(f"token request failed: {type(exc).__name__}") from exc
            if status != 200 or not isinstance(body, dict):
                code = body.get("error") if isinstance(body, dict) else None
                detail = f" ({code})" if isinstance(code, str) and code else ""
                raise TokenError(f"token endpoint returned HTTP {status}{detail}")
            token = body.get("access_token")
            if not isinstance(token, str) or not token:
                raise TokenError("token endpoint returned no access token")
            if str(body.get("token_type", "")).lower() != "bearer":
                raise TokenError("token endpoint returned a token type this client cannot use")
            try:
                lifetime_s = float(body.get("expires_in", 300))
            except (TypeError, ValueError):
                lifetime_s = 300.0
            self._token = token
            self._expires_at = time.monotonic() + max(5.0, lifetime_s - self.EXPIRY_SKEW_S)
            return token


def client_credentials_from_env(
    namespace: str, resource: str, env: Mapping[str, str] | None = None
) -> ClientCredentialsToken | None:
    """Build the grant for *namespace*, or ``None`` when it is not configured."""
    source = os.environ if env is None else env
    suffix = namespace.upper()
    token_url = source.get(f"MCP_OAUTH_TOKEN_URL_{suffix}", "").strip()
    client_id = source.get(f"MCP_OAUTH_CLIENT_ID_{suffix}", "").strip()
    client_secret = source.get(f"MCP_OAUTH_CLIENT_SECRET_{suffix}", "").strip()
    if not (token_url or client_id or client_secret):
        return None
    if not (token_url and client_id and client_secret):
        logger.error(
            "MCP namespace %s: OAuth needs MCP_OAUTH_TOKEN_URL_, MCP_OAUTH_CLIENT_ID_ "
            "and MCP_OAUTH_CLIENT_SECRET_ together; the link stays unauthenticated",
            namespace,
        )
        return None
    scheme = urllib.parse.urlsplit(token_url).scheme
    if scheme != "https" and (_is_production(source) or scheme != "http"):
        logger.error("MCP namespace %s: the token endpoint must use https", namespace)
        return None
    return ClientCredentialsToken(
        token_url=token_url,
        client_id=client_id,
        client_secret=client_secret,
        resource=resource,
        scope=source.get(f"MCP_OAUTH_SCOPE_{suffix}", "").strip(),
    )


def mint_obo_actor_assertion(
    *,
    subject: str,
    tenant_id: str,
    actor_agent: str,
    resource: str,
    scopes: tuple[str, ...] = (),
    secret: str = "",
    ttl_s: int = 60,
    now: float | None = None,
) -> str:
    """Mint a short-lived RFC 8693 OBO + RFC 8707 audience-bound assertion token (MO-4 / OWASP ASI03).

    Produces a compact ``<base64url-payload>.<base64url-hmac-sha256>`` token that
    binds ``sub`` (taxpayer/user), ``tid`` (tenant), ``act`` (delegating agent role),
    ``aud`` (canonical MCP server resource URI), and ``scope`` (narrowed tool scope).
    """
    import json

    signing_key = (secret or os.getenv("MCP_OBO_SIGNING_SECRET") or "").encode("utf-8")
    if len(signing_key) < MIN_SHARED_TOKEN_CHARS:
        return ""
    ts = int(time.time() if now is None else now)
    actor_id = actor_agent if str(actor_agent).startswith("agent:") else f"agent:{actor_agent or 'orchestrator'}"
    payload = {
        "sub": str(subject or "public"),
        "tid": str(tenant_id or "default"),
        "act": {"sub": actor_id},
        "aud": str(resource or "").rstrip("/"),
        "scope": " ".join(s for s in scopes if s),
        "iat": ts,
        "exp": ts + max(5, int(ttl_s)),
    }
    raw_json = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(raw_json).rstrip(b"=").decode("ascii")
    sig = hmac.new(signing_key, b64_payload.encode("ascii"), hashlib.sha256).digest()
    b64_sig = base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")
    return f"{b64_payload}.{b64_sig}"


def verify_obo_actor_assertion(
    assertion: str,
    *,
    expected_resource: str,
    secret: str,
    required_scope: str = "",
    now: float | None = None,
) -> dict[str, Any] | None:
    """Verify an RFC 8693 OBO + RFC 8707 actor assertion token; returns claims or ``None``."""
    import json

    signing_key = (secret or "").encode("utf-8")
    if not assertion or "." not in assertion or len(signing_key) < MIN_SHARED_TOKEN_CHARS:
        return None
    b64_payload, _, b64_sig = assertion.partition(".")
    if not b64_payload or not b64_sig:
        return None
    expected_sig = hmac.new(signing_key, b64_payload.encode("ascii"), hashlib.sha256).digest()
    expected_b64 = base64.urlsafe_b64encode(expected_sig).rstrip(b"=").decode("ascii")
    if not hmac.compare_digest(b64_sig, expected_b64):
        return None
    try:
        pad = "=" * (-len(b64_payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(b64_payload + pad).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(claims, dict):
        return None
    ts = int(time.time() if now is None else now)
    exp = claims.get("exp")
    if not isinstance(exp, int) or ts >= exp:
        return None
    if str(claims.get("aud") or "").rstrip("/") != str(expected_resource or "").rstrip("/"):
        return None
    if required_scope:
        granted = set(str(claims.get("scope") or "").split())
        if required_scope not in granted:
            return None
    return claims

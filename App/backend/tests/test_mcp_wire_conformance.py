"""MCP 2026-07-28 conformance and caller authentication, server side.

Every server in ``app.mcp.servers`` is built on ``servers/wire.py``, so
these run the rules once against a purpose-built server and then check
that each real server is in fact wired to them.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import unittest
from typing import Any
from unittest.mock import patch

from app.auth.jwt_auth import JWTVerifier, _b64url_encode
from app.mcp.auth import (
    MIN_SHARED_TOKEN_CHARS,
    AuthOutcome,
    ServerAuth,
    protected_resource_metadata_url,
)
from app.mcp.protocol import MCP_PROTOCOL_VERSION, request_meta
from app.mcp.servers import wire
from app.mcp.servers.bwims.server import handle_request as handle_bwims
from app.mcp.servers.dts.server import handle_request as handle_dts
from app.mcp.servers.efris.server import handle_request as handle_efris
from app.mcp.servers.payment.server import handle_request as handle_payment
from app.mcp.servers.tax_calculator.server import handle_request as handle_calc
from app.mcp.servers.tin.server import handle_request as handle_tin
from app.mcp.servers.ura_account.server import handle_request as handle_account
from app.mcp.servers.ursb.server import handle_request as handle_ursb
from app.tools import Tool, ToolRegistry, ToolSchema
from starlette.testclient import TestClient

TOKEN = "t" * MIN_SHARED_TOKEN_CHARS
RESOURCE = "https://mcp.example/wire_test"
ISSUER = "https://idp.example/realms/ura"


class _OpenTool(Tool):
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="wire_open_read",
            description="A read anyone may call.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            namespace="wire_test",
        )

    def execute(self, **_: Any) -> dict[str, Any]:
        return {"ok": True, "explanation": "open"}


class _StaffTool(Tool):
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="wire_staff_read",
            description="A read only URA staff may call.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            namespace="wire_test",
            allowed_roles=("ura_staff",),
        )

    def execute(self, **_: Any) -> dict[str, Any]:
        return {"ok": True, "explanation": "staff"}


def _server() -> wire.StatelessMCPServer:
    return wire.StatelessMCPServer(
        "mcp_wire_test",
        "wire_test",
        instructions="A server that exists to be tested.",
        resources=lambda: [{"uri": "wire://one", "name": "One"}],
        read_resource=lambda uri: {"contents": [{"uri": uri, "text": "1"}]}
        if uri == "wire://one"
        else {},
    )


def _body(
    method: str, params: dict[str, Any] | None = None, *, meta: dict[str, Any] | None = None
) -> dict[str, Any]:
    merged = dict(params or {})
    merged["_meta"] = (
        meta if meta is not None else request_meta(user_id="u-1", user_role="ura_staff")
    )
    return {"jsonrpc": "2.0", "id": "r-1", "method": method, "params": merged}


def _headers(method: str, name: str = "", token: str = TOKEN) -> dict[str, str]:
    headers = {"MCP-Protocol-Version": MCP_PROTOCOL_VERSION, "Mcp-Method": method}
    if name:
        headers["Mcp-Name"] = name
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _token_auth() -> ServerAuth:
    return ServerAuth(
        mode="token", namespace="wire_test", token_digest=hashlib.sha256(TOKEN.encode()).digest()
    )


class _RegisteredTools(unittest.TestCase):
    def setUp(self) -> None:
        for tool in (_OpenTool(), _StaffTool()):
            ToolRegistry.register(tool)
        self.addCleanup(ToolRegistry.unregister, "wire_open_read")
        self.addCleanup(ToolRegistry.unregister, "wire_staff_read")
        self.server = _server()


class DiscoverTests(_RegisteredTools):
    def test_discover_states_versions_capabilities_and_identity(self) -> None:
        result = self.server.handle_request(_body("server/discover"))["result"]
        self.assertEqual(result["supportedVersions"], [MCP_PROTOCOL_VERSION])
        self.assertIn("tools", result["capabilities"])
        self.assertIn("resources", result["capabilities"])
        self.assertNotIn(
            "prompts", result["capabilities"], "a capability is only declared if it is served"
        )
        self.assertEqual(result["instructions"], "A server that exists to be tested.")
        self.assertEqual(
            result["_meta"][wire.META_SERVER_INFO], {"name": "mcp_wire_test", "version": "2.0.0"}
        )
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual((result["ttlMs"], result["cacheScope"]), (wire.LIST_TTL_MS, "public"))

    def test_every_real_server_implements_discover(self) -> None:
        handlers = {
            "mcp_tax_calculator": handle_calc,
            "mcp_ura_account": handle_account,
            "mcp_efris": handle_efris,
            "mcp_dts": handle_dts,
            "mcp_payment": handle_payment,
            "mcp_tin": handle_tin,
            "mcp_ursb": handle_ursb,
            "mcp_bwims": handle_bwims,
        }
        for name, handler in handlers.items():
            with self.subTest(server=name):
                result = handler(_body("server/discover"))["result"]
                self.assertEqual(result["supportedVersions"], [MCP_PROTOCOL_VERSION])
                self.assertEqual(result["_meta"][wire.META_SERVER_INFO]["name"], name)
                listed = handler(_body("tools/list"))["result"]
                self.assertEqual(listed["cacheScope"], "public")
                self.assertEqual(listed["resultType"], "complete")
                self.assertEqual(
                    [tool["name"] for tool in listed["tools"]],
                    sorted(tool["name"] for tool in listed["tools"]),
                    "tools/list must be in a stable order so clients can cache it",
                )


class ResultShapeTests(_RegisteredTools):
    def test_a_tool_result_is_complete_and_names_its_server(self) -> None:
        result = self.server.handle_request(
            _body("tools/call", {"name": "wire_open_read", "arguments": {}})
        )["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["explanation"], "open")
        self.assertEqual(result["_meta"][wire.META_SERVER_INFO]["name"], "mcp_wire_test")

    def test_a_resource_read_is_marked_private_and_uncacheable(self) -> None:
        result = self.server.handle_request(_body("resources/read", {"uri": "wire://one"}))[
            "result"
        ]
        self.assertEqual(result["contents"][0]["text"], "1")
        self.assertEqual((result["ttlMs"], result["cacheScope"]), (0, "private"))

    def test_a_connector_server_reads_only_its_own_resources(self) -> None:
        own = handle_efris(_body("resources/read", {"uri": "efris://taxpayers/profile"}))
        self.assertIn("result", own)
        # mcp_payment used to answer this: any connector could read any other's.
        foreign = handle_payment(_body("resources/read", {"uri": "efris://taxpayers/profile"}))
        self.assertEqual(foreign["error"]["code"], wire.INVALID_PARAMS)


class VersionTests(_RegisteredTools):
    def test_an_unsupported_version_lists_the_supported_ones(self) -> None:
        meta = {**request_meta(), "io.modelcontextprotocol/protocolVersion": "1900-01-01"}
        response = self.server.handle_request(_body("tools/list", meta=meta))
        self.assertEqual(response["error"]["code"], wire.UNSUPPORTED_PROTOCOL_VERSION)
        self.assertEqual(
            response["error"]["data"],
            {"supported": [MCP_PROTOCOL_VERSION], "requested": "1900-01-01"},
        )
        self.assertEqual(wire.http_status_for(response), 400)

    def test_initialize_is_refused_naming_the_versions_it_could_use(self) -> None:
        error = self.server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})[
            "error"
        ]
        self.assertEqual(error["code"], wire.METHOD_NOT_FOUND)
        self.assertIn(MCP_PROTOCOL_VERSION, error["message"])

    def test_an_unknown_method_is_reported_before_missing_metadata(self) -> None:
        response = self.server.handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/teleport"}
        )
        self.assertEqual(response["error"]["code"], wire.METHOD_NOT_FOUND)
        self.assertEqual(wire.http_status_for(response), 404)


class HeaderTests(_RegisteredTools):
    def test_strict_mode_requires_the_routing_headers(self) -> None:
        body = _body("tools/call", {"name": "wire_open_read", "arguments": {}})
        for missing in ("MCP-Protocol-Version", "Mcp-Method", "Mcp-Name"):
            headers = _headers("tools/call", "wire_open_read")
            del headers[missing]
            with self.subTest(missing=missing):
                error = self.server.handle_request(body, headers, strict_headers=True)["error"]
                self.assertEqual(error["code"], wire.HEADER_MISMATCH)
                self.assertIn(missing, error["message"])

    def test_header_version_must_match_the_body(self) -> None:
        headers = {**_headers("tools/list"), "MCP-Protocol-Version": "2025-11-25"}
        error = self.server.handle_request(_body("tools/list"), headers, strict_headers=True)[
            "error"
        ]
        self.assertEqual(error["code"], wire.HEADER_MISMATCH)

    def test_a_base64_encoded_name_is_decoded_before_comparison(self) -> None:
        encoded = "=?base64?" + base64.b64encode(b"wire://one").decode() + "?="
        response = self.server.handle_request(
            _body("resources/read", {"uri": "wire://one"}),
            _headers("resources/read", encoded),
            strict_headers=True,
        )
        self.assertIn("result", response)

    def test_a_malformed_encoded_name_is_a_header_mismatch(self) -> None:
        error = self.server.handle_request(
            _body("resources/read", {"uri": "wire://one"}),
            _headers("resources/read", "=?base64?%%%?="),
            strict_headers=True,
        )["error"]
        self.assertEqual(error["code"], wire.HEADER_MISMATCH)


class IdentityTests(_RegisteredTools):
    """A role in the body is a claim; it counts only from an authenticated caller."""

    def _call(self, *, trusted: bool) -> dict[str, Any]:
        body = _body("tools/call", {"name": "wire_staff_read", "arguments": {}})
        return self.server.handle_request(body, trusted_identity=trusted)["result"]

    def test_an_authenticated_caller_is_believed(self) -> None:
        self.assertFalse(self._call(trusted=True)["isError"])

    def test_an_unauthenticated_caller_is_public_whatever_it_claims(self) -> None:
        result = self._call(trusted=False)
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"], "policy_denied")
        self.assertEqual(result["structuredContent"]["policy"]["user_role"], "public")


class HttpTests(_RegisteredTools):
    def setUp(self) -> None:
        super().setUp()
        self.http = TestClient(
            self.server.create_app(_token_auth(), allowed_origins=("https://console.ura.example",))
        )

    def _post(self, body: Any, headers: dict[str, str]) -> Any:
        return self.http.post(
            "/", content=json.dumps(body), headers={"Content-Type": "application/json", **headers}
        )

    def test_a_request_without_credentials_is_challenged(self) -> None:
        response = self._post(_body("tools/list"), _headers("tools/list", token=""))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["www-authenticate"], 'Bearer realm="mcp"')

    def test_a_wrong_token_is_refused_without_being_echoed(self) -> None:
        response = self._post(
            _body("tools/list"), _headers("tools/list", token="w" * MIN_SHARED_TOKEN_CHARS)
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn('error="invalid_token"', response.headers["www-authenticate"])
        self.assertNotIn(
            "w" * MIN_SHARED_TOKEN_CHARS, response.text + response.headers["www-authenticate"]
        )

    def test_an_authenticated_call_runs_with_the_identity_it_asserts(self) -> None:
        response = self._post(
            _body("tools/call", {"name": "wire_staff_read", "arguments": {}}),
            _headers("tools/call", "wire_staff_read"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["result"]["isError"])

    def test_status_codes_separate_no_such_method_from_no_such_tool(self) -> None:
        unknown_method = self._post(_body("tools/teleport"), _headers("tools/teleport"))
        self.assertEqual(unknown_method.status_code, 404)
        self.assertEqual(unknown_method.json()["error"]["code"], wire.METHOD_NOT_FOUND)

        unknown_tool = self._post(
            _body("tools/call", {"name": "nope", "arguments": {}}), _headers("tools/call", "nope")
        )
        self.assertEqual(unknown_tool.status_code, 200)
        self.assertEqual(unknown_tool.json()["error"]["code"], wire.INVALID_PARAMS)

    def test_a_header_that_disagrees_with_the_body_is_a_400(self) -> None:
        response = self._post(
            _body("tools/call", {"name": "wire_open_read", "arguments": {}}),
            _headers("tools/call", "wire_staff_read"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], wire.HEADER_MISMATCH)

    def test_the_routing_headers_are_mandatory_over_http(self) -> None:
        # Direct and stdio callers may omit them; a request that crossed a
        # network may have passed a gateway that routed on them.
        for missing in ("MCP-Protocol-Version", "Mcp-Method"):
            headers = _headers("tools/list")
            del headers[missing]
            with self.subTest(missing=missing):
                response = self._post(_body("tools/list"), headers)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], wire.HEADER_MISMATCH)

    def test_only_post_is_served_on_the_endpoint(self) -> None:
        for method in ("get", "delete"):
            with self.subTest(method=method):
                response = getattr(self.http, method)("/")
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response.headers["allow"], "POST")

    def test_batches_bad_json_and_oversized_bodies_are_refused(self) -> None:
        headers = _headers("tools/list")
        self.assertEqual(self._post([_body("tools/list")], headers).status_code, 400)
        bad = self.http.post("/", content="{not json", headers=headers)
        self.assertEqual((bad.status_code, bad.json()["error"]["code"]), (400, wire.PARSE_ERROR))
        huge = self.http.post("/", content="x" * (wire.MAX_BODY_BYTES + 1), headers=headers)
        self.assertEqual(huge.status_code, 413)

    def test_a_browser_origin_must_be_listed(self) -> None:
        body, headers = _body("tools/list"), _headers("tools/list")
        blocked = self._post(body, {**headers, "Origin": "https://evil.example"})
        self.assertEqual(blocked.status_code, 403)
        allowed = self._post(body, {**headers, "Origin": "https://console.ura.example"})
        self.assertEqual(allowed.status_code, 200)

    def test_health_is_open_and_says_how_callers_authenticate(self) -> None:
        health = self.http.get("/health").json()
        self.assertEqual((health["ok"], health["auth"], health["tools"]), (True, "token", 2))

    def test_token_mode_publishes_no_oauth_metadata(self) -> None:
        self.assertEqual(self.http.get("/.well-known/oauth-protected-resource").status_code, 404)

    def test_a_server_with_no_credentials_configured_is_locked_not_open(self) -> None:
        locked = TestClient(self.server.create_app(ServerAuth.from_env("wire_test", {})))
        response = locked.post("/", json=_body("tools/list"), headers=_headers("tools/list"))
        self.assertEqual(response.status_code, 401)

    def test_anonymous_development_access_ignores_claimed_roles(self) -> None:
        auth = ServerAuth.from_env("wire_test", {"MCP_SERVER_ALLOW_ANONYMOUS": "true"})
        self.assertEqual(auth.mode, "anonymous")
        anonymous = TestClient(self.server.create_app(auth))
        result = anonymous.post(
            "/",
            json=_body("tools/call", {"name": "wire_staff_read", "arguments": {}}),
            headers=_headers("tools/call", "wire_staff_read", token=""),
        ).json()["result"]
        self.assertEqual(result["structuredContent"]["error"], "policy_denied")


class ServerAuthConfigTests(unittest.TestCase):
    def test_the_shared_token_is_read_from_the_variable_the_client_uses(self) -> None:
        auth = ServerAuth.from_env("efris", {"MCP_SERVER_TOKEN_EFRIS": TOKEN})
        self.assertEqual(auth.mode, "token")
        self.assertTrue(auth.verify(f"Bearer {TOKEN}").authenticated)
        self.assertFalse(auth.verify(f"Basic {TOKEN}").ok)

    def test_a_short_token_locks_the_server(self) -> None:
        auth = ServerAuth.from_env("efris", {"MCP_SERVER_TOKEN_EFRIS": "password"})
        self.assertEqual(auth.mode, "locked")
        self.assertIn("shorter", auth.reason)

    def test_anonymous_access_is_refused_in_production(self) -> None:
        auth = ServerAuth.from_env(
            "efris", {"MCP_SERVER_ALLOW_ANONYMOUS": "true", "APP_ENV": "production"}
        )
        self.assertEqual(auth.mode, "locked")

    def test_partial_oauth_configuration_locks_rather_than_guessing(self) -> None:
        auth = ServerAuth.from_env("efris", {"MCP_SERVER_OAUTH_ISSUER": ISSUER})
        self.assertEqual(auth.mode, "locked")

    def test_a_namespaced_setting_wins_so_one_process_can_host_several_servers(self) -> None:
        env = {
            "MCP_SERVER_OAUTH_ISSUER": ISSUER,
            "MCP_SERVER_OAUTH_JWKS_URL": f"{ISSUER}/certs",
            "MCP_SERVER_RESOURCE": "https://mcp.example",
            "MCP_SERVER_RESOURCE_EFRIS": "https://mcp.example/efris/",
        }
        self.assertEqual(ServerAuth.from_env("efris", env).resource, "https://mcp.example/efris")
        self.assertEqual(ServerAuth.from_env("ursb", env).resource, "https://mcp.example")

    def test_metadata_lives_where_rfc_9728_puts_it(self) -> None:
        self.assertEqual(
            protected_resource_metadata_url("https://mcp.example/efris"),
            "https://mcp.example/.well-known/oauth-protected-resource/efris",
        )
        self.assertEqual(
            protected_resource_metadata_url("https://mcp.example"),
            "https://mcp.example/.well-known/oauth-protected-resource",
        )


class OAuthResourceServerTests(_RegisteredTools):
    """Bearer JWTs: signature, issuer, expiry and — above all — audience."""

    @classmethod
    def setUpClass(cls) -> None:
        from cryptography.hazmat.primitives.asymmetric import rsa

        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = cls.key.public_key().public_numbers()

        def b64(value: int) -> str:
            return _b64url_encode(value.to_bytes((value.bit_length() + 7) // 8, "big"))

        cls.jwks = {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": "k1",
                    "alg": "RS256",
                    "use": "sig",
                    "n": b64(numbers.n),
                    "e": b64(numbers.e),
                }
            ]
        }

    def setUp(self) -> None:
        super().setUp()
        JWTVerifier.reset_shared_jwks_cache()
        self.addCleanup(JWTVerifier.reset_shared_jwks_cache)
        patcher = patch("app.auth.jwt_auth._fetch_jwks", return_value=self.jwks)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.auth = ServerAuth.from_env(
            "wire_test",
            {
                "MCP_SERVER_OAUTH_ISSUER": ISSUER,
                "MCP_SERVER_OAUTH_JWKS_URL": f"{ISSUER}/protocol/openid-connect/certs",
                "MCP_SERVER_RESOURCE": RESOURCE,
                "MCP_SERVER_OAUTH_SCOPES": "connectors:read",
            },
        )

    def _jwt(self, **overrides: Any) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": RESOURCE,
            "sub": "ura-chatbot",
            "azp": "ura-chatbot",
            "iat": now,
            "exp": now + 300,
            "scope": "connectors:read",
        }
        claims.update(overrides)
        claims = {key: value for key, value in claims.items() if value is not None}
        signing_input = ".".join(
            _b64url_encode(json.dumps(part, separators=(",", ":")).encode())
            for part in ({"alg": "RS256", "typ": "JWT", "kid": "k1"}, claims)
        )
        signature = self.key.sign(
            signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256()
        )
        return f"{signing_input}.{_b64url_encode(signature)}"

    def test_a_token_issued_for_this_server_is_accepted(self) -> None:
        outcome = self.auth.verify(f"Bearer {self._jwt()}")
        self.assertEqual(
            (outcome.ok, outcome.authenticated, outcome.subject), (True, True, "ura-chatbot")
        )

    def test_a_token_for_another_audience_is_refused(self) -> None:
        for audience in (
            "https://mcp.example/other",
            "https://ura-chatbot/api",
            ["https://elsewhere.example"],
        ):
            with self.subTest(audience=audience):
                outcome = self.auth.verify(f"Bearer {self._jwt(aud=audience)}")
                self.assertEqual(
                    (outcome.ok, outcome.status, outcome.error), (False, 401, "invalid_token")
                )

    def test_expired_unexpiring_and_foreign_issuer_tokens_are_refused(self) -> None:
        cases = {
            "expired": {"exp": int(time.time()) - 5},
            "no expiry": {"exp": None},
            "other issuer": {"iss": "https://rogue.example"},
        }
        for label, overrides in cases.items():
            with self.subTest(case=label):
                self.assertFalse(self.auth.verify(f"Bearer {self._jwt(**overrides)}").ok)

    def test_a_tampered_token_is_refused(self) -> None:
        header, payload, signature = self._jwt().split(".")
        forged = _b64url_encode(
            json.dumps({"iss": ISSUER, "aud": RESOURCE, "exp": 9_999_999_999}).encode()
        )
        self.assertFalse(self.auth.verify(f"Bearer {header}.{forged}.{signature}").ok)
        self.assertTrue(self.auth.verify(f"Bearer {header}.{payload}.{signature}").ok)

    def test_a_token_without_the_required_scope_is_forbidden(self) -> None:
        outcome = self.auth.verify(f"Bearer {self._jwt(scope='profile')}")
        self.assertEqual((outcome.status, outcome.error), (403, "insufficient_scope"))
        challenge = self.auth.challenge(outcome)
        self.assertIn('error="insufficient_scope"', challenge)
        self.assertIn('scope="connectors:read"', challenge)

    def test_the_challenge_points_at_the_protected_resource_metadata(self) -> None:
        http = TestClient(self.server.create_app(self.auth))
        refused = http.post("/", json=_body("tools/list"), headers=_headers("tools/list", token=""))
        self.assertEqual(refused.status_code, 401)
        self.assertIn(
            'resource_metadata="https://mcp.example/.well-known/oauth-protected-resource/wire_test"',
            refused.headers["www-authenticate"],
        )
        document = http.get("/.well-known/oauth-protected-resource").json()
        self.assertEqual(document["resource"], RESOURCE)
        self.assertEqual(document["authorization_servers"], [ISSUER])
        self.assertEqual(document["bearer_methods_supported"], ["header"])

    def test_an_oauth_server_advertises_the_client_credentials_extension(self) -> None:
        http = TestClient(self.server.create_app(self.auth))
        response = http.post(
            "/",
            json=_body("server/discover"),
            headers=_headers("server/discover", token=self._jwt()),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "io.modelcontextprotocol/oauth-client-credentials",
            response.json()["result"]["capabilities"]["extensions"],
        )

    def test_a_refused_outcome_never_reaches_the_tools(self) -> None:
        self.assertEqual(self.auth.verify(None), AuthOutcome(ok=False, status=401))


if __name__ == "__main__":
    unittest.main()

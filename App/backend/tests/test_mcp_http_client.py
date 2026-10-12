"""The MCP client's Streamable HTTP transport against the 2026-07-28 rules."""

from __future__ import annotations

import base64
import json
import unittest
from typing import Any
from unittest.mock import patch

from app.mcp.auth import ClientCredentialsToken, TokenError, client_credentials_from_env
from app.mcp.mrtr import parse_input_required
from app.mcp.protocol import DEFAULT_CLIENT_CAPABILITIES, MCP_PROTOCOL_VERSION
from app.mcp.transport import (
    HttpTransport,
    InProcessTransport,
    TransportError,
    _remote_url_problem,
    build_transports,
    encode_header_value,
    header_parameters,
)

SECRET = "s3cr3t-value-that-must-never-appear"  # noqa: S105  # pragma: allowlist secret - a marker the tests search for


class _Response:
    def __init__(
        self,
        status: int = 200,
        payload: Any = None,
        *,
        text: str = "",
        content_type: str = "application/json",
    ) -> None:
        self.status_code = status
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")
        self.headers = {"content-type": content_type}

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


class _Recorder:
    """Stands in for ``httpx.Client``: answers from a script, records what was sent."""

    is_closed = False

    def __init__(self, *responders: Any) -> None:
        self._responders = list(responders)
        self.requests: list[dict[str, Any]] = []

    def post(
        self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> _Response:  # noqa: A002
        self.requests.append({"url": url, "body": json, "headers": headers, "timeout": timeout})
        responder = self._responders.pop(0) if len(self._responders) > 1 else self._responders[0]
        if isinstance(responder, Exception):
            raise responder
        return responder(json) if callable(responder) else responder


def _result(result: dict[str, Any]) -> Any:
    return lambda body: _Response(200, {"jsonrpc": "2.0", "id": body["id"], "result": result})


def _error(status: int, code: int, message: str, data: Any = None) -> Any:
    return lambda body: _Response(
        status,
        {
            "jsonrpc": "2.0",
            "id": body["id"],
            "error": {"code": code, "message": message, "data": data},
        },
    )


def _transport(*responders: Any, **kwargs: Any) -> tuple[HttpTransport, _Recorder]:
    recorder = _Recorder(*responders)
    return HttpTransport("efris", "https://mcp.example/efris/", client=recorder, **kwargs), recorder


class HeaderEncodingTests(unittest.TestCase):
    def test_the_specification_examples(self) -> None:
        self.assertEqual(encode_header_value("us-west1"), "us-west1")
        self.assertEqual(encode_header_value("Hello, 世界"), "=?base64?SGVsbG8sIOS4lueVjA==?=")
        self.assertEqual(encode_header_value(" padded "), "=?base64?IHBhZGRlZCA=?=")
        self.assertEqual(encode_header_value("line1\nline2"), "=?base64?bGluZTEKbGluZTI=?=")
        self.assertEqual(
            encode_header_value("=?base64?literal?="), "=?base64?PT9iYXNlNjQ/bGl0ZXJhbD89?="
        )

    def test_a_value_can_never_smuggle_a_second_header(self) -> None:
        encoded = encode_header_value("x\r\nAuthorization: Bearer stolen")
        self.assertNotIn("\r", encoded)
        self.assertNotIn("\n", encoded)
        self.assertEqual(
            base64.b64decode(encoded[len("=?base64?") : -2]).decode(),
            "x\r\nAuthorization: Bearer stolen",
        )


class HeaderParameterTests(unittest.TestCase):
    def test_annotated_properties_are_found_by_their_exact_path(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "region": {"type": "string", "x-mcp-header": "Region"},
                "options": {
                    "type": "object",
                    "properties": {"dry_run": {"type": "boolean", "x-mcp-header": "Dry-Run"}},
                },
                "query": {"type": "string"},
            },
        }
        self.assertEqual(
            header_parameters(schema), {"Region": ("region",), "Dry-Run": ("options", "dry_run")}
        )

    def test_annotations_the_specification_forbids_invalidate_the_tool(self) -> None:
        invalid = {
            "inside an array": {
                "properties": {
                    "rows": {"type": "array", "items": {"type": "string", "x-mcp-header": "Row"}}
                }
            },
            "inside a composition": {
                "oneOf": [{"properties": {"a": {"type": "string", "x-mcp-header": "A"}}}]
            },
            "on a number": {"properties": {"amount": {"type": "number", "x-mcp-header": "Amount"}}},
            "empty name": {"properties": {"a": {"type": "string", "x-mcp-header": ""}}},
            "not a header token": {
                "properties": {"a": {"type": "string", "x-mcp-header": "bad name"}}
            },
            "duplicate ignoring case": {
                "properties": {
                    "a": {"type": "string", "x-mcp-header": "Region"},
                    "b": {"type": "string", "x-mcp-header": "region"},
                }
            },
        }
        for label, schema in invalid.items():
            with self.subTest(case=label), self.assertRaises(ValueError):
                header_parameters(schema)


class RequestShapeTests(unittest.TestCase):
    def test_a_call_carries_the_routing_headers_and_accepts_both_response_forms(self) -> None:
        transport, recorder = _transport(
            _result({"structuredContent": {"ok": True}, "isError": False}), token=SECRET
        )
        transport.call(
            "efris_taxpayer_status", {"tin": "1000000001"}, meta={"k": "v"}, timeout_s=7.0
        )
        sent = recorder.requests[0]
        self.assertEqual(sent["url"], "https://mcp.example/efris")
        self.assertEqual(sent["timeout"], 7.0)
        self.assertEqual(sent["headers"]["Mcp-Method"], "tools/call")
        self.assertEqual(sent["headers"]["Mcp-Name"], "efris_taxpayer_status")
        self.assertEqual(sent["headers"]["MCP-Protocol-Version"], MCP_PROTOCOL_VERSION)
        self.assertEqual(sent["headers"]["Accept"], "application/json, text/event-stream")
        self.assertEqual(sent["headers"]["Authorization"], f"Bearer {SECRET}")
        self.assertNotIn(SECRET, sent["url"])

    def test_resource_and_prompt_requests_name_their_target(self) -> None:
        transport, recorder = _transport(_result({"contents": []}))
        transport.read_resource("efris://taxpayers/profile")
        transport.get_prompt("vat_calculation_guide", {"amount": "1"})
        self.assertEqual(recorder.requests[0]["headers"]["Mcp-Name"], "efris://taxpayers/profile")
        self.assertEqual(recorder.requests[1]["headers"]["Mcp-Name"], "vat_calculation_guide")

    def test_annotated_arguments_are_mirrored_into_headers(self) -> None:
        tool = {
            "name": "execute_sql",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "region": {"type": "string", "x-mcp-header": "Region"},
                    "limit": {"type": "integer", "x-mcp-header": "Limit"},
                    "dry_run": {"type": "boolean", "x-mcp-header": "Dry-Run"},
                    "note": {"type": "string", "x-mcp-header": "Note"},
                },
            },
        }
        transport, recorder = _transport(
            _result({"tools": [tool]}), _result({"structuredContent": {"ok": True}})
        )
        transport.list_tools()
        transport.call(
            "execute_sql",
            {"region": "us-west1", "limit": 5, "dry_run": True},
            meta={},
            timeout_s=5.0,
        )
        headers = recorder.requests[1]["headers"]
        self.assertEqual(headers["Mcp-Param-Region"], "us-west1")
        self.assertEqual(headers["Mcp-Param-Limit"], "5")
        self.assertEqual(headers["Mcp-Param-Dry-Run"], "true")
        self.assertNotIn("Mcp-Param-Note", headers, "an argument that was not given gets no header")

    def test_a_tool_with_an_invalid_header_annotation_is_left_out_not_fatal(self) -> None:
        good = {"name": "good", "inputSchema": {"type": "object", "properties": {}}}
        bad = {
            "name": "bad",
            "inputSchema": {"properties": {"n": {"type": "number", "x-mcp-header": "N"}}},
        }
        transport, _ = _transport(_result({"tools": [good, bad]}))
        self.assertEqual([tool["name"] for tool in transport.list_tools()], ["good"])


class ResponseHandlingTests(unittest.TestCase):
    def test_a_streamed_response_yields_the_message_that_answers_the_request(self) -> None:
        def stream(body: dict[str, Any]) -> _Response:
            events = [
                {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}},
                {"jsonrpc": "2.0", "id": "someone-else", "result": {"tools": [{"name": "wrong"}]}},
                {"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [{"name": "right"}]}},
            ]
            text = ": keep-alive\n\n" + "".join(
                f"event: message\ndata: {json.dumps(event)}\n\n" for event in events
            )
            return _Response(200, text=text, content_type="text/event-stream; charset=utf-8")

        transport, _ = _transport(stream)
        self.assertEqual([tool["name"] for tool in transport.list_tools()], ["right"])

    def test_a_protocol_error_keeps_its_code_and_data(self) -> None:
        transport, _ = _transport(_error(200, -32602, "Unknown tool: nope"))
        with self.assertRaises(TransportError) as caught:
            transport.call("nope", {}, meta={}, timeout_s=5.0)
        self.assertEqual(caught.exception.code, -32602)
        self.assertIn("Unknown tool", str(caught.exception))

    def test_a_version_mismatch_says_what_the_server_does_speak(self) -> None:
        transport, _ = _transport(
            _error(
                400,
                -32022,
                "Unsupported protocol version",
                {"supported": ["2025-11-25"], "requested": "x"},
            )
        )
        with self.assertRaises(TransportError) as caught:
            transport.list_tools()
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(caught.exception.data["supported"], ["2025-11-25"])
        self.assertIn("2025-11-25", str(caught.exception))

    def test_refused_credentials_are_named_without_revealing_them(self) -> None:
        transport, _ = _transport(_Response(401, {"error": "nope"}), token=SECRET)
        with self.assertRaises(TransportError) as caught:
            transport.list_tools()
        self.assertEqual(caught.exception.status, 401)
        self.assertIn("credentials", str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))

    def test_an_unreadable_error_body_is_reported_by_status(self) -> None:
        transport, _ = _transport(
            _Response(502, text="<html>bad gateway</html>", content_type="text/html")
        )
        with self.assertRaises(TransportError) as caught:
            transport.list_tools()
        self.assertIn("HTTP 502", str(caught.exception))

    def test_a_network_failure_names_the_address_but_not_the_token(self) -> None:
        transport, _ = _transport(ConnectionError(f"refused while sending {SECRET}"), token=SECRET)
        with self.assertRaises(TransportError) as caught:
            transport.list_tools()
        self.assertIn("https://mcp.example/efris", str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))


class DiscoveryTests(unittest.TestCase):
    DISCOVERED = {
        "supportedVersions": [MCP_PROTOCOL_VERSION],
        "capabilities": {"tools": {}},
        "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "mcp_efris", "version": "2.0.0"}},
        "ttlMs": 60_000,
        "cacheScope": "public",
    }

    def test_discover_is_cached_for_as_long_as_the_server_allows(self) -> None:
        transport, recorder = _transport(_result(self.DISCOVERED))
        self.assertEqual(transport.discover()["supportedVersions"], [MCP_PROTOCOL_VERSION])
        transport.discover()
        self.assertEqual(len(recorder.requests), 1)
        self.assertEqual(recorder.requests[0]["body"]["method"], "server/discover")

    def test_ping_probes_with_discover_because_ping_left_the_protocol(self) -> None:
        transport, recorder = _transport(_result(self.DISCOVERED))
        self.assertTrue(transport.ping())
        self.assertEqual(recorder.requests[0]["body"]["method"], "server/discover")
        down, _ = _transport(ConnectionError("down"))
        self.assertFalse(down.ping())

    def test_a_failed_discovery_is_not_retried_on_every_request(self) -> None:
        transport, recorder = _transport(ConnectionError("down"))
        for _ in range(3):
            with self.assertRaises(TransportError):
                transport.list_tools()
        self.assertEqual(len(recorder.requests), 1, "a server that is down must not be hammered")

    def test_discovery_resumes_once_the_retry_window_has_passed(self) -> None:
        transport, recorder = _transport(
            ConnectionError("down"), _result({"tools": [{"name": "back"}]})
        )
        with self.assertRaises(TransportError):
            transport.list_tools()
        transport._retry_after = 0.0
        self.assertEqual([tool["name"] for tool in transport.list_tools()], ["back"])
        self.assertEqual(len(recorder.requests), 2)


class ClientCredentialsTests(unittest.TestCase):
    def _grant(
        self, *responses: tuple[int, Any]
    ) -> tuple[ClientCredentialsToken, list[dict[str, Any]]]:
        calls: list[dict[str, Any]] = []
        queue = list(responses)

        def post(
            url: str, form: dict[str, str], headers: dict[str, str], timeout_s: float
        ) -> tuple[int, Any]:
            calls.append({"url": url, "form": form, "headers": headers})
            return queue.pop(0) if len(queue) > 1 else queue[0]

        grant = ClientCredentialsToken(
            token_url="https://idp.example/token",  # noqa: S106 - an address, not a secret
            client_id="ura chatbot",
            client_secret=SECRET + ":/+",
            resource="https://mcp.example/efris/",
            scope="connectors:read",
            post=post,
        )
        return grant, calls

    def test_the_request_binds_the_token_to_one_server(self) -> None:
        grant, calls = self._grant(
            (200, {"access_token": "at-1", "token_type": "Bearer", "expires_in": 600})
        )
        self.assertEqual(grant.get(), "at-1")
        self.assertEqual(
            calls[0]["form"],
            {
                "grant_type": "client_credentials",
                "resource": "https://mcp.example/efris",
                "scope": "connectors:read",
            },
        )
        # The secret travels only as HTTP Basic, each half form-encoded first.
        decoded = base64.b64decode(
            calls[0]["headers"]["Authorization"].removeprefix("Basic ")
        ).decode()
        self.assertEqual(decoded, "ura%20chatbot:" + SECRET + "%3A%2F%2B")
        self.assertNotIn(SECRET, json.dumps(calls[0]["form"]) + calls[0]["url"])

    def test_the_token_is_reused_until_it_is_invalidated(self) -> None:
        grant, calls = self._grant(
            (200, {"access_token": "at-1", "token_type": "bearer", "expires_in": 600}),
            (200, {"access_token": "at-2", "token_type": "bearer", "expires_in": 600}),
        )
        self.assertEqual((grant.get(), grant.get()), ("at-1", "at-1"))
        grant.invalidate()
        self.assertEqual(grant.get(), "at-2")
        self.assertEqual(len(calls), 2)

    def test_failures_never_carry_the_secret(self) -> None:
        cases = {
            "refused": (401, {"error": "invalid_client", "error_description": SECRET}),
            "not json": (200, None),
            "no token": (200, {"token_type": "Bearer"}),
            "unusable type": (200, {"access_token": "at", "token_type": "mac"}),
        }
        for label, response in cases.items():
            grant, _ = self._grant(response)
            with self.subTest(case=label), self.assertRaises(TokenError) as caught:
                grant.get()
            self.assertNotIn(SECRET, str(caught.exception))

    def test_a_refused_token_is_replaced_once_before_the_link_is_reported_broken(self) -> None:
        grant, token_calls = self._grant(
            (200, {"access_token": "stale", "token_type": "Bearer", "expires_in": 600}),
            (200, {"access_token": "fresh", "token_type": "Bearer", "expires_in": 600}),
        )
        transport, recorder = _transport(_Response(401), _result({"tools": []}), token_source=grant)
        self.assertEqual(transport.list_tools(), [])
        self.assertEqual(
            [request["headers"]["Authorization"] for request in recorder.requests],
            ["Bearer stale", "Bearer fresh"],
        )
        self.assertEqual(len(token_calls), 2)

    def test_a_link_using_the_grant_declares_the_extension(self) -> None:
        grant, _ = self._grant((200, {"access_token": "at", "token_type": "Bearer"}))
        transport, recorder = _transport(_result({"tools": []}), token_source=grant)
        transport.list_tools()
        capabilities = recorder.requests[0]["body"]["params"]["_meta"][
            "io.modelcontextprotocol/clientCapabilities"
        ]
        self.assertIn(
            "io.modelcontextprotocol/oauth-client-credentials", capabilities["extensions"]
        )

    def test_configuration_is_all_or_nothing_and_https_outside_development(self) -> None:
        complete = {
            "MCP_OAUTH_TOKEN_URL_EFRIS": "https://idp.example/token",
            "MCP_OAUTH_CLIENT_ID_EFRIS": "ura-chatbot",
            "MCP_OAUTH_CLIENT_SECRET_EFRIS": SECRET,
        }
        self.assertIsNotNone(
            client_credentials_from_env("efris", "https://mcp.example/efris", complete)
        )
        self.assertIsNone(client_credentials_from_env("efris", "https://mcp.example/efris", {}))
        partial = {key: value for key, value in complete.items() if "SECRET" not in key}
        self.assertIsNone(
            client_credentials_from_env("efris", "https://mcp.example/efris", partial)
        )
        plain = {
            **complete,
            "MCP_OAUTH_TOKEN_URL_EFRIS": "http://idp.internal/token",
            "APP_ENV": "production",
        }
        self.assertIsNone(client_credentials_from_env("efris", "https://mcp.example/efris", plain))


class BindingTests(unittest.TestCase):
    def test_a_url_must_be_plain_http_s_and_carry_no_credentials(self) -> None:
        self.assertEqual(_remote_url_problem("https://mcp.example/efris"), "")
        self.assertEqual(_remote_url_problem("http://connectors:8930/efris"), "")
        self.assertIn("http(s)", _remote_url_problem("file:///etc/passwd"))
        # A URL that carries a password is refused; this one is a fixture.
        self.assertIn(
            "MCP_SERVER_TOKEN",
            _remote_url_problem("https://user:pw@mcp.example/efris"),  # pragma: allowlist secret
        )

    def test_production_refuses_a_link_tls_cannot_vouch_for(self) -> None:
        with patch.dict("os.environ", {"APP_ENV": "production"}):
            self.assertIn("https", _remote_url_problem("http://connectors:8930/efris"))

    def test_an_unusable_binding_leaves_the_namespace_in_process(self) -> None:
        env = {
            "MCP_SERVER_URL_EFRIS": "http://connectors:8930/efris",
            "MCP_SERVER_URL_URSB": "ftp://connectors/ursb",
        }
        with patch.dict("os.environ", env):
            transports = build_transports()
        self.assertIsInstance(transports["efris"], HttpTransport)
        self.assertIsInstance(transports["ursb"], InProcessTransport)


class ClientCapabilityTests(unittest.TestCase):
    def test_roots_is_not_advertised(self) -> None:
        self.assertNotIn("roots", DEFAULT_CLIENT_CAPABILITIES)

    def test_input_requests_keep_the_keys_their_answers_are_filed_under(self) -> None:
        parsed = parse_input_required(
            {
                "resultType": "input_required",
                "inputRequests": {
                    "login": {"method": "elicitation/create", "params": {"mode": "form"}}
                },
                "requestState": "opaque",
            }
        )
        assert parsed is not None
        self.assertEqual(list(parsed["inputRequests"]), ["login"])
        self.assertEqual(parsed["requestState"], "opaque")


if __name__ == "__main__":
    unittest.main()

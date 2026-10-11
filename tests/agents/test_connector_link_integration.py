"""The chat backend talking to the system connectors over a real MCP link.

Everything else about connectors is tested against a stand-in transport.
Here nothing is stood in for: the real ``HttpTransport`` speaks to the
real connector gateway — the app ``python -m app.mcp.servers.gateway``
serves — through its real HTTP routes, with a real shared token, down to
the EFRIS, payment, stamp, registry, warehouse and TIN simulators.  The
only thing that is not real is the socket.

It lives in the root suite because that is the suite CI runs with
coverage: the wire rules, the authentication and the catalog would
otherwise be protected only on paper.
"""

from __future__ import annotations

from typing import Any

import pytest
from app import llm
from app.flags import flags
from app.mcp import MCPClient
from app.mcp.auth import MIN_SHARED_TOKEN_CHARS
from app.mcp.chat_connectors import chat_connector_catalog, validate_chat_connector_selection
from app.mcp.servers import gateway
from app.mcp.transport import HttpTransport, InProcessTransport
from starlette.testclient import TestClient

# The transport passes its deadline per request, which Starlette's TestClient
# accepts but warns about; a real httpx client takes it without comment.
pytestmark = pytest.mark.filterwarnings("ignore:You should not use the 'timeout' argument")

TOKEN = "link-" + "k" * MIN_SHARED_TOKEN_CHARS
SYSTEMS = sorted(gateway.CONNECTOR_SERVERS)
#: A taxpayer the EFRIS simulator seeds on first use.
SAMPLE_TIN = "1000000001"


@pytest.fixture
def bind(monkeypatch: pytest.MonkeyPatch):
    """Start the gateway and return a factory for clients bound to it."""
    from plugins import get_orchestrator

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("MCP_SERVER_ALLOW_ANONYMOUS", raising=False)
    for namespace in SYSTEMS:
        monkeypatch.setenv(f"MCP_SERVER_TOKEN_{namespace.upper()}", TOKEN)
        monkeypatch.delenv(f"MCP_SERVER_MODE_{namespace.upper()}", raising=False)
    monkeypatch.setattr(flags, "is_enabled", lambda name: name == "enterprise_connectors")
    # Another test may have left the simulators' tools out of the registry.
    get_orchestrator().wire_tools()
    http = TestClient(gateway.create_app(), base_url="http://connectors.test")

    def factory(*namespaces: str, token: str = TOKEN) -> MCPClient:
        transports: dict[str, Any] = {"core": InProcessTransport()}
        for namespace in namespaces or SYSTEMS:
            transports[namespace] = HttpTransport(
                namespace, f"http://connectors.test/{namespace}", token=token, client=http
            )
        return MCPClient(transports)

    factory.http = http  # type: ignore[attr-defined]
    return factory


def test_every_plugin_system_is_offered_as_a_demo_connector(bind):
    catalog = chat_connector_catalog(bind(), user_role="public")

    assert catalog["enabled"] is True
    by_namespace = {item["namespace"]: item for item in catalog["connectors"]}
    assert sorted(by_namespace) == SYSTEMS
    for item in by_namespace.values():
        assert (item["mode"], item["status"], item["read_only"]) == (
            "simulation",
            "available",
            True,
        )
        assert item["description"].startswith("Demo data only")
        assert item["operation_count"] == len(item["operations"]) >= 1
        assert not any(
            "LOCAL SIMULATOR" in operation["description"] for operation in item["operations"]
        )

    assert by_namespace["efris"]["label"] == "EFRIS"
    assert by_namespace["payment_system"]["label"] == "Payments"
    # The summary is the plugin's own account of itself, read from
    # server/discover rather than from whichever tool happened to sort first.
    assert "does not connect to URA" in by_namespace["efris"]["summary"]
    offered = {
        operation["name"] for item in by_namespace.values() for operation in item["operations"]
    }
    assert {
        "efris_taxpayer_status",
        "payment_view_status",
        "dts_verify_stamp",
        "ursb_verify_business",
    } <= offered
    # Writes are served by the systems but are never part of the chat surface.
    assert not {"efris_fiscal_invoice", "payment_checkout_settle", "tin_apply_individual"} & offered


def test_a_call_runs_on_the_connector_server_and_says_it_is_a_simulation(bind):
    client = bind("efris")

    result = client.call_tool("efris_taxpayer_status", {"tin": SAMPLE_TIN}, user_role="public")

    assert result.ok is True
    assert (result.namespace, result.transport) == ("efris", "http:efris")
    assert result.result["tin"] == SAMPLE_TIN
    assert (result.result["mode"], result.result["live"]) == ("simulation", False)
    assert "No live URA" in result.result["simulation_notice"]


def test_a_write_cannot_be_run_through_the_link(bind):
    client = bind("payment_system")

    result = client.call_tool(
        "payment_checkout_settle",
        {"prn": "220000000001", "payment_method": "MOBILE_MONEY"},
        user_id="u-1",
        user_role="verified_taxpayer",
        granted_purposes=["ura_account_access"],
    )

    # A proposal, or a refusal — never a settled payment.
    assert result.result.get("submitted") is not True
    assert result.result.get("requires_confirmation") is True or result.ok is False


def test_the_wrong_token_makes_a_connector_unavailable_not_open(bind):
    client = bind("efris", token="wrong-" + "x" * MIN_SHARED_TOKEN_CHARS)

    catalog = chat_connector_catalog(client, user_role="public")

    assert [(item["namespace"], item["status"]) for item in catalog["connectors"]] == [
        ("efris", "unavailable")
    ]
    assert not validate_chat_connector_selection(client, ["efris"], user_role="public")
    # The tool is still registered in this process, and must not be run from
    # here just because the server it is bound to would not answer.
    refused = client.call_tool("efris_taxpayer_status", {"tin": SAMPLE_TIN})
    assert refused.ok is False
    assert refused.result["retryable"] is True
    assert "tin" not in refused.result


def test_each_system_has_its_own_endpoint_and_credentials(bind, monkeypatch: pytest.MonkeyPatch):
    http = bind.http
    health = http.get("/health").json()
    assert sorted(health["servers"]) == SYSTEMS
    assert {server["auth"] for server in health["servers"].values()} == {"token"}

    unauthenticated = http.post("/efris", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["www-authenticate"].startswith("Bearer")
    assert http.get("/efris").status_code == 405

    # A token is per system: with a different one for URSB, EFRIS's no longer opens it.
    monkeypatch.setenv("MCP_SERVER_TOKEN_URSB", "ursb-" + "u" * MIN_SHARED_TOKEN_CHARS)
    separate = TestClient(gateway.create_app(), base_url="http://connectors.test")
    transport = HttpTransport("ursb", "http://connectors.test/ursb", token=TOKEN, client=separate)
    assert transport.ping() is False


def test_a_simulator_is_refused_in_production_at_both_ends(bind, monkeypatch: pytest.MonkeyPatch):
    client = bind("efris")
    assert validate_chat_connector_selection(client, ["efris"], user_role="public")

    monkeypatch.setenv("APP_ENV", "production")

    assert chat_connector_catalog(client, user_role="public")["connectors"] == []
    assert not validate_chat_connector_selection(client, ["efris"], user_role="public")
    # Even called directly, the system itself declines.
    direct = client.call_tool("efris_taxpayer_status", {"tin": SAMPLE_TIN})
    assert direct.ok is False
    assert "production" in direct.result["error"]


def test_an_agent_turn_reads_from_the_selected_connector(bind, monkeypatch: pytest.MonkeyPatch):
    client = bind("efris", "payment_system")
    monkeypatch.setattr(llm, "LLM_BACKEND", "vllm")
    monkeypatch.setattr(llm, "_vllm_ready", lambda: True)
    monkeypatch.setattr("app.mcp.get_client", lambda: client)
    monkeypatch.setattr(llm, "_select_tools_for_query", lambda _query, names, **_options: names)
    turns: list[dict[str, Any]] = []

    def model(messages, *, tools=None, **_kwargs):
        turns.append({"messages": messages, "tools": tools})
        if len(turns) == 1:
            return {
                "content": "",
                "tool_calls": [{"name": "efris_taxpayer_status", "arguments": {"tin": SAMPLE_TIN}}],
            }
        return {"content": "The demo EFRIS record shows this taxpayer.", "tool_calls": []}

    monkeypatch.setattr(llm, "_vllm_chat_completion", model)
    events: list[dict[str, Any]] = []

    result = llm.generate_with_tools(
        query=f"What does EFRIS hold for TIN {SAMPLE_TIN}?",
        passages=[],
        locale="en",
        connector_namespaces=["efris"],
        event_callback=events.append,
    )

    offered = {spec["function"]["name"] for spec in turns[0]["tools"]}
    assert "efris_taxpayer_status" in offered
    # Selecting EFRIS does not hand the model the payment system as well.
    assert not any(name.startswith("payment_") for name in offered)
    tool_message = next(message for message in turns[1]["messages"] if message["role"] == "tool")
    assert SAMPLE_TIN in tool_message["content"]
    assert "simulation" in tool_message["content"]
    assert result["tool_calls"][0]["result"]["mode"] == "simulation"
    completed = next(event for event in events if event["type"] == "tool_call.completed")
    assert (completed["ok"], completed["connector"], completed["connector_mode"]) == (
        True,
        "efris",
        "simulation",
    )

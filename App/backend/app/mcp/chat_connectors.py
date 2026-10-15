"""Safe discovery and selection of deployment-configured chat connectors.

A *connector* here is a namespace bound to a remote MCP server by
``MCP_SERVER_URL_<NAMESPACE>``.  In-process tools are never listed: the
taxpayer-facing connector surface is only ever a server the deployment
reached out to on purpose.

Two things about a bound server are stated by the deployment rather
than believed from the server:

* **What is behind it.**  ``MCP_SERVER_MODE_<NAMESPACE>`` is ``live`` or
  ``simulation``, and absent means ``simulation``.  The bundled EFRIS,
  payment, stamp, registry, warehouse and TIN servers are simulators
  over fixture data; nothing they return is a URA record.  A connector
  is only ever presented as live because an operator said so, and a
  simulator is never offered in production.
* **What it may do.**  Only operations that declare themselves
  read-only, non-destructive and confirmation-free are eligible, and
  the dispatch policy is run again for the caller before anything is
  offered.

A taxpayer's selection is a standing choice for the conversation, so a
selected service that stops answering — or that this account may no
longer use — is left out of the turn with a notice
(:func:`resolve_chat_connector_selection`) instead of failing it.
:func:`connector_diagnostics` tells staff why a bound server is or is
not offered, from the same gate the chat uses.
"""

from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..flags import flags
from .client import MCPClient, RemoteNamespace, _meta_of
from .protocol import MCP_PROTOCOL_VERSION

_NAMESPACE_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_NAMESPACE_LABELS = {
    "bwims": "BWIMS",
    "digital_tax_stamps": "Digital Tax Stamps",
    "dts": "Digital Tax Stamps",
    "efris": "EFRIS",
    "payment_system": "Payments",
    "payments": "Payments",
    "tin_registration": "TIN services",
    "tin": "TIN services",
    "ursb": "URSB",
}
_MODE_ENV_PREFIX = "MCP_SERVER_MODE_"
MODE_LIVE = "live"
MODE_SIMULATION = "simulation"

#: The sentence every simulator tool prepends for the model's benefit.
#: The catalog says the same thing once, as ``mode``, so it is taken off
#: the text a person reads.
_SIMULATOR_PREFIX_RE = re.compile(r"^LOCAL SIMULATOR ONLY\.[^.]*\.\s*", re.IGNORECASE)
_SIMULATION_NOTICE = "Demo data only — not connected to live URA systems."
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]+")
#: Roles that also see which server software answered.
_STAFF_ROLES = frozenset({"ura_staff", "ura_admin", "ura_auditor"})
_MAX_OPERATIONS_LISTED = 12


def connector_label(namespace: str) -> str:
    """The name a taxpayer knows a connector by."""
    return _NAMESPACE_LABELS.get(namespace, namespace.replace("_", " ").title())


def connector_mode(namespace: str) -> str:
    """What the deployment says is behind *namespace*; ``simulation`` unless told otherwise."""
    declared = os.getenv(f"{_MODE_ENV_PREFIX}{namespace.upper()}", "").strip().lower()
    return MODE_LIVE if declared == MODE_LIVE else MODE_SIMULATION


def _offered_in_this_environment(namespace: str) -> bool:
    """A simulator is a development aid; production offers only live connectors."""
    if os.getenv("APP_ENV", "development").lower() != "production":
        return True
    return connector_mode(namespace) == MODE_LIVE


def _display_text(value: Any, limit: int) -> str:
    """Text from a server, made safe to show: one line, bounded, no control characters."""
    text = _CONTROL_CHARS_RE.sub(" ", str(value or ""))
    text = _SIMULATOR_PREFIX_RE.sub("", " ".join(text.split()))
    return text[:limit].rstrip()


def _operation_title(descriptor: dict[str, Any], namespace: str = "") -> str:
    """The name of an operation as a person reads it.

    A server's own title is used when it has one. Otherwise it is made from the
    function name with the service's prefix removed, because the picker shows
    it under the service's name already: ``bwims_consignment_status`` under
    BWIMS is "Consignment status", not "Bwims consignment status".
    """
    annotations = descriptor.get("annotations")
    title = descriptor.get("title") or (annotations.get("title") if isinstance(annotations, dict) else "")
    shown = _display_text(title, 80)
    if shown:
        return shown
    from ..tools import readable_title  # noqa: PLC0415 - tools imports this package

    return readable_title(str(descriptor.get("name", "")), namespace)


#: Why an operation is not offered in chat, as :func:`read_tool_problems`
#: and :func:`connector_diagnostics` report it. Stable codes: the staff
#: console words them, and tests pin them.
PROBLEM_NO_ANNOTATIONS = "no_annotations"
PROBLEM_NOT_READ_ONLY = "not_read_only"
PROBLEM_DESTRUCTIVE_NOT_FALSE = "destructive_not_false"
PROBLEM_NO_RISK_LEVEL = "no_risk_level"
PROBLEM_UNKNOWN_RISK_LEVEL = "unknown_risk_level"
PROBLEM_MALFORMED_SCOPES = "malformed_scopes"
PROBLEM_NEEDS_CONFIRMATION = "needs_confirmation"
PROBLEM_NO_SCOPES_FOR_RISK = "no_scopes_for_risk"
PROBLEM_DUPLICATE_NAME = "duplicate_name"


def read_tool_problems(descriptor: dict[str, Any]) -> list[str]:
    """Everything that keeps *descriptor* out of the taxpayer chat surface.

    MCP annotations are hints, and MCP defaults a missing ``destructiveHint``
    to true. A connector must therefore declare both safe behaviour hints
    and URA's risk/confirmation metadata. These checks are combined with
    deployment review, the dispatch-time policy, and server authorization.
    An empty list is the only passing answer; :func:`is_taxpayer_read_tool`
    is this function's verdict, so the diagnostics cannot drift from the gate.
    """
    annotations = descriptor.get("annotations")
    if not isinstance(annotations, dict):
        return [PROBLEM_NO_ANNOTATIONS]
    problems: list[str] = []
    if annotations.get("readOnlyHint") is not True:
        problems.append(PROBLEM_NOT_READ_ONLY)
    if annotations.get("destructiveHint") is not False:
        problems.append(PROBLEM_DESTRUCTIVE_NOT_FALSE)
    declared_risk = _meta_of(descriptor, "risk", None)
    risk = declared_risk.lower() if isinstance(declared_risk, str) else ""
    if not isinstance(declared_risk, str):
        problems.append(PROBLEM_NO_RISK_LEVEL)
    elif risk not in {"low", "medium", "high"}:
        problems.append(PROBLEM_UNKNOWN_RISK_LEVEL)
    scopes = _meta_of(descriptor, "requiredScopes", ())
    scopes_ok = isinstance(scopes, list | tuple) and all(
        isinstance(scope, str) and scope.strip() for scope in scopes
    )
    if not scopes_ok:
        problems.append(PROBLEM_MALFORMED_SCOPES)
    if _meta_of(descriptor, "requiresConfirmation", None) is not False:
        problems.append(PROBLEM_NEEDS_CONFIRMATION)
    if scopes_ok and risk in {"medium", "high"} and not scopes:
        problems.append(PROBLEM_NO_SCOPES_FOR_RISK)
    return problems


def is_taxpayer_read_tool(descriptor: dict[str, Any]) -> bool:
    """Require a declared, non-writing operation with the needed policy data."""
    return not read_tool_problems(descriptor)


def _eligible_from(
    client: MCPClient,
    remotes: list[RemoteNamespace],
    *,
    user_role: str,
    granted_purposes: list[str] | None,
) -> list[tuple[str, dict[str, Any]]]:
    available = set(
        client.available_for(
            user_role=user_role,
            granted_purposes=granted_purposes or [],
        )
    )
    remote_descriptors = [
        (remote.namespace, descriptor) for remote in remotes for descriptor in remote.tools
    ]
    name_counts = Counter(
        str(descriptor.get("name", ""))
        for _, descriptor in remote_descriptors
    )
    return [
        (namespace, descriptor)
        for namespace, descriptor in remote_descriptors
        if _NAMESPACE_RE.fullmatch(namespace)
        and _offered_in_this_environment(namespace)
        and name_counts[str(descriptor.get("name", ""))] == 1
        and str(descriptor.get("name", "")) in available
        and is_taxpayer_read_tool(descriptor)
    ]


def eligible_chat_connector_tools(
    client: MCPClient,
    *,
    user_role: str,
    granted_purposes: list[str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Return policy-authorized read tools only when the feature is enabled."""
    if not flags.is_enabled("enterprise_connectors"):
        return []
    return _eligible_from(
        client,
        client.remote_namespaces(),
        user_role=user_role,
        granted_purposes=granted_purposes,
    )


def chat_connector_catalog(
    client: MCPClient,
    *,
    user_role: str,
    granted_purposes: list[str] | None = None,
) -> dict[str, Any]:
    """Build a browser-safe list without returning schemas, URLs, or secrets.

    Each entry says what the connector is (``mode``), whether it can be
    used right now (``status``), and what it would let the assistant do
    (``operations``) — the three things a person needs before agreeing
    to let it be used in their chat.
    """
    enabled = flags.is_enabled("enterprise_connectors")
    remotes = client.remote_namespaces() if enabled else []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for namespace, descriptor in _eligible_from(
        client, remotes, user_role=user_role, granted_purposes=granted_purposes
    ):
        grouped[namespace].append(descriptor)

    connectors = []
    for remote in remotes:
        namespace = remote.namespace
        if not _NAMESPACE_RE.fullmatch(namespace) or not _offered_in_this_environment(namespace):
            continue
        tools = grouped.get(namespace, [])
        if remote.reachable and not tools:
            # Reachable, but nothing this caller may use: not theirs to see.
            continue
        mode = connector_mode(namespace)
        summary = _display_text(remote.server.get("instructions"), 240) or next(
            (text for tool in tools if (text := _display_text(tool.get("description"), 240))), ""
        )
        entry: dict[str, Any] = {
            "namespace": namespace,
            "label": connector_label(namespace),
            # ``description`` is what an older client renders, so the
            # simulation notice rides in it; ``summary`` is the same text
            # without it, for a client that shows ``mode`` as a badge.
            "description": f"{_SIMULATION_NOTICE} {summary}".strip() if mode == MODE_SIMULATION else summary,
            "summary": summary,
            "mode": mode,
            "status": "available" if remote.reachable else "unavailable",
            "operation_count": len(tools),
            "operations": [
                {
                    "name": str(tool.get("name", "")),
                    "title": _operation_title(tool, namespace),
                    "description": _display_text(tool.get("description"), 200),
                }
                for tool in tools[:_MAX_OPERATIONS_LISTED]
            ],
            "read_only": True,
        }
        if user_role in _STAFF_ROLES and remote.server.get("name"):
            entry["server"] = {
                "name": _display_text(remote.server.get("name"), 80),
                "version": _display_text(remote.server.get("version"), 40),
            }
        connectors.append(entry)

    return {
        "ok": True,
        "enabled": enabled,
        "protocol_version": MCP_PROTOCOL_VERSION,
        "connectors": connectors,
    }


def selected_chat_connector_tools(
    client: MCPClient,
    namespaces: list[str] | None,
    *,
    user_role: str,
    granted_purposes: list[str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Resolve selected namespaces to only their currently authorized read tools."""
    selected = set(namespaces or [])
    if not selected:
        return []
    return [
        (namespace, descriptor)
        for namespace, descriptor in eligible_chat_connector_tools(
            client,
            user_role=user_role,
            granted_purposes=granted_purposes,
        )
        if namespace in selected
    ]


#: Why a selected connector was left out of a turn. Deliberately coarse: a
#: caller learns nothing the catalog does not already show them.
SKIPPED_DISABLED = "disabled"  # the deployment has chat connectors switched off
SKIPPED_UNAVAILABLE = "unavailable"  # offered to this caller, not answering right now
SKIPPED_NOT_OFFERED = "not_offered"  # not in this caller's catalog


@dataclass(frozen=True)
class ConnectorSelection:
    """A turn's selected connectors: the ones it can use, and why the rest are left out."""

    usable: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)


def resolve_chat_connector_selection(
    client: MCPClient,
    namespaces: list[str] | None,
    *,
    user_role: str,
    granted_purposes: list[str] | None = None,
) -> ConnectorSelection:
    """Split a selection into the connectors this turn may use and notices for the rest.

    Fail-closed is about dispatch, not about the turn: nothing outside the
    caller's current catalog is ever offered to the model. What changed is
    that a service which stops answering, or which this account may no
    longer use (a sign-out, a withdrawn consent, a flag switched off), no
    longer fails the whole turn. Before, the stream answered 403, the
    client's retry on ``/v1/chat`` answered 403 too, and the taxpayer read
    "could not reach the URA knowledge base" on every message until they
    found the chip and removed it.
    """
    selected = list(dict.fromkeys(ns for ns in namespaces or [] if _NAMESPACE_RE.fullmatch(ns)))
    if not selected:
        return ConnectorSelection()

    def notice(namespace: str, reason: str) -> dict[str, str]:
        return {"namespace": namespace, "label": connector_label(namespace), "reason": reason}

    if not flags.is_enabled("enterprise_connectors"):
        return ConnectorSelection(skipped=[notice(ns, SKIPPED_DISABLED) for ns in selected])
    remotes = client.remote_namespaces()
    eligible = {
        namespace
        for namespace, _descriptor in _eligible_from(
            client, remotes, user_role=user_role, granted_purposes=granted_purposes
        )
    }
    down = {
        remote.namespace
        for remote in remotes
        if not remote.reachable and _offered_in_this_environment(remote.namespace)
    }
    usable: list[str] = []
    skipped: list[dict[str, str]] = []
    for namespace in selected:
        if namespace in eligible:
            usable.append(namespace)
        elif namespace in down:
            skipped.append(notice(namespace, SKIPPED_UNAVAILABLE))
        else:
            skipped.append(notice(namespace, SKIPPED_NOT_OFFERED))
    return ConnectorSelection(usable=usable, skipped=skipped)


def validate_chat_connector_selection(
    client: MCPClient,
    namespaces: list[str] | None,
    *,
    user_role: str,
    granted_purposes: list[str] | None = None,
) -> bool:
    """Return whether every requested namespace has at least one eligible tool."""
    selection = resolve_chat_connector_selection(
        client, namespaces, user_role=user_role, granted_purposes=granted_purposes
    )
    return not selection.skipped


# ---------------------------------------------------------------------------
# Staff diagnostics: why a bound server is, or is not, offered in chat
# ---------------------------------------------------------------------------

#: Who a taxpayer-facing operation can be offered to. Signed-in taxpayers are
#: evaluated as if they had granted every scope the servers declare: the
#: question for an operator is whether the operation *can* reach them.
_AUDIENCES = ("public", "verified_taxpayer")


def _refused_bindings(client: MCPClient) -> list[dict[str, str]]:
    """``MCP_SERVER_URL_*`` settings that were present but not bound, and why.

    Names the variable and the rule it broke, never the address: the
    refusal itself is the operator's next step.
    """
    from .transport import URL_ENV_PREFIX, remote_url_problem  # noqa: PLC0415

    bound = client.remote_namespace_names()
    refused: list[dict[str, str]] = []
    for key, value in sorted(os.environ.items()):
        if not key.startswith(URL_ENV_PREFIX) or not value.strip():
            continue
        namespace = key[len(URL_ENV_PREFIX) :].lower()
        if namespace in bound:
            continue
        problem = remote_url_problem(value.strip()) or (
            "it was set after this API process started; restart the API to bind it"
        )
        refused.append({"namespace": namespace, "variable": key, "problem": problem})
    return refused


def connector_diagnostics(client: MCPClient) -> dict[str, Any]:
    """Why each bound server is, or is not, offered in taxpayer chat.

    Built from the same gate the chat uses (:func:`read_tool_problems`,
    :func:`_offered_in_this_environment`, the dispatch policy), so the
    answer here cannot disagree with what a taxpayer is shown. Browser-safe
    for staff: no URL, credential or schema, only how the link
    authenticates (a kind, never a value).
    """
    enabled = flags.is_enabled("enterprise_connectors")
    remotes = client.remote_namespaces()
    name_counts = Counter(
        str(descriptor.get("name", "")) for remote in remotes for descriptor in remote.tools
    )
    all_scopes = sorted(
        {
            scope
            for remote in remotes
            for descriptor in remote.tools
            for scope in (_meta_of(descriptor, "requiredScopes", ()) or ())
            if isinstance(scope, str) and scope.strip()
        }
    )
    visible = {
        "public": set(client.available_for(user_role="public", granted_purposes=[])),
        "verified_taxpayer": set(
            client.available_for(user_role="verified_taxpayer", granted_purposes=all_scopes)
        ),
    }

    servers: list[dict[str, Any]] = []
    for remote in remotes:
        namespace = remote.namespace
        offered_here = _offered_in_this_environment(namespace)
        tools: list[dict[str, Any]] = []
        for descriptor in remote.tools:
            name = str(descriptor.get("name", ""))
            problems = read_tool_problems(descriptor)
            if name_counts[name] > 1:
                problems.append(PROBLEM_DUPLICATE_NAME)
            offered_to = (
                [audience for audience in _AUDIENCES if name in visible[audience]]
                if not problems
                else []
            )
            risk = _meta_of(descriptor, "risk", "")
            scopes = _meta_of(descriptor, "requiredScopes", ())
            tools.append(
                {
                    "name": name,
                    "title": _operation_title(descriptor, namespace),
                    "problems": problems,
                    "risk": risk if isinstance(risk, str) else "",
                    "required_scopes": [s for s in scopes if isinstance(s, str)]
                    if isinstance(scopes, list | tuple)
                    else [],
                    "offered_to": offered_to,
                }
            )
        offered = sum(1 for tool in tools if tool["offered_to"])
        if not offered_here:
            status = "not_offered_in_production"
        elif not remote.reachable:
            status = "not_answering"
        elif not offered:
            status = "no_eligible_operations"
        elif not enabled:
            status = "feature_off"
        else:
            status = "ready"
        entry: dict[str, Any] = {
            "namespace": namespace,
            "label": connector_label(namespace),
            "mode": connector_mode(namespace),
            "status": status,
            "reachable": remote.reachable,
            "credential": remote.credential or "none",
            "tools_listed": len(tools),
            "tools_offered": offered,
            "tools": tools,
        }
        if remote.server.get("name"):
            entry["server"] = {
                "name": _display_text(remote.server.get("name"), 80),
                "version": _display_text(remote.server.get("version"), 40),
            }
        servers.append(entry)

    return {
        "ok": True,
        "enabled": enabled,
        "environment": "production"
        if os.getenv("APP_ENV", "development").lower() == "production"
        else "development",
        "protocol_version": MCP_PROTOCOL_VERSION,
        "servers": servers,
        "refused_bindings": _refused_bindings(client),
    }

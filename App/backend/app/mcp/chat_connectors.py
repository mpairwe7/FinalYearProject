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
"""

from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
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
    name = str(descriptor.get("name", ""))
    words = [word for word in name.split("_") if word]
    prefixes = {word for word in namespace.split("_") if word}
    if namespace:
        prefixes.add(namespace)
        prefixes.add("".join(word[0] for word in namespace.split("_") if word))
    if len(words) > 1 and words[0] in prefixes:
        words = words[1:]
    return " ".join(words).capitalize()


def is_taxpayer_read_tool(descriptor: dict[str, Any]) -> bool:
    """Require a declared, non-writing operation with the needed policy data.

    MCP annotations are hints, and MCP defaults missing ``destructiveHint``
    to true. The connector must therefore declare both safe behavior hints
    and URA's risk/confirmation metadata. These checks are combined with
    deployment review, the dispatch-time policy, and server authorization.
    """
    annotations = descriptor.get("annotations")
    if not isinstance(annotations, dict):
        return False
    declared_risk = _meta_of(descriptor, "risk", None)
    if not isinstance(declared_risk, str):
        return False
    risk = declared_risk.lower()
    scopes = _meta_of(descriptor, "requiredScopes", ())
    if not isinstance(scopes, (list, tuple)) or any(
        not isinstance(scope, str) or not scope.strip() for scope in scopes
    ):
        return False
    return bool(
        annotations.get("readOnlyHint") is True
        and annotations.get("destructiveHint") is False
        and risk in {"low", "medium", "high"}
        and _meta_of(descriptor, "requiresConfirmation", None) is False
        and (risk == "low" or bool(scopes))
    )


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


def validate_chat_connector_selection(
    client: MCPClient,
    namespaces: list[str] | None,
    *,
    user_role: str,
    granted_purposes: list[str] | None = None,
) -> bool:
    """Return whether every requested namespace has at least one eligible tool."""
    selected = set(namespaces or [])
    if not selected:
        return True
    eligible = {
        namespace
        for namespace, _descriptor in eligible_chat_connector_tools(
            client,
            user_role=user_role,
            granted_purposes=granted_purposes,
        )
    }
    return selected <= eligible

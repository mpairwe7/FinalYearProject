"""Safe discovery and selection of deployment-configured chat connectors."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from ..flags import flags
from .client import MCPClient, _meta_of

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


def eligible_chat_connector_tools(
    client: MCPClient,
    *,
    user_role: str,
    granted_purposes: list[str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Return policy-authorized read tools only when the feature is enabled."""
    if not flags.is_enabled("enterprise_connectors"):
        return []
    available = set(
        client.available_for(
            user_role=user_role,
            granted_purposes=granted_purposes or [],
        )
    )
    remote_descriptors = client.remote_tool_descriptors()
    name_counts = Counter(
        str(descriptor.get("name", ""))
        for _, descriptor in remote_descriptors
    )
    return [
        (namespace, descriptor)
        for namespace, descriptor in remote_descriptors
        if _NAMESPACE_RE.fullmatch(namespace)
        and name_counts[str(descriptor.get("name", ""))] == 1
        and str(descriptor.get("name", "")) in available
        and is_taxpayer_read_tool(descriptor)
    ]


def chat_connector_catalog(
    client: MCPClient,
    *,
    user_role: str,
    granted_purposes: list[str] | None = None,
) -> dict[str, Any]:
    """Build a browser-safe list without returning schemas, URLs, or secrets."""
    enabled = flags.is_enabled("enterprise_connectors")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if enabled:
        for namespace, descriptor in eligible_chat_connector_tools(
            client,
            user_role=user_role,
            granted_purposes=granted_purposes,
        ):
            grouped[namespace].append(descriptor)

    connectors = []
    for namespace, tools in sorted(grouped.items()):
        descriptions = [
            str(tool.get("description") or "").strip()
            for tool in tools
            if str(tool.get("description") or "").strip()
        ]
        label = _NAMESPACE_LABELS.get(
            namespace,
            namespace.replace("_", " ").title(),
        )
        connectors.append(
            {
                "namespace": namespace,
                "label": label,
                "description": descriptions[0][:240] if descriptions else "",
                "operation_count": len(tools),
                "read_only": True,
            }
        )

    return {
        "ok": True,
        "enabled": enabled,
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

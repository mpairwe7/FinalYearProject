"""Base interfaces and data structures for URA Agentic System plugins and connectors."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool schema and base conforming to URA agent framework & MCP
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ToolSchema:
    """Structural description of a tool, exported to the LLM and to MCP."""

    name: str
    description: str
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
        }
    )
    risk: str = "low"
    requires_confirmation: bool = False
    output_schema: dict[str, Any] | None = None
    namespace: str = "core"
    required_scopes: tuple[str, ...] = ()
    allowed_roles: tuple[str, ...] = ()
    scope_exempt_roles: tuple[str, ...] = ()
    title: str = ""
    read_only: bool = True
    destructive: bool = False
    idempotent: bool = True
    open_world: bool = False

    def annotations(self) -> dict[str, Any]:
        """MCP annotations object for this tool."""
        return {
            "title": self.title or self.name.replace("_", " ").capitalize(),
            "readOnlyHint": self.read_only,
            "destructiveHint": self.destructive,
            "idempotentHint": self.idempotent,
            "openWorldHint": self.open_world,
        }


class Tool(ABC):
    """Abstract base for all agent tools."""

    @property
    @abstractmethod
    def schema(self) -> ToolSchema:
        """Return the structural description of this tool."""

    @abstractmethod
    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Run the tool and return a JSON-serialisable result."""

    def to_openai_spec(self) -> dict[str, Any]:
        """Convert to the OpenAI/Qwen2.5 function-calling schema."""
        s = self.schema
        description = s.description
        if s.namespace in {"efris", "payment_system", "bwims", "tin_registration", "digital_tax_stamps", "ursb"}:
            description = (
                "LOCAL SIMULATOR ONLY. This tool does not contact or change an external government, identity, "
                "customs, registry, bank, or payment system. "
                + description
            )
        return {
            "type": "function",
            "function": {
                "name": s.name,
                "description": description,
                "parameters": s.parameters,
            },
        }

    def to_mcp_tool(self) -> dict[str, Any]:
        """Convert to an MCP Tool descriptor."""
        s = self.schema
        description = s.description
        if s.namespace in {"efris", "payment_system", "bwims", "tin_registration", "digital_tax_stamps", "ursb"}:
            description = (
                "LOCAL SIMULATOR ONLY. This tool does not contact or change an external government, identity, "
                "customs, registry, bank, or payment system. "
                + description
            )
        descriptor: dict[str, Any] = {
            "name": s.name,
            "description": description,
            "inputSchema": s.parameters,
            "annotations": s.annotations(),
            "_meta": {
                "ug.go.ura.chatbot/risk": s.risk,
                "ug.go.ura.chatbot/namespace": s.namespace,
                "ug.go.ura.chatbot/requiredScopes": list(s.required_scopes),
                "ug.go.ura.chatbot/allowedRoles": list(s.allowed_roles),
                "ug.go.ura.chatbot/scopeExemptRoles": list(s.scope_exempt_roles),
                "ug.go.ura.chatbot/requiresConfirmation": s.requires_confirmation,
            },
        }
        if s.output_schema is not None:
            descriptor["outputSchema"] = s.output_schema
        return descriptor


class PluginStatus(str, Enum):
    """Lifecycle status of a plugin."""

    DISABLED = "disabled"
    INITIALIZING = "initializing"
    ACTIVE = "active"
    DEGRADED = "degraded"
    ERROR = "error"


@dataclass(frozen=True)
class PluginMetadata:
    """Metadata describing a plugin and its backing system."""

    name: str
    display_name: str
    version: str
    description: str
    author: str = "URA IT Innovation & Modernization"
    system_type: str = "tax_system"  # e.g., "e-invoicing", "track-and-trace"
    tags: tuple[str, ...] = ()
    documentation_url: str = ""


class SystemConnector(ABC):
    """Abstract connector bridging an external or internal system to the agentic URA framework."""

    @property
    @abstractmethod
    def system_name(self) -> str:
        """Unique identifier for this system connector."""

    @abstractmethod
    def initialize(self) -> bool:
        """Initialize the connector, verifying credentials and connectivity.

        Returns True if the system is ready, False otherwise.
        """

    @abstractmethod
    def is_healthy(self) -> bool:
        """Check if the underlying system and connector are operational."""

    @abstractmethod
    def get_tools(self) -> list[Tool]:
        """Return the list of agent Tool instances exposed by this connector."""

    @abstractmethod
    def get_status(self) -> dict[str, Any]:
        """Return structured diagnostics and status for the connector."""

    def shutdown(self) -> None:  # noqa: B027
        """Gracefully release connector resources."""


class Plugin:
    """Base class for system plugins."""

    def __init__(self, metadata: PluginMetadata, connector: SystemConnector) -> None:
        self._metadata = metadata
        self._connector = connector
        self._status = PluginStatus.INITIALIZING

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata

    @property
    def connector(self) -> SystemConnector:
        return self._connector

    @property
    def status(self) -> PluginStatus:
        return self._status

    @status.setter
    def status(self, new_status: PluginStatus) -> None:
        self._status = new_status

    def enable(self) -> bool:
        """Enable plugin and initialize backing connector."""
        try:
            ok = self._connector.initialize()
            self._status = PluginStatus.ACTIVE if ok else PluginStatus.DEGRADED
            logger.info("Plugin %s enabled (status=%s)", self._metadata.name, self._status.value)
            return ok
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to enable plugin %s: %s", self._metadata.name, exc)
            self._status = PluginStatus.ERROR
            return False

    def disable(self) -> None:
        """Disable plugin and disconnect connector."""
        try:
            self._connector.shutdown()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error shutting down plugin %s: %s", self._metadata.name, exc)
        finally:
            self._status = PluginStatus.DISABLED
            logger.info("Plugin %s disabled", self._metadata.name)

"""Plugin and Connector Orchestrator for the URA Agentic System.

Manages discovery, lifecycle, health, and wiring of system connectors
into the agentic tool registry and MCP service layer.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .base import Plugin, PluginStatus, SystemConnector

logger = logging.getLogger(__name__)


def _validate_remote_endpoint(url: str) -> str:
    from urllib.parse import urlparse
    import ipaddress
    import socket

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Invalid URL scheme; must be http or https")
    hostname = parsed.hostname or ""
    if not hostname:
        raise ValueError("Missing hostname in endpoint URL")

    # Block cloud metadata addresses
    if hostname.lower() in ("169.254.169.254", "metadata.google.internal", "metadata"):
        raise ValueError("Access to cloud metadata endpoints is prohibited")

    app_env = os.getenv("APP_ENV", "development").lower()
    if app_env == "production":
        if parsed.scheme != "https":
            raise ValueError("In production, remote connector endpoints must use HTTPS")
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
                raise ValueError(f"Endpoint IP {ip} is not allowed in production")
        except ValueError:
            try:
                for res in socket.getaddrinfo(hostname, None):
                    resolved_ip = ipaddress.ip_address(res[4][0])
                    if resolved_ip.is_private or resolved_ip.is_loopback or resolved_ip.is_link_local or resolved_ip.is_multicast:
                        raise ValueError(f"Resolved endpoint IP {resolved_ip} is not allowed in production")
            except socket.gaierror:
                raise ValueError(f"Could not resolve hostname {hostname}")

    return url.rstrip("/")


class PluginOrchestrator:
    """Orchestrator managing plugins and system connectors."""

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}
        self._initialized = False

    def register_plugin(self, plugin: Plugin) -> None:
        """Register a plugin with the orchestrator."""
        name = plugin.metadata.name
        self._plugins[name] = plugin
        logger.info("Registered plugin '%s' (%s v%s)", name, plugin.metadata.display_name, plugin.metadata.version)

    def get_plugin(self, name: str) -> Plugin | None:
        """Lookup a plugin by name."""
        return self._plugins.get(name)

    def get_connector(self, system_name: str) -> SystemConnector | None:
        """Lookup a system connector by system name."""
        for plugin in self._plugins.values():
            if plugin.connector.system_name == system_name:
                return plugin.connector
        return None

    def enable_plugin(self, name: str) -> bool:
        """Enable a plugin and initialize its connector."""
        plugin = self.get_plugin(name)
        if not plugin:
            logger.warning("Cannot enable unknown plugin '%s'", name)
            return False
        return plugin.enable()

    def disable_plugin(self, name: str) -> None:
        """Disable a plugin and shut down its connector."""
        plugin = self.get_plugin(name)
        if plugin:
            plugin.disable()

    def list_plugins(self) -> list[dict[str, Any]]:
        """List registered plugins and their current status."""
        result = []
        for name, p in self._plugins.items():
            result.append(
                {
                    "name": name,
                    "display_name": p.metadata.display_name,
                    "version": p.metadata.version,
                    "system_type": p.metadata.system_type,
                    "status": p.status.value,
                    "system_name": p.connector.system_name,
                    "healthy": p.connector.is_healthy(),
                    "tools": [t.schema.name for t in p.connector.get_tools()],
                    "status_details": p.connector.get_status(),
                }
            )
        return result

    def get_connectors_summary(self) -> list[dict[str, Any]]:
        """Summarize all available system connectors for UI presentation."""
        connectors = []
        for p in self._plugins.values():
            status_data = p.connector.get_status()
            connectors.append(
                {
                    "id": p.metadata.name,
                    "system_name": p.connector.system_name,
                    "name": p.metadata.display_name,
                    "description": p.metadata.description,
                    "version": p.metadata.version,
                    "system_type": p.metadata.system_type,
                    "tags": list(p.metadata.tags),
                    "connected": p.status == PluginStatus.ACTIVE,
                    "healthy": p.connector.is_healthy(),
                    "status": p.status.value,
                    "tools": [t.schema.name for t in p.connector.get_tools()],
                    "database": status_data.get("database", {}),
                }
            )
        return connectors

    def get_connector_records(self, system_name: str, limit: int = 5) -> dict[str, Any]:
        """Fetch recent records from the connector's independent database for inspection."""
        name_clean = system_name.lower().replace("-", "_")
        conn = self.get_connector(name_clean)
        if not conn:
            # Try matching by plugin name
            p = self.get_plugin(name_clean)
            conn = p.connector if p else None

        if not conn or not hasattr(conn, "service"):
            return {"ok": False, "error": f"Connector '{system_name}' not found or has no database"}

        db = getattr(conn.service, "database", None)
        if not db:
            return {"ok": False, "error": f"No database attached to connector '{system_name}'"}

        if name_clean in ("efris",):
            return {
                "ok": True,
                "system": "efris",
                "database": str(getattr(db, "db_path", "efris_system.db")),
                "invoices": db.list_recent_invoices(limit),
                "stock": db.get_stock_items("1000000005")[:limit],
            }
        elif name_clean in ("digital_tax_stamps", "dts"):
            return {
                "ok": True,
                "system": "digital_tax_stamps",
                "database": str(getattr(db, "db_path", "dts_system.db")),
                "stamps": db.list_recent_stamps(limit),
                "orders": db.list_recent_orders(limit),
            }
        elif name_clean in ("ursb",):
            return {
                "ok": True,
                "system": "ursb",
                "database": str(getattr(db, "db_path", "ursb_system.db")),
                "entities": db.list_recent_entities(limit),
                "stats": db.get_stats(),
            }
        elif name_clean in ("bwims",):
            return {
                "ok": True,
                "system": "bwims",
                "database": str(getattr(db, "db_path", "bwims_system.db")),
                "consignments": db.list_recent_consignments(limit),
                "stats": db.get_stats(),
            }
        elif name_clean in ("tin_registration", "tin"):
            return {
                "ok": True,
                "system": "tin_registration",
                "database": str(getattr(db, "db_path", "tin_system.db")),
                "taxpayers": db.list_recent_taxpayers(limit),
                "stats": db.get_stats(),
            }
        elif name_clean in ("payment_system", "payment", "payments", "make_payment"):
            return {
                "ok": True,
                "system": "payment_system",
                "database": str(getattr(db, "db_path", "payments_system.db")),
                "prns": db.list_recent_prns(limit),
                "stats": db.get_stats(),
            }
        return {"ok": True, "system": system_name, "stats": db.get_stats()}

    def toggle_connector(self, system_name: str, enable: bool) -> dict[str, Any]:
        """Connect or disconnect a system connector."""
        name_clean = system_name.lower().replace("-", "_")
        if name_clean == "dts":
            name_clean = "digital_tax_stamps"

        p = self.get_plugin(name_clean)
        if not p:
            return {"ok": False, "error": f"Plugin '{system_name}' not found"}

        if enable:
            ok = self.enable_plugin(name_clean)
            self.wire_tools()
            return {"ok": ok, "connected": ok, "status": p.status.value}
        else:
            tool_names = [t.schema.name for t in p.connector.get_tools()]
            self.disable_plugin(name_clean)
            try:
                from app.tools import ToolRegistry

                for tool_name in tool_names:
                    ToolRegistry.unregister(tool_name)
            except Exception:
                pass
            return {"ok": True, "connected": False, "status": p.status.value}

    def register_remote_connector(
        self,
        name: str,
        endpoint_url: str,
        api_key: str = "",
        system_type: str = "external_mcp",
        display_name: str = "",
        description: str = "",
    ) -> dict[str, Any]:
        """Dynamically register an external API/MCP system connector (e.g. Stripe, GitHub, or standalone plugins server)."""
        from .base import Plugin, PluginMetadata, Tool, ToolSchema

        name_clean = name.lower().replace("-", "_").strip()
        endpoint_clean = _validate_remote_endpoint(endpoint_url)

        if name_clean in self._plugins:
            p = self._plugins[name_clean]
            p.enable()
            self.wire_tools()
            return {
                "ok": True,
                "name": name_clean,
                "message": f"Connector '{name_clean}' reactivated.",
                "tools_count": len(p.connector.get_tools()),
            }

        class _RemoteProxyTool(Tool):
            def __init__(self, tool_name: str, desc: str, params: dict[str, Any]) -> None:
                self._tool_name = tool_name
                self._desc = desc
                self._params = params

            @property
            def schema(self) -> ToolSchema:
                return ToolSchema(
                    name=self._tool_name,
                    description=self._desc,
                    parameters=self._params,
                    risk="medium",
                    namespace=name_clean,
                    required_scopes=("ura_account_access",),
                    allowed_roles=("verified_taxpayer", "ura_staff", "ura_admin", "taxpayer"),
                )

            def execute(self, **kwargs: Any) -> dict[str, Any]:
                try:
                    import json
                    import urllib.request

                    req = urllib.request.Request(  # noqa: S310
                        f"{endpoint_clean}/mcp/call",
                        data=json.dumps({"name": self._tool_name, "arguments": kwargs}).encode("utf-8"),
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {api_key}" if api_key else "",
                        },
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 # noqa: S310
                        return json.loads(resp.read().decode("utf-8"))
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "error": f"Remote call failed: {exc}"}

        class _RemoteConnector(SystemConnector):
            def __init__(self) -> None:
                self._tools = [
                    _RemoteProxyTool(
                        tool_name=f"{name_clean}_action",
                        desc=f"Execute action on external {name_clean} system.",
                        params={"type": "object", "properties": {"action": {"type": "string"}}, "additionalProperties": False},
                    )
                ]
                self._healthy = True

            @property
            def system_name(self) -> str:
                return name_clean

            def initialize(self) -> bool:
                self._healthy = True
                return True

            def is_healthy(self) -> bool:
                return self._healthy

            def get_tools(self) -> list[Tool]:
                return self._tools

            def get_status(self) -> dict[str, Any]:
                return {
                    "system_name": name_clean,
                    "display_name": display_name or name_clean.capitalize(),
                    "healthy": self._healthy,
                    "live_mode": True,
                    "endpoint_url": endpoint_clean,
                    "tool_count": len(self._tools),
                    "tools": [t.schema.name for t in self._tools],
                    "database": {"database": f"{name_clean}_remote_api", "status": "ONLINE"},
                }

        meta = PluginMetadata(
            name=name_clean,
            display_name=display_name or name_clean.capitalize(),
            version="1.0.0",
            description=description or f"External connector for {name_clean} at {endpoint_clean}",
            author="External System Provider",
            system_type=system_type,
            tags=("external", "mcp", name_clean),
            documentation_url=endpoint_clean,
        )

        plugin = Plugin(metadata=meta, connector=_RemoteConnector())
        self.register_plugin(plugin)
        plugin.enable()
        self.wire_tools()

        return {
            "ok": True,
            "name": name_clean,
            "endpoint_url": endpoint_clean,
            "status": "connected",
            "message": f"Successfully connected external system '{name_clean}' via MCP gateway at {endpoint_clean}.",
            "tools": [t.schema.name for t in plugin.connector.get_tools()],
        }

    def get_all_tools(self) -> list[Any]:
        """Aggregate all tools across active plugins."""
        tools: list[Any] = []
        for plugin in self._plugins.values():
            if plugin.status == PluginStatus.ACTIVE:
                tools.extend(plugin.connector.get_tools())
        return tools

    def wire_tools(self, registry: Any = None) -> list[str]:
        """Wire all active connector tools into the agent ToolRegistry."""
        if registry is None:
            try:
                from app.tools import ToolRegistry
                registry = ToolRegistry
            except ImportError:
                logger.warning("app.tools.ToolRegistry not available; cannot wire tools")
                return []

        wired_names: list[str] = []
        for tool in self.get_all_tools():
            registry.register(tool)
            wired_names.append(tool.schema.name)
            logger.debug("Wired connector tool '%s' into ToolRegistry", tool.schema.name)

        logger.info("Orchestrator wired %d connector tools into ToolRegistry: %s", len(wired_names), wired_names)
        return wired_names

    def unwire_tools(self, registry: Any = None) -> list[str]:
        """Unregister all connector tools from the agent ToolRegistry."""
        if registry is None:
            try:
                from app.tools import ToolRegistry
                registry = ToolRegistry
            except ImportError:
                return []

        unwired_names: list[str] = []
        for tool in self.get_all_tools():
            registry.unregister(tool.schema.name)
            unwired_names.append(tool.schema.name)

        return unwired_names

    def health_check(self) -> dict[str, Any]:
        """Comprehensive health report for all plugins and system connectors."""
        systems = {}
        all_healthy = True

        for name, p in self._plugins.items():
            status = p.connector.get_status()
            healthy = p.connector.is_healthy()
            systems[name] = status
            if not healthy and p.status == PluginStatus.ACTIVE:
                all_healthy = False

        return {
            "all_healthy": all_healthy,
            "plugin_count": len(self._plugins),
            "active_plugins": [n for n, p in self._plugins.items() if p.status == PluginStatus.ACTIVE],
            "systems": systems,
        }

    def initialize_default_plugins(self) -> None:
        """Bootstrap default system plugins for EFRIS and Digital Tax Stamps."""
        if self._initialized:
            return

        app_env = os.getenv("APP_ENV", "development").lower()
        connectors_enabled = os.getenv("FLAG_ENTERPRISE_CONNECTORS", "true" if app_env != "production" else "false").lower() in ("true", "1", "yes")
        if app_env == "production" and not connectors_enabled:
            logger.info("Default local mock connectors disabled in production (FLAG_ENTERPRISE_CONNECTORS=false).")
            self._initialized = True
            return

        from .base import Plugin, PluginMetadata
        from .bwims.connector import BwimsConnector
        from .digital_tax_stamps.connector import DigitalTaxStampsConnector
        from .efris.connector import EfrisConnector
        from .payment_system.connector import PaymentConnector
        from .tin_registration.connector import TinRegistrationConnector
        from .ursb.connector import UrsbConnector

        efris_meta = PluginMetadata(
            name="efris",
            display_name="Electronic Fiscal Receipting & Invoicing System (EFRIS)",
            version="1.0.0",
            description=(
                "Connects the agentic URA system to EFRIS for real-time fiscal invoice generation, "
                "FDN verification, stock inventory tracking, credit notes, and taxpayer terminal readiness."
            ),
            author="URA IT Innovation & Modernization",
            system_type="e-invoicing",
            tags=("efris", "vat", "invoicing", "fiscal", "tax_compliance"),
            documentation_url="https://efris.ura.go.ug",
        )
        dts_meta = PluginMetadata(
            name="digital_tax_stamps",
            display_name="Digital Tax Stamps (DTS) / Kakasa Track & Trace",
            version="1.0.0",
            description=(
                "Connects the agentic URA system to the DTS track-and-trace solution for gazetted excisable goods. "
                "Enables Kakasa stamp authentication, stamp requisition & PRN generation, production line activation, "
                "damaged stamp declaration, and manufacturer compliance inspection."
            ),
            author="URA Domestic Taxes & Customs Modernization",
            system_type="track_and_trace",
            tags=("dts", "digital_tax_stamps", "kakasa", "excise", "track_and_trace"),
            documentation_url="https://ura.go.ug/en/category/domestic-tax/digital-tax-stamps/",
        )
        ursb_meta = PluginMetadata(
            name="ursb",
            display_name="Uganda Registration Services Bureau (URSB)",
            version="1.0.0",
            description=(
                "Connects the agentic URA system to URSB for verifying registered companies, "
                "business names, Form 20 directors, annual return compliance, and non-individual TIN prerequisites."
            ),
            author="URSB & URA Inter-Agency Automation",
            system_type="business_registry",
            tags=("ursb", "business_registration", "incorporation", "form_20"),
            documentation_url="https://ursb.go.ug",
        )
        bwims_meta = PluginMetadata(
            name="bwims",
            display_name="Bonded Warehouse Information Management System (BWIMS)",
            version="1.0.0",
            description=(
                "Connects the agentic URA system to BWIMS for tracking customs bonded warehouses, "
                "IM7 warehousing declarations, statutory time-limits under EACCMA, and ex-warehouse IM4 clearances."
            ),
            author="URA Customs Modernization & DRMS",
            system_type="customs_warehousing",
            tags=("bwims", "customs", "bonded_warehouse", "im7", "im4"),
            documentation_url="https://ura.go.ug/en/bwims/",
        )
        tin_meta = PluginMetadata(
            name="tin_registration",
            display_name="URA Taxpayer Registration & Instant TIN System",
            version="1.0.0",
            description=(
                "Connects the agentic URA system to the Tax Identification Number (TIN) registration engine. "
                "Enables Instant Individual TIN issuance via citizen NIN, Non-Individual company TIN registration "
                "linked to URSB, tax heads enrollment (VAT, PAYE, CIT), and active taxpayer verification."
            ),
            author="URA Domestic Taxes & Registration Modernization",
            system_type="taxpayer_registration",
            tags=("tin", "instant_tin", "nin", "registration", "tax_obligations"),
            documentation_url="https://ura.go.ug/en/domestic-taxes/get-a-tin/",
        )
        payment_meta = PluginMetadata(
            name="payment_system",
            display_name="URA e-Services > Make a Payment Suite",
            version="1.0.0",
            description=(
                "Connects the agentic URA system to the official e-Payment portal suite. "
                "Enables generating PRN payment slips (taxes and NTR/MDA fees), reactivating expired PRNs, "
                "real-time bank payment status checking, card/mobile-money checkout, and advance motor vehicle tax verification."
            ),
            author="URA Revenue Accounting & e-Payments",
            system_type="payment_gateway",
            tags=("payments", "prn", "bank_slip", "mobile_money", "visa", "advance_tax"),
            documentation_url="https://ura.go.ug/en/make-a-payment/",
        )

        plugins_to_register = [
            Plugin(metadata=efris_meta, connector=EfrisConnector()),
            Plugin(metadata=dts_meta, connector=DigitalTaxStampsConnector()),
            Plugin(metadata=ursb_meta, connector=UrsbConnector()),
            Plugin(metadata=bwims_meta, connector=BwimsConnector()),
            Plugin(metadata=tin_meta, connector=TinRegistrationConnector()),
            Plugin(metadata=payment_meta, connector=PaymentConnector()),
        ]

        for p in plugins_to_register:
            self.register_plugin(p)
            p.enable()

        self._initialized = True
        logger.info("Initialized default URA plugins: efris, digital_tax_stamps, ursb, bwims, tin_registration, payment_system")


# Singleton instance
_global_orchestrator: PluginOrchestrator | None = None


def get_orchestrator() -> PluginOrchestrator:
    """Return or initialize global PluginOrchestrator singleton."""
    global _global_orchestrator
    if _global_orchestrator is None:
        _global_orchestrator = PluginOrchestrator()
    if not _global_orchestrator._initialized:
        _global_orchestrator.initialize_default_plugins()
    return _global_orchestrator


def reset_orchestrator() -> None:
    """Reset global orchestrator (useful for test isolation)."""
    global _global_orchestrator
    if _global_orchestrator is not None:
        for p in _global_orchestrator._plugins.values():
            p.disable()
    _global_orchestrator = None

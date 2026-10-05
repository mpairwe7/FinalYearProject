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
                    "mode": "simulation",
                    "live": False,
                    "tools": [t.schema.name for t in p.connector.get_tools()],
                    "database": status_data.get("database", {}),
                }
            )
        return connectors

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
                logger.debug("Could not unregister connector tools for %s", p.metadata.name, exc_info=True)
            return {"ok": True, "connected": False, "status": p.status.value}

    def test_connector(self, system_name: str) -> dict[str, Any]:
        """Run an active diagnostic ping and capability handshake on the connector."""
        import time

        name_clean = system_name.lower().replace("-", "_")
        if name_clean == "dts":
            name_clean = "digital_tax_stamps"

        p = self.get_plugin(name_clean)
        if not p:
            return {"ok": False, "error": f"Connector '{system_name}' not found"}

        t0 = time.perf_counter()
        try:
            healthy = p.connector.is_healthy()
        except Exception as exc:
            return {"ok": False, "healthy": False, "error": str(exc), "latency_ms": round((time.perf_counter() - t0) * 1000, 2)}

        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        tools = [t.schema.name for t in p.connector.get_tools()]
        mode = getattr(p, "mode", "simulation")
        return {
            "ok": True,
            "id": p.metadata.name,
            "name": p.metadata.display_name,
            "system_name": p.connector.system_name,
            "healthy": bool(healthy),
            "latency_ms": max(latency_ms, 0.1),
            "status": p.status.value,
            "mode": mode,
            "protocol": "mcp",
            "tools_count": len(tools),
            "tools": tools,
            "tested_at": time.time(),
        }

    def configure_connector(self, system_name: str, config: dict[str, Any]) -> dict[str, Any]:
        """Configure connector settings such as environment mode (live vs simulation)."""
        import time

        name_clean = system_name.lower().replace("-", "_")
        if name_clean == "dts":
            name_clean = "digital_tax_stamps"

        p = self.get_plugin(name_clean)
        if not p:
            return {"ok": False, "error": f"Connector '{system_name}' not found"}

        app_env = os.getenv("APP_ENV", "development").lower()
        requested_mode = config.get("mode", "").lower()
        if requested_mode:
            if requested_mode not in ("live", "simulation", "sandbox"):
                return {"ok": False, "error": "mode must be 'live' or 'simulation'"}
            if app_env == "production" and requested_mode != "live":
                return {"ok": False, "error": "Simulation mode cannot be enabled under APP_ENV=production"}
            setattr(p, "mode", requested_mode)

        endpoint_url = config.get("endpoint_url")
        if endpoint_url:
            setattr(p, "endpoint_url", str(endpoint_url))

        return {
            "ok": True,
            "id": p.metadata.name,
            "name": p.metadata.display_name,
            "mode": getattr(p, "mode", "simulation"),
            "updated_at": time.time(),
        }

    def register_remote_connector(
        self,
        name: str,
        endpoint_url: str,
        api_key: str = "",
        system_type: str = "external_mcp",
        display_name: str = "",
        description: str = "",
    ) -> dict[str, Any]:
        """Reject arbitrary endpoints until protocol negotiation and review exist."""
        del name, endpoint_url, api_key, system_type, display_name, description
        raise RuntimeError(
            "Dynamic connector registration is disabled; configure a reviewed server through deployment settings."
        )

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
        if app_env == "production":
            logger.info("Default local simulator connectors are never enabled in production.")
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
                "Local EFRIS simulator for sample invoice, stock, and taxpayer-profile data; it does not connect to URA."
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
                "Local digital-tax-stamp simulator for sample stamp, order, and manufacturer data; it does not connect to URA."
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
                "Local URSB simulator for sample business searches and registrations; it does not connect to URSB."
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
                "Local BWIMS simulator for sample warehouse, consignment, and clearance data; it does not connect to customs."
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
                "Local TIN simulator for sample taxpayer searches and registration records; it does not verify NINs or issue live TINs."
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
                "Local payment simulator for sample references, status, and checkout outcomes; it does not connect to URA or payment networks."
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

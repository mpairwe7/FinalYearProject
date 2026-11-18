"""URA Agentic System Plugins and Connectors Package.

Provides pluggable architecture and connectors for external and auxiliary
tax systems, starting with EFRIS and Digital Tax Stamps (DTS).
"""

from __future__ import annotations

from . import (
    bwims,
    digital_tax_stamps,
    efris,
    payment_system,
    tin_registration,
    ursb,
)
from .base import Plugin, PluginMetadata, PluginStatus, SystemConnector
from .orchestrator import PluginOrchestrator, get_orchestrator, reset_orchestrator

# Aliases for convenience matching user/system naming
dts = digital_tax_stamps
EFRIS = efris
URSB = ursb
BWIMS = bwims
TIN = tin_registration
tin = tin_registration
payment = payment_system
payments = payment_system
make_payment = payment_system

__all__ = [
    "Plugin",
    "PluginMetadata",
    "PluginStatus",
    "SystemConnector",
    "PluginOrchestrator",
    "get_orchestrator",
    "reset_orchestrator",
    "efris",
    "digital_tax_stamps",
    "ursb",
    "bwims",
    "tin_registration",
    "payment_system",
    "EFRIS",
    "dts",
    "URSB",
    "BWIMS",
    "TIN",
    "tin",
    "payment",
    "payments",
    "make_payment",
]

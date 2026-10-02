"""Backend plugin bridge exposing top-level plugins package within the app namespace."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is in sys.path so plugins package is accessible
from .._root import PROJECT_ROOT

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins import (  # noqa: E402
    bwims,
    digital_tax_stamps,
    efris,
    payment_system,
    tin_registration,
    ursb,
)
from plugins.base import Plugin, PluginMetadata, PluginStatus, SystemConnector  # noqa: E402
from plugins.orchestrator import (  # noqa: E402
    PluginOrchestrator,
    get_orchestrator,
    reset_orchestrator,
)

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

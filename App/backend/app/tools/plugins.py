"""Registration of plugin-provided system connectors (EFRIS, Digital Tax Stamps).

Wires external and auxiliary system tools into the global ToolRegistry and MCP layer.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure repo root is on sys.path
_root = Path(__file__).resolve().parents[3]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
for candidate in (_root, _root / "app", Path("/app")):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

logger = logging.getLogger(__name__)

try:
    from plugins.orchestrator import get_orchestrator  # noqa: E402
    from . import ToolRegistry  # noqa: E402

    # Orchestrate and register all active connector tools into ToolRegistry
    _orchestrator = get_orchestrator()
    for _tool in _orchestrator.get_all_tools():
        ToolRegistry.register(_tool)
except Exception:
    logger.debug("plugins orchestrator not available or optional", exc_info=True)

"""Registration of plugin-provided system connectors (EFRIS, Digital Tax Stamps).

Wires external and auxiliary system tools into the global ToolRegistry and MCP layer.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from .._root import PROJECT_ROOT as _root

logger = logging.getLogger(__name__)


def project_paths(root: Path) -> list[Path]:
    """Where the top-level ``plugins`` package can live, project root first.

    Never the ``app`` package directory itself. On ``sys.path`` it makes every
    subpackage importable as a top-level module, and ``import plugins`` then
    finds ``app/plugins`` — the backend's bridge to this very package — whose
    relative imports fail. In the container the package sits at ``/app/app``
    and ``PROJECT_ROOT`` is ``/app``, so ``root / "app"`` was exactly that
    directory: from 2026-10-14 the connector gateway, which imports this module
    first, served no tools at all, and the failure was logged at DEBUG.

    ``/app`` is only a fallback for a root that has no ``plugins`` of its own;
    ahead of a root that does, it would pick some other copy of the package.
    """
    container_root = Path("/app")
    if (root / "plugins").is_dir() or root == container_root:
        return [root]
    return [root, container_root]


for _candidate in reversed(project_paths(_root)):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

try:
    from plugins.orchestrator import get_orchestrator  # noqa: E402

    from . import ToolRegistry  # noqa: E402

    # Local simulators are development-only. The production flag is reserved
    # for future reviewed live integrations and cannot enable these fixtures.
    _orchestrator = get_orchestrator()

    if os.getenv("APP_ENV", "development").lower() == "production":
        for _plugin in _orchestrator.list_plugins():
            for _tool_name in _plugin["tools"]:
                ToolRegistry.unregister(_tool_name)
            _orchestrator.disable_plugin(_plugin["name"])
        logger.info("Local connector simulators remain disabled in production")
    else:
        for _tool in _orchestrator.get_all_tools():
            ToolRegistry.register(_tool)
except Exception:
    # Outside production the simulators are expected, and a connector server
    # with nothing to serve looks healthy from the outside: say so loudly.
    if os.getenv("APP_ENV", "development").lower() == "production":
        logger.debug("plugins orchestrator not available or optional", exc_info=True)
    else:
        logger.warning("connector simulators not registered: plugins could not be loaded", exc_info=True)

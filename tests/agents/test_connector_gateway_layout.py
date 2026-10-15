"""The connector gateway serves the plugin systems' tools in the container layout.

In the image the backend package is ``/app/app``, the repository's ``plugins``
package is mounted at ``/app/plugins`` and ``PROJECT_ROOT`` is ``/app``. From
2026-10-14 ``app.tools.plugins`` put ``/app/app`` — the package directory itself —
on ``sys.path``. ``import plugins`` then found the backend's ``app/plugins``
bridge, which failed on its relative import; the failure was logged at DEBUG,
and the gateway served six servers with no tools at all: healthy on
``/health``, absent from the chat picker.

The repository layout cannot show this (``<root>/app`` does not exist there),
so the test rebuilds the container's layout with symlinks and runs the
gateway's own import path in a fresh interpreter.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_PROBE = """
import json, os, sys
import app.mcp.servers.gateway as gateway
import plugins
from app.tools import ToolRegistry
root = os.environ["PROJECT_ROOT"]
print(json.dumps({
    "counts": {ns: len(ToolRegistry.mcp_tools(namespace=ns)) for ns in sorted(gateway.CONNECTOR_SERVERS)},
    "plugins_from": os.path.dirname(plugins.__file__),
    "package_dir_on_path": os.path.join(root, "app") in sys.path,
}))
"""


def test_the_gateway_serves_plugin_tools_in_the_container_layout(tmp_path: Path) -> None:
    (tmp_path / "app").symlink_to(ROOT / "App" / "backend" / "app", target_is_directory=True)
    (tmp_path / "plugins").symlink_to(ROOT / "plugins", target_is_directory=True)
    (tmp_path / "data_store").mkdir()
    env = {
        **os.environ,
        "PROJECT_ROOT": str(tmp_path),
        "APP_ENV": "development",
        "PYTHONPATH": str(tmp_path),
        "ANALYTICS_DB_DIR": str(tmp_path / "data_store"),
    }

    probe = subprocess.run(  # noqa: S603 - the current interpreter on a fixed script
        [sys.executable, "-c", _PROBE],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert probe.returncode == 0, probe.stderr[-3000:]
    seen = json.loads(probe.stdout.strip().splitlines()[-1])
    # The invariant, whatever else is on this machine (an image with its own
    # /app/plugins would mask the symptom): the package directory is never on
    # the import path, and ``plugins`` is the project's own package.
    assert seen["package_dir_on_path"] is False
    assert Path(seen["plugins_from"]) == tmp_path / "plugins"
    # The symptom: every connector server has tools to serve.
    counts = seen["counts"]
    assert set(counts) == {"bwims", "digital_tax_stamps", "efris", "payment_system", "tin_registration", "ursb"}
    assert all(count > 0 for count in counts.values()), counts


def test_the_package_directory_is_never_put_on_the_import_path(tmp_path: Path) -> None:
    from app.tools.plugins import project_paths

    # A root without its own plugins falls back to the container's, root first.
    bare = tmp_path / "bare"
    assert project_paths(bare) == [bare, Path("/app")]
    # A root with plugins is used alone: nothing else may shadow it.
    (tmp_path / "plugins").mkdir()
    assert project_paths(tmp_path) == [tmp_path]
    assert all(path.name != "app" or path == Path("/app") for path in project_paths(bare))

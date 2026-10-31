"""The Space's CPU layer lives in one file, and no CI job builds that file.

The roll pushes ``App/deploy/hf-space/Dockerfile`` to the Space whole. On 2026-10-31 the layer
existed only on the Space, so a roll deleted it and left its flags on, and the API crashed on
every start for about 8 hours while Hugging Face reported RUNNING (G371).

The layer also edits app source at build time with ``assert old in text``. An ordinary change to
one of those lines would pass every test here and then fail the Space's build in production, so
these tests read the patch and check what it depends on. They parse it and never run it.
"""

from __future__ import annotations

import ast
import base64
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "App" / "deploy" / "hf-space" / "Dockerfile"
RECEPTIONIST_REQUIREMENTS = ROOT / "App" / "backend" / "requirements-receptionist.txt"


def _patch_script() -> ast.Module:
    encoded = re.search(r'HF_CPU_PATCH_B64="([A-Za-z0-9+/=]+)"', DOCKERFILE.read_text(encoding="utf-8"))
    assert encoded, "the CPU layer is missing from the Dockerfile the roll pushes"
    return ast.parse(base64.b64decode(encoded.group(1)).decode("utf-8"))


def _in_repo(container_path: str) -> Path:
    return ROOT / "App" / "backend" / container_path.removeprefix("/app/backend/")


def _patched_lines() -> list[tuple[Path, str, str]]:
    """(source file, patch variable, text) for every line the layer expects to find.

    The script reads a file, then declares what it replaces in it, so each ``old_*`` belongs
    to the last ``Path(".../*.py")`` above it.
    """
    source: Path | None = None
    found: list[tuple[Path, str, str]] = []
    for node in _patch_script().body:
        if not (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)):
            continue
        name, value = node.targets[0].id, node.value
        if isinstance(value, ast.Call) and getattr(value.func, "id", "") == "Path":
            path = value.args[0].value
            if path.endswith(".py"):
                source = _in_repo(path)
        elif name.startswith("old") and isinstance(value, ast.Constant) and source is not None:
            found.append((source, name, value.value))
    return found


PATCHED_LINES = _patched_lines()


def test_the_patch_was_read() -> None:
    # Guards the parametrized test below, which would pass by running nothing.
    assert len(PATCHED_LINES) >= 12


@pytest.mark.parametrize(
    ("source", "name", "text"),
    PATCHED_LINES,
    ids=[f"{source.name}:{name}" for source, name, _text in PATCHED_LINES],
)
def test_every_line_the_layer_edits_still_exists(source: Path, name: str, text: str) -> None:
    assert text in source.read_text(encoding="utf-8"), (
        f"{source.name} no longer contains {name}. The Space's build asserts on it and would fail "
        "in production. Update HF_CPU_PATCH_B64 in App/deploy/hf-space/Dockerfile with this change."
    )


def test_the_flag_that_needs_pipecat_ships_with_pipecat() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "FLAG_VOICE_RECEPTIONIST=true" in dockerfile
    installed = re.search(r'"(pipecat-ai\[[^"]+)"', dockerfile)
    assert installed, "the receptionist is switched on here and Pipecat is not installed, which stops the API starting"
    assert installed.group(1) in RECEPTIONIST_REQUIREMENTS.read_text(encoding="utf-8")

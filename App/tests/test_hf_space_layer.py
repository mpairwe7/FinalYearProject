"""The Space's CPU layer lives in one file, and no CI job builds that file.

The roll pushes ``App/deploy/hf-space/Dockerfile`` to the Space whole. On 2026-10-31 the layer
existed only on the Space, so a roll deleted it and left its flags on, and the API crashed on
every start for about 8 hours while Hugging Face reported RUNNING (G371).

The layer also edits app source at build time with ``assert old in text``. An ordinary change to
one of those lines would pass every test here and then fail the Space's build in production, so
these tests read the patch and check what it depends on. They parse it and never run it.

What the layer once pasted into the retriever and the speech service is repository code now, with
its own tests (ADR-0068): ``test_cpu_lexical_reranker.py`` and ``test_gemini_audio_fallback.py``.
"""

from __future__ import annotations

import ast
import base64
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
DOCKERFILE = ROOT / "App" / "deploy" / "hf-space" / "Dockerfile"
RECEPTIONIST_REQUIREMENTS = ROOT / "App" / "backend" / "requirements-receptionist.txt"


def _patch_script() -> ast.Module:
    encoded = re.search(r'HF_CPU_PATCH_B64="([A-Za-z0-9+/=]+)"', DOCKERFILE.read_text(encoding="utf-8"))
    assert encoded, "the CPU layer is missing from the Dockerfile the roll pushes"
    return ast.parse(base64.b64decode(encoded.group(1)).decode("utf-8"))


def _patch_strings() -> dict[str, str]:
    return {
        node.targets[0].id: node.value.value
        for node in _patch_script().body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


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
        if isinstance(value, ast.Call) and getattr(value.func, "id", "") in ("Path", "_app_source"):
            path = value.args[0].value
            if getattr(value.func, "id", "") == "_app_source":  # the current path, as this repo has it
                path = f"/app/backend/app/{path}"
            if path.endswith(".py"):
                source = _in_repo(path)
        elif name.startswith("old") and isinstance(value, ast.Constant) and source is not None:
            found.append((source, name, value.value))
    return found


PATCHED_LINES = _patched_lines()


def test_the_patch_was_read() -> None:
    # Guards the parametrized test below, which would pass by running nothing.
    assert len(PATCHED_LINES) >= 8


def test_the_cpu_reranker_is_the_repositorys_and_the_space_turns_it_on() -> None:
    """The layer once pasted `_CpuLexicalReranker` into the retriever and replaced its score loop,
    which left that reranker's candidates without the scores demotion and the gates read (G698)."""
    assert "retriever.py" not in {source.name for source, _name, _text in PATCHED_LINES}
    assert "class _CpuLexicalReranker" not in "".join(_patch_strings().values())
    assert re.search(r"^\s*SPARSE_CPU_RERANKER=true\b", DOCKERFILE.read_text(encoding="utf-8"), re.M)
    retriever = (ROOT / "App" / "backend" / "app" / "rag" / "retriever.py").read_text(encoding="utf-8")
    assert "class _CpuLexicalReranker" in retriever


def test_the_gemini_speech_fallback_is_the_repositorys_and_the_space_turns_it_on() -> None:
    """The layer once inserted `_gemini_audio_transcribe` into the speech service, untraced, outside
    the cloud deadline, and reporting a blocked response as silence (G698)."""
    assert "speech_service.py" not in {source.name for source, _name, _text in PATCHED_LINES}
    assert "_gemini_audio_transcribe" not in "".join(_patch_strings().values())
    assert re.search(r"^\s*STT_FALLBACK_BACKEND=gemini\b", DOCKERFILE.read_text(encoding="utf-8"), re.M)
    speech = (ROOT / "App" / "backend" / "app" / "speech" / "speech_service.py").read_text(encoding="utf-8")
    assert "def _gemini_audio_transcribe" in speech


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
    assert installed, "the receptionist is switched on here and Pipecat is not installed: every call would be refused"
    assert installed.group(1) in RECEPTIONIST_REQUIREMENTS.read_text(encoding="utf-8")

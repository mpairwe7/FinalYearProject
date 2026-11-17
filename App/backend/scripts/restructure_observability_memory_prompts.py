"""Move observability and memory modules into packages and give system prompts one home (ADR-0060).

Step 1 reuses the ADR-0056 codemod (``restructure_rag_layout.py``) with this move
table: every import, patch-target string and path in the repository follows the
module. Step 2 moves each module-level system prompt into ``app/prompts/`` and
imports it back into the module that uses it, under the name it had, so callers
and tests that read ``module.NAME`` are unchanged. Rerun on the parent commit to
reproduce the diff.

    python3 App/backend/scripts/restructure_observability_memory_prompts.py --phase 1           # dry run
    python3 App/backend/scripts/restructure_observability_memory_prompts.py --phase 1 --apply   # rewrite + git mv
    python3 App/backend/scripts/restructure_observability_memory_prompts.py --phase 1 --verify  # every app import resolves
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
import textwrap
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import restructure_rag_layout as codemod  # noqa: E402

SELF = Path(__file__).resolve()
ADR = codemod.REPO / "docs/adr/0060-observability-memory-and-prompt-packages.md"

# Three phases, one pull request each, so every diff stays reviewable (under 100 files).
# Run each phase on the commit before its pull request.
PHASES: dict[int, dict[str, str]] = {
    1: {
        "tracing": "observability.tracing",
        "client_telemetry": "observability.client_telemetry",
        "security_events": "observability.security_events",
        "case_facts": "memory.case_facts",
        "language_state": "memory.language_state",
    },
    2: {"analytics": "observability.analytics"},
    3: {"agents.prompts": "prompts.agents"},
}
PACKAGES = {
    1: {"observability": "Observability: Prometheus metrics, OpenTelemetry traces, real-user telemetry and security events."},
    2: {},
    3: {"prompts": "System prompts for every model call, one module per area. Callers import them from here."},
}
codemod.WORD_RENAMES = {}
codemod.ALIAS_RENAMES = {}
codemod.SKIP = {codemod.SELF, SELF, ADR}
codemod.OLD_NAMES_ON_PURPOSE = {
    ADR: "the record of the move",
    codemod.SELF: "the ADR-0056 move table",
    codemod.REPO / "App/deploy/hf-space/Dockerfile": "builds FROM a pinned image that may predate the move",
}
# Findings on the parent commit (the same nine ADR-0056 recorded), under their old and new paths.
codemod.KNOWN_FINDINGS = {
    f"App/backend/app/{prefix}client_telemetry.py: unresolved string app.client" for prefix in ("", "observability/")
} | {
    f"App/backend/app/{prefix}security_events.py: unresolved string app.security" for prefix in ("", "observability/")
} | {
    "App/backend/tests/test_mcp_wire_conformance.py: no module app.mcp.servers",
    "App/backend/tests/test_observability_endpoints.py: unresolved string app.security",
    "App/backend/tests/test_observability_endpoints.py: unresolved string app.test",
    "tests/agents/test_connector_link_integration.py: no module app.mcp.servers",
    "tests/agents/test_kernel_advanced_2026_11.py: unresolved string app.secret",
    "tests/agents/test_mcp_toolathlon_2026_october.py: unresolved string app.security",
    "tests/test_hf_space_layer.py: unresolved string app._speech_service_as_built",
}

# (module under app/, constant, prompts module, name in app/prompts). The module imports it back as the constant.
PROMPTS: list[tuple[str, str, str, str]] = [
    ("rag/prompt_builder.py", "SYSTEM_PROMPT", "answer", "SYSTEM_PROMPT"),
    ("rag/prompt_builder.py", "CLOUD_SYSTEM_PROMPT", "answer", "CLOUD_SYSTEM_PROMPT"),
    ("rag/pipeline.py", "_JUDGE_PROMPT", "answer", "FAQ_JUDGE_PROMPT"),
    ("rag/query.py", "REWRITE_SYSTEM", "query", "REWRITE_SYSTEM"),
    ("rag/query.py", "REWRITE_PROMPT", "query", "REWRITE_PROMPT"),
    ("receptionist/brain.py", "LUGANDA_RAG_SYSTEM", "receptionist", "LUGANDA_RAG_SYSTEM"),
    ("receptionist/brain.py", "QUERY_EXTRACT_SYSTEM", "receptionist", "QUERY_EXTRACT_SYSTEM"),
    ("receptionist/brief.py", "BRIEF_SYSTEM", "receptionist", "BRIEF_SYSTEM"),
    ("receptionist/brief.py", "_FULL_PROMPT", "receptionist", "BRIEF_FULL_PROMPT"),
    ("receptionist/brief.py", "_INCREMENTAL_PROMPT", "receptionist", "BRIEF_INCREMENTAL_PROMPT"),
    ("receptionist/summary.py", "SUMMARY_SYSTEM", "receptionist", "SUMMARY_SYSTEM"),
    ("chat_title.py", "_SYSTEM_PROMPT", "title", "CHAT_TITLE_SYSTEM"),
    ("agents/tiebreak.py", "_PROMPT", "agents", "TIEBREAK_PROMPT"),
    ("vision/encoder.py", "_VLM_PROMPT", "vision", "VLM_PROMPT"),
]
PROMPT_MODULE_DOCS = {
    "answer": "System prompts for answer generation and the FAQ judge. ``rag/prompt_builder.py`` assembles the messages.",
    "query": "Prompts for the follow-up rewriter, whose output becomes the retrieval query (ADR-0031).",
    "receptionist": "System prompts for the call receptionist: query extraction, Luganda answers, the officer brief and the call summary.",
    "title": "System prompt for the summary chat title (ADR-0017).",
    "vision": "Instruction prompt for the vision model that reads uploaded documents.",
}


def _block(lines: list[str], node: ast.stmt) -> tuple[int, int]:
    """0-based [start, end) of an assignment and the comment lines directly above it."""
    start = node.lineno - 1
    while start > 0 and lines[start - 1].lstrip().startswith("#"):
        start -= 1
    return start, node.end_lineno


def _assignment(tree: ast.Module, name: str) -> ast.stmt | None:
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return node
    return None


def _import_line(module: str, names: list[str]) -> str:
    line = f"from {module} import {', '.join(names)}\n"
    return line if len(line) <= 100 else f"from {module} import (\n" + "".join(f"    {n},\n" for n in names) + ")\n"


def _docstring(text: str) -> str:
    return f'"""{text}"""\n' if len(text) <= 94 else '"""' + "\n".join(textwrap.wrap(text, 96)) + '\n"""\n'


def extract_prompts(apply: bool) -> None:
    by_module: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for module, const, target, new in PROMPTS:
        by_module[module].append((const, target, new))
    appended: dict[str, list[str]] = defaultdict(list)
    for module, items in by_module.items():
        path = codemod.APP / module
        text = path.read_text()
        tree = ast.parse(text)
        items = [(c, t, n, node) for c, t, n in items if (node := _assignment(tree, c)) is not None]
        if not items:
            continue  # already extracted
        lines = text.splitlines(keepends=True)
        spans, imports = [], defaultdict(list)
        for const, target, new, node in items:
            start, end = _block(lines, node)
            spans.append((start, end))
            block = "".join(lines[start:end])
            appended[target].append(re.sub(rf"^{re.escape(const)}\b", new, block, count=1, flags=re.M))
            imports[target].append(new if new == const else f"{new} as {const}")
        first = min(s for s, _ in spans)
        anchor = max((n.end_lineno for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) and n.end_lineno <= first), default=0)
        depth = module.count("/")
        statements = "".join(_import_line(f"{'.' * (depth + 1)}prompts.{t}", sorted(names)) for t, names in sorted(imports.items()))
        for start, end in sorted(spans, reverse=True):
            del lines[start:end]
        lines.insert(anchor, statements)
        new_text = re.sub(r"\n{4,}", "\n\n\n", "".join(lines))
        print(f"prompts: {module}: {', '.join(c for c, _, _, _ in items)}")
        if apply:
            path.write_text(new_text)
    for target, blocks in appended.items():
        path = codemod.APP / "prompts" / f"{target}.py"
        print(f"prompts: -> app/prompts/{target}.py ({len(blocks)})")
        if not apply:
            continue
        head = path.read_text().rstrip() + "\n\n\n" if path.exists() else f"{_docstring(PROMPT_MODULE_DOCS[target])}\nfrom __future__ import annotations\n\n"
        path.write_text(head + "\n\n".join(b.rstrip() + "\n" for b in blocks))
        subprocess.run(["git", "add", str(path)], cwd=codemod.REPO, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true", help="rewrite files, git mv them and extract the prompts")
    group.add_argument("--verify", action="store_true", help="check every app import resolves")
    parser.add_argument("--phase", type=int, choices=sorted(PHASES), required=True)
    args = parser.parse_args()
    codemod.MOVES, codemod.NEW_PACKAGES = PHASES[args.phase], PACKAGES[args.phase]
    if args.verify:
        return codemod.verify()
    codemod.run(apply=args.apply)
    if args.phase == 3:
        extract_prompts(apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())

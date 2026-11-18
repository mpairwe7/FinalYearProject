"""Guardrails, agents and workflows each get a package laid out by job (ADR-0061).

Phase 1 splits ``guardrails.py`` into ``guardrails/`` (``split_module.py``). Phase 2
moves the premise guard, guard friction and PDF guards into it (the ADR-0056
codemod). Phase 3 moves the LangGraph-style orchestration from ``agents/graphs/``
to ``workflows/graph/`` and names the evaluator-optimizer after its role,
``agents/critic.py``. One pull request per phase keeps each diff under 100 files.
Run each phase on the commit before its pull request.

    python3 App/backend/scripts/restructure_guardrails_agents_workflows.py --phase 1           # dry run
    python3 App/backend/scripts/restructure_guardrails_agents_workflows.py --phase 1 --apply
    python3 App/backend/scripts/restructure_guardrails_agents_workflows.py --phase 1 --verify
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import restructure_rag_layout as codemod  # noqa: E402
import split_module  # noqa: E402

SELF = Path(__file__).resolve()
ADR = codemod.REPO / "docs/adr/0061-guardrails-agents-and-workflows-packages.md"
OTHER_LAYOUTS = {codemod.SELF, SELF, ADR, Path(split_module.__file__).resolve(),
                 codemod.APP.parent / "scripts/restructure_observability_memory_prompts.py"}

GUARDRAILS = split_module.Plan(
    module="guardrails",
    package_doc=(
        "OWASP LLM Top 10 (2025) controls, one module per job.\n\n"
        "- ``input_checks``: prompt injection, direct and indirect (LLM01): ``InputGuard``, ``scan_*_text``\n"
        "- ``fences``: the spotlight fences that keep retrieved and uploaded text as data\n"
        "- ``pii``: Ugandan personal data patterns, redaction and the streaming DLP filter (LLM02)\n"
        "- ``output_checks``: ``OutputGuard`` (grounding, abstention, escalation, prompt leakage, LLM05/07/09),\n"
        "  the figure release gate and link provenance\n"
        "- ``normalize``: Unicode canonicalisation both sides share\n"
        "- ``premise``, ``friction``, ``pdf``: false-premise checks, friction against adaptive probing, PDF guards\n"
    ),
    submodules={
        "normalize": "Unicode canonicalisation the input and output checks share, and the package logger.",
        "input_checks": "Prompt injection checks, direct and indirect (OWASP LLM01): ``InputGuard`` and ``scan_*_text``.",
        "fences": "Spotlight fences: retrieved, uploaded and header text stays data in every prompt (ADR-0027).",
        "pii": "Ugandan personal data: patterns, redaction, stated identifiers and the streaming DLP filter (LLM02).",
        "output_checks": "``OutputGuard``, the figure release gate and link provenance (OWASP LLM05, LLM07, LLM09).",
    },
    starts=[(1, "normalize"), (275, "input_checks"), (462, "pii"), (494, "normalize"), (715, "input_checks"),
            (938, "fences"), (1011, "pii"), (1288, "output_checks")],
    overrides={"STORE_RAW_PROMPTS": "pii", "ABSTENTION_THRESHOLD": "output_checks"},
    shared="normalize",
    # The logger keeps its name, so log queries on "app.guardrails" still match.
    text_edits={
        "normalize": [("logging.getLogger(__name__)", 'logging.getLogger("app.guardrails")')],
        # A ten-digit literal starting with 1 reads as a TIN to the ura-pii-ug-tin-hardcoded Semgrep rule.
        "pii": [("(e.g. 1" + "0" * 9 + " UGX)", "(for example 1,000,000,000 UGX)")],
    },
)

PHASES: dict[int, dict[str, str]] = {
    1: {},
    2: {"premise_guard": "guardrails.premise", "guard_friction": "guardrails.friction", "pdf_guards": "guardrails.pdf"},
    3: {"agents.graphs": "workflows.graph", "agents.evaluator": "agents.critic"},
}

codemod.WORD_RENAMES = {}
codemod.ALIAS_RENAMES = {}
codemod.SKIP = OTHER_LAYOUTS
codemod.OLD_NAMES_ON_PURPOSE = {
    ADR: "the record of the move",
    codemod.SELF: "the ADR-0056 move table",
    codemod.REPO / "App/deploy/hf-space/Dockerfile": "builds FROM a pinned image that may predate the move",
}
codemod.KNOWN_FINDINGS = {
    f"App/backend/app/observability/{name}.py: unresolved string app.{word}"
    for name, word in (("client_telemetry", "client"), ("security_events", "security"))
} | {
    "App/backend/tests/test_mcp_wire_conformance.py: no module app.mcp.servers",
    "App/backend/tests/test_observability_endpoints.py: unresolved string app.security",
    "App/backend/tests/test_observability_endpoints.py: unresolved string app.test",
    "tests/agents/test_connector_link_integration.py: no module app.mcp.servers",
    "tests/agents/test_kernel_advanced_2026_11.py: unresolved string app.secret",
    "tests/agents/test_mcp_toolathlon_2026_october.py: unresolved string app.security",
    "tests/test_hf_space_layer.py: unresolved string app._speech_service_as_built",
}


GUARD_FILES = ("input_checks", "output_checks", "pii", "fences", "normalize")
LIVING_DOCS = ("README.md", "App/README.md", "docs/PROJECT_SETUP.md", "docs/RAG_ARCHITECTURE.md",
               "docs/runbooks/multilingual-figure-fidelity.md")
# (path, old, new): what the split cannot infer. Exact text; every edit must apply.
HAND_EDITS: dict[int, list[tuple[str, str, str]]] = {
    1: [
        ("App/backend/tests/test_linear_time_text.py", "from app import calculator_router, guardrails, mt\n",
         "from app import calculator_router, mt\nfrom app.guardrails import output_checks\n"),
        ("App/backend/tests/test_privacy_compliance_306.py", "from app import guardrails\n",
         "from app.guardrails import input_checks\n"),
        ("App/backend/tests/test_privacy_compliance_306.py", "guardrails.logger", "input_checks.logger"),
        ("App/backend/tests/test_privacy_compliance_306.py", "guardrails.InputGuard", "input_checks.InputGuard"),
        ("tests/agents/test_agentic_rag_context_sandbox_2026_11.py", '("guardrails.py", ',
         "(" + ", ".join(f'"guardrails/{m}.py"' for m in GUARD_FILES) + ",\n                  "),
        ("governance/compliance_check.py", '    "App/backend/app/guardrails/",\n',
         "".join(f'    "App/backend/app/guardrails/{m}.py",\n' for m in GUARD_FILES)),
        ("governance/compliance_check.py",
         '    "App/backend/app/guardrails/": [\n        "InputGuard",\n        "OutputGuard",\n        "redact_pii",\n'
         '        "check_grounding",\n        "should_abstain",\n        "should_escalate",\n        "STORE_RAW_PROMPTS",\n    ],\n',
         '    "App/backend/app/guardrails/input_checks.py": ["InputGuard"],\n'
         '    "App/backend/app/guardrails/output_checks.py": [\n        "OutputGuard",\n        "redact_pii",\n'
         '        "check_grounding",\n        "should_abstain",\n        "should_escalate",\n    ],\n'
         '    "App/backend/app/guardrails/pii.py": ["redact_pii_text", "STORE_RAW_PROMPTS"],\n'),
        ("App/backend/AGENTS.md", "| `guardrails.py` | Input/output OWASP LLM01–09 |",
         "| `guardrails/` | OWASP LLM01–09 (ADR-0061): `input_checks.py` (`InputGuard`, `scan_*_text`), `fences.py` "
         "(spotlight fences), `pii.py` (redaction, streaming DLP), `output_checks.py` (`OutputGuard`, the figure "
         "release gate, link provenance), `normalize.py` (shared canonicalisation) |"),
        ("governance/controls/README.md",
         "`OutputGuard.redact_pii` and `redact_pii_text` in [guardrails.py](../../App/backend/app/guardrails/) strip",
         "`OutputGuard.redact_pii` ([output_checks.py](../../App/backend/app/guardrails/output_checks.py)) and "
         "`redact_pii_text` ([pii.py](../../App/backend/app/guardrails/pii.py)) strip"),
        ("governance/controls/README.md", "both in [guardrails.py](../../App/backend/app/guardrails/).",
         "in [input_checks.py](../../App/backend/app/guardrails/input_checks.py) and "
         "[output_checks.py](../../App/backend/app/guardrails/output_checks.py)."),
        ("governance/policies/prohibited_uses.md", "[guardrails.py](../../App/backend/app/guardrails/) blocks",
         "[input_checks.py](../../App/backend/app/guardrails/input_checks.py) blocks"),
        ("prompts/README.md", "in `guardrails.py` must", "in `guardrails/output_checks.py` must"),
        ("agents/prompts/README.md", "Keep `guardrails.py`", "Keep `guardrails/output_checks.py`"),
    ],
}


def _hand_edits(phase: int, apply: bool) -> None:
    for rel, old, new in HAND_EDITS.get(phase, []):
        path = codemod.REPO / rel
        text = path.read_text()
        if new in text and old not in text:
            continue  # already applied
        assert old in text, f"{rel}: {old[:60]!r} not found"
        if apply:
            path.write_text(text.replace(old, new))
    if phase == 1 and apply:
        path = codemod.REPO / "App/backend/tests/test_linear_time_text.py"
        path.write_text(re.sub(r"\bguardrails\.", "output_checks.", path.read_text()))
        for rel in LIVING_DOCS:
            path = codemod.REPO / rel
            path.write_text(path.read_text().replace("guardrails.py", "guardrails/"))
    print(f"hand edits: {len(HAND_EDITS.get(phase, []))} applied")


def _rewrite_guardrails_paths(apply: bool) -> None:
    """Docs and comments that name ``guardrails.py`` name the package instead."""
    changed = 0
    for path in codemod.tracked_files():
        if path in OTHER_LAYOUTS or not codemod.is_text(path):
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        new = text.replace("app/guardrails.py", "app/guardrails/")
        if new != text:
            changed += 1
            if apply:
                path.write_text(new)
    print(f"paths: {changed} files name app/guardrails/")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true")
    group.add_argument("--verify", action="store_true")
    parser.add_argument("--phase", type=int, choices=sorted(PHASES), required=True)
    args = parser.parse_args()
    codemod.MOVES, codemod.NEW_PACKAGES = PHASES[args.phase], {}
    if args.verify:
        return codemod.verify()
    if args.phase == 1 and (codemod.APP / "guardrails.py").exists():
        owner_of = split_module.split(GUARDRAILS, apply=args.apply)
        split_module.repoint(GUARDRAILS, owner_of, apply=args.apply, skip=OTHER_LAYOUTS)
        _rewrite_guardrails_paths(apply=args.apply)
        _hand_edits(1, apply=args.apply)
    if codemod.MOVES:
        codemod.run(apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())

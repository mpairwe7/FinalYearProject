"""Move the agentic RAG modules into the reference package layout (ADR-0056).

The diff this writes is the whole move: review MOVES and the rewrite rules
here, then rerun it on the parent commit to reproduce the change.

    python3 App/backend/scripts/restructure_rag_layout.py           # dry run
    python3 App/backend/scripts/restructure_rag_layout.py --apply   # rewrite + git mv
    python3 App/backend/scripts/restructure_rag_layout.py --verify  # every app import resolves
"""

from __future__ import annotations

import argparse
import ast
import functools
import importlib.util
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
APP = REPO / "App/backend/app"
SELF = Path(__file__).resolve()
# Files that name an old module on purpose; --verify does not report them.
OLD_NAMES_ON_PURPOSE = {
    REPO / "docs/adr/0056-rag-package-layout.md": "the record of the move",
    REPO / "App/deploy/hf-space/Dockerfile": "builds FROM a pinned image that may predate the move",
    REPO / "App/backend/tests/test_local_dense_leg.py": "asserts the Dockerfile's fallback",
}
# Findings on dev before the move, keyed without line numbers; --verify fails only on others.
KNOWN_FINDINGS = {
    "App/backend/app/client_telemetry.py: unresolved string app.client",
    "App/backend/app/security_events.py: unresolved string app.security",
    "App/backend/tests/test_mcp_wire_conformance.py: no module app.mcp.servers",
    "App/backend/tests/test_observability_endpoints.py: unresolved string app.security",
    "App/backend/tests/test_observability_endpoints.py: unresolved string app.test",
    "tests/agents/test_connector_link_integration.py: no module app.mcp.servers",
    "tests/agents/test_kernel_advanced_2026_11.py: unresolved string app.secret",
    "tests/agents/test_mcp_toolathlon_2026_october.py: unresolved string app.security",
    "tests/test_hf_space_layer.py: unresolved string app._speech_service_as_built",
}

# Old module (dotted, under app) -> new module. A package key moves the whole tree.
MOVES: dict[str, str] = {
    "service": "rag.pipeline",
    "retriever": "rag.retriever",
    "llm": "rag.generator",
    "query": "rag.query",
    "hyde": "rag.hyde",
    "corrective_rag": "rag.corrective_rag",
    "context_manager": "rag.context_manager",
    "cache": "rag.cache",
    "claim_verifier": "rag.claim_verifier",
    "claim_polarity": "rag.claim_polarity",
    "entailment": "rag.entailment",
    "faq_binding": "rag.faq_binding",
    "turn_context": "rag.turn_context",
    "turn_record": "rag.turn_record",
    "english_rendering": "rag.english_rendering",
    "live_research_turn": "rag.live_research_turn",
    "offline_rag": "rag.offline_rag",
    "indexer": "ingestion.indexer",
    "pdf_corpus": "ingestion.pdf_corpus",
    "faq_corpus": "ingestion.faq_corpus",
    "crawl_corpus": "ingestion.crawl_corpus",
    "docling_intelligence": "ingestion.docling_intelligence",
    "document_normalization": "ingestion.document_normalization",
    "dense_cpu": "embeddings.embedder",
    "pgvector_dense": "vectorstore.vector_db",
    "index_lifecycle": "vectorstore.index_lifecycle",
    "qdrant_backup": "vectorstore.qdrant_backup",
    "corpus_sync": "vectorstore.corpus_sync",
    "point_ids": "vectorstore.point_ids",
    "postgres": "db.postgres",
    "database": "db.database",
    "models": "api.models",
    "routes": "api",
    "logging_config": "middleware.logger",
    "flags": "config.flags",
}

NEW_PACKAGES: dict[str, str] = {
    "rag": "Main RAG flow: query rewriting, retrieval, generation and grounding checks.",
    "ingestion": "Load, parse and chunk source documents into the corpus the index is built from.",
    "embeddings": "Generate embeddings for the dense retrieval leg.",
    "vectorstore": "Vector database operations: the dense leg, Qdrant lifecycle, snapshots and sync.",
    "db": "Persistence: the analytics store and its PostgreSQL backend.",
    "middleware": "Request-scoped HTTP concerns: structured, correlated request logging.",
    "config": "Application settings: the env-backed feature flag registry.",
}

# Old aliases that named the old module; bound to the renamed module they take its name.
WORD_RENAMES = {"service_module": "pipeline_module", "llm_module": "generator_module", "_llm_module": "_generator_module"}
ALIAS_RENAMES = {"svc": "pipeline"}

# Root tests import the package as App.backend.app as well as app.
PREFIXES = ("App.backend.app", "app")

TEXT_SUFFIXES = {
    ".py", ".md", ".mdx", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".sh", ".txt",
    ".json", ".ts", ".tsx", ".js", ".mjs", ".conf", ".example", ".env", ".rst",
}
TEXT_NAMES = {"Dockerfile", "Makefile", "CODEOWNERS", "Procfile"}


def app_prefix(dotted: str) -> str | None:
    return next((p for p in PREFIXES if dotted == p or dotted.startswith(p + ".")), None)


def mapped(dotted: str) -> str | None:
    """New absolute module for an absolute ``app.*`` name, or None when it did not move."""
    prefix = app_prefix(dotted)
    if prefix is None:
        return None
    parts = dotted[len(prefix) + 1:].split(".") if dotted != prefix else []
    for i in range(len(parts), 0, -1):
        head = ".".join(parts[:i])
        if head in MOVES:
            return ".".join([prefix, *MOVES[head].split("."), *parts[i:]])
    return None


def canonical(dotted: str) -> str:
    prefix = app_prefix(dotted)
    return "app" + dotted[len(prefix):] if prefix else dotted


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO, check=True, capture_output=True, text=True).stdout
    return [REPO / line for line in out.splitlines() if line]


def module_of(path: Path) -> str | None:
    try:
        rel = path.relative_to(APP.parent)
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def package_of(path: Path, module: str) -> str:
    return module if path.name == "__init__.py" else module.rpartition(".")[0]


def file_moves() -> dict[Path, Path]:
    """Every tracked file under a moved module, old path -> new path."""
    moves: dict[Path, Path] = {}
    for old, new in MOVES.items():
        src_dir, src_file = APP / old.replace(".", "/"), APP / (old.replace(".", "/") + ".py")
        if src_file.is_file():
            moves[src_file] = APP / (new.replace(".", "/") + ".py")
        elif src_dir.is_dir():
            for f in tracked_files():
                if src_dir in f.parents:
                    moves[f] = APP / new.replace(".", "/") / f.relative_to(src_dir)
    return moves


def relative(target: str, pkg: str) -> str:
    t, p = target.split("."), pkg.split(".")
    common = 0
    while common < min(len(t), len(p)) and t[common] == p[common]:
        common += 1
    return "." * (len(p) - common + 1) + ".".join(t[common:])


def resolve_relative(pkg: str, level: int, module: str | None) -> str:
    return importlib.util.resolve_name("." * level + (module or ""), pkg)


class Rules:
    """Text rewrites shared by every file type, built from the file moves."""

    def __init__(self, moves: dict[Path, Path], files: list[Path]) -> None:
        dotted: dict[str, str] = {}
        paths: dict[str, str] = {}
        bare: dict[str, str] = {}
        basenames = defaultdict(int)
        for f in files:
            if f.suffix == ".py":
                basenames[f.name] += 1
        for old, new in moves.items():
            old_mod, new_mod = module_of(old), module_of(new)
            if old.name != "__init__.py":  # app.routes is also FastAPI's route list
                dotted[old_mod] = new_mod
            old_rel, new_rel = old.relative_to(APP).as_posix(), new.relative_to(APP).as_posix()
            paths[f"app/{old_rel}"] = f"app/{new_rel}"
            if "/" not in old_rel and basenames[old.name] == 1:
                bare[old.name] = new_rel
        paths["app/routes/"] = "app/api/"
        self.dotted, self.paths, self.bare = dotted, paths, bare
        self.ambiguous_bare = sorted(o.name for o in moves if "/" not in o.relative_to(APP).as_posix() and o.name not in bare)
        self._dotted_re = self._alternation(dotted, r"(?<![\w.])", r"(?![\w])")
        self._dotted_prefixed_re = re.compile(r"(?<![\w.])App\.backend\.(" + "|".join(re.escape(k) for k in sorted(dotted, key=len, reverse=True)) + r")(?![\w])")
        self._word_re = re.compile(r"\b(" + "|".join(sorted(WORD_RENAMES, key=len, reverse=True)) + r")\b")
        self._path_re = self._alternation(paths, r"(?<![\w])", r"(?![\w])")
        self._bare_re = self._alternation(bare, r"(?<![\w/.-])", r"(?![\w])")
        self.old_basename = {module_of(n): module_of(o).rpartition(".")[2] for o, n in moves.items()}
        self.renamed = {n for n, old in self.old_basename.items() if n.rpartition(".")[2] != old}

    @staticmethod
    def _alternation(table: dict[str, str], before: str, after: str) -> re.Pattern[str]:
        keys = sorted(table, key=len, reverse=True)
        return re.compile(before + "(" + "|".join(re.escape(k) for k in keys) + ")" + after)

    def apply(self, text: str) -> str:
        text = self._path_re.sub(lambda m: self.paths[m.group(1)], text)
        text = self._dotted_prefixed_re.sub(lambda m: "App.backend." + self.dotted[m.group(1)], text)
        text = self._dotted_re.sub(lambda m: self.dotted[m.group(1)], text)
        text = self._word_re.sub(lambda m: WORD_RENAMES[m.group(1)], text)
        return self._bare_re.sub(lambda m: self.bare[m.group(1)], text)


def splice(text: str, edits: list[tuple[int, int, int, int, str]]) -> str:
    raw = text.encode()
    starts = [0]
    for line in raw.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    spans = sorted(((starts[a - 1] + b, starts[c - 1] + d, r) for a, b, c, d, r in edits), reverse=True)
    for (s, e, _), (ns, _, _) in zip(spans, spans[1:]):
        assert ns < s or ns == s == e, f"overlapping edits at byte {s}"
    for s, e, r in spans:
        raw = raw[:s] + r.encode() + raw[e:]
    return raw.decode()


_FROM = re.compile(rb"from\s+(\.*[\w.]*)\s*import")


class ImportRewriter:
    """Rewrite one Python file's imports for its new location and its targets' new locations."""

    def __init__(self, rules: Rules, moves: dict[Path, Path]) -> None:
        self.rules, self.moves = rules, moves
        self.report: dict[str, list[str]] = defaultdict(list)

    def rewrite(self, path: Path, text: str) -> str:
        old_mod = module_of(path)
        new_path = self.moves.get(path, path)
        in_app = old_mod is not None and (old_mod == "app" or old_mod.startswith("app."))
        old_pkg = package_of(path, old_mod) if in_app else None
        new_pkg = package_of(new_path, module_of(new_path)) if in_app else None
        tree = ast.parse(text)
        raw = text.encode()
        lines = raw.splitlines(keepends=True)
        edits: list[tuple[int, int, int, int, str]] = []
        renames: dict[str, str] = {}

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self._plain_import(node, edits, renames, path)
            elif isinstance(node, ast.ImportFrom):
                if node.level and not in_app:
                    continue
                absolute = resolve_relative(old_pkg, node.level, node.module) if node.level else node.module or ""
                if app_prefix(absolute) is None:
                    continue
                self._from_import(node, absolute, new_pkg, path in self.moves, lines, edits, renames, path)

        if edits:
            text = splice(text, edits)
        return self._rename_identifiers(path, text, renames) if renames else text

    def _plain_import(self, node: ast.Import, edits, renames, path) -> None:
        for alias in node.names:
            target = alias.name
            new_base = target.rpartition(".")[2]
            old_base = self.rules.old_basename.get(canonical(target))
            renamed = canonical(target) in self.rules.renamed
            if alias.asname and renamed and ALIAS_RENAMES.get(alias.asname) == new_base:
                old_base = alias.asname
            if alias.asname and old_base and alias.asname == old_base and old_base != new_base:
                if len(node.names) != 1:
                    self.report["manual: multi-name import aliasing a renamed module"].append(f"{path}:{node.lineno}")
                    continue
                parent = target.rpartition(".")[0]
                edits.append((node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, f"from {parent} import {new_base}"))
                renames[alias.asname] = new_base

    def _from_import(self, node, absolute, new_pkg, file_moved, lines, edits, renames, path) -> None:
        new_abs = mapped(absolute) or absolute
        groups: dict[str, list[tuple[ast.alias, str]]] = defaultdict(list)
        for alias in node.names:
            full = mapped(f"{absolute}.{alias.name}") if alias.name != "*" else None
            parent, name = full.rpartition(".")[::2] if full else (new_abs, alias.name)
            groups[parent].append((alias, name))

        def spell(target: str) -> str:
            return relative(target, new_pkg) if node.level else target

        changed_module = new_abs != absolute or (node.level and file_moved)
        if len(groups) == 1:
            (parent, members), = groups.items()
            if changed_module or parent != new_abs:
                line = lines[node.lineno - 1]
                m = _FROM.match(line, node.col_offset)
                if not m:
                    self.report["manual: unparseable from-import"].append(f"{path}:{node.lineno}")
                    return
                if spell(parent) != m.group(1).decode():
                    edits.append((node.lineno, m.start(1), node.lineno, m.end(1), spell(parent)))
            for alias, name in members:
                asname = alias.asname
                if asname in ALIAS_RENAMES and canonical(f"{parent}.{name}") in self.rules.renamed:
                    renames[asname] = ALIAS_RENAMES[asname]
                    asname = None if ALIAS_RENAMES[asname] == name else ALIAS_RENAMES[asname]
                elif name != alias.name and not asname:
                    renames[alias.name] = name
                if (name, asname) != (alias.name, alias.asname):
                    text = f"{name} as {asname}" if asname else name
                    edits.append((alias.lineno, alias.col_offset, alias.end_lineno, alias.end_col_offset, text))
            return

        indent = " " * node.col_offset
        statements = []
        for parent, members in groups.items():
            names = ", ".join(f"{n} as {a.asname}" if a.asname else n for a, n in members)
            statements.append(f"from {spell(parent)} import {names}")
            for alias, name in members:
                if name != alias.name and not alias.asname:
                    renames[alias.name] = name
        self.report["regenerated (check comments survived)"].append(f"{path}:{node.lineno}")
        edits.append((node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, ("\n" + indent).join(statements)))

    def _rename_identifiers(self, path: Path, text: str, renames: dict[str, str]) -> str:
        tree = ast.parse(text)
        methods = {id(m) for c in ast.walk(tree) if isinstance(c, ast.ClassDef) for m in c.body}
        taken: set[str] = set()
        params: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                taken.add(node.id)
            elif isinstance(node, ast.arg):
                params.add(node.arg)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and id(node) not in methods:
                taken.add(node.name)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                params.update(node.names)
        safe, unsafe = {}, {}
        for old, new in renames.items():
            if old in params or new in params or new in taken:
                fallback = f"{new}_module"
                if old in params or fallback in params or fallback in taken:
                    self.report[f"manual: rename {old}->{new} collides, imported as {old}"].append(str(path))
                    unsafe[new] = old
                else:
                    self.report[f"collision: {old} imported as {fallback}"].append(str(path))
                    unsafe[new] = fallback
                    safe[old] = fallback
            else:
                safe[old] = new
        edits = [
            (n.lineno, n.col_offset, n.end_lineno, n.end_col_offset, safe[n.id])
            for n in ast.walk(tree)
            if isinstance(n, ast.Name) and n.id in safe
        ]
        edits += [
            (a.lineno, a.col_offset, a.end_lineno, a.end_col_offset, f"{a.name} as {unsafe[a.name]}")
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom)
            for a in n.names
            if a.name in unsafe and a.asname is None
        ]
        return splice(text, edits) if edits else text


def is_text(path: Path) -> bool:
    if path.suffix not in TEXT_SUFFIXES and path.name not in TEXT_NAMES and not path.name.startswith("Dockerfile"):
        return False
    if "node_modules" in path.parts or path.suffix == ".json" and path.stat().st_size > 1_000_000:
        return False
    return path.is_file() and path != SELF


def run(apply: bool) -> int:
    files = tracked_files()
    moves = file_moves()
    rules = Rules(moves, files)
    rewriter = ImportRewriter(rules, moves)
    changed = 0
    for path in files:
        if not is_text(path):
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        new = rules.apply(text)
        if path.suffix == ".py":
            try:
                new = rewriter.rewrite(path, new)
            except SyntaxError:
                rewriter.report["skipped: syntax error"].append(str(path))
        if new != text:
            changed += 1
            if apply:
                path.write_text(new)
    print(f"{len(moves)} files move; {changed} files rewritten")
    for key, items in sorted(rewriter.report.items()):
        print(f"\n{key} ({len(items)}):")
        for item in items:
            print(f"  {item}")
    print(f"\nbare filenames left for review (not unique in the repo): {', '.join(rules.ambiguous_bare)}")
    if apply:
        _move(moves)
    return 0


def _move(moves: dict[Path, Path]) -> None:
    # File by file: a directory-level git mv nests the tree when a stale target dir exists.
    for old, new in moves.items():
        if old.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "mv", str(old), str(new)], cwd=REPO, check=True)
    for pkg, doc in NEW_PACKAGES.items():
        init = APP / pkg / "__init__.py"
        if not init.exists():
            init.write_text(f'"""{doc}"""\n')
            subprocess.run(["git", "add", str(init)], cwd=REPO, check=True)


@functools.cache
def _top_level(module_path: Path) -> list[ast.stmt]:
    return ast.parse(module_path.read_text()).body


@functools.cache
def _source(module_path: Path) -> str:
    return module_path.read_text()


@functools.cache
def _defines(module_path: Path, name: str) -> bool:
    for node in _top_level(module_path):
        targets: list[str] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            targets = [node.name]
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for t in node.targets if isinstance(node, ast.Assign) else [node.target]:
                targets += [n.id for n in ast.walk(t) if isinstance(n, ast.Name)]
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            targets = [(a.asname or a.name).split(".")[0] for a in node.names]
        if name in targets:
            return True
    # Names bound under if/try: a def, class, assignment or import of the name, never prose.
    n = re.escape(name)
    binding = rf"^\s*(?:(?:async\s+)?def\s+{n}\b|class\s+{n}\b|{n}\s*(?::[^=\n]*)?=(?!=)|(?:from\s+\S+\s+)?import\s+.*\b{n}\b)"
    return re.search(binding, _source(module_path), re.M) is not None


@functools.cache
def _module_file(dotted: str) -> Path | None:
    base = APP.parent / dotted.replace(".", "/")
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    return None


def verify() -> int:
    """Every app import and patch-target string resolves; no old path survives."""
    problems: list[str] = []
    old_dotted = re.compile(r"(?<![\w.])(?:App\.backend\.)?app\.(" + "|".join(re.escape(k) for k in MOVES if k != "routes") + r")(?![\w])")
    patch_target = re.compile(r"[\"'](?:App\.backend\.)?(app(?:\.\w+)+)[\"']")
    old_bindings = {o.rpartition(".")[2] for o, n in MOVES.items() if o.rpartition(".")[2] != n.rpartition(".")[2]} | set(WORD_RENAMES)
    for path in tracked_files():
        if not is_text(path):
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for m in old_dotted.finditer(text if path not in OLD_NAMES_ON_PURPOSE else ""):
            if not _module_file(canonical(m.group(0))):
                problems.append(f"{path.relative_to(REPO)}: stale {m.group(0)}")
        if path.suffix != ".py":
            continue
        mod = module_of(path)
        in_app = mod is not None and mod.startswith("app")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        bound: dict[str, Path] = {}  # local name -> the app module file it is bound to
        for node in ast.walk(tree):
            targets: list[tuple[str, str | None, str | None]] = []
            if isinstance(node, ast.Import):
                targets = [(canonical(a.name), None, a.asname) for a in node.names if app_prefix(a.name)]
            elif isinstance(node, ast.ImportFrom):
                if node.level and in_app:
                    absolute = resolve_relative(package_of(path, mod), node.level, node.module)
                elif node.level:
                    continue
                else:
                    absolute = node.module or ""
                if app_prefix(absolute):
                    targets = [(canonical(absolute), a.name, a.asname) for a in node.names]
            for module, name, asname in targets:
                target_file = _module_file(module)
                if name and _module_file(f"{module}.{name}"):
                    bound[asname or name] = _module_file(f"{module}.{name}")
                elif not name and asname and target_file:
                    bound[asname] = target_file
                if target_file is None:
                    problems.append(f"{path.relative_to(REPO)}:{node.lineno}: no module {module}")
                elif name and name != "*" and not _module_file(f"{module}.{name}") and not _defines(target_file, name):
                    problems.append(f"{path.relative_to(REPO)}:{node.lineno}: {module} has no {name}")
        for node in ast.walk(tree):  # module.llm where the module bound the old name
            if (isinstance(node, ast.Attribute) and node.attr in old_bindings and isinstance(node.value, ast.Name)
                    and node.value.id in bound and not _defines(bound[node.value.id], node.attr)):
                problems.append(f"{path.relative_to(REPO)}:{node.lineno}: {node.value.id}.{node.attr} no longer exists")
        for m in patch_target.finditer(text):
            parts = m.group(1).split(".")
            for i in range(len(parts), 1, -1):
                module_file = _module_file(".".join(parts[:i]))
                if module_file:
                    if i < len(parts) and not _defines(module_file, parts[i]) and not _module_file(".".join(parts[: i + 1])):
                        problems.append(f"{path.relative_to(REPO)}: {m.group(1)} has no {parts[i]}")
                    break
            else:
                problems.append(f"{path.relative_to(REPO)}: unresolved string {m.group(1)}")
    known = {p for p in set(problems) if re.sub(r":\d+: ", ": ", p) in KNOWN_FINDINGS}
    new = sorted(set(problems) - known)
    for line in sorted(known):
        print(f"known on dev before the move: {line}")
    for line in new:
        print(line)
    print(f"{len(new)} problems ({len(known)} known from before the move)")
    return 1 if new else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true", help="rewrite files and git mv them")
    group.add_argument("--verify", action="store_true", help="check every app import resolves")
    args = parser.parse_args()
    return verify() if args.verify else run(apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())

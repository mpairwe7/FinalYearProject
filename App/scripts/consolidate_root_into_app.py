#!/usr/bin/env python3
"""Move the root code and record folders under App/ and rewrite the paths to them (ADR-0064 phase C).

    python3 App/scripts/consolidate_root_into_app.py            # dry run: what would change
    python3 App/scripts/consolidate_root_into_app.py --apply    # move and rewrite

The rewrite works from the tree as it was before the move:

- a relative link or path (``](../docs/x.md)``, ``../monitoring/y``) is resolved
  from its file's old location and written again from the new one;
- a root-relative path (``governance/compliance_check.py``, ``"tests/**"``) gains
  ``App/``, except inside ``App/backend``, ``App/frontend`` and ``App/blog`` when
  the same path exists in that package (there ``scripts/`` means its own folder);
- a moved Python file's ``parents[n]``, ``.parent`` chain or nested ``dirname``
  that reached the repository root reaches it one level further up.

What it cannot decide (a ``ROOT / "docs"`` segment, a shell ``dirname "$0"/..``,
a bare folder word in a workflow) is reported for a person to fix. Rerunning
after ``--apply`` finds nothing to move and nothing to rewrite.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MOVED = (
    "docs",
    "tests",
    "scripts",
    "ml",
    "evals",
    "governance",
    "plugins",
    "monitoring",
    "infra",
    "threat-model",
)
PACKAGES = ("App/backend", "App/frontend", "App/blog")
TEXT_SUFFIXES = {
    ".md",
    ".mdx",
    ".rst",
    ".txt",
    ".py",
    ".pyi",
    ".sh",
    ".bash",
    ".yml",
    ".yaml",
    ".toml",
    ".cfg",
    ".ini",
    ".json",
    ".jsonl",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".html",
    ".css",
    ".tex",
    ".conf",
    ".env",
    ".example",
    ".tsv",
    ".csv",
    ".rules",
    ".gitignore",
    ".dockerignore",
    ".baseline",
}
TEXT_NAMES = {
    "Dockerfile",
    "Makefile",
    "CODEOWNERS",
    ".gitignore",
    ".dockerignore",
    ".semgrepignore",
    ".trivyignore",
    ".pre-commit-config.yaml",
    ".secrets.baseline",
    "Procfile",
}
SKIP_PREFIXES = (
    "Data/",
    "App/Data/",
    "Results/",
    "Artifacts/",
)  # corpora and generated output: never rewritten
GROUP = "|".join(re.escape(m) for m in MOVED)
LINK = re.compile(r"(\]\(|\bsrc=\"|\bhref=\")(?P<target>[^)\"\s#?]+)(?P<rest>[^)\"\s]*)")
RELATIVE = re.compile(r"(?<![\w./@$-])(?P<target>(?:\.\.?/)+[\w@+.-][\w@+./-]*)")
ROOTED = re.compile(rf"(?<![\w./@$~-])(?P<target>(?:{GROUP})/(?:[\w@+.*/-]|\\\.)*)")
PARENTS = re.compile(r"Path\(__file__\)(?P<resolve>\.resolve\(\))?\.parents\[(?P<n>\d+)\]")
PARENT_CHAIN = re.compile(
    r"Path\(__file__\)(?P<resolve>\.resolve\(\))?(?P<chain>(?:\.parent)+)(?![\w\[])"
)
DIRNAME = re.compile(r"((?:os\.path\.dirname\()+)(os\.path\.(?:abspath|realpath)\(__file__\))(\)+)")
UNDECIDED = [
    ("root segment", re.compile(rf"(?<![\"']App[\"'] )/\s*[\"'](?:{GROUP})[\"']")),
    ("shell parent", re.compile(r"dirname\s+\"?\$\{?(?:0|BASH_SOURCE)")),
    ("bare folder word", re.compile(rf"(?<![\w./@$-])(?:{GROUP})(?![\w/.-])")),
]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout


def say(line: str) -> None:
    sys.stdout.write(line + "\n")


def tracked() -> list[str]:
    return [p for p in _git("ls-files", "-z").split("\0") if p]


def new_path(old: str) -> str:
    first = old.split("/", 1)[0]
    return f"App/{old}" if first in MOVED else old


def _is_text(path: str) -> bool:
    name = posixpath.basename(path)
    return (
        name in TEXT_NAMES
        or posixpath.splitext(name)[1] in TEXT_SUFFIXES
        or name.startswith("Dockerfile")
    )


class OldTree:
    """Every tracked file and folder as it was before the move."""

    def __init__(self, files: list[str]) -> None:
        self.files = set(files)
        self.dirs = {posixpath.dirname(f) for f in files}
        for d in list(self.dirs):
            while d:
                d = posixpath.dirname(d)
                self.dirs.add(d)

    def has(self, path: str) -> bool:
        path = path.rstrip("/")
        return path in self.files or path in self.dirs


def _relink(target: str, old_file: str, new_file: str, tree: OldTree) -> str | None:
    """A relative target written from new_file, or None when it needs no change."""
    trailing = "/" if target.endswith("/") else ""
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(old_file), target))
    if resolved.startswith("..") or not tree.has(resolved):
        return None
    moved = new_path(resolved)
    rel = posixpath.relpath(moved, posixpath.dirname(new_file) or ".")
    if target.startswith(("./", "../")) and not rel.startswith("../"):
        rel = "./" + rel
    rel += trailing
    return None if rel == target else rel


def _package_of(path: str) -> str | None:
    return next((p for p in PACKAGES if path.startswith(p + "/")), None)


def _rooted(target: str, old_file: str, new_file: str, tree: OldTree) -> str | None:
    """A bare `docs/x` gains App/ only when it names the root folder, not one beside the file."""
    glob = "*" in target
    head, plain = target.split("/", 1)[0], target.replace("\\.", ".").rstrip("/")
    if not tree.has(head):
        return None
    if not glob and not tree.has(plain) and plain != head:
        return None
    if posixpath.dirname(old_file) and tree.has(posixpath.join(posixpath.dirname(old_file), plain)):
        return None  # relative to the file's own folder
    package = _package_of(new_file)
    if package and tree.has(f"{package}/{plain}"):
        return None  # the package's own folder of that name
    return f"App/{target}"


STEP_START = re.compile(r"^(?P<indent>\s*)-\s+(?:name|uses|run|id):")
WORKDIR = re.compile(r"^\s*working-directory:\s*[\"']?\.?/?(?P<dir>[\w./-]+?)/?[\"']?\s*$")
DEFAULTS = re.compile(r"^(?P<indent>\s*)defaults:\s*$")


def workflow_dirs(lines: list[str]) -> list[str | None]:
    """Each line's working directory: its step's `working-directory`, else its job's `defaults`."""
    dirs: list[str | None] = [None] * len(lines)
    job_default: str | None = None
    starts = [i for i, line in enumerate(lines) if STEP_START.match(line)] + [len(lines)]
    for i, line in enumerate(lines):
        if re.match(r"^  [\w-]+:\s*$", line):  # a new job
            job_default = None
        if DEFAULTS.match(line):
            for nxt in lines[i + 1 : i + 4]:
                m = WORKDIR.match(nxt)
                if m:
                    job_default = m.group("dir")
        dirs[i] = job_default
    for a, b in zip(starts, starts[1:], strict=False):
        step_dir = next((m.group("dir") for m in map(WORKDIR.match, lines[a:b]) if m), None)
        if step_dir:
            for i in range(a, b):
                dirs[i] = step_dir
    return dirs


REPO_URL = re.compile(
    r"(?P<base>github\.com/mpairwe7/FinalYearProject/(?:blob|tree|raw)/[\w.-]+/)(?P<path>[\w@+./-]+)"
)


def rewrite_text(text: str, old_file: str, new_file: str, tree: OldTree) -> tuple[str, int]:
    count = 0

    def repo_url(m: re.Match[str]) -> str:
        nonlocal count
        path = m.group("path")
        core = path.rstrip(".")
        if core.split("/", 1)[0] not in MOVED or not tree.has(core):
            return m.group(0)
        count += 1
        return m.group("base") + "App/" + path

    text = REPO_URL.sub(repo_url, text)

    def link(m: re.Match[str]) -> str:
        nonlocal count
        target = m.group("target")
        if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("/"):
            return m.group(0)
        new = _relink(target, old_file, new_file, tree)  # a link is relative to its file
        if new is None:
            return m.group(0)
        count += 1
        return m.group(1) + new + m.group("rest")

    def relative(m: re.Match[str]) -> str:
        nonlocal count
        target = m.group("target")
        core = target.rstrip(".")  # a full stop ending a sentence is not part of the path
        new = _relink(core, old_file, new_file, tree)
        if new is None:
            return m.group(0)
        count += 1
        return new + target[len(core) :]

    def rooted(m: re.Match[str]) -> str:
        nonlocal count
        target = m.group("target")
        core = target.rstrip(".")
        new = _rooted(core, old_file, new_file, tree)
        if new is None:
            return m.group(0)
        count += 1
        return new + target[len(core) :]

    if posixpath.splitext(new_file)[1] in {".md", ".mdx", ".html", ".rst"}:
        text = LINK.sub(link, text)
        # Prose and code spans outside links are root-relative by this repo's convention.
        parts = re.split(r"(\]\([^)]*\))", text)
        text = "".join(
            p if p.startswith("](") else ROOTED.sub(rooted, RELATIVE.sub(relative, p))
            for p in parts
        )
    elif new_file.startswith(".github/workflows/"):
        lines = text.split("\n")
        for i, (line, workdir) in enumerate(zip(lines, workflow_dirs(lines), strict=True)):
            if workdir and not workdir.startswith(MOVED):
                # Relative to the step's folder, which did not move: a bare `tests/` is that folder's own.
                lines[i] = ROOTED.sub(
                    lambda m, w=workdir: (
                        m.group(0)
                        if tree.has(
                            f"{w}/{m.group('target').rstrip('.').split('*', 1)[0].rstrip('/')}"
                        )
                        else rooted(m)
                    ),
                    line,
                )
            else:
                lines[i] = ROOTED.sub(rooted, line)
        text = "\n".join(lines)
    else:
        text = RELATIVE.sub(relative, text)
        text = ROOTED.sub(rooted, text)
    return text, count


def fix_depth(text: str, old_file: str) -> tuple[str, int]:
    """In a moved Python file, a parent reference that reached the repository root goes one level further."""
    depth = old_file.count("/")  # folders between the root and the file: parents[depth] is the root
    count = 0

    def parents(m: re.Match[str]) -> str:
        nonlocal count
        n = int(m.group("n"))
        if n != depth:
            return m.group(0)
        count += 1
        return f"Path(__file__){m.group('resolve') or ''}.parents[{n + 1}]"

    def chain(m: re.Match[str]) -> str:
        nonlocal count
        hops = m.group("chain").count(".parent")
        if hops != depth + 1:
            return m.group(0)
        count += 1
        return f"Path(__file__){m.group('resolve') or ''}{m.group('chain')}.parent"

    def dirname(m: re.Match[str]) -> str:
        nonlocal count
        hops = m.group(1).count("os.path.dirname(")
        closing = m.group(3)
        if hops != depth + 1 or len(closing) < hops:
            return m.group(0)
        count += 1
        return "os.path.dirname(" + m.group(1) + m.group(2) + closing[:hops] + ")" + closing[hops:]

    text = PARENTS.sub(parents, text)
    text = PARENT_CHAIN.sub(chain, text)
    text = DIRNAME.sub(dirname, text)
    return text, count


ASSIGN = re.compile(
    r"^\s*(?P<var>[A-Za-z_]\w*)\s*(?::\s*Path)?\s*=\s*(?P<expr>.+?)\s*(?:#.*)?$", re.MULTILINE
)
FILE_BASE = re.compile(r"^Path\(__file__\)(?:\.resolve\(\))?")
STEP = re.compile(r"\.parents\[(\d+)\]|\.parent\b|\s*/\s*[\"']([^\"']+)[\"']")


def _walk(expr: str, known: dict[str, str], file_dir: str) -> str | None:
    """The repo-relative folder a Path expression names, or None when it is not a plain one."""
    base = FILE_BASE.match(expr)
    if base:
        current, rest = file_dir, expr[base.end() :]
        current = posixpath.join(
            current, "__file__"
        )  # Path(__file__) is the file; .parent is its folder
    else:
        head = re.match(r"^[A-Za-z_]\w*", expr)
        if not head or head.group(0) not in known:
            return None
        current, rest = known[head.group(0)], expr[head.end() :]
    pos = 0
    while pos < len(rest):
        m = STEP.match(rest, pos)
        if not m:
            return None
        if m.group(1) is not None:
            for _ in range(int(m.group(1)) + 1):
                current = posixpath.dirname(current)
        elif m.group(0).startswith(".parent"):
            current = posixpath.dirname(current)
        else:
            current = (
                posixpath.normpath(posixpath.join(current, m.group(2))) if current else m.group(2)
            )
        pos = m.end()
    return "" if current in (".", "") else current


def fix_root_segments(text: str, new_file: str) -> tuple[str, int]:
    """`ROOT / "docs"` gains `"App" /` when ROOT, traced to __file__ in this file, is the repository root."""
    file_dir = posixpath.dirname(new_file)
    known: dict[str, str] = {}
    for m in ASSIGN.finditer(text):
        where = _walk(m.group("expr"), known, file_dir)
        if where is not None:
            known[m.group("var")] = where
    roots = sorted(v for v, where in known.items() if where == "")
    count = 0
    if roots:
        pattern = re.compile(
            rf"\b(?P<var>{'|'.join(map(re.escape, roots))})\s*/\s*(?P<q>[\"'])(?P<seg>{GROUP})(?P=q)"
        )
        text, n = pattern.subn(
            lambda m: f'{m.group("var")} / "App" / {m.group("q")}{m.group("seg")}{m.group("q")}',
            text,
        )
        count += n
    inline = re.compile(
        rf"(?P<base>Path\(__file__\)(?:\.resolve\(\))?\.parents\[(?P<n>\d+)\])\s*/\s*(?P<q>[\"'])(?P<seg>{GROUP})(?P=q)"
    )

    def inline_fix(m: re.Match[str]) -> str:
        nonlocal count
        if _walk(m.group("base"), {}, file_dir) != "":
            return m.group(0)
        count += 1
        return f'{m.group("base")} / "App" / {m.group("q")}{m.group("seg")}{m.group("q")}'

    text = inline.sub(inline_fix, text)
    return text, count


# What the rewrite cannot infer, written out: (file, old, new, expected count of old).
HAND_EDITS: list[tuple[str, str, str, int]] = [
    # ml and plugins now sit under App/ in a checkout; the image keeps them where they were.
    (
        "App/backend/app/_root.py",
        "PROJECT_ROOT = _resolve()\n",
        "PROJECT_ROOT = _resolve()\n"
        "#: The folder holding the ``ml`` and ``plugins`` packages: ``App/`` in a checkout\n"
        "#: (ADR-0064), the project root in the image, which copies ``ml`` beside the app.\n"
        'CODE_ROOT = PROJECT_ROOT / "App" if (PROJECT_ROOT / "App").is_dir() else PROJECT_ROOT\n',
        1,
    ),
    (
        "App/backend/app/speech_service.py",
        "# Ensure the project root is on sys.path so `ml.scripts.*` imports resolve.\nfrom ._root import PROJECT_ROOT as _PROJECT_ROOT_P",
        "# Ensure the folder holding `ml` is on sys.path so `ml.scripts.*` imports resolve.\nfrom ._root import CODE_ROOT as _PROJECT_ROOT_P",
        1,
    ),
    (
        "App/backend/app/plugins/__init__.py",
        "# Ensure repo root is in sys.path so plugins package is accessible\nfrom .._root import PROJECT_ROOT\n\n"
        "if str(PROJECT_ROOT) not in sys.path:\n    sys.path.insert(0, str(PROJECT_ROOT))",
        "# Ensure the folder holding the plugins package is on sys.path\nfrom .._root import CODE_ROOT\n\n"
        "if str(CODE_ROOT) not in sys.path:\n    sys.path.insert(0, str(CODE_ROOT))",
        1,
    ),
    (
        "App/backend/app/tools/plugins.py",
        "from .._root import PROJECT_ROOT as _root",
        "from .._root import CODE_ROOT as _root",
        1,
    ),
    ("pyproject.toml", 'testpaths = ["tests"]', 'testpaths = ["App/tests"]', 1),
    ("pyproject.toml", 'source = ["ml", "App/backend"]', 'source = ["App/ml", "App/backend"]', 1),
    (".github/workflows/ci-ml-pipeline.yml", "--cov=ml ", "--cov=App/ml ", 1),
    (
        ".github/workflows/ci-ml-pipeline.yml",
        "PYTHONPATH: .:${{ github.workspace }}\n",
        "PYTHONPATH: .:${{ github.workspace }}/App\n",
        3,
    ),
    (
        ".github/workflows/devsecops-sast-dast.yml",
        "sys.path.insert(0, 'threat-model')",
        "sys.path.insert(0, 'App/threat-model')",
        2,
    ),
    # Shell scripts that found the root one folder above their own now sit one deeper.
    (
        "App/scripts/manage_ngrok.sh",
        'ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"',
        'ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"',
        1,
    ),
    (
        "App/scripts/prototype.sh",
        'ROOT="$(cd "$(dirname "$0")/.." && pwd)"',
        'ROOT="$(cd "$(dirname "$0")/../.." && pwd)"',
        1,
    ),
    (
        "App/scripts/reindex.sh",
        'PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"',
        'PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"',
        1,
    ),
    # A hard-coded absolute REPO_ROOT the tracer cannot follow.
    (
        "App/scripts/benchmark_50000_cx_multimodal_ngrok.py",
        'REPO_ROOT / "docs" /',
        'REPO_ROOT / "App" / "docs" /',
        1,
    ),
]


def hand_edits(apply: bool) -> int:
    applied = 0
    for rel, old, new, expected in HAND_EDITS:
        path = REPO / rel
        text = path.read_text(encoding="utf-8")
        if text.count(old) == 0 and new in text:
            continue  # already applied
        assert text.count(old) == expected, (
            f"{rel}: expected {expected} of {old[:60]!r}, found {text.count(old)}"
        )
        applied += 1
        if apply:
            path.write_text(text.replace(old, new), encoding="utf-8")
    return applied


def move(apply: bool) -> list[tuple[str, str]]:
    planned = []
    for folder in MOVED:
        src, dst = REPO / folder, REPO / "App" / folder
        if not src.exists():
            continue
        entries = sorted({p.split("/", 2)[1] for p in tracked() if p.startswith(folder + "/")})
        for entry in entries:
            planned.append((f"{folder}/{entry}", f"App/{folder}/{entry}"))
            # `git mv a b` into an existing b nests a inside it (stray ignored files can make one).
            if (dst / entry).exists():
                raise SystemExit(f"App/{folder}/{entry} already exists; remove it before the move")
            if apply:
                dst.mkdir(parents=True, exist_ok=True)
                _git("mv", f"{folder}/{entry}", f"App/{folder}/{entry}")
        if apply:  # ignored files (fonts, local outputs) follow; caches are dropped
            for leftover in sorted(src.rglob("*"), reverse=True):
                if "__pycache__" in leftover.parts:
                    continue
                if leftover.is_file():
                    target = dst / leftover.relative_to(src)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        shutil.move(str(leftover), str(target))
            shutil.rmtree(src, ignore_errors=True)
    return planned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    old_files = tracked()
    tree = OldTree(old_files)
    if not any(f.split("/", 1)[0] in MOVED for f in old_files):
        say("nothing to move: every folder is already under App/")
        return 0

    planned = move(args.apply)
    rewrites, depth_fixes, undecided = {}, {}, []
    for old in old_files:
        new = new_path(old)
        if old.startswith(SKIP_PREFIXES) or not _is_text(old):
            continue
        path = REPO / (new if args.apply else old)
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        updated, n = rewrite_text(text, old, new, tree)
        if new != old and old.endswith(".py"):
            updated, d = fix_depth(updated, old)
            if d:
                depth_fixes[new] = d
        if old.endswith(".py"):
            updated, r = fix_root_segments(updated, new)
            if r:
                rewrites[new] = rewrites.get(new, 0) + r
        if n:
            rewrites[new] = n
        if updated != text and args.apply:
            path.write_text(updated, encoding="utf-8")
        for line_no, line in enumerate(updated.splitlines(), 1):
            for label, pattern in UNDECIDED:
                if label == "bare folder word" and not (
                    new.startswith(".github/")
                    or new in {"pyproject.toml", ".pre-commit-config.yaml"}
                ):
                    continue
                if pattern.search(line):
                    undecided.append(f"{new}:{line_no}: {label}: {line.strip()[:120]}")

    edits = hand_edits(args.apply) if args.apply else len(HAND_EDITS)
    say(f"moves: {len(planned)} entries from {len({p[0].split('/')[0] for p in planned})} folders")
    say(f"hand edits: {edits}")
    say(f"path rewrites: {sum(rewrites.values())} in {len(rewrites)} files")
    say(f"depth fixes: {sum(depth_fixes.values())} in {len(depth_fixes)} files")
    say(f"for a person to check: {len(undecided)}")
    for item in undecided:
        say("  " + item)
    if not args.apply:
        say("dry run: nothing changed (pass --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

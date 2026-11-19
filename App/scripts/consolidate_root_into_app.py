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
import hashlib
import os
import posixpath
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve().relative_to(REPO).as_posix()  # its own table must not be rewritten
#: Each entry moves a root folder ("x/") or file to its place (ADR-0064). Phase C's
#: folders have moved already; their entries are kept so a rerun stays a no-op.
SECURITY = ("trivy.yaml", ".trivyignore", "trufflehog-config.yml", ".trufflehog-exclude-paths.txt",
            ".gitleaks.toml", ".bandit.yaml", ".checkov.yaml", ".zap-rules.tsv", ".secrets.baseline")
MOVES: list[tuple[str, str]] = [
    *[(f"{d}/", f"App/{d}/") for d in ("docs", "tests", "scripts", "ml", "evals", "governance",
                                       "plugins", "monitoring", "infra", "threat-model")],
    ("Data/", "App/Data/"), ("Results/", "App/Results/"), ("Artifacts/", "App/Artifacts/"),
    ("Model/", "App/Model/"), (".semgrep/", "App/security/.semgrep/"), (".security/", "App/security/"),
    ("Dockerfile", "App/Dockerfile"), ("Dockerfile.gpu", "App/Dockerfile.gpu"),
    ("Dockerfile.plugins", "App/Dockerfile.plugins"), ("docker-compose.yml", "App/docker-compose.full.yml"),
    ("docker-compose.plugins.yml", "App/docker-compose.plugins.yml"),
    ("docker-compose.mcp.yml", "App/docker-compose.mcp.yml"), ("Caddyfile", "App/Caddyfile"),
    ("requirements.txt", "App/requirements.txt"), ("requirements-onnx-export.txt", "App/requirements-onnx-export.txt"),
    ("pyproject.toml", "App/pyproject.toml"), (".env.example", "App/.env.example"),
    ("SECURITY.md", ".github/SECURITY.md"), ("CONTRIBUTING.md", ".github/CONTRIBUTING.md"),
    ("CODEOWNERS", ".github/CODEOWNERS"),
    *[(f, f"App/security/{f}") for f in SECURITY],
]
DELETES: tuple[str, ...] = ()
MOVED = tuple(src[:-1] for src, _ in MOVES if src.endswith("/"))
MOVED_FILES = {src: dst for src, dst in MOVES if not src.endswith("/")}
#: Names that also exist inside App/backend, App/frontend or App/blog: bare, they are
#: the root file only where the root is the frame (workflows, root files, moved files).
AMBIGUOUS = {"Dockerfile", "requirements.txt", "pyproject.toml", "docker-compose.yml"}
COMPOSE = {"docker-compose.yml", "docker-compose.plugins.yml", "docker-compose.mcp.yml"}
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
    "Caddyfile",
}
SKIP_PREFIXES = ("Data/", "App/Data/", "Results/", "Artifacts/")  # corpora and outputs: never rewritten
GROUP = "|".join(re.escape(m) for m in MOVED)
LINK = re.compile(r"(\]\(|\bsrc=\"|\bhref=\")(?P<target>[^)\"\s#?]+)(?P<rest>[^)\"\s]*)")
RELATIVE = re.compile(r"(?<![\w./@$-])(?P<target>(?:\.\.?/)+[\w@+.-][\w@+./-]*)")
ROOTED = re.compile(rf"(?<![\w./@$~-])(?P<target>(?:{GROUP})/(?:[\w@+.*/-]|\\\.)*)")
FILE_GROUP = "|".join(re.escape(f) for f in sorted(MOVED_FILES, key=len, reverse=True))
FILE_ROOTED = re.compile(rf"(?<![\w./@$~-])(?P<target>{FILE_GROUP})(?![\w.-])")
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
    for src, dst in MOVES:
        if src.endswith("/"):
            if old == src[:-1]:
                return dst[:-1]
            if old.startswith(src):
                return dst + old[len(src) :]
        elif old == src:
            return dst
    return old


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
    return new_path(head + "/")[:-1] + target[len(head) :]


def _file_rooted(target: str, old_file: str, new_file: str, tree: OldTree, workdir: str | None = None) -> str | None:
    """A bare root file name (`trivy.yaml`, `.env.example`) maps through the table."""
    if target not in MOVED_FILES or not tree.has(target):
        return None
    if workdir and tree.has(f"{workdir}/{target}"):
        return None  # the step's own folder has one
    folder = posixpath.dirname(old_file)
    if folder and tree.has(posixpath.join(folder, target)):
        return None  # a file of that name beside this one
    package = _package_of(new_file)
    if package and tree.has(f"{package}/{target}"):
        return None
    # The threat model and the governance registry cite evidence as repository paths.
    root_frame = (
        not folder
        or old_file in MOVED_FILES
        or new_file.startswith((".github/", "App/threat-model/", "App/governance/"))
    )
    if target in AMBIGUOUS and not root_frame:
        return None  # left for a person: it may mean a package's own file
    return MOVED_FILES[target]


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


COMPOSE_KEY = re.compile(r"^(?P<lead>\s*(?:-\s+)?(?:context|env_file|file|source|dockerfile):\s*)(?P<q>[\"']?)(?P<path>[^\"'\s#]+)(?P=q)(?P<rest>.*)$")
COMPOSE_VOLUME = re.compile(r"^(?P<lead>\s*-\s+)(?P<q>[\"']?)(?P<host>\.{1,2}(?:/[^:\"']*)?|\.[^:\"']*)(?P<tail>:[^\"']*)(?P=q)(?P<rest>.*)$")
COMPOSE_LIST_ITEM = re.compile(r"^(?P<lead>\s*-\s+)(?P<q>[\"']?)(?P<path>\.{1,2}/[^:\"'\s]*)(?P=q)\s*$")


def _rebase(path: str, base: str = "App") -> str:
    """A path written from the repository root, written again from `base`."""
    if path.startswith(("/", "$", "~")) or "://" in path:
        return path
    target = new_path(posixpath.normpath(path))
    rel = posixpath.relpath(target, base)
    return rel if rel.startswith("..") or rel == "." else "./" + rel


def rebase_compose(text: str, tree: OldTree) -> tuple[str, int]:
    """A Compose file moved from the root to App/: every relative path is now read from App/."""
    lines, count, context = text.split("\n"), 0, None
    for i, line in enumerate(lines):
        m = COMPOSE_KEY.match(line)
        if m:
            key = m.group("lead").strip(" -").rstrip(": ")
            path = m.group("path")
            if key == "context":
                context = path
                new = _rebase(path)
            elif key == "dockerfile":
                # Relative to the build context: only a root context sees a moved root file.
                new = MOVED_FILES.get(path, path) if context in (".", "./") else path
            else:
                new = _rebase(path)
            if new != path:
                lines[i] = f"{m.group('lead')}{m.group('q')}{new}{m.group('q')}{m.group('rest')}"
                count += 1
            continue
        m = COMPOSE_VOLUME.match(line) or COMPOSE_LIST_ITEM.match(line)
        if m:
            host = m.group("host") if "host" in m.groupdict() else m.group("path")
            new = _rebase(host)
            if new != host:
                tail = m.group("tail") if "tail" in m.groupdict() else ""
                rest = m.group("rest") if "rest" in m.groupdict() else ""
                lines[i] = f"{m.group('lead')}{m.group('q')}{new}{tail}{m.group('q')}{rest}"
                count += 1
    return "\n".join(lines), count


QUOTED_PATH = re.compile(r"(?P<q>[\"'])(?P<path>[\w.][\w@+.*/-]*/[\w@+.*/-]*)(?P=q)")


def rebase_quoted(text: str, base: str) -> str:
    """pyproject.toml moved into `base`: its root-relative path strings are now read from there."""

    def fix(m: re.Match[str]) -> str:
        path = m.group("path")
        if path.startswith(base + "/"):
            return f"{m.group('q')}{path[len(base) + 1 :]}{m.group('q')}"
        if new_path(path) != path and new_path(path).startswith(base + "/"):
            return f"{m.group('q')}{new_path(path)[len(base) + 1 :]}{m.group('q')}"
        return m.group(0)

    return QUOTED_PATH.sub(fix, text)


ANCHORED_FILES = {".gitignore", ".dockerignore", "CODEOWNERS"}


def rebase_anchored(text: str) -> tuple[str, int]:
    """An anchored `/ml/` pattern (CODEOWNERS, ignore files) names a root path too."""
    pattern = re.compile(rf"(?m)(?<![\w.~-])/(?P<path>(?:{GROUP})(?=/|\s|$)[^\s]*|(?:{FILE_GROUP})(?![\w.-]))")
    count = 0

    def fix(m: re.Match[str]) -> str:
        nonlocal count
        path = m.group("path")
        head = path.split("/", 1)[0]
        mapped = new_path(path) if path in MOVED_FILES else new_path(head + "/")[:-1] + path[len(head) :]
        if mapped == path:
            return m.group(0)
        count += 1
        return "/" + mapped

    return pattern.sub(fix, text), count


QUOTED_PREFIX = re.compile(r"(?<=[\"'])(?P<head>Data|Results|Model|Artifacts)/(?P<second>[\w.-]+)(?=/[^\"'\n]* )")


REPO_URL = re.compile(
    r"(?P<base>github\.com/mpairwe7/FinalYearProject/(?:blob|tree|raw)/[\w.-]+/)(?P<path>[\w@+./-]+)"
)


def rewrite_text(text: str, old_file: str, new_file: str, tree: OldTree) -> tuple[str, int]:
    count = 0

    def repo_url(m: re.Match[str]) -> str:
        nonlocal count
        path = m.group("path")
        core = path.rstrip(".")
        if new_path(core) == core or not tree.has(core):
            return m.group(0)
        count += 1
        return m.group("base") + new_path(core) + path[len(core) :]

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

    def quoted_prefix(m: re.Match[str]) -> str:
        nonlocal count
        head = m.group("head")
        second = m.group("second")
        if not tree.has(f"{head}/{second}"):
            return m.group(0)
        count += 1
        return new_path(head + "/")[:-1] + "/" + second

    def file_rooted(m: re.Match[str], workdir: str | None = None) -> str:
        nonlocal count
        new = _file_rooted(m.group("target"), old_file, new_file, tree, workdir)
        if new is None:
            return m.group(0)
        count += 1
        return new

    if old_file in COMPOSE:
        return rebase_compose(text, tree)
    if posixpath.basename(old_file) in ANCHORED_FILES or old_file.endswith(".dockerignore"):
        text, anchored = rebase_anchored(text)
        count += anchored

    if posixpath.splitext(new_file)[1] in {".md", ".mdx", ".html", ".rst"}:
        text = LINK.sub(link, text)
        # Prose and code spans outside links are root-relative by this repo's convention.
        parts = re.split(r"(\]\([^)]*\))", text)
        text = "".join(
            p if p.startswith("](") else FILE_ROOTED.sub(file_rooted, ROOTED.sub(rooted, RELATIVE.sub(relative, p)))
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
                lines[i] = FILE_ROOTED.sub(lambda m, w=workdir: file_rooted(m, w), lines[i])
            else:
                lines[i] = FILE_ROOTED.sub(file_rooted, ROOTED.sub(rooted, line))
        text = "\n".join(lines)
    else:
        text = RELATIVE.sub(relative, text)
        text = FILE_ROOTED.sub(file_rooted, ROOTED.sub(rooted, text))
        text = QUOTED_PREFIX.sub(quoted_prefix, text)
        if old_file == "pyproject.toml":
            text = rebase_quoted(text, "App")
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
    seg_group = f"{GROUP}|{FILE_GROUP}"

    def parts(seg: str, q: str) -> str:
        new = MOVED_FILES.get(seg) or new_path(seg + "/")[:-1]
        return " / ".join(f"{q}{part}{q}" for part in new.split("/"))

    if roots:
        pattern = re.compile(
            rf"\b(?P<var>{'|'.join(map(re.escape, roots))})\s*/\s*(?P<q>[\"'])(?P<seg>{seg_group})(?P=q)"
        )
        text, n = pattern.subn(lambda m: f'{m.group("var")} / {parts(m.group("seg"), m.group("q"))}', text)
        count += n
    inline = re.compile(
        rf"(?P<base>Path\(__file__\)(?:\.resolve\(\))?\.parents\[(?P<n>\d+)\])\s*/\s*(?P<q>[\"'])(?P<seg>{seg_group})(?P=q)"
    )

    def inline_fix(m: re.Match[str]) -> str:
        nonlocal count
        if _walk(m.group("base"), {}, file_dir) != "":
            return m.group(0)
        count += 1
        return f'{m.group("base")} / {parts(m.group("seg"), m.group("q"))}'

    text = inline.sub(inline_fix, text)
    return text, count


# What the rewrite cannot infer, written out per phase: (file, old, new, expected count).
# A phase's edits run only while that phase still has something to move, so a later
# phase never sees an earlier phase's text come back and edits it twice.
PHASE_SOURCES = {
    "C": tuple(src for src, _ in MOVES[:10]),
    "D": tuple(src for src, _ in MOVES[10:]) + DELETES,
}
HAND_EDITS: dict[str, list[tuple[str, str, str, int]]] = {"C": [
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
], "D": [
    # The corpora now live in App/Data: the build no longer seeds a second copy there.
    (
        ".github/workflows/ura-chatbot-build-push.yml",
        "          # App/Data is the serving corpus root; seed it from the tracked copies.\n"
        "          mkdir -p ../Data/dataset ../Data/teacher_qa ../Data/pdfs\n"
        '          cp -f "$GITHUB_WORKSPACE"/Data/dataset/*.csv ../Data/dataset/\n'
        '          cp -f "$GITHUB_WORKSPACE"/Data/teacher_qa/*.jsonl ../Data/teacher_qa/\n',
        "          # App/Data is the serving corpus root and holds the tracked corpora (ADR-0064).\n",
        1,
    ),
    (
        ".github/workflows/ura-chatbot-build-push.yml",
        '            cp -f "$GITHUB_WORKSPACE"/Data/pdfs/*.pdf ../Data/pdfs/\n',
        "",
        1,
    ),
    # The corpus and model now live under App/: APP_DATA_ROOT and CODE_ROOT already name
    # them in a checkout and in the image (/app/Data, /app/Model).
    ("App/backend/app/ingestion/indexer.py",
     "# Tracked PDFs live at the repository root (`App/Data/pdfs`), matching `App/Data/crawl/pages`.\n"
     "_DEFAULT_PDF_DIR = (\n"
     '    _PROJECT_ROOT / "Data" / "pdfs"\n'
     '    if (_PROJECT_ROOT / "Data" / "pdfs").is_dir()\n'
     '    else _APP_DATA_ROOT / "pdfs"\n'
     ")\n",
     "# The tracked PDFs and the crawl live in App/Data with the derived corpora (ADR-0064).\n"
     '_DEFAULT_PDF_DIR = _APP_DATA_ROOT / "pdfs"\n', 1),
    ("App/backend/app/ingestion/indexer.py",
     "# The crawl lives at the repository root (it is committed), not under App/Data.\n"
     'CRAWL_PAGES_DIR = Path(os.getenv("CRAWL_PAGES_DIR", str(_PROJECT_ROOT / "Data" / "crawl" / "pages")))',
     'CRAWL_PAGES_DIR = Path(os.getenv("CRAWL_PAGES_DIR", str(_APP_DATA_ROOT / "crawl" / "pages")))', 1),
    ("App/backend/app/publications.py",
     '    for candidate in (\n'
     '        PROJECT_ROOT / "Data" / "eval" / "publications_fixture.txt",\n'
     '        APP_DATA_ROOT / "eval" / "publications_fixture.txt",\n'
     '    ):\n'
     '        if candidate.is_file():\n'
     '            return candidate\n'
     '    return PROJECT_ROOT / "Data" / "eval" / "publications_fixture.txt"\n',
     '    return APP_DATA_ROOT / "eval" / "publications_fixture.txt"\n', 1),
    ("App/backend/app/rag/pipeline.py",
     'from .._root import PROJECT_ROOT as _PROJECT_ROOT\n',
     'from .._root import APP_DATA_ROOT as _APP_DATA_ROOT  # noqa: E402\nfrom .._root import PROJECT_ROOT as _PROJECT_ROOT\n', 1),
    ("App/backend/app/rag/pipeline.py",
     '_DEFAULT_DATA_DIR = str(_PROJECT_ROOT / "Data" / "dataset")',
     '_DEFAULT_DATA_DIR = str(_APP_DATA_ROOT / "dataset")', 1),
    ("App/backend/app/rag/query.py",
     'from .._root import PROJECT_ROOT as _PROJECT_ROOT\n',
     'from .._root import CODE_ROOT as _CODE_ROOT\n', 1),
    ("App/backend/app/rag/query.py",
     'return (BM25_STATE_PATH, _PROJECT_ROOT / "Model" / "bm25_state.json")',
     'return (BM25_STATE_PATH, _CODE_ROOT / "Model" / "bm25_state.json")', 1),
    ("App/backend/app/receptionist/lexicon.py",
     'from .._root import PROJECT_ROOT\n',
     'from .._root import APP_DATA_ROOT, PROJECT_ROOT\n', 1),
    ("App/backend/app/receptionist/lexicon.py",
     '_LEXICON_PATH = PROJECT_ROOT / "Data" / "receptionist" / "lexicon_en.txt"',
     '_LEXICON_PATH = APP_DATA_ROOT / "receptionist" / "lexicon_en.txt"', 1),
    ("App/backend/app/seed_prototype.py",
     'from ._root import PROJECT_ROOT\n',
     'from ._root import APP_DATA_ROOT\n', 1),
    ("App/backend/app/seed_prototype.py",
     'str(PROJECT_ROOT / "Data" / "eval" / "prototype_seed.json")',
     'str(APP_DATA_ROOT / "eval" / "prototype_seed.json")', 1),
    ("App/backend/tests/test_eval_set_completeness.py",
     'from app._root import PROJECT_ROOT  # noqa: E402',
     'from app._root import APP_DATA_ROOT  # noqa: E402', 1),
    ("App/backend/tests/test_eval_set_completeness.py",
     'EVAL_PATH = PROJECT_ROOT / "Data" / "eval" / "rag_eval.jsonl"',
     'EVAL_PATH = APP_DATA_ROOT / "eval" / "rag_eval.jsonl"', 1),
    # The Compose file moved to App/: its ./ mounts are read from there.
    ("App/tests/test_monitoring_config.py",
     '                assert (ROOT / source).exists(), f"{name} mounts missing {source}"',
     '                assert (ROOT / "App" / source).exists(), f"{name} mounts missing {source}"', 1),
    # A checkout now has App/Model/bm25_state.json, as the image does. This test is about
    # acronym folding, so it keeps running without the state (G672 records what changes with it).
    ("App/backend/tests/test_faq_acronyms.py",
     "    @classmethod\n"
     "    def setUpClass(cls):\n"
     "        cls.index, _ = pipeline._load_faq_data(pipeline._DATA_DIR)\n",
     "    @classmethod\n"
     "    def setUpClass(cls):\n"
     "        from unittest import mock\n"
     "\n"
     "        cls._no_bm25_state = mock.patch.multiple(pipeline, _BM25_ENCODER=None, _BM25_LOAD_ATTEMPTED=True)\n"
     "        cls._no_bm25_state.start()\n"
     "        cls.index, _ = pipeline._load_faq_data(pipeline._DATA_DIR)\n", 1),
    ("App/backend/tests/test_faq_acronyms.py",
     "    def test_acronyms_are_learned_from_the_shipped_corpus(self):\n",
     "    @classmethod\n"
     "    def tearDownClass(cls):\n"
     "        cls._no_bm25_state.stop()\n"
     "\n"
     "    def test_acronyms_are_learned_from_the_shipped_corpus(self):\n", 1),
    ("App/docs/GAPS_AND_AGENTIC_ROADMAP.md",
     "| G671 ",
     "| G672 🟡 | With the image's BM25 state loaded (`/app/Model/bm25_state.json`), the Kiswahili FAQ row "
     "\"Nyaraka gani zinahitajika kusafisha gari lililoagizwa katika forodha?\" does not retrieve itself: "
     "`pipeline._simple_search` returns nothing for it. Without the state it ranks first. A checkout had no "
     "`App/Model/bm25_state.json` until ADR-0064 moved `Model/`, so `test_faq_acronyms` never ran the production "
     "path; it now runs without the state on purpose. | Open: with `pipeline._get_bm25_encoder` active, "
     "measure self-retrieval for every Swahili and Luganda FAQ row, and fix the BM25 scoring of those words. |\n"
     "| G671 ", 1),
    # These expectations quote a Compose file in App/, whose corpus mounts now sit beside it.
    ("App/backend/tests/test_qdrant_index_lifecycle_300.py",
     '    assert "../Data/pdfs:/app/Data/pdfs:ro" in body\n'
     '    assert "../Data/crawl/pages:/app/Data/crawl/pages:ro" in body\n',
     '    assert "./Data/pdfs:/app/Data/pdfs:ro" in body\n'
     '    assert "./Data/crawl/pages:/app/Data/crawl/pages:ro" in body\n', 1),
    # App/Model was ignored whole when it held only downloads; now it also holds
    # the tracked BM25 state, so the rule lists what is tracked instead.
    (".gitignore",
     "# Model files (large, use DVC or HF Hub)\n"
     "App/Model/*.joblib\n"
     "App/Model/*.pth\n"
     "App/Model/*.pt\n"
     "App/Model/*.onnx\n"
     "App/Model/*.tflite\n"
     "Model/faiss_index/\n"
     "Model/adapters/\n"
     "App/Model/\n"
     "\n"
     "# Downloaded LLM checkpoints (16GB+ safetensors shards + HF cache locks \u2014\n"
     "# App/Model/Sunflower-14B-FP8/ is a local snapshot_download of\n"
     "# Sunbird/Sunflower-14B-FP8, not a repo artifact; re-download via\n"
     "# LLM_MODEL/.env.example rather than committing it)\n"
     "App/Model/\n",
     "# App/Model tracks three files. The rest is downloads (LLM checkpoints such as\n"
     "# App/Model/Sunflower-14B-FP8/, re-fetched via LLM_MODEL in App/.env.example),\n"
     "# weight files, and index runtime state (index_*.json, static_index.npz).\n"
     "App/Model/*\n"
     "!App/Model/.gitkeep\n"
     "!App/Model/README.md\n"
     "!App/Model/bm25_state.json\n", 1),
    (".gitignore", "CosyVoice/\nModel/static_index.npz\n", "CosyVoice/\n", 1),
    (".gitignore",
     "# HuggingFace model weights (App/Model/*/ contains downloaded LLM shards)\n"
     "App/Model/*/\n"
     "# Runtime state generated by indexing tests; it contains machine-specific paths\n"
     "# and content hashes rather than versioned application data.\n"
     "App/Model/index_reindex_requested.json\n"
     "\n", "", 1),
    (".github/workflows/ura-chatbot-build-push.yml",
     "Checkout (full context needed \u2014 App/Dockerfile copies App/Model/ + App/Data/)",
     "Checkout (full context needed \u2014 App/Dockerfile.cranecloud copies App/Model/ + App/Data/)", 1),
    # The full stack moved to App/: its .env is still the one at the repository root,
    # and commands that layered on the old root docker-compose.yml now name it.
    ("App/docker-compose.full.yml", "    env_file:\n      - .env\n", "    env_file:\n      - ../.env\n", 1),
    ("App/docker-compose.mcp.yml",
     "#   docker compose -f docker-compose.yml -f docker-compose.mcp.yml up -d",
     "#   cd App && docker compose -f docker-compose.full.yml -f docker-compose.mcp.yml up -d", 1),
    ("App/docs/runbooks/enterprise-connectors-and-mcp.md",
     "docker compose -f docker-compose.yml -f App/docker-compose.mcp.yml up -d \\\n",
     "docker compose -f App/docker-compose.full.yml -f App/docker-compose.mcp.yml up -d \\\n", 1),
    ("README.md",
     "build and run locally with `docker compose up --build`",
     "build and run locally with `docker compose -f App/docker-compose.full.yml up --build`", 1),
    ("App/README.md",
     "docker compose --profile postgres --profile vllm --profile tls up -d\n",
     "docker compose -f App/docker-compose.full.yml --profile postgres --profile vllm --profile tls up -d\n", 1),
    ("App/docs/DEPLOYMENT.md",
     "The project ships a single `docker-compose.yml` at the repo root with four services:",
     "The full stack is `App/docker-compose.full.yml`, run from the repository root with "
     "`docker compose -f App/docker-compose.full.yml`. It has four services:", 1),
    # Outputs and inputs named by bare folder words or joins, which the path rules
    # cannot see because the files do not exist in the tree.
    (".github/workflows/ci-ml-pipeline.yml", 'root / "Data" /', 'root / "App" / "Data" /', 3),
    (".github/workflows/ci-ml-pipeline.yml", "          mkdir -p Results\n", "          mkdir -p App/Results\n", 1),
    (".github/workflows/ci-ml-pipeline.yml", "            --output-dir Results \\\n", "            --output-dir App/Results \\\n", 1),
    (".github/workflows/ci-ml-pipeline.yml", "            --output-dir Results/lg \\\n", "            --output-dir App/Results/lg \\\n", 1),
    (".github/workflows/ci-ml-pipeline.yml", "            Results/lg/\n", "            App/Results/lg/\n", 1),
    (".github/workflows/index-freshness.yml", "--crawl-dir ../../Data/crawl", "--crawl-dir ../Data/crawl", 1),
    ("App/ml/configs/training_config.yaml", '  output_dir: "Model"\n  results_dir: "Results"\n',
     '  output_dir: "App/Model"\n  results_dir: "App/Results"\n', 1),
    ("App/README.md", '="../../Model/adapters/', '="../Model/adapters/', 6),
    # Run from App/backend: ml is one level up since phase C, Results since phase D.
    ("App/docs/runbooks/corpus-coverage.md", "PYTHONPATH=.:$PWD/../.. ", "PYTHONPATH=.:$PWD/.. ", 4),
    ("App/docs/runbooks/corpus-coverage.md", "--output ../../Results/eval/", "--output ../Results/eval/", 2),
    # In the image CODE_ROOT is /app, as PROJECT_ROOT is; in a checkout it is App/.
    ("App/backend/app/artifact_export.py",
     '        from ._root import PROJECT_ROOT as _pr\n\n        results_dir = _pr / "Results"\n',
     '        from ._root import CODE_ROOT\n\n        results_dir = CODE_ROOT / "Results"\n', 1),
    ("App/backend/app/artifact_export.py",
     '        from ._root import PROJECT_ROOT as _pr\n\n        out_dir = _pr / "Artifacts"\n',
     '        from ._root import CODE_ROOT\n\n        out_dir = CODE_ROOT / "Artifacts"\n', 1),
    ("App/backend/app/main.py",
     '    from ._root import PROJECT_ROOT as _pr2\n\n    results_dir = _pr2 / "Results"\n',
     '    from ._root import CODE_ROOT\n\n    results_dir = CODE_ROOT / "Results"\n', 1),
    # Compose in App/ mounted the adapters from the root Model/, which moved beside it.
    ('App/docker-compose.yml', '      - ../Model/adapters:/app/adapters:ro\n', '      - ./Model/adapters:/app/adapters:ro\n', 1),
    ('App/docker-compose.dev2.yml', '      - ../Model/adapters:/app/adapters:ro\n', '      - ./Model/adapters:/app/adapters:ro\n', 1),
    ('App/README.md', 'production compose mounts `../Model/adapters:/app/adapters:ro`', 'production compose mounts `./Model/adapters:/app/adapters:ro`', 1),
    ('App/docker-compose.coverage-gpu.yml', '# Only Luganda has an adapter (`Model/adapters/` is empty and', '# Only Luganda has an adapter (`App/Model/adapters/` is empty and', 1),
    # The joins above were the last users of these imports.
    ("App/backend/app/ingestion/indexer.py", "from .._root import PROJECT_ROOT as _PROJECT_ROOT\n", "", 1),
    ("App/backend/app/publications.py", "from ._root import APP_DATA_ROOT, PROJECT_ROOT\n", "from ._root import APP_DATA_ROOT\n", 1),
    # The audited requirements file moved with the others.
    ("App/tests/test_ci_supply_chain_policy.py", '        ("requirements.txt", ci_python),', '        ("App/requirements.txt", ci_python),', 1),
    # Coverage reads its config from the directory it runs in, which is the root.
    (
        ".github/workflows/ci-ml-pipeline.yml",
        "pytest App/tests/ -v --cov=App/ml",
        "pytest App/tests/ -v --cov-config=App/pyproject.toml --cov=App/ml",
        1,
    ),
    # security-trivy.yml's container-scan-frontend scans and SBOMs the same image.
    (
        ".github/workflows/frontend-deploy.yml",
        "      - name: Trivy vulnerability scan (SARIF)\n"
        "        uses: aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25 # v0.36.0\n"
        "        with:\n"
        "          image-ref: ${{ steps.docker_mode.outputs.local_tag }}\n"
        "          scanners: vuln,secret\n"
        "          severity: CRITICAL,HIGH\n"
        "          format: sarif\n"
        "          output: trivy-frontend.sarif\n"
        "          trivy-config: App/security/trivy.yaml\n"
        "          ignore-unfixed: true\n"
        "\n"
        "      - name: Upload Trivy SARIF to GitHub Security\n"
        "        if: always() && hashFiles('trivy-frontend.sarif') != ''\n"
        "        uses: github/codeql-action/upload-sarif@cdf488f595d80d6e07e03d4674febd5ab45fa938 # v4.37.9\n"
        "        with:\n"
        "          sarif_file: trivy-frontend.sarif\n"
        "          category: trivy-frontend-pipeline\n"
        "\n"
        "      - name: Generate SBOM (CycloneDX)\n"
        "        if: always()\n"
        "        uses: aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25 # v0.36.0\n"
        "        with:\n"
        "          image-ref: ${{ steps.docker_mode.outputs.local_tag }}\n"
        "          format: cyclonedx\n"
        "          output: sbom-frontend-pipeline.cdx.json\n"
        "\n"
        "      - name: Upload scan artifacts\n"
        "        if: always()\n"
        "        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1\n"
        "        with:\n"
        "          name: trivy-frontend-pipeline-results\n"
        "          path: |\n"
        "            trivy-frontend.sarif\n"
        "            sbom-frontend-pipeline.cdx.json\n"
        "          retention-days: 90\n"
        "\n",
        "",
        1,
    ),
    # Trivy finds .trivyignore only in the working directory; name it beside its config.
    (
        "App/security/trivy.yaml",
        "# Docs: https://aquasecurity.github.io/trivy/latest/docs/references/configuration/config-file/\n"
        "# =============================================================================\n",
        "# Docs: https://aquasecurity.github.io/trivy/latest/docs/references/configuration/config-file/\n"
        "# =============================================================================\n"
        "\n"
        "# Scans run from the repository root; the ignore file sits beside this config (ADR-0064).\n"
        "ignorefile: App/security/.trivyignore\n",
        1,
    ),
]}


def trivy_steps_name_their_config(apply: bool) -> int:
    """A trivy-action step without `trivy-config` read trivy.yaml from the root, which is gone."""
    path = REPO / ".github/workflows/security-trivy.yml"
    lines, added = path.read_text(encoding="utf-8").split("\n"), 0
    i = 0
    while i < len(lines):
        if "uses: aquasecurity/trivy-action@" in lines[i]:
            j = i + 1
            while j < len(lines) and not re.match(r"^\s*- (name|uses|run):", lines[j]):
                j += 1
            block = lines[i:j]
            if not any("trivy-config:" in line for line in block):
                w = next((k for k in range(i, j) if lines[k].strip() == "with:"), None)
                if w is not None:
                    indent = len(lines[w]) - len(lines[w].lstrip()) + 2
                    lines.insert(w + 1, " " * indent + "trivy-config: App/security/trivy.yaml")
                    added += 1
                    j += 1
            i = j
        else:
            i += 1
    if apply and added:
        path.write_text("\n".join(lines), encoding="utf-8")
    return added


def kernel_release(apply: bool) -> int:
    """The rate tables' citations moved, so their content hash did: record the engineering release.

    No rate changed. A URA-reviewed release is recorded with a reviewer, never here
    (App/docs/runbooks/kernel-release.md).
    """
    backend = REPO / "App/backend"
    env = {**os.environ, "PYTHONPATH": f".:{REPO / 'App'}"}
    check = subprocess.run([sys.executable, "-m", "app.kernel.release", "check"], cwd=backend, env=env,
                           capture_output=True, text=True)
    if check.returncode == 0 or not apply:
        return 0
    subprocess.run([sys.executable, "-m", "app.kernel.release", "record", "--version", "1.2.3", "--notes",
                    "Source citations moved under App/Data (ADR-0064); no rate changed"],
                   cwd=backend, env=env, check=True, capture_output=True, text=True)
    return 1


# A string that starts with the folder; `ROOT / "App" / "Results"` joins are already right.
CWD_OUTPUT = re.compile(r"(?<=[\"'])(?<!/ \")(?<!/ ')(?:\./)?(?=(?:Results|Artifacts)[\"'/]|Data/eval/feedback)")


def cwd_outputs_under_app(apply: bool) -> int:
    """Scripts run from the repository root write ``Results/...``: point them at App/.

    The path rules skip these because the outputs do not exist until a run makes them.
    """
    count = 0
    for root in ("App/scripts", "App/ml"):
        for path in sorted((REPO / root).rglob("*.py")):
            if path.relative_to(REPO).as_posix() == SELF:
                continue
            text = path.read_text(encoding="utf-8")
            new, n = CWD_OUTPUT.subn("App/", text)
            if n and apply:
                path.write_text(new, encoding="utf-8")
            count += n
    return count


def heldout_digest(apply: bool) -> int:
    """The frozen held-out set's paths moved; its recorded SHA-256 is recomputed with them."""
    folder = REPO / "App/evals/complex_scenarios"
    digest = hashlib.sha256((folder / "heldout.jsonl").read_bytes()).hexdigest()
    record = folder / "HELDOUT.sha256"
    line = f"{digest}  heldout.jsonl\n"
    if record.read_text(encoding="utf-8") == line:
        return 0
    if apply:
        record.write_text(line, encoding="utf-8")
    return 1


def coverage_omit_anywhere(apply: bool) -> int:
    """Coverage resolves a relative omit pattern from where it runs (the root): match anywhere."""
    path = REPO / "App/pyproject.toml"
    text = path.read_text(encoding="utf-8")
    start, end = text.index("[tool.coverage.run]"), text.index("[tool.coverage.report]")
    section, n = re.subn(r'(?m)^(\s+")(?=(?:ml|backend)/)', r"\1*/", text[start:end])
    if apply and n:
        path.write_text(text[:start] + section + text[end:], encoding="utf-8")
    return n


def pending_phases() -> set[str]:
    return {phase for phase, sources in PHASE_SOURCES.items() if any((REPO / s.rstrip("/")).exists() for s in sources)}


def hand_edits(apply: bool, phases: set[str]) -> int:
    applied = 0
    for rel, old, new, expected in (edit for phase in sorted(phases) for edit in HAND_EDITS[phase]):
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


#: A root folder's README merged into the README already at its destination.
MERGED_READMES = {"Data/README.md": "App/Data/README.md"}


def _merge_readme(src: str, dst: str, tree: OldTree, apply: bool) -> None:
    body = (REPO / src).read_text(encoding="utf-8")
    body, _ = rewrite_text(body, src, dst, tree)
    lines = body.split("\n")
    if lines and lines[0].startswith("# "):
        lines = lines[1:]  # the destination keeps its own title
    merged = (REPO / dst).read_text(encoding="utf-8").rstrip("\n") + "\n\n## Source corpora\n" + "\n".join(lines)
    if apply:
        (REPO / dst).write_text(merged.rstrip("\n") + "\n", encoding="utf-8")
        _git("rm", "-q", src)


def move(apply: bool, tree: OldTree) -> list[tuple[str, str]]:
    planned = []
    files = tracked()
    for src, dst in MOVES:
        source = REPO / src.rstrip("/")
        if not source.exists():
            continue
        if not src.endswith("/"):
            planned.append((src, dst))
            if (REPO / dst).exists():
                raise SystemExit(f"{dst} already exists; remove it before the move")
            if apply:
                (REPO / dst).parent.mkdir(parents=True, exist_ok=True)
                _git("mv", src, dst)
            continue
        destination = REPO / dst.rstrip("/")
        entries = sorted({p[len(src) :].split("/", 1)[0] for p in files if p.startswith(src)})
        for entry in entries:
            old, new = f"{src}{entry}", f"{dst}{entry}"
            planned.append((old, new))
            if old in MERGED_READMES:
                _merge_readme(old, MERGED_READMES[old], tree, apply)
                continue
            # `git mv a b` into an existing b nests a inside it (stray ignored files can make one).
            if (REPO / new).exists():
                raise SystemExit(f"{new} already exists; remove it before the move")
            if apply:
                destination.mkdir(parents=True, exist_ok=True)
                _git("mv", old, new)
        if apply:  # ignored files (fonts, local outputs) follow; caches are dropped
            for leftover in sorted(source.rglob("*"), reverse=True):
                if "__pycache__" in leftover.parts:
                    continue
                if leftover.is_file():
                    target = destination / leftover.relative_to(source)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        shutil.move(str(leftover), str(target))
            shutil.rmtree(source, ignore_errors=True)
    for name in DELETES:
        if (REPO / name).exists():
            planned.append((name, "(deleted)"))
            if apply:
                _git("rm", "-q", name)
    return planned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    old_files = tracked()
    tree = OldTree(old_files)
    if not any((REPO / src.rstrip("/")).exists() for src, _ in MOVES) and not any(
        (REPO / name).exists() for name in DELETES
    ):
        say("nothing to move: every folder and file is already in place")
        return 0

    phases = pending_phases()
    planned = move(args.apply, tree)
    rewrites, depth_fixes, undecided = {}, {}, []
    for old in old_files:
        new = new_path(old)
        if old.startswith(SKIP_PREFIXES) or old in DELETES or old == SELF or not _is_text(old):
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

    edits = hand_edits(args.apply, phases) if args.apply else sum(len(HAND_EDITS[p]) for p in phases)
    if args.apply and "D" in phases:
        edits += trivy_steps_name_their_config(True) + coverage_omit_anywhere(True) + heldout_digest(True)
        edits += kernel_release(True) + cwd_outputs_under_app(True)
    say(f"moves: {len(planned)} entries from {len({p[0].split('/')[0] for p in planned})} root names")
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

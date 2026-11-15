"""Carve the chat, documents and health routes and their middleware out of main.py (ADR-0056, step 2).

The diff this writes is the whole carve-out: review PLAN, RENAMES and
SUBSTITUTIONS here, then rerun it on the parent commit to reproduce the change.

    python3 App/backend/scripts/carve_main_routes.py          # dry run: what moves, what each module imports
    python3 App/backend/scripts/carve_main_routes.py --apply  # write the modules and the new main.py
    python3 App/backend/scripts/carve_main_routes.py --verify # tests that patch moved code through main
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
MAIN = APP / "main.py"

# New module -> (docstring, top-level statements of main.py to move). A name moves the def or
# assignment that binds it; "if:<name>" moves the top-level `if` whose test reads <name>.
PLAN: dict[str, tuple[str, list[str]]] = {
    "middleware/rate_limiter.py": (
        "Rate limiting: the shared slowapi limiter and the per-route ceilings.",
        ["_RATE_LIMIT", "_EXPORT_RATE_LIMIT", "_DOCUMENT_RATE_LIMIT", "_CHAT_POLL_RATE_LIMIT",
         "_SLOWAPI_STORAGE_URI", "_limiter_kwargs", "if:_SLOWAPI_STORAGE_URI", "limiter"],
    ),
    "middleware/error_handler.py": (
        "Global error handling: the HTTP exception handler every route's errors pass through.",
        ["_http_exception_with_challenge"],
    ),
    "api/deps.py": (
        "Request dependencies shared by the routers and main.py: the loaded models and the operator checks.",
        ["get_model", "get_speech_model", "_experiment_log_fields", "_INDEX_API_KEY", "_has_valid_ops_key",
         "_require_ops_key", "require_admin_access"],
    ),
    "api/health.py": (
        "Health check: liveness, readiness, index freshness and the Prometheus scrape.",
        ["health_liveness", "_local_dense_status", "_receptionist_status", "health_readiness", "index_freshness",
         "_has_valid_metrics_token", "require_metrics_reader", "prometheus_metrics"],
    ),
    "api/rag.py": (
        "RAG APIs: chat over REST, SSE and WebSocket, conversation titles, and the FAQ catalogue.",
        ["_resolve_chat_connectors", "chat", "_sse_not_disconnected", "_log_stream_conversation", "chat_stream",
         "chat_title", "chat_stream_ws_v2", "list_tags", "get_faq"],
    ),
    "api/documents.py": (
        "Document APIs: analyse an upload, its report and status, and re-index the corpus.",
        ["_DOCUMENT_MULTIPART_OVERHEAD_BYTES", "_DOCUMENT_UPLOAD_CHUNK_BYTES", "_reject_oversized_document_request",
         "_read_document_upload_bounded", "analyze_uploaded_document", "document_report", "document_status",
         "_INDEX_REBUILD_LOCK", "_exclusive_index_rebuild", "trigger_indexing"],
    ),
}
ROUTERS = ("api/health.py", "api/rag.py", "api/documents.py")

# A name imported across modules loses its leading underscore.
RENAMES = {
    "_RATE_LIMIT": "RATE_LIMIT",
    "_EXPORT_RATE_LIMIT": "EXPORT_RATE_LIMIT",
    "_DOCUMENT_RATE_LIMIT": "DOCUMENT_RATE_LIMIT",
    "_CHAT_POLL_RATE_LIMIT": "CHAT_POLL_RATE_LIMIT",
    "_http_exception_with_challenge": "http_exception_with_challenge",
    "_experiment_log_fields": "experiment_log_fields",
    "_INDEX_API_KEY": "INDEX_API_KEY",
    "_has_valid_ops_key": "has_valid_ops_key",
    "_require_ops_key": "require_ops_key",
}

SETTINGS = '''"""Application settings: the API's name and version, read by main.py and the health routes."""

API_TITLE = "URA Chatbot API"
API_VERSION = "1.2.0"
API_DESCRIPTION = "AI-powered customer-service chatbot for the Uganda Revenue Authority"
'''

RULE = "# " + "-" * 75
REDIS_NOTE = (
    "# Set SLOWAPI_STORAGE_URI=redis://redis:6379 for multi-worker / multi-replica\n"
    "# deploys.  When unset, the limiter uses an in-process memory bucket — this\n"
    "# is fine for a single-worker dev server but will NOT correctly enforce a\n"
    "# shared limit across workers or Kubernetes replicas.\n"
)

# Exact edits the move needs, each found exactly once: (file, old, new).
SUBSTITUTIONS = [
    # Section banners whose code moved: the limiter's note goes with the limiter.
    ("main.py", f"{RULE}\n# Rate limiter (2026 production: Redis-backed when SLOWAPI_STORAGE_URI set)\n{RULE}\n{REDIS_NOTE}", ""),
    ("main.py", f"{RULE}\n# Document attachments (analysis + report)\n{RULE}\n", ""),
    ("middleware/rate_limiter.py", '_SLOWAPI_STORAGE_URI = os.getenv("SLOWAPI_STORAGE_URI", "")\n',
     f'\n{REDIS_NOTE}_SLOWAPI_STORAGE_URI = os.getenv("SLOWAPI_STORAGE_URI", "")\n'),
    ("main.py", 'title="URA Chatbot API",', "title=API_TITLE,"),
    ("main.py", 'version="1.2.0",', "version=API_VERSION,"),
    ("main.py", 'description="AI-powered customer-service chatbot for the Uganda Revenue Authority",',
     "description=API_DESCRIPTION,"),
    # A router has no global app: the version comes from settings, the socket's app from the socket.
    ("api/health.py", '"version": app.version}', '"version": API_VERSION}'),
    ("api/health.py", "version=app.version,", "version=API_VERSION,"),
    ("api/rag.py", "await chat_stream_ws(websocket, app)", "await chat_stream_ws(websocket, websocket.app)"),
]
EXTRA_IMPORTS = {
    "main.py": ["from .config.settings import API_DESCRIPTION, API_TITLE, API_VERSION"],
    "api/health.py": ["from ..config.settings import API_VERSION"],
}


def package(module_path: str) -> str:
    return ".".join(["app", *Path(module_path).with_suffix("").parts[:-1]])


def dotted(module_path: str) -> str:
    return ".".join(["app", *Path(module_path).with_suffix("").parts])


def relative(target: str, pkg: str) -> str:
    t, p = target.split("."), pkg.split(".")
    common = 0
    while common < min(len(t), len(p)) and t[common] == p[common]:
        common += 1
    return "." * (len(p) - common + 1) + ".".join(t[common:])


def bound_names(stmt: ast.stmt) -> list[str]:
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [stmt.name]
    if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        return [n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)]
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        return [(a.asname or a.name).split(".")[0] for a in stmt.names]
    return []


def used_names(nodes: list[ast.stmt]) -> set[str]:
    return {n.id for node in nodes for n in ast.walk(node) if isinstance(n, ast.Name)}


def global_uses(nodes: list[ast.stmt]) -> set[str]:
    """Names read from module scope: a function's parameters and locals shadow the module's names."""
    found: set[str] = set()

    def local_names(fn: ast.AST) -> set[str]:
        args = fn.args
        names = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg] if a}
        for n in ast.walk(fn):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                names.add(n.id)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n is not fn:
                names.add(n.name)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                names |= {(a.asname or a.name).split(".")[0] for a in n.names}
            elif isinstance(n, ast.Global):
                names -= set(n.names)
        return names

    def visit(node: ast.AST, shadowed: set[str]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for part in [*getattr(node, "decorator_list", []), *node.args.defaults, *node.args.kw_defaults,
                         getattr(node, "returns", None),
                         *(a.annotation for a in [*node.args.args, *node.args.kwonlyargs] if a.annotation)]:
                if part is not None:
                    visit(part, shadowed)
            inner = shadowed | local_names(node)
            for child in (node.body if isinstance(node.body, list) else [node.body]):
                visit(child, inner)
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in shadowed:
            found.add(node.id)
        for child in ast.iter_child_nodes(node):
            visit(child, shadowed)

    for node in nodes:
        visit(node, set())
    return found


class Carve:
    def __init__(self) -> None:
        self.source = MAIN.read_text()
        self.lines = self.source.splitlines(keepends=True)
        self.tree = ast.parse(self.source)
        self.errors: list[str] = []
        self.index: dict[str, ast.stmt] = {}
        self.imports: dict[str, tuple[ast.stmt, ast.alias]] = {}
        for stmt in self.tree.body:
            for name in bound_names(stmt):
                if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                    alias = next(a for a in stmt.names if (a.asname or a.name).split(".")[0] == name)
                    self.imports[name] = (stmt, alias)
                else:
                    self.index[name] = stmt
            if isinstance(stmt, ast.If):
                for name in used_names([ast.Expr(stmt.test)]):
                    self.index.setdefault(f"if:{name}", stmt)
        self.home: dict[str, str] = {}  # moved name -> new module path
        self.moved: dict[str, list[ast.stmt]] = {}
        for module, (_, keys) in PLAN.items():
            stmts = []
            for key in keys:
                stmt = self.index.get(key)
                if stmt is None:
                    self.errors.append(f"{module}: main.py has no top-level {key}")
                    continue
                stmts.append(stmt)
                for name in bound_names(stmt):
                    self.home[name] = module
            self.moved[module] = sorted(stmts, key=lambda s: s.lineno)

    def span(self, stmt: ast.stmt) -> tuple[int, int]:
        """1-based inclusive line span: decorators and the comment block directly above."""
        start = min([stmt.lineno, *(d.lineno for d in getattr(stmt, "decorator_list", []))])
        while start > 1 and self.lines[start - 2].lstrip().startswith("#"):
            start -= 1
        return start, stmt.end_lineno

    def segment(self, stmt: ast.stmt, pkg: str = "app") -> str:
        """The statement's source, with relative imports inside it re-spelled from ``pkg``."""
        start, end = self.span(stmt)
        lines = self.lines[start - 1:end]
        for node in ast.walk(stmt):
            if isinstance(node, ast.ImportFrom) and node.level:
                base = ["app"][: max(0, 1 - (node.level - 1))]
                if node.level > 1:
                    self.errors.append(f"main.py:{node.lineno}: relative import above the app package")
                target = ".".join([*base, *(node.module.split(".") if node.module else [])])
                i = node.lineno - start
                m = re.compile(r"from\s+(\.+[\w.]*)\s*import").match(lines[i], node.col_offset)
                if not m:
                    self.errors.append(f"main.py:{node.lineno}: cannot re-spell this import")
                    continue
                lines[i] = lines[i][:m.start(1)] + relative(target, pkg) + lines[i][m.end(1):]
        return "".join(lines)

    def needs(self, module: str) -> tuple[list[str], bool]:
        """Import lines for a new module, and whether it needs its own logger."""
        stmts = self.moved[module]
        own = {name for s in stmts for name in bound_names(s)}
        pkg = package(module)
        from_lines: dict[str, list[str]] = {}
        plain: list[str] = []
        logger = False
        for name in sorted(global_uses(stmts) - own):
            if name == "logger":
                logger = True
            elif name == "app":
                continue  # decorators become @router; SUBSTITUTIONS handle the rest, and the check below proves it
            elif name in self.home:
                target = dotted(self.home[name])
                from_lines.setdefault(relative(target, pkg), []).append(RENAMES.get(name, name))
            elif name in self.imports:
                stmt, alias = self.imports[name]
                if isinstance(stmt, ast.Import):
                    plain.append(f"import {alias.name}" + (f" as {alias.asname}" if alias.asname else ""))
                else:
                    src = stmt.module or ""
                    spelled = relative(".".join(["app", *src.split(".")]) if src else "app", pkg) \
                        if stmt.level else src
                    if stmt.level and stmt.level > 1:
                        self.errors.append(f"{module}: deep relative import of {name} needs a hand check")
                    from_lines.setdefault(spelled, []).append(
                        alias.name + (f" as {alias.asname}" if alias.asname else ""))
            elif name in self.index:
                self.errors.append(f"{module}: needs {name}, which stays in main.py")
        lines = sorted(set(plain)) + [
            f"from {src} import {', '.join(sorted(set(names)))}" for src, names in sorted(from_lines.items())
        ]
        return lines, logger

    def module_text(self, module: str) -> str:
        imports, logger = self.needs(module)
        if module in ROUTERS:
            imports.append("from fastapi import APIRouter")
        imports += EXTRA_IMPORTS.get(module, [])
        if logger:
            imports.append("import logging")
        merged: dict[str, set[str]] = {}
        plain = set()
        for line in imports:
            if line.startswith("from "):
                src, names = line[5:].split(" import ", 1)
                merged.setdefault(src, set()).update(n.strip() for n in names.split(","))
            else:
                plain.add(line)
        imports = sorted(plain) + [f"from {src} import {', '.join(sorted(names))}" for src, names in merged.items()]

        def order(line: str) -> tuple[int, str]:
            return (0 if line.startswith("import ") else 1, line.split()[1])

        stdlib = sorted((i for i in imports if i.split()[1].split(".")[0] in sys.stdlib_module_names), key=order)
        third = sorted((i for i in imports if i not in stdlib and not i.split()[1].startswith(".")), key=order)
        local = sorted((i for i in imports if i.split()[1].startswith(".")), key=order)
        head = f'"""{PLAN[module][0]}"""\n\nfrom __future__ import annotations\n\n'
        head += "\n\n".join("\n".join(group) for group in (stdlib, third, local) if group) + "\n\n"
        if logger:
            head += "logger = logging.getLogger(__name__)\n"
        if module in ROUTERS:
            head += "router = APIRouter()\n"
        definition = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        parts: list[str] = []
        previous = None
        for stmt in self.moved[module]:
            start, _ = self.span(stmt)
            gap = self.lines[previous.end_lineno:start - 1] if previous is not None else []
            if previous is not None and gap and all(not g.strip() for g in gap):
                parts.append("".join(gap))
            elif isinstance(stmt, definition) or isinstance(previous, definition) or previous is None:
                parts.append("\n\n" if isinstance(stmt, definition) or previous is not None else "\n")
            else:
                parts.append("\n" if start < stmt.lineno else "")  # a commented statement gets a blank line
            parts.append(re.sub(r"^@app\.", "@router.", self.segment(stmt, package(module)), flags=re.M))
            previous = stmt
        return head + "".join(parts)

    def main_text(self) -> str:
        spans = sorted(self.span(s) for stmts in self.moved.values() for s in stmts)
        first = spans[0][0]
        keep = [line for i, line in enumerate(self.lines, 1) if not any(a <= i <= b for a, b in spans)]
        remaining = ast.parse("".join(keep))
        still = global_uses(remaining.body)
        back: dict[str, list[str]] = {}
        for name, module in self.home.items():
            if name in still:
                back.setdefault(module, []).append(RENAMES.get(name, name))
        block = [f"from .{Path(m).with_suffix('').as_posix().replace('/', '.')} import {', '.join(sorted(n))}"
                 for m, n in sorted(back.items())]
        block.append("from .api import documents as documents_api, health as health_api, rag as rag_api")
        block += EXTRA_IMPORTS["main.py"]
        insert_at = sum(1 for i in range(1, first) if not any(a <= i <= b for a, b in spans))
        keep[insert_at:insert_at] = [line + "\n" for line in block] + ["\n"]
        text = self.drop_orphaned_imports("".join(keep))
        anchor = "app.include_router(quality_router)\n"
        if text.count(anchor) != 1:
            self.errors.append("main.py: the include_router anchor moved")
        text = text.replace(anchor, anchor + "".join(
            f"app.include_router({r}.router)\n" for r in ("health_api", "rag_api", "documents_api")))
        return text

    def drop_orphaned_imports(self, text: str) -> str:
        """Remove main.py imports that only the moved code read; leave imports that were already unused."""
        before = global_uses(self.tree.body)
        tree = ast.parse(text)
        after = global_uses(tree.body)
        lines = text.splitlines(keepends=True)
        drop_lines: set[int] = set()
        rewrites: dict[int, str] = {}
        for stmt in tree.body:
            if not isinstance(stmt, (ast.Import, ast.ImportFrom)) or stmt.lineno > len(lines):
                continue
            gone = [a for a in stmt.names if (a.asname or a.name).split(".")[0] in before - after]
            if not gone:
                continue
            kept = [a for a in stmt.names if a not in gone]
            if stmt.end_lineno > stmt.lineno and all(a.lineno != stmt.lineno for a in stmt.names):
                if kept:
                    drop_lines |= {a.lineno for a in gone}
                    if any(a.lineno == b.lineno for a in gone for b in kept):
                        self.errors.append(f"main.py:{stmt.lineno}: two names share an import line")
                else:
                    drop_lines |= set(range(stmt.lineno, stmt.end_lineno + 1))
            elif stmt.end_lineno == stmt.lineno:
                line = lines[stmt.lineno - 1]
                comment = line[line.index("#"):].rstrip("\n") if "#" in line else ""
                if kept:
                    names = ", ".join(a.name + (f" as {a.asname}" if a.asname else "") for a in kept)
                    head = ast.unparse(stmt).split(" import ")[0]
                    rewrites[stmt.lineno] = " " * stmt.col_offset + f"{head} import {names}" + (f"  {comment}" if comment else "") + "\n"
                else:
                    drop_lines.add(stmt.lineno)
            else:
                self.errors.append(f"main.py:{stmt.lineno}: import layout needs a hand edit")
        return "".join(rewrites.get(i, line) for i, line in enumerate(lines, 1) if i not in drop_lines)

    def build(self) -> dict[Path, str]:
        out = {APP / m: self.module_text(m) for m in PLAN}
        out[MAIN] = self.main_text()
        out[APP / "config/settings.py"] = SETTINGS
        for path in list(out):
            out[path] = re.sub(r"\b(" + "|".join(map(re.escape, RENAMES)) + r")\b", lambda m: RENAMES[m.group(1)], out[path])
        for rel, old, new in SUBSTITUTIONS:
            text = out[APP / rel]
            if text.count(old) != 1:
                self.errors.append(f"{rel}: expected one {old!r}, found {text.count(old)}")
            out[APP / rel] = text.replace(old, new)
        tidy = re.sub(r"\n{4,}", "\n\n\n", out[MAIN])  # removed code leaves runs of blank lines
        if ast.dump(ast.parse(tidy)) != ast.dump(ast.parse(out[MAIN])):
            self.errors.append("main.py: collapsing blank lines would change the program")
        else:
            out[MAIN] = tidy
        for module in ROUTERS:
            tree = ast.parse(out[APP / module])
            if "app" in used_names(tree.body):
                self.errors.append(f"{module}: still reads the global app")
        return out


def patches_through_main() -> list[str]:
    """Test patches of ``main.<name>`` where carved code reads <name>: they no longer reach it."""
    import subprocess

    repo = APP.parents[2]
    reads: dict[str, set[str]] = {}
    for module in PLAN:
        for node in ast.walk(ast.parse((APP / module).read_text())):
            if isinstance(node, ast.Name):
                reads.setdefault(node.id, set()).add(module)
    main_reads = used_names(ast.parse(MAIN.read_text()).body)

    def is_main(node: ast.AST) -> bool:
        return (isinstance(node, ast.Name) and node.id in {"main", "main_mod", "main_module"}) or (
            isinstance(node, ast.Attribute) and node.attr == "main" and isinstance(node.value, ast.Name))

    files = subprocess.run(["git", "ls-files", "App/backend/tests/*.py", "tests/*.py"], cwd=repo,
                           capture_output=True, text=True, check=True).stdout.split()
    found = []
    for rel in files:
        for node in ast.walk(ast.parse((repo / rel).read_text())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            name = None
            if func in ("object", "setattr") and len(node.args) >= 2 and is_main(node.args[0]) \
                    and isinstance(node.args[1], ast.Constant):
                name = node.args[1].value
            elif func in ("patch", "setattr") and node.args and isinstance(node.args[0], ast.Constant) \
                    and str(node.args[0].value).startswith(("app.main.", "App.backend.app.main.")):
                name = node.args[0].value.split("main.", 1)[1].split(".")[0]
            if name in reads:
                also = "main.py reads it too" if name in main_reads else "only carved code reads it"
                found.append(f"{rel}:{node.lineno}: main.{name} is read by {', '.join(sorted(reads[name]))} ({also})")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the files")
    parser.add_argument("--verify", action="store_true", help="list test patches through main that moved code reads")
    args = parser.parse_args()
    if args.verify:
        found = patches_through_main()
        print("\n".join(found) or "no test patches moved code through main")
        return 0
    carve = Carve()
    out = carve.build()
    for module, stmts in carve.moved.items():
        names = [n for s in stmts for n in bound_names(s)] or [ast.unparse(s.test) for s in stmts]
        print(f"{module}: {len(stmts)} statements ({', '.join(names)})")
        print("  imports: " + "; ".join(carve.needs(module)[0]))
    for err in carve.errors:
        print(f"ERROR {err}")
    if carve.errors:
        return 1
    if args.apply:
        for path, text in out.items():
            path.write_text(text)
        print(f"wrote {len(out)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())

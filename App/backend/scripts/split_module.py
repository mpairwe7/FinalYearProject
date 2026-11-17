"""Split one module into a package of submodules and repoint every importer (ADR-0061).

A plan names the module, its submodules (each with a docstring and the first line
of each run of top-level statements it takes) and, optionally, names that move
somewhere other than where their lines fall. The tool:

1. writes ``<package>/<submodule>.py`` for each submodule: its statements in source
   order, the imports it uses, and ``from .<other> import ...`` for names it uses
   from a sibling;
2. rewrites every ``from <module> import a, b`` in the repository (relative or
   absolute, at any depth, lazy imports included) into one import per submodule;
3. rewrites ``"app.<module>.NAME"`` patch-target strings;
4. deletes the old module. Nothing re-exports the old names.

Module-object uses (``from app import guardrails`` then ``guardrails.X``) are
reported for a hand edit; there are few.
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import restructure_rag_layout as codemod


@dataclass
class Plan:
    module: str  # dotted under app, e.g. "guardrails"
    submodules: dict[str, str]  # name -> docstring
    starts: list[tuple[int, str]]  # (first line, submodule), ascending; a statement goes to the last start <= its line
    package_doc: str
    overrides: dict[str, str] = field(default_factory=dict)  # top-level name -> submodule
    shared: str | None = None  # a name here that only one other submodule uses moves to that submodule
    text_edits: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # submodule -> [(old, new)] after writing


def _defined(node: ast.stmt) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Assign):
        return {t.id for t in node.targets if isinstance(t, ast.Name)}
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


def _bound_by_import(node: ast.stmt) -> set[str]:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                out.add(a.asname or a.name.split(".")[0])
    return out


def _deeper(code: str) -> str:
    """One package level deeper than the module the code came from: relative imports gain a dot."""
    return re.sub(r"^(\s*from\s+)(\.+)(?=\w)", r"\1.\2", code, flags=re.M)


def _span(lines: list[str], node: ast.stmt) -> tuple[int, int]:
    start = node.lineno - 1
    if getattr(node, "decorator_list", None):
        start = node.decorator_list[0].lineno - 1
    while start > 0 and lines[start - 1].lstrip().startswith("#"):
        start -= 1
    return start, node.end_lineno


def split(plan: Plan, apply: bool) -> dict[str, str]:
    """Write the submodules; return {name: submodule} for every top-level name."""
    path = codemod.APP / (plan.module.replace(".", "/") + ".py")
    text = path.read_text()
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) or isinstance(n, ast.If)]
    body = [n for n in tree.body if n not in imports and not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    owner_of: dict[str, str] = {}
    node_sub: dict[int, str] = {}
    for node in body:
        names = _defined(node)
        sub = next((plan.overrides[n] for n in names if n in plan.overrides), None)
        node_sub[id(node)] = sub or [s for line, s in plan.starts if line <= node.lineno][-1]
        for n in names:
            owner_of[n] = node_sub[id(node)]
    moved = True
    while plan.shared and moved:  # localise: shared names with a single user move to it
        moved = False
        users: dict[str, set[str]] = defaultdict(set)
        for node in body:
            for n in {x.id for x in ast.walk(node) if isinstance(x, ast.Name)} - _defined(node):
                if n in owner_of:
                    users[n].add(node_sub[id(node)])
        for node in body:
            names = _defined(node)
            if node_sub[id(node)] != plan.shared or not names or any(n in plan.overrides for n in names):
                continue
            u = set().union(*(users[n] for n in names))
            if plan.shared not in u and len(u) == 1:
                node_sub[id(node)] = next(iter(u))
                owner_of.update({n: node_sub[id(node)] for n in names})
                moved = True
    placed: dict[str, list[ast.stmt]] = defaultdict(list)
    for node in body:
        placed[node_sub[id(node)]].append(node)
    for sub, nodes in placed.items():
        used = {n.id for node in nodes for n in ast.walk(node) if isinstance(n, ast.Name)}
        local = {n for node in nodes for n in _defined(node)}
        head = [f'"""{plan.submodules[sub]}"""\n\n']
        for imp in imports:
            if isinstance(imp, ast.If) and _bound_by_import(imp) & used:
                used |= {n.id for n in ast.walk(imp.test) if isinstance(n, ast.Name)}
        for imp in imports:
            if isinstance(imp, ast.ImportFrom) and imp.module == "__future__":
                head.append("".join(lines[imp.lineno - 1 : imp.end_lineno]) + "\n")
            elif _bound_by_import(imp) & used:
                # One level deeper than the module it came from: relative imports gain a dot.
                block = "".join(lines[imp.lineno - 1 : imp.end_lineno])
                head.append(_deeper(block))
        siblings = defaultdict(set)
        for name in used - local:
            if name in owner_of and owner_of[name] != sub:
                siblings[owner_of[name]].add(name)
        for other, names in sorted(siblings.items()):
            head.append(codemod_import(f".{other}", sorted(names)))
        chunks = []
        for node in nodes:
            s, e = _span(lines, node)
            chunks.append(_deeper("".join(lines[s:e])).rstrip() + "\n")
        out = "".join(head).rstrip() + "\n\n\n" + "\n\n".join(chunks)
        for old, new in plan.text_edits.get(sub, []):
            assert old in out, f"{sub}: {old!r} not found"
            out = out.replace(old, new, 1)
        target = path.with_suffix("") / f"{sub}.py"
        print(f"split: {plan.module} -> {plan.module}.{sub}: {len(nodes)} statements")
        if apply:
            target.parent.mkdir(exist_ok=True)
            target.write_text(re.sub(r"\n{4,}", "\n\n\n", out))
    if apply:
        (path.with_suffix("") / "__init__.py").write_text(f'"""{plan.package_doc}"""\n')
        subprocess.run(["git", "rm", "-q", str(path)], cwd=codemod.REPO, check=True)
        subprocess.run(["git", "add", str(path.with_suffix(""))], cwd=codemod.REPO, check=True)
    return owner_of


def codemod_import(module: str, names: list[str]) -> str:
    line = f"from {module} import {', '.join(names)}\n"
    return line if len(line) <= 100 else f"from {module} import (\n" + "".join(f"    {n},\n" for n in names) + ")\n"


def repoint(plan: Plan, owner_of: dict[str, str], apply: bool, skip: set[Path]) -> None:
    dotted = f"app.{plan.module}"
    patch_re = re.compile(rf"([\"'])((?:App\.backend\.)?{re.escape(dotted)})\.([A-Za-z_]\w*)((?:\.\w+)*)\1")
    module_object: list[str] = []
    changed = 0
    for path in codemod.tracked_files():
        if path.suffix != ".py" or path in skip or not path.is_file():
            continue
        text = path.read_text()
        if plan.module.rpartition(".")[2] not in text:
            continue
        tree = ast.parse(text)
        mod = codemod.module_of(path)
        in_app = mod is not None and mod.startswith("app")
        pkg = codemod.package_of(path, mod) if in_app else None
        lines = text.encode().splitlines(keepends=True)
        edits = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level and not in_app:
                continue
            absolute = codemod.resolve_relative(pkg, node.level, node.module) if node.level else node.module or ""
            if codemod.canonical(absolute) == codemod.canonical(f"app.{plan.module.rpartition('.')[0]}".rstrip(".")) and any(a.name == plan.module.rpartition(".")[2] for a in node.names):
                module_object.append(f"{path.relative_to(codemod.REPO)}:{node.lineno}")
                continue
            if codemod.canonical(absolute) != dotted:
                continue
            groups = defaultdict(list)
            for a in node.names:
                sub = owner_of.get(a.name)
                if sub is None:
                    raise SystemExit(f"{path}:{node.lineno}: {a.name} is not a top-level name of {dotted}")
                groups[sub].append(f"{a.name} as {a.asname}" if a.asname else a.name)
            prefix = codemod.app_prefix(absolute) if not node.level else None
            indent = " " * node.col_offset
            stmts = []
            for sub, names in sorted(groups.items()):
                target = f"{absolute}.{sub}"
                spelled = codemod.relative(codemod.canonical(target), pkg) if node.level else (prefix + target[len(codemod.app_prefix(absolute)):])
                stmts.append(codemod_import(spelled, names).rstrip("\n").replace("\n", "\n" + indent))
            edits.append((node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, ("\n" + indent).join(stmts)))
        new = codemod.splice(text, edits) if edits else text

        def patch(m: re.Match[str]) -> str:
            sub = owner_of.get(m.group(3))
            return f"{m.group(1)}{m.group(2)}.{sub}.{m.group(3)}{m.group(4)}{m.group(1)}" if sub else m.group(0)

        new = patch_re.sub(patch, new)
        if new != text:
            changed += 1
            if apply:
                path.write_text(new)
    print(f"repoint: {changed} files rewritten")
    for item in module_object:
        print(f"  hand edit (module object): {item}")

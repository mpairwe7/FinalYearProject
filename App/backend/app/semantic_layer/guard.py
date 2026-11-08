"""SELECT-only guard for certified metric templates (ADR-0033, phase 3).

Deliberately stricter than SQL: a certified template is reviewed code, so
anything a metric does not need is refused rather than parsed.

A template passes only if it is one statement, starts with SELECT or WITH,
has no comments, no dollar quoting, no positional placeholders, none of the
write, DDL, session or file keywords, none of the dangerous server functions,
and reads only from the sources its metric declares (or its own CTEs).
"""

from __future__ import annotations

import re

FORBIDDEN_WORDS = frozenset({
    "alter", "analyze", "attach", "begin", "call", "checkpoint", "cluster", "comment", "commit", "copy", "create",
    "deallocate", "delete", "detach", "discard", "do", "drop", "exec", "execute", "explain", "export", "for",
    "grant", "import", "insert", "into", "listen", "load", "lock", "merge", "notify", "pragma", "prepare",
    "refresh", "reindex", "reset", "returning", "revoke", "rollback", "savepoint", "security", "set",
    "show", "truncate", "unlisten", "update", "upsert", "vacuum",
})
FORBIDDEN_FUNCTIONS = frozenset({
    "current_setting", "dblink", "dblink_exec", "lo_export", "lo_import", "load_extension", "pg_advisory_lock",
    "pg_cancel_backend", "pg_ls_dir", "pg_read_binary_file", "pg_read_file", "pg_reload_conf", "pg_sleep",
    "pg_stat_file", "pg_terminate_backend", "query_to_xml", "set_config", "xp_cmdshell",
})
_STRING_RE = re.compile(r"'(?:[^']|'')*'")
_QUOTED_IDENT_RE = re.compile(r'"(?:[^"]|"")*"')
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_SOURCE_RE = re.compile(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)", re.I)
_CTE_RE = re.compile(r"(?:\bwith|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(", re.I)
_PARAM_RE = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")


class SqlRejected(ValueError):
    """A template that is not a single, read-only SELECT over its sources."""


_FROM_LIST_RE = re.compile(
    r"\bfrom\s+(.+?)(?=\b(?:where|group|order|limit|having|union|intersect|except|join|inner|left|right|full|cross|"
    r"natural|on|using|window|offset|fetch)\b|\)|$)",
    re.I | re.S,
)
_RELATION_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)")


def _cte_bodies_removed(body: str) -> str:
    """*body* with each ``name AS ( ... )`` CTE body blanked, so only the main FROM lists remain."""
    out, i = [], 0
    for match in re.finditer(r"\bas\s*\(", body, re.I):
        if match.start() < i:
            continue
        out.append(body[i:match.end() - 1])
        depth, j = 0, match.end() - 1
        while j < len(body):
            depth += body[j] == "("
            depth -= body[j] == ")"
            j += 1
            if depth == 0:
                break
        out.append(" ")
        i = j
    out.append(body[i:])
    return "".join(out)


def _from_list_relations(body: str) -> set[str]:
    """Every relation in a FROM list, not only the first: ``FROM a, b`` reads both."""
    found = set()
    for clause in _FROM_LIST_RE.findall(body):
        for item in clause.split(","):
            match = _RELATION_RE.match(item)
            if match and not item.strip().startswith("("):
                found.add(match.group(1).lower())
    return found


def check(sql: str, *, sources: frozenset[str] | set[str]) -> set[str]:
    """Validate *sql*; returns the named parameters it binds. Raises :class:`SqlRejected`."""
    text = (sql or "").strip()
    if not text:
        raise SqlRejected("empty statement")
    if "--" in text or "/*" in text or "#" in text:
        raise SqlRejected("comments are not allowed")
    if "$$" in text or re.search(r"\$[A-Za-z_]*\$", text):
        raise SqlRejected("dollar quoting is not allowed")
    unstrung = _STRING_RE.sub(" 'lit' ", text)
    for ident in _QUOTED_IDENT_RE.findall(unstrung):
        name = ident.strip('"').replace('""', '"').lower()
        if name in FORBIDDEN_FUNCTIONS or name in FORBIDDEN_WORDS or name.startswith("pg_"):
            raise SqlRejected(f"quoted identifier not allowed: {name}")
    masked = _QUOTED_IDENT_RE.sub(" ident ", unstrung)
    if "'" in masked.replace("'lit'", ""):
        raise SqlRejected("unterminated string literal")
    body = masked.rstrip().removesuffix(";")
    if ";" in body:
        raise SqlRejected("one statement only")
    if "?" in body or "%s" in body or re.search(r"%\(", body):
        raise SqlRejected("use named :parameters only")
    words = [w.lower() for w in _WORD_RE.findall(body)]
    if not words or words[0] not in {"select", "with"}:
        raise SqlRejected("a template must start with SELECT or WITH")
    bad = sorted(set(words) & FORBIDDEN_WORDS)
    if bad:
        raise SqlRejected(f"not allowed in a certified template: {', '.join(bad)}")
    bad = sorted(set(words) & FORBIDDEN_FUNCTIONS)
    if bad:
        raise SqlRejected(f"function not allowed: {', '.join(bad)}")
    if any(w.startswith("pg_") for w in words):
        raise SqlRejected("system catalogue access is not allowed")
    if re.search(r"\b(?:from|join)\s*\(|,\s*\(", _cte_bodies_removed(body), re.I):
        # A parenthesised item in a FROM list hides the relations after it from the
        # check below; a certified template uses a CTE instead.
        raise SqlRejected("parenthesised FROM or JOIN items are not allowed; use a WITH clause")
    ctes = {m.lower() for m in _CTE_RE.findall(body)}
    allowed = {s.lower() for s in sources} | ctes
    used = {m.lower() for m in _SOURCE_RE.findall(body)} | _from_list_relations(body)
    outside = sorted(used - allowed)
    if outside:
        raise SqlRejected(f"reads from undeclared sources: {', '.join(outside)}")
    if not used:
        raise SqlRejected("a template must read from a declared source")
    return set(_PARAM_RE.findall(body))

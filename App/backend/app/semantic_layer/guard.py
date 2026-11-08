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


_RELATION_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)")
#: Keywords that end a FROM list at its own depth.
_FROM_LIST_END = frozenset({
    "where", "group", "order", "limit", "having", "union", "intersect", "except", "join", "inner", "left",
    "right", "full", "cross", "natural", "on", "using", "window", "offset", "fetch",
})


def _from_lists(body: str) -> list[list[str]]:
    """The items of every FROM list, in the main query, CTE bodies and subqueries alike.

    Read by parenthesis depth, so a nested ``FROM a, (…) x, b`` is split at its own
    commas and ``b`` is not lost after the parenthesis.
    """
    lists = []
    for match in re.finditer(r"\bfrom\b", body, re.I):
        items, depth, i, item_start = [], 0, match.end(), match.end()
        while i < len(body):
            char = body[i]
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and char == ",":
                items.append(body[item_start:i])
                item_start = i + 1
            elif depth == 0 and (char.isalpha() or char == "_") and (i == 0 or not (body[i - 1].isalnum() or body[i - 1] in "_$.")):
                word = _WORD_RE.match(body, i)
                if word and word.group(0).lower() in _FROM_LIST_END:
                    break
                i = word.end() if word else i + 1
                continue
            i += 1
        items.append(body[item_start:i])
        lists.append(items)
    return lists


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
    used = {m.lower() for m in _SOURCE_RE.findall(body)}
    if re.search(r"\bjoin\s*\(", body, re.I):
        raise SqlRejected("parenthesised FROM or JOIN items are not allowed; use a WITH clause")
    for items in _from_lists(body):
        for item in items:
            if item.strip().startswith("("):
                # A parenthesised item hides the relations inside and after it from this
                # check, in a CTE body as much as in the main query; use a CTE instead.
                raise SqlRejected("parenthesised FROM or JOIN items are not allowed; use a WITH clause")
            relation = _RELATION_RE.match(item)
            if relation:
                used.add(relation.group(1).lower())
    ctes = {m.lower() for m in _CTE_RE.findall(body)}
    allowed = {s.lower() for s in sources} | ctes
    outside = sorted(used - allowed)
    if outside:
        raise SqlRejected(f"reads from undeclared sources: {', '.join(outside)}")
    if not used:
        raise SqlRejected("a template must read from a declared source")
    return set(_PARAM_RE.findall(body))

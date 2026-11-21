"""Linear-time text scans for shapes a backtracking regex makes quadratic on user input.

``re.sub(r"<[^>]+>", ...)`` restarts at every ``<``, and each start scans to the end of the
text when no ``>`` follows, so a reply of many ``<`` costs time quadratic in its length.
These functions give the same result as the regex they replace, from one pass over the text.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


def strip_spans(
    text: str,
    opener: str,
    closer: str,
    repl: str = "",
    *,
    min_inner: int = 0,
    ignore_case: bool = False,
) -> str:
    """The result of ``re.sub(pattern, repl, text)``, where *pattern* is
    ``re.escape(opener) + f"[^{closer}]{{{min_inner},}}" + re.escape(closer)``.

    *closer* is one character; *ignore_case* adds ``re.IGNORECASE`` to the opener.
    """
    opener_re = re.compile(re.escape(opener), re.IGNORECASE) if ignore_case else None
    out: list[str] = []
    kept = pos = 0
    while True:
        if opener_re is None:
            start = text.find(opener, pos)
            inner = start + len(opener)
        else:
            m = opener_re.search(text, pos)
            start, inner = (m.start(), m.end()) if m else (-1, -1)
        if start < 0:
            break
        end = text.find(closer, inner)
        if end < 0:
            break  # no closer after this opener, so none after any later one either
        if end - inner < min_inner:
            pos = start + 1
            continue
        out += (text[kept:start], repl)
        kept = pos = end + 1
    out.append(text[kept:])
    return "".join(out)


def strip_blocks(
    text: str, opener: re.Pattern[str], closer: re.Pattern[str], *, or_to_end: bool = False
) -> str:
    """The result of ``re.sub(pattern, "", text, flags=re.DOTALL)``, where *pattern* is
    ``opener + "[^>]*>.*?(?:" + closer + ("|$" if or_to_end else "") + ")"``.

    The flags that matter (``re.IGNORECASE``) are the patterns' own.
    """
    out: list[str] = []
    kept = pos = 0
    n = len(text)
    while (m := opener.search(text, pos)) is not None:
        gt = text.find(">", m.end())
        if gt < 0:
            break
        close = closer.search(text, gt + 1)
        if or_to_end:
            # "$" holds at the end, and before a newline that ends the text.
            dollar = n - 1 if text.endswith("\n") and gt + 1 <= n - 1 else n
            end = close.end() if close is not None and close.start() <= dollar else dollar
        elif close is None:
            break
        else:
            end = close.end()
        out.append(text[kept : m.start()])
        kept = pos = end
    out.append(text[kept:])
    return "".join(out)


def _at_word_boundary(text: str, i: int) -> bool:
    """Whether ``\\b`` holds at *i*: a ``\\w`` character on one side of it only."""
    before = i > 0 and (text[i - 1].isalnum() or text[i - 1] == "_")
    after = i < len(text) and (text[i].isalnum() or text[i] == "_")
    return before != after


def email_spans(
    text: str, local_class: str, domain: str, flags: int = 0
) -> Iterator[tuple[int, int]]:
    """The spans ``re.finditer(r"\\b" + local_class + "+@" + domain, text, flags)`` gives.

    *local_class* is one character class, and neither it nor *domain* matches "@". The regex
    restarts at every word boundary in a run of local-part characters and reads the run to its
    end each time. This reads each run once, back from its "@", and matches *domain* anchored
    with ``\\A`` on the text up to the next "@" (G715, G716).
    """
    local_char = re.compile(local_class, flags)
    domain_at = re.compile(rf"\A(?:{domain})", flags)
    prev_end = 0
    at = text.find("@")
    while at >= 0:
        nxt = text.find("@", at + 1)
        start = at
        while start > prev_end and local_char.fullmatch(text[start - 1]):
            start -= 1
        start = next((i for i in range(start, at) if _at_word_boundary(text, i)), at)
        # "@" is not a word character, so a closing \b reads the slice's end as it reads the "@".
        m = domain_at.match(text[at + 1 : nxt if nxt >= 0 else None]) if start < at else None
        if m:
            prev_end = at + 1 + m.end()
            yield start, prev_end
        at = nxt


def sub_emails(text: str, repl: str, local_class: str, domain: str, flags: int = 0) -> str:
    """``re.sub(r"\\b" + local_class + "+@" + domain, repl, text, flags=flags)`` for a *repl*
    without backslashes, at the spans :func:`email_spans` gives."""
    out: list[str] = []
    kept = 0
    for start, end in email_spans(text, local_class, domain, flags):
        out += (text[kept:start], repl)
        kept = end
    out.append(text[kept:])
    return "".join(out)

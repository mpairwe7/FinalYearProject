"""Linear-time text scans for shapes a backtracking regex makes quadratic on user input.

``re.sub(r"<[^>]+>", ...)`` restarts at every ``<``, and each start scans to the end of the
text when no ``>`` follows, so a reply of many ``<`` costs time quadratic in its length.
These functions give the same result as the regex they replace, from one pass over the text.
"""

from __future__ import annotations

import re


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

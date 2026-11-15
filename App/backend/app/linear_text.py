"""Linear-time text scans for shapes a backtracking regex makes quadratic on user input.

``re.sub(r"<[^>]+>", ...)`` restarts at every ``<``, and each start scans to the end of the
text when no ``>`` follows, so a reply of many ``<`` costs time quadratic in its length.
These functions give the same result as the regex they replace, from one pass over the text.
"""

from __future__ import annotations


def strip_angle_tags(text: str, repl: str = "") -> str:
    """``re.sub(r"<[^>]+>", repl, text)`` in one pass."""
    out: list[str] = []
    kept = pos = 0
    while (start := text.find("<", pos)) >= 0:
        end = text.find(">", start + 1)
        if end < 0:
            break  # no ">" after this "<", so none after any later one either
        if end == start + 1:
            pos = start + 1  # "<>": the regex needs one character inside
            continue
        out += (text[kept:start], repl)
        kept = pos = end + 1
    out.append(text[kept:])
    return "".join(out)

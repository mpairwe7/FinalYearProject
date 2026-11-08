"""An English-decided reply that came back in another language, translated back (G486).

Moved out of ``service.py``. :func:`answer_in_english` is the one patch point: ``service``
and the graph both call it through this module, so a stub here reaches every path.
"""

from __future__ import annotations

import logging
import re

from . import mt
from .query import translate_query_for_retrieval

logger = logging.getLogger(__name__)


#: A blank line between paragraphs, kept so the translation keeps the layout.
_PARAGRAPH_BREAK_RE = re.compile(r"(\n[ \t]*\n+)")


def answer_in_english(reply: str) -> str:
    """*reply* in English when the turn was decided English but it is not.

    The answer language is decided in code (``app.language_state``); the model
    is only asked to follow it. When the prompt carried another language — the
    taxpayer's earlier Luganda turns, before they were replayed in English
    (G119) — Sunflower followed those instead, live on 2026-10-06. This is the
    check: a reply with clear Luganda or Swahili evidence is translated back,
    paragraph by paragraph (Sunflower can stop after the first) and under the
    same figure and length guards as any reply translation. When that fails the
    reply is served as it is, and logged.
    """
    from .receptionist.language import lexical_hits  # noqa: PLC0415 — imports this module's peers

    hits = lexical_hits(reply or "")
    source = max(("lg", "sw", "nyn", "ach"), key=lambda k: hits.get(k, 0))
    if hits.get(source, 0) < 3 or hits.get(source, 0) <= hits.get("en", 0):
        return reply
    pieces = _PARAGRAPH_BREAK_RE.split(reply)
    out = list(pieces)
    for index, piece in enumerate(pieces):
        if index % 2 or not piece.strip():
            continue
        english = translate_query_for_retrieval(piece, source)
        if not english or not mt.figures_survived(piece, english) or not mt.length_plausible(piece, english):
            logger.warning("English-decided reply came back in %s and could not be translated", source)
            return reply
        out[index] = english
    logger.info("English-decided reply came back in %s; served its English translation", source)
    return "".join(out)

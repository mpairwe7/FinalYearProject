"""The statutory kernel's state, as ``/ready`` reports it (ADR-0033).

An operator, or a deploy check, reads from outside the process which kernel
release answers, whether it is a recorded release, which graph backend serves
it, whether the staff semantic layer is on, and which rate tables are in
force. Each value is a short string, like the other ``/ready`` capabilities.
Never raises: a readiness probe must not fail because of a status line.
"""

from __future__ import annotations

import os


def readiness_capabilities() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        from .release import current

        version = current()
        state = "released" if version.released else "UNRELEASED"
        out["kernel"] = f"{version.version} {state} ({version.review_status}; ruleset {version.ruleset_version})"
    except Exception:
        out["kernel"] = "unavailable"
    try:
        from ..graph.neo4j_store import backend

        out["graph_store"] = backend()
    except Exception:
        out["graph_store"] = "unavailable"
    try:
        from ..flags import flags

        out["semantic_layer"] = "on" if flags.is_enabled("semantic_layer") else "off"
    except Exception:
        out["semantic_layer"] = "unavailable"
    try:
        from ..tax import tables

        years = tables.list_fiscal_years()
        out["rate_tables"] = (f"{years[0]}..{years[-1]}; in force {tables.resolve_fiscal_year()} "
                              f"({tables.coverage()})")
    except Exception:
        out["rate_tables"] = "unavailable"
    if os.getenv("CURRENT_FISCAL_YEAR"):
        out["rate_tables"] += f"; CURRENT_FISCAL_YEAR={os.environ['CURRENT_FISCAL_YEAR']}"
    return out

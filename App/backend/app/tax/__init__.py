"""Versioned URA fiscal data and the arithmetic that reads it.

:mod:`app.tax.tables` owns the effective-dated rate tables (one JSON
file per fiscal year, with provenance and a confirmed/provisional
status); :mod:`app.tax.money` owns the Decimal band arithmetic.  The
calculator tools in :mod:`app.tools.calculators` are thin wrappers over
both, so adding a fiscal year is a data change plus a test.
"""

from __future__ import annotations

from .money import (
    AmountError,
    apply_bands,
    marginal_band,
    quantize,
    to_decimal,
    to_float,
    to_rate,
)
from .tables import (
    STATUS_CONFIRMED,
    STATUS_PROVISIONAL,
    Band,
    RateTable,
    RateTableError,
    SourceRef,
    compare,
    get_table,
    latest_fiscal_year,
    list_fiscal_years,
    reload_tables,
    require_confirmed,
    resolve_fiscal_year,
)

__all__ = [
    "STATUS_CONFIRMED",
    "STATUS_PROVISIONAL",
    "AmountError",
    "Band",
    "RateTable",
    "RateTableError",
    "SourceRef",
    "apply_bands",
    "compare",
    "get_table",
    "latest_fiscal_year",
    "list_fiscal_years",
    "marginal_band",
    "quantize",
    "reload_tables",
    "require_confirmed",
    "resolve_fiscal_year",
    "to_decimal",
    "to_float",
    "to_rate",
]

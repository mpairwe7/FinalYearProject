"""Production gate for the semantic layer (ADR-0033, phase 3)."""

from __future__ import annotations

import os


def _on() -> bool:
    return os.getenv("FLAG_SEMANTIC_LAYER", "").strip().lower() in {"1", "true", "yes", "on"}


def production_errors() -> list[str]:
    """Startup errors when ``FLAG_SEMANTIC_LAYER`` is on in production; empty when off."""
    if not _on():
        return []
    errors = []
    if not os.getenv("VAULT_ADDR", "").startswith("https://"):
        errors.append("G507: FLAG_SEMANTIC_LAYER requires an https VAULT_ADDR; credentials come only from Vault.")
    if not os.getenv("SEMANTIC_LAYER_VAULT_PATH"):
        errors.append("G507: FLAG_SEMANTIC_LAYER requires SEMANTIC_LAYER_VAULT_PATH (the read-only DSN's secret).")
    if os.getenv("SEMANTIC_LAYER_DSN"):
        errors.append("G507: SEMANTIC_LAYER_DSN must not be set in production; read credentials from Vault.")
    if not os.getenv("SEMANTIC_LAYER_DSA_REF"):
        errors.append("G507: FLAG_SEMANTIC_LAYER requires SEMANTIC_LAYER_DSA_REF (the data-sharing agreement).")
    if not os.getenv("SEMANTIC_LAYER_DPIA_REF"):
        errors.append("G507: FLAG_SEMANTIC_LAYER requires SEMANTIC_LAYER_DPIA_REF (the data protection impact assessment).")
    try:
        from .catalogue import certified

        if not certified():
            errors.append("G507: FLAG_SEMANTIC_LAYER is on but no metric is certified.")
    except Exception as exc:
        errors.append(f"G507: the semantic-layer catalogue failed to load: {exc}")
    return errors

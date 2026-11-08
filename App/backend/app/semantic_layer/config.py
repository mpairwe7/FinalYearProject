"""Production gate for the semantic layer (ADR-0033, phase 3)."""

from __future__ import annotations

import os


def _on() -> bool:
    """Whether the layer can be on by any route: the env flag, a rollout or an in-process override.

    The gate used to read ``FLAG_SEMANTIC_LAYER`` alone, so a cohort rollout or an
    admin override switched the layer on with none of the checks below.
    """
    if os.getenv("FLAG_SEMANTIC_LAYER", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if any(os.getenv(f"FLAG_SEMANTIC_LAYER_{suffix}") for suffix in ("PERCENT", "COHORTS", "ALLOWLIST")):
        return True
    try:
        from ..flags import flags

        return flags.is_enabled("semantic_layer")
    except Exception:  # noqa: BLE001 - a flag registry that cannot answer is treated as on
        return True


def production_errors(*, force: bool = False) -> list[str]:
    """Errors that make the layer unsafe in production; empty when it is off.

    *force* runs the checks whatever the flag says: the tools call it before every
    use in production, so an override enabled after start-up is checked too.
    """
    if not force and not _on():
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

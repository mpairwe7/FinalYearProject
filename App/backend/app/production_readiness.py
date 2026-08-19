"""Production activation gates for prototype-scoped gaps (G12–G15, G29–G31).

Development keeps sandbox defaults. ``APP_ENV=production`` refuses those
defaults so a go-live cannot silently ship mock balances, fixture news,
or unacked multi-tenant RLS. This module does **not** invent a live URA
account API, an email/SMS network, or a cluster autoscaler.

CLI::

    PYTHONPATH=App/backend python3 -m app.production_readiness
    PYTHONPATH=App/backend python3 -m app.production_readiness --as-production
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse


def _truthy(name: str, default: str = "") -> bool:
    return (os.getenv(name) or default).strip().lower() in {"1", "true", "yes", "on"}


def is_production() -> bool:
    return (os.getenv("APP_ENV") or "development").lower() == "production"


def _https_url(raw: str) -> bool:
    parsed = urlparse((raw or "").strip())
    return parsed.scheme == "https" and bool(parsed.netloc)


# What "full delivery" still needs after the gate is green. Operators
# flip the env; they do not get a fake integration.
FULL_DELIVERY: dict[str, str] = {
    "G12": (
        "A real URA account contract: https URA_ACCOUNT_API_BASE, token, "
        "and URA_ACCOUNT_LIVE_ACK=true. Until then mode must stay off "
        "(never mock). This repo does not invent balances."
    ),
    "G13": (
        "ClamAV reachable at CLAMD_HOST:CLAMD_PORT plus a dedicated parse "
        "pool / gVisor later. MALWARE_SCAN_REQUIRED and DOCUMENT_PARSE_ISOLATED "
        "must be on; the API does not start ClamAV for you."
    ),
    "G14": (
        "SES or Africa's Talking credentials and a sender. In-app inbox "
        "and the mock outbox stay; NOTIFICATION_LIVE must stay false."
    ),
    "G15": (
        "URA_PUBLICATIONS_URL must be a live https page. Nightly ingest "
        "never auto-recreates the index."
    ),
    "G29": (
        "Measured eval then EVAL_GATE_OK=1 before any DPO_RUN. No trainer "
        "is wired; the job still exits 2."
    ),
    "G30": (
        "Apply infra/postgres/rls.sql on the production database, then set "
        "MULTI_TENANT_RLS_APPLIED=true. The predicate alone is not RLS."
    ),
    "G31": (
        "Git-backed FAQ editor later. Exact-match overrides are enough for "
        "staff corrections. SEED_PROTOTYPE must stay false."
    ),
    "G33": "Apply infra/k8s/hpa-chat.yaml and keda-chat.yaml after a measured p95.",
    "G34": "Run a cluster game day. In-process fail-closed tests are not that drill.",
    "G35": (
        "The DPO/legal owner must sign the DPIA, record the PDPO registration "
        "decision, and approve each external processor/cross-border transfer. "
        "Environment values are deployment attestations, not legal evidence."
    ),
}


def _account_errors() -> list[str]:
    mode = (os.getenv("URA_ACCOUNT_API_MODE") or "").strip().lower()
    if mode == "mock":
        return ["G12: URA_ACCOUNT_API_MODE=mock is not allowed in production."]
    if mode == "live":
        errors: list[str] = []
        base = (os.getenv("URA_ACCOUNT_API_BASE") or "").strip()
        token = (os.getenv("URA_ACCOUNT_API_TOKEN") or "").strip()
        if not _https_url(base):
            errors.append("G12: URA_ACCOUNT_API_MODE=live requires https URA_ACCOUNT_API_BASE.")
        if not token:
            errors.append("G12: URA_ACCOUNT_API_MODE=live requires URA_ACCOUNT_API_TOKEN.")
        if not _truthy("URA_ACCOUNT_LIVE_ACK"):
            errors.append(
                "G12: URA_ACCOUNT_LIVE_ACK=true is required for live mode "
                "(operator confirms a real URA contract; this repo still does not invent one)."
            )
        return errors
    return []


def _document_errors() -> list[str]:
    errors: list[str] = []
    if not _truthy("MALWARE_SCAN_REQUIRED"):
        errors.append("G13: MALWARE_SCAN_REQUIRED must be true in production.")
    if not _truthy("DOCUMENT_PARSE_ISOLATED"):
        errors.append("G13: DOCUMENT_PARSE_ISOLATED must be true in production.")
    return errors


def _notify_errors() -> list[str]:
    if _truthy("NOTIFICATION_LIVE") or _truthy("NOTIFY_LIVE"):
        return [
            "G14: NOTIFICATION_LIVE must stay false until a real sender is wired "
            "(SES / Africa's Talking). The mock outbox is not delivery."
        ]
    return []


def _publications_errors() -> list[str]:
    raw = (os.getenv("URA_PUBLICATIONS_URL") or "").strip()
    if raw in {"", "fixture", "fixture://ura-publications"}:
        return ["G15: URA_PUBLICATIONS_URL must be an https URL in production (fixture is demo-only)."]
    if not _https_url(raw):
        return ["G15: URA_PUBLICATIONS_URL must be https in production."]
    return []


def _dpo_errors() -> list[str]:
    if _truthy("DPO_RUN") and not _truthy("EVAL_GATE_OK"):
        return ["G29: DPO_RUN requires EVAL_GATE_OK after a measured eval."]
    return []


def _tenancy_errors() -> list[str]:
    flag = (os.getenv("FLAG_MULTI_TENANT") or "true").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return []
    if not _truthy("MULTI_TENANT_RLS_APPLIED"):
        return [
            "G30: MULTI_TENANT_RLS_APPLIED=true is required in production "
            "(apply infra/postgres/rls.sql; the app predicate is not RLS)."
        ]
    return []


def _seed_errors() -> list[str]:
    raw = (os.getenv("SEED_PROTOTYPE") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return ["G31: SEED_PROTOTYPE must be false in production."]
    return []


def _privacy_governance_errors() -> list[str]:
    """Require deployer attestations for controls that code cannot perform."""
    errors: list[str] = []
    if not _truthy("DPIA_APPROVED"):
        errors.append("G35: DPIA_APPROVED=true is required in production after DPO/legal sign-off.")
    if not (os.getenv("DPIA_APPROVAL_REFERENCE") or "").strip():
        errors.append("G35: DPIA_APPROVAL_REFERENCE is required in production (signed DPIA evidence id).")
    registration = (os.getenv("PDPO_REGISTRATION_STATUS") or "").strip().lower()
    if registration not in {"confirmed", "not_required"}:
        errors.append(
            "G35: PDPO_REGISTRATION_STATUS must be confirmed or not_required after a documented DPO decision."
        )
    if not (os.getenv("PDPO_REGISTRATION_REFERENCE") or "").strip():
        errors.append(
            "G35: PDPO_REGISTRATION_REFERENCE is required in production (registration or DPO decision evidence id)."
        )
    return errors


def _external_processor_names() -> list[str]:
    """Configured outbound processors that can receive user content."""
    providers: list[str] = []
    if os.getenv("SUNBIRD_API_TOKEN") or os.getenv("SUNBIRD_FALLBACK_API_TOKEN"):
        providers.append("Sunbird")
    cloudflare_enabled = _truthy("FLAG_CLOUDFLARE_FALLBACK")
    # An unset dense selector is intentionally "auto" when the master switch
    # is on, so the master switch itself is the material processing decision.
    if cloudflare_enabled:
        providers.append("Cloudflare/Gemini or Workers AI")
    vllm_url = urlparse((os.getenv("VLLM_BASE_URL") or "").strip())
    if (
        (os.getenv("LLM_BACKEND") or "").strip().lower() == "vllm"
        and vllm_url.hostname not in {None, "localhost", "127.0.0.1", "::1"}
    ):
        providers.append("remote vLLM")
    return providers


def _cross_border_errors() -> list[str]:
    providers = _external_processor_names()
    if not providers:
        return []
    errors: list[str] = []
    if not _truthy("CROSS_BORDER_PROCESSING_APPROVED"):
        errors.append(
            f"G35: external processing ({', '.join(providers)}) requires "
            "CROSS_BORDER_PROCESSING_APPROVED=true after the transfer assessment "
            "and processor disclosure are approved."
        )
    if not (os.getenv("CROSS_BORDER_TRANSFER_ASSESSMENT_ID") or "").strip():
        errors.append(
            "G35: external processing requires CROSS_BORDER_TRANSFER_ASSESSMENT_ID "
            "(approved transfer-assessment evidence id)."
        )
    return errors


def gap_gate_errors() -> list[str]:
    """Production-only errors for remaining prototype features. Empty in development."""
    if not is_production():
        return []
    errors: list[str] = []
    errors.extend(_account_errors())
    errors.extend(_document_errors())
    errors.extend(_notify_errors())
    errors.extend(_publications_errors())
    errors.extend(_dpo_errors())
    errors.extend(_tenancy_errors())
    errors.extend(_seed_errors())
    errors.extend(_privacy_governance_errors())
    errors.extend(_cross_border_errors())
    return errors


def evaluate_gate(gap: str) -> dict[str, Any]:
    """Evaluate one gap as if deciding go-live. Used by the report."""
    checkers = {
        "G12": _account_errors,
        "G13": _document_errors,
        "G14": _notify_errors,
        "G15": _publications_errors,
        "G29": _dpo_errors,
        "G30": _tenancy_errors,
        "G31": _seed_errors,
        "G35": lambda: _privacy_governance_errors() + _cross_border_errors(),
    }
    deferred = {"G33", "G34"}
    if gap in deferred:
        return {
            "gap": gap,
            "ok": True,
            "blocker": False,
            "status": "deferred",
            "errors": [],
            "full_delivery": FULL_DELIVERY[gap],
        }
    errors = checkers[gap]()
    return {
        "gap": gap,
        "ok": not errors,
        "blocker": bool(errors) and is_production(),
        "status": "ready" if not errors else "blocked",
        "errors": errors,
        "full_delivery": FULL_DELIVERY[gap],
    }


def readiness_report(*, as_production: bool = False) -> dict[str, Any]:
    """Per-gap report. ``as_production`` evaluates gates even in development."""
    saved = os.environ.get("APP_ENV")
    if as_production:
        os.environ["APP_ENV"] = "production"
    try:
        gaps = ["G12", "G13", "G14", "G15", "G29", "G30", "G31", "G33", "G34", "G35"]
        items = [evaluate_gate(gap) for gap in gaps]
        errors = gap_gate_errors()
        return {
            "app_env": os.getenv("APP_ENV") or "development",
            "ok": not errors,
            "errors": errors,
            "gaps": items,
        }
    finally:
        if as_production:
            if saved is None:
                os.environ.pop("APP_ENV", None)
            else:
                os.environ["APP_ENV"] = saved


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--as-production",
        action="store_true",
        help="Evaluate gates as APP_ENV=production without changing the running env permanently.",
    )
    args = parser.parse_args(argv)
    report = readiness_report(as_production=args.as_production)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

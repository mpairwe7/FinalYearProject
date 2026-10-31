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

import ipaddress
import json
import os
import re
from typing import Any
from urllib.parse import urlparse

COMMERCIAL_KEY_ENV_VARS: tuple[str, ...] = (
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "CF_AIG_TOKEN",
    "CLOUDFLARE_API_TOKEN",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "TAVILY_API_KEY",
    "BRAVE_SEARCH_API_KEY",
)

_COMPOSE_SERVICE_HOST_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$", re.IGNORECASE)
_RFC1918_NETWORKS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
)


def _truthy(name: str, default: str = "") -> bool:
    return (os.getenv(name) or default).strip().lower() in {"1", "true", "yes", "on"}


def is_production() -> bool:
    return (os.getenv("APP_ENV") or "development").lower() == "production"


def is_sovereign_mode() -> bool:
    """Return True when SOVEREIGN_MODE is enabled for strict local-only data sovereignty."""
    return _truthy("SOVEREIGN_MODE")


def _is_allowed_sovereign_llm_host(hostname: str | None) -> bool:
    """Return True if ``hostname`` is localhost, loopback, RFC 1918 private IP, or a compose service name."""
    if not hostname:
        return False
    host = hostname.strip().lower()
    if host in {"localhost", "host.docker.internal"}:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Must be a single-label Docker Compose service name (no dots / external domains)
        return "." not in host and bool(_COMPOSE_SERVICE_HOST_RE.match(host))
    if ip.is_loopback:
        return True
    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in net for net in _RFC1918_NETWORKS)
    return ip.is_private and not ip.is_link_local


def sovereign_mode_errors() -> list[str]:
    """Return startup validation errors when ``SOVEREIGN_MODE=true`` (empty when disabled)."""
    if not is_sovereign_mode():
        return []

    from .flags import flags

    errors: list[str] = []
    if flags.is_enabled("cloudflare_fallback") or _truthy("FLAG_CLOUDFLARE_FALLBACK"):
        errors.append("SOVEREIGN_MODE: cloudflare_fallback flag must be disabled.")

    fallback_backend = (os.getenv("LLM_FALLBACK_BACKEND") or "").strip()
    if fallback_backend:
        errors.append("SOVEREIGN_MODE: LLM_FALLBACK_BACKEND must be empty.")

    primary_backend = (os.getenv("LLM_PRIMARY_BACKEND") or "").strip().lower()
    if primary_backend not in {"", "local", "vllm"}:
        errors.append("SOVEREIGN_MODE: LLM_PRIMARY_BACKEND must be local ('local' or 'vllm').")

    llm_backend = (os.getenv("LLM_BACKEND") or "local").strip().lower()
    if llm_backend not in {"", "local", "vllm"}:
        errors.append("SOVEREIGN_MODE: LLM_BACKEND must be local ('local' or 'vllm').")

    for url_var in ("VLLM_BASE_URL", "REASONER_BASE_URL", "SEARXNG_URL"):
        raw_url = (os.getenv(url_var) or "").strip()
        if not raw_url:
            continue
        parsed = urlparse(raw_url)
        if parsed.scheme not in {"http", "https"} or not _is_allowed_sovereign_llm_host(parsed.hostname):
            errors.append(
                f"SOVEREIGN_MODE: {url_var} must resolve to a local compose service, localhost, or RFC 1918 private address."
            )

    for key_var in COMMERCIAL_KEY_ENV_VARS:
        if (os.getenv(key_var) or "").strip():
            # Log only the variable name, NEVER its value (§0.3 & §5.C.2)
            errors.append(
                f"SOVEREIGN_MODE: commercial API key environment variable {key_var} must not be set."
            )

    return errors


def assert_sovereign_mode() -> None:
    """Fail startup with SystemExit when SOVEREIGN_MODE=true and any commercial path is enabled."""
    errors = sovereign_mode_errors()
    if errors:
        msg = "SOVEREIGN MODE CHECK FAILED — refusing to start.\n" + "\n".join(
            f"  • {e}" for e in errors
        )
        raise SystemExit(msg)


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
    "G33": (
        "Measured p95 is in docs/runbooks/capacity-slo.md "
        "(2026-08-19, one A6000). Apply infra/k8s/hpa-chat.yaml and "
        "keda-chat.yaml only after agreeing the hybrid vs blended SLI; "
        "do not invent replica counts."
    ),
    "G34": "Run a cluster game day. In-process fail-closed tests are not that drill.",
    "G35": (
        "The DPO/legal owner must sign the DPIA, record the PDPO registration "
        "decision, and approve each external processor/cross-border transfer. "
        "Environment values are deployment attestations, not legal evidence."
    ),
    "G36": (
        "Caller and officer media use self-hosted LiveKit WebRTC with short-lived "
        "room-scoped tokens. Active-call coordination remains process-local: run "
        "one API worker/replica and explicitly acknowledge that process loss ends "
        "active calls. Shared actor state and seamless call failover remain required "
        "before horizontal scaling."
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
        from .notify import configured_providers

        provs = configured_providers()
        # Verify at least one real external delivery provider (email, sms, webhook) is configured
        has_configured_provider = any(
            k != "in_app" and p.get("configured") and p.get("backend") not in ("live_sandbox", "mock")
            for k, p in provs.items()
        )
        if not has_configured_provider and not _truthy("ALLOW_SANDBOX_NOTIFY"):
            return [
                "G14: NOTIFICATION_LIVE is enabled but no real delivery provider is configured "
                "(RESEND_API_KEY, SMTP_HOST, AFRICASTALKING_API_KEY, TWILIO_ACCOUNT_SID, or WEBHOOK_NOTIFY_URL required)."
            ]
    return []


def _publications_errors() -> list[str]:
    raw = (os.getenv("URA_PUBLICATIONS_URL") or "").strip()
    if raw in {"", "fixture", "fixture://ura-publications"}:
        return ["G15: URA_PUBLICATIONS_URL must be an https URL in production (fixture is demo-only)."]
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts or not all(_https_url(p) for p in parts):
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


def _receptionist_errors() -> list[str]:
    from .receptionist.config import local_engine_errors
    from .receptionist.livekit import production_errors

    return [*production_errors(), *local_engine_errors()]


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
    # A search query leaves the deployment too. It is built from a closed
    # vocabulary, but that is a reason to approve the transfer, not to skip it.
    from .web_search_providers import hosted_provider_names

    if _truthy("FLAG_LIVE_WEB_RESEARCH"):
        providers.extend(f"web search ({name})" for name in hosted_provider_names())
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


def _mcp_security_errors() -> list[str]:
    """Validate optional remote MCP server URLs and MRTR state secret in production."""
    from .mcp.transport import URL_ENV_PREFIX, remote_url_problem

    errors: list[str] = []
    state_secret = (os.getenv("MCP_REQUEST_STATE_SECRET") or "").strip()
    if state_secret and len(state_secret) < 32:
        errors.append("MCP: MCP_REQUEST_STATE_SECRET must be at least 32 characters in production.")
    for key, value in sorted(os.environ.items()):
        if not key.startswith(URL_ENV_PREFIX) or not value.strip():
            continue
        namespace = key[len(URL_ENV_PREFIX) :].lower()
        problem = remote_url_problem(value.strip())
        if problem:
            errors.append(f"MCP ({namespace}): {problem}.")
    return errors


def gap_gate_errors() -> list[str]:
    """Production-only errors for remaining prototype features, plus sovereign-mode errors when SOVEREIGN_MODE=true."""
    errors: list[str] = []
    if is_sovereign_mode():
        errors.extend(sovereign_mode_errors())
    if not is_production():
        return errors
    if _truthy("EVAL_DEBUG"):
        errors.append("EVAL_DEBUG=1 must not be enabled in production.")
    errors.extend(_account_errors())
    errors.extend(_document_errors())
    errors.extend(_notify_errors())
    errors.extend(_publications_errors())
    errors.extend(_dpo_errors())
    errors.extend(_tenancy_errors())
    errors.extend(_seed_errors())
    errors.extend(_receptionist_errors())
    errors.extend(_privacy_governance_errors())
    errors.extend(_cross_border_errors())
    errors.extend(_mcp_security_errors())
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
        "G36": _receptionist_errors,
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
    if gap == "G36" and not errors and not _truthy("FLAG_VOICE_RECEPTIONIST"):
        return {
            "gap": gap,
            "ok": True,
            "blocker": False,
            "status": "disabled",
            "errors": [],
            "full_delivery": FULL_DELIVERY[gap],
        }
    return {
        "gap": gap,
        "ok": not errors,
        "blocker": bool(errors) and is_production(),
        "status": "ready" if not errors else "blocked",
        "errors": errors,
        "full_delivery": FULL_DELIVERY[gap],
    }


def _live_research_status() -> dict[str, Any]:
    """Whether live research can do anything here, for the readiness report.

    Deliberately not a boot gate. No provider degrades to a disclosed "could not
    check", which is safe; refusing to start would turn a missing key into an
    outage of every other answer.
    """
    from .flags import flags
    from .web_search_providers import active_providers, sanctioned_provider_configured

    return {
        "enabled": flags.is_enabled("live_web_research"),
        "providers": [provider.name for provider in active_providers()],
        "sanctioned_provider": sanctioned_provider_configured(),
    }


def architecture_integrity_status() -> dict[str, Any]:
    """Verify October 2026 architectural invariants (ADR-0012): storage parity, ASI10 manifest, portal pack, and ExpRAG."""
    from .flags import flags
    from .memory.procedural import ProceduralMemory
    from .storage_protocol import verify_storage_backend_parity
    from .tools.portal_navigator_data import get_portal_knowledge_provenance
    from .turn_pipeline import CANONICAL_BEHAVIORAL_MANIFEST

    parity = verify_storage_backend_parity()
    portal_prov = get_portal_knowledge_provenance()
    lifecycle = flags.lifecycle_summary()
    pm = ProceduralMemory()
    return {
        "ok": bool(parity["ok"] and portal_prov.get("loaded")),
        "storage_backend_parity": parity,
        "behavioral_manifest_sha256": CANONICAL_BEHAVIORAL_MANIFEST.digest_sha256(),
        "portal_knowledge_sha256": portal_prov.get("sha256", ""),
        "procedural_exemplar_count": len(pm.list_exemplars()),
        "live_research": _live_research_status(),
        "flag_lifecycle_counts": {k: len(v) for k, v in lifecycle.items()},
    }


def readiness_report(*, as_production: bool = False) -> dict[str, Any]:
    """Per-gap report. ``as_production`` evaluates gates even in development."""
    saved = os.environ.get("APP_ENV")
    if as_production:
        os.environ["APP_ENV"] = "production"
    try:
        gaps = ["G12", "G13", "G14", "G15", "G29", "G30", "G31", "G33", "G34", "G35", "G36"]
        items = [evaluate_gate(gap) for gap in gaps]
        errors = gap_gate_errors()
        arch = architecture_integrity_status()
        if not arch["ok"]:
            errors.append("ADR-0012: architecture_integrity_status failed parity or provenance check.")
        return {
            "app_env": os.getenv("APP_ENV") or "development",
            "ok": not errors,
            "errors": errors,
            "gaps": items,
            "architecture_integrity": arch,
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

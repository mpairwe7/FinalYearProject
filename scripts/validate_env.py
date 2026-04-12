#!/usr/bin/env python3
"""Environment variable validation script.

Run before deployment to catch configuration errors early.
Exits 0 on success, 1 on any critical misconfiguration.

Usage:
    python scripts/validate_env.py
    python scripts/validate_env.py --env production

Standards: NIST SSDF PO.1.1, ISO/IEC 25010:2023 §5 (Reliability)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REQUIRED_VARS: dict[str, str] = {
    "HF_TOKEN": "Hugging Face API token for model access",
    "QDRANT_URL": "Qdrant vector store endpoint",
}

PRODUCTION_REQUIRED: dict[str, str] = {
    "CORS_ORIGINS": "Allowed CORS origins (must not contain localhost)",
    "SLOWAPI_STORAGE_URI": "Redis URI for distributed rate limiting",
    "LLM_MODEL_REVISION": "Pinned model revision (SLSA v1.2)",
}

INSECURE_DEFAULTS: dict[str, str] = {
    "AUTH_DEV_SECRET": "dev-insecure-change-me",
}

MUST_BE_FALSE_IN_PROD: list[str] = [
    "STORE_RAW_PROMPTS",
    "LLM_TRUST_REMOTE_CODE",
]


def validate(env: str = "development") -> tuple[list[str], list[str]]:
    """Return list of error messages. Empty = all good."""
    errors: list[str] = []
    warnings: list[str] = []
    is_prod = env == "production"

    # Load .env if present
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

    # Check required vars
    for var, desc in REQUIRED_VARS.items():
        if not os.getenv(var):
            errors.append(f"MISSING: {var} — {desc}")

    # Production-only checks
    if is_prod:
        for var, desc in PRODUCTION_REQUIRED.items():
            if not os.getenv(var):
                errors.append(f"PRODUCTION MISSING: {var} — {desc}")

        for var, default in INSECURE_DEFAULTS.items():
            if os.getenv(var, default) == default:
                errors.append(
                    f"INSECURE DEFAULT: {var} is still '{default}'. "
                    "Rotate before deploying to production."
                )

        for var in MUST_BE_FALSE_IN_PROD:
            val = os.getenv(var, "false").lower()
            if val in ("1", "true", "yes", "on"):
                errors.append(f"UNSAFE: {var}={val} must be false in production")

        cors = os.getenv("CORS_ORIGINS", "")
        if "localhost" in cors or "127.0.0.1" in cors:
            errors.append("CORS_ORIGINS contains localhost — not safe for production")
    else:
        # Dev-mode warnings
        if not os.getenv("HF_TOKEN"):
            warnings.append("HF_TOKEN not set — model download may fail")

    return errors, warnings


def main() -> None:
    env = "development"
    if "--env" in sys.argv:
        idx = sys.argv.index("--env")
        if idx + 1 < len(sys.argv):
            env = sys.argv[idx + 1]

    print(f"Validating environment: {env}")
    print("-" * 50)

    errors, warnings = validate(env)

    for w in warnings:
        print(f"  WARNING: {w}")

    for e in errors:
        print(f"  ERROR: {e}")

    print("-" * 50)
    if errors:
        print(f"FAILED: {len(errors)} error(s) found.")
        sys.exit(1)
    else:
        print(f"PASSED ({len(warnings)} warning(s)).")
        sys.exit(0)


if __name__ == "__main__":
    main()

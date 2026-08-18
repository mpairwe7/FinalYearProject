"""Sandbox taxpayer profiles — never a live URA account.

Enabled only when ``URA_ACCOUNT_API_MODE=mock``. Production startup
rejects that mode. Responses always set ``live=false`` and
``source=mock``.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

# Obviously fake TINs. Do not treat these as real taxpayers.
MOCK_PROFILES: dict[str, dict[str, Any]] = {
    "1999999999": {
        "tin": "1999999999",
        "display_name": "Sandbox Taxpayer (individual)",
        "taxpayer_type": "individual",
        "status": "active",
        "registered_tax_types": ["income", "vat"],
        "balance_ugx": 0,
        "returns_due": [],
        "note": "Placeholder. Not URA data.",
    },
    "1888888888": {
        "tin": "1888888888",
        "display_name": "Sandbox SME Ltd",
        "taxpayer_type": "company",
        "status": "active",
        "registered_tax_types": ["vat", "paye", "corporation"],
        "balance_ugx": 0,
        "returns_due": [
            {"name": "VAT return", "period": "sandbox-fy", "status": "not_filed"},
        ],
        "note": "Placeholder. Not URA data.",
    },
}

GENERIC_SANDBOX = {
    "tin": "",
    "display_name": "Sandbox taxpayer",
    "taxpayer_type": "unknown",
    "status": "sandbox",
    "registered_tax_types": [],
    "balance_ugx": 0,
    "returns_due": [],
    "note": "Placeholder. Not URA data.",
}


def account_mode() -> str:
    """Prototype default is mock. Production never defaults to mock."""
    raw = (os.getenv("URA_ACCOUNT_API_MODE") or "").strip().lower()
    env = (os.getenv("APP_ENV") or "development").lower()
    if env == "production":
        if raw == "mock":
            return "off"
        return raw if raw in {"off", "live"} else "off"
    if raw in {"off", "mock", "live"}:
        return raw
    return "mock"


def live_credentials_configured() -> bool:
    base = (os.getenv("URA_ACCOUNT_API_BASE") or "").strip()
    token = (os.getenv("URA_ACCOUNT_API_TOKEN") or "").strip()
    parsed = urlparse(base)
    return parsed.scheme == "https" and bool(parsed.netloc) and bool(token)


def lookup_mock(taxpayer_id: str) -> dict[str, Any]:
    key = str(taxpayer_id or "").strip()
    profile = dict(MOCK_PROFILES.get(key) or MOCK_PROFILES["1999999999"])
    if key and key not in MOCK_PROFILES:
        profile["linked_subject"] = key
    return {
        "ok": True,
        "configured": False,
        "live": False,
        "source": "mock",
        "mode": "mock",
        "profile": profile,
    }

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


def generate_mock_prn(
    tax_type: str = "Income Tax",
    amount_ugx: float | int = 0,
    tin: str = "1999999999",
    taxpayer_name: str = "Sandbox Taxpayer",
) -> dict[str, Any]:
    """Generate a realistic simulated PRN voucher for sandbox/prototype execution."""
    import time
    import random

    now = time.time()
    prn_suffix = f"{random.randint(10000000, 99999999)}"
    prn = f"26{prn_suffix}"
    expiry_time = now + (21 * 86400)
    amt_formatted = f"UGX {amount_ugx:,.0f}" if amount_ugx else "UGX 0"

    return {
        "ok": True,
        "live": False,
        "source": "mock_prn_gateway",
        "prn": prn,
        "search_code": f"ASMT-{random.randint(100000, 999999)}",
        "tax_head": (tax_type or "General Tax").title(),
        "amount_ugx": float(amount_ugx),
        "amount_formatted": amt_formatted,
        "tin": tin or "1999999999",
        "taxpayer_name": taxpayer_name or "Sandbox Taxpayer",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
        "expires_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(expiry_time)),
        "expiry_days": 21,
    }


def format_prn_voucher_reply(voucher: dict[str, Any], locale: str = "en") -> str:
    """Format a transactional PRN voucher into clear, actionable instructions."""
    prn = voucher.get("prn", "")
    code = voucher.get("search_code", "")
    tax = voucher.get("tax_head", "Tax Assessment")
    amt = voucher.get("amount_formatted", "UGX 0")
    tin = voucher.get("tin", "1999999999")
    name = voucher.get("taxpayer_name", "Sandbox Taxpayer")
    exp = voucher.get("expires_at", "").split()[0]

    if locale == "lg":
        return (
            f"### 🧾 Satifiketi y'Ensasula ya PRN (Voucher)\n"
            f"*Enkola: Ey'okukakasa mu Sandbox (Simulated Voucher)*\n\n"
            f"- **Namba ya PRN**: `{prn}`\n"
            f"- **Ensimbi Ezisasulwa**: **{amt}**\n"
            f"- **Ekika ky'Omusolo**: {tax}\n"
            f"- **Omusasuzi**: {name} (`TIN: {tin}`)\n"
            f"- **Namba y'Okunoonyereza**: `{code}`\n"
            f"- **Ekoma Okukola**: {exp} (ennaku 21)\n\n"
            f"**Engeri gy'Osasulamu**:\n"
            f"1. **Mobile Money**: Koona `*165#` (MTN) oba `*185#` (Airtel) → Sasula URA → Yingiza PRN `{prn}`.\n"
            f"2. **Bbanka Yonna**: Ttwaala namba ya PRN eya digito 10 ku kaawunta ya bbanka yonna mu Uganda.\n"
            f"3. **Omukutu gwa URA**: Sasulira ku yintaneeti: https://ura.go.ug → e-Services → Make a Payment."
        )

    if locale == "sw":
        return (
            f"### 🧾 Vocha ya Nambari ya Malipo (PRN)\n"
            f"*Hali: Jaribio la Sandbox (Simulated Voucher)*\n\n"
            f"- **Nambari ya PRN**: `{prn}`\n"
            f"- **Kiasi cha Kulipwa**: **{amt}**\n"
            f"- **Aina ya Kodi**: {tax}\n"
            f"- **Mlipakodi**: {name} (`TIN: {tin}`)\n"
            f"- **Nambari ya Rejea**: `{code}`\n"
            f"- **Mwisho wa Matumizi**: {exp} (siku 21)\n\n"
            f"**Jinsi ya Kukamilisha Malipo**:\n"
            f"1. **Pesa kwa Simu**: Piga `*165#` (MTN) au `*185#` (Airtel) → Lipa Kodi ya URA → Weka PRN `{prn}`.\n"
            f"2. **Benki Yoyote ya Biashara**: Peana nambari hii ya PRN yenye tarakimu 10 kwa kaunta ya benki.\n"
            f"3. **Tovuti ya URA**: Lipa mtandaoni kwa kadi (Visa/Mastercard): https://ura.go.ug."
        )

    return (
        f"### 🧾 Payment Registration Number (PRN) Voucher\n"
        f"*Mode: Prototype Sandbox / Simulated Payment Voucher*\n\n"
        f"- **PRN Number**: `{prn}`\n"
        f"- **Amount Payable**: **{amt}**\n"
        f"- **Tax Head**: {tax}\n"
        f"- **Taxpayer Name**: {name} (`TIN: {tin}`)\n"
        f"- **Search / Assessment Code**: `{code}`\n"
        f"- **Valid Until**: {exp} (21 calendar days)\n\n"
        f"**How to Complete Payment**:\n"
        f"1. **Mobile Money**: Dial `*165#` (MTN) or `*185#` (Airtel) → Pay Services / Pay Bill → URA Taxes → Enter PRN `{prn}`.\n"
        f"2. **Commercial Bank Counter**: Present the 10-digit PRN to any bank teller across Uganda.\n"
        f"3. **Online Card Payment**: Pay directly online using VISA / Mastercard via the official e-Services portal: https://ura.go.ug/en/domestic-taxes/make-a-payment/."
    )

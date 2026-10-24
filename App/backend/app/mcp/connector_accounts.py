"""Local simulation account sign-in, sign-up, and session linking for MCP connectors.

In development/simulation mode, adding a URA or government system connector
(EFRIS, Digital Tax Stamps, Payments, TIN services, URSB, BWIMS) to a chat
requires linking a local simulation account — either by signing in to an
existing simulation profile (TIN, URSB BRN, email, or entity name) or by
creating a new local simulation profile that is provisioned across the local
simulator SQLite databases (`data_store/*.db`).

In production (`APP_ENV=production`), local simulation account creation and
sign-in are refused fail-closed (`409 Conflict`).
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_UTC = getattr(datetime, "UTC", datetime.timezone.utc)  # noqa: UP017
_TIN_RE = re.compile(r"^\d{10}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NAMESPACE_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_PBKDF2_ITERATIONS = 200_000

SIMULATION_NAMESPACES: tuple[str, ...] = (
    "efris",
    "digital_tax_stamps",
    "payment_system",
    "tin_registration",
    "ursb",
    "bwims",
)

DEMO_CONNECTOR_PRESETS: list[dict[str, str]] = [
    {
        "tin": "1000000001",
        "brn": "URSB-CO-10001",
        "entity_name": "Kakira Sugar Limited",
        "email": "efris@kakirasugar.com",
        "category": "COMPANY",
        "district": "Jinja",
        "hint": "Large Manufacturer · EFRIS, DTS, TIN, URSB",
    },
    {
        "tin": "1000000005",
        "brn": "URSB-CO-10005",
        "entity_name": "Kampala City Supermarket Ltd",
        "email": "grace@kampalacitysupermarket.ug",
        "category": "COMPANY",
        "district": "Kampala",
        "hint": "Retail & Wholesale · EFRIS, URSB, Payments, TIN",
    },
    {
        "tin": "1000000008",
        "brn": "URSB-BN-55102",
        "entity_name": "Pearl Organic Coffee Traders",
        "email": "david@pearlorganic.ug",
        "category": "INDIVIDUAL",
        "district": "Mukono",
        "hint": "Sole Proprietor · URSB Business Name, TIN",
    },
]

_db_lock = threading.Lock()
_mem_links: dict[tuple[str, str], dict[str, Any]] = {}


def _data_store_dir() -> Path:
    env_dir = os.getenv("CONNECTOR_DATA_STORE_DIR")
    if env_dir:
        p = Path(env_dir)
    else:
        root = Path(os.getenv("PROJECT_ROOT") or Path(__file__).resolve().parents[4])
        p = root / "data_store"
    try:
        p.mkdir(parents=True, exist_ok=True)
        if not os.access(p, os.W_OK):
            raise PermissionError(f"{p} is not writable")
    except OSError:
        p = Path("/tmp/ura_connector_data_store")
        p.mkdir(parents=True, exist_ok=True)
    return p


def _accounts_db_path() -> Path:
    return _data_store_dir() / "connector_accounts.db"


def _connect() -> sqlite3.Connection:
    path = _accounts_db_path()
    try:
        conn = sqlite3.connect(str(path), timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        import tempfile

        fallback_dir = Path(tempfile.gettempdir()) / "ura_connector_data_store"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(fallback_dir / "connector_accounts.db"), timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS connector_accounts (
            account_id TEXT PRIMARY KEY,
            tin TEXT NOT NULL UNIQUE,
            brn TEXT NOT NULL,
            entity_name TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'INDIVIDUAL',
            nin_or_passport TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL,
            mobile TEXT NOT NULL DEFAULT '',
            district TEXT NOT NULL DEFAULT 'Kampala',
            password_salt TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS connector_account_links (
            owner_key TEXT NOT NULL,
            namespace TEXT NOT NULL,
            account_id TEXT NOT NULL,
            tin TEXT NOT NULL,
            brn TEXT NOT NULL,
            entity_name TEXT NOT NULL,
            email TEXT NOT NULL,
            category TEXT NOT NULL,
            connected_at REAL NOT NULL,
            PRIMARY KEY (owner_key, namespace)
        );
        """
    )
    return conn


def _hash_password(password: str, salt_hex: str) -> str:
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        _PBKDF2_ITERATIONS,
    )
    return dk.hex()


def _provision_across_simulator_dbs(
    *,
    tin: str,
    brn: str,
    entity_name: str,
    category: str,
    nin_or_passport: str,
    email: str,
    mobile: str,
    district: str,
) -> None:
    """Provision a newly signed-up simulation account into the local simulator SQLite DBs."""
    now_iso = datetime.datetime.now(_UTC).isoformat()
    date_str = now_iso[:10]
    cat_upper = "COMPANY" if category.upper() == "COMPANY" else "INDIVIDUAL"
    nin_val = nin_or_passport or ("CM900192837KLA" if cat_upper == "INDIVIDUAL" else "CF890019284KLA")
    mob_val = mobile or "+256772100999"
    dist_val = district or "Kampala"
    office_val = f"Plot 10, {dist_val} Road"

    # 1. TIN simulator DB
    try:
        from plugins.tin_registration.database import TinDatabase

        tin_db = TinDatabase()
        tin_cat = "NON_INDIVIDUAL_COMPANY" if cat_upper == "COMPANY" else "INDIVIDUAL"
        with tin_db._lock:
            conn = tin_db._get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO tin_taxpayers (
                        tin, legal_name, category, nin_or_passport, ursb_reg_no,
                        mobile, email, registered_office, district, registration_date,
                        status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?)
                    """,
                    (
                        tin,
                        entity_name,
                        tin_cat,
                        nin_val,
                        brn if cat_upper == "COMPANY" else None,
                        mob_val,
                        email,
                        office_val,
                        dist_val,
                        date_str,
                        now_iso,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO tin_obligations (
                        tin, tax_head, effective_from, status, filing_frequency, created_at
                    ) VALUES (?, ?, ?, 'ACTIVE', 'ANNUAL', ?)
                    """,
                    (
                        tin,
                        "CORPORATION_TAX" if cat_upper == "COMPANY" else "INCOME_TAX_INDIVIDUAL",
                        date_str,
                        now_iso,
                    ),
                )
                if cat_upper == "COMPANY":
                    conn.execute(
                        """
                        INSERT INTO tin_obligations (
                            tin, tax_head, effective_from, status, filing_frequency, created_at
                        ) VALUES (?, 'VAT_STANDARD', ?, 'ACTIVE', 'MONTHLY', ?)
                        """,
                        (tin, date_str, now_iso),
                    )
    except Exception:
        logger.debug("Simulator TIN provisioning skipped", exc_info=True)

    # 2. EFRIS simulator DB
    try:
        from plugins.efris.database import EfrisDatabase

        efris_db = EfrisDatabase()
        with efris_db._lock:
            conn = efris_db._get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO efris_taxpayers (
                        tin, business_name, is_vat_registered, efris_status,
                        mandated_sector, registration_date, integration_mode,
                        active_terminals, sdc_enabled, created_at
                    ) VALUES (?, ?, 1, 'ACTIVE', 'General Trade & Services', ?, 'Web Portal & System-to-System', '["EFD-SIM-01"]', 1, ?)
                    """,
                    (tin, entity_name, date_str, now_iso),
                )
    except Exception:
        logger.debug("Simulator EFRIS provisioning skipped", exc_info=True)

    # 3. URSB simulator DB
    try:
        from plugins.ursb.database import UrsbDatabase

        ursb_db = UrsbDatabase()
        ent_type = "LIMITED_COMPANY" if cat_upper == "COMPANY" else "BUSINESS_NAME"
        with ursb_db._lock:
            conn = ursb_db._get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO ursb_entities (
                        registration_number, business_name, entity_type, registration_date,
                        status, registered_office, district, nature_of_business,
                        latest_annual_returns_year, form_20_registered, created_at
                    ) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, 'General Commercial Trade & Services', 2026, 1, ?)
                    """,
                    (brn, entity_name, ent_type, date_str, office_val, dist_val, now_iso),
                )
    except Exception:
        logger.debug("Simulator URSB provisioning skipped", exc_info=True)

    # 4. Digital Tax Stamps (DTS) simulator DB
    try:
        from plugins.digital_tax_stamps.database import DtsDatabase

        dts_db = DtsDatabase()
        with dts_db._lock:
            conn = dts_db._get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO dts_manufacturers (
                        tin, manufacturer_name, taxpayer_type, gazetted_categories,
                        dts_registration_date, active_stamps_inventory, is_compliant, created_at
                    ) VALUES (?, ?, 'Local Manufacturer', '["Bottled Water", "Beverages"]', ?, 2500, 1, ?)
                    """,
                    (tin, entity_name, date_str, now_iso),
                )
    except Exception:
        logger.debug("Simulator DTS provisioning skipped", exc_info=True)


def _link_account_to_owner(
    owner_key: str,
    namespace: str,
    account: dict[str, Any],
    *,
    link_all: bool = True,
) -> dict[str, Any]:
    now_ts = round(time.time(), 3)
    target_namespaces = (
        list(SIMULATION_NAMESPACES)
        if link_all
        else [namespace]
    )
    if namespace not in target_namespaces and _NAMESPACE_RE.fullmatch(namespace):
        target_namespaces.append(namespace)

    record = {
        "account_id": str(account.get("account_id") or f"sim-{account['tin']}"),
        "tin": str(account["tin"]),
        "brn": str(account.get("brn") or ""),
        "entity_name": str(account["entity_name"]),
        "email": str(account.get("email") or ""),
        "category": str(account.get("category") or "INDIVIDUAL"),
        "connected_at": now_ts,
    }
    with _db_lock:
        for ns in target_namespaces:
            _mem_links[(owner_key, ns)] = dict(record)
        try:
            with _connect() as conn:
                for ns in target_namespaces:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO connector_account_links (
                            owner_key, namespace, account_id, tin, brn,
                            entity_name, email, category, connected_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            owner_key,
                            ns,
                            record["account_id"],
                            record["tin"],
                            record["brn"],
                            record["entity_name"],
                            record["email"],
                            record["category"],
                            now_ts,
                        ),
                    )
        except Exception:
            logger.debug("connector_account_links sqlite write failed", exc_info=True)
    return record


def signup_connector_account(
    *,
    owner_key: str,
    namespace: str,
    entity_name: str,
    email: str,
    password: str,
    category: str = "INDIVIDUAL",
    tin: str = "",
    brn: str = "",
    nin_or_passport: str = "",
    mobile: str = "",
    district: str = "Kampala",
    link_all: bool = True,
) -> dict[str, Any]:
    """Create a new local simulation account, provision it in simulator DBs, and link it."""
    clean_name = " ".join((entity_name or "").split()).strip()
    clean_email = (email or "").strip().lower()
    clean_pw = password or ""
    if len(clean_name) < 2 or len(clean_name) > 120:
        raise ValueError("Please provide a valid full name or business name (2–120 characters).")
    if not _EMAIL_RE.fullmatch(clean_email):
        raise ValueError("Please provide a valid email address.")
    if len(clean_pw) < 6:
        raise ValueError("Password must be at least 6 characters.")

    cat_upper = "COMPANY" if (category or "").strip().upper() == "COMPANY" else "INDIVIDUAL"
    clean_tin = (tin or "").strip()
    if clean_tin:
        if not _TIN_RE.fullmatch(clean_tin):
            raise ValueError("TIN must be an exact 10-digit number (or leave blank to auto-generate).")
    else:
        digest_int = int(hashlib.sha256(f"{clean_email}:{clean_name}".encode("utf-8")).hexdigest()[:8], 16)
        clean_tin = f"10{10000000 + (digest_int % 89999999):08d}"

    clean_brn = (brn or "").strip().upper()
    if not clean_brn:
        prefix = "URSB-CO" if cat_upper == "COMPANY" else "URSB-BN"
        clean_brn = f"{prefix}-{clean_tin[-5:]}"

    salt_hex = secrets.token_hex(16)
    pw_hash = _hash_password(clean_pw, salt_hex)
    account_id = f"sim-{clean_tin}"
    now_iso = datetime.datetime.now(_UTC).isoformat()

    with _db_lock:
        with _connect() as conn:
            existing = conn.execute(
                "SELECT tin, email FROM connector_accounts WHERE tin = ? OR LOWER(email) = ?",
                (clean_tin, clean_email),
            ).fetchone()
            if existing:
                raise ValueError(
                    f"A simulation account with TIN {clean_tin} or email {clean_email} already exists. Please sign in instead."
                )
            conn.execute(
                """
                INSERT INTO connector_accounts (
                    account_id, tin, brn, entity_name, category,
                    nin_or_passport, email, mobile, district,
                    password_salt, password_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    clean_tin,
                    clean_brn,
                    clean_name,
                    cat_upper,
                    (nin_or_passport or "").strip().upper(),
                    clean_email,
                    (mobile or "").strip(),
                    (district or "Kampala").strip(),
                    salt_hex,
                    pw_hash,
                    now_iso,
                ),
            )

    _provision_across_simulator_dbs(
        tin=clean_tin,
        brn=clean_brn,
        entity_name=clean_name,
        category=cat_upper,
        nin_or_passport=(nin_or_passport or "").strip().upper(),
        email=clean_email,
        mobile=(mobile or "").strip(),
        district=(district or "Kampala").strip(),
    )

    return _link_account_to_owner(
        owner_key,
        namespace,
        {
            "account_id": account_id,
            "tin": clean_tin,
            "brn": clean_brn,
            "entity_name": clean_name,
            "email": clean_email,
            "category": cat_upper,
        },
        link_all=link_all,
    )


def _lookup_fixture_account(identifier: str) -> dict[str, str] | None:
    ident_clean = identifier.strip()
    ident_low = ident_clean.lower()

    for preset in DEMO_CONNECTOR_PRESETS:
        if ident_low in (
            preset["tin"].lower(),
            preset["brn"].lower(),
            preset["email"].lower(),
            preset["entity_name"].lower(),
        ) or ident_low in preset["entity_name"].lower():
            return dict(preset)

    try:
        from plugins.tin_registration.database import TinDatabase

        tin_db = TinDatabase()
        rec = tin_db.search_taxpayer(ident_clean)
        if rec is not None:
            return {
                "tin": rec.tin,
                "brn": rec.ursb_reg_no or f"URSB-BN-{rec.tin[-5:]}",
                "entity_name": rec.legal_name,
                "email": rec.email,
                "category": rec.category.value if hasattr(rec.category, "value") else str(rec.category),
            }
    except Exception:
        pass

    try:
        from plugins.ursb.database import UrsbDatabase

        ursb_db = UrsbDatabase()
        ent = ursb_db.search_entity(ident_clean)
        if ent is not None:
            return {
                "tin": "1000000005" if ent.registration_number == "URSB-CO-10005" else "1000000001",
                "brn": ent.registration_number,
                "entity_name": ent.business_name,
                "email": f"info@{ent.registration_number.lower()}.ug",
                "category": "COMPANY" if "COMPANY" in str(ent.entity_type) else "INDIVIDUAL",
            }
    except Exception:
        pass

    return None


def login_connector_account(
    *,
    owner_key: str,
    namespace: str,
    identifier: str,
    password: str,
    link_all: bool = True,
) -> dict[str, Any]:
    """Authenticate an existing custom or fixture simulation account and link it."""
    ident = (identifier or "").strip()
    pw = password or ""
    if not ident:
        raise ValueError("Please enter your TIN, URSB Registration Number, email, or business name.")
    if len(pw) < 4:
        raise ValueError("Please enter your simulation password (at least 4 characters).")

    with _db_lock:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM connector_accounts
                WHERE tin = ?
                   OR UPPER(brn) = UPPER(?)
                   OR LOWER(email) = LOWER(?)
                   OR LOWER(entity_name) = LOWER(?)
                LIMIT 1
                """,
                (ident, ident, ident, ident),
            ).fetchone()

    if row is not None:
        expected_hash = str(row["password_hash"])
        actual_hash = _hash_password(pw, str(row["password_salt"]))
        if not hmac.compare_digest(actual_hash, expected_hash):
            raise ValueError("Invalid password for this simulation account.")
        return _link_account_to_owner(
            owner_key,
            namespace,
            {
                "account_id": str(row["account_id"]),
                "tin": str(row["tin"]),
                "brn": str(row["brn"]),
                "entity_name": str(row["entity_name"]),
                "email": str(row["email"]),
                "category": str(row["category"]),
            },
            link_all=link_all,
        )

    fixture = _lookup_fixture_account(ident)
    if fixture is not None:
        return _link_account_to_owner(
            owner_key,
            namespace,
            {
                "account_id": f"sim-{fixture['tin']}",
                "tin": fixture["tin"],
                "brn": fixture["brn"],
                "entity_name": fixture["entity_name"],
                "email": fixture["email"],
                "category": fixture["category"],
            },
            link_all=link_all,
        )

    raise ValueError(
        f"No simulation account found for '{ident}'. Use 'Create Account' to register a new simulation profile."
    )


def disconnect_connector_account(owner_key: str, namespace: str) -> None:
    """Unlink a simulation account for *namespace* (or all namespaces if ``namespace == 'all'``)."""
    if not owner_key:
        return
    with _db_lock:
        if namespace == "all":
            keys_to_drop = [k for k in _mem_links if k[0] == owner_key]
            for k in keys_to_drop:
                _mem_links.pop(k, None)
        else:
            _mem_links.pop((owner_key, namespace), None)
        try:
            with _connect() as conn:
                if namespace == "all":
                    conn.execute("DELETE FROM connector_account_links WHERE owner_key = ?", (owner_key,))
                else:
                    conn.execute(
                        "DELETE FROM connector_account_links WHERE owner_key = ? AND namespace = ?",
                        (owner_key, namespace),
                    )
        except Exception:
            logger.debug("connector_account_links sqlite delete failed", exc_info=True)


def get_all_linked_accounts(owner_key: str) -> dict[str, dict[str, Any]]:
    """Return ``{namespace: account_dict}`` for *owner_key*."""
    if not owner_key:
        return {}
    out: dict[str, dict[str, Any]] = {}
    with _db_lock:
        for (ok, ns), rec in _mem_links.items():
            if ok == owner_key:
                out[ns] = dict(rec)
        try:
            with _connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM connector_account_links WHERE owner_key = ?",
                    (owner_key,),
                ).fetchall()
                for r in rows:
                    ns = str(r["namespace"])
                    rec = {
                        "account_id": str(r["account_id"]),
                        "tin": str(r["tin"]),
                        "brn": str(r["brn"]),
                        "entity_name": str(r["entity_name"]),
                        "email": str(r["email"]),
                        "category": str(r["category"]),
                        "connected_at": float(r["connected_at"]),
                    }
                    out[ns] = rec
                    _mem_links[(owner_key, ns)] = rec
        except Exception:
            logger.debug("connector_account_links sqlite read failed", exc_info=True)
    return out


def format_linked_accounts_prompt_context(
    owner_key: str,
    namespaces: list[str] | None,
) -> str:
    """Build a concise prompt context block describing the caller's connected simulation account(s)."""
    if not owner_key or not namespaces:
        return ""
    linked = get_all_linked_accounts(owner_key)
    if not linked:
        return ""
    lines: list[str] = []
    for ns in namespaces:
        acc = linked.get(ns)
        if not acc:
            continue
        lines.append(
            f"- Connected Simulation Profile ({ns}): Entity/Taxpayer='{acc['entity_name']}', "
            f"TIN='{acc['tin']}', URSB_BRN='{acc['brn']}', Category='{acc['category']}' "
            "(Simulation mode only, live=false — use these identifiers when the user refers to 'my TIN', 'my business', 'my EFRIS', or 'my account')."
        )
    if not lines:
        return ""
    return "## Connected Local Simulation Accounts\n" + "\n".join(lines)

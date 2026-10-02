"""Independent SQLite database for the URA Tax Identification Number (TIN) Registration System.

Maintains dedicated persistence for registered taxpayers, tax obligations (VAT, PAYE, CIT),
and instant TIN issuance applications under data_store/tin_system.db.
"""

from __future__ import annotations

import datetime
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .models import TaxHeadType, TaxObligation, TaxpayerCategory, TaxpayerRecord

logger = logging.getLogger(__name__)

_UTC = getattr(datetime, "UTC", datetime.timezone.utc)  # noqa: UP017


def _resolve_default_db_path() -> Path:
    env_path = os.getenv("TIN_DB_PATH")
    if env_path:
        return Path(env_path)
    root = Path(__file__).resolve().parents[2]
    data_dir = root / "data_store"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "tin_system.db"


class TinDatabase:
    """Thread-safe persistence layer for URA TIN Registration platform."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else _resolve_default_db_path()
        self._is_memory = str(self.db_path) == ":memory:"
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._is_memory:
            if not hasattr(self, "_memory_conn"):
                conn = sqlite3.connect(":memory:", check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys=ON")
                self._memory_conn = conn
            return self._memory_conn

        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=10.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS tin_taxpayers (
                        tin TEXT PRIMARY KEY,
                        legal_name TEXT NOT NULL,
                        category TEXT NOT NULL,
                        nin_or_passport TEXT NOT NULL,
                        ursb_reg_no TEXT,
                        mobile TEXT NOT NULL,
                        email TEXT NOT NULL,
                        registered_office TEXT NOT NULL,
                        district TEXT NOT NULL,
                        registration_date TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'ACTIVE',
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS tin_obligations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tin TEXT NOT NULL,
                        tax_head TEXT NOT NULL,
                        effective_from TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'ACTIVE',
                        filing_frequency TEXT NOT NULL DEFAULT 'MONTHLY',
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (tin) REFERENCES tin_taxpayers(tin)
                    );

                    CREATE TABLE IF NOT EXISTS tin_applications (
                        app_id TEXT PRIMARY KEY,
                        applicant_name TEXT NOT NULL,
                        category TEXT NOT NULL,
                        nin_or_reg_no TEXT NOT NULL,
                        assigned_tin TEXT,
                        status TEXT NOT NULL DEFAULT 'APPROVED',
                        created_at TEXT NOT NULL
                    );
                    """
                )
            self._seed_if_empty()

    def _seed_if_empty(self) -> None:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM tin_taxpayers")
        if cur.fetchone()[0] > 0:
            return

        now_iso = datetime.datetime.now(_UTC).isoformat()

        taxpayers = [
            (
                "1000000001",
                "Kakira Sugar Limited",
                "NON_INDIVIDUAL_COMPANY",
                "URSB-CO-10001",
                "URSB-CO-10001",
                "+256414111222",
                "info@kakirasugar.com",
                "Kakira Estate, Jinja",
                "Jinja",
                "1985-06-01",
                "ACTIVE",
                now_iso,
            ),
            (
                "1000000002",
                "Nile Breweries Limited",
                "NON_INDIVIDUAL_COMPANY",
                "URSB-CO-10002",
                "URSB-CO-10002",
                "+256414333444",
                "info@nilebreweries.com",
                "Plot 2, Nile Crescent",
                "Jinja",
                "1951-04-10",
                "ACTIVE",
                now_iso,
            ),
            (
                "1000000003",
                "Roofings Rolling Mills Ltd",
                "NON_INDIVIDUAL_COMPANY",
                "URSB-CO-10003",
                "URSB-CO-10003",
                "+256414555666",
                "info@roofings.co.ug",
                "Plot 126, Namanve Industrial Estate",
                "Mukono",
                "2009-11-01",
                "ACTIVE",
                now_iso,
            ),
            (
                "1000000004",
                "Mukwano Enterprises Limited",
                "NON_INDIVIDUAL_COMPANY",
                "URSB-CO-10004",
                "URSB-CO-10004",
                "+256414777888",
                "info@mukwano.com",
                "Plot 30, Mukwano Road, Kibuli",
                "Kampala",
                "1986-08-15",
                "ACTIVE",
                now_iso,
            ),
            (
                "1000000005",
                "Kampala City Supermarket Ltd",
                "NON_INDIVIDUAL_COMPANY",
                "URSB-CO-10005",
                "URSB-CO-10005",
                "+256701234567",
                "accounts@kampalasupermarket.co.ug",
                "Plot 14, Kampala Road",
                "Kampala",
                "2018-10-01",
                "ACTIVE",
                now_iso,
            ),
            (
                "1000000006",
                "Rwenzori Bottling Company Ltd",
                "NON_INDIVIDUAL_COMPANY",
                "URSB-CO-10006",
                "URSB-CO-10006",
                "+256414999000",
                "orders@rwenzori.co.ug",
                "Namanve Industrial Park",
                "Mukono",
                "2020-01-15",
                "ACTIVE",
                now_iso,
            ),
            (
                "1000000007",
                "Kampala Auto Traders Limited",
                "NON_INDIVIDUAL_COMPANY",
                "URSB-CO-10007",
                "URSB-CO-10007",
                "+256772111222",
                "customs@kla-autotraders.com",
                "Nakawa Industrial Area",
                "Kampala",
                "2019-05-18",
                "ACTIVE",
                now_iso,
            ),
            (
                "1000000008",
                "David Ochieng",
                "INDIVIDUAL",
                "CM910029384GUL",
                "URSB-BN-55102",
                "+256782333444",
                "dochieng@gmail.com",
                "Shop 5, Mukono Market",
                "Mukono",
                "2022-02-01",
                "ACTIVE",
                now_iso,
            ),
        ]
        with conn:
            conn.executemany(
                """
                INSERT INTO tin_taxpayers (
                    tin, legal_name, category, nin_or_passport, ursb_reg_no,
                    mobile, email, registered_office, district, registration_date,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                taxpayers,
            )

            # Seed tax obligations
            obligations = [
                ("1000000001", "CORPORATION_TAX", "1985-06-01", "ACTIVE", "ANNUAL", now_iso),
                ("1000000001", "PAYE", "1985-06-01", "ACTIVE", "MONTHLY", now_iso),
                ("1000000001", "VAT_STANDARD", "1996-07-01", "ACTIVE", "MONTHLY", now_iso),
                ("1000000001", "LOCAL_EXCISE", "2019-11-01", "ACTIVE", "MONTHLY", now_iso),
                ("1000000001", "WITHHOLDING_TAX", "2000-01-01", "ACTIVE", "MONTHLY", now_iso),
                ("1000000002", "CORPORATION_TAX", "1951-04-10", "ACTIVE", "ANNUAL", now_iso),
                ("1000000002", "PAYE", "1951-04-10", "ACTIVE", "MONTHLY", now_iso),
                ("1000000002", "VAT_STANDARD", "1996-07-01", "ACTIVE", "MONTHLY", now_iso),
                ("1000000002", "LOCAL_EXCISE", "2019-11-01", "ACTIVE", "MONTHLY", now_iso),
                ("1000000005", "CORPORATION_TAX", "2018-10-01", "ACTIVE", "ANNUAL", now_iso),
                ("1000000005", "PAYE", "2018-10-01", "ACTIVE", "MONTHLY", now_iso),
                ("1000000005", "VAT_STANDARD", "2018-10-01", "ACTIVE", "MONTHLY", now_iso),
                ("1000000008", "INCOME_TAX_INDIVIDUAL", "2022-02-01", "ACTIVE", "ANNUAL", now_iso),
            ]
            conn.executemany(
                """
                INSERT INTO tin_obligations (
                    tin, tax_head, effective_from, status, filing_frequency, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                obligations,
            )

    def search_taxpayer(self, query: str, *, exact: bool = False) -> TaxpayerRecord | None:
        q = query.strip()
        conn = self._get_connection()
        cur = conn.cursor()
        if exact:
            cur.execute(
                """
                SELECT * FROM tin_taxpayers
                WHERE tin = ? OR UPPER(nin_or_passport) = UPPER(?) OR UPPER(ursb_reg_no) = UPPER(?)
                LIMIT 1
                """,
                (q, q, q),
            )
        else:
            cur.execute(
                """
                SELECT * FROM tin_taxpayers
                WHERE tin = ? OR UPPER(nin_or_passport) = UPPER(?) OR UPPER(ursb_reg_no) = UPPER(?) OR LOWER(legal_name) LIKE LOWER(?)
                ORDER BY (tin = ? OR UPPER(nin_or_passport) = UPPER(?)) DESC
                LIMIT 1
                """,
                (q, q, q, f"%{q}%", q, q),
            )
        row = cur.fetchone()
        if not row:
            return None

        # Fetch obligations
        cur.execute("SELECT * FROM tin_obligations WHERE tin = ? AND status = 'ACTIVE'", (row["tin"],))
        obligations = [
            TaxObligation(
                tax_head=TaxHeadType(ob["tax_head"]),
                effective_from=ob["effective_from"],
                status=ob["status"],
                filing_frequency=ob["filing_frequency"],
            )
            for ob in cur.fetchall()
        ]

        return TaxpayerRecord(
            tin=row["tin"],
            legal_name=row["legal_name"],
            category=TaxpayerCategory(row["category"]),
            nin_or_passport=row["nin_or_passport"],
            ursb_reg_no=row["ursb_reg_no"],
            mobile=row["mobile"],
            email=row["email"],
            registered_office=row["registered_office"],
            district=row["district"],
            registration_date=row["registration_date"],
            status=row["status"],
            obligations=obligations,
        )

    def insert_taxpayer(self, taxpayer: TaxpayerRecord) -> None:
        conn = self._get_connection()
        now_iso = datetime.datetime.now(_UTC).isoformat()
        with conn:
            conn.execute(
                """
                INSERT INTO tin_taxpayers (
                    tin, legal_name, category, nin_or_passport, ursb_reg_no,
                    mobile, email, registered_office, district, registration_date,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    taxpayer.tin,
                    taxpayer.legal_name,
                    taxpayer.category.value,
                    taxpayer.nin_or_passport,
                    taxpayer.ursb_reg_no,
                    taxpayer.mobile,
                    taxpayer.email,
                    taxpayer.registered_office,
                    taxpayer.district,
                    taxpayer.registration_date,
                    taxpayer.status,
                    now_iso,
                ),
            )
            for ob in taxpayer.obligations:
                conn.execute(
                    """
                    INSERT INTO tin_obligations (tin, tax_head, effective_from, status, filing_frequency, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (taxpayer.tin, ob.tax_head.value, ob.effective_from, ob.status, ob.filing_frequency, now_iso),
                )

    def add_obligation(self, tin: str, tax_head: TaxHeadType, frequency: str = "MONTHLY") -> None:
        conn = self._get_connection()
        now_iso = datetime.datetime.now(_UTC).isoformat()
        date_str = now_iso[:10]
        with conn:
            conn.execute(
                """
                INSERT INTO tin_obligations (tin, tax_head, effective_from, status, filing_frequency, created_at)
                VALUES (?, ?, ?, 'ACTIVE', ?, ?)
                """,
                (tin.strip(), tax_head.value, date_str, frequency, now_iso),
            )

    def get_stats(self) -> dict[str, Any]:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), SUM(CASE WHEN category = 'INDIVIDUAL' THEN 1 ELSE 0 END), SUM(CASE WHEN category = 'NON_INDIVIDUAL_COMPANY' THEN 1 ELSE 0 END) FROM tin_taxpayers")
        row = cur.fetchone()
        total_taxpayers = row[0]
        individuals = row[1] or 0
        companies = row[2] or 0

        cur.execute("SELECT COUNT(*) FROM tin_obligations WHERE status = 'ACTIVE'")
        active_obligations = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM tin_obligations WHERE tax_head = 'VAT_STANDARD' AND status = 'ACTIVE'")
        vat_registered = cur.fetchone()[0]

        return {
            "database": str(self.db_path.name if isinstance(self.db_path, Path) else self.db_path),
            "total_registered_taxpayers": total_taxpayers,
            "individual_tins": individuals,
            "company_tins": companies,
            "active_tax_obligations": active_obligations,
            "vat_registered_taxpayers": vat_registered,
            "status": "ONLINE",
        }

    def list_recent_taxpayers(self, limit: int = 5) -> list[dict[str, Any]]:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT tin, legal_name, category, registration_date, status, district
            FROM tin_taxpayers ORDER BY rowid DESC LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]

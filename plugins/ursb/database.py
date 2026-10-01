"""Independent SQLite database for the Uganda Registration Services Bureau (URSB) sample system.

Maintains dedicated persistence for incorporated companies, registered business names,
directors (Form 20), and annual return filings under data_store/ursb_system.db.
"""

from __future__ import annotations

import datetime
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .models import BusinessEntity, DirectorInfo, EntityStatus, EntityType

logger = logging.getLogger(__name__)

_UTC = getattr(datetime, "UTC", datetime.timezone.utc)  # noqa: UP017


def _resolve_default_db_path() -> Path:
    env_path = os.getenv("URSB_DB_PATH")
    if env_path:
        return Path(env_path)
    root = Path(__file__).resolve().parents[2]
    data_dir = root / "data_store"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "ursb_system.db"


class UrsbDatabase:
    """Thread-safe persistence layer for URSB platform."""

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
                    CREATE TABLE IF NOT EXISTS ursb_entities (
                        registration_number TEXT PRIMARY KEY,
                        business_name TEXT NOT NULL,
                        entity_type TEXT NOT NULL,
                        registration_date TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'ACTIVE',
                        registered_office TEXT NOT NULL,
                        district TEXT NOT NULL,
                        nature_of_business TEXT NOT NULL,
                        latest_annual_returns_year INTEGER NOT NULL DEFAULT 2025,
                        form_20_registered INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS ursb_directors (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        registration_number TEXT NOT NULL,
                        full_name TEXT NOT NULL,
                        nin_or_passport TEXT NOT NULL,
                        nationality TEXT NOT NULL DEFAULT 'Ugandan',
                        role TEXT NOT NULL DEFAULT 'DIRECTOR',
                        tin TEXT,
                        shares_percentage REAL NOT NULL DEFAULT 50.0,
                        FOREIGN KEY (registration_number) REFERENCES ursb_entities(registration_number)
                    );

                    CREATE TABLE IF NOT EXISTS ursb_applications (
                        application_id TEXT PRIMARY KEY,
                        business_name TEXT NOT NULL,
                        entity_type TEXT NOT NULL,
                        applicant_nin TEXT NOT NULL,
                        fee_ugx REAL NOT NULL,
                        status TEXT NOT NULL DEFAULT 'APPROVED',
                        created_at TEXT NOT NULL
                    );
                    """
                )
            self._seed_if_empty()

    def _seed_if_empty(self) -> None:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM ursb_entities")
        if cur.fetchone()[0] > 0:
            return

        now_iso = datetime.datetime.now(_UTC).isoformat()

        entities = [
            (
                "URSB-CO-10001",
                "Kakira Sugar Limited",
                "LIMITED_COMPANY",
                "1985-05-12",
                "ACTIVE",
                "Kakira Estate, Jinja Road",
                "Jinja",
                "Agro-processing, Sugar Manufacturing & Power Cogeneration",
                2025,
                1,
                now_iso,
            ),
            (
                "URSB-CO-10002",
                "Nile Breweries Limited",
                "LIMITED_COMPANY",
                "1951-03-24",
                "ACTIVE",
                "Plot 2, Nile Crescent",
                "Jinja",
                "Brewing and Manufacturing of Alcoholic & Malt Beverages",
                2025,
                1,
                now_iso,
            ),
            (
                "URSB-CO-10003",
                "Roofings Rolling Mills Ltd",
                "LIMITED_COMPANY",
                "2009-10-14",
                "ACTIVE",
                "Plot 126, Namanve Industrial Estate",
                "Mukono",
                "Steel Fabrication & Construction Building Materials",
                2025,
                1,
                now_iso,
            ),
            (
                "URSB-CO-10004",
                "Mukwano Enterprises Limited",
                "LIMITED_COMPANY",
                "1986-07-02",
                "ACTIVE",
                "Plot 30, Mukwano Road, Kibuli",
                "Kampala",
                "Manufacturing of Edible Oils, Soaps, Detergents & FMCG",
                2025,
                1,
                now_iso,
            ),
            (
                "URSB-CO-10005",
                "Kampala City Supermarket Ltd",
                "LIMITED_COMPANY",
                "2018-09-11",
                "ACTIVE",
                "Plot 14, Kampala Road",
                "Kampala",
                "Wholesale and Retail Supermarket Trade",
                2025,
                1,
                now_iso,
            ),
            (
                "URSB-BN-55102",
                "Pearl Organic Coffee Traders",
                "BUSINESS_NAME",
                "2022-01-20",
                "ACTIVE",
                "Shop 5, Mukono Central Market",
                "Mukono",
                "Coffee Sourcing, Value Addition, and Grain Trading",
                2025,
                1,
                now_iso,
            ),
            (
                "URSB-CO-99812",
                "Defunct Logistics Uganda Ltd",
                "LIMITED_COMPANY",
                "2012-06-15",
                "PENDING_ANNUAL_RETURNS",
                "Plot 99, 6th Street Industrial Area",
                "Kampala",
                "Freight Logistics and Cargo Handling",
                2020,
                0,
                now_iso,
            ),
        ]
        with conn:
            conn.executemany(
                """
                INSERT INTO ursb_entities (
                    registration_number, business_name, entity_type, registration_date,
                    status, registered_office, district, nature_of_business,
                    latest_annual_returns_year, form_20_registered, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                entities,
            )

            # Seed directors
            directors = [
                ("URSB-CO-10001", "Mayur Madhvani", "CM850019284HJA", "Ugandan", "MANAGING_DIRECTOR", "1000000001", 60.0),
                ("URSB-CO-10001", "Kamlesh Madhvani", "CM860029381HJB", "Ugandan", "DIRECTOR", "1000000001", 40.0),
                ("URSB-CO-10002", "Adu Rando", "PP991028471FRA", "French", "MANAGING_DIRECTOR", "1000000002", 0.0),
                ("URSB-CO-10004", "Alykhan Karmali", "CM680019284KMP", "Ugandan", "MANAGING_DIRECTOR", "1000000004", 75.0),
                ("URSB-CO-10005", "Grace Nakabugo", "CF890019284KLA", "Ugandan", "MANAGING_DIRECTOR", "1000000005", 100.0),
                ("URSB-BN-55102", "David Ochieng", "CM910029384GUL", "Ugandan", "PROPRIETOR", "1000000008", 100.0),
            ]
            conn.executemany(
                """
                INSERT INTO ursb_directors (
                    registration_number, full_name, nin_or_passport, nationality, role, tin, shares_percentage
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                directors,
            )

    def get_entity(self, registration_number: str) -> BusinessEntity | None:
        """Retrieve registered entity by registration number."""
        return self.search_entity(registration_number)

    def search_entity(self, query: str) -> BusinessEntity | None:
        q = query.strip()
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM ursb_entities
            WHERE registration_number = ? OR LOWER(business_name) = LOWER(?) OR LOWER(business_name) LIKE LOWER(?)
            LIMIT 1
            """,
            (q, q, f"%{q}%"),
        )
        row = cur.fetchone()
        if not row:
            return None

        # Fetch directors
        cur.execute("SELECT * FROM ursb_directors WHERE registration_number = ?", (row["registration_number"],))
        directors = [
            DirectorInfo(
                full_name=d["full_name"],
                nin_or_passport=d["nin_or_passport"],
                nationality=d["nationality"],
                role=d["role"],
                tin=d["tin"],
                shares_percentage=d["shares_percentage"],
            )
            for d in cur.fetchall()
        ]

        return BusinessEntity(
            registration_number=row["registration_number"],
            business_name=row["business_name"],
            entity_type=EntityType(row["entity_type"]),
            registration_date=row["registration_date"],
            status=EntityStatus(row["status"]),
            registered_office=row["registered_office"],
            district=row["district"],
            nature_of_business=row["nature_of_business"],
            directors=directors,
            latest_annual_returns_year=row["latest_annual_returns_year"],
            form_20_registered=bool(row["form_20_registered"]),
        )

    def insert_entity(self, entity: BusinessEntity) -> None:
        conn = self._get_connection()
        now_iso = datetime.datetime.now(_UTC).isoformat()
        with conn:
            conn.execute(
                """
                INSERT INTO ursb_entities (
                    registration_number, business_name, entity_type, registration_date,
                    status, registered_office, district, nature_of_business,
                    latest_annual_returns_year, form_20_registered, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entity.registration_number,
                    entity.business_name,
                    entity.entity_type.value,
                    entity.registration_date,
                    entity.status.value,
                    entity.registered_office,
                    entity.district,
                    entity.nature_of_business,
                    entity.latest_annual_returns_year,
                    1 if entity.form_20_registered else 0,
                    now_iso,
                ),
            )
            for d in entity.directors:
                conn.execute(
                    """
                    INSERT INTO ursb_directors (
                        registration_number, full_name, nin_or_passport, nationality, role, tin, shares_percentage
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entity.registration_number,
                        d.full_name,
                        d.nin_or_passport,
                        d.nationality,
                        d.role,
                        d.tin,
                        d.shares_percentage,
                    ),
                )

    def get_stats(self) -> dict[str, Any]:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END) FROM ursb_entities")
        ent_row = cur.fetchone()
        total_entities = ent_row[0]
        active_entities = ent_row[1] or 0

        cur.execute("SELECT COUNT(*) FROM ursb_directors")
        directors_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM ursb_applications")
        app_count = cur.fetchone()[0]

        return {
            "database": str(self.db_path.name if isinstance(self.db_path, Path) else self.db_path),
            "total_registered_entities": total_entities,
            "active_entities": active_entities,
            "directors_count": directors_count,
            "registered_applications": app_count,
            "status": "ONLINE",
        }

    def list_recent_entities(self, limit: int = 5) -> list[dict[str, Any]]:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT registration_number, business_name, entity_type, registration_date, status, district
            FROM ursb_entities ORDER BY rowid DESC LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]

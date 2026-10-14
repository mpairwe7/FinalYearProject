"""Independent SQLite database for the URA Digital Tax Stamps (DTS / Kakasa platform) sample system.

Maintains dedicated, isolated persistence for gazetted manufacturers, packaging lines,
digital tax stamps, orders, fee tariffs, and damaged stamp declarations under data_store/dts_system.db.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .models import GazettedCategory, StampRecord, StampStatus, TaxpayerDtsProfile

logger = logging.getLogger(__name__)

_UTC = getattr(datetime, "UTC", datetime.timezone.utc)  # noqa: UP017


def _resolve_default_db_path() -> Path:
    env_path = os.getenv("DTS_DB_PATH")
    if env_path:
        return Path(env_path)
    root = Path(__file__).resolve().parents[2]
    data_dir = root / "data_store"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "dts_system.db"


class DtsDatabase:
    """Thread-safe persistence layer for Digital Tax Stamps platform."""

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
                    CREATE TABLE IF NOT EXISTS dts_manufacturers (
                        tin TEXT PRIMARY KEY,
                        manufacturer_name TEXT NOT NULL,
                        taxpayer_type TEXT NOT NULL,
                        gazetted_categories TEXT NOT NULL,
                        dts_registration_date TEXT NOT NULL,
                        active_stamps_inventory INTEGER NOT NULL DEFAULT 0,
                        is_compliant INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS dts_packaging_lines (
                        line_id TEXT PRIMARY KEY,
                        tin TEXT NOT NULL,
                        equipment_type TEXT NOT NULL,
                        speed_bpm INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'ACTIVE',
                        location TEXT NOT NULL,
                        FOREIGN KEY (tin) REFERENCES dts_manufacturers(tin)
                    );

                    CREATE TABLE IF NOT EXISTS dts_stamps (
                        stamp_code TEXT PRIMARY KEY,
                        product_category TEXT NOT NULL,
                        brand_name TEXT NOT NULL,
                        manufacturer_tin TEXT NOT NULL,
                        manufacturer_name TEXT NOT NULL,
                        batch_number TEXT NOT NULL,
                        production_date TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'GENUINE',
                        expiry_date TEXT,
                        line_id TEXT,
                        activated_at TEXT,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (manufacturer_tin) REFERENCES dts_manufacturers(tin)
                    );

                    CREATE TABLE IF NOT EXISTS dts_orders (
                        order_id TEXT PRIMARY KEY,
                        taxpayer_tin TEXT NOT NULL,
                        manufacturer_name TEXT NOT NULL,
                        product_category TEXT NOT NULL,
                        quantity INTEGER NOT NULL,
                        unit_fee_ugx REAL NOT NULL,
                        total_amount_ugx REAL NOT NULL,
                        prn TEXT NOT NULL UNIQUE,
                        payment_status TEXT NOT NULL DEFAULT 'PENDING',
                        collection_point TEXT NOT NULL,
                        order_status TEXT NOT NULL DEFAULT 'AWAITING_PAYMENT',
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (taxpayer_tin) REFERENCES dts_manufacturers(tin)
                    );

                    CREATE TABLE IF NOT EXISTS dts_damaged_declarations (
                        declaration_id TEXT PRIMARY KEY,
                        taxpayer_tin TEXT NOT NULL,
                        damaged_count INTEGER NOT NULL,
                        serials_json TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        line_id TEXT NOT NULL,
                        credit_allowable_ugx REAL NOT NULL,
                        status TEXT NOT NULL DEFAULT 'ACKNOWLEDGED',
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (taxpayer_tin) REFERENCES dts_manufacturers(tin)
                    );
                    """
                )
            self._seed_if_empty()

    def _seed_if_empty(self) -> None:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM dts_manufacturers")
        if cur.fetchone()[0] > 0:
            return

        now_iso = datetime.datetime.now(_UTC).isoformat()

        # Seed manufacturers
        manufacturers = [
            (
                "1000000002",
                "Nile Breweries Limited",
                "LOCAL_MANUFACTURER",
                json.dumps(["BEER", "SPIRITS"]),
                "2019-11-01",
                450000,
                1,
                now_iso,
            ),
            (
                "1000000001",
                "Kakira Sugar Limited",
                "LOCAL_MANUFACTURER",
                json.dumps(["SUGAR", "SPIRITS"]),
                "2020-02-15",
                250000,
                1,
                now_iso,
            ),
            (
                "1000000006",
                "Rwenzori Bottling Company Ltd",
                "LOCAL_MANUFACTURER",
                json.dumps(["BOTTLED_WATER", "JUICES"]),
                "2020-01-10",
                800000,
                1,
                now_iso,
            ),
            (
                "1000000003",
                "Tororo Cement Limited",
                "LOCAL_MANUFACTURER",
                json.dumps(["CEMENT"]),
                "2021-04-01",
                150000,
                1,
                now_iso,
            ),
        ]
        with conn:
            conn.executemany(
                """
                INSERT INTO dts_manufacturers (
                    tin, manufacturer_name, taxpayer_type, gazetted_categories,
                    dts_registration_date, active_stamps_inventory, is_compliant, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                manufacturers,
            )

            # Seed packaging lines
            lines = [
                ("LINE-01-BOTTLING", "1000000002", "AUTOMATED_LINE_APPLICATOR", 600, "ACTIVE", "Jinja Main Plant"),
                ("LINE-02-CANNING", "1000000002", "AUTOMATED_LINE_APPLICATOR", 450, "ACTIVE", "Mbarara Plant"),
                ("LINE-SUGAR-50KG", "1000000001", "BAG_SEWING_STAMPER", 120, "ACTIVE", "Kakira Factory"),
                ("LINE-WATER-500ML", "1000000006", "AUTOMATED_LINE_APPLICATOR", 800, "ACTIVE", "Namanve Industrial"),
                ("PACKER-01-ROTARY", "1000000003", "BAG_DIRECT_MARKING", 300, "ACTIVE", "Tororo Plant"),
            ]
            conn.executemany(
                """
                INSERT INTO dts_packaging_lines (
                    line_id, tin, equipment_type, speed_bpm, status, location
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                lines,
            )

            # Seed genuine test stamps
            stamps = [
                (
                    "DTS-UG-BEV-990182746",
                    "BEER",
                    "Nile Special Lager 500ml",
                    "1000000002",
                    "Nile Breweries Limited",
                    "BATCH-NBL-2026-03",
                    "2026-03-01",
                    "GENUINE",
                    "2027-03-01",
                    "LINE-01-BOTTLING",
                    now_iso,
                    now_iso,
                ),
                (
                    "DTS-UG-WTR-554433221",
                    "BOTTLED_WATER",
                    "Rwenzori Pure Natural Mineral Water 500ml",
                    "1000000006",
                    "Rwenzori Bottling Company Ltd",
                    "BATCH-RWZ-2026-02",
                    "2026-02-15",
                    "GENUINE",
                    "2027-02-15",
                    "LINE-WATER-500ML",
                    now_iso,
                    now_iso,
                ),
                (
                    "DTS-UG-SGR-112233445",
                    "SUGAR",
                    "Kakira White Refined Sugar 50kg Bag",
                    "1000000001",
                    "Kakira Sugar Limited",
                    "BATCH-KS-2026-A1",
                    "2026-02-28",
                    "GENUINE",
                    "2028-02-28",
                    "LINE-SUGAR-50KG",
                    now_iso,
                    now_iso,
                ),
                (
                    "DTS-UG-BEV-EXPIRED-001",
                    "BEER",
                    "Club Pilsener 330ml",
                    "1000000002",
                    "Nile Breweries Limited",
                    "BATCH-NBL-2023-OLD",
                    "2023-01-01",
                    "EXPIRED",
                    "2024-01-01",
                    "LINE-01-BOTTLING",
                    None,
                    now_iso,
                ),
            ]
            conn.executemany(
                """
                INSERT INTO dts_stamps (
                    stamp_code, product_category, brand_name, manufacturer_tin,
                    manufacturer_name, batch_number, production_date, status,
                    expiry_date, line_id, activated_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                stamps,
            )

    # ── Database Operations ─────────────────────────────────────────

    def get_manufacturer(self, tin: str) -> TaxpayerDtsProfile | None:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM dts_manufacturers WHERE tin = ?", (tin.strip(),))
        row = cur.fetchone()
        if not row:
            return None

        # Fetch lines
        cur.execute("SELECT line_id, equipment_type, speed_bpm, status, location FROM dts_packaging_lines WHERE tin = ?", (tin.strip(),))
        lines = [dict(r) for r in cur.fetchall()]

        return TaxpayerDtsProfile(
            tin=row["tin"],
            manufacturer_name=row["manufacturer_name"],
            taxpayer_type=row["taxpayer_type"],
            gazetted_categories=json.loads(row["gazetted_categories"]) if row["gazetted_categories"] else [],
            packaging_lines=lines,
            dts_registration_date=row["dts_registration_date"],
            active_stamps_inventory=row["active_stamps_inventory"],
            is_compliant=bool(row["is_compliant"]),
        )

    def get_packaging_line(self, line_id: str) -> dict[str, Any] | None:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT line_id, tin, equipment_type, speed_bpm, status, location FROM dts_packaging_lines WHERE line_id = ?", (line_id.strip(),))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "line_id": row["line_id"],
            "manufacturer_tin": row["tin"],
            "tin": row["tin"],
            "equipment_type": row["equipment_type"],
            "speed_bpm": row["speed_bpm"],
            "status": row["status"],
            "location": row["location"],
        }

    def get_stamp(self, stamp_code: str) -> StampRecord | None:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM dts_stamps WHERE stamp_code = ?", (stamp_code.strip(),))
        row = cur.fetchone()
        if not row:
            return None

        cat = GazettedCategory(row["product_category"]) if row["product_category"] in GazettedCategory._value2member_map_ else GazettedCategory.BEER
        status = StampStatus(row["status"]) if row["status"] in StampStatus._value2member_map_ else StampStatus.UNKNOWN

        return StampRecord(
            stamp_code=row["stamp_code"],
            product_category=cat,
            brand_name=row["brand_name"],
            manufacturer_tin=row["manufacturer_tin"],
            manufacturer_name=row["manufacturer_name"],
            batch_number=row["batch_number"],
            production_date=row["production_date"],
            status=status,
            expiry_date=row["expiry_date"],
            line_id=row["line_id"],
        )

    def insert_order(self, order_dict: dict[str, Any]) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO dts_orders (
                    order_id, taxpayer_tin, manufacturer_name, product_category,
                    quantity, unit_fee_ugx, total_amount_ugx, prn, payment_status,
                    collection_point, order_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_dict["order_id"],
                    order_dict["taxpayer_tin"],
                    order_dict["manufacturer_name"],
                    order_dict["product_category"],
                    order_dict["quantity"],
                    order_dict["unit_fee_ugx"],
                    order_dict["total_amount_ugx"],
                    order_dict["prn"],
                    order_dict.get("payment_status", "PENDING"),
                    order_dict["collection_point"],
                    order_dict.get("order_status", "AWAITING_PAYMENT"),
                    order_dict["created_at"],
                ),
            )

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM dts_orders WHERE order_id = ?", (order_id.strip(),))
        row = cur.fetchone()
        return dict(row) if row else None

    def activate_batch(
        self,
        order_id: str,
        line_id: str,
        stamp_serials: list[str],
        batch_id: str,
        cat: GazettedCategory,
        manufacturer_name: str,
        taxpayer_tin: str,
    ) -> None:
        conn = self._get_connection()
        now_iso = datetime.datetime.now(_UTC).isoformat()
        date_str = now_iso[:10]

        records = [
            (
                serial,
                cat.value,
                f"{manufacturer_name} Gazetted Unit",
                taxpayer_tin,
                manufacturer_name,
                batch_id,
                date_str,
                StampStatus.GENUINE.value,
                None,
                line_id,
                now_iso,
                now_iso,
            )
            for serial in stamp_serials
        ]
        with conn:
            cur = conn.cursor()
            for serial in stamp_serials:
                cur.execute("SELECT 1 FROM dts_stamps WHERE stamp_code = ?", (serial.strip(),))
                if cur.fetchone():
                    raise ValueError(f"Stamp serial '{serial}' already exists in registry.")
            conn.executemany(
                """
                INSERT INTO dts_stamps (
                    stamp_code, product_category, brand_name, manufacturer_tin,
                    manufacturer_name, batch_number, production_date, status,
                    expiry_date, line_id, activated_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )
            conn.execute(
                "UPDATE dts_orders SET order_status = 'ACTIVATED' WHERE order_id = ?",
                (order_id.strip(),),
            )

    def insert_damaged_declaration(self, decl: dict[str, Any], serials: list[str]) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO dts_damaged_declarations (
                    declaration_id, taxpayer_tin, damaged_count, serials_json,
                    reason, line_id, credit_allowable_ugx, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decl["declaration_id"],
                    decl["tin"],
                    decl["damaged_count"],
                    json.dumps(serials),
                    decl["reason"],
                    decl["line_id"],
                    decl["credit_allowable_ugx"],
                    decl.get("status", "ACKNOWLEDGED"),
                    decl["timestamp"],
                ),
            )
            for s in serials:
                conn.execute(
                    "UPDATE dts_stamps SET status = ? WHERE stamp_code = ?",
                    (StampStatus.SPOILED.value, s.strip()),
                )

    def get_stats(self) -> dict[str, Any]:
        """Aggregate statistical metrics for DTS database."""
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM dts_manufacturers")
        manufacturers_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM dts_packaging_lines")
        lines_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*), SUM(CASE WHEN status = 'GENUINE' THEN 1 ELSE 0 END) FROM dts_stamps")
        stamps_row = cur.fetchone()
        stamps_count = stamps_row[0]
        genuine_count = stamps_row[1] or 0

        cur.execute("SELECT COUNT(*), COALESCE(SUM(total_amount_ugx), 0) FROM dts_orders")
        orders_row = cur.fetchone()
        orders_count = orders_row[0]
        revenue_prn = round(orders_row[1], 2)

        cur.execute("SELECT COUNT(*), COALESCE(SUM(damaged_count), 0) FROM dts_damaged_declarations")
        decl_row = cur.fetchone()
        decl_count = decl_row[0]
        reconciled_stamps = decl_row[1]

        return {
            "database": str(self.db_path.name if isinstance(self.db_path, Path) else self.db_path),
            "manufacturers_count": manufacturers_count,
            "packaging_lines_count": lines_count,
            "total_stamps_count": stamps_count,
            "genuine_stamps_count": genuine_count,
            "orders_count": orders_count,
            "total_prn_fee_revenue_ugx": revenue_prn,
            "damaged_declarations_count": decl_count,
            "reconciled_spoiled_stamps": reconciled_stamps,
            "status": "ONLINE",
        }

    def list_recent_stamps(self, limit: int = 5) -> list[dict[str, Any]]:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT stamp_code, product_category, brand_name, manufacturer_name, status, production_date
            FROM dts_stamps ORDER BY rowid DESC LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]

    def list_recent_orders(self, limit: int = 5) -> list[dict[str, Any]]:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT order_id, manufacturer_name, product_category, quantity, total_amount_ugx, prn, order_status, created_at
            FROM dts_orders ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]

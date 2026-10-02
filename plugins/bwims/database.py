"""Independent SQLite database for the URA Bonded Warehouse Information Management System (BWIMS).

Maintains dedicated persistence for customs bonded warehouses, IM7 cargo entries,
bonded stock inventory, and ex-warehouse clearances under data_store/bwims_system.db.
"""

from __future__ import annotations

import datetime
import logging
import os
import secrets
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .models import BondedConsignment, ConsignmentStatus

logger = logging.getLogger(__name__)

_UTC = getattr(datetime, "UTC", datetime.timezone.utc)  # noqa: UP017


def _resolve_default_db_path() -> Path:
    env_path = os.getenv("BWIMS_DB_PATH")
    if env_path:
        return Path(env_path)
    root = Path(__file__).resolve().parents[2]
    data_dir = root / "data_store"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "bwims_system.db"


class BwimsDatabase:
    """Thread-safe persistence layer for BWIMS platform."""

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
                    CREATE TABLE IF NOT EXISTS bwims_warehouses (
                        warehouse_code TEXT PRIMARY KEY,
                        operator_name TEXT NOT NULL,
                        warehouse_type TEXT NOT NULL,
                        location TEXT NOT NULL,
                        bond_security_amount_ugx REAL NOT NULL,
                        status TEXT NOT NULL DEFAULT 'ACTIVE',
                        licensed_expiry TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS bwims_consignments (
                        entry_number TEXT PRIMARY KEY,
                        importer_tin TEXT NOT NULL,
                        importer_name TEXT NOT NULL,
                        warehouse_code TEXT NOT NULL,
                        goods_description TEXT NOT NULL,
                        quantity REAL NOT NULL,
                        unit_of_measure TEXT NOT NULL,
                        cif_value_ugx REAL NOT NULL,
                        warehousing_date TEXT NOT NULL,
                        statutory_expiry_date TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'BONDED_IN_STORAGE',
                        hs_code TEXT NOT NULL,
                        cleared_quantity REAL NOT NULL DEFAULT 0.0,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (warehouse_code) REFERENCES bwims_warehouses(warehouse_code)
                    );

                    CREATE TABLE IF NOT EXISTS bwims_clearances (
                        clearance_id TEXT PRIMARY KEY,
                        entry_number TEXT NOT NULL,
                        declaration_type TEXT NOT NULL,
                        cleared_quantity REAL NOT NULL,
                        duty_paid_prn TEXT NOT NULL,
                        cleared_at TEXT NOT NULL,
                        FOREIGN KEY (entry_number) REFERENCES bwims_consignments(entry_number)
                    );
                    """
                )
            self._seed_if_empty()

    def _seed_if_empty(self) -> None:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM bwims_warehouses")
        if cur.fetchone()[0] > 0:
            return

        now_iso = datetime.datetime.now(_UTC).isoformat()

        warehouses = [
            (
                "WH-KLA-001",
                "Uganda Inland Container Depot (ICD) Nakawa",
                "PUBLIC_BONDED_WAREHOUSE",
                "Nakawa Industrial Area, Kampala",
                15000000000.0,
                "ACTIVE",
                "2026-12-31",
                now_iso,
            ),
            (
                "WH-JJA-002",
                "Nile Grain & Agro Transit Silos",
                "PRIVATE_BONDED_WAREHOUSE",
                "Plot 44, Factory Road, Jinja",
                5000000000.0,
                "ACTIVE",
                "2026-12-31",
                now_iso,
            ),
            (
                "WH-EBB-003",
                "Entebbe Aviation Cargo Transit Shed",
                "INLAND_CONTAINER_DEPOT",
                "Cargo Terminal, Entebbe International Airport",
                10000000000.0,
                "ACTIVE",
                "2026-12-31",
                now_iso,
            ),
            (
                "WH-NMS-004",
                "Namanve Logistics Central Bond",
                "PUBLIC_BONDED_WAREHOUSE",
                "Namanve Industrial Park, Mukono",
                8000000000.0,
                "ACTIVE",
                "2026-12-31",
                now_iso,
            ),
        ]
        with conn:
            conn.executemany(
                """
                INSERT INTO bwims_warehouses (
                    warehouse_code, operator_name, warehouse_type, location,
                    bond_security_amount_ugx, status, licensed_expiry, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                warehouses,
            )

            # Seed consignments
            consignments = [
                (
                    "2026-ASY-IM7-88912",
                    "1000000007",
                    "Kampala Auto Traders Limited",
                    "WH-KLA-001",
                    "100 Motor Vehicles (Toyota Hilux Double Cab 4WD)",
                    100.0,
                    "UNITS",
                    4500000000.0,
                    "2026-01-10",
                    "2026-10-10",
                    "BONDED_IN_STORAGE",
                    "8703.23.90",
                    0.0,
                    now_iso,
                ),
                (
                    "2026-ASY-IM7-44102",
                    "1000000001",
                    "Kakira Sugar Limited",
                    "WH-JJA-002",
                    "500 Metric Tonnes Industrial Raw Refining Sugar in Bulk",
                    500.0,
                    "TONNES",
                    1200000000.0,
                    "2026-02-01",
                    "2026-11-01",
                    "BONDED_IN_STORAGE",
                    "1701.14.00",
                    0.0,
                    now_iso,
                ),
                (
                    "2026-ASY-IM7-11928",
                    "1000000009",
                    "Uganda Healthcare Imports Ltd",
                    "WH-EBB-003",
                    "50 Pallets Temperature-Controlled Essential Pharmaceuticals",
                    50.0,
                    "PALLETS",
                    850000000.0,
                    "2026-02-20",
                    "2026-11-20",
                    "BONDED_IN_STORAGE",
                    "3004.90.00",
                    0.0,
                    now_iso,
                ),
                (
                    "2025-ASY-IM7-00912",
                    "1000000010",
                    "Old Line Importers Enterprises",
                    "WH-KLA-001",
                    "200 Bales Commercial Secondhand Used Clothing",
                    200.0,
                    "BALES",
                    120000000.0,
                    "2025-03-01",
                    "2025-12-01",
                    "OVERSTAYED_AUCTION_RISK",
                    "6309.00.00",
                    0.0,
                    now_iso,
                ),
            ]
            conn.executemany(
                """
                INSERT INTO bwims_consignments (
                    entry_number, importer_tin, importer_name, warehouse_code,
                    goods_description, quantity, unit_of_measure, cif_value_ugx,
                    warehousing_date, statutory_expiry_date, status, hs_code,
                    cleared_quantity, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                consignments,
            )

    def get_consignment(self, entry_number: str) -> BondedConsignment | None:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM bwims_consignments WHERE entry_number = ?", (entry_number.strip(),))
        row = cur.fetchone()
        if not row:
            return None
        st = ConsignmentStatus(row["status"]) if row["status"] in ConsignmentStatus._value2member_map_ else ConsignmentStatus.BONDED_IN_STORAGE
        return BondedConsignment(
            entry_number=row["entry_number"],
            importer_tin=row["importer_tin"],
            importer_name=row["importer_name"],
            warehouse_code=row["warehouse_code"],
            goods_description=row["goods_description"],
            quantity=row["quantity"],
            unit_of_measure=row["unit_of_measure"],
            cif_value_ugx=row["cif_value_ugx"],
            warehousing_date=row["warehousing_date"],
            statutory_expiry_date=row["statutory_expiry_date"],
            status=st,
            hs_code=row["hs_code"],
            cleared_quantity=row["cleared_quantity"],
        )

    def list_warehouse_consignments(
        self, warehouse_code: str, importer_tin: str | None = None
    ) -> list[BondedConsignment]:
        conn = self._get_connection()
        cur = conn.cursor()
        if importer_tin:
            cur.execute(
                "SELECT * FROM bwims_consignments WHERE warehouse_code = ? AND importer_tin = ?",
                (warehouse_code.strip(), importer_tin.strip()),
            )
        else:
            cur.execute(
                "SELECT * FROM bwims_consignments WHERE warehouse_code = ?",
                (warehouse_code.strip(),),
            )
        rows = cur.fetchall()
        result = []
        for row in rows:
            st = ConsignmentStatus(row["status"]) if row["status"] in ConsignmentStatus._value2member_map_ else ConsignmentStatus.BONDED_IN_STORAGE
            result.append(
                BondedConsignment(
                    entry_number=row["entry_number"],
                    importer_tin=row["importer_tin"],
                    importer_name=row["importer_name"],
                    warehouse_code=row["warehouse_code"],
                    goods_description=row["goods_description"],
                    quantity=row["quantity"],
                    unit_of_measure=row["unit_of_measure"],
                    cif_value_ugx=row["cif_value_ugx"],
                    warehousing_date=row["warehousing_date"],
                    statutory_expiry_date=row["statutory_expiry_date"],
                    status=st,
                    hs_code=row["hs_code"],
                    cleared_quantity=row["cleared_quantity"],
                )
            )
        return result

    def record_clearance(
        self,
        entry_number: str,
        cleared_quantity: float,
        declaration_type: str,
        duty_paid_prn: str,
    ) -> dict[str, Any]:
        conn = self._get_connection()
        now_iso = datetime.datetime.now(_UTC).isoformat()
        clearance_id = f"BWIMS-CLR-{now_iso[:10].replace('-', '')}-{1000 + secrets.randbelow(9000)}"
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT quantity, cleared_quantity FROM bwims_consignments WHERE entry_number = ?", (entry_number.strip(),))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Entry {entry_number} not found")

            total_qty = row["quantity"]
            already_cleared = row["cleared_quantity"]
            new_cleared = already_cleared + cleared_quantity
            remaining = max(0.0, total_qty - new_cleared)
            if remaining > 0.0:
                new_status = ConsignmentStatus.BONDED_IN_STORAGE.value
            elif declaration_type == "RE_EXPORT":
                new_status = ConsignmentStatus.RE_EXPORTED.value
            else:
                new_status = ConsignmentStatus.EX_WAREHOUSED_HOME_USE.value

            conn.execute(
                """
                UPDATE bwims_consignments
                SET cleared_quantity = ?, status = ?
                WHERE entry_number = ?
                """,
                (new_cleared, new_status, entry_number.strip()),
            )

            conn.execute(
                """
                INSERT INTO bwims_clearances (
                    clearance_id, entry_number, declaration_type, cleared_quantity, duty_paid_prn, cleared_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (clearance_id, entry_number.strip(), declaration_type, cleared_quantity, duty_paid_prn, now_iso),
            )

            return {
                "clearance_id": clearance_id,
                "entry_number": entry_number,
                "cleared_quantity": cleared_quantity,
                "remaining_quantity": remaining,
                "status": new_status,
            }

    def get_stats(self) -> dict[str, Any]:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), COALESCE(SUM(bond_security_amount_ugx), 0) FROM bwims_warehouses")
        wh_row = cur.fetchone()
        wh_count = wh_row[0]
        total_bond = round(wh_row[1], 2)

        cur.execute("SELECT COUNT(*), COALESCE(SUM(cif_value_ugx), 0), SUM(CASE WHEN status = 'OVERSTAYED_AUCTION_RISK' THEN 1 ELSE 0 END) FROM bwims_consignments")
        c_row = cur.fetchone()
        consignments_count = c_row[0]
        total_cif = round(c_row[1], 2)
        overstayed_count = c_row[2] or 0

        cur.execute("SELECT COUNT(*) FROM bwims_clearances")
        clearance_count = cur.fetchone()[0]

        return {
            "database": str(self.db_path.name if isinstance(self.db_path, Path) else self.db_path),
            "warehouses_count": wh_count,
            "total_bond_security_ugx": total_bond,
            "bonded_consignments_count": consignments_count,
            "total_cif_value_ugx": total_cif,
            "overstayed_consignments_count": overstayed_count,
            "clearance_declarations_count": clearance_count,
            "status": "ONLINE",
        }

    def list_recent_consignments(self, limit: int = 5) -> list[dict[str, Any]]:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT entry_number, importer_name, warehouse_code, goods_description, cif_value_ugx, status, warehousing_date
            FROM bwims_consignments ORDER BY rowid DESC LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]

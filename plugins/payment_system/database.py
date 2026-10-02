"""Independent SQLite database for the URA Payment System (e-Services > Make a Payment suite).

Maintains dedicated persistence for Payment Registration Numbers (PRNs), bank checkout
transactions, payment clearance ledgers, and advance motor vehicle tax under data_store/payments_system.db.
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

from .models import PaymentCategory, PaymentChannel, PaymentPrnRecord, PrnStatus

logger = logging.getLogger(__name__)

_UTC = getattr(datetime, "UTC", datetime.timezone.utc)  # noqa: UP017


def _resolve_default_db_path() -> Path:
    env_path = os.getenv("PAYMENT_DB_PATH")
    if env_path:
        return Path(env_path)
    root = Path(__file__).resolve().parents[2]
    data_dir = root / "data_store"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "payments_system.db"


class PaymentDatabase:
    """Thread-safe persistence layer for URA Payment System."""

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
                    CREATE TABLE IF NOT EXISTS payment_prns (
                        prn TEXT PRIMARY KEY,
                        taxpayer_tin TEXT,
                        taxpayer_name TEXT NOT NULL,
                        payment_category TEXT NOT NULL,
                        tax_head TEXT NOT NULL,
                        amount_ugx REAL NOT NULL,
                        payment_channel TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'PENDING',
                        created_at TEXT NOT NULL,
                        expiry_date TEXT NOT NULL,
                        cleared_at TEXT,
                        bank_reference TEXT,
                        agency_details_json TEXT
                    );

                    CREATE TABLE IF NOT EXISTS payment_transactions (
                        transaction_id TEXT PRIMARY KEY,
                        prn TEXT NOT NULL,
                        payment_method TEXT NOT NULL,
                        payer_identifier TEXT NOT NULL,
                        amount_paid_ugx REAL NOT NULL,
                        status TEXT NOT NULL DEFAULT 'SUCCESS',
                        receipt_number TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        FOREIGN KEY (prn) REFERENCES payment_prns(prn)
                    );

                    CREATE TABLE IF NOT EXISTS payment_advance_tax (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        vehicle_registration_number TEXT NOT NULL,
                        vehicle_type TEXT NOT NULL,
                        capacity INTEGER NOT NULL,
                        prn TEXT NOT NULL,
                        amount_ugx REAL NOT NULL,
                        valid_until TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'COMPLIANT'
                    );
                    """
                )
            self._seed_if_empty()

    def _seed_if_empty(self) -> None:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM payment_prns")
        if cur.fetchone()[0] > 0:
            return

        now = datetime.datetime.now(_UTC)
        now_iso = now.isoformat()
        future_21d = (now + datetime.timedelta(days=21)).strftime("%Y-%m-%d")
        past_expired = (now - datetime.timedelta(days=25)).strftime("%Y-%m-%d")

        prns = [
            (
                "226030001001",
                "1000000005",
                "Kampala City Supermarket Ltd",
                "DOMESTIC_TAX",
                "VAT_STANDARD",
                18000.0,
                "COMMERCIAL_BANK",
                "CLEARED",
                now_iso,
                future_21d,
                now_iso,
                "BK-STANBIC-99018",
                json.dumps({"period": "2026-02", "return_form": "DT-1014"}),
            ),
            (
                "226030002002",
                "1000000002",
                "Nile Breweries Limited",
                "DOMESTIC_TAX",
                "DTS_EXCISE_STAMPS",
                350000.0,
                "COMMERCIAL_BANK",
                "CLEARED",
                now_iso,
                future_21d,
                now_iso,
                "BK-DFCU-44129",
                json.dumps({"stamps_order_id": "DTS-ORD-2026-001"}),
            ),
            (
                "226030003003",
                "1000000003",
                "Roofings Rolling Mills Ltd",
                "DOMESTIC_TAX",
                "CORPORATION_TAX",
                1500000.0,
                "COMMERCIAL_BANK",
                "PENDING",
                now_iso,
                future_21d,
                None,
                None,
                json.dumps({"assessment_year": 2026}),
            ),
            (
                "225090004004",
                "1000000004",
                "Mukwano Enterprises Limited",
                "DOMESTIC_TAX",
                "WITHHOLDING_TAX",
                450000.0,
                "COMMERCIAL_BANK",
                "EXPIRED",
                (now - datetime.timedelta(days=40)).isoformat(),
                past_expired,
                None,
                None,
                json.dumps({"service": "WHT on Freight"}),
            ),
            (
                "226030005005",
                "1000000007",
                "Kampala Auto Traders Limited",
                "ADVANCE_INCOME_TAX",
                "ADVANCE_INCOME_TAX",
                280000.0,
                "MOBILE_MONEY",
                "CLEARED",
                now_iso,
                future_21d,
                now_iso,
                "MM-MTN-2948194",
                json.dumps({"vehicle_plate": "UBK 412A", "seats": 14}),
            ),
        ]
        with conn:
            conn.executemany(
                """
                INSERT INTO payment_prns (
                    prn, taxpayer_tin, taxpayer_name, payment_category, tax_head,
                    amount_ugx, payment_channel, status, created_at, expiry_date,
                    cleared_at, bank_reference, agency_details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                prns,
            )

            # Seed advance tax entry
            conn.execute(
                """
                INSERT INTO payment_advance_tax (
                    vehicle_registration_number, vehicle_type, capacity, prn,
                    amount_ugx, valid_until, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("UBK 412A", "PASSENGER_PSV", 14, "226030005005", 280000.0, (now + datetime.timedelta(days=365)).strftime("%Y-%m-%d"), "COMPLIANT"),
            )

    def insert_prn(self, record: PaymentPrnRecord) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO payment_prns (
                    prn, taxpayer_tin, taxpayer_name, payment_category, tax_head,
                    amount_ugx, payment_channel, status, created_at, expiry_date,
                    cleared_at, bank_reference, agency_details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.prn,
                    record.taxpayer_tin,
                    record.taxpayer_name,
                    record.payment_category.value,
                    record.tax_head,
                    record.amount_ugx,
                    record.payment_channel.value,
                    record.status.value,
                    record.created_at,
                    record.expiry_date,
                    record.cleared_at,
                    record.bank_reference,
                    json.dumps(record.agency_details),
                ),
            )

    def get_prn(self, prn: str) -> PaymentPrnRecord | None:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM payment_prns WHERE prn = ?", (prn.strip(),))
        row = cur.fetchone()
        if not row:
            return None

        cat = PaymentCategory(row["payment_category"]) if row["payment_category"] in PaymentCategory._value2member_map_ else PaymentCategory.DOMESTIC_TAX
        ch = PaymentChannel(row["payment_channel"]) if row["payment_channel"] in PaymentChannel._value2member_map_ else PaymentChannel.COMMERCIAL_BANK
        st = PrnStatus(row["status"]) if row["status"] in PrnStatus._value2member_map_ else PrnStatus.PENDING

        details = json.loads(row["agency_details_json"]) if row["agency_details_json"] else {}
        return PaymentPrnRecord(
            prn=row["prn"],
            taxpayer_tin=row["taxpayer_tin"],
            taxpayer_name=row["taxpayer_name"],
            payment_category=cat,
            tax_head=row["tax_head"],
            amount_ugx=row["amount_ugx"],
            payment_channel=ch,
            status=st,
            created_at=row["created_at"],
            expiry_date=row["expiry_date"],
            cleared_at=row["cleared_at"],
            bank_reference=row["bank_reference"],
            agency_details=details,
        )

    def reactivate_prn(self, prn: str, new_expiry_date: str) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                UPDATE payment_prns
                SET status = 'PENDING', expiry_date = ?
                WHERE prn = ?
                """,
                (new_expiry_date, prn.strip()),
            )

    def record_checkout_transaction(
        self,
        prn: str,
        transaction_id: str,
        payment_method: str,
        payer_identifier: str,
        amount_paid_ugx: float,
        receipt_number: str,
    ) -> None:
        conn = self._get_connection()
        now_iso = datetime.datetime.now(_UTC).isoformat()
        with conn:
            conn.execute(
                """
                INSERT INTO payment_transactions (
                    transaction_id, prn, payment_method, payer_identifier,
                    amount_paid_ugx, status, receipt_number, timestamp
                ) VALUES (?, ?, ?, ?, ?, 'SUCCESS', ?, ?)
                """,
                (transaction_id, prn.strip(), payment_method, payer_identifier, amount_paid_ugx, receipt_number, now_iso),
            )
            conn.execute(
                """
                UPDATE payment_prns
                SET status = 'CLEARED', cleared_at = ?, bank_reference = ?
                WHERE prn = ?
                """,
                (now_iso, transaction_id, prn.strip()),
            )

    def get_advance_tax(self, vehicle_reg: str) -> dict[str, Any] | None:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM payment_advance_tax
            WHERE UPPER(vehicle_registration_number) = UPPER(?)
            ORDER BY id DESC LIMIT 1
            """,
            (vehicle_reg.strip(),),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def get_stats(self) -> dict[str, Any]:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), COALESCE(SUM(amount_ugx), 0) FROM payment_prns")
        row = cur.fetchone()
        total_prns = row[0]
        total_amount = round(row[1], 2)

        cur.execute("SELECT COUNT(*), COALESCE(SUM(amount_ugx), 0) FROM payment_prns WHERE status = 'CLEARED'")
        cleared_row = cur.fetchone()
        cleared_prns = cleared_row[0]
        cleared_amount = round(cleared_row[1], 2)

        cur.execute("SELECT COUNT(*) FROM payment_transactions")
        tx_count = cur.fetchone()[0]

        return {
            "database": str(self.db_path.name if isinstance(self.db_path, Path) else self.db_path),
            "total_prns_generated": total_prns,
            "total_prn_amount_ugx": total_amount,
            "cleared_prns_count": cleared_prns,
            "total_revenue_cleared_ugx": cleared_amount,
            "electronic_transactions": tx_count,
            "status": "ONLINE",
        }

    def list_recent_prns(self, limit: int = 5) -> list[dict[str, Any]]:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT prn, taxpayer_name, tax_head, amount_ugx, status, created_at, expiry_date
            FROM payment_prns ORDER BY rowid DESC LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]

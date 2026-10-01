"""Independent SQLite database for the URA EFRIS sample system.

Maintains dedicated, isolated persistence for taxpayers, e-invoices,
credit notes, stock inventory, and fiscal terminals under data_store/efris_system.db.
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

from .models import DocumentStatus, TaxpayerEfrisProfile

logger = logging.getLogger(__name__)

_UTC = getattr(datetime, "UTC", datetime.timezone.utc)  # noqa: UP017


def _resolve_default_db_path() -> Path:
    # Check env var override
    env_path = os.getenv("EFRIS_DB_PATH")
    if env_path:
        return Path(env_path)
    # Default to data_store/efris_system.db relative to repository root
    root = Path(__file__).resolve().parents[2]
    data_dir = root / "data_store"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "efris_system.db"


class EfrisDatabase:
    """Thread-safe persistence layer for EFRIS system."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else _resolve_default_db_path()
        self._is_memory = str(self.db_path) == ":memory:"
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._is_memory:
            # For in-memory testing, retain a single shared connection
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
                    CREATE TABLE IF NOT EXISTS efris_taxpayers (
                        tin TEXT PRIMARY KEY,
                        business_name TEXT NOT NULL,
                        is_vat_registered INTEGER NOT NULL DEFAULT 1,
                        efris_status TEXT NOT NULL DEFAULT 'ACTIVE',
                        mandated_sector TEXT NOT NULL,
                        registration_date TEXT NOT NULL,
                        integration_mode TEXT NOT NULL,
                        active_terminals TEXT NOT NULL,
                        sdc_enabled INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS efris_invoices (
                        fdn TEXT PRIMARY KEY,
                        verification_code TEXT NOT NULL,
                        qr_code_url TEXT NOT NULL,
                        seller_tin TEXT NOT NULL,
                        seller_name TEXT NOT NULL,
                        buyer_tin TEXT,
                        buyer_name TEXT,
                        currency TEXT NOT NULL DEFAULT 'UGX',
                        net_amount REAL NOT NULL,
                        tax_amount REAL NOT NULL,
                        gross_amount REAL NOT NULL,
                        status TEXT NOT NULL DEFAULT 'ISSUED',
                        issued_at TEXT NOT NULL,
                        items_json TEXT NOT NULL,
                        offline_ref TEXT,
                        FOREIGN KEY (seller_tin) REFERENCES efris_taxpayers(tin)
                    );

                    CREATE TABLE IF NOT EXISTS efris_credit_notes (
                        credit_note_number TEXT PRIMARY KEY,
                        original_fdn TEXT NOT NULL,
                        seller_tin TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        adjusted_gross REAL NOT NULL,
                        adjusted_vat REAL NOT NULL,
                        description TEXT,
                        status TEXT NOT NULL DEFAULT 'APPROVED',
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (original_fdn) REFERENCES efris_invoices(fdn)
                    );

                    CREATE TABLE IF NOT EXISTS efris_stock_inventory (
                        tin TEXT NOT NULL,
                        commodity_code TEXT NOT NULL,
                        description TEXT NOT NULL,
                        unit_of_measure TEXT NOT NULL,
                        quantity_on_hand REAL NOT NULL,
                        unit_cost REAL NOT NULL,
                        category TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (tin, commodity_code)
                    );

                    CREATE TABLE IF NOT EXISTS efris_stock_movements (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tin TEXT NOT NULL,
                        commodity_code TEXT NOT NULL,
                        movement_type TEXT NOT NULL,
                        quantity REAL NOT NULL,
                        reference TEXT,
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS efris_terminals (
                        terminal_id TEXT PRIMARY KEY,
                        tin TEXT NOT NULL,
                        terminal_type TEXT NOT NULL,
                        serial_number TEXT NOT NULL UNIQUE,
                        branch_name TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'ONLINE',
                        last_sync_at TEXT NOT NULL
                    );
                    """
                )
            self._seed_if_empty()

    def _seed_if_empty(self) -> None:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM efris_taxpayers")
        if cur.fetchone()[0] > 0:
            return

        now_iso = datetime.datetime.now(_UTC).isoformat()

        # Seed taxpayers
        taxpayers = [
            (
                "1000000001",
                "Kakira Sugar Limited",
                1,
                "ACTIVE",
                "Manufacturing / Agro-processing",
                "2021-01-15",
                "SYSTEM_TO_SYSTEM",
                json.dumps(["EFD-KS-001", "EFD-KS-002", "POS-SRV-01"]),
                1,
                now_iso,
            ),
            (
                "1000000002",
                "Nile Breweries Limited",
                1,
                "ACTIVE",
                "Manufacturing / Beverages",
                "2020-11-01",
                "SYSTEM_TO_SYSTEM",
                json.dumps(["NBL-JINJA-01", "NBL-MBARARA-02"]),
                1,
                now_iso,
            ),
            (
                "1000000003",
                "Roofings Rolling Mills Ltd",
                1,
                "ACTIVE",
                "Manufacturing / Construction Materials",
                "2021-03-20",
                "SYSTEM_TO_SYSTEM",
                json.dumps(["RRM-NMS-01", "RRM-LUB-02"]),
                1,
                now_iso,
            ),
            (
                "1000000004",
                "Mukwano Enterprises Limited",
                1,
                "ACTIVE",
                "Manufacturing / Fast Moving Consumer Goods",
                "2020-12-10",
                "SYSTEM_TO_SYSTEM",
                json.dumps(["MKW-IND-01", "MKW-POS-02"]),
                1,
                now_iso,
            ),
            (
                "1000000005",
                "Kampala City Supermarket Ltd",
                1,
                "ACTIVE",
                "Wholesale & Retail Trade",
                "2022-05-18",
                "EFD",
                json.dumps(["EFD-KLA-101", "EFD-KLA-102"]),
                1,
                now_iso,
            ),
        ]
        with conn:
            conn.executemany(
                """
                INSERT INTO efris_taxpayers (
                    tin, business_name, is_vat_registered, efris_status, mandated_sector,
                    registration_date, integration_mode, active_terminals, sdc_enabled, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                taxpayers,
            )

            # Seed stock catalogue for Kampala Supermarket
            stocks = [
                ("1000000005", "50202301", "Mineral Drinking Water 500ml", "BOTTLE", 5000.0, 1000.0, "Beverages", now_iso),
                ("1000000005", "50202201", "Lager Beer 500ml", "BOTTLE", 1200.0, 4000.0, "Alcoholic Beverages", now_iso),
                ("1000000005", "50161801", "White Refined Sugar 1kg", "BAG", 800.0, 4500.0, "Dry Foods", now_iso),
            ]
            conn.executemany(
                """
                INSERT INTO efris_stock_inventory (
                    tin, commodity_code, description, unit_of_measure, quantity_on_hand,
                    unit_cost, category, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                stocks,
            )

            # Seed terminals
            terminals = [
                ("TERM-KS-01", "1000000001", "SYSTEM_TO_SYSTEM", "SDC-KLA-9901", "Jinji Mill 1", "ONLINE", now_iso),
                ("TERM-NBL-01", "1000000002", "SYSTEM_TO_SYSTEM", "SDC-JJA-4412", "Jinja Brewery Line", "ONLINE", now_iso),
                ("TERM-KLA-01", "1000000005", "EFD", "EFD-KLA-101", "Kampala Central Checkout", "ONLINE", now_iso),
            ]
            conn.executemany(
                """
                INSERT INTO efris_terminals (
                    terminal_id, tin, terminal_type, serial_number, branch_name, status, last_sync_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                terminals,
            )

            # Seed sample verified invoice
            seed_fdn = "01240000000000001001"
            sample_items = [
                {
                    "commodity_code": "50202301",
                    "description": "Mineral Drinking Water 500ml",
                    "quantity": 100.0,
                    "unit_price": 1000.0,
                    "total_amount": 118000.0,
                }
            ]
            conn.execute(
                """
                INSERT INTO efris_invoices (
                    fdn, verification_code, qr_code_url, seller_tin, seller_name,
                    buyer_tin, buyer_name, currency, net_amount, tax_amount, gross_amount,
                    status, issued_at, items_json, offline_ref
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    seed_fdn,
                    "A9F23B",
                    f"https://efris.ura.go.ug/verify?fdn={seed_fdn}&code=A9F23B",
                    "1000000005",
                    "Kampala City Supermarket Ltd",
                    "1000000001",
                    "Kakira Sugar Limited",
                    "UGX",
                    100000.0,
                    18000.0,
                    118000.0,
                    DocumentStatus.ISSUED.value,
                    "2026-03-01T10:15:30Z",
                    json.dumps(sample_items),
                    None,
                ),
            )

    # ── Database Operations ─────────────────────────────────────────

    def get_taxpayer(self, tin: str) -> TaxpayerEfrisProfile | None:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM efris_taxpayers WHERE tin = ?", (tin.strip(),))
        row = cur.fetchone()
        if not row:
            return None
        return TaxpayerEfrisProfile(
            tin=row["tin"],
            business_name=row["business_name"],
            is_vat_registered=bool(row["is_vat_registered"]),
            efris_status=row["efris_status"],
            mandated_sector=row["mandated_sector"],
            registration_date=row["registration_date"],
            integration_mode=row["integration_mode"],
            active_terminals=json.loads(row["active_terminals"]) if row["active_terminals"] else [],
            sdc_enabled=bool(row["sdc_enabled"]),
        )

    def insert_invoice(self, invoice_record: dict[str, Any]) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO efris_invoices (
                    fdn, verification_code, qr_code_url, seller_tin, seller_name,
                    buyer_tin, buyer_name, currency, net_amount, tax_amount, gross_amount,
                    status, issued_at, items_json, offline_ref
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invoice_record["fdn"],
                    invoice_record["verification_code"],
                    invoice_record["qr_code_url"],
                    invoice_record["seller_tin"],
                    invoice_record["seller_name"],
                    invoice_record.get("buyer_tin"),
                    invoice_record.get("buyer_name"),
                    invoice_record.get("currency", "UGX"),
                    invoice_record["net_amount"],
                    invoice_record["tax_amount"],
                    invoice_record["gross_amount"],
                    invoice_record["status"].value if hasattr(invoice_record["status"], "value") else str(invoice_record["status"]),
                    invoice_record["issued_at"],
                    json.dumps(invoice_record.get("items", [])),
                    invoice_record.get("offline_ref"),
                ),
            )

    def get_invoice(self, fdn: str) -> dict[str, Any] | None:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM efris_invoices WHERE fdn = ?", (fdn.strip(),))
        row = cur.fetchone()
        if not row:
            return None
        items = json.loads(row["items_json"]) if row["items_json"] else []
        return {
            "fdn": row["fdn"],
            "verification_code": row["verification_code"],
            "qr_code_url": row["qr_code_url"],
            "seller_tin": row["seller_tin"],
            "seller_name": row["seller_name"],
            "buyer_tin": row["buyer_tin"],
            "buyer_name": row["buyer_name"],
            "currency": row["currency"],
            "net_amount": row["net_amount"],
            "tax_amount": row["tax_amount"],
            "gross_amount": row["gross_amount"],
            "status": DocumentStatus(row["status"]) if row["status"] in DocumentStatus._value2member_map_ else DocumentStatus.ISSUED,
            "issued_at": row["issued_at"],
            "items_count": len(items),
            "items": items,
            "offline_ref": row["offline_ref"],
        }

    def update_invoice_status(self, fdn: str, status: DocumentStatus) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute(
                "UPDATE efris_invoices SET status = ? WHERE fdn = ?",
                (status.value, fdn.strip()),
            )

    def insert_credit_note(self, cn: dict[str, Any]) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO efris_credit_notes (
                    credit_note_number, original_fdn, seller_tin, reason,
                    adjusted_gross, adjusted_vat, description, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cn["credit_note_number"],
                    cn["original_fdn"],
                    cn["seller_tin"],
                    cn["reason"],
                    cn["adjusted_gross"],
                    cn["adjusted_vat"],
                    cn.get("description", ""),
                    cn.get("status", "APPROVED"),
                    cn["created_at"],
                ),
            )
            # Mark original invoice as adjusted
            conn.execute(
                "UPDATE efris_invoices SET status = ? WHERE fdn = ?",
                (DocumentStatus.ADJUSTED.value, cn["original_fdn"].strip()),
            )

    def get_stock_items(self, tin: str, commodity_code: str | None = None) -> list[dict[str, Any]]:
        conn = self._get_connection()
        cur = conn.cursor()
        if commodity_code:
            cur.execute(
                "SELECT * FROM efris_stock_inventory WHERE tin = ? AND commodity_code = ?",
                (tin.strip(), commodity_code.strip()),
            )
        else:
            cur.execute("SELECT * FROM efris_stock_inventory WHERE tin = ?", (tin.strip(),))
        rows = cur.fetchall()
        return [
            {
                "commodity_code": r["commodity_code"],
                "description": r["description"],
                "unit_of_measure": r["unit_of_measure"],
                "quantity_on_hand": r["quantity_on_hand"],
                "unit_cost": r["unit_cost"],
                "category": r["category"],
            }
            for r in rows
        ]

    def deduct_stock(self, tin: str, commodity_code: str, quantity: float) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                UPDATE efris_stock_inventory
                SET quantity_on_hand = MAX(0.0, quantity_on_hand - ?),
                    updated_at = ?
                WHERE tin = ? AND commodity_code = ?
                """,
                (quantity, datetime.datetime.now(_UTC).isoformat(), tin.strip(), commodity_code.strip()),
            )
            conn.execute(
                """
                INSERT INTO efris_stock_movements (tin, commodity_code, movement_type, quantity, created_at)
                VALUES (?, ?, 'INVOICE_DEDUCTION', ?, ?)
                """,
                (tin.strip(), commodity_code.strip(), -quantity, datetime.datetime.now(_UTC).isoformat()),
            )

    def record_stock_in(
        self,
        tin: str,
        commodity_code: str,
        description: str,
        quantity: float,
        unit_cost: float,
        unit_of_measure: str = "PIECES",
        category: str = "General",
    ) -> float:
        conn = self._get_connection()
        now_iso = datetime.datetime.now(_UTC).isoformat()
        with conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT quantity_on_hand FROM efris_stock_inventory WHERE tin = ? AND commodity_code = ?",
                (tin.strip(), commodity_code.strip()),
            )
            row = cur.fetchone()
            if row:
                new_qty = row["quantity_on_hand"] + quantity
                conn.execute(
                    """
                    UPDATE efris_stock_inventory
                    SET quantity_on_hand = ?, unit_cost = ?, updated_at = ?
                    WHERE tin = ? AND commodity_code = ?
                    """,
                    (new_qty, unit_cost, now_iso, tin.strip(), commodity_code.strip()),
                )
            else:
                new_qty = quantity
                conn.execute(
                    """
                    INSERT INTO efris_stock_inventory (
                        tin, commodity_code, description, unit_of_measure,
                        quantity_on_hand, unit_cost, category, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (tin.strip(), commodity_code.strip(), description, unit_of_measure, new_qty, unit_cost, category, now_iso),
                )

            conn.execute(
                """
                INSERT INTO efris_stock_movements (tin, commodity_code, movement_type, quantity, created_at)
                VALUES (?, ?, 'STOCK_IN', ?, ?)
                """,
                (tin.strip(), commodity_code.strip(), quantity, now_iso),
            )
            return new_qty

    def get_stats(self) -> dict[str, Any]:
        """Aggregate statistical metrics for EFRIS database."""
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM efris_taxpayers")
        taxpayers_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*), COALESCE(SUM(gross_amount), 0), COALESCE(SUM(tax_amount), 0) FROM efris_invoices")
        inv_row = cur.fetchone()
        invoices_count = inv_row[0]
        total_gross = round(inv_row[1], 2)
        total_vat = round(inv_row[2], 2)

        cur.execute("SELECT COUNT(*) FROM efris_credit_notes")
        cn_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*), COALESCE(SUM(quantity_on_hand), 0) FROM efris_stock_inventory")
        stock_row = cur.fetchone()
        stock_items_count = stock_row[0]
        total_stock_qty = stock_row[1]

        cur.execute("SELECT COUNT(*) FROM efris_terminals")
        terminals_count = cur.fetchone()[0]

        return {
            "database": str(self.db_path.name if isinstance(self.db_path, Path) else self.db_path),
            "taxpayers_count": taxpayers_count,
            "invoices_count": invoices_count,
            "credit_notes_count": cn_count,
            "total_fiscalized_ugx": total_gross,
            "total_vat_collected_ugx": total_vat,
            "stock_items_count": stock_items_count,
            "total_stock_units": total_stock_qty,
            "terminals_count": terminals_count,
            "status": "ONLINE",
        }

    def list_recent_invoices(self, limit: int = 5) -> list[dict[str, Any]]:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT fdn, seller_tin, seller_name, buyer_name, gross_amount, tax_amount, status, issued_at
            FROM efris_invoices ORDER BY issued_at DESC LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]

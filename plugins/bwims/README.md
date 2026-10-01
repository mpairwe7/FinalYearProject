# BWIMS Plugin & Connector

This folder provides a complete sample system and agentic connector for the **URA Bonded Warehouse Information Management System (BWIMS)** under the East African Community Customs Management Act (EACCMA).

## System Capabilities

1. **`models.py`**:
   - `BondedWarehouse`: Warehouse code, licensing, bond security.
   - `BondedConsignment`: IM7 entry number, CIF value, warehousing date, statutory 9-month expiry countdown.
   - `ExWarehouseClearance`: IM4 home use or re-export clearance with PRN payment validation.

2. **`database.py` (`BwimsDatabase`)**:
   - Independent SQLite database (`data_store/bwims_system.db`).
   - Pre-seeded with bonded facilities (Nakawa ICD, Jinja Silos, Entebbe Aviation Shed, Namanve Central Bond).

3. **`service.py` (`BwimsService`)**:
   - Tracking storage duration and alerting on consignments exceeding the statutory 270-day (9-month) threshold at risk of customs auction.

4. **`connector.py` (`BwimsConnector`)**:
   - `bwims_consignment_status`: Track cargo in bond and inspect statutory deadlines.
   - `bwims_warehouse_inventory`: Audit stock across bonded warehouses.
   - `bwims_release_clearance`: Record ex-warehouse clearances.

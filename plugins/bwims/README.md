# BWIMS Plugin & Connector

This folder provides a local BWIMS simulator for agent workflow development. Its warehouse and consignment records are fixtures; clearance actions do not release real goods or update a customs system.

## System Capabilities

1. **`models.py`**:
   - `BondedWarehouse`: Warehouse code, licensing, bond security.
   - `BondedConsignment`: Sample IM7-shaped entry, value, and warehousing dates.
   - `ExWarehouseClearance`: Simulated clearance record; no customs entry or payment is verified.

2. **`database.py` (`BwimsDatabase`)**:
   - Independent SQLite database (`data_store/bwims_system.db`).
   - Pre-seeded with test warehouse fixtures, not a live URA warehouse register.

3. **`service.py` (`BwimsService`)**:
   - Calculates illustrative storage durations from fixture dates; do not use its alerts as legal advice or customs status.

4. **`connector.py` (`BwimsConnector`)**:
   - `bwims_consignment_status`: Read sample consignment dates and locally calculated durations.
   - `bwims_warehouse_inventory`: Audit stock across bonded warehouses.
   - `bwims_release_clearance`: Record ex-warehouse clearances.

## Taxpayer guidance

The chat workflow `bwims_warehouse_guidance` collects only the type of help
requested and the user's role. It does not query a consignment, collect TINs or
customs references, change stock, or release goods. It links to URA's public
BWIMS and customs-system pages. The connector above remains a local simulator;
its fixture results must not guide customs or release decisions.

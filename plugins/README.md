# URA Agentic System Plugins & Connectors

This directory houses system plugins and connectors for external, auxiliary, and enterprise tax platforms that integrate with the agentic URA system.

## Directory Layout & Independent Databases

```
plugins/
├── base.py                   # Plugin, PluginMetadata, PluginStatus, SystemConnector, Tool
├── orchestrator.py           # PluginOrchestrator singleton, lifecycle & tool wiring
├── efris/                    # Electronic Fiscal Receipting & Invoicing System (data_store/efris_system.db)
│   ├── models.py             # FDN, invoice, credit note, stock, and profile schemas
│   ├── database.py           # Dedicated SQLite database store & migrations
│   ├── service.py            # Stateful simulator with FDN/checksum generation & ledger
│   ├── client.py             # Typed SDK for EFRIS operations
│   ├── connector.py          # EfrisConnector with 4 Tool implementations
│   └── README.md
├── digital_tax_stamps/       # Digital Tracking Solution (DTS) / Kakasa (data_store/dts_system.db)
│   ├── models.py             # Stamp verification, orders, activations, tariffs
│   ├── database.py           # Dedicated SQLite database store
│   ├── service.py            # DTS service simulator & Kakasa verification engine
│   ├── client.py             # Typed SDK for DTS operations
│   ├── connector.py          # DigitalTaxStampsConnector with 4 Tool implementations
│   └── README.md
├── ursb/                     # Uganda Registration Services Bureau (data_store/ursb_system.db)
│   ├── models.py             # Company incorporation, business names, Form 20 directors
│   ├── database.py           # Dedicated SQLite database store
│   ├── service.py            # Business registry search & legal compliance engine
│   ├── client.py             # Typed SDK for URSB operations
│   ├── connector.py          # UrsbConnector with 3 Tool implementations
│   └── README.md
├── bwims/                    # Bonded Warehouse Information Management System (data_store/bwims_system.db)
│   ├── models.py             # Customs bonded warehouses, IM7 consignments, IM4 release
│   ├── database.py           # Dedicated SQLite database store
│   ├── service.py            # IM7 warehousing tracking & 9-month overstay alert engine
│   ├── client.py             # Typed SDK for BWIMS operations
│   ├── connector.py          # BwimsConnector with 3 Tool implementations
│   └── README.md
├── tin_registration/         # URA Taxpayer Registration & Instant TIN (data_store/tin_system.db)
│   ├── models.py             # Taxpayers, NIN verification, non-individual company TINs
│   ├── database.py           # Dedicated SQLite database store
│   ├── service.py            # Instant 10-digit TIN issuance & tax obligations engine
│   ├── client.py             # Typed SDK for TIN operations
│   ├── connector.py          # TinRegistrationConnector with 4 Tool implementations
│   └── README.md
├── payment_system/           # URA Make a Payment & PRN Suite (data_store/payments_system.db)
│   ├── models.py             # 12-digit PRN slips, expired PRN renewals, checkout receipts
│   ├── database.py           # Dedicated SQLite database store
│   ├── service.py            # PRN generator, bank clearance, and advance tax verification
│   ├── client.py             # Typed SDK for Payment operations
│   ├── connector.py          # PaymentConnector with 5 Tool implementations
│   └── README.md
├── EFRIS/                    # Symlink -> efris
├── dts/                      # Symlink -> digital_tax_stamps
├── digital tax stamps/       # Symlink -> digital_tax_stamps
├── URSB/                     # Symlink -> ursb
├── BWIMS/                    # Symlink -> bwims
├── TIN/                      # Symlink -> tin_registration
├── tin/                      # Symlink -> tin_registration
├── payment/                  # Symlink -> payment_system
├── payments/                 # Symlink -> payment_system
└── make_payment/             # Symlink -> payment_system
```

## Connectors & Agent Tools (23 Tools Total)

### 1. EFRIS Connector (`efris` namespace, `efris_system.db`)
1. **`efris_fiscal_invoice`**: Generates 20-digit Fiscal Document Numbers (FDN) with 18% standard VAT calculation and verification codes, or validates existing FDN authenticity.
2. **`efris_taxpayer_status`**: Queries taxpayer EFRIS enrollment, active EFD devices, cashier terminals, and integration mode.
3. **`efris_stock_management`**: Checks real-time stock balances or records stock additions from local purchases and customs imports.
4. **`efris_credit_note`**: Applies credit notes against issued fiscal documents with elevated authorization.

### 2. Digital Tax Stamps Connector (`digital_tax_stamps` namespace, `dts_system.db`)
1. **`dts_verify_stamp`**: Authenticates digital tax stamps on gazetted commodities (beer, spirits, wine, bottled water, soda, tobacco, cement, sugar, cooking oil, juices) via the Kakasa verification protocol.
2. **`dts_order_stamps`**: Requisitions stamps, calculates statutory unit tariffs, and generates 10-digit PRNs with collection designated at SICPA Uganda (Ntinda).
3. **`dts_activate_stamps`**: Activates stamps on factory packaging lines or reports spoiled stamps from packaging line jams.
4. **`dts_taxpayer_status`**: Inspects manufacturer/importer packaging lines, applicator types, and compliance status.

### 3. URSB Connector (`ursb` namespace, `ursb_system.db`)
1. **`ursb_verify_business`**: Searches and verifies legal business names, registration numbers (BRN), and Form 20 director records.
2. **`ursb_register_business`**: Reserves and registers formal business names and companies.
3. **`ursb_compliance_status`**: Audits active legal standing, annual return filings, and non-individual TIN readiness.

### 4. BWIMS Connector (`bwims` namespace, `bwims_system.db`)
1. **`bwims_consignment_status`**: Tracks imported cargo stored under IM7 customs bond and enforces statutory 9-month overstay limits.
2. **`bwims_warehouse_inventory`**: Audits inventory balances across licensed customs bonded warehouses.
3. **`bwims_release_clearance`**: Processes ex-warehouse entries for home consumption (IM4) or re-export.

### 5. TIN Registration Connector (`tin_registration` namespace, `tin_system.db`)
1. **`tin_search_verify`**: Looks up taxpayer 10-digit TIN, citizen NIN, or URSB incorporation number.
2. **`tin_apply_individual`**: Issues Instant Individual TINs for citizens with valid 14-character NINs.
3. **`tin_apply_non_individual`**: Registers Non-Individual corporate TINs cross-referenced with URSB credentials.
4. **`tin_tax_obligations`**: Enrolls or updates statutory tax heads (VAT, PAYE, CIT).

### 6. URA Payment System Connector (`payment_system` namespace, `payments_system.db`)
1. **`payment_generate_prn`**: Generates 12-digit PRN payment vouchers with bank barcodes and 21-day validity for taxes and NTR/MDA fees.
2. **`payment_view_status`**: Verifies real-time bank clearance, ledger posting, and electronic receipts.
3. **`payment_reactivate_prn`**: Reactivates expired PRNs without having to re-declare tax returns.
4. **`payment_checkout_settle`**: Executes electronic checkout settlement via VISA, MasterCard, or Mobile Money (MTN/Airtel).
5. **`payment_verify_advance_tax`**: Verifies motor vehicle advance income tax compliance for passenger PSVs and freight carriers.

## UI Integration (Inspired by Grok App Connectors)

- **Chat Composer Attach Icon**: Clicking the attach button displays a Grok-inspired menu:
  - 📄 Upload Document (PDF, Word, Excel, CSV, Image)
  - ⚡ Add Connector / Connect Systems (displays active count badge)
- **Active Connector Chips Bar**: Displays real-time green-pulsing active chips above the prompt (`[EFRIS · Active]`, `[DTS · Active]`, `[URSB · Active]`, `[BWIMS · Active]`, `[TIN · Active]`, `[PAYMENTS · Active]`).
- **Connectors Modal (`ConnectorsModal.tsx`)**:
  - Connect / Disconnect individual enterprise systems.
  - Inspect live SQLite database records (`data_store/*.db`) directly from the UI.

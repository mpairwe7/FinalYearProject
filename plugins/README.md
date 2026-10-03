# URA Agentic System Plugins & Connectors

This directory contains **local connector simulators** for URA and related services. They use SQLite fixture data; they do not connect to URA, NIRA, URSB, a bank, or a payment network. A healthy simulator is not evidence that an external service is available, and a generated reference is not an official URA record.

## Directory Layout & Local Simulator Databases

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
│   ├── service.py            # Sample PRN generation, simulated clearance, and advance tax examples
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
1. **`efris_fiscal_invoice`**: Simulates local invoice references and validates fixture records; it does not issue or verify an official FDN.
2. **`efris_taxpayer_status`**: Reads local EFRIS fixture profiles and device examples.
3. **`efris_stock_management`**: Reads or changes simulator inventory.
4. **`efris_credit_note`**: Creates a simulator credit note after explicit confirmation.

### 2. Digital Tax Stamps Connector (`digital_tax_stamps` namespace, `dts_system.db`)
1. **`dts_verify_stamp`**: Checks sample stamp fixtures locally; it does not verify a physical stamp through URA or Kakasa.
2. **`dts_order_stamps`**: Simulates stamp orders, fees, and payment references using local fixtures.
3. **`dts_activate_stamps`**: Updates simulator stamp status; it does not activate physical stamps.
4. **`dts_taxpayer_status`**: Inspects manufacturer/importer packaging lines, applicator types, and compliance status.

### 3. URSB Connector (`ursb` namespace, `ursb_system.db`)
1. **`ursb_verify_business`**: Searches and verifies legal business names, registration numbers (BRN), and Form 20 director records.
2. **`ursb_register_business`**: Creates a local simulator registration record; it does not reserve or register a real business.
3. **`ursb_compliance_status`**: Audits active legal standing, annual return filings, and non-individual TIN readiness.

### 4. BWIMS Connector (`bwims` namespace, `bwims_system.db`)
1. **`bwims_consignment_status`**: Reads sample cargo fixtures and illustrative dates; it does not verify a customs entry or determine a legal deadline.
2. **`bwims_warehouse_inventory`**: Audits inventory balances across licensed customs bonded warehouses.
3. **`bwims_release_clearance`**: Simulates an ex-warehouse clearance; it does not release real bonded goods.

### 5. TIN Registration Connector (`tin_registration` namespace, `tin_system.db`)
1. **`tin_search_verify`**: Searches local taxpayer fixture records.
2. **`tin_apply_individual`**: Creates a simulator record; it does not verify a NIN with NIRA or issue a live TIN.
3. **`tin_apply_non_individual`**: Creates a simulator company record; it does not verify URSB credentials.
4. **`tin_tax_obligations`**: Updates simulator tax-head records.

### 6. URA Payment System Connector (`payment_system` namespace, `payments_system.db`)
1. **`payment_generate_prn`**: Generates a simulator payment reference; it is not a valid URA PRN.
2. **`payment_view_status`**: Reads simulator payment status; it does not check bank clearance or the URA ledger.
3. **`payment_reactivate_prn`**: Changes a simulator reference only.
4. **`payment_checkout_settle`**: Simulates checkout; it does not contact a card network or mobile-money provider.
5. **`payment_verify_advance_tax`**: Verifies motor vehicle advance income tax compliance for passenger PSVs and freight carriers.

## Access and safety

- Connector status and enablement are available to staff at `/admin/connectors`. The page identifies every built-in connector as a simulation and does not expose taxpayer records.
- Connector health and toggle APIs require staff authentication; changing state also requires a staff-writer role. Raw record inspection is retired (`410 Gone`); taxpayers cannot inspect fixture databases.
- Mutating agent tools return a proposal first. The MCP dispatch layer checks role and consent, then requires explicit confirmation and an idempotency key before it runs the action.
- The standalone plugin service requires `PLUGINS_API_KEY`; its write APIs use confirmation and replay keys. Production always disables local simulator writes; `FLAG_ENTERPRISE_CONNECTORS` cannot enable fixture data. Keep the service key in the server environment and never send it from a browser.
- Dynamic remote registration is disabled. The previous endpoint accepted arbitrary URLs and browser-supplied credentials without MCP discovery or a review step. A reviewed deployment configuration and a real protocol negotiation path are required before re-enabling remote connectors.
- Taxpayer filing, TIN registration, and payment workflows are guidance only. They link to the official URA portal and state that the chat has not submitted a filing, created a TIN, or made a payment.

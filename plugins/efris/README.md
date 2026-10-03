# EFRIS Plugin & Connector

This folder provides a local EFRIS simulator for agent workflow development. It does not connect to URA and cannot issue or verify an official fiscal document.

## System Components

1. **`models.py`**:
   - `FiscalInvoiceRequest`, `FiscalInvoiceResponse`: 20-digit Fiscal Document Number (FDN), verification code, QR codes, 18% standard VAT, B2B/B2C/B2G structures.
   - `InvoiceVerificationRequest`, `InvoiceVerificationResponse`: FDN validity checking, seller/buyer details, tamper verification.
   - `CreditNoteRequest`, `CreditNoteResponse`: Sample invoice adjustments (`GOODS_RETURNED`, `PRICE_DISCOUNT`, `INVOICING_ERROR`).
   - `StockItem`, `TaxpayerEfrisProfile`: Local stock fixtures, commodity codes, and example device-terminal data.

2. **`service.py` (`EfrisService`)**:
   - Stateful local simulator with sample operations:
     - 20-digit FDN generation algorithm (`01` + date + sequence checksum).
     - Cryptographic 6-character verification code generation.
     - Local SQLite ledger with fictionalized/sample taxpayer fixtures.
     - Automatic stock quantity deduction on invoice issuance.
     - Offline voucher batch synchronization (allowing up to 5 days offline operation per URA guidelines).

3. **`client.py` (`EfrisClient`)**:
   - Client SDK for local simulator interactions; remote REST API calls are not implemented.

4. **`connector.py` (`EfrisConnector`)**:
   - Agentic connector that wraps the EFRIS client into tools conforming to the URA `Tool` and `ToolSchema` specifications:
     - `efris_fiscal_invoice`: Generate or verify fiscal receipts/invoices.
     - `efris_taxpayer_status`: Check registration, VAT enrollment, and terminal statuses.
     - `efris_stock_management`: Inspect stock levels and record local purchases/imports.
     - `efris_credit_note`: Adjust issued invoices with elevated authorization.

# EFRIS Plugin & Connector

This folder provides a complete sample system and agentic connector for the **Uganda Revenue Authority Electronic Fiscal Receipting and Invoicing System (EFRIS)** under the Tax Procedures Code Act 2014.

## System Components

1. **`models.py`**:
   - `FiscalInvoiceRequest`, `FiscalInvoiceResponse`: 20-digit Fiscal Document Number (FDN), verification code, QR codes, 18% standard VAT, B2B/B2C/B2G structures.
   - `InvoiceVerificationRequest`, `InvoiceVerificationResponse`: FDN validity checking, seller/buyer details, tamper verification.
   - `CreditNoteRequest`, `CreditNoteResponse`: Statutory invoice adjustments (`GOODS_RETURNED`, `PRICE_DISCOUNT`, `INVOICING_ERROR`).
   - `StockItem`, `TaxpayerEfrisProfile`: Real-time stock balance tracking, commodity classification codes, device terminals (EFD/SDC).

2. **`service.py` (`EfrisService`)**:
   - Stateful simulator replicating central URA EFRIS operations:
     - 20-digit FDN generation algorithm (`01` + date + sequence checksum).
     - Cryptographic 6-character verification code generation.
     - In-memory transactional ledger with pre-seeded taxpayers (e.g. Kakira Sugar, Nile Breweries, Mukwano).
     - Automatic stock quantity deduction on invoice issuance.
     - Offline voucher batch synchronization (allowing up to 5 days offline operation per URA guidelines).

3. **`client.py` (`EfrisClient`)**:
   - Client SDK for application and agent interactions, supporting local engine or remote REST API endpoints.

4. **`connector.py` (`EfrisConnector`)**:
   - Agentic connector that wraps the EFRIS client into tools conforming to the URA `Tool` and `ToolSchema` specifications:
     - `efris_fiscal_invoice`: Generate or verify fiscal receipts/invoices.
     - `efris_taxpayer_status`: Check registration, VAT enrollment, and terminal statuses.
     - `efris_stock_management`: Inspect stock levels and record local purchases/imports.
     - `efris_credit_note`: Adjust issued invoices with elevated authorization.

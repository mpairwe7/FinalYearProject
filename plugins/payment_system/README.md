# URA Payment System Plugin & Connector

This folder provides a complete sample system and agentic connector for the **URA Payment System (e-Services > Make a Payment suite)**.

## System Capabilities

1. **`models.py`**:
   - `GeneratePrnRequest`, `GeneratePrnResponse`: 12-digit PRN vouchers for taxes or NTR/MDA fees with bank barcodes and 21-day validity.
   - `ReactivatePrnRequest`, `ReactivatePrnResponse`: Extending expired PRN validity without re-filing tax returns.
   - `PaymentStatusRequest`, `PaymentStatusResponse`: Verifying real-time bank postings and electronic cleared receipts.
   - `PaymentCheckoutRequest`, `PaymentCheckoutResponse`: Instant VISA, MasterCard, or Mobile Money (MTN/Airtel) clearance.
   - `AdvanceTaxVerifyRequest`, `AdvanceTaxVerifyResponse`: Verifying motor vehicle advance income tax (UGX 20,000/seat or UGX 50,000/tonne).

2. **`database.py` (`PaymentDatabase`)**:
   - Independent SQLite database (`data_store/payments_system.db`).
   - Pre-seeded with PRN vouchers, checkout ledgers, and vehicle advance tax compliance records.

3. **`service.py` (`PaymentService`)**:
   - Automated 12-digit PRN generator, bank clearance simulator, and 21-day validity lifecycle engine.

4. **`connector.py` (`PaymentConnector`)**:
   - `payment_generate_prn`: Generate a PRN payment slip.
   - `payment_view_status`: Real-time clearance inspection.
   - `payment_reactivate_prn`: Reactivate expired PRN.
   - `payment_checkout_settle`: Instant card or mobile money checkout.
   - `payment_verify_advance_tax`: Motor vehicle advance tax verification.

# URA Payment System Plugin & Connector

This folder provides a **local payment simulator** for agent workflow development. It is not connected to URA e-Services, a bank, VISA, Mastercard, or mobile-money providers; its references and receipts are not valid for payment.

## System Capabilities

1. **`models.py`**:
   - `GeneratePrnRequest`, `GeneratePrnResponse`: Sample PRN-shaped references for local scenarios; they cannot be used to pay taxes or fees.
   - `ReactivatePrnRequest`, `ReactivatePrnResponse`: Extending sample reference validity in the local database.
   - `PaymentStatusRequest`, `PaymentStatusResponse`: Simulator-only payment and receipt status.
   - `PaymentCheckoutRequest`, `PaymentCheckoutResponse`: Simulated checkout state; no card or mobile-money network is contacted.
   - `AdvanceTaxVerifyRequest`, `AdvanceTaxVerifyResponse`: Sample motor-vehicle tax scenarios; not an assessment or compliance check.

2. **`database.py` (`PaymentDatabase`)**:
   - Independent SQLite database (`data_store/payments_system.db`).
   - Pre-seeded with PRN vouchers, checkout ledgers, and vehicle advance tax compliance records.

3. **`service.py` (`PaymentService`)**:
   - Sample 12-digit reference generator, local clearance state, and illustrative expiry lifecycle.

4. **`connector.py` (`PaymentConnector`)**:
   - `payment_generate_prn`: Generate a PRN payment slip.
   - `payment_view_status`: Simulator-only clearance inspection.
   - `payment_reactivate_prn`: Reactivate expired PRN.
   - `payment_checkout_settle`: Simulated card or mobile-money checkout; no network is contacted.
   - `payment_verify_advance_tax`: Motor vehicle advance tax verification.

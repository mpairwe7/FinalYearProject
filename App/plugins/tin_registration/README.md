# URA TIN Registration Plugin & Connector

This folder provides a **local TIN-registration simulator** for agent workflow development. It does not connect to URA or NIRA and cannot issue an official TIN or verify an identity document.

## System Capabilities

1. **`models.py`**:
   - `TaxpayerRecord`, `TaxObligation`: Sample taxpayer fixtures, categories, and tax-head examples (VAT, PAYE, CIT).
   - `InstantTinRequest`, `InstantTinResponse`: Simulator TIN generation using local validation rules, not NIRA verification.
   - `NonIndividualTinRequest`, `NonIndividualTinResponse`: Simulated company registration data; no URSB lookup or official TIN issuance.

2. **`database.py` (`TinDatabase`)**:
   - Independent SQLite database (`data_store/tin_system.db`).
   - Pre-seeded with test fixtures. Never treat these entries as URA registry records.

3. **`service.py` (`TinRegistrationService`)**:
   - Automated sample TIN generation and duplicate fixture checks.

4. **`connector.py` (`TinRegistrationConnector`)**:
   - `tin_search_verify`: Search local taxpayer fixtures; it cannot verify the URA register.
   - `tin_apply_individual`: Create a simulator record; taxpayer-facing guidance directs users to the secure URA portal.
   - `tin_apply_non_individual`: Create a simulator record; it does not check URSB or issue a real TIN.
   - `tin_tax_obligations`: Enroll for statutory tax heads (e.g. VAT when turnover > 150M).

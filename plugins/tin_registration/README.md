# URA TIN Registration Plugin & Connector

This folder provides a complete sample system and agentic connector for the **URA Tax Identification Number (TIN) Registration System**.

## System Capabilities

1. **`models.py`**:
   - `TaxpayerRecord`, `TaxObligation`: Registered taxpayers, legal categories, tax head enrollments (VAT, PAYE, CIT).
   - `InstantTinRequest`, `InstantTinResponse`: Instant Individual TIN generation with NIRA National ID (NIN) validation.
   - `NonIndividualTinRequest`, `NonIndividualTinResponse`: Corporate company TIN issuance cross-referenced with URSB.

2. **`database.py` (`TinDatabase`)**:
   - Independent SQLite database (`data_store/tin_system.db`).
   - Pre-seeded with realistic taxpayers (Kakira Sugar, Nile Breweries, Roofings, Mukwano, Kampala Supermarket, David Ochieng).

3. **`service.py` (`TinRegistrationService`)**:
   - Automated 10-digit TIN generation, duplicate NIN prevention, and statutory tax obligations activation.

4. **`connector.py` (`TinRegistrationConnector`)**:
   - `tin_search_verify`: Search and verify TIN, taxpayer legal name, and active status.
   - `tin_apply_individual`: Issue instant individual TIN via NIN.
   - `tin_apply_non_individual`: Register company TIN linked to URSB.
   - `tin_tax_obligations`: Enroll for statutory tax heads (e.g. VAT when turnover > 150M).

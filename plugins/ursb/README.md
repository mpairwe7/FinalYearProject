# URSB Plugin & Connector

This folder provides a complete sample system and agentic connector for the **Uganda Registration Services Bureau (URSB)**.

## System Capabilities

1. **`models.py`**:
   - `BusinessEntity`, `DirectorInfo`: Corporate particulars, registered office, Form 20 directors, annual return status.
   - `BusinessSearchRequest`, `BusinessSearchResponse`: Querying business names or BRNs.
   - `ComplianceCheckResponse`: Verifying active legal standing and eligibility for URA non-individual TIN application.

2. **`database.py` (`UrsbDatabase`)**:
   - Independent SQLite database (`data_store/ursb_system.db`).
   - Pre-seeded with corporate entities (Kakira Sugar, Nile Breweries, Mukwano, Roofings, Kampala City Supermarket).

3. **`service.py` (`UrsbService`)**:
   - Simulator handling name searches, incorporation generation (`URSB-CO-XXXXX`), and legal compliance audits.

4. **`connector.py` (`UrsbConnector`)**:
   - `ursb_verify_business`: Search and verify businesses.
   - `ursb_register_business`: Register new business name / company.
   - `ursb_compliance_status`: Check annual returns and Form 20 director records for URA TIN readiness.

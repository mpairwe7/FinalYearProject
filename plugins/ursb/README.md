# URSB Plugin & Connector

This folder provides a local URSB sample simulator. It does not connect to or update the official URSB register; its entities and registration references are fixtures.

## System Capabilities

1. **`models.py`**:
   - `BusinessEntity`, `DirectorInfo`: Corporate particulars, registered office, Form 20 directors, annual return status.
   - `BusinessSearchRequest`, `BusinessSearchResponse`: Searching local sample business names or BRN-shaped values.
   - `ComplianceCheckResponse`: Local example status; it cannot verify legal standing or TIN eligibility.

2. **`database.py` (`UrsbDatabase`)**:
   - Independent SQLite database (`data_store/ursb_system.db`).
   - Pre-seeded with test fixtures. Do not treat these entries as registry records.

3. **`service.py` (`UrsbService`)**:
   - Simulator handling fixture searches, sample registration references, and illustrative compliance fields.

4. **`connector.py` (`UrsbConnector`)**:
   - `ursb_verify_business`: Search local business fixtures.
   - `ursb_register_business`: Create a local simulator record; no real business is registered.
   - `ursb_compliance_status`: Read local example data; it cannot verify statutory filings or readiness.

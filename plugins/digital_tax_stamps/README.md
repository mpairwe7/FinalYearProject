# Digital Tax Stamps (DTS) Plugin & Connector

This folder provides a local DTS sample simulator. It does not contact URA or authenticate physical stamps. Product categories, legal references, tariffs, and sample locations below are fixture examples; verify current requirements and rates with official URA sources before relying on them.

## Gazetted Commodities Supported

The simulator includes example data for these product categories and amounts (not current legal or tariff guidance):
1. Beer and malt beverages (UGX 35/unit)
2. Spirits and liquor (UGX 110/unit)
3. Wine and ready-to-drink alcoholic beverages (UGX 100/unit)
4. Bottled mineral drinking water (UGX 15/unit)
5. Soda and carbonated non-alcoholic drinks (UGX 30/unit)
6. Tobacco and cigarettes (UGX 50/unit)
7. Cement (UGX 135/bag)
8. Sugar (UGX 35/bag)
9. Cooking oil and vegetable fats (UGX 30/unit)
10. Fruit and vegetable juices (UGX 30/unit)

## System Components

1. **`models.py`**:
   - `StampVerificationRequest`, `StampVerificationResponse`: simulator lookups (`GENUINE`, `EXPIRED`, `COUNTERFEIT`, `UNACTIVATED`, or `UNKNOWN`). `UNKNOWN` means the simulator has no matching fixture; it does not determine the authenticity of a physical stamp.
   - `StampOrderRequest`, `StampOrderResponse`: Requisitioning stamps, automatic fee calculation, PRN generation, collection scheduling at SICPA Uganda (Henley Business Park, Ntinda Industrial Area).
   - `StampActivationRequest`, `StampActivationResponse`: Production line controller (PLC) batch commissioning.
   - `DamagedStampDeclarationRequest`, `DamagedStampDeclarationResponse`: Packaging line jam spoiled stamp declaration and credit reconciliation.
   - `TaxpayerDtsProfile`: Packaging line records, applicator types, and compliance tracking.

2. **`service.py` (`DigitalTaxStampsService`)**:
   - Stateful local simulator with sample records:
     - Pre-seeded genuine product database (Nile Breweries, Kakira Sugar, Rwenzori Bottling, Tororo Cement).
     - Statutory tariff calculations and 10-digit PRN generator.
     - Line controller activation transitions.
     - Damaged stamp ledger and credit allowance computations.

3. **`client.py` (`DigitalTaxStampsClient`)**:
   - Client SDK for local simulator interactions; remote DTS API calls are not implemented.

4. **`connector.py` (`DigitalTaxStampsConnector`)**:
   - Agentic connector that wraps DTS capabilities into tools conforming to `Tool` and `ToolSchema`:
     - `dts_verify_stamp`: Check sample stamp fixtures locally; no physical stamp is authenticated.
     - `dts_order_stamps`: Order stamps with fee and PRN generation.
     - `dts_activate_stamps`: Activate stamps or declare spoiled stamps.
     - `dts_taxpayer_status`: Inspect manufacturer packaging lines and compliance standing.

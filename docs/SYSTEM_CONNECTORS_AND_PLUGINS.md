# URA Enterprise Systems, Connectors & Autonomous Agentic Architecture

**Version**: 2026.1  
**Scope**: 6 Independent Enterprise Systems, Grok-inspired Agent Connectors, URA Security CAPTCHA Gates, and Autonomous Vision/OCR Agent Workflows.

---

## 1. Executive Summary

This architecture implements six production-grade, independent enterprise systems reflecting the digital infrastructure of the **Uganda Revenue Authority (URA)** and partner statutory bodies (**Uganda Registration Services Bureau - URSB**).

The systems operate both as **standalone cloud services** (with dedicated web dashboards and authentication gates) and as **pluggable agent connectors** integrated into the URA Taxpayer Chatbot and Autonomous Agent runtime.

```
                      +-----------------------------------+
                      |   URA AI Chatbot & Agent Core     |
                      |  (FastAPI / Supervisor / MCP)    |
                      +-----------------+-----------------+
                                        |
       +--------------------------------+-------------------------------+
       |                 Dynamic Plugin Orchestrator                   |
       |                   (23 Registered Tools)                       |
       +-------+------------+------------+-----------+---------+-------+
               |            |            |           |         |
               v            v            v           v         v
            [EFRIS]       [DTS]        [URSB]     [BWIMS]    [TIN] & [PAYMENTS]
          efris_system  dts_system   ursb_system bwims_system tin_system  payments_system
              .db          .db          .db         .db       .db          .db
```

---

## 2. Core Architectural Pillars

### 2.1 Independent Data Isolation
Each enterprise system possesses its own isolated SQLite relational database under `data_store/`:
- `data_store/efris_system.db`: Electronic fiscal devices, invoices, credit notes, stock items, VAT returns.
- `data_store/dts_system.db`: Excisable manufacturers, packaging lines, tax stamp orders, Kakasa stamp verifications.
- `data_store/ursb_system.db`: Business name reservations, incorporated companies, Form 20 directors, annual return compliance.
- `data_store/bwims_system.db`: Bonded warehouse facilities, customs officers, IM7 bonded consignments, ex-warehouse releases.
- `data_store/tin_system.db`: Individual citizens (NIN validation), corporate entities, registered tax heads, issued TINs.
- `data_store/payments_system.db`: 12-digit Payment Registration Numbers (PRNs), bank/MoMo transactions, advance tax assessments.

### 2.2 Security Authentication Gate & URA CAPTCHA
All standalone web portals enforce a mandatory **Auth Gate**:
- Unauthenticated users cannot view portal metrics, database tables, or trigger operations.
- **URA Security CAPTCHA**:
  - Dynamically generated 5-character alphanumeric token rendered on an HTML5 canvas.
  - Distorted with Bezier noise curves, random character rotation, and background interference dots.
  - **Strict Server-Side Validation**: `POST /api/v1/auth/login` and `POST /api/v1/auth/signup` require a valid CAPTCHA token; invalid attempts fail with HTTP 400.
  - **1-Click Quick Demo Presets**: Pre-seeded corporate and individual accounts allow one-click testing while maintaining full server validation.

---

## 3. The Six Enterprise Systems

### 3.1 EFRIS (Electronic Fiscal Receipting & Invoicing System)
- **Primary Function**: Real-time fiscal invoice issuance, B2B/B2C transactions, VAT declaration.
- **Key Features**:
  - Generates verifiable 20-digit Fiscal Document Numbers (FDN) with SHA256 fiscal verification codes and QR codes.
  - Credit note issuance with statutory VAT adjustment validation.
  - Real-time stock decrement upon invoice generation.
  - Automatic reconciliation for **Monthly VAT Return (Form DT-1014)**.
- **Agent Tools**: `efris_issue_invoice`, `efris_verify_fdn`, `efris_issue_credit_note`, `efris_get_stock`.

### 3.2 Digital Tax Stamps (DTS / Kakasa)
- **Primary Function**: Track-and-trace system for 9 gazetted excisable commodities (beer, spirits, wine, bottled water, soda, tobacco, cement, sugar, cooking oil).
- **Key Features**:
  - **Kakasa Stamp Scanner**: Validates stamp security codes as `GENUINE`, `EXPIRED`, or `COUNTERFEIT_ALERT`.
  - Requisition ordering with automatic PRN generation (e.g. UGX 110/stamp for spirits, UGX 15/stamp for water).
  - Production line controller activation batching.
  - Spoiled and damaged stamp return reconciliation.
- **Agent Tools**: `dts_verify_stamp`, `dts_order_stamps`, `dts_activate_line_controller`, `dts_report_damaged_stamps`.

### 3.3 URSB (Uganda Registration Services Bureau)
- **Primary Function**: Official registry for business names, partnerships, and limited liability companies.
- **Key Features**:
  - Name search and reservation against existing entity collisions.
  - Full company incorporation issuing official registration numbers (`URSB-CO-XXXXX`).
  - Company Form 20 management (directors and corporate secretaries).
  - Annual return compliance verification as a strict prerequisite for URA Non-Individual TIN registration.
- **Agent Tools**: `ursb_search_business`, `ursb_reserve_name`, `ursb_incorporate_company`, `ursb_get_directors`, `ursb_verify_compliance`.

### 3.4 BWIMS (Bonded Warehouse Information Management System)
- **Primary Function**: Customs control of bonded warehouses and duty-deferred imported cargo.
- **Key Features**:
  - IM7 customs entry tracking for imported goods held in bonded warehousing.
  - **EACCMA Section 67 Overstay Alerts**: Automated statutory alert when goods exceed the statutory 9-month (270-day) warehousing limit, flagging them for public customs auction.
  - Ex-warehouse release processing (IM4 Home Consumption or IM8 Transit) verifying duty settlement.
- **Agent Tools**: `bwims_track_consignment`, `bwims_check_overstay_alerts`, `bwims_release_cargo`, `bwims_get_warehouse_inventory`.

### 3.5 TIN Registration System
- **Primary Function**: Issuance of 10-digit Taxpayer Identification Numbers.
- **Key Features**:
  - **Instant Individual TIN**: Validates 14-character Ugandan National Identification Numbers (NIN) against simulated NIRA identity registries.
  - **Non-Individual TIN**: Validates business incorporation numbers against URSB before tax registration.
  - Registration of statutory tax heads (Income Tax, VAT, PAYE, Local Excise Duty, Withholding Tax).
- **Agent Tools**: `tin_apply_individual`, `tin_apply_non_individual`, `tin_verify_status`, `tin_add_tax_head`.

### 3.6 Payment System (PRN & Assessment Suite)
- **Primary Function**: Official URA e-Tax payment gateway and reconciliation engine.
- **Key Features**:
  - Generates official 12-digit Payment Registration Numbers (PRNs) with valid search codes.
  - 21-day statutory PRN expiry check and automated renewal.
  - Multi-channel instant checkout supporting VISA, MasterCard, MTN Mobile Money, and Airtel Money.
  - Motor Vehicle Advance Income Tax calculator (Section 118A Income Tax Act).
- **Agent Tools**: `payments_generate_prn`, `payments_check_status`, `payments_renew_prn`, `payments_calculate_advance_tax`, `payments_process_checkout`.

---

## 4. Autonomous Agent Workflows

### 4.1 Autonomous National ID OCR to Instant TIN
When a taxpayer uploads or snaps a photo of a Ugandan National ID card in the chat interface:
1. **Vision Classification**: Identified as `national_id` by `App/backend/app/vision/document_classifier.py`.
2. **Entity Extraction**: `App/backend/app/vision/ocr.py` runs regex-based heuristic extraction to identify:
   - 14-character Ugandan NIN (e.g. `CM950019284KLA`).
   - Taxpayer legal name and date of birth.
   - Contact mobile number (e.g. `+256 772 123456`).
3. **Autonomous Execution**: `App/backend/app/service.py` autonomously invokes `tin_apply_individual` via the TIN connector without requiring the user to fill forms.
4. **Interactive Response**: The chat assistant returns the newly issued 10-digit TIN, taxpayer category, and next steps for e-tax login.

---

## 5. UI/UX & Connector Integrations

### 5.1 Grok-Inspired Chat Composer (`+` Add Button)
- Replaced basic attachment button with a modern `+` Add Icon (`PlusIcon`, `.composer-add-btn`).
- Interactive popover offering:
  - 📄 **Upload a file**: PDF, Word, Excel, CSV, or Image.
  - 📷 **Take a photo**: Direct camera viewfinder capture (`CameraCapture.tsx`).
  - ⚡ **Add connector**: Opens the Connectors Modal.
- **Active Connector Chips Bar**:
  - Displays currently enabled enterprise systems directly above the input box.
  - Pulsing emerald indicator with quick-disconnect (`×`) control.

### 5.2 Connectors Modal (`ConnectorsModal.tsx`)
- Tabbed management interface:
  - **Available Connectors**: Toggle systems on/off, inspect active tools, view live stats.
  - **Database Inspector**: Live interactive viewer for SQLite tables in `data_store/`.
  - **External Connectors**: Register custom remote REST APIs or MCP 2026 servers on the fly via `POST /v1/connectors/register`.

---

## 6. Live Deployments

### 6.1 Crane Cloud (RENU Infrastructure)
- **URA EFRIS**: `https://ura-efris-67a9f3c3.renu-01.cranecloud.io`
- **URA DTS (Kakasa)**: `https://ura-dts-f1af1c08.renu-01.cranecloud.io`
- **URSB Registry**: `https://ura-ursb-7e854617.renu-01.cranecloud.io`
- **URA BWIMS**: `https://ura-bwims-d16293b1.renu-01.cranecloud.io`
- **URA Central Gateway**: `https://ura-plugins-gateway-6f178237.renu-01.cranecloud.io`

### 6.2 Hugging Face Spaces (`landwind22`)
- `https://huggingface.co/spaces/landwind22/ura-efris`
- `https://huggingface.co/spaces/landwind22/ura-digital-tax-stamps`
- `https://huggingface.co/spaces/landwind22/ura-ursb`
- `https://huggingface.co/spaces/landwind22/ura-bwims`
- `https://huggingface.co/spaces/landwind22/ura-tin-payments-gateway`

---

## 7. Operational Runbook

### 7.1 Running All Systems Locally (Development)
```bash
# Run standalone gateway hosting all 6 systems
PYTHONPATH=. python3 plugins/server.py --port 8005

# Or run individual systems:
PYTHONPATH=. python3 plugins/apps/efris_app.py --port 8006
PYTHONPATH=. python3 plugins/apps/dts_app.py --port 8007
PYTHONPATH=. python3 plugins/apps/ursb_app.py --port 8008
PYTHONPATH=. python3 plugins/apps/bwims_app.py --port 8009
```

### 7.2 Running via Docker Compose
```bash
docker compose -f docker-compose.plugins.yml up --build -d
```

### 7.3 Verification Commands
```bash
# Test backend orchestrator and autonomous agent flows
PYTHONPATH=App/backend python3 -m pytest tests/agents/test_plugins_orchestrator.py tests/agents/test_autonomous_agent_tin.py -q

# Test all 93 backend API endpoints
PYTHONPATH=App/backend python3 -m pytest tests/test_all_endpoints_e2e.py -q

# Test frontend
cd App/frontend && bun run test && bun run lint
```

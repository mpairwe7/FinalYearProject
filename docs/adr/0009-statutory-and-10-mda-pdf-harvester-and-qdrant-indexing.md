# ADR-0009: Statutory Portal (`parliament.go.ug`, `ura.go.ug`) & 10-MDA PDF Harvester, Structure-Aware Statutory Chunking & Qdrant Hybrid Indexing

## Status

Accepted — 2026-10-22

## Context and Problem Statement

Taxpayers frequently overwhelm URA Customer Experience (CXP) (`0800 117 000` / `0800 217 000`) with inquiries originating from two statutory/inter-agency gaps:

1. **Multi-Portal Statutory Drift (`parliament.go.ug` & `ura.go.ug/download-category/laws-and-acts/`)**:
   - The 2026 Parliamentary Tax Amendment Bills are published on Drupal 10 at `https://www.parliament.go.ug/page/tax-bills-2026` inside `div.node__content`, while consolidated Acts, Regulations, and EAC Customs compendiums are published on WordPress Download Manager (`wpdm`) at `https://ura.go.ug/download-category/laws-and-acts/` across paginated views (`?cp=2`) using `<a data-downloadurl="...?wpdmdl=...">` attributes.
   - Previously, `publications.py` accepted only a single URL (`URA_PUBLICATIONS_URL`), `crawler.py` stripped query strings (`?wpdmdl=...`, `?cp=...`) and lacked `data-downloadurl` discovery, and `chunkers.py` did not promote plain-text statutory `PART` / `MEMORANDUM` / `SCHEDULE` / `Section` / `Clause` headings into `heading_trail` breadcrumbs when extracted via permissive engines (`pypdfium2`).
2. **10 High-Dependency Ugandan Government Bodies (MDAs) Driving URA CXP Inquiry Spikes**:
   - Because URA acts as the centralized **Non-Tax Revenue (NTR / ONTR) 13-digit PRN payment gateway**, the **mandatory TIN & Tax Clearance Certificate (TCC) gatekeeper** (Tax Procedures Code Act s.135), and the lead **Uganda Electronic Single Window (UESW / ASYCUDA World)** agency, citizens and businesses flood URA CXP whenever services stall across **10 primary MDAs**:
     1. **MoWT** (UDLS Driving Permits & Motor Vehicle Registry)
     2. **Ministry of Internal Affairs / DCIC** (e-Passports, Work Permits, Special/Dependant Passes & Immigration Fees)
     3. **NIRA** (National ID NIN-to-TIN Verification, ID Replacement/Renewal & Civil Registration Fees)
     4. **PPDA (e-GP) & MoFPED (IFMS)** (Public Procurement TCC Verification, EFRIS FDN Supplier Invoicing & 6% WHT)
     5. **UNBS** (E-Portal & PVoC Import Clearance / ASYCUDA Holds)
     6. **MLHUD** (UgNLIS Land Title Registration, CGV Valuation & 1% Stamp Duty PRNs)
     7. **KCCA (`e-Citie`) & Local Governments (`IRAS` / MoLG)** (Trading Licenses, Local Revenue & Presumptive Tax)
     8. **Uganda Police Force (UPF)** (Traffic Express Penalty Scheme EPS PRNs & Vehicle Transfer Blocks)
     9. **URSB (`OBRS`)** (Company Incorporation, Business Names, Annual Returns & Statutory Fees)
     10. **NDA, NEMA, MAAIF & UCDA (`UESW`)** (Single Window Import/Export Verification Permits & NEMA 30%/70% EIA PRN Generation on the URA Portal)

## Decision Outcome

We implemented a unified, October 2026 standard **Statutory & 10-MDA PDF Harvester, Structure-Aware Statutory Chunker, and Staged Qdrant Hybrid Indexer**:

1. **Multi-Source Portal & 10-MDA Registry (`App/backend/app/pdf_corpus.py`, `App/backend/app/publications.py`, `App/backend/app/production_readiness.py`)**:
   - Defined `DEFAULT_STATUTORY_PDF_SOURCE_URLS` (`https://www.parliament.go.ug/page/tax-bills-2026`, `https://ura.go.ug/download-category/laws-and-acts/`) and `MDA_STATUTORY_PDF_REGISTRY` (15 authoritative Acts, Fee Regulations, and URA PRN Technical Guides across all 10 MDA groups).
   - Added `parse_pdf_source_urls()`, `discover_statutory_pdf_links()`, `canonical_statutory_pdf_filename()`, and `harvest_remote_pdfs()` supporting comma-separated HTTPS portal URLs, Drupal `div.node__content` isolation, WPDM `data-downloadurl` (`?wpdmdl=`) resolution, `?cp=` pagination traversal, pre-stream header/size deduplication, SHA-256 content-addressable deduplication, `inspect_pdf_bytes(..., CORPUS_PDF_LIMITS)` structural security guards, and `malware_scan.scan_bytes()`.
2. **Structure-Aware Statutory Heading Extraction & Handle Cleanup (`ml/scripts/data_aug/chunkers.py`)**:
   - Added `_inject_statutory_headings()` promoting plain-text statutory `PART` / `CHAPTER` / `MEMORANDUM` / `SCHEDULE` (`#` level 1) and `SECTION` / `CLAUSE` / numbered statutory clauses (`##` level 2) into hierarchical `heading_trail` breadcrumbs and Anthropic Contextual Retrieval prefixes (`[Document: ... — PART I > 1. Commencement]`).
   - Added explicit `finally:` cleanup (`textpage.close()`, `page.close()`, `pdf.close()`) in `extract_markdown()` to prevent native `pypdfium2` file-descriptor leaks.
3. **Self-Updating Qdrant Origin Allowlist & Indexer CLI (`App/backend/app/indexer.py`, `App/backend/app/web_research.py`, `App/backend/app/corpus_sync.py`)**:
   - Extended `ALLOWED_STATUTORY_PDF_HOSTS`, `TIER1_DOMAINS`, and `QDRANT_SYNC_ALLOWED_HOSTS` to include `parliament.go.ug`, `bills.parliament.ug`, `media.ulii.org`, and the 10 Ugandan MDA domains (`works.go.ug`, `immigration.go.ug`, `nira.go.ug`, `ppda.go.ug`, `finance.go.ug`, `unbs.go.ug`, `mlhud.go.ug`, `kcca.go.ug`, `upf.go.ug`, `ursb.go.ug`, `nda.or.ug`, `nema.go.ug`).
   - Added `--download-pdfs`, `--no-mda-sources`, and `--pdf-source-urls` to `python -m app.indexer`.
4. **Five-Pillar Cross-MDA Resolution & Tax-Base Expansion Suite (`App/backend/app/tools/mda_interdependence.py`, `App/backend/app/scenario_planner.py`, `App/backend/app/mcp/tool_rag.py`, `App/backend/app/workflows/flows/mda_ntr_resolution.yaml`)**:
   - **Pillar 1 (`resolve_mda_ntr_payment`)**: Disambiguates NTR vs. ONTR vs. Stamp Duty across all 10 MDAs, diagnoses 3-hop PRN settlement (`Bank/Mobile Money -> URA e-Tax -> MDA Webhook`), computes NEMA `30%`/`70%` split EIA PRNs, and provides `Form DT-3008` NTR refund steps.
   - **Pillar 2 (`assess_life_event_tax_onboarding`)**: Maps citizen/MDA life events (`KCCA/IRAS` trading licenses, `MoWT` commercial vehicles, `MLHUD` land transfers, `URSB` incorporation) to mandatory TPC Act s.135 TIN requirements, discloses the `0%` (`UGX 0`) Presumptive Tax band for turnover $\le$ `UGX 10,000,000`, and runs a `NIRA` NIN-to-TIN pre-flight Surname/Given-Name order check.
   - **Pillar 3 (`evaluate_b2g_and_uesw_clearance`)**: Evaluates `PPDA e-GP` TCC readiness, mandatory `EFRIS` FDN e-invoicing, `MoFPED IFMS` `6%` WHT above `UGX 1,000,000` (and Deemed VAT under VAT Act s.24(7)), plus `UNBS` PVoC (`USD 2,000` FOB threshold & `15%` CIF surcharge) and `NDA`/`NEMA` `UESW` holds.
   - **Pillar 4, 5 & 6 (Queue Elimination & New-Taxpayer Onboarding)**: Added `build_new_taxpayer_compliance_shield` (Day-1 Nil-Return Shield, FY2026-27 `UGX 300M` VAT threshold downgrade advisor & dormancy guide), `simulate_tax_relief_and_instalments` (`TPC Act s.66(1a)` 100% Voluntary Disclosure penalty/interest waiver & `s.40` TCC-unlocking instalment simulator), and `resolve_efris_and_account_recovery` (Zero-cost EFRIS channel matcher, FY2026-27 `UGX 2,000,000` `5%` consumer e-receipt VAT refund calculator & lost-SIM/email TIN OTP recovery), wired into `scenario_planner.py`, 5-locale `ToolRAGSelector`, and guided workflows `mda_ntr_resolution.yaml` and `new_taxpayer_and_relief.yaml`.
5. **Cross-MDA TTS Pronunciation Normalization & ASR Entity Repair (`App/backend/app/speech_normalization.py`, `App/frontend/src/lib/answerText.ts`, `App/backend/app/receptionist/lexicon.py`, `App/backend/app/query.py`)**:
   - Normalizes letter-by-letter initialisms (`kcca`/`KCCA` $\rightarrow$ `K C C A`, `ursb`/`URSB` $\rightarrow$ `U R S B`, `unbs`/`UNBS` $\rightarrow$ `U N B S`, `DCIC` $\rightarrow$ `D C I C`, `UDLS` $\rightarrow$ `U D L S`, `MLHUD` $\rightarrow$ `M L H U D`, `UgNLIS` $\rightarrow$ `U G N L I S`, `UPF` $\rightarrow$ `U P F`, `NDA` $\rightarrow$ `N D A`, `PPDA` $\rightarrow$ `P P D A`, `IFMS` $\rightarrow$ `I F M S`, `OBRS` $\rightarrow$ `O B R S`, `UESW` $\rightarrow$ `U E S W`, `NTR` $\rightarrow$ `N T R`, `ONTR` $\rightarrow$ `O N T R`, `MoWT` $\rightarrow$ `M O W T`, `MoFPED` $\rightarrow$ `M O F P E D`, `PVoC` $\rightarrow$ `P V O C`, `e-GP` $\rightarrow$ `E G P`) vs. pronounceable word acronyms (`nema`/`NEMA` $\rightarrow$ `Nema`, `nira`/`NIRA` $\rightarrow$ `Nira`, `iras`/`IRAS` $\rightarrow$ `Iras`, `bwims`/`BWIMS` $\rightarrow$ `Bwims`, `comesa`/`COMESA` $\rightarrow$ `Comesa`, `TIN`/`tin` $\rightarrow$ `TIN`/`tin` never `T-I-N`, `NIN`/`nin` $\rightarrow$ `NIN`/`nin` never `N-I-N`).

### Harvested & Indexed Corpus Provenance Summary (October 2026)

| Source / MDA Code | Canonical PDF Filename (`Data/pdfs/`) | Upstream Official HTTPS Source | SHA-256 Prefix | Chunks |
| :--- | :--- | :--- | :---: | :---: |
| **Parliament (2026 Bills)** | `Traffic-and-Road-Safety-Amendment-Bill-2026-FY2026-27.pdf` *(+7 existing 2026 Tax Bills)* | `https://www.parliament.go.ug/page/tax-bills-2026` | `e058692495ca` | `4` |
| **URA (`laws-and-acts`)** | `COMESA-Protocol-on-Rules-of-Origin-2015-1-1-1.pdf` *(+18 existing Acts/Rules)* | `https://ura.go.ug/download-category/laws-and-acts/?cp=2` | `35d7e8ac5352` | `229` |
| **MDA 1 (`MoWT`)** | `MoWT-Traffic-and-Road-Safety-Act-Cap-361.pdf` | `https://media.ulii.org/media/legislation/18258/source_file/5b5c2f1ce136c1a3/1998-15.pdf` | `5a2ec4797fa5` | `102` |
| **MDA 2 (`DCIC`)** | `DCIC-Uganda-Citizenship-and-Immigration-Control-Act-Cap-313.pdf` | `https://media.ulii.org/media/legislation/18262/source_file/8e4f495a61b25d69/1999-3.pdf` | `a067cb8193a2` | `57` |
| **MDA 2 (`DCIC`)** | `DCIC-Uganda-Citizenship-and-Immigration-Control-Fees-Regulations-2021.pdf` | `https://media.ulii.org/media/legislation/107108/source_file/uganda-citizenship-and-immigration-control-fees-regulations-2021.pdf` | `936d86d651db` | `4` |
| **MDA 3 (`NIRA`)** | `NIRA-Summary-of-Registration-of-Persons-Fees-2024-FY2024-25.pdf` | `https://www.nira.go.ug/media/2024/10/SUMMARY-OF-THE-REGISTRATION-OF-PERSONS-FEES.pdf` | `39dffbc6aebc` | `2` |
| **MDA 3 (`NIRA`)** | `NIRA-Registration-of-Persons-Act-2015.pdf` | `https://media.ulii.org/media/legislation/17871/source_file/9715a09ebcdf6785/2015-4.pdf` | `1ccb7c6ab0bd` | `45` |
| **MDA 4 (`PPDA/MoFPED`)** | `PPDA-Public-Procurement-and-Disposal-of-Public-Assets-Act.pdf` | `https://media.ulii.org/media/legislation/18276/source_file/40a9202d41ec69ad/2003-1.pdf` | `d64c3a5b07a6` | `46` |
| **MDA 5 (`UNBS`)** | `UNBS-Uganda-National-Bureau-of-Standards-Act.pdf` | `https://media.ulii.org/media/legislation/73988/source_file/259e194e1d73cc25/1983-1.pdf` | `8258a9051780` | `20` |
| **MDA 6 (`MLHUD`)** | `MLHUD-Registration-of-Titles-Act-Cap-230.pdf` | `https://mlhud.go.ug/wp-content/uploads/2019/03/Registration-of-Titles-Act-Chapter_230.pdf` | `84b194570ec7` | `149` |
| **MDA 7 (`KCCA/MoLG`)** | `KCCA-Trade-Licensing-Act-Cap-79.pdf` | `https://media.ulii.org/media/legislation/18578/source_file/177d6b9459ffc6af/1969-14.pdf` | `a274a2571d30` | `17` |
| **MDA 7 (`KCCA/MoLG`)** | `MOLG-Local-Governments-Act-Cap-243.pdf` | `https://media.ulii.org/media/legislation/18245/source_file/5461811597227ca8/1997-5.pdf` | `49e99a7c4b60` | `140` |
| **MDA 8 (`UPF`)** | `UPF-Traffic-and-Road-Safety-Speed-of-Motor-Vehicles-Order-2026-FY2026-27.pdf` | `https://media.ulii.org/media/legislation/131751/source_file/f969ab7100677e30/ug-act-si-2026-50-publication-document.pdf` | `1c2cfec87e9b` | `1` |
| **MDA 8 (`UPF/MoWT`)** | `MoWT-UPF-Roads-Act-2019-Express-Penalty.pdf` | `https://media.ulii.org/media/legislation/18052/source_file/2019-16.pdf` | `afaa1af862ee` | `55` |
| **MDA 9 (`URSB`)** | `URSB-Companies-Act-2012.pdf` | `https://media.ulii.org/media/legislation/17888/source_file/7e53dcc2c056a04d/2012-1.pdf` | `e2ef686be57e` | `269` |
| **MDA 10 (`NDA/UESW`)** | `NDA-National-Drug-Policy-and-Authority-Act.pdf` | `https://media.ulii.org/media/legislation/121926/source_file/3d9e9752a5995226/1993-13.pdf` | `ee41b57e9f95` | `84` |
| **MDA 10 (`NEMA/URA`)** | `NEMA-Technical-Guide-URA-PRN-EIA-Fees.pdf` | `https://nema.go.ug/sites/all/themes/nema/docs/TECHNICAL%20GUIDE.pdf` | `5fe412f6c05f` | `2` |

## Consequences

- **Positive**: Grounds taxpayer inquiries across all 8 Parliamentary 2026 Tax Bills, all 19 URA Laws & Acts compendiums, and all 10 high-dependency MDAs in hash-verified, structure-aware JSONL chunks (`186` PDF sources, `175` unique, `5,639` PDF chunks; `7,251` total Qdrant hybrid vectors in `ura_knowledge_base_jsonl_active`, `source_corpus_hash: 7ec4cb064443...`).
- **Negative / Trade-offs**: Full Qdrant rebuild embedding grows from `5,600` to `7,251` points (`~90s` on a single RTX A6000 with `INDEX_BATCH_SIZE=256`).

## Confirmation

Validated continuously in CI by:
- `App/backend/tests/test_pdf_corpus.py` (`StatutoryPortalHarvestTests`)
- `App/backend/tests/test_mcp_2026_october_audit_and_corpus.py` (`Statutory2024To2027CorpusAndRatesTests`)
- `tests/agents/test_mda_interdependence_pillars.py`
- `tests/test_data_augmentation.py` (`TestChunkers.test_statutory_headings_injected_when_markdown_has_no_hash_headers`)
- `tests/test_monorepo_architecture_governance.py`

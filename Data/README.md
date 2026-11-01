# data/

Canonical datasets live in `Data/` (capital D) for historical CI paths.
Point new eval and crawl jobs at `Data/eval` and `Data/crawl_jsonl`.
`Data/crawl/pages/*.json` are content-addressed captures of URA's live site, and
`Data/crawl/crawl_state.json` is the ledger that says which capture each URL stands on,
when the site last confirmed it, and what each run did. Neither is edited by hand:
changes arrive as a reviewed pull request (ADR-0023, `docs/runbooks/crawl-operations.md`).
`Data/eval/prototype_seed.json` is the local demo fixture (`SEED_PROTOTYPE`, never production).

## Statutory & 10-MDA PDF Corpus Traceability (`Data/pdfs/` → `App/Data/pdf_jsonl/`)

`Data/pdfs/` tracks **186 official PDFs** (`175` unique after SHA-256 deduplication, exported into `5,639` hierarchical chunks in `App/Data/pdf_jsonl/pdf_corpus_manifest.json` and indexed into Qdrant `ura_knowledge_base_jsonl_active`):
- **Parliament of Uganda (`https://www.parliament.go.ug/page/tax-bills-2026`)**: All 8 FY2026-27 Tax Amendment Bills (`Income Tax`, `Value Added Tax`, `Excise Duty`, `Tax Procedures Code`, `Stamp Duty`, `Lotteries and Gaming`, `External Trade`, and `Traffic and Road Safety`).
- **URA Laws, Acts & Regulations (`https://ura.go.ug/download-category/laws-and-acts/` + `?cp=2`)**: All 19 consolidated Domestic Tax & Customs Acts, Regulations, EAC CET, and COMESA Rules of Origin compendiums.
- **10 High-Dependency Ugandan MDAs (`MDA_STATUTORY_PDF_REGISTRY` in `App/backend/app/pdf_corpus.py`; see `docs/adr/0009-statutory-and-10-mda-pdf-harvester-and-qdrant-indexing.md`)**:
  1. `MoWT` (`MoWT-Traffic-and-Road-Safety-Act-Cap-361.pdf`)
  2. `DCIC` (`DCIC-Uganda-Citizenship-and-Immigration-Control-Act-Cap-313.pdf`, `DCIC-Uganda-Citizenship-and-Immigration-Control-Fees-Regulations-2021.pdf`)
  3. `NIRA` (`NIRA-Summary-of-Registration-of-Persons-Fees-2024-FY2024-25.pdf`, `NIRA-Registration-of-Persons-Act-2015.pdf`)
  4. `PPDA / MoFPED` (`PPDA-Public-Procurement-and-Disposal-of-Public-Assets-Act.pdf`)
  5. `UNBS` (`UNBS-Uganda-National-Bureau-of-Standards-Act.pdf`, `UNBS-E-Portal-ASYCUDA-and-Compulsory-Standards-Notice-FY2026-27.pdf`, `UNBS-PVoC-Import-Clearance-Scheme-FAQs-FY2025-26.pdf`)
  6. `MLHUD` (`MLHUD-Registration-of-Titles-Act-Cap-230.pdf`)
  7. `KCCA & MoLG` (`KCCA-Trade-Licensing-Act-Cap-79.pdf`, `MOLG-Local-Governments-Act-Cap-243.pdf`)
  8. `UPF` (`UPF-Traffic-and-Road-Safety-Speed-of-Motor-Vehicles-Order-2026-FY2026-27.pdf`, `MoWT-UPF-Roads-Act-2019-Express-Penalty.pdf`)
  9. `URSB` (`URSB-Companies-Act-2012.pdf`, `URSB-Business-Names-Registration-Amendment-Rules-2024-FY2024-25.pdf`, `URSB-Trademarks-Schedule-Fees-2025-FY2025-26.pdf`, `URSB-Client-Charter-2025-2030-FY2025-26.pdf`)
  10. `NDA / NEMA / UESW` (`NDA-National-Drug-Policy-and-Authority-Act.pdf`, `NEMA-Technical-Guide-URA-PRN-EIA-Fees.pdf`)


# Week 5 – SBOM, License Obligation Matrix, IP Ownership & Data Provenance

**Project**: MLOps Pipeline for URA Chat Bot
**Date**: February 2026
**Standard Alignment**: SPDX 2.3, CycloneDX 1.6, CISA SBOM Minimum Elements, SLSA v1.2, ISO/IEC 42001:2023 §A.7.4

---

## 1. Software Bill of Materials (SBOM)

The full machine-readable SBOM is provided in CycloneDX 1.6 JSON format at:

**`/docs/capstone/sbom-cyclonedx.json`**

### 1.1 SBOM Summary

| Category | Package Count | Key Packages |
|----------|--------------|--------------|
| Python – Backend | 3 | FastAPI 0.111.0, Uvicorn 0.30.1, Pydantic 2.7.4 |
| Python – ML Pipeline | 8 | transformers, torch, datasets, peft, bitsandbytes, accelerate, sentencepiece, safetensors |
| Python – Dev/CI | 4 | ruff, black, mypy, pytest |
| JavaScript – Frontend | 4 | next 14.2.4, react 18.3.1, react-dom 18.3.1, zustand 4.5.2 |
| Infrastructure | 3 | Docker (base images), GitHub Actions, Docker Hub |
| **Total** | **22+** | |

### 1.2 CISA Minimum Elements Compliance

| Element | Status | Evidence |
|---------|--------|----------|
| Supplier name | Present | Package registry metadata (PyPI, npm) |
| Component name | Present | All packages named in SBOM |
| Version | Present | Pinned versions in requirements.txt and package.json |
| Unique identifier | Present | PURL format in CycloneDX |
| Dependency relationship | Present | Direct vs transitive marked |
| Author of SBOM | Present | "mpairweLandwind" |
| Timestamp | Present | ISO 8601 generation timestamp |

---

## 2. License Obligation Matrix

| # | Component | Version | License | Copyleft? | Patent Grant? | Attribution Required? | Compatibility with Apache-2.0 |
|---|-----------|---------|---------|-----------|---------------|----------------------|-------------------------------|
| 1 | FastAPI | 0.111.0 | MIT | No | No | Yes (in LICENSE) | Compatible |
| 2 | Uvicorn | 0.30.1 | BSD-3-Clause | No | No | Yes | Compatible |
| 3 | Pydantic | 2.7.4 | MIT | No | No | Yes | Compatible |
| 4 | Next.js | 14.2.4 | MIT | No | No | Yes | Compatible |
| 5 | React | 18.3.1 | MIT | No | No | Yes | Compatible |
| 6 | Zustand | 4.5.2 | MIT | No | No | Yes | Compatible |
| 7 | Transformers | ≥4.40 | Apache-2.0 | No | Yes | Yes | Same license |
| 8 | PyTorch | ≥2.2 | BSD-3-Clause | No | No | Yes | Compatible |
| 9 | Datasets | ≥2.18 | Apache-2.0 | No | Yes | Yes | Same license |
| 10 | PEFT | ≥0.10 | Apache-2.0 | No | Yes | Yes | Same license |
| 11 | Gemma-2-9B (base model) | 2 | Gemma Terms of Use | No | Limited | Yes (model card) | Requires Gemma license acceptance |
| 12 | Ruff | ≥0.4 | MIT | No | No | Yes | Compatible |
| 13 | Black | ≥24 | MIT | No | No | Yes | Compatible |
| 14 | Trivy | ≥0.50 | Apache-2.0 | No | Yes | Yes | Same license |
| 15 | Semgrep | ≥1.60 | LGPL-2.1 | Weak copyleft | No | Yes | Compatible (tool use, not linking) |

### License Compliance Actions

1. **Gemma-2-9B**: Accept Google's Gemma Terms of Use before downloading; include model card attribution in documentation.
2. **All MIT/BSD/Apache-2.0**: Include original LICENSE files in distribution; maintain NOTICE file.
3. **Semgrep (LGPL-2.1)**: Used as a standalone CLI tool in CI only; no linking – LGPL does not propagate.
4. **No GPL components**: The project intentionally avoids GPL-licensed runtime dependencies to maintain Apache-2.0 compatibility.

---

## 3. IP Ownership & Provenance Plan

### 3.1 IP Ownership Table

| Asset | Owner | License | Notes |
|-------|-------|---------|-------|
| Application source code (App/, ml/, tests/) | Mpairwe Landwind (student) | Apache-2.0 | Original work |
| URA FAQ data (Data/dataset/*.csv) | Uganda Revenue Authority | Public domain (government publication) | Scraped from ura.go.ug; publicly available |
| URA PDF documents (Data/pdfs/*.pdf) | Uganda Revenue Authority | Public domain (government publication) | Official URA handbooks and guides |
| Luganda corpus (Data/TTT/) | Various (see provenance) | Mixed – see below | Academic and community corpora |
| Gemma-2-9B base weights | Google DeepMind | Gemma Terms of Use | Requires license acceptance |
| Fine-tuned model weights | Mpairwe Landwind (derivative) | Apache-2.0 (code) + Gemma (weights) | Derivative of Gemma; Gemma terms apply to weights |
| Frontend design & assets | Mpairwe Landwind | Apache-2.0 | Original work |
| CI/CD pipeline configuration | Mpairwe Landwind | Apache-2.0 | Original work |

### 3.2 Data Provenance Table

| Dataset | Source | Date Collected | License / Permission | Integrity Check |
|---------|--------|---------------|---------------------|-----------------|
| ura_*_faqs.csv (45 files) | ura.go.ug official website | 2024–2025 | Public government information | SHA-256 checksums in DVC |
| URA PDF handbooks (47 files) | ura.go.ug downloads section | 2023–2026 | Public government publication | SHA-256 checksums |
| Luganda.csv | Academic corpus | 2024 | Academic use | Verified |
| Makerere Sentiment corpus | Makerere University NLP Lab | 2023 | CC-BY-4.0 | Downloaded from official source |
| WordProject Luganda-English | WordProject.org | 2024 | Free use with attribution | Verified |
| Common Voice audio (lg) | Mozilla Common Voice | 2024 | CC-0 | Downloaded from official Mozilla repository |
| teacher_qa.jsonl | Generated via LLM pipeline | 2025 | Apache-2.0 (generated content) | Pipeline-generated; reproducible |

### 3.3 Data Provenance Checklist

- [x] All training data sources documented with origin URL/reference
- [x] License/permission status verified for each dataset
- [x] No datasets with restrictive licenses (GPL, NC) used for model training
- [x] PII redaction applied to all FAQ and PDF data before training
- [x] Government data confirmed as public domain under Ugandan law
- [x] Academic datasets used under their stated licenses
- [x] Data versioning enabled (Git + DVC references)
- [x] SHA-256 integrity checksums generated for all data files
- [x] Gemma model license accepted and terms documented
- [x] SLSA provenance attestation configured in CI/CD pipeline

---

*This document is aligned with SPDX 2.3, CycloneDX 1.6, CISA SBOM Minimum Elements (2024), SLSA v1.2, and ISO/IEC 42001:2023 §A.7.4 (Data Management for AI).*

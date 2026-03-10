# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

The URA Chat Bot project takes security seriously. If you discover a security vulnerability, please report it responsibly.

### How to Report

1. **Do NOT** open a public GitHub issue for security vulnerabilities.
2. Email the maintainer at **mpairwe.landwind@students.mak.ac.ug** with:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact assessment
   - Suggested fix (if any)

### What to Expect

- **Acknowledgement**: Within 48 hours of your report.
- **Assessment**: We will evaluate the severity using CVSS v4.0 scoring.
- **Resolution**: Critical vulnerabilities will be patched within 7 days.
- **Disclosure**: We follow coordinated disclosure (90-day window).

## Security Measures in Place

### Application Security
- Input validation on all API endpoints (Pydantic models)
- Restricted CORS policy with explicit origin allowlisting
- Content-Security-Policy and HSTS headers on frontend and backend
- X-Request-ID header validation (log-injection prevention)
- DATA_DIR path-traversal guard on backend
- No secrets in source code (`.env.example` template only)

### Supply Chain Security
- SHA-pinned GitHub Actions (no mutable tags)
- Dependabot enabled for automated dependency updates
- Trivy container image scanning in CI/CD pipeline
- SBOM generation (CycloneDX format) for every release
- SLSA v1.2 build provenance attestation

### AI/ML-Specific Security (OWASP LLM Top 10 2025)
- **LLM01 – Prompt Injection**: `InputGuard` with 11 regex patterns; system-prompt isolation via Qwen chat template; passage delimiters (`<passage>` tags) to reduce indirect injection surface
- **LLM02 – Sensitive Information Disclosure**: `OutputGuard.redact_pii()` on all responses and before database storage; Uganda-specific PII patterns (TIN, NID, phone, passport); `STORE_RAW_PROMPTS=false` default; conversation history sanitized before feeding to LLM
- **LLM03 – Supply Chain Vulnerabilities**: Pinned dependency versions; Trivy container scanning; SBOM generation; SHA-256 data integrity checks
- **LLM04 – Data and Model Poisoning**: Data provenance tracking; CODEOWNERS review gates; quality gates; local inference (no external API calls)
- **LLM05 – Improper Output Handling**: `OutputGuard.sanitize()` strips `<script>`, HTML tags, suspicious markdown image links; applied per-token in SSE streaming path; CSP headers
- **LLM09 – Misinformation**: Runtime faithfulness scoring via `compute_faithfulness()`; grounding disclaimer appended when score < threshold; calibrated abstention when confidence too low; human escalation flagging
- **LLM10 – Unbounded Consumption**: Rate limiting via `slowapi` (configurable per-IP); bearer token auth on `/v1/index` endpoint; `MAX_INPUT_LENGTH` enforced; semantic cache reduces redundant LLM calls

### Infrastructure
- Non-root container execution
- Read-only filesystem where applicable
- Environment-based secret injection (never hardcoded)
- TLS enforced on all production endpoints

## Security-Related Configuration

See `.env.example` for all configurable security parameters. Never commit `.env` files.

## Dependency Management

This project uses:
- **Dependabot** for automated Python and JavaScript (Bun/npm) dependency updates
- **Trivy** for container vulnerability scanning
- **Semgrep** for static analysis (SAST)

## Compliance

This project aligns with:
- NIST SP 800-218 (Secure Software Development Framework)
- OWASP LLM Top 10 (2025 Edition)
- SLSA v1.2 Supply Chain Integrity
- ISO/IEC 42001:2023 AI Management System security controls

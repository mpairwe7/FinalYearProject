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

### Secret Scanning (Defence-in-Depth)

Four independent secret scanners run at both pre-commit (local) and CI (remote) stages:

| Scanner | Method | Coverage | Config File |
|---------|--------|----------|-------------|
| TruffleHog v3 | Verified credentials (contacts provider API) | 800+ detector types | `.trufflehog-exclude-paths.txt` |
| GitGuardian ggshield | ML-based pattern detection | 400+ secret types | `.gitguardian.yaml` |
| Gitleaks v8 | Regex + entropy + custom rules | 150+ rules + URA custom | `.gitleaks.toml` |
| detect-secrets (Yelp) | Entropy + baseline comparison | Generic high-entropy | `.secrets.baseline` |

- **Pre-commit hooks**: All 4 scanners run locally before every commit (setup: `bash scripts/setup-secret-scanning.sh`)
- **CI pipeline**: `.github/workflows/secret-scanning.yml` runs all scanners on push, PR, and weekly full-history scan
- **Summary gate**: All scanners must pass; ggshield gracefully skips on forks without API key
- **Custom rules** (`.gitleaks.toml`): Uganda PII (TIN, NIN, mobile), ML API keys (HuggingFace, Kaggle, OpenAI, Google AI, W&B), mobile secrets (Firebase, Android keystore)
- **detect-secrets baseline**: `.secrets.baseline` tracks known false-positives; audited in CI
- **Branch protection**: `no-commit-to-branch` hook blocks direct commits to `main`/`master`
- **File hygiene**: `detect-private-key` blocks `.pem`/`.key` files; `check-added-large-files` blocks files >500 KB

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

### Mobile App Security (Flutter / On-Device Inference)
- **On-device inference**: Gemma-2B GGUF model runs locally via MediaPipe — no data leaves the device
- **Platform channels**: Kotlin/Swift native bridge with input validation on both sides
- **No embedded secrets**: API URL injected via `--dart-define` at build time, never hardcoded
- **Android**: minSdk 31 (Android 12+), `android:usesCleartextTraffic="false"`, ProGuard/R8 code shrinking
- **iOS**: Minimum iOS 16, App Transport Security (ATS) enforced
- **Model integrity**: SHA-256 checksum in `manifest.json` validates GGUF model before loading
- **Offline-first**: Network errors trigger automatic fallback to on-device model — no unencrypted retry

### Infrastructure
- Non-root container execution
- Read-only filesystem where applicable
- Environment-based secret injection (never hardcoded)
- TLS enforced on all production endpoints

## Security-Related Configuration

See `.env.example` for all configurable security parameters. Never commit `.env` files.

## Dependency Management

This project uses:
- **Dependabot** for automated Python, JavaScript (Bun/npm), and Flutter (pub) dependency updates
- **Trivy** for container vulnerability scanning
- **Semgrep** for static analysis (SAST)
- **Pre-commit autoupdate** for keeping secret scanning hooks current (`pre-commit autoupdate`)

## Compliance

This project aligns with:
- NIST SP 800-218 (Secure Software Development Framework)
- OWASP LLM Top 10 (2025 Edition)
- SLSA v1.2 Supply Chain Integrity
- ISO/IEC 42001:2023 AI Management System security controls

# Privacy Impact Assessment (PIA)

**System**: URA Chatbot — AI Tax Assistant
**Version**: 1.1.0
**Date**: 2026-04-13
**Author**: Mpairwe Landwind
**Legal Framework**: Uganda National Data Protection and Privacy Act (NDPA) 2019, AU Data Policy Framework 2022

---

## 1. System Description

The URA Chatbot is an AI-powered customer-service assistant for the Uganda Revenue Authority. It answers taxpayer questions about tax policy, procedures, and compliance using Retrieval-Augmented Generation (RAG) over indexed URA publications.

### 1.1 Data Flows

| Stage | Data Collected | Stored | Duration | Legal Basis |
|-------|---------------|--------|----------|-------------|
| Chat input | User question text | Session memory only | Until browser/app session ends | Public interest (NDPA §8(1)(e)) |
| API logs | Request metadata (IP, timestamp, path) | Server logs | 7 days | Legitimate interest (service reliability) |
| Analytics events | Event type, locale, latency | Analytics DB | 365 days (anonymised) | Legitimate interest (service improvement) |
| Feedback | Rating (up/down), optional comment | Analytics DB | 90 days | Consent (explicit user action) |
| Voice (mobile) | Raw audio | **Never stored** — processed on-device | 0 seconds | Consent (per-purpose grant) |
| Voice transcript | Transcribed text | Only if voiceStoreTranscript consent granted | Session | Consent |

### 1.2 Data NOT Collected

- **No PII collection by design**: No user accounts, no login, no personal identifiers required
- **No cookies**: Session state uses in-memory or localStorage only
- **No audio upload**: ASR runs on-device via Whisper (sherpa_onnx)
- **No biometric data**: No voice biometrics, face recognition, or fingerprints
- **No location data**: Permissions-Policy disables geolocation API

---

## 2. Privacy Risks Identified

| # | Risk | Likelihood | Impact | Inherent Risk | Mitigation | Residual Risk |
|---|------|-----------|--------|---------------|------------|---------------|
| PR-1 | PII in training data | Low | High | Medium | PII redaction pipeline (UG TIN, NID, phone, email patterns) during data ingestion | Low |
| PR-2 | PII in model responses | Medium | High | High | OutputGuard.redact_pii() applied to every response before delivery | Low |
| PR-3 | Prompt injection to extract PII | Medium | High | High | InputGuard (11 injection patterns) + system prompt leakage detection | Low |
| PR-4 | Voice data retention | Low | High | Medium | On-device ASR (never uploaded); per-purpose consent with audit trail | Low |
| PR-5 | Analytics re-identification | Low | Medium | Low | No user IDs in analytics; session IDs are random UUIDs | Low |
| PR-6 | Server log exposure | Low | Medium | Medium | 7-day retention; logs exclude message content when STORE_RAW_PROMPTS=false | Low |
| PR-7 | Cross-border data transfer | Low | High | Medium | Sovereign hosting recommendation; no mandatory external API calls | Low |

---

## 3. NDPA 2019 Compliance Matrix

| NDPA Section | Requirement | Implementation | Status |
|-------------|-------------|----------------|--------|
| §3 | Lawful processing | Public interest basis for tax assistance (§8(1)(e)) | Done |
| §8 | Conditions for processing | Documented in Privacy Notice (week04) | Done |
| §13 | Rights of data subjects | Access, rectification, erasure via /v1/me/* endpoints | Done |
| §14 | Right to object | Consent withdrawal via /v1/me/consents/withdraw | Done |
| §19 | Data minimisation | Session-only storage, STORE_RAW_PROMPTS=false default | Done |
| §20 | Purpose limitation | Per-purpose consent model (personalization, analytics, etc.) | Done |
| §22 | Data security | TLS 1.3, PII redaction, input validation, rate limiting | Done |
| §25 | Breach notification | Incident response plan (week06) with 72-hour notification | Documented |
| §26 | Data Protection Officer | DPO contact in Privacy Notice | Done |
| §28 | Impact assessment | This PIA document | Done |

---

## 4. Data Protection by Design & Default (NDPA §19)

### 4.1 Privacy by Design

1. **Minimal collection**: Chat text only; no accounts, no persistent user IDs
2. **On-device processing**: Voice ASR and LLM inference run locally on mobile
3. **Client-side speech**: Browser Web Speech API; no audio sent to server
4. **Ephemeral sessions**: Chat history persists in client localStorage only
5. **PII pipeline**: Automated redaction at both ingestion and output stages

### 4.2 Privacy by Default

1. **STORE_RAW_PROMPTS=false**: Server never stores unredacted user prompts
2. **Consent defaults**: voiceImproveModel defaults to OFF (opt-in only)
3. **Analytics anonymisation**: No user identifiers in analytics events
4. **Short retention**: 7-day logs, 90-day feedback, session-only chat

---

## 5. Data Subject Rights Implementation

| Right | Endpoint | Implementation |
|-------|----------|----------------|
| Access | GET /v1/me/export | JSON export of all user data |
| Rectification | PUT /v1/me/profile | Edit profile fields |
| Erasure | DELETE /v1/me | Cascading delete with audit tombstone |
| Restriction | POST /v1/me/consents/withdraw | Per-purpose consent withdrawal |
| Objection | POST /v1/me/consents/withdraw | Withdraw any consent purpose |
| Portability | GET /v1/me/export | JSON format export |

---

## 6. Audit Trail

All privacy-relevant actions are logged to a hash-chained audit ledger:
- Consent grants and withdrawals
- Data exports and erasure requests
- Microphone access events (mobile)
- ASR/TTS processing events

The ledger uses SHA-256 chaining to prevent tampering (each row's hash depends on the previous row). Erasure creates a tombstone event that preserves chain integrity while removing personal data.

---

## 7. Recommendations

1. **Annual PIA review**: Re-assess after each major feature release
2. **DPO consultation**: Engage URA Data Protection Office before production deployment
3. **User testing**: Validate Privacy Notice comprehensibility with target users
4. **Breach drill**: Conduct tabletop exercise per incident response plan quarterly
5. **Sovereign hosting**: Deploy within Uganda to avoid cross-border data transfer concerns

---

## 8. Approval

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Developer | Mpairwe Landwind | 2026-04-13 | _Pending_ |
| Academic Supervisor | _TBD_ | _TBD_ | _Pending_ |
| Data Protection Officer | _TBD_ | _TBD_ | _Pending_ |

# Document/PDF evaluation traceability — 2026-07-21

## Scope and decision record

This record covers the document upload, OCR, PDF report/export, and local
evaluation work in `App/`. The required operating boundary is local-only:
document bytes, OCR, evaluation data, and report generation remain on the
workstation or its private Docker network. No Cloud Run or remote deployment
configuration was added or changed for this work.

Existing remote/deployment files are retained outside this scope. The local
profile instead overrides remote-capable application settings at runtime, so
normal deployment definitions remain traceable and unaffected.

## Phased change log

| Phase | Outcome | Primary implementation | Evidence |
| --- | --- | --- | --- |
| P0 — input safety | Reject malformed/oversized PDF, Office, image, and multipart inputs before expensive parsing. | `backend/app/main.py`, `backend/app/documents.py` | Source limit, chunked read, PDF signature, Office archive, and image-pixel tests in `backend/tests/test_documents.py`. |
| P1 — bounded OCR | Keep OCR in a health-checked local sidecar with request, concurrency, pixel, and time budgets. | `backend/app/ocr_service.py`, `backend/app/vision/ocr.py`, `backend/Dockerfile.ocr`, `docker-compose.ocr.yml` | Sidecar/service behaviour tests in `backend/tests/test_ocr_service.py` and `backend/tests/test_vision.py`. |
| P2 — provenance and access | Bind upload records to session/user and return source fingerprint, extraction/OCR status, pages, and bounded field evidence. | `backend/app/documents.py`, `backend/app/models.py`, `backend/app/main.py` | Ownership, expiry, validation, and provenance tests in `backend/tests/test_documents.py`. |
| P3 — report hardening | Bound report inputs/rates, preserve Unicode safely, and label heuristic classifier output correctly. | `backend/app/pdf_export.py`, `backend/app/models.py`, `backend/app/main.py` | `backend/tests/test_pdf_export_hardening.py`; direct local export probes. |
| P4 — local evaluation profile | Disable hosted inference/fallbacks and run API/OCR through local Docker networking only. | `docker-compose.local-documents.yml`, `docker-compose.ocr.yml`, `docs/local-document-evaluation.md` | Configuration review and YAML parse recorded below. |

## Local runtime configuration

Start the profile with:

```bash
docker compose -f docker-compose.yml -f docker-compose.local-documents.yml \
  -f docker-compose.ocr.yml --profile ocr up -d --build redis qdrant api ocr
```

The local override disables LLM generation, dense retrieval, speech, telemetry,
and every configured Cloudflare, Gemini, Sunbird, or host-vLLM path. OCR is
reachable to the API only at `http://ocr:8100`; its optional host port is bound
to `127.0.0.1`. `HF_HUB_OFFLINE=1` prevents model-hub retrieval during runtime.

The profile has a narrow purpose: document/PDF analysis and evaluation. It does
not claim an air-gapped image build; Docker images and EasyOCR model artefacts
must be pre-cached before operating without network access.

## Verification evidence

The following focused checks completed during this change set:

| Check | Result |
| --- | --- |
| `tests/test_documents.py` focused local suite, excluding its dependency-injected endpoint harness | 18 passed, 12 deselected |
| `tests/test_ocr_service.py tests/test_vision.py tests/test_pdf_export_hardening.py` | 37 passed |
| Python compilation of changed document/OCR/PDF modules | Passed |
| `git diff --check` | Passed |
| `docker compose ... config -q` for base + local document + OCR profiles | Accepted the merged profile without starting containers |
| Rendered Compose boundary audit | API, frontend, and OCR host ports resolve to `127.0.0.1`; LLM, vLLM, dense fallback, speech, Sunbird, Cloudflare fallback, and OTEL resolve to disabled/blank values |
| Direct local wrapper probes for Unicode conversation export, typed `Decimal` tax export, and max-size conversation export | Passed; max-size probe completed in about 959 ms |

Known verification limits are recorded rather than hidden:

- The repository's full in-process ASGI/TestClient path stalled in existing
  middleware setup before document-route dispatch, so it is not counted as an
  endpoint integration pass.
- EasyOCR model inference was not benchmarked here because the model artefact
  was not present in the local virtual environment. The sidecar readiness check
  is the required precondition for a manual scanned-document evaluation.
- No Nginx syntax check was run because Nginx is not installed in this local
  environment. That deployment configuration is outside this local-only
  profile.
- Docker Compose v2.28.1 emitted an exit-time plugin-socket panic when its
  rendered JSON was piped for the boundary audit, after returning the complete,
  valid configuration. The independent quiet Compose validation completed; no
  services were launched as part of this verification.

## Operator trace

For each manual evaluation, retain the following alongside the resulting PDF:

1. Input file SHA-256 and a non-sensitive sample identifier.
2. API response `document_id`, `provenance`, extraction warnings, and any
   field-evidence page references.
3. Sidecar `/ready` result and local profile command used.
4. Report SHA-256, UTC timestamp, and test-command output.

This creates a reproducible evidence chain without uploading the source
document or its extracted taxpayer data to a remote service.

# Local document and PDF evaluation

This profile runs document analysis, OCR, and PDF report evaluation on the
local machine only. It does not configure or call a hosted model, Cloud Run,
Crane Cloud, ngrok, Cloudflare Workers AI, Gemini, Sunbird, or a host vLLM
endpoint. Existing deployment files are deliberately outside this profile.

## Start the local stack

From `App/`, start only the dependencies needed for document/PDF work:

```bash
docker compose -f docker-compose.yml -f docker-compose.local-documents.yml \
  -f docker-compose.ocr.yml --profile ocr up -d --build redis qdrant api ocr
```

The API is loopback-bound at `http://127.0.0.1:8083` by default and the OCR
sidecar is deliberately loopback-bound at `http://127.0.0.1:8100`. Do not
start the `frontend` service for this focused evaluation flow. If it is started
as part of the whole profile, its `3032` port is also loopback-bound. Set
`LOCAL_DOCUMENT_API_PORT` or `LOCAL_DOCUMENT_FRONTEND_PORT` to resolve local
port conflicts.

The local override uses Compose's `!override` tag to replace (not append to)
the base port mappings. It requires Docker Compose v2.24.4 or newer.

Verify the local process and model readiness:

```bash
curl http://127.0.0.1:8083/health
curl http://127.0.0.1:8100/health
curl http://127.0.0.1:8100/ready
```

`/ready` can return `503` while EasyOCR is loading. The `ocr_models` Docker
volume retains its models after that initial load. For a fully disconnected
run, build the images and pre-populate that volume while connected; no runtime
request falls back to a remote provider.

## Configuration boundary

`docker-compose.local-documents.yml` takes precedence over values from the
base stack and its `env_file`. It sets the following local-only boundary:

| Capability | Local profile behaviour |
| --- | --- |
| Text generation | Disabled (`LLM_ENABLED=false`); the host vLLM URL is blank. |
| Retrieval | Qdrant/dense retrieval disabled; no Workers AI fallback. |
| OCR | API calls `http://ocr:8100` on the private Docker network. |
| Speech/translation | Disabled, with Sunbird and fallback providers blanked. |
| Cloud fallback | Cloudflare and Gemini credentials/flags blanked or disabled. |
| Telemetry | OpenTelemetry export disabled. |
| Documents/reports | Bounded local disk path under container `/tmp`; SQLite analytics. |

The profile is intentionally limited to the document/PDF evaluation surface.
It does not remove or modify any remote deployment configuration elsewhere in
the repository.

## Exercise the flow

The upload needs a session or authenticated identity. Use an opaque local
session value for a manual evaluation:

```bash
curl --fail-with-body \
  -H 'X-Session-ID: local-document-evaluation' \
  -F 'file=@/absolute/path/to/sample.pdf' \
  http://127.0.0.1:8083/v1/documents/analyze
```

Record the returned `document_id`, then retrieve the bounded PDF report with
the same session binding:

```bash
curl --fail-with-body \
  -H 'X-Session-ID: local-document-evaluation' \
  -o document-report.pdf \
  http://127.0.0.1:8083/v1/documents/DOCUMENT_ID/report
```

For scanned PDFs, confirm that the analysis provenance records `ocr_used`, an
OCR backend/status, processed page numbers, region count, and mean confidence.
For text-layer PDFs, OCR is normally skipped. Classification scores are keyword
heuristics rather than calibrated probabilities; retain the provenance and
field evidence when recording an evaluation result.

## Repeatable checks

Run the focused automated checks from `App/backend`:

```bash
PYTHONPATH=. python -m pytest -q \
  tests/test_documents.py tests/test_ocr_service.py tests/test_vision.py \
  tests/test_pdf_export_hardening.py --timeout=30
```

The full in-process API test harness can be run separately when its local
middleware dependencies are available:

```bash
ANALYTICS_DB_DIR=/tmp/ura-document-evaluation \
  PYTHONPATH=. python -m pytest -q tests/test_api_endpoints.py --timeout=30
```

See [document-processing.md](document-processing.md) for processing controls,
[local-ocr.md](local-ocr.md) for sidecar details, and
[the traceability record](traceability/document-pdf-evaluation-2026-07-21.md)
for the implementation and verification history.

# Local OCR for document processing

The document pipeline always extracts a PDF text layer first.  Use this
optional sidecar only for scanned PDFs and image attachments.  It follows the
BP workflow-engine local pattern: OCR owns its model/runtime, exposes a small
HTTP contract and health check, and the API calls it through a bounded client.

## Docker Compose (recommended)

For the local document/PDF evaluation boundary, use the dedicated override:

```bash
docker compose -f docker-compose.yml -f docker-compose.local-documents.yml \
  -f docker-compose.ocr.yml --profile ocr up -d --build redis qdrant api ocr
```

This keeps OCR on the private Docker network and disables hosted inference and
fallbacks for the document workflow. See
[local-document-evaluation.md](local-document-evaluation.md) for its full
configuration boundary and evaluation procedure.

Verify liveness and model readiness on the host:

```bash
curl http://127.0.0.1:8100/health
curl http://127.0.0.1:8100/ready
```

`/health` only confirms the process is alive. `/ready` warms EasyOCR and
returns `503` until the model can load; Compose waits for that probe before it
starts the API override. The named `ocr_models` volume retains model downloads
across restarts. The default is CPU mode; set `OCR_GPU=true` only after adding
an appropriate GPU compose override for the host.

The API receives these settings through `docker-compose.ocr.yml`:

```text
OCR_BACKEND=service
OCR_SERVICE_URL=http://ocr:8100
OCR_SERVICE_TIMEOUT_SECONDS=6
OCR_SERVICE_MAX_CONCURRENT=2
OCR_INFERENCE_MAX_CONCURRENT=1
```

Use `OCR_HOST_PORT` to avoid a local port conflict.  The host binding is
loopback-only; do not publish this service through a public reverse proxy.

## Host-process development

In one terminal, install and start only the OCR runtime:

```bash
source ../.venv/bin/activate
pip install -r backend/requirements-ocr.txt
OCR_BACKEND=easyocr OCR_GPU=false uvicorn app.ocr_service:app --host 127.0.0.1 --port 8100
```

In a second terminal, point the API process at it:

```bash
OCR_BACKEND=service OCR_SERVICE_URL=http://127.0.0.1:8100 \
  uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Operating modes

| `OCR_BACKEND` | Behaviour |
| --- | --- |
| `service` | Use only the sidecar; report OCR as unavailable if it cannot respond. |
| `auto` | Use the sidecar when `OCR_SERVICE_URL` is set, then fall back to embedded EasyOCR. |
| `easyocr` | Use embedded EasyOCR in the API process for small one-process experiments. |
| `disabled` | Do not attempt OCR. |

The sidecar accepts one raw image per `POST /v1/ocr` request and returns text,
quadrilateral coordinates and per-region confidence.  OCR requests are capped
by `OCR_SERVICE_MAX_BYTES` and `OCR_SERVICE_MAX_PIXELS`; the API additionally
limits sidecar concurrency with `OCR_SERVICE_MAX_CONCURRENT`. Requests are
also bounded to six seconds by default; scanned-PDF processing has a separate
document-level budget and downscales oversized rendered pages before OCR.

## Validation

```bash
cd backend
PYTHONPATH=. python -m pytest -q tests/test_ocr_service.py tests/test_vision.py
```

For an end-to-end document check, submit a scanned receipt or tax form to
`POST /v1/documents/analyze`.  A successful scanned-PDF analysis includes
`"ocr_used": true` plus OCR backend/status, processed page numbers, region
count, and mean OCR score in `provenance`. A sidecar outage produces an
explicit `unavailable` status and extraction warning rather than pretending
that OCR succeeded.

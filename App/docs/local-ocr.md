# Triton-served PP-OCRv6 SOTA OCR & Document Recognition

The document pipeline extracts a clean PDF text layer and normalizes document inputs
first via the `app.document_normalization` adapter. For scanned PDFs, fine-print receipts,
complex financial tables, and hybrid embedded visual evidence, the system dispatches
to **Triton-served PP-OCRv6**.

It aligns directly with the **enterprise BP workflow-engine architecture**:
- **SOTA PP-OCRv6 Engine**: State-of-the-art recognition for dense tables, complex alphanumeric codes (TINs, PRNs, EFRIS invoice numbers, assessment refs), and fine-print thermal receipts.
- **Line-Level Bounding Polygons**: Returns precise 4-point quadrilaterals / multi-point polygon contours (`polygon`) alongside axis-aligned bounding boxes (`box`).
- **Low GPU Latency & Dynamic Batching**: Optimized Triton Inference Server KServe v2 protocol execution with concurrency limits and watchdog auto-reclamation.
- **Switchable Variants**: `OCR_MODEL_VARIANT=v6` (default SOTA) and `v5` (server fallback).

## Docker Compose

Run the Triton-served PP-OCRv6 profile:

```bash
docker compose -f docker-compose.yml -f docker-compose.local-documents.yml \
  -f docker-compose.ocr.yml --profile ocr up -d --build redis qdrant api ocr
```

Verify liveness and model readiness on the host:

```bash
curl http://127.0.0.1:8100/health
curl http://127.0.0.1:8100/ready
```

`/health` reports active engine, GPU readiness, and pixel limits. `/ready` warms the PP-OCRv6 model
pipeline and returns `200` once recognition weights are hot.

The API connects via internal container networking:

```text
OCR_BACKEND=service
OCR_SERVICE_URL=http://ocr:8100
OCR_SERVICE_TIMEOUT_SECONDS=6
OCR_SERVICE_MAX_CONCURRENT=4
DOCUMENT_OCR_PDF_PAGES=3
DOCUMENT_ENABLE_SPATIAL_OCR=false
```

## Operating Modes

| `OCR_BACKEND` | Description |
| :--- | :--- |
| `triton` / `service` | Use the dedicated Triton PP-OCRv6 sidecar process with low GPU latency and line-level polygons. |
| `auto` | Query Triton/sidecar when `OCR_SERVICE_URL` is configured; fall back gracefully to in-process PP-OCR. |
| `ppocrv6` / `paddleocr` | In-process PP-OCRv6 engine. |
| `easyocr` | Embedded lightweight CPU fallback. |
| `disabled` | Disable OCR for strict text-only processing. |

## Domain-Specific Alphanumeric Extraction

The OCR post-processor ([`app.vision.ocr`](../backend/app/vision/ocr.py)) provides specialized recognition for:
- **TINs**: 10-digit Uganda Taxpayer Identification Numbers (`extract_tin_numbers`).
- **PRNs**: Payment Registration Numbers starting with 2 (`extract_prn_numbers`).
- **EFRIS Fiscal Invoices**: Electronic Fiscal Receipt & Invoicing System references (`extract_efris_invoice_numbers`).
- **Assessment Numbers**: URA tax assessment identifiers (`extract_reference_numbers`).
- **Monetary Amounts**: Dense UGX / USD amounts across tabular assessment notices (`extract_ugx_amounts`).

## Validation

```bash
cd backend
PYTHONPATH=. python -m pytest -q tests/test_ocr_service.py tests/test_documents.py tests/test_pdf_guards.py tests/test_pdf_corpus.py
```

"""GPU/System PDF Document Processing & Pipeline Benchmark (2026).

Benchmarks rasterization, vector glyph extraction, ruling-line table structuring,
normalization manifest generation, and HITL routing on real URA statutory documents.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Target free GPU device 2
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "2")

import torch
from app import documents, hitl_routing
from app.document_normalization import normalize_document, build_normalization_manifest, validate_normalization_manifest
from app.vision import glyph_fusion, table_structuring


def run_benchmark():
    print("=" * 85)
    print("🚀 INITIALIZING PDF DOCUMENT PROCESSING & EXTRACTION PIPELINE BENCHMARK")
    print("=" * 85)

    # 1. Device Info
    cuda_available = torch.cuda.is_available()
    device_name = torch.cuda.get_device_name(0) if cuda_available else "CPU (High-Performance Vector Engine)"
    device_count = torch.cuda.device_count() if cuda_available else 0

    print(f"CUDA Available      : {cuda_available}")
    print(f"Target Compute Unit : {device_name}")
    print(f"Available Devices   : {device_count}")
    print("-" * 85)

    # 2. Select Real URA PDF Fixtures
    pdf_dir = Path("App/Data/pdfs")
    sample_files = [
        pdf_dir / "TAX-WAIVER-2025-26-1.pdf",
        pdf_dir / "BUSINESS-FORMALISATION-ENGLISH-FY-2024-25.pdf",
        pdf_dir / "Advance-Tax-on-the-Transport-Sector-FY-2024-25.pdf",
    ]
    available_pdfs = [p for p in sample_files if p.exists()]
    if not available_pdfs:
        available_pdfs = list(pdf_dir.glob("*.pdf"))[:3]

    print(f"📄 Loaded {len(available_pdfs)} official URA benchmark PDF documents:")
    for p in available_pdfs:
        print(f"   • {p.name} ({p.stat().st_size:,} bytes)")
    print("-" * 85)

    total_chars_extracted = 0
    total_tables_extracted = 0
    start_total_time = time.perf_counter()

    for idx, pdf_path in enumerate(available_pdfs, 1):
        print(f"\n[{idx}/{len(available_pdfs)}] Processing: {pdf_path.name}")
        pdf_bytes = pdf_path.read_bytes()

        # 3. Extraction Benchmark
        t0 = time.perf_counter()
        doc_record = documents.analyze_document(pdf_bytes, pdf_path.name)
        t_extract = (time.perf_counter() - t0) * 1000.0

        char_count = len(doc_record.text)
        table_count = len(doc_record.tables)
        total_chars_extracted += char_count
        total_tables_extracted += table_count

        print(f"   ✓ Ingest & Extraction  : {t_extract:.2f} ms")
        print(f"   ✓ Document Type        : {doc_record.doc_type}")
        print(f"   ✓ Confidence Score     : {doc_record.confidence:.2%}")
        print(f"   ✓ Extracted Characters : {char_count:,} chars")
        print(f"   ✓ Extracted Tables     : {table_count} table(s)")

        # 4. Table Structuring & Grid Detection Benchmark
        t0 = time.perf_counter()
        structured_tables = table_structuring.structure_document_tables(
            pdf_bytes=pdf_bytes,
            raw_text=doc_record.text,
            page_number=1,
        )
        t_tbl = (time.perf_counter() - t0) * 1000.0
        print(f"   ✓ Table Structuring    : {t_tbl:.2f} ms ({len(structured_tables)} structured grids detected)")

        # 5. Normalization Manifest & SHA-256 Provenance Hashing
        t0 = time.perf_counter()
        bundle = normalize_document(pdf_bytes, extract_text_layer=True, extract_tables=True)
        manifest = build_normalization_manifest(bundle, document_id=doc_record.doc_id)
        valid = validate_normalization_manifest(manifest, raw_data=pdf_bytes)
        t_manifest = (time.perf_counter() - t0) * 1000.0
        print(f"   ✓ Normalization SHA256 : {t_manifest:.2f} ms | Pages: {bundle.page_count} | Hash: {bundle.source_fingerprint[:16]}... | Verified: {valid}")

        # 6. HITL Uncertainty Routing
        t0 = time.perf_counter()
        hitl_decision = hitl_routing.assess_document_for_human_review(doc_record)
        t_hitl = (time.perf_counter() - t0) * 1000.0
        print(f"   ✓ HITL Assessment      : {t_hitl:.2f} ms (Requires Review: {hitl_decision.requires_review})")

    total_duration = time.perf_counter() - start_total_time

    print("\n" + "=" * 85)
    print("📊 BENCHMARK SUMMARY & PERFORMANCE RESULTS")
    print("=" * 85)
    print(f"Total Documents Ingested : {len(available_pdfs)}")
    print(f"Total Text Extracted     : {total_chars_extracted:,} characters")
    print(f"Total Tables Structured  : {total_tables_extracted} tables")
    print(f"Total Processing Time    : {total_duration:.2f} seconds")
    print(f"Average Document Latency : {(total_duration / len(available_pdfs)) * 1000.0:.2f} ms")
    print(f"Throughput Rate          : {len(available_pdfs) / max(0.001, total_duration):.2f} docs/sec")
    print("=" * 85)

    if cuda_available:
        torch.cuda.empty_cache()
        print("🧹 CUDA cache cleared and memory reclaimed.")


if __name__ == "__main__":
    run_benchmark()

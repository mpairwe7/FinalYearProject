"""Automated Document Analysis & PDF Report Generation Stress Benchmark (2026).

Benchmarks:
1. Concurrent multi-page document ingestion & entity extraction under load (ProcessPool).
2. Cryptographic normalization manifest hashing & validation throughput.
3. Branded PDF report generation throughput & latency percentiles (p50, p95, p99).
"""

from __future__ import annotations

import concurrent.futures
import gc
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Target free GPU device 5
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "5")

import torch
from app import documents, pdf_export, hitl_routing
from app.document_normalization import normalize_document, build_normalization_manifest, validate_normalization_manifest
from app.vision import table_structuring


@dataclass
class StressResult:
    test_name: str
    total_ops: int
    successful_ops: int
    failed_ops: int
    duration_sec: float
    ops_per_sec: float
    p50_ms: float
    p95_ms: float
    p99_ms: float


def _worker_ingest(task_args: tuple[bytes, str]) -> tuple[bool, float, dict]:
    data, filename = task_args
    t0 = time.perf_counter()
    try:
        rec = documents.analyze_document(data, filename)
        dur = (time.perf_counter() - t0) * 1000.0
        return (True, dur, rec.to_report_payload())
    except Exception as e:
        dur = (time.perf_counter() - t0) * 1000.0
        return (False, dur, {})


def _worker_manifest(task_args: tuple[bytes, str]) -> tuple[bool, float]:
    data, doc_id = task_args
    t0 = time.perf_counter()
    try:
        bundle = normalize_document(data, extract_text_layer=True, extract_tables=True)
        manifest = build_normalization_manifest(bundle, document_id=doc_id)
        valid = validate_normalization_manifest(manifest, raw_data=data)
        dur = (time.perf_counter() - t0) * 1000.0
        return (valid, dur)
    except Exception:
        dur = (time.perf_counter() - t0) * 1000.0
        return (False, dur)


def _worker_report(report_payload: dict) -> tuple[bool, float]:
    t0 = time.perf_counter()
    try:
        pdf_out = pdf_export.generate_document_report_pdf(report_payload)
        dur = (time.perf_counter() - t0) * 1000.0
        is_valid = pdf_out.startswith(b"%PDF-") and b"%%EOF" in pdf_out
        return (is_valid, dur)
    except Exception:
        dur = (time.perf_counter() - t0) * 1000.0
        return (False, dur)


def run_benchmark_suite(concurrency: int = 4, total_iterations: int = 20) -> list[StressResult]:
    print("=" * 90)
    print("🚀 INITIALIZING CONCURRENT DOCUMENT ANALYSIS & REPORT GENERATION STRESS BENCHMARK")
    print("=" * 90)

    # 1. Device Info
    cuda_available = torch.cuda.is_available()
    device_name = torch.cuda.get_device_name(0) if cuda_available else "High-Performance CPU Vector Engine"
    print(f"Compute Hardware    : {device_name} (CUDA: {cuda_available})")
    print(f"Target Concurrency  : {concurrency} worker processes | Total Operations: {total_iterations}")
    print("-" * 90)

    # 2. Load Statutory Test Documents
    pdf_path = Path("App/Data/pdfs/TAX-WAIVER-2025-26-1.pdf")
    assert pdf_path.exists(), f"Missing {pdf_path}"
    pdf_bytes = pdf_path.read_bytes()
    filename = pdf_path.name

    results: list[StressResult] = []

    # -------------------------------------------------------------------------
    # Scenario 1: Concurrent Document Ingest & Entity Extraction
    # -------------------------------------------------------------------------
    print(f"📄 1. Stress Testing Ingest & Entity Extraction ({total_iterations} requests, C={concurrency})...")
    latencies: list[float] = []
    successes, failures = 0, 0
    sample_report_payload: dict = {}

    t_start = time.perf_counter()
    with concurrent.futures.ProcessPoolExecutor(max_workers=concurrency) as executor:
        tasks = [(pdf_bytes, f"doc_{i}_{filename}") for i in range(total_iterations)]
        for ok, lat, r_payload in executor.map(_worker_ingest, tasks):
            latencies.append(lat)
            if ok:
                successes += 1
                if not sample_report_payload:
                    sample_report_payload = r_payload
            else:
                failures += 1
    t_total = time.perf_counter() - t_start

    latencies.sort()
    n = len(latencies)
    res1 = StressResult(
        test_name="1. Document Ingest & Extraction",
        total_ops=n,
        successful_ops=successes,
        failed_ops=failures,
        duration_sec=round(t_total, 3),
        ops_per_sec=round(n / max(0.001, t_total), 2),
        p50_ms=round(latencies[int(n * 0.50)], 2),
        p95_ms=round(latencies[int(n * 0.95)], 2),
        p99_ms=round(latencies[int(n * 0.99)], 2),
    )
    results.append(res1)
    print(f"   ✓ Completed: {successes}/{n} succeeded | Throughput: {res1.ops_per_sec} ops/s | p50: {res1.p50_ms}ms | p95: {res1.p95_ms}ms")

    # -------------------------------------------------------------------------
    # Scenario 2: Concurrent Cryptographic Provenance Manifest Generation
    # -------------------------------------------------------------------------
    print(f"\n🔒 2. Stress Testing Provenance Normalization Manifests ({total_iterations} requests, C={concurrency})...")
    latencies = []
    successes, failures = 0, 0

    t_start = time.perf_counter()
    with concurrent.futures.ProcessPoolExecutor(max_workers=concurrency) as executor:
        tasks = [(pdf_bytes, f"doc_{i}") for i in range(total_iterations)]
        for ok, lat in executor.map(_worker_manifest, tasks):
            latencies.append(lat)
            if ok:
                successes += 1
            else:
                failures += 1
    t_total = time.perf_counter() - t_start

    latencies.sort()
    n = len(latencies)
    res2 = StressResult(
        test_name="2. Provenance Manifest Validation",
        total_ops=n,
        successful_ops=successes,
        failed_ops=failures,
        duration_sec=round(t_total, 3),
        ops_per_sec=round(n / max(0.001, t_total), 2),
        p50_ms=round(latencies[int(n * 0.50)], 2),
        p95_ms=round(latencies[int(n * 0.95)], 2),
        p99_ms=round(latencies[int(n * 0.99)], 2),
    )
    results.append(res2)
    print(f"   ✓ Completed: {successes}/{n} succeeded | Throughput: {res2.ops_per_sec} ops/s | p50: {res2.p50_ms}ms | p95: {res2.p95_ms}ms")

    # -------------------------------------------------------------------------
    # Scenario 3: Concurrent Branded PDF Report Generation
    # -------------------------------------------------------------------------
    print(f"\n📑 3. Stress Testing Branded PDF Report Generation ({total_iterations} requests, C={concurrency})...")
    if not sample_report_payload:
        sample_rec = documents.analyze_document(pdf_bytes, filename)
        sample_report_payload = sample_rec.to_report_payload()

    latencies = []
    successes, failures = 0, 0

    t_start = time.perf_counter()
    with concurrent.futures.ProcessPoolExecutor(max_workers=concurrency) as executor:
        tasks = [sample_report_payload for _ in range(total_iterations)]
        for ok, lat in executor.map(_worker_report, tasks):
            latencies.append(lat)
            if ok:
                successes += 1
            else:
                failures += 1
    t_total = time.perf_counter() - t_start

    latencies.sort()
    n = len(latencies)
    res3 = StressResult(
        test_name="3. Branded PDF Report Generation",
        total_ops=n,
        successful_ops=successes,
        failed_ops=failures,
        duration_sec=round(t_total, 3),
        ops_per_sec=round(n / max(0.001, t_total), 2),
        p50_ms=round(latencies[int(n * 0.50)], 2),
        p95_ms=round(latencies[int(n * 0.95)], 2),
        p99_ms=round(latencies[int(n * 0.99)], 2),
    )
    results.append(res3)
    print(f"   ✓ Completed: {successes}/{n} succeeded | Throughput: {res3.ops_per_sec} ops/s | p50: {res3.p50_ms}ms | p95: {res3.p95_ms}ms")

    # 4. Clean up Memory
    gc.collect()
    if cuda_available:
        torch.cuda.empty_cache()
    print("\n🧹 Garbage collection and CUDA cache reclamation completed.")

    return results


if __name__ == "__main__":
    bench_results = run_benchmark_suite(concurrency=4, total_iterations=20)
    print("\n" + "=" * 90)
    print(f"{'Benchmark Scenario':<38} | {'Ops':<5} | {'Ops/sec':<8} | {'p50 (ms)':<9} | {'p95 (ms)':<9} | {'p99 (ms)':<9}")
    print("=" * 90)
    for r in bench_results:
        print(f"{r.test_name:<38} | {r.total_ops:<5} | {r.ops_per_sec:<8} | {r.p50_ms:<9} | {r.p95_ms:<9} | {r.p99_ms:<9}")
    print("=" * 90)

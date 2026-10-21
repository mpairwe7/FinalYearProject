"""Unit tests for Phase D: ephemeral tmpfs PDF/XLSX sandbox, pdf_guards inspection, and chunk caching."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app import ephemeral_pdf_sandbox as sbx
from app.pdf_guards import PdfRejected


@pytest.fixture(autouse=True)
def _reset_sandbox_cache():
    """Clear L1 sandbox chunk cache before and after every test."""
    sbx.clear_sandbox_cache()
    yield
    sbx.clear_sandbox_cache()


def _make_pdf_bytes(pages_text: list[str], *, inject_js: bool = False) -> bytes:
    """Build a valid multi-page PDF-1.4 document in pure Python for testing sandbox extraction and guards."""
    objs: list[tuple[int, bytes]] = []
    page_ids = [4 + 2 * i for i in range(len(pages_text))]
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    extra_cat = "/OpenAction << /S /JavaScript /JS (app.alert(1)) >>" if inject_js else ""
    cat = f"<< /Type /Catalog /Pages 2 0 R {extra_cat} >>".strip()
    objs.append((1, cat.encode("latin-1")))
    objs.append((2, f"<< /Type /Pages /Kids [{kids}] /Count {len(pages_text)} >>".encode("latin-1")))
    objs.append((3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))

    for i, txt in enumerate(pages_text):
        pid = 4 + 2 * i
        cid = pid + 1
        lines = txt.splitlines()
        stream_lines = ["BT", "/F1 11 Tf", "50 750 Td", "14 TL"]
        for idx, line in enumerate(lines):
            esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            if idx == 0:
                stream_lines.append(f"({esc}) Tj")
            else:
                stream_lines.append(f"T* ({esc}) Tj")
        stream_lines.append("ET")
        stream_bytes = "\n".join(stream_lines).encode("latin-1")
        page_dict = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {cid} 0 R >>"
        ).encode("latin-1")
        content_dict = (
            f"<< /Length {len(stream_bytes)} >>\nstream\n".encode("latin-1")
            + stream_bytes
            + b"\nendstream"
        )
        objs.append((pid, page_dict))
        objs.append((cid, content_dict))

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for oid, body in objs:
        offsets[oid] = len(out)
        out.extend(f"{oid} 0 obj\n".encode("latin-1"))
        out.extend(body)
        out.extend(b"\nendobj\n")

    xref_pos = len(out)
    total_objs = len(objs) + 1
    out.extend(f"xref\n0 {total_objs}\n0000000000 65535 f \n".encode("latin-1"))
    for oid in range(1, total_objs):
        out.extend(f"{offsets[oid]:010d} 00000 n \n".encode("latin-1"))
    out.extend(
        f"trailer\n<< /Size {total_objs} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode(
            "latin-1"
        )
    )
    return bytes(out)


def test_ephemeral_sandbox_wipes_directory_on_normal_exit_and_on_exception(
    tmp_path: Path,
) -> None:
    """Verify ephemeral_sandbox creates a 0o700 directory and wipes it even when an exception occurs."""
    created_dir: Path | None = None
    with sbx.ephemeral_sandbox("turn-101", base_dir=tmp_path) as d:
        created_dir = d
        assert d.exists()
        (d / "temp.pdf").write_bytes(b"%PDF-1.7 test")
        assert (d / "temp.pdf").exists()

    assert created_dir is not None
    assert not created_dir.exists()

    # Also verify cleanup when an exception is raised inside the context block
    failed_dir: Path | None = None
    with pytest.raises(RuntimeError, match="simulated crash"):
        with sbx.ephemeral_sandbox("turn-crash", base_dir=tmp_path) as d2:
            failed_dir = d2
            (d2 / "partial.pdf").write_bytes(b"data")
            raise RuntimeError("simulated crash")

    assert failed_dir is not None
    assert not failed_dir.exists()


def test_valid_ura_pdf_extraction_metadata_reranking_and_sha256_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify valid PDF extraction, metadata parsing, GPU/lexical reranking, and 24h Redis SHA-256 cache."""
    redis_store: dict[str, str] = {}

    class _FakeRedis:
        """Fake Redis client for testing 24h SHA-256 PDF chunk cache."""

        def get(self, key: str) -> str | None:
            """Get cached JSON string."""
            return redis_store.get(key)

        def setex(self, key: str, ttl: int, value: str) -> None:
            """Store cached JSON string with TTL."""
            assert ttl == sbx.SANDBOX_CACHE_TTL_SECONDS
            redis_store[key] = value

    monkeypatch.setattr(sbx, "_get_redis", lambda: _FakeRedis())

    pages = [
        "UGANDA REVENUE AUTHORITY\nTAXATION OF THE REAL ESTATE SECTOR GUIDE FY 2026-27\nSection 1: Overview of Rental Tax.",
        "SECTION 2: INDIVIDUAL LANDLORD RENTAL TAX\nUnder Section 5 of the Income Tax Act, individual rental tax is 12% above UGX 2,820,000 annual threshold with zero expense deductions.",
        "SECTION 3: CORPORATE LANDLORD RENTAL INCOME\nCompanies pay 30% on net chargeable rental income after allowable deductions capped at 50%.",
        "SECTION 4: WITHHOLDING TAX ON RENT\nDesignated withholding agents deduct 6% WHT on gross rental payments.",
        "SECTION 5: EFRIS E-INVOICING FOR COMMERCIAL LANDLORDS\nAll landlords must issue EFRIS e-receipts.",
        "SECTION 6: FILING DEADLINES AND PENALTIES\nAnnual rental tax returns are due by 31 December.",
    ]
    pdf_bytes = _make_pdf_bytes(pages)
    url = "https://ura.go.ug/en/wp-content/uploads/2026/07/TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2026-27.pdf"

    class _FakeReranker:
        """Stub GPU 2 CrossEncoder scoring chunks that mention 12% and 2,820,000 highest."""

        def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
            """Return higher score for the individual rental tax page."""
            return [0.95 if "2,820,000" in p[1] else 0.30 for p in pairs]

    class _FakeRetriever:
        """Stub HybridRetriever carrying _reranker."""

        _reranker = _FakeReranker()

    doc = sbx.fetch_and_extract_sandbox_document(
        url,
        "What is the individual rental tax rate and threshold in FY2026-27?",
        turn_id="turn-real-estate",
        retriever=_FakeRetriever(),
        top_k=2,
        byte_fetcher=lambda _u, _m: pdf_bytes,
        base_dir=tmp_path,
    )
    assert doc.from_cache is False
    assert doc.fiscal_year == "FY2026-27"
    assert doc.doc_family == "sector_guide_real_estate"
    assert doc.doc_kind == "full_guide"
    assert doc.page_count == 6
    assert len(doc.chunks) == 6
    assert len(doc.top_chunks) == 2
    assert "2,820,000" in doc.top_chunks[0]["text"]
    assert f"ura:pdf_chunks:{doc.sha256}" in redis_store

    # Clear L1 cache so second call loads from Redis cache
    sbx.clear_sandbox_cache()
    doc_cached = sbx.fetch_and_extract_sandbox_document(
        url,
        "What is the corporate rental income deduction cap for companies?",
        turn_id="turn-real-estate-2",
        top_k=2,
        byte_fetcher=lambda _u, _m: pdf_bytes,
        base_dir=tmp_path,
    )
    assert doc_cached.from_cache is True
    assert doc_cached.sha256 == doc.sha256
    assert "30%" in doc_cached.top_chunks[0]["text"]


def test_malicious_javascript_pdf_and_oversized_download_rejected(
    tmp_path: Path,
) -> None:
    """Verify pdf_guards rejects /JavaScript PDFs and download_to_sandbox rejects >25 MB or Tier-3 URLs."""
    malicious_bytes = _make_pdf_bytes(["URA Public Notice"], inject_js=True)
    url = "https://ura.go.ug/en/wp-content/uploads/2026/07/Notice.pdf"

    with pytest.raises(PdfRejected, match="JavaScript"):
        sbx.fetch_and_extract_sandbox_document(
            url,
            "public notice",
            turn_id="turn-malicious",
            byte_fetcher=lambda _u, _m: malicious_bytes,
            base_dir=tmp_path,
        )
    # Confirm no sandbox subdirectory remains in tmp_path
    assert list(tmp_path.iterdir()) == []

    # Oversized download (> max_bytes) is rejected
    with sbx.ephemeral_sandbox("turn-size", base_dir=tmp_path) as d:
        with pytest.raises(sbx.SandboxDownloadRejected, match="exceeds sandbox cap"):
            sbx.download_to_sandbox(
                url,
                d,
                max_bytes=1024,
                byte_fetcher=lambda _u, _m: b"%PDF-1.7 " + (b"x" * 2048),
            )

    # Non-Tier-1 URL is rejected before downloading
    with sbx.ephemeral_sandbox("turn-tier3", base_dir=tmp_path) as d:
        with pytest.raises(sbx.SandboxDownloadRejected, match="Disallowed sandbox URL|Tier-1"):
            sbx.download_to_sandbox(
                "https://www.kra.go.ke/notice.pdf",
                d,
                byte_fetcher=lambda _u, _m: b"%PDF-1.7",
            )

"""URA website crawler and PDF discovery (2026).

High-performance full-site scraper with:
  - CDX-powered Wayback Machine discovery (finds ALL archived URLs upfront)
  - Persistent session pooling with keep-alive for Wayback fetches
  - Concurrent I/O via ThreadPoolExecutor (single pool, no respawn)
  - Adaptive strategy: skips broken strategies after first failure
  - Shared headless browser instance (launched once, reused)
  - Rich extraction: text, tables (structured + markdown), PDF content
  - Deep-link BFS with dedup by content hash
  - Offline CSV fallback when no connectivity

Usage::

    from ml.scripts.data_aug.crawler import CrawlConfig, crawl_ura, load_crawled_pages

    cfg = CrawlConfig(pdf_output_dir=Path("Data/pdfs"))
    pages = list(crawl_ura(cfg))
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

from ml.scripts.data_aug.schema import (
    URA_SYSTEM_PROMPT,
    Message,
    Metadata,
    SourceType,
    TaskType,
    TrainingExample,
    content_hash,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]

_BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

_CDX_SKIP_EXT = frozenset((
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".eot", ".map", ".webp", ".mp4",
    ".mp3", ".webm", ".zip", ".gz", ".tar",
))

USER_AGENT = "URA-Chatbot-Crawler/1.0 (+https://github.com/ura-chatbot; research)"


# ---------------------------------------------------------------------------
# Config & models
# ---------------------------------------------------------------------------


@dataclass
class CrawlConfig:
    seed_urls: list[str] = field(
        default_factory=lambda: [
            "https://ura.go.ug/en/",
            "https://ura.go.ug/en/domestic-taxes",
            "https://ura.go.ug/en/customs",
            "https://ura.go.ug/en/tax-types",
            "https://ura.go.ug/en/downloads",
        ]
    )
    allowed_domains: list[str] = field(default_factory=lambda: ["ura.go.ug"])
    max_pages: int = 500
    rate_limit_s: float = 0.3
    request_timeout: int = 20
    max_workers: int = 6
    respect_robots: bool = True
    pdf_output_dir: Path = Path("Data/pdfs")
    crawl_output_dir: Path = Path("Data/crawl")
    csv_fallback_dir: Path = Path("Data/dataset")
    state_file: Path | None = None

    def __post_init__(self):
        if self.state_file is None:
            self.state_file = self.crawl_output_dir / "crawl_state.json"


@dataclass
class CrawledPage:
    url: str
    title: str
    text: str
    content_hash: str
    timestamp: str
    discovered_pdfs: list[str] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    status_code: int = 200


# ---------------------------------------------------------------------------
# Session factory — persistent, pooled, with retry
# ---------------------------------------------------------------------------


def _make_session() -> requests.Session:
    """Create a session with connection pooling and automatic retry."""
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=0.5,
                  status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({**_BROWSER_HEADERS, "User-Agent": random.choice(_USER_AGENTS)})
    return s


# ---------------------------------------------------------------------------
# CDX discovery — find ALL archived URLs in one batch
# ---------------------------------------------------------------------------


def _cdx_discover(
    domain: str = "ura.go.ug",
    session: requests.Session | None = None,
    timeout: int = 45,
) -> list[tuple[str, str]]:
    """Query Wayback CDX API for all archived URLs. Returns [(timestamp, url)]."""
    s = session or _make_session()
    prefixes = [
        "en/*", "download/*", "ac/*", "lg/*",
        "en/domestic-taxes/*", "en/customs/*", "en/category/*",
        "en/efris/*", "en/tax-*", "en/legal-policy/*",
        "en/media-center/*", "en/opportunities/*", "en/community/*",
        "en/research-publications/*", "download-category/*",
    ]

    results: list[tuple[str, str]] = []
    for prefix in prefixes:
        try:
            resp = s.get(
                "https://web.archive.org/cdx/search/cdx",
                params={
                    "url": f"{domain}/{prefix}",
                    "output": "json",
                    "fl": "timestamp,original,statuscode",
                    "filter": "statuscode:200",
                    "collapse": "urlkey",
                    "limit": "500",
                },
                timeout=timeout,
            )
            if resp.status_code != 200:
                continue
            data = json.loads(resp.text)
            for row in data[1:]:
                ts, url, _ = row
                low = url.lower()
                if any(low.endswith(e) for e in _CDX_SKIP_EXT):
                    continue
                if ".min." in low or "wp-json" in low or "wp-admin" in low:
                    continue
                results.append((ts, url))
        except Exception as e:
            log.debug("cdx: prefix %s failed: %s", prefix, e)

    # Deduplicate — keep latest timestamp per URL
    by_url: dict[str, str] = {}
    for ts, url in results:
        if url not in by_url or ts > by_url[url]:
            by_url[url] = ts

    deduped = sorted(by_url.items(), key=lambda kv: kv[1], reverse=True)
    log.info("cdx: discovered %d unique URLs", len(deduped))
    return [(ts, url) for url, ts in deduped]


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------


def _wayback_fetch(
    url: str, timestamp: str, session: requests.Session, timeout: int,
) -> requests.Response | None:
    """Fetch a Wayback snapshot. Try exact timestamp first (id_ = raw content),
    then fall back to redirect-based latest."""
    # Exact timestamp — returns raw content, no Wayback toolbar
    wb_url = f"https://web.archive.org/web/{timestamp}id_/{url}"
    try:
        resp = session.get(wb_url, timeout=timeout)
        if resp.status_code == 200 and len(resp.content) > 200:
            r = requests.models.Response()
            r.status_code = 200
            r._content = resp.content
            r.headers.update(dict(resp.headers))
            r.url = url
            return r
    except Exception:
        pass

    # Latest redirect
    try:
        resp = session.get(
            f"https://web.archive.org/web/2/{url}", timeout=timeout,
        )
        if resp.status_code == 200 and len(resp.content) > 500:
            r = requests.models.Response()
            r.status_code = 200
            r._content = resp.content
            r.headers.update(dict(resp.headers))
            r.url = url
            return r
    except Exception:
        pass

    return None


def _direct_fetch(
    url: str, session: requests.Session, timeout: int,
) -> requests.Response | None:
    """Try fetching directly from ura.go.ug (works when site is up)."""
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200 and len(resp.content) > 200:
            return resp
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# HTML & table extraction
# ---------------------------------------------------------------------------


def _sha256_short(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _extract_tables(soup: BeautifulSoup) -> list[dict]:
    """Extract HTML tables as structured dicts."""
    tables = []
    for table in soup.find_all("table"):
        headers: list[str] = []
        rows: list[list[str]] = []
        thead = table.find("thead")
        if thead:
            headers = [th.get_text(separator=" ", strip=True)
                       for th in thead.find_all(["th", "td"])]
        for tr in table.find_all("tr"):
            cells = [td.get_text(separator=" ", strip=True)
                     for td in tr.find_all(["td", "th"])]
            if not cells or all(not c for c in cells):
                continue
            if not headers and all(c for c in cells):
                headers = cells
                continue
            rows.append(cells)
        if rows:
            tables.append({"headers": headers, "rows": rows, "row_count": len(rows)})
    return tables


def _tables_to_markdown(tables: list[dict]) -> str:
    parts = []
    for t in tables:
        h = t.get("headers", [])
        rows = t.get("rows", [])
        if not rows:
            continue
        ncols = max(len(h), max((len(r) for r in rows), default=0))
        h += [""] * (ncols - len(h))
        md = "| " + " | ".join(h) + " |\n| " + " | ".join(["---"] * ncols) + " |\n"
        for row in rows:
            row += [""] * (ncols - len(row))
            md += "| " + " | ".join(row) + " |\n"
        parts.append(md)
    return "\n".join(parts)


def _clean_html_text(soup: BeautifulSoup, tables: list[dict] | None = None) -> str:
    """Extract text + tables from parsed HTML."""
    for tag in soup.find_all(["script", "style", "nav", "footer", "header",
                              "aside", "noscript", "iframe", "form"]):
        tag.decompose()
    for wb in soup.find_all(id=re.compile(r"wm-ipp|donato|playback")):
        wb.decompose()
    for wb in soup.find_all(class_=re.compile(r"wm-ipp|web-archive")):
        wb.decompose()

    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find("div", class_=re.compile(r"content|main|article|page-body", re.I))
        or soup.find("div", id=re.compile(r"content|main|article", re.I))
    )
    target = main or soup.body or soup

    lines = []
    for el in target.find_all(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6",
                                "dt", "dd", "blockquote", "figcaption", "summary"]):
        if el.name in ("td", "th"):
            continue
        t = el.get_text(separator=" ", strip=True)
        if not t or len(t) < 8:
            continue
        if el.name.startswith("h") and el.name[1:].isdigit():
            t = "#" * int(el.name[1]) + " " + t
        elif el.name == "dt":
            t = "**" + t + "**"
        lines.append(t)

    tbl = tables if tables is not None else _extract_tables(target)
    tbl_md = _tables_to_markdown(tbl)
    if tbl_md:
        lines.append("\n" + tbl_md)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------


def _extract_pdf_text(pdf_path: Path) -> str:
    try:
        import pymupdf4llm
        return pymupdf4llm.to_markdown(str(pdf_path))
    except ImportError:
        pass
    try:
        import pymupdf
        doc = pymupdf.open(str(pdf_path))
        text = "\n\n".join(page.get_text() for page in doc)
        doc.close()
        return text
    except (ImportError, Exception) as e:
        log.debug("pdf extract failed for %s: %s", pdf_path.name, e)
    return ""


# ---------------------------------------------------------------------------
# Link discovery
# ---------------------------------------------------------------------------


def _normalise_href(raw: str, base_url: str) -> str | None:
    if not raw or raw.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    href = urljoin(base_url, raw).split("#")[0].split("?")[0]
    # Unwrap Wayback Machine links back to the page they archived. The host has
    # to come from the parsed URL, not from `"web.archive.org" in href`: that
    # substring also appears in `https://evil.example/web.archive.org/web/1/...`,
    # and the rewrite below would then hand the crawler whatever target that
    # path carried -- a crawled page choosing the next host to fetch. Anchoring
    # the pattern to the parsed path closes the same hole from the other side.
    parsed = urlparse(href)
    host = (parsed.hostname or "").lower()
    if host == "web.archive.org" or host.endswith(".web.archive.org"):
        m = re.match(r"/web/\d+/(https?://.*)", parsed.path)
        if m:
            href = m.group(1)
    return href


def _discover_links(
    soup: BeautifulSoup, base_url: str, allowed: list[str],
) -> tuple[list[str], list[str]]:
    """Return (page_urls, pdf_urls) discovered in a page."""
    pages, pdfs = [], []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = _normalise_href(a["href"], base_url)
        if not href or href in seen:
            continue
        seen.add(href)
        if href.lower().endswith(".pdf"):
            pdfs.append(href)
        elif _is_same_domain(href, allowed):
            pages.append(href)
    for link in soup.find_all("link", rel=True, href=True):
        if any(r in ("alternate", "next", "prev") for r in link.get("rel", [])):
            href = _normalise_href(link["href"], base_url)
            if href and href not in seen and _is_same_domain(href, allowed):
                pages.append(href)
                seen.add(href)
    return pages, pdfs


def _is_same_domain(url: str, allowed: list[str]) -> bool:
    host = urlparse(url).hostname or ""
    return any(host == d or host.endswith(f".{d}") for d in allowed)


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def _load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"visited": {}, "pdf_hashes": {}}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Page processing
# ---------------------------------------------------------------------------


def _process_response(
    resp: requests.Response, url: str, config: CrawlConfig, state: dict,
) -> CrawledPage | None:
    """Turn an HTTP response into a CrawledPage (HTML or PDF)."""
    ct = resp.headers.get("Content-Type", "")

    # --- PDF ---
    if "application/pdf" in ct or url.lower().endswith(".pdf"):
        return _handle_pdf(resp, url, config, state)

    # --- HTML ---
    if ct and "text/html" not in ct and "text/plain" not in ct:
        return None

    html = resp.text if hasattr(resp, "text") else resp.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else url

    # Skip error pages
    if title.lower() in ("error", "404", "not found", "page not found"):
        return None

    tables = _extract_tables(soup)
    text = _clean_html_text(soup, tables)

    if len(text) < 50 and not tables:
        return None

    _, pdfs = _discover_links(soup, url, config.allowed_domains)
    return CrawledPage(
        url=url, title=title, text=text,
        content_hash=_sha256_short(text),
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        discovered_pdfs=pdfs, tables=tables,
        status_code=resp.status_code,
    )


def _handle_pdf(
    resp: requests.Response, url: str, config: CrawlConfig, state: dict,
) -> CrawledPage | None:
    """Download PDF, extract text, return as CrawledPage."""
    data = resp.content
    if len(data) < 500:
        return None
    h = hashlib.sha256(data).hexdigest()[:32]
    if h in state.get("pdf_hashes", {}):
        return None

    slug = urlparse(url).path.split("/")[-1] or "page.pdf"
    if not slug.lower().endswith(".pdf"):
        slug += ".pdf"
    safe = re.sub(r"[^\w\-.]", "_", f"{urlparse(url).hostname}-{slug}")
    dest = config.pdf_output_dir / safe
    if not dest.exists():
        dest.write_bytes(data)

    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state.setdefault("pdf_hashes", {})[h] = {
        "url": url, "filename": safe,
        "size_bytes": len(data), "timestamp": ts,
    }
    log.info("crawl: downloaded PDF %s (%d bytes)", safe, len(data))

    text = _extract_pdf_text(dest)
    if len(text) < 100:
        return None

    return CrawledPage(
        url=url, title=f"PDF: {slug}", text=text,
        content_hash=_sha256_short(text), timestamp=ts,
        status_code=200,
    )


# ---------------------------------------------------------------------------
# CSV offline fallback
# ---------------------------------------------------------------------------


def _offline_fallback(config: CrawlConfig) -> Iterator[CrawledPage]:
    csv_dir = config.csv_fallback_dir
    if not csv_dir.exists():
        return
    pages_dir = config.crawl_output_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for csv_file in sorted(csv_dir.glob("*.csv")):
        try:
            with open(csv_file, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except Exception:
            continue
        if not rows:
            continue

        topic = csv_file.stem
        lines = []
        for row in rows:
            q = row.get("question", row.get("Question", "")).strip()
            a = row.get("answer", row.get("Answer", "")).strip()
            if q and a:
                lines.append(f"Q: {q}\nA: {a}")
        if not lines:
            continue

        text = f"URA FAQ — {topic.replace('_', ' ').title()}\n\n" + "\n\n".join(lines[:20])
        h = _sha256_short(text)
        url = f"https://ura.go.ug/en/faq/{topic}"
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        page = CrawledPage(url=url, title=f"URA FAQ: {topic}", text=text,
                           content_hash=h, timestamp=ts, status_code=200)
        page_file = pages_dir / f"{h}.json"
        page_file.write_text(json.dumps({
            "url": url, "title": page.title, "text": text,
            "content_hash": h, "timestamp": ts,
            "discovered_pdfs": [], "tables": [],
        }, ensure_ascii=False, indent=2))
        count += 1
        yield page

    log.info("crawl offline: generated %d pages from CSV fallback", count)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def crawl_ura(config: CrawlConfig) -> Iterator[CrawledPage]:
    """Full-site crawl of ura.go.ug.

    Pipeline:
      1. CDX discovery  → all archived URLs in one API call
      2. Direct fetch    → try ura.go.ug first (fast if site is up)
      3. Wayback fetch   → exact-timestamp snapshots (if direct fails)
      4. Concurrent I/O  → single ThreadPoolExecutor, persistent sessions
      5. Deep-link BFS   → follow PDFs + pages discovered in content
      6. CSV fallback    → offline generation from existing FAQ data
    """
    session = _make_session()
    state = _load_state(config.state_file)
    visited_hashes: set[str] = set()
    visited_urls: set[str] = set()
    config.crawl_output_dir.mkdir(parents=True, exist_ok=True)
    config.pdf_output_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = config.crawl_output_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    # --- Phase 1: check direct access ---
    direct_works = False
    try:
        r = session.head(config.seed_urls[0], timeout=8)
        direct_works = r.status_code > 0
    except Exception:
        pass
    log.info("crawl: direct access to ura.go.ug: %s", "YES" if direct_works else "NO")

    # --- Phase 2: CDX discovery ---
    cdx_urls = _cdx_discover(session=session, timeout=config.request_timeout)
    if not cdx_urls and not direct_works:
        log.warning("crawl: no CDX URLs and no direct access — offline fallback")
        yield from _offline_fallback(config)
        return

    # Build URL queue: CDX URLs + seed URLs (deduped)
    url_queue: list[tuple[str, str]] = []  # (timestamp, url)
    seen_in_queue: set[str] = set()
    for ts, url in cdx_urls:
        if url not in seen_in_queue:
            url_queue.append((ts, url))
            seen_in_queue.add(url)
    for seed in config.seed_urls:
        if seed not in seen_in_queue:
            url_queue.append(("", seed))
            seen_in_queue.add(seed)

    # Prioritize: /en/ pages first, then downloads, then PDFs
    def _priority(item: tuple[str, str]) -> int:
        url = item[1]
        if "/en/" in url and not url.endswith(".pdf"):
            return 0
        if "/download" in url and not url.endswith(".pdf"):
            return 1
        if url.endswith(".pdf"):
            return 2
        return 3
    url_queue.sort(key=_priority)

    log.info("crawl: %d URLs to process (max %d pages)", len(url_queue), config.max_pages)

    # --- Phase 3: Concurrent fetch + process ---
    pages_yielded = 0
    urls_fetched = 0
    deep_links: list[str] = []

    def _fetch_one(ts_url: tuple[str, str]) -> tuple[str, requests.Response | None]:
        ts, url = ts_url
        # Try direct first (fast)
        if direct_works:
            resp = _direct_fetch(url, session, config.request_timeout)
            if resp:
                return url, resp
        # Then wayback
        if ts:
            return url, _wayback_fetch(url, ts, session, config.request_timeout)
        return url, _wayback_fetch(url, "", session, config.request_timeout)

    # Single executor for entire crawl — no respawn overhead
    batch_size = min(config.max_workers * 2, len(url_queue))
    idx = 0

    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        while idx < len(url_queue) and pages_yielded < config.max_pages:
            batch = url_queue[idx:idx + batch_size]
            idx += batch_size

            futures = {executor.submit(_fetch_one, item): item for item in batch}
            for future in as_completed(futures):
                try:
                    url, resp = future.result()
                except Exception:
                    continue

                urls_fetched += 1
                visited_urls.add(url)

                if resp is None:
                    continue

                page = _process_response(resp, url, config, state)
                if page is None or page.content_hash in visited_hashes:
                    continue

                visited_hashes.add(page.content_hash)

                # Persist
                pf = pages_dir / f"{page.content_hash}.json"
                pf.write_text(json.dumps({
                    "url": page.url, "title": page.title,
                    "text": page.text[:50000],
                    "content_hash": page.content_hash,
                    "timestamp": page.timestamp,
                    "discovered_pdfs": page.discovered_pdfs,
                    "tables": page.tables,
                }, ensure_ascii=False, indent=2))

                pages_yielded += 1
                yield page

                # Collect deep links for Phase 4
                for link in page.discovered_pdfs:
                    if link not in visited_urls and link not in seen_in_queue:
                        deep_links.append(link)
                        seen_in_queue.add(link)

                if pages_yielded % 10 == 0:
                    log.info("crawl: %d pages | %d/%d URLs | %d deep links queued",
                             pages_yielded, urls_fetched, len(url_queue), len(deep_links))

            # Brief pause between batches
            time.sleep(config.rate_limit_s)

        # --- Phase 4: Deep-link PDFs ---
        if deep_links and pages_yielded < config.max_pages:
            log.info("crawl: Phase 4 — %d deep-link PDFs", len(deep_links))
            remaining = config.max_pages - pages_yielded
            dl_batch = [(ts, u) for u in deep_links[:remaining] for ts in [""]]
            futures = {executor.submit(_fetch_one, item): item for item in dl_batch}
            for future in as_completed(futures):
                try:
                    url, resp = future.result()
                except Exception:
                    continue
                if resp is None:
                    continue
                page = _process_response(resp, url, config, state)
                if page and page.content_hash not in visited_hashes:
                    visited_hashes.add(page.content_hash)
                    pf = pages_dir / f"{page.content_hash}.json"
                    pf.write_text(json.dumps({
                        "url": page.url, "title": page.title,
                        "text": page.text[:50000],
                        "content_hash": page.content_hash,
                        "timestamp": page.timestamp,
                        "discovered_pdfs": page.discovered_pdfs,
                        "tables": page.tables,
                    }, ensure_ascii=False, indent=2))
                    pages_yielded += 1
                    yield page

    _save_state(config.state_file, state)
    log.info("crawl: done. %d URLs fetched → %d unique pages, %d deep links.",
             urls_fetched, pages_yielded, len(deep_links))


# ---------------------------------------------------------------------------
# Pipeline integration — load crawled pages as TrainingExamples
# ---------------------------------------------------------------------------


def load_crawled_pages(crawl_dir: Path) -> Iterator[TrainingExample]:
    pages_dir = crawl_dir / "pages"
    if not pages_dir.exists():
        return

    page_files = sorted(pages_dir.glob("*.json"))
    log.info("crawl loader: found %d crawled pages", len(page_files))

    for pf in page_files:
        try:
            data = json.loads(pf.read_text())
            text = data.get("text", "").strip()
            url = data.get("url", "")
            title = data.get("title", "")
            if len(text) < 100:
                continue

            yield TrainingExample(
                messages=[
                    Message(role="system", content=URA_SYSTEM_PROMPT),
                    Message(role="user",
                            content=f"Summarise the following URA guidance:\n\n{text[:2000]}"),
                    Message(role="assistant",
                            content=text[:2000] if len(text) > 200 else text),
                ],
                metadata=Metadata(
                    source=url,
                    source_type=SourceType.WEB_CRAWL,
                    task=TaskType.CORPUS,
                    language="en",
                    tag=_extract_tag(url),
                    content_hash=content_hash(text[:2000], text[:2000]),
                    doc_id=data.get("content_hash", ""),
                    extra={"title": title, "url": url},
                ),
            )
        except Exception as e:
            log.warning("crawl loader: skipping %s: %s", pf.name, e)


def _extract_tag(url: str) -> str | None:
    path = urlparse(url).path.strip("/")
    parts = [p for p in path.split("/") if p and p != "en"]
    return parts[-1] if parts else None

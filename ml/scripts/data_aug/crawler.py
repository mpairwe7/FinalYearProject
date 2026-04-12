"""URA website crawler and PDF discovery (2026).

BFS crawl of ``ura.go.ug`` that:
  1. Respects robots.txt
  2. Rate-limits requests (configurable, default 1.5 s)
  3. Extracts clean text from HTML pages
  4. Discovers and downloads new PDFs (diff against existing Data/pdfs/)
  5. Stores crawl state for incremental re-runs
  6. Yields :class:`TrainingExample` for pipeline integration

Usage::

    from ml.scripts.data_aug.crawler import CrawlConfig, crawl_ura, load_crawled_pages

    cfg = CrawlConfig(pdf_output_dir=Path("Data/pdfs"))
    pages = list(crawl_ura(cfg))

    # Or wire into pipeline:
    examples = load_crawled_pages(Path("Data/crawl"))
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from ml.scripts.data_aug.schema import (
    Message,
    Metadata,
    SourceType,
    TaskType,
    TrainingExample,
    URA_SYSTEM_PROMPT,
    content_hash,
)

log = logging.getLogger(__name__)

USER_AGENT = "URA-Chatbot-Crawler/1.0 (+https://github.com/ura-chatbot; research)"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class CrawlConfig:
    seed_urls: list[str] = field(
        default_factory=lambda: [
            "https://ura.go.ug",
            "https://ura.go.ug/en/domestic-taxes",
            "https://ura.go.ug/en/customs",
            "https://ura.go.ug/en/tax-types",
            "https://ura.go.ug/en/downloads",
        ]
    )
    allowed_domains: list[str] = field(default_factory=lambda: ["ura.go.ug"])
    max_pages: int = 500
    rate_limit_s: float = 1.5
    request_timeout: int = 30
    respect_robots: bool = True
    pdf_output_dir: Path = Path("Data/pdfs")
    crawl_output_dir: Path = Path("Data/crawl")
    state_file: Optional[Path] = None  # defaults to crawl_output_dir/crawl_state.json

    def __post_init__(self):
        if self.state_file is None:
            self.state_file = self.crawl_output_dir / "crawl_state.json"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass
class CrawledPage:
    url: str
    title: str
    text: str
    content_hash: str
    timestamp: str
    discovered_pdfs: list[str] = field(default_factory=list)
    status_code: int = 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256_short(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _clean_html_text(soup: BeautifulSoup) -> str:
    """Extract clean text from a page, removing nav/footer/script."""
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    # Prefer main content area if present.
    main = soup.find("main") or soup.find("article") or soup.find(
        "div", class_=re.compile(r"content|main|article", re.I)
    )
    target = main if main else soup.body or soup

    lines = []
    for p in target.find_all(["p", "li", "h1", "h2", "h3", "h4", "td", "th"]):
        t = p.get_text(separator=" ", strip=True)
        if t and len(t) > 10:
            lines.append(t)

    return "\n".join(lines)


def _is_same_domain(url: str, allowed: list[str]) -> bool:
    host = urlparse(url).hostname or ""
    return any(host == d or host.endswith(f".{d}") for d in allowed)


def _load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"visited": {}, "pdf_hashes": {}}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------------


def crawl_ura(config: CrawlConfig) -> Iterator[CrawledPage]:
    """BFS crawl of ura.go.ug. Yields CrawledPage per successfully fetched page."""

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    # Robots.txt
    rp: Optional[RobotFileParser] = None
    if config.respect_robots:
        rp = RobotFileParser()
        rp.set_url("https://ura.go.ug/robots.txt")
        try:
            rp.read()
        except Exception:
            log.warning("Could not fetch robots.txt; proceeding without")
            rp = None

    state = _load_state(config.state_file)
    visited = set(state.get("visited", {}).keys())
    queue = list(config.seed_urls)
    pages_crawled = 0

    config.crawl_output_dir.mkdir(parents=True, exist_ok=True)
    config.pdf_output_dir.mkdir(parents=True, exist_ok=True)

    while queue and pages_crawled < config.max_pages:
        url = queue.pop(0)
        if url in visited:
            continue

        # Robots check.
        if rp and not rp.can_fetch(USER_AGENT, url):
            log.debug("robots.txt disallows: %s", url)
            visited.add(url)
            continue

        if not _is_same_domain(url, config.allowed_domains):
            continue

        try:
            time.sleep(config.rate_limit_s)
            resp = session.get(url, timeout=config.request_timeout, allow_redirects=True)
            visited.add(url)
            pages_crawled += 1

            content_type = resp.headers.get("Content-Type", "")

            # PDF discovery — download and skip HTML parsing.
            if "application/pdf" in content_type or url.lower().endswith(".pdf"):
                _handle_pdf(resp, url, config, state)
                continue

            if resp.status_code != 200 or "text/html" not in content_type:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            title = soup.title.string.strip() if soup.title and soup.title.string else url
            text = _clean_html_text(soup)

            if len(text) < 50:
                continue

            h = _sha256_short(text)

            # Discover links and PDFs.
            discovered_pdfs = []
            for a in soup.find_all("a", href=True):
                href = urljoin(url, a["href"]).split("#")[0].split("?")[0]
                if href.lower().endswith(".pdf"):
                    discovered_pdfs.append(href)
                    if href not in visited:
                        queue.append(href)
                elif _is_same_domain(href, config.allowed_domains) and href not in visited:
                    queue.append(href)

            page = CrawledPage(
                url=url,
                title=title,
                text=text,
                content_hash=h,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                discovered_pdfs=discovered_pdfs,
                status_code=resp.status_code,
            )

            # Persist page to disk.
            page_file = config.crawl_output_dir / "pages" / f"{h}.json"
            page_file.parent.mkdir(parents=True, exist_ok=True)
            page_file.write_text(json.dumps({
                "url": page.url,
                "title": page.title,
                "text": page.text,
                "content_hash": page.content_hash,
                "timestamp": page.timestamp,
                "discovered_pdfs": page.discovered_pdfs,
            }, ensure_ascii=False, indent=2))

            state["visited"][url] = {"hash": h, "timestamp": page.timestamp}
            yield page

            if pages_crawled % 25 == 0:
                _save_state(config.state_file, state)
                log.info("crawl: %d pages processed, %d in queue", pages_crawled, len(queue))

        except requests.RequestException as e:
            log.warning("crawl: failed to fetch %s: %s", url, e)
            visited.add(url)
        except Exception as e:
            log.error("crawl: unexpected error on %s: %s", url, e)
            visited.add(url)

    _save_state(config.state_file, state)
    log.info("crawl: finished. %d pages crawled total.", pages_crawled)


def _handle_pdf(resp: requests.Response, url: str, config: CrawlConfig, state: dict) -> None:
    """Download a PDF if not already present (by content hash)."""
    pdf_bytes = resp.content
    h = hashlib.sha256(pdf_bytes).hexdigest()[:32]

    if h in state.get("pdf_hashes", {}):
        log.debug("crawl: PDF already downloaded (hash match): %s", url)
        return

    # Derive filename from URL.
    parsed = urlparse(url)
    slug = parsed.path.split("/")[-1] or "page.pdf"
    if not slug.lower().endswith(".pdf"):
        slug += ".pdf"
    # Prefix with domain for provenance.
    safe_name = re.sub(r"[^\w\-.]", "_", f"{parsed.hostname}-{slug}")
    dest = config.pdf_output_dir / safe_name

    if dest.exists():
        log.debug("crawl: PDF file already exists: %s", dest)
        return

    dest.write_bytes(pdf_bytes)
    state.setdefault("pdf_hashes", {})[h] = {
        "url": url,
        "filename": safe_name,
        "size_bytes": len(pdf_bytes),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    log.info("crawl: downloaded PDF %s (%d bytes)", safe_name, len(pdf_bytes))


# ---------------------------------------------------------------------------
# Pipeline integration — load crawled pages as TrainingExamples
# ---------------------------------------------------------------------------


def load_crawled_pages(crawl_dir: Path) -> Iterator[TrainingExample]:
    """Read previously-crawled pages from Data/crawl/pages/ and yield
    TrainingExamples in the same format as load_csv_faqs or load_pdf_chunks."""

    pages_dir = crawl_dir / "pages"
    if not pages_dir.exists():
        log.info("crawl loader: no pages directory at %s", pages_dir)
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

            # Treat as corpus passage (same as PDF_CORPUS).
            yield TrainingExample(
                messages=[
                    Message(role="system", content=URA_SYSTEM_PROMPT),
                    Message(role="user", content=f"Summarise the following URA guidance:\n\n{text[:2000]}"),
                    Message(
                        role="assistant",
                        content=text[:2000] if len(text) > 200 else text,
                    ),
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


def _extract_tag(url: str) -> Optional[str]:
    """Derive a rough topic tag from the URL path."""
    path = urlparse(url).path.strip("/")
    parts = [p for p in path.split("/") if p and p != "en"]
    return parts[-1] if parts else None

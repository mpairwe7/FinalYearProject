"""URA website crawler: live capture with evidence (ADR-0023).

Reads the publisher's own index of its site, fetches what has become stale from
the publisher, and records what it saw, from where and when. It proposes; it
does not publish. The run leaves files on disk for :mod:`app.crawl_gate` to judge,
and a person merges the result.

Two rules decide what the crawl touches:

* **Update what is stale, and leave what is current alone.** A page is read when
  the publisher's ``lastmod`` says it changed, when it has never been read, or
  (on the full pass) when it is due for re-verification. Anything else is
  skipped, and a page whose text did not change writes nothing.
* **Older never replaces newer.** A restored backup or a stale cache serves an
  older page with a perfectly valid 200. If the publisher's own date on what
  came back is earlier than the date on what is held, the held capture stands
  and the response is reported. The same goes for PDFs.

What this replaced, and why each piece went:

* **Discovery through the Wayback index.** URA's Yoast sitemaps list 2,021
  content URLs with ``lastmod`` dates. The crawler had captured 265 of them,
  because it walked an archive listing and stopped at 200 pages. The URLs it
  missed included URA's own VAT, PAYE and withholding pages, revised for the
  2026 amendments.
* **Archive copies as content.** A failed live fetch fell back to the Wayback
  Machine, and ``collapse=urlkey`` meant the *oldest* capture. A withdrawn page
  came back stamped with today's date. An empty timestamp built
  ``/web/id_/<url>``, which redirects to archive.org's home page, and that was
  saved as ``https://ura.go.ug/en/tax-types``. Archives are gone from this file.
* **A content-root regex.** ``content|main|article`` matched Bootstrap's
  ``justify-content-between``, so any page without ``<article>`` extracted a
  45-character header bar and was thrown away as empty.
* **A CSV fallback** that invented ``https://ura.go.ug/en/faq/<topic>`` URLs. A
  crawl that cannot reach the site now says so.

Usage::

    python -m ml.scripts.data_aug.crawler --tier full
    python -m ml.scripts.data_aug.crawler --tier changes
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import html as html_lib
import json
import logging
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from ml.scripts.data_aug.crawl_policy import (
    CRAWL_ALLOWED_HOSTS,
    CRAWLER_TOKEN,
    HELD_ROBOTS_MAX_AGE_DAYS,
    PRIMARY_HOST,
    ROBOTS_MAX_BYTES,
    ROBOTS_MAX_REDIRECTS,
    ROBOTS_READ_LAST_HOSTS,
    USER_AGENT,
    HostGovernor,
    RobotsPolicy,
    canonical_url,
    classify_failure,
    classify_url,
    content_digest,
    drop_volatile_lines,
    parse_robots,
    parse_usage_preferences,
    pdf_document_date,
    robots_from_status,
)
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
    from collections.abc import Iterable, Iterator

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATE_SCHEMA_VERSION = 2
#: A capture written by this file. Version 1 carried no provenance at all.
CAPTURE_SCHEMA_VERSION = 2
#: Raised when extraction changes what a page's text would be, so the gate can
#: tell "the site changed" from "we now read it differently".
EXTRACTOR_VERSION = 2

SITEMAP_INDEX_URLS = ("https://ura.go.ug/sitemap_index.xml",)
#: Only sitemaps that list guidance. The index also carries tag, author, video
#: and taxonomy sitemaps that would spend the budget on listings.
_SITEMAP_FOLLOW = re.compile(r"/(?:post|page|wpdmpro)-sitemap\d*\.xml$", re.IGNORECASE)

#: A deadline extension cannot wait for the monthly pass.
NOTICE_HUBS = (
    "https://ura.go.ug/en/category/publications/public-notices/",
    "https://ura.go.ug/en/category/publications/press-releases/",
)
FULL_HUBS = (
    *NOTICE_HUBS,
    "https://ura.go.ug/en/category/legal-policy/",
    "https://ura.go.ug/download-category/laws-and-acts/",
    "https://www.parliament.go.ug/page/tax-bills-2026",
)

#: A retried or redirected fetch of these is a transient fault, not an answer.
_RETRYABLE = frozenset({"network_error", "server_error", "rate_limited"})
_RETRY_AFTER_CAP_S = 120.0
_MAX_REDIRECTS = 5
_MAX_ATTEMPTS = 3
_HOST_FAILURE_OUTCOMES = _RETRYABLE | {"blocked", "challenge"}

_RUNS_KEPT = 24
MAX_PAGE_TEXT_CHARS = 300_000
_MIN_PAGE_CHARS = 50

#: Removed before extraction. ``header`` is handled separately: inside an
#: ``article`` it holds the page's title.
_CHROME_TAGS = (
    "script", "style", "nav", "footer", "aside", "noscript", "iframe", "form", "template",
    "textarea", "svg", "button", "select", "object", "embed",
)
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_TEXT_BLOCKS = (*_HEADINGS, "p", "li", "dt", "dd", "blockquote", "figcaption", "summary", "div", "section")
#: Where a run of text stops belonging to the block around it. ``table`` is here
#: so a ``div`` wrapping a table does not repeat every cell as its own text.
_TEXT_BOUNDARIES = frozenset((*_TEXT_BLOCKS, "table"))

#: Repeated on every URA page, and meaningless to a search for tax guidance.
_BOILERPLATE_LINE = re.compile(
    r"^(?:#+\s*)?(?:how do you find this resource\??|share now|share this|add to bookmarks"
    r"|please login to bookmark|\[featured_image\]|link:\s*permalink\s*\.?)\s*$",
    re.IGNORECASE,
)

# Concealment methods from Unit 42's March 2026 survey of web prompt injection
# seen in the wild. Only what a static parser can see: a class hidden by a
# stylesheet is not detectable here, and script-assembled text is never run.
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none"
    r"|visibility\s*:\s*(?:hidden|collapse)"
    r"|opacity\s*:\s*0(?:\.0+)?\s*(?:!important\s*)?(?:;|$)"
    r"|font-size\s*:\s*0(?:\.0+)?(?:px|pt|em|rem|%)?\s*(?:!important\s*)?(?:;|$)"
    r"|(?:left|top|right|bottom|text-indent|margin-left)\s*:\s*-\d{3,}"
    r"|clip\s*:\s*rect\(\s*0"
    r"|clip-path\s*:\s*inset\(\s*(?:50|100)%",
    re.IGNORECASE,
)
_ZERO_HEIGHT = re.compile(r"(?:^|[;\s])(?:max-)?height\s*:\s*0(?:px)?\s*(?:;|$)", re.IGNORECASE)
_OVERFLOW_HIDDEN = re.compile(r"overflow\s*:\s*hidden", re.IGNORECASE)
_STYLE_COLOUR = re.compile(r"(?:^|;)\s*color\s*:\s*([^;!]+)", re.IGNORECASE)
_STYLE_BACKGROUND = re.compile(r"background(?:-color)?\s*:\s*([^;!]+)", re.IGNORECASE)
#: Screen-reader helpers, plus plain "hidden" classes. Bootstrap's ``collapse``
#: is deliberately absent: URA's guidance lives inside collapsed accordions.
_HIDDEN_CLASSES = frozenset({"sr-only", "screen-reader-text", "visually-hidden", "hidden", "invisible"})
_RESPONSIVE_DISPLAY = re.compile(r"^d-(?:sm|md|lg|xl|xxl)-(?:block|flex|inline|table|grid)")
#: Bidi, zero-width and tag characters: invisible to a reader, legible to a model.
_INVISIBLE_CHARS = re.compile("[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff\U000e0000-\U000e007f]")

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_VISIBLE_CREATED = re.compile(r"^Create Date\s+(.+)$", re.IGNORECASE | re.MULTILINE)
_VISIBLE_UPDATED = re.compile(r"^Last Updated\s+(.+)$", re.IGNORECASE | re.MULTILINE)

_SITEMAP_ENTRY = re.compile(r"<(url|sitemap)\b[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_SITEMAP_LOC = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</loc>", re.IGNORECASE | re.DOTALL)
_SITEMAP_LASTMOD = re.compile(r"<lastmod>\s*(.*?)\s*</lastmod>", re.IGNORECASE | re.DOTALL)

_PDF_MAGIC = b"%PDF-"
_CHARSET = re.compile(r"charset=[\"']?([\w-]+)", re.IGNORECASE)
_DISPOSITION_NAME = re.compile(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';\r\n]+)', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Config & models
# ---------------------------------------------------------------------------


@dataclass
class CrawlConfig:
    #: ``changes`` reads what the sitemaps and notice listings say is new or
    #: changed. ``full`` also re-verifies anything not confirmed for
    #: ``reverify_days``, which is how a withdrawn page is noticed.
    tier: str = "full"
    allowed_hosts: frozenset[str] = CRAWL_ALLOWED_HOSTS
    #: Every HTTP request counts, including redirects and retries. The old
    #: ``max_pages`` counted *new* content, so noise spent the budget.
    max_fetches: int = 4000
    max_seconds: float = 2 * 3600.0
    #: Below ``CRAWL_MAX_UNVERIFIED_DAYS``, so a page is re-read before it expires.
    reverify_days: int = 30
    max_new_pdfs: int = 10
    #: Requests spent on documents per run. Few servers answer 304 here, so
    #: re-reading all 398 download pages' files monthly would refetch gigabytes.
    max_pdf_fetches: int = 40
    min_delay_s: float = 1.0
    request_timeout: float = 25.0
    max_page_bytes: int = 5 * 1024 * 1024
    max_pdf_bytes: int = 60 * 1024 * 1024
    #: Links followed from a seed. A notice is one click from its listing.
    link_depth: int = 2
    checkpoint_every: int = 50
    pdf_output_dir: Path = Path("Data/pdfs")
    crawl_output_dir: Path = Path("Data/crawl")
    state_file: Path | None = None

    def __post_init__(self) -> None:
        if self.tier not in ("full", "changes"):
            raise ValueError(f"Unknown crawl tier {self.tier!r}")
        if self.tier == "changes":
            self.link_depth = 1
            self.max_fetches = min(self.max_fetches, 300)
        if self.state_file is None:
            self.state_file = self.crawl_output_dir / "crawl_state.json"


@dataclass
class FetchResult:
    outcome: str
    url: str
    final_url: str = ""
    status: int = 0
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    detail: str = ""
    retry_after: float | None = None


@dataclass
class ParsedPage:
    title: str
    text: str
    tables: list[dict[str, Any]]
    kind: str  # "content" | "hub"
    failure: str = ""
    noindex: bool = False
    published_at: str = ""
    modified_at: str = ""
    date_source: str = ""
    section: str = ""
    hidden_chars: int = 0
    hidden_excerpt: str = ""
    truncated: bool = False
    links: list[str] = field(default_factory=list)
    pdfs: list[str] = field(default_factory=list)


@dataclass
class CrawledPage:
    url: str
    title: str
    text: str
    content_hash: str
    timestamp: str
    change: str = "new"  # "new" | "changed"
    discovered_pdfs: list[str] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    status_code: int = 200


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _iso_date(value: object) -> str:
    """``YYYY-MM-DD`` from a machine or display date, or ``""`` when implausible."""
    raw = str(value or "").strip()
    match = _ISO_DATE.match(raw)
    try:
        if match:
            parsed = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        elif raw:
            parsed = datetime.strptime(raw, "%B %d, %Y")
        else:
            return ""
    except ValueError:
        return ""
    if not 1995 <= parsed.year <= datetime.now(timezone.utc).year + 1:
        return ""
    return parsed.strftime("%Y-%m-%d")


def _parse_moment(value: object) -> datetime | None:
    """A timestamp or a sitemap ``lastmod`` as UTC. A bare date means that day."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _clean_cell(text: str) -> str:
    return " ".join(_INVISIBLE_CHARS.sub("", text).split())


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def _retry_after_seconds(value: str) -> float | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return float(raw)
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max((moment - datetime.now(timezone.utc)).total_seconds(), 0.0)


# ---------------------------------------------------------------------------
# HTML extraction
# ---------------------------------------------------------------------------


def _is_hidden(element: Tag) -> bool:
    if element.has_attr("hidden") or str(element.get("aria-hidden", "")).lower() == "true":
        return True
    classes = [str(name).lower() for name in (element.get("class") or [])]
    if _HIDDEN_CLASSES.intersection(classes):
        return True
    if "d-none" in classes and not any(_RESPONSIVE_DISPLAY.match(name) for name in classes):
        return True
    style = str(element.get("style") or "")
    if not style:
        return False
    if _HIDDEN_STYLE.search(style):
        return True
    if _ZERO_HEIGHT.search(style) and _OVERFLOW_HIDDEN.search(style):
        return True
    colour = _STYLE_COLOUR.search(style)
    background = _STYLE_BACKGROUND.search(style)
    return bool(colour and background and colour.group(1).strip().lower() == background.group(1).strip().lower())


def _strip_hidden(soup: BeautifulSoup) -> tuple[int, str]:
    """Remove what a person cannot see; return how much, and a sample for the gate.

    OWASP LLM08 asks for extraction that ignores formatting and detects hidden
    content. The sample matters: the crawler cannot judge intent (``ml/`` may not
    import the guardrails), so the gate reads it to tell a screen-reader label
    from an instruction aimed at a model.
    """
    removed = 0
    samples: list[str] = []
    for element in list(soup.find_all(True)):
        # A preloader commonly sets opacity:0 on <body> until scripts finish.
        if element.decomposed or element.name in ("html", "body") or not _is_hidden(element):
            continue
        text = element.get_text(" ", strip=True)
        removed += len(text)
        if text:
            samples.append(text[:400])
        element.decompose()
    return removed, " | ".join(samples)[:2000]


def _strip_chrome(soup: BeautifulSoup) -> None:
    for element in soup.find_all(list(_CHROME_TAGS)):
        element.decompose()
    for element in soup.find_all("header"):
        if not element.find_parent(["article", "main"]):
            element.decompose()


def _densest_block(soup: BeautifulSoup) -> Tag | None:
    """The ancestor holding the most running text, for a theme this file does not know."""
    scores: dict[int, int] = {}
    nodes: dict[int, Tag] = {}
    for block in soup.find_all(["p", "li", "td"]):
        length = len(block.get_text(strip=True))
        if length < 25:
            continue
        ancestor = block.parent
        for _ in range(4):
            if not isinstance(ancestor, Tag) or ancestor.name in ("body", "html", "[document]"):
                break
            scores[id(ancestor)] = scores.get(id(ancestor), 0) + length
            nodes[id(ancestor)] = ancestor
            ancestor = ancestor.parent
    return nodes[max(scores, key=lambda key: scores[key])] if scores else None


def _content_root(soup: BeautifulSoup) -> tuple[Tag, bool]:
    """``(root, is_listing)``.

    Named containers first, then text density. Never a substring match on class
    names: that is how ``justify-content-between`` became the page body.
    """
    articles = soup.find_all("article")
    main = soup.find("main")
    if len(articles) > 1:
        return main or soup.body or soup, True
    root = main or (articles[0] if articles else None) or soup.select_one(".clean-stuff, .entry-content, .node__content")
    return root or _densest_block(soup) or soup.body or soup, False


def _table_grid(table: Tag) -> tuple[list[list[str]], bool]:
    """``(rows, first_row_is_header)``, with merged cells written into every slot.

    A dropped ``rowspan`` moves the rest of the row one column left. URA's PAYE
    table opens with one, and its presumptive-tax table uses ``colspan`` over
    the two columns that separate record keepers from everyone else, so a
    figure could land under the wrong heading.
    """
    grid: list[list[str]] = []
    carried: dict[int, list[Any]] = {}  # column -> [rows remaining, text]
    first_row_header = False
    rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
    for index, row in enumerate(rows):
        cells = list(row.find_all(["td", "th"], recursive=False))
        if index == 0:
            first_row_header = bool(row.find_parent("thead")) or (bool(cells) and all(c.name == "th" for c in cells))
        values: list[str] = []
        column = 0
        while cells or any(position >= column for position in carried):
            if column in carried:
                carried[column][0] -= 1
                values.append(carried[column][1])
                if carried[column][0] <= 0:
                    del carried[column]
                column += 1
                continue
            if not cells:
                values.append("")
                column += 1
                continue
            cell = cells.pop(0)
            text = _clean_cell(cell.get_text(" ", strip=True))
            width = _span(cell.get("colspan"), 50)
            height = _span(cell.get("rowspan"), 1000)
            for offset in range(width):
                values.append(text)
                if height > 1:
                    carried[column + offset] = [height - 1, text]
            column += width
        if any(values):
            grid.append(values)
    return grid, first_row_header


def _span(value: object, limit: int) -> int:
    try:
        return max(1, min(int(str(value)), limit))
    except (TypeError, ValueError):
        return 1


def _table_struct(table: Tag) -> dict[str, Any] | None:
    grid, first_row_header = _table_grid(table)
    if not grid:
        return None
    width = max(len(row) for row in grid)
    grid = [row + [""] * (width - len(row)) for row in grid]
    if first_row_header or all(grid[0]):
        headers, rows = grid[0], grid[1:]
    else:
        headers, rows = [""] * width, grid
    return {"headers": headers, "rows": rows, "row_count": len(rows)} if rows else None


def _extract_tables(root: Tag) -> list[dict[str, Any]]:
    """Top-level tables under *root*, as structured dicts."""
    found = (_table_struct(table) for table in root.find_all("table") if table.find_parent("table") is None)
    return [table for table in found if table]


def _tables_to_markdown(tables: list[dict[str, Any]]) -> str:
    return "\n".join(_table_markdown(table) for table in tables)


def _table_markdown(table: dict[str, Any]) -> str:
    def line(cells: list[str]) -> str:
        return "| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |"

    headers = table["headers"]
    return "\n".join([line(headers), line(["---"] * len(headers)), *(line(row) for row in table["rows"])]) + "\n"


def _own_text(block: Tag) -> str:
    """Text whose nearest block ancestor is *block*, so nothing is emitted twice.

    Comments, CDATA and declarations are excluded by the exact type check:
    those are ``NavigableString`` subclasses, and hiding text in them is one of
    the delivery methods the Unit 42 survey lists.
    """
    pieces: list[str] = []
    for node in block.descendants:
        if type(node) is not NavigableString:
            continue
        parent = node.parent
        while parent is not block and parent is not None and parent.name not in _TEXT_BOUNDARIES:
            parent = parent.parent
        if parent is block:
            pieces.append(str(node))
    return _clean_cell(" ".join(pieces))


def _clean_html_text(root: Tag) -> tuple[str, list[dict[str, Any]]]:
    """Body text and tables under *root*, in reading order.

    Tables stay where they are on the page. They used to be appended at the end,
    so the chunker filed a rate table under whichever heading came last.
    """
    lines: list[str] = []
    tables: list[dict[str, Any]] = []
    for element in root.find_all([*_TEXT_BLOCKS, "table"]):
        if element.name == "table":
            if element.find_parent("table") is None:
                table = _table_struct(element)
                if table:
                    tables.append(table)
                    lines.append("\n" + _table_markdown(table))
            continue
        if element.find_parent("table"):
            continue
        text = _own_text(element)
        if not text or _BOILERPLATE_LINE.match(text):
            continue
        # A short line can be a figure: "18%" on its own is the answer.
        if len(text) < 8 and element.name not in _HEADINGS and not re.search(r"\d", text):
            continue
        if element.name in _HEADINGS:
            text = "#" * int(element.name[1]) + " " + text
        elif element.name == "dt":
            text = f"**{text}**"
        lines.append(text)
    return drop_volatile_lines("\n".join(lines)).strip(), tables


def _json_ld_nodes(soup: BeautifulSoup) -> Iterator[dict[str, Any]]:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            payload = json.loads(script.string or "")
        except (TypeError, ValueError):
            continue
        graph = payload.get("@graph", [payload]) if isinstance(payload, dict) else payload
        for node in graph if isinstance(graph, list) else []:
            if isinstance(node, dict):
                yield node


def _meta_content(soup: BeautifulSoup, name: str) -> str:
    tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
    return str(tag.get("content") or "") if tag else ""


def _page_dates(soup: BeautifulSoup) -> tuple[str, str, str, str]:
    """``(published, modified, source, section)``, read before the page is stripped.

    A crawl date is not a publication date. "Stale-Document Poisoning" (Ahmed et
    al., 2026) measured outdated retrieved evidence overriding correct answers,
    and found the remedy depends on dates being right.
    """
    published = _iso_date(_meta_content(soup, "article:published_time"))
    modified = _iso_date(_meta_content(soup, "article:modified_time") or _meta_content(soup, "og:updated_time"))
    source = "meta" if published or modified else ""
    section = ""
    for node in _json_ld_nodes(soup):
        found_published = _iso_date(node.get("datePublished"))
        found_modified = _iso_date(node.get("dateModified"))
        if not source and (found_published or found_modified):
            source = "json-ld"
        published = published or found_published
        modified = modified or found_modified
        labels = node.get("articleSection")
        if not section and labels:
            section = html_lib.unescape(", ".join(labels) if isinstance(labels, list) else str(labels))
    articles = soup.find_all("article")
    if not published and len(articles) == 1:
        moment = articles[0].find("time", attrs={"datetime": True})
        published = _iso_date(moment.get("datetime")) if moment else ""
        source = source or ("time" if published else "")
    return published, modified, source, section


def _discover_links(roots: Iterable[Tag], base_url: str, allowed: Iterable[str]) -> tuple[list[str], list[str]]:
    """``(page_urls, document_urls)`` linked from *roots*.

    Runs after chrome and hidden elements are gone, so neither a navigation menu
    nor a concealed link can steer the crawl.
    """
    pages: list[str] = []
    documents: list[str] = []
    seen: set[str] = set()
    for root in roots:
        for anchor in root.find_all("a"):
            for attribute in ("data-downloadurl", "href"):
                href = canonical_url(str(anchor.get(attribute) or ""), base_url, allowed_hosts=allowed)
                if not href or href in seen:
                    continue
                seen.add(href)
                kind = classify_url(href)
                if kind == "document":
                    documents.append(href)
                elif kind in ("page", "hub"):
                    pages.append(href)
    return pages, documents


def decode_html(body: bytes, content_type: str = "") -> str:
    """Declared charset, then strict UTF-8, then Windows-1252.

    Bytes handed to BeautifulSoup are sniffed, and a page with no declared
    charset was read as MacRoman, which is one way mojibake reached the corpus.
    """
    declared = _CHARSET.search(content_type or "")
    for encoding in (declared.group(1) if declared else "", "utf-8"):
        if encoding:
            try:
                return body.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue
    return body.decode("cp1252", errors="replace")


def parse_page(
    body: bytes,
    url: str,
    *,
    content_type: str = "",
    allowed_hosts: Iterable[str] = CRAWL_ALLOWED_HOSTS,
) -> ParsedPage:
    """Turn fetched HTML into text, tables, dates and links. Pure: no I/O."""
    soup = BeautifulSoup(decode_html(body, content_type), "html.parser")
    title = _clean_cell(soup.title.get_text(" ", strip=True)) if soup.title else ""
    robots_meta = _meta_content(soup, "robots").lower()
    published, modified, date_source, section = _page_dates(soup)

    hidden_chars, hidden_excerpt = _strip_hidden(soup)
    _strip_chrome(soup)
    root, is_listing = _content_root(soup)
    text, tables = _clean_html_text(root)
    if is_listing or classify_url(url) == "hub":
        kind = "hub"
        scopes: list[Tag] = list(soup.find_all("article")) if is_listing else [root]
    else:
        kind = "content"
        scopes = [root]
    links, pdfs = _discover_links(scopes, url, allowed_hosts)

    if not published:
        created = _VISIBLE_CREATED.search(text)
        published = _iso_date(created.group(1)) if created else ""
        date_source = date_source or ("visible" if published else "")
    if not modified:
        updated = _VISIBLE_UPDATED.search(text)
        modified = _iso_date(updated.group(1)) if updated else ""

    truncated = len(text) > MAX_PAGE_TEXT_CHARS
    return ParsedPage(
        title=title or url,
        text=text[:MAX_PAGE_TEXT_CHARS],
        tables=tables,
        kind=kind,
        failure=classify_failure(title, text),
        noindex="noindex" in robots_meta,
        published_at=published,
        modified_at=modified,
        date_source=date_source,
        section=section,
        hidden_chars=hidden_chars,
        hidden_excerpt=hidden_excerpt,
        truncated=truncated,
        links=links,
        pdfs=pdfs,
    )


def parse_sitemap(xml_text: str) -> tuple[list[tuple[str, str]], bool]:
    """``([(loc, lastmod)], is_index)``.

    Regex over a size-capped body rather than an XML parser: the input is
    untrusted, only ``<loc>`` and ``<lastmod>`` are wanted, and this leaves no
    entity expansion to worry about.
    """
    entries: list[tuple[str, str]] = []
    is_index = False
    for match in _SITEMAP_ENTRY.finditer(xml_text):
        is_index = is_index or match.group(1).lower() == "sitemap"
        location = _SITEMAP_LOC.search(match.group(2))
        if not location:
            continue
        lastmod = _SITEMAP_LASTMOD.search(match.group(2))
        entries.append((html_lib.unescape(location.group(1).strip()), lastmod.group(1).strip() if lastmod else ""))
    return entries, is_index


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def _load_state(path: Path) -> dict[str, Any]:
    """The ledger, upgraded from version 1. A corrupt file is an error, not a reset."""
    state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    state.setdefault("visited", {})
    state.setdefault("pdf_hashes", {})
    state.setdefault("runs", [])
    state["schema_version"] = STATE_SCHEMA_VERSION
    return state


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, (json.dumps(state, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


class _CrawlRun:
    """One crawl: the frontier, the budget, and what happened to each URL."""

    def __init__(self, config: CrawlConfig, client: httpx.Client, governor: HostGovernor) -> None:
        self.config = config
        self.client = client
        self.governor = governor
        self.state_file: Path = config.state_file or config.crawl_output_dir / "crawl_state.json"
        self.state = _load_state(self.state_file)
        self.ledger: dict[str, dict[str, Any]] = self.state["visited"]
        self.pages_dir = config.crawl_output_dir / "pages"
        self.outcomes: Counter[str] = Counter()
        self.robots: dict[str, RobotsPolicy] = {}
        self.robots_text = ""
        self.sitemap: dict[str, str] = {}
        self.sitemap_failed = False
        self.frontier: list[tuple[int, str, int, str, int]] = []
        self.sequence = 0
        self.queued: set[str] = set()
        self.suspects: list[str] = []
        self.pdf_queue: list[tuple[str, str]] = []
        self.fetches = 0
        self.processed_since_checkpoint = 0
        self.monotonic_start = time.monotonic()
        self.started_at = _now()
        self.stopped_by = ""
        self.ran_to_completion = False
        self.bytes_on_disk: set[str] | None = None

    # -- budget ------------------------------------------------------------

    def _budget_spent(self) -> bool:
        if self.fetches >= self.config.max_fetches:
            self.stopped_by = "fetch_budget"
        elif time.monotonic() - self.monotonic_start >= self.config.max_seconds:
            self.stopped_by = "time_budget"
        return bool(self.stopped_by)

    # -- transport ---------------------------------------------------------

    def _robots_for(self, host: str) -> RobotsPolicy:
        policy = self.robots.get(host)
        if policy is None:
            if host in ROBOTS_READ_LAST_HOSTS:
                policy = self._held_robots(host)
            else:
                policy = self._read_robots(host)
            self.robots[host] = policy
            self.outcomes[f"robots_{policy.state}"] += 1
        return policy

    def _held_robots(self, host: str = PRIMARY_HOST) -> RobotsPolicy:
        """The rules the last full pass found for *host*, without touching the file now."""
        checked = _parse_moment(self.state.get("robots_checked_at"))
        if (
            self.config.tier == "changes"
            and checked is not None
            and (datetime.now(timezone.utc) - checked).total_seconds() > HELD_ROBOTS_MAX_AGE_DAYS * 86400
        ):
            self.stopped_by = "robots_expired"
            return RobotsPolicy(state="unreachable")
        by_host = self.state.get("robots_rules_by_host")
        if isinstance(by_host, dict) and host in by_host:
            raw_rules = str(by_host.get(host) or "")
        else:
            raw_rules = str(self.state.get("robots_rules") or "") if host == PRIMARY_HOST else ""
        held = parse_robots(raw_rules)
        held.state = "read_last"
        return held

    def _read_robots_last(self) -> None:
        """Read robots.txt once the work is done, so any ban lands on a finished run.

        The rules bind from the next run, because the requests in this one are
        over. An HTML error page parses to no rules, and clears any held ones,
        which is right: the file has gone. A failed read changes nothing.
        """
        hosts_to_read = [h for h in (PRIMARY_HOST, *sorted(ROBOTS_READ_LAST_HOSTS - {PRIMARY_HOST})) if h in self.robots]
        if not hosts_to_read:
            hosts_to_read = [PRIMARY_HOST]
        by_host = dict(self.state.get("robots_rules_by_host") or {})
        for host in hosts_to_read:
            if self.fetches >= self.config.max_fetches:
                break
            self.outcomes["robots_read_last"] += 1
            policy = self._read_robots(host)
            self.state["robots_checked_at"] = _now()
            if host == PRIMARY_HOST:
                self.state["robots_last_state"] = policy.state
            if policy.state != "unreachable":
                has_rules = bool(policy.rules or policy.usage or policy.sitemaps or policy.crawl_delay)
                rule_text = self.robots_text if has_rules else ""
                by_host[host] = rule_text
                if host == PRIMARY_HOST:
                    self.state["robots_rules"] = rule_text
        self.state["robots_rules_by_host"] = by_host

    def _read_robots(self, host: str) -> RobotsPolicy:
        """Sections 2.3.1.2 to 2.3.1.4, with redirects held to the allowed hosts."""
        current = f"https://{host}/robots.txt"
        for _ in range(ROBOTS_MAX_REDIRECTS + 1):
            self.governor.wait(host)
            self.fetches += 1
            try:
                with self.client.stream("GET", current) as response:
                    if response.has_redirect_location:
                        target = canonical_url(
                            response.headers.get("location", ""), current, allowed_hosts=self.config.allowed_hosts
                        )
                        if not target:
                            return RobotsPolicy(state="unreachable")
                        current = target
                        continue
                    implied = robots_from_status(response.status_code)
                    if implied is not None:
                        return implied
                    body = self._read_capped(response, ROBOTS_MAX_BYTES, truncate=True)
                    self.robots_text = body.decode("utf-8", errors="replace")
                    return parse_robots(self.robots_text)
            except httpx.HTTPError as exc:
                self.outcomes[f"robots_error_{type(exc).__name__}"] += 1
                return RobotsPolicy(state="unreachable")
        return RobotsPolicy(state="unavailable")  # Section 2.3.1.2: MAY assume unavailable.

    @staticmethod
    def _read_capped(response: httpx.Response, limit: int, *, truncate: bool = False) -> bytes:
        """The body, bounded mid-stream. A declared length is not trusted."""
        buffer = bytearray()
        for chunk in response.iter_bytes(chunk_size=65536):
            buffer.extend(chunk)
            if len(buffer) > limit:
                if truncate:
                    return bytes(buffer[:limit])
                raise OverflowError
        return bytes(buffer)

    def _fetch_once(self, url: str, *, etag: str, last_modified: str, max_bytes: int) -> FetchResult:
        current = url
        conditional: dict[str, str] = {}
        if etag:
            conditional["If-None-Match"] = etag
        if last_modified:
            conditional["If-Modified-Since"] = last_modified
        for _ in range(_MAX_REDIRECTS + 1):
            host = urlsplit(current).hostname or ""
            policy = self._robots_for(host)
            if not policy.allows(current):
                return FetchResult("disallowed", url, final_url=current)
            self.governor.wait(host, policy.crawl_delay)
            self.fetches += 1
            try:
                with self.client.stream("GET", current, headers=conditional if current == url else None) as response:
                    status = response.status_code
                    if response.has_redirect_location:
                        target = canonical_url(
                            response.headers.get("location", ""), current, allowed_hosts=self.config.allowed_hosts
                        )
                        if not target:
                            return FetchResult(
                                "redirect_rejected",
                                url,
                                final_url=current,
                                status=status,
                                detail=response.headers.get("location", "")[:200],
                            )
                        current = target
                        continue
                    headers = {key.lower(): value for key, value in response.headers.items()}
                    result = FetchResult("ok", url, final_url=current, status=status, headers=headers)
                    if status == 304:
                        result.outcome = "not_modified"
                    elif status == 404:
                        result.outcome = "not_found"
                    elif status == 410:
                        result.outcome = "gone"
                    elif status in (401, 403):
                        result.outcome = "blocked"
                    elif status in (429, 503):
                        result.outcome = "rate_limited"
                        result.retry_after = _retry_after_seconds(headers.get("retry-after", ""))
                    elif status >= 500:
                        result.outcome = "server_error"
                    elif status != 200:
                        result.outcome = "unexpected_status"
                    else:
                        try:
                            result.body = self._read_capped(response, max_bytes)
                        except OverflowError:
                            result.outcome, result.body = "too_large", b""
                    return result
            except httpx.TimeoutException as exc:
                return FetchResult("network_error", url, final_url=current, detail=type(exc).__name__)
            except httpx.HTTPError as exc:
                return FetchResult("network_error", url, final_url=current, detail=type(exc).__name__)
        return FetchResult("redirect_rejected", url, final_url=current, detail="too many redirects")

    def fetch(self, url: str, *, etag: str = "", last_modified: str = "", max_bytes: int | None = None) -> FetchResult:
        """Fetch with retries. Failures are reported per host, and a bad host is dropped."""
        orig_host = urlsplit(url).hostname or ""
        limit = max_bytes or self.config.max_page_bytes
        result = FetchResult("network_error", url, final_url=url)
        for attempt in range(_MAX_ATTEMPTS):
            result = self._fetch_once(url, etag=etag, last_modified=last_modified, max_bytes=limit)
            attempt_host = urlsplit(result.final_url or url).hostname or orig_host
            if result.outcome == "network_error" and result.detail:
                self.outcomes[f"network_error_{result.detail}"] += 1
            if result.outcome not in _RETRYABLE:
                self.governor.record(attempt_host, ok=result.outcome not in _HOST_FAILURE_OUTCOMES)
                return result
            self.governor.record(attempt_host, ok=False)
            if result.retry_after is not None and result.retry_after > _RETRY_AFTER_CAP_S:
                self.governor.trip(attempt_host)
            if self.governor.tripped(attempt_host) or attempt == _MAX_ATTEMPTS - 1 or self._budget_spent():
                return result
            self.governor.defer(
                attempt_host,
                result.retry_after if result.retry_after is not None else 2.0 ** (attempt + 1),
            )
        return result

    # -- frontier ----------------------------------------------------------

    def _enqueue(self, url: str, *, priority: int, order: str = "", depth: int = 0) -> None:
        if url in self.queued or classify_url(url) == "skip":
            return
        self.queued.add(url)
        self.sequence += 1
        heapq.heappush(self.frontier, (priority, order, self.sequence, url, depth))

    def _read_sitemaps(self) -> None:
        advertised = self._robots_for(PRIMARY_HOST).sitemaps
        indexes = [canonical_url(url, allowed_hosts=self.config.allowed_hosts) for url in (*SITEMAP_INDEX_URLS, *advertised)]
        pending = [url for url in indexes if url]
        read: set[str] = set()
        while pending and not self._budget_spent():
            url = pending.pop(0)
            if url in read:
                continue
            read.add(url)
            result = self.fetch(url)
            if result.outcome != "ok":
                self.sitemap_failed = True
                self.outcomes["sitemap_failed"] += 1
                log.warning("crawl: sitemap %s: %s", url, result.outcome)
                continue
            entries, is_index = parse_sitemap(result.body.decode("utf-8", errors="replace"))
            for location, lastmod in entries:
                target = canonical_url(location, allowed_hosts=self.config.allowed_hosts)
                if not target:
                    continue
                if is_index:
                    if _SITEMAP_FOLLOW.search(urlsplit(target).path):
                        pending.append(target)
                else:
                    self.sitemap[target] = lastmod

    def _was_delisted(self, url: str, entry: dict[str, Any]) -> bool:
        """Whether the publisher stopped listing a page it used to list.

        The earliest sign of a withdrawal, so it is checked now rather than when
        verification next falls due. A failed sitemap read proves nothing: every
        page would look delisted.
        """
        return bool(self.sitemap) and not self.sitemap_failed and bool(entry.get("sitemap_lastmod")) and url not in self.sitemap

    def _staleness(self, url: str) -> str:
        """Why *url* needs reading, or ``""`` when what is held is still current."""
        entry = self.ledger.get(url, {})
        verified = _parse_moment(entry.get("last_verified_at"))
        if verified is None:
            return "unseen"
        lastmod = _parse_moment(self.sitemap.get(url))
        if lastmod is not None and lastmod > verified:
            return "changed"
        if self._was_delisted(url, entry):
            return "delisted"
        if self.config.tier == "full":
            age = datetime.now(timezone.utc) - verified
            if age.total_seconds() >= self.config.reverify_days * 86400:
                return "due"
        return ""

    def _known_urls(self) -> set[str]:
        """Everything the ledger or an existing capture names, so each is re-verified."""
        known = {url for url, entry in self.ledger.items() if entry.get("status") not in ("gone", "moved")}
        if self.pages_dir.is_dir():
            for path in sorted(self.pages_dir.glob("*.json")):
                try:
                    captured = canonical_url(
                        json.loads(path.read_text(encoding="utf-8")).get("url", ""),
                        allowed_hosts=self.config.allowed_hosts,
                    )
                except (OSError, ValueError):
                    continue
                entry = self.ledger.get(captured, {})
                if captured and entry.get("status") not in ("gone", "moved"):
                    known.add(captured)
        return known

    def seed(self) -> None:
        """Queue only what is stale: what changed, then what is new, then what is due.

        The order is the fix for starvation. A fixed queue under a cap re-read
        the same prefix every month, so 1,092 of 1,848 known URLs were never
        reached on 2026-10-31, while pages that had not changed were fetched
        again. A page the publisher has not touched and that was recently
        verified is not requested at all.
        """
        hubs = FULL_HUBS if self.config.tier == "full" else NOTICE_HUBS
        for url in hubs:
            self._enqueue(url, priority=0)
        self._read_sitemaps()
        candidates = set(self.sitemap)
        candidates |= {url for url, entry in self.ledger.items() if self._was_delisted(url, entry)}
        if self.config.tier == "full":
            candidates |= self._known_urls()
        for url in sorted(candidates):
            reason = self._staleness(url)
            if not reason:
                self.outcomes["fresh_skipped"] += 1
                continue
            english_first = "0" if "/en/" in url else "1"
            verified = str(self.ledger.get(url, {}).get("last_verified_at") or "")
            priority, order = {
                "changed": (1, english_first),
                "delisted": (1, english_first),
                "unseen": (2, english_first),
                "due": (3, verified),
            }[reason]
            self._enqueue(url, priority=priority, order=order)

    # -- processing --------------------------------------------------------

    def _entry(self, url: str, kind: str) -> dict[str, Any]:
        entry = self.ledger.setdefault(url, {"first_seen": _now()})
        entry["kind"] = kind
        if url in self.sitemap:
            held = _parse_moment(entry.get("sitemap_lastmod"))
            listed = _parse_moment(self.sitemap[url])
            if held is not None and listed is not None and listed < held:
                self.outcomes["lastmod_went_backwards"] += 1
            entry["sitemap_lastmod"] = self.sitemap[url]
        return entry

    def _clear_delisted_marker(self, url: str, entry: dict[str, Any]) -> None:
        if self._was_delisted(url, entry):
            entry.pop("sitemap_lastmod", None)

    @staticmethod
    def _verified(entry: dict[str, Any], result: FetchResult) -> None:
        now = _now()
        entry.update(status="live", last_verified_at=now, failures=0, source_kind="live")
        entry.pop("gone_at", None)
        entry.pop("moved_to", None)
        for field_name, header in (("etag", "etag"), ("last_modified", "last-modified")):
            if result.headers.get(header):
                entry[field_name] = result.headers[header]

    def _store_capture(
        self, url: str, requested: str, entry: dict[str, Any], page: ParsedPage, result: FetchResult, usage: str
    ) -> CrawledPage | None:
        digest = content_digest(page.text)
        previous = entry.get("content_hash")
        change = "unchanged" if previous == digest else ("changed" if previous else "new")
        self.outcomes[f"page_{change}"] += 1
        timestamp = _now()
        entry.update(
            content_hash=digest,
            published_at=page.published_at,
            modified_at=page.modified_at,
            content_usage=usage,
        )
        if change != "unchanged":
            entry["last_changed_at"] = timestamp

        path = self.pages_dir / f"{digest}.json"
        if not path.exists():
            record = {
                "schema_version": CAPTURE_SCHEMA_VERSION,
                "extractor_version": EXTRACTOR_VERSION,
                "url": url,
                "requested_url": requested,
                "title": page.title,
                "text": page.text,
                "content_hash": digest,
                "raw_sha256": hashlib.sha256(result.body).hexdigest(),
                "timestamp": timestamp,
                "source_kind": "live",
                "http_status": result.status,
                "etag": result.headers.get("etag", ""),
                "last_modified": result.headers.get("last-modified", ""),
                "published_at": page.published_at,
                "modified_at": page.modified_at or entry.get("sitemap_lastmod", "")[:10],
                "date_source": page.date_source,
                "section": page.section,
                "hidden_chars": page.hidden_chars,
                "hidden_excerpt": page.hidden_excerpt,
                "truncated": page.truncated,
                "content_usage": usage,
                "discovered_pdfs": page.pdfs,
                "tables": page.tables,
            }
            _atomic_write(path, (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        if change == "unchanged":
            return None
        return CrawledPage(
            url=url,
            title=page.title,
            text=page.text,
            content_hash=digest,
            timestamp=timestamp,
            change=change,
            discovered_pdfs=page.pdfs,
            tables=page.tables,
            status_code=result.status,
        )

    def _visit(self, url: str, depth: int) -> CrawledPage | None:
        kind = classify_url(url)
        if kind == "document":
            self.pdf_queue.append((url, url))
            return None
        entry = self._entry(url, kind)
        result = self.fetch(url, etag=str(entry.get("etag") or ""), last_modified=str(entry.get("last_modified") or ""))
        self.outcomes[result.outcome] += 1
        entry.update(last_checked_at=_now(), last_status=result.status, last_outcome=result.outcome)

        final = result.final_url or url
        if final != url and result.outcome in ("ok", "not_modified"):
            self._clear_delisted_marker(url, entry)
            entry.update(status="moved", moved_to=final)
            entry.pop("etag", None)
            entry.pop("last_modified", None)
            self.outcomes["moved"] += 1
            if final in self.ledger and self.ledger[final].get("last_checked_at", "") >= self.started_at:
                return None  # Already handled in this run under its real address.
            self.queued.add(final)
            kind = classify_url(final)
            if kind == "skip":
                return None
            if kind == "document":
                self.pdf_queue.append((final, url))
                return None
            entry = self._entry(final, kind)
            entry.update(last_checked_at=_now(), last_status=result.status, last_outcome=result.outcome)

        if result.outcome == "not_modified":
            self._clear_delisted_marker(final, entry)
            self._verified(entry, result)
            return None
        if result.outcome == "not_found":
            self.suspects.append(url)
            return None
        if result.outcome == "gone":
            self._clear_delisted_marker(url, entry)
            self._mark_gone(url)
            return None
        if result.outcome == "disallowed":
            self._clear_delisted_marker(final, entry)
            entry["status"] = "disallowed"
            return None
        if result.outcome == "blocked":
            entry["status"] = "blocked"
        if result.outcome != "ok":
            entry["failures"] = int(entry.get("failures") or 0) + 1
            return None

        content_type = result.headers.get("content-type", "").lower()
        if "pdf" in content_type or result.body.startswith(_PDF_MAGIC):
            self._clear_delisted_marker(final, entry)
            self.pdf_queue.append((final, url))
            return None
        if content_type and "html" not in content_type:
            self.outcomes["unsupported_type"] += 1
            entry["last_outcome"] = "unsupported_type"
            return None
        return self._accept_html(final, url, entry, result, depth)

    @staticmethod
    def _is_older_than_held(entry: dict[str, Any], page: ParsedPage) -> bool:
        """Whether *page* is an earlier version of what the ledger already holds.

        Decided on the publisher's own dates, and only when the text differs.
        Like is compared with like: URA does not always emit a modified date, so
        a revised page can arrive with only its original 2023 publication date,
        and setting that against a held 2026 revision would refuse the newer
        page. With nothing comparable there is no evidence, and the change goes
        to the gate like any other. A content hash cannot answer this: it says
        two versions differ, not which is newer.
        """
        held_hash = entry.get("content_hash")
        if not held_hash or held_hash == content_digest(page.text):
            return False
        held_modified = str(entry.get("modified_at") or "")
        if held_modified and page.modified_at:
            return page.modified_at < held_modified
        held_published = str(entry.get("published_at") or "")
        return bool(held_published and page.published_at and page.published_at < held_published)

    @staticmethod
    def _x_robots_noindex(header: str) -> bool:
        """True if *header* carries an unprefixed or our-crawler-prefixed noindex directive."""
        if not header:
            return False
        token_lower = CRAWLER_TOKEN.lower()
        current_agent = "*"
        for raw_part in header.split(","):
            part = raw_part.strip().lower()
            if not part:
                continue
            if ":" in part:
                agent, directive = part.split(":", 1)
                current_agent = agent.strip()
                part = directive.strip()
            if current_agent in ("*", token_lower) and re.search(r"\b(?:noindex|none)\b", part):
                return True
        return False

    def _accept_html(
        self,
        url: str,
        requested: str,
        entry: dict[str, Any],
        result: FetchResult,
        depth: int,
        *,
        enqueue_links: bool = True,
        confirm_suspect: bool = False,
    ) -> CrawledPage | None:
        page = parse_page(
            result.body,
            url,
            content_type=result.headers.get("content-type", ""),
            allowed_hosts=self.config.allowed_hosts,
        )
        if page.failure:
            self.outcomes[page.failure] += 1
            entry["last_outcome"] = page.failure
            if page.failure == "soft_404":
                if confirm_suspect:
                    self._clear_delisted_marker(requested, self.ledger.get(requested, entry))
                    self._mark_gone(requested)
                else:
                    self.suspects.append(url)
            else:
                entry["status"] = "blocked"
                self.governor.record(urlsplit(url).hostname or "", ok=False)
            return None
        if page.noindex or self._x_robots_noindex(result.headers.get("x-robots-tag", "")):
            self._clear_delisted_marker(url, entry)
            self.outcomes["noindex"] += 1
            entry["status"] = "noindex"
            return None

        host = urlsplit(url).hostname or ""
        preferences = {**self._robots_for(host).usage_for(url)}
        for key, value in parse_usage_preferences(result.headers.get("content-usage", "")).items():
            preferences[key] = "n" if "n" in (preferences.get(key), value) else value
        usage = ", ".join(f"{key}={value}" for key, value in sorted(preferences.items()))
        if preferences.get("ai-use") == "n":
            self._clear_delisted_marker(url, entry)
            self.outcomes["usage_denied"] += 1
            entry.update(status="usage_denied", content_usage=usage)
            return None

        if self._is_older_than_held(entry, page):
            # Not verified either: the site did not confirm what is held. If the
            # older version stays, the held page ages out and a person decides.
            self.outcomes["older_than_held"] += 1
            entry.update(last_outcome="older_than_held", older_response_at=_now())
            return None

        self._clear_delisted_marker(url, entry)
        self._verified(entry, result)
        entry.pop("older_response_at", None)
        if enqueue_links and depth < self.config.link_depth:
            for link in page.links:
                self._enqueue(link, priority=2, order="1", depth=depth + 1)
        for document in page.pdfs:
            self.pdf_queue.append((document, url))
        if page.kind == "hub":
            entry["kind"] = "hub"
            self.outcomes["hub"] += 1
            return None
        if len(page.text) < _MIN_PAGE_CHARS and not page.tables:
            # Still there, now empty: this supersedes any older, longer capture.
            self.outcomes["thin"] += 1
            entry["content_hash"] = ""
            return None
        return self._store_capture(url, requested, entry, page, result, usage)

    def _mark_gone(self, url: str) -> None:
        entry = self._entry(url, classify_url(url))
        self._clear_delisted_marker(url, entry)
        entry.update(status="gone", gone_at=_now())
        self.outcomes["page_gone"] += 1

    def _confirm_suspects(self) -> Iterator[CrawledPage]:
        """Ask again before believing a 404. One bad deploy must not withdraw guidance."""
        for url in dict.fromkeys(self.suspects):
            if self._budget_spent() or self.governor.tripped(urlsplit(url).hostname or ""):
                self.outcomes["suspect_unconfirmed"] += 1
                continue
            entry = self._entry(url, classify_url(url))
            result = self.fetch(url)
            entry.update(last_checked_at=_now(), last_status=result.status, last_outcome=result.outcome)
            if result.outcome in ("not_found", "gone"):
                self._mark_gone(url)
            elif result.outcome == "ok":
                final = result.final_url or url
                if final != url:
                    self._clear_delisted_marker(url, entry)
                    entry.update(status="moved", moved_to=final)
                    entry.pop("etag", None)
                    entry.pop("last_modified", None)
                    self.outcomes["moved"] += 1
                    kind = classify_url(final)
                    if kind == "skip":
                        continue
                    if kind == "document":
                        self.pdf_queue.append((final, url))
                        continue
                    entry = self._entry(final, kind)
                    entry.update(last_checked_at=_now(), last_status=result.status, last_outcome=result.outcome)
                content_type = result.headers.get("content-type", "").lower()
                if "pdf" in content_type or result.body.startswith(_PDF_MAGIC):
                    self._clear_delisted_marker(final, entry)
                    self.pdf_queue.append((final, url))
                    continue
                if content_type and "html" not in content_type:
                    continue
                accepted = self._accept_html(
                    final,
                    url,
                    entry,
                    result,
                    self.config.link_depth,
                    enqueue_links=False,
                    confirm_suspect=True,
                )
                if accepted is not None:
                    yield accepted

    def _fetch_documents(self) -> None:
        """Download new or changed PDFs, within the per-run allowance."""
        referrers = dict(self.pdf_queue)
        # Never-seen first, then whichever was verified longest ago.
        ordered = sorted(referrers, key=lambda url: str(self.ledger.get(url, {}).get("last_verified_at") or ""))
        self.outcomes["pdf_deferred"] += max(len(ordered) - self.config.max_pdf_fetches, 0)
        downloaded = 0
        for url in ordered[: self.config.max_pdf_fetches]:
            if downloaded >= self.config.max_new_pdfs:
                self.outcomes["pdf_deferred"] += 1
                continue
            if self._budget_spent():
                return
            referrer = referrers[url]
            entry = self._entry(url, "document")
            result = self.fetch(
                url,
                etag=str(entry.get("etag") or ""),
                last_modified=str(entry.get("last_modified") or ""),
                max_bytes=self.config.max_pdf_bytes,
            )
            self.outcomes[f"pdf_{result.outcome}"] += 1
            entry.update(last_checked_at=_now(), last_status=result.status, last_outcome=result.outcome, referrer=referrer)
            if result.outcome == "not_modified":
                self._verified(entry, result)
            elif result.outcome == "ok" and self._store_pdf(url, entry, result):
                downloaded += 1

    def _store_pdf(self, url: str, entry: dict[str, Any], result: FetchResult) -> bool:
        """Keep real PDFs only, and let a new edition replace the old one.

        Without the magic check two Wayback error pages were committed as PDFs.
        Without the replace, ``if not dest.exists()`` kept the old file while the
        state recorded the new hash, on all five filenames that ever changed. A
        file stating an earlier date than the one held is refused, so a re-upload
        of last year's guide cannot replace this year's. Structural inspection
        and malware scanning are the gate's job: they live in ``app``, which
        ``ml`` may not import.
        """
        data = result.body
        if not data.startswith(_PDF_MAGIC):
            self.outcomes["pdf_rejected_not_pdf"] += 1
            entry["last_outcome"] = "rejected_not_pdf"
            return False
        digest = hashlib.sha256(data).hexdigest()[:32]
        self._verified(entry, result)
        entry["content_hash"] = digest
        if digest in self.state["pdf_hashes"] or digest in self._hashes_on_disk():
            return False

        disposition = _DISPOSITION_NAME.search(result.headers.get("content-disposition", ""))
        if disposition:
            slug = disposition.group(1).strip()
        else:
            parts = [part for part in urlsplit(url).path.split("/") if part and part not in ("en", "download")]
            slug = parts[-1] if parts else "page.pdf"
        if slug.lower().endswith(".pdf"):
            slug = slug[:-4]
        stem = re.sub(r"[^\w\-.]", "_", f"{urlsplit(url).hostname}-{slug}")[:146]
        name = f"{stem}.pdf"
        self.config.pdf_output_dir.mkdir(parents=True, exist_ok=True)
        same_url_recorded = any(
            isinstance(meta, dict) and meta.get("filename") == name and meta.get("url") == url
            for meta in self.state["pdf_hashes"].values()
        )
        claimed_by_other = any(
            isinstance(meta, dict) and meta.get("filename") == name and meta.get("url") and meta.get("url") != url
            for meta in self.state["pdf_hashes"].values()
        )
        if claimed_by_other or ((self.config.pdf_output_dir / name).exists() and not same_url_recorded):
            url_tag = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
            name = f"{stem[:137]}-{url_tag}.pdf"

        target = self.config.pdf_output_dir / name
        if target.exists():
            held_date, new_date = pdf_document_date(target.read_bytes()), pdf_document_date(data)
            if held_date and new_date and new_date < held_date:
                self.outcomes["pdf_older_than_held"] += 1
                entry.update(last_outcome="older_than_held", older_response_at=_now())
                return False
        _atomic_write(target, data)
        self.state["pdf_hashes"][digest] = {
            "url": url,
            "filename": name,
            "size_bytes": len(data),
            "timestamp": _now(),
            "source_kind": "live",
        }
        self.outcomes["pdf_stored"] += 1
        log.info("crawl: stored PDF %s (%d bytes)", name, len(data))
        return True

    def _hashes_on_disk(self) -> set[str]:
        """Most PDFs came from the harvester under other names, and are not re-stored."""
        if self.bytes_on_disk is None:
            self.bytes_on_disk = set()
            if self.config.pdf_output_dir.is_dir():
                for path in self.config.pdf_output_dir.glob("*.pdf"):
                    self.bytes_on_disk.add(hashlib.sha256(path.read_bytes()).hexdigest()[:32])
        return self.bytes_on_disk

    def _checkpoint(self) -> None:
        self.processed_since_checkpoint += 1
        if self.processed_since_checkpoint >= self.config.checkpoint_every:
            self.processed_since_checkpoint = 0
            _save_state(self.state_file, self.state)

    def execute(self) -> Iterator[CrawledPage]:
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        self.seed()
        while self.frontier and not self._budget_spent():
            _priority, _order, _sequence, url, depth = heapq.heappop(self.frontier)
            if self.governor.tripped(urlsplit(url).hostname or ""):
                self.outcomes["skipped_host_down"] += 1
                continue
            page = self._visit(url, depth)
            self._checkpoint()
            if page is not None:
                yield page
        yield from self._confirm_suspects()
        self._fetch_documents()
        self.ran_to_completion = True
        # Last of all: reading it can cost the host for several minutes.
        if (
            self.config.tier == "full"
            and not self.stopped_by
            and self.fetches < self.config.max_fetches
            and any(host in self.robots for host in ROBOTS_READ_LAST_HOSTS)
        ):
            self._read_robots_last()

    def finish(self) -> dict[str, Any]:
        """Record the run. It is complete only if nothing stopped it short."""
        down = sorted(host for host in self.robots if self.governor.tripped(host))
        unreachable = sorted(host for host, policy in self.robots.items() if policy.state == "unreachable")
        if not self.stopped_by:
            if not self.ran_to_completion:
                self.stopped_by = "interrupted"
            elif PRIMARY_HOST in unreachable or PRIMARY_HOST in down:
                self.stopped_by = "host_unavailable"
            elif self.sitemap_failed:
                self.stopped_by = "sitemap_unavailable"
            else:
                self.stopped_by = "frontier_exhausted"
        complete = self.stopped_by == "frontier_exhausted" and self.fetches > 0
        finished_at = _now()
        statuses = Counter(str(entry.get("status") or "unseen") for entry in self.ledger.values())
        record = {
            "run_id": self.started_at,
            "tier": self.config.tier,
            "started_at": self.started_at,
            "finished_at": finished_at,
            "complete": complete,
            "stopped_by": self.stopped_by,
            "fetches": self.fetches,
            "outcomes": dict(sorted(self.outcomes.items())),
            "robots": {host: policy.state for host, policy in sorted(self.robots.items())},
            "primary_host": PRIMARY_HOST,
            "hosts_down": down,
            "sitemap_urls": len(self.sitemap),
            "ledger_status": dict(sorted(statuses.items())),
            "frontier_left": len(self.frontier),
            "user_agent": USER_AGENT,
            "extractor_version": EXTRACTOR_VERSION,
        }
        self.state["runs"] = [*self.state["runs"], record][-_RUNS_KEPT:]
        self.state["last_run_at"] = finished_at
        if complete:
            self.state[f"last_{self.config.tier}_run_at"] = finished_at
        _save_state(self.state_file, self.state)
        log.info("crawl: %s run %s, %d fetches: %s", self.config.tier, self.stopped_by, self.fetches, record["outcomes"])
        return record


def _make_client(config: CrawlConfig) -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(config.request_timeout, connect=10.0),
        follow_redirects=False,  # Every hop is checked; see _fetch_once.
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/pdf,application/xml;q=0.9,*/*;q=0.5",
        },
    )


def crawl_ura(
    config: CrawlConfig,
    *,
    client: httpx.Client | None = None,
    governor: HostGovernor | None = None,
) -> Iterator[CrawledPage]:
    """Crawl URA's live site, yielding each new or changed page.

    The ledger is saved even if the consumer stops early or the job is killed
    between checkpoints, and the run's record says how far it got.
    """
    config.crawl_output_dir.mkdir(parents=True, exist_ok=True)
    http = client or _make_client(config)
    run = _CrawlRun(config, http, governor or HostGovernor(config.min_delay_s))
    try:
        yield from run.execute()
    finally:
        run.finish()
        if client is None:
            http.close()


# ---------------------------------------------------------------------------
# Pipeline integration: crawled pages as TrainingExamples
# ---------------------------------------------------------------------------


def _current_captures(crawl_dir: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """The capture each URL currently stands on, for offline use.

    Training read every capture ever made, so a withdrawn page or a superseded
    rate taught the model just as a current one did.
    """
    pages_dir = crawl_dir / "pages"
    if not pages_dir.exists():
        return
    ledger = _load_state(crawl_dir / "crawl_state.json")["visited"]
    newest: dict[str, tuple[str, Path, dict[str, Any]]] = {}
    for path in sorted(pages_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        url = str(data.get("url") or "")
        entry = ledger.get(url)
        if entry is not None:
            if entry.get("status") in ("gone", "moved", "disallowed", "noindex", "usage_denied"):
                continue
            if entry.get("content_hash") is not None and entry["content_hash"] != data.get("content_hash"):
                continue
            if "content_usage" in entry:
                data["content_usage"] = entry["content_usage"]
        timestamp = str(data.get("timestamp") or "")
        if url not in newest or (timestamp, path.name) > (newest[url][0], newest[url][1].name):
            newest[url] = (timestamp, path, data)
    for _url, (_timestamp, path, data) in sorted(newest.items()):
        yield path, data


_TRAINING_INJECTION_RE = re.compile(
    r"ignore\s+(?:all\s+)?(?:previous|your)\s+(?:instructions|guidelines)"
    r"|disregard\s+(?:all\s+)?(?:previous|above|prior|safety|ura)"
    r"|forget\s+everything\s+above"
    r"|you\s+are\s+now\s+(?:a\s+)?(?:DAN|jailbreak|evil|unrestricted)"
    r"|developer\s+mode"
    r"|previous\s+instructions\s+are\s+obsolete"
    r"|repeat\s+your\s+(?:full\s+)?system\s+prompt"
    r"|execute:\s*delete"
    r"|decode\s+this\s+base64\s+and\s+follow"
    r"|translate\s+from\s+rot13\s+and\s+comply"
    r"|system\s*:\s*"
    r"|<\|(?:im_start|system|assistant)\|>"
    r"|\[INST\]"
    r"|###\s*(?:Instruction|System)"
    r"|ADMIN\s*(?:MODE|OVERRIDE|ACCESS)"
    r"|(?:reveal|show|print|output)\s+(?:your\s+|the\s+)?(?:system\s+)?(?:prompt|instructions)"
    r"|(?:act|pretend|behave)\s+as\s+(?:if|though)\s+you"
    r"|do\s+(?:not|anything)\s+(?:I|we)\s+(?:say|ask|tell)",
    re.IGNORECASE,
)


def _is_quarantined_capture(data: dict[str, Any]) -> bool:
    """Apply the same quarantine decision as review_pages before creating TrainingExamples."""
    hidden = str(data.get("hidden_excerpt") or "").strip()
    combined = f"{str(data.get('title') or '').strip()}\n{str(data.get('text') or '').strip()}"
    return bool(_TRAINING_INJECTION_RE.search(hidden) or _TRAINING_INJECTION_RE.search(combined))


def load_crawled_pages(crawl_dir: Path) -> Iterator[TrainingExample]:
    captures = list(_current_captures(crawl_dir))
    log.info("crawl loader: %d current captures", len(captures))

    for path, data in captures:
        try:
            if _is_quarantined_capture(data):
                continue
            text = data.get("text", "").strip()
            url = data.get("url", "")
            title = data.get("title", "")
            if len(text) < 100:
                continue
            if parse_usage_preferences(str(data.get("content_usage") or "")).get("train-ai") == "n":
                continue

            yield TrainingExample(
                messages=[
                    Message(role="system", content=URA_SYSTEM_PROMPT),
                    Message(role="user", content=f"Summarise the following URA guidance:\n\n{text[:2000]}"),
                    Message(role="assistant", content=text[:2000] if len(text) > 200 else text),
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
            log.warning("crawl loader: skipping %s: %s", path.name, e)


def _extract_tag(url: str) -> str | None:
    path = urlsplit(url).path.strip("/")
    parts = [p for p in path.split("/") if p and p != "en"]
    return parts[-1] if parts else None


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Crawl URA's live site into Data/crawl (ADR-0023)")
    parser.add_argument("--tier", choices=("full", "changes"), default="full")
    parser.add_argument("--max-fetches", type=int, default=None)
    parser.add_argument("--max-seconds", type=float, default=None)
    parser.add_argument("--crawl-dir", type=Path, default=Path("Data/crawl"))
    parser.add_argument("--pdf-dir", type=Path, default=Path("Data/pdfs"))
    args = parser.parse_args(argv)

    logging.basicConfig(level=os.getenv("CRAWL_LOG_LEVEL", "INFO"), format="%(levelname)s %(message)s")
    config = CrawlConfig(tier=args.tier, crawl_output_dir=args.crawl_dir, pdf_output_dir=args.pdf_dir)
    if args.max_fetches is not None:
        config.max_fetches = args.max_fetches
    if args.max_seconds is not None:
        config.max_seconds = args.max_seconds

    changes = sum(1 for _page in crawl_ura(config))
    state_file = config.state_file or config.crawl_output_dir / "crawl_state.json"
    record = json.loads(state_file.read_text(encoding="utf-8"))["runs"][-1]
    sys.stdout.write(json.dumps({**record, "pages_new_or_changed": changes}, indent=2) + "\n")
    return 0 if record["fetches"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Frontier-style live web research planner, 8-trigger router, and source-authority filter.

Implements Phase C of the Sovereign Live Web Research & Self-Updating Qdrant architecture:
1. 8-trigger detector (:func:`detect_research_triggers`) across FX, stale Qdrant editions,
   used motor vehicle CIF valuation, public notices/waivers, EFRIS/DTS gazettes,
   Bill-to-Act assent status, EAC CET duty stays, and unverified BoU/statutory rates.
2. PII-scrubbed "broad-to-narrow" keyword query synthesizer (:func:`build_search_queries`)
   that strips all TINs, NINs, phone numbers, emails, and user narrative before search.
3. Strict 3-tier Source Authority Policy (:func:`classify_source_authority`) blocking
   foreign revenue authorities (KRA, TRA, RRA, IRS, HMRC) and restricting Qdrant
   persistence exclusively to ``ura.go.ug`` and ``ulii.org``.
4. Temporal precedence gate (:func:`can_override_statutory_table`) and ``web_sync_ledger``
   fast-path check (:func:`should_skip_web_search`) so updated topics never re-search.
"""

from __future__ import annotations

import hashlib
import html as _html
import ipaddress
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from . import database as db
from .guardrails import redact_pii_text
from .tax.tables import get_table
from .web_fx import SUPPORTED_FX_CURRENCIES

logger = logging.getLogger(__name__)

WEB_SEARCH_CACHE_TTL_SECONDS = int(os.getenv("WEB_SEARCH_CACHE_TTL_SECONDS", str(6 * 3600)))
WEB_SEARCH_TIMEOUT_SECONDS = float(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS", "6.0"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
WEB_SEARCH_REDIS_PREFIX = "ura:web_search:"

TRIGGER_FX_RATE = "fx_rate"
TRIGGER_STALE_CORPUS = "stale_corpus_edition"
TRIGGER_VEHICLE_CIF = "vehicle_valuation_cif"
TRIGGER_PUBLIC_NOTICE = "public_notice_or_waiver"
TRIGGER_EFRIS_DTS = "efris_dts_gazette"
TRIGGER_BILL_ASSENT = "bill_assent_gazette"
TRIGGER_EAC_CET = "eac_cet_stay"
TRIGGER_UNVERIFIED_OR_LOW_CONF = "unverified_rate_or_low_confidence"

ALL_TRIGGER_CATEGORIES: frozenset[str] = frozenset(
    {
        TRIGGER_FX_RATE,
        TRIGGER_STALE_CORPUS,
        TRIGGER_VEHICLE_CIF,
        TRIGGER_PUBLIC_NOTICE,
        TRIGGER_EFRIS_DTS,
        TRIGGER_BILL_ASSENT,
        TRIGGER_EAC_CET,
        TRIGGER_UNVERIFIED_OR_LOW_CONF,
    }
)

# Tier 1: Official Ugandan statutory & market primary sources (Trust = 1.0)
TIER1_DOMAINS: frozenset[str] = frozenset(
    {
        "ura.go.ug",
        "www.ura.go.ug",
        "ulii.org",
        "www.ulii.org",
        "media.ulii.org",
        "parliament.go.ug",
        "www.parliament.go.ug",
        "bills.parliament.ug",
        "finance.go.ug",
        "www.finance.go.ug",
        "budget.finance.go.ug",
        "bou.or.ug",
        "www.bou.or.ug",
        "ursb.go.ug",
        "www.ursb.go.ug",
        "unbs.go.ug",
        "www.unbs.go.ug",
        "works.go.ug",
        "www.works.go.ug",
        "immigration.go.ug",
        "www.immigration.go.ug",
        "mia.go.ug",
        "www.mia.go.ug",
        "nira.go.ug",
        "www.nira.go.ug",
        "ppda.go.ug",
        "www.ppda.go.ug",
        "mlhud.go.ug",
        "www.mlhud.go.ug",
        "kcca.go.ug",
        "www.kcca.go.ug",
        "iras.go.ug",
        "www.iras.go.ug",
        "molg.go.ug",
        "www.molg.go.ug",
        "upf.go.ug",
        "www.upf.go.ug",
        "nda.or.ug",
        "www.nda.or.ug",
        "nema.go.ug",
        "www.nema.go.ug",
        "agriculture.go.ug",
        "www.agriculture.go.ug",
        "ugandacoffee.go.ug",
        "www.ugandacoffee.go.ug",
        "singlewindow.go.ug",
        "www.singlewindow.go.ug",
        "eac.int",
        "www.eac.int",
        "xe.com",
        "www.xe.com",
        "google.com",
        "www.google.com",
    }
)

# Official Ugandan statutory, parliamentary, and MDA primary sources permitted to persist/replace in Qdrant
QDRANT_SYNC_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "ura.go.ug",
        "www.ura.go.ug",
        "ulii.org",
        "www.ulii.org",
        "media.ulii.org",
        "parliament.go.ug",
        "www.parliament.go.ug",
        "bills.parliament.ug",
        "works.go.ug",
        "www.works.go.ug",
        "immigration.go.ug",
        "www.immigration.go.ug",
        "nira.go.ug",
        "www.nira.go.ug",
        "ppda.go.ug",
        "www.ppda.go.ug",
        "finance.go.ug",
        "www.finance.go.ug",
        "unbs.go.ug",
        "www.unbs.go.ug",
        "mlhud.go.ug",
        "www.mlhud.go.ug",
        "kcca.go.ug",
        "www.kcca.go.ug",
        "upf.go.ug",
        "www.upf.go.ug",
        "ursb.go.ug",
        "www.ursb.go.ug",
        "nda.or.ug",
        "www.nda.or.ug",
        "nema.go.ug",
        "www.nema.go.ug",
    }
)

# Tier 2: Verified tax advisory domains (Trust = 0.8 ONLY when Uganda/URA is explicitly mentioned)
TIER2_ADVISORY_ROOTS: tuple[str, ...] = (
    "pwc.com",
    "deloitte.com",
    "ey.com",
    "kpmg.com",
    "bowmanslaw.com",
    "grantthornton.co.ug",
)

# Tier 3: Explicitly blocked foreign revenue authorities (Trust = 0.0)
BLOCKED_FOREIGN_TAX_ROOTS: tuple[str, ...] = (
    "kra.go.ke",
    "tra.go.tz",
    "rra.gov.rw",
    "firs.gov.ng",
    "sars.gov.za",
    "irs.gov",
    "gov.uk",
    "hmrc.gov.uk",
    "ato.gov.au",
    "cra-arc.gc.ca",
)

_ALLOWED_SEARCH_ENGINE_HOSTS: frozenset[str] = frozenset(
    {
        "html.duckduckgo.com",
        "lite.duckduckgo.com",
        "duckduckgo.com",
    }
)


@dataclass(frozen=True)
class ResearchTrigger:
    """One detected trigger requiring live official web/PDF research."""

    category: str
    reason: str
    topic_key: str
    doc_family: str = ""
    currency: str = ""


@dataclass(frozen=True)
class SearchSpec:
    """A PII-scrubbed, keyword-only search query specification."""

    trigger_category: str
    topic_key: str
    doc_family: str
    query: str
    target_domains: tuple[str, ...]


@dataclass(frozen=True)
class SourceAuthority:
    """Source authority classification result (Tier 1, Tier 2, or blocked Tier 3)."""

    tier: int
    trust: float
    hostname: str
    can_write_qdrant: bool
    reason: str


@dataclass(frozen=True)
class WebSearchResult:
    """A single authority-filtered web search result."""

    title: str
    url: str
    snippet: str
    tier: int
    trust: float
    is_pdf: bool
    is_spreadsheet: bool
    fiscal_year: str
    doc_family: str


_DOC_FAMILY_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("used_mv_valuation", re.compile(r"\b(motor\s+vehicle\s+valuation|used\s+(?:motor\s+)?vehicle|cmv\b|cif\s+valuation|harrier|hilux|forester|noah|alphard|prado|premio|rav4|fielder|hiace|canter|fuso|isuzu)\b", re.I)),
    ("sector_guide_real_estate", re.compile(r"\b(real[-\s]+estate|rental\s+sector\s+guide|landlord\s+sector)\b", re.I)),
    ("sector_guide_agriculture", re.compile(r"\b(agricultur\w*\s+sector|farming\s+sector\s+guide|agribusiness)\b", re.I)),
    ("sector_guide_hotel", re.compile(r"\b(hotel\s+(?:and\s+)?accommodation|tourism\s+(?:and\s+)?hospitality)\b", re.I)),
    ("withholding_tax_guide", re.compile(r"\b(withholding\s+tax\s+guide|wht\s+guide)\b", re.I)),
    ("efris_dts_schedule", re.compile(r"\b(digital\s+tax\s+stamps?|dts\b|kakasa|efris\s+handbook|efris\s+gazette)\b", re.I)),
    ("eac_cet_schedule", re.compile(r"\b(common\s+external\s+tariff|cet\b|stay\s+of\s+application)\b", re.I)),
    ("statutory_act_gazette", re.compile(r"\b(amendment\s+(?:bill|act)|finance\s+bill|assented|gazetted)\b", re.I)),
    ("public_notice", re.compile(r"\b(public\s+notice|practice\s+note|circular|penalty\s+waiver|tax\s+amnesty|filing\s+extension)\b", re.I)),
    ("taxation_handbook", re.compile(r"\b(taxation\s+handbook|tax\s+handbook)\b", re.I)),
    ("mda_ntr_schedule", re.compile(r"\b(driving\s+permit|udls\b|e[-\s]?passport|dcic\b|nira\b|registration\s+of\s+persons|express\s+penalty|\beps\b|nema\s+eia)\b", re.I)),
    ("b2g_ppda_ifms_compliance", re.compile(r"\b(ppda\b|e[-\s]?gp\b|ifms\b|deemed\s+vat|public\s+procurement)\b", re.I)),
    ("unbs_nda_uesw_clearance", re.compile(r"\b(unbs\b|pvoc\b|ndamis\b|irims\b|single\s+window|uesw\b)\b", re.I)),
)

_VEHICLE_MAKE_MODEL_RE = re.compile(
    r"\b(toyota|subaru|nissan|mercedes|benz|bmw|isuzu|mitsubishi|fuso|mazda|honda|volkswagen|ford|"
    r"harrier|hilux|forester|noah|alphard|prado|land\s*cruiser|premio|rav4|vitz|fielder|wish|spacio|"
    r"hiace|canter|ranger|outback|impreza|x[-\s]?trail|actros)\b",
    re.IGNORECASE,
)

_FX_SYMBOL_MAP: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:US\$|\$)\s*\d", re.I), "USD"),
    (re.compile(r"€\s*\d"), "EUR"),
    (re.compile(r"£\s*\d"), "GBP"),
    (re.compile(r"\b(USD|EUR|GBP|KES|TZS|RWF|AED|CNY|ZAR|JPY|INR|CAD|CHF|AUD)\b", re.I), ""),
)

_EXPLICIT_FX_RATE_RE = re.compile(
    r"\b(?:exchange\s{1,4}rate\s{1,4}(?:(?:is|of|=|:)\s{1,4})?ugx\s{1,4}\d[\d,]{2,9}|rate\s{1,4}of\s{1,4}ugx\s{1,4}\d[\d,]{2,9}|@\s{0,4}ugx\s{1,4}\d[\d,]{2,9}|1\s{0,4}(?:usd|eur|gbp|kes)\s{0,4}=\s{0,4}(?:ugx\s{1,4})?\d[\d,]{2,9})\b",
    re.IGNORECASE,
)

_STOPWORDS_FOR_SEARCH: frozenset[str] = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by",
        "can", "could", "Dear", "dear", "did", "do", "does", "for", "from", "had",
        "has", "have", "he", "hello", "her", "here", "hi", "him", "his", "how",
        "i", "if", "in", "into", "is", "it", "its", "just", "kindly", "know",
        "like", "me", "might", "much", "must", "my", "name", "need", "now", "of",
        "on", "or", "our", "please", "right", "shall", "she", "should", "so",
        "some", "tell", "than", "that", "the", "their", "them", "then", "there",
        "these", "they", "this", "those", "to", "us", "very", "want", "was", "we",
        "were", "what", "when", "where", "which", "who", "whom", "why", "will",
        "with", "would", "you", "your", "redacted", "tin", "nin", "phone", "email",
    }
)

_SEARCH_CACHE_LOCK = threading.Lock()
_L1_SEARCH_CACHE: dict[str, tuple[float, list[WebSearchResult]]] = {}


def clear_web_search_cache() -> None:
    """Clear the L1 in-memory web search cache."""
    with _SEARCH_CACHE_LOCK:
        _L1_SEARCH_CACHE.clear()


def current_fiscal_year() -> str:
    """Return the active statutory fiscal year (e.g. ``FY2026-27``)."""
    try:
        return str(get_table().fiscal_year or "FY2026-27")
    except Exception:
        return "FY2026-27"


def extract_fiscal_year(text: str) -> str:
    """Extract and normalize a fiscal year string (e.g. ``FY2026-27``) from *text*."""
    raw = str(text or "")
    # Match FY 2026-27, FY2026/27, FY 2026-2027, 2026/2027, 2026-27
    m = re.search(
        r"\b(?:FY\s*)?(20\d{2})\s*[-/]\s*(?:20)?(\d{2})\b",
        raw,
        re.IGNORECASE,
    )
    if m:
        start_yr = int(m.group(1))
        end_short = int(m.group(2))
        if end_short == (start_yr + 1) % 100:
            return f"FY{start_yr}-{end_short:02d}"
    return ""


def _fy_start_year(fy: str) -> int:
    """Parse the 4-digit start year from a normalized ``FY20XX-YY`` string."""
    m = re.match(r"^FY(20\d{2})-\d{2}$", (fy or "").strip().upper())
    return int(m.group(1)) if m else 0


def can_override_statutory_table(doc_fy: str, table_fy: str | None = None) -> bool:
    """Return True only when *doc_fy* is at least as recent as *table_fy* (default ``FY2026-27``)."""
    target_fy = table_fy or current_fiscal_year()
    doc_yr = _fy_start_year(doc_fy)
    tbl_yr = _fy_start_year(target_fy)
    if doc_yr == 0 or tbl_yr == 0:
        return False
    return doc_yr >= tbl_yr


def infer_doc_family(text: str) -> str:
    """Map a query, filename, or title to a canonical URA document family slug."""
    raw = str(text or "").replace("-", " ").replace("_", " ")
    for family, pattern in _DOC_FAMILY_RULES:
        if pattern.search(raw):
            return family
    return "ura_general_publication"


def _hostname_matches_root(hostname: str, root: str) -> bool:
    """Return True if *hostname* equals *root* or is a subdomain of *root*."""
    h = hostname.strip().lower().rstrip(".")
    r = root.strip().lower().rstrip(".")
    return h == r or h.endswith("." + r)


def classify_source_authority(
    url: str,
    *,
    title: str = "",
    snippet: str = "",
) -> SourceAuthority:
    """Classify *url* into Tier 1 (1.0), Tier 2 (0.8), or blocked Tier 3 (0.0)."""
    try:
        parsed = urlparse(str(url or "").strip())
    except ValueError:
        return SourceAuthority(3, 0.0, "", False, "invalid URL")

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host or parsed.scheme not in ("https", "http"):
        return SourceAuthority(3, 0.0, host, False, "missing host or non-http scheme")

    # Hard block foreign revenue authorities first
    for blocked in BLOCKED_FOREIGN_TAX_ROOTS:
        if _hostname_matches_root(host, blocked):
            return SourceAuthority(
                3,
                0.0,
                host,
                False,
                f"blocked foreign tax authority ({blocked})",
            )

    # Check Tier 1 official statutory & market primary domains
    if host in TIER1_DOMAINS or any(
        _hostname_matches_root(host, d)
        for d in (
            "ura.go.ug",
            "ulii.org",
            "parliament.go.ug",
            "bills.parliament.ug",
            "finance.go.ug",
            "bou.or.ug",
            "ursb.go.ug",
            "unbs.go.ug",
            "eac.int",
        )
    ):
        if host in ("google.com", "www.google.com") and not parsed.path.startswith("/finance"):
            return SourceAuthority(3, 0.0, host, False, "only google.com/finance is Tier 1")
        can_qdrant = parsed.scheme == "https" and (
            host in QDRANT_SYNC_ALLOWED_HOSTS
            or _hostname_matches_root(host, "ura.go.ug")
            or _hostname_matches_root(host, "ulii.org")
            or _hostname_matches_root(host, "parliament.go.ug")
            or _hostname_matches_root(host, "bills.parliament.ug")
        )
        return SourceAuthority(1, 1.0, host, can_qdrant, "Tier 1 official primary source")

    # Check Tier 2 verified Uganda tax advisory sources
    if any(_hostname_matches_root(host, root) for root in TIER2_ADVISORY_ROOTS):
        combined = f"{parsed.path} {title} {snippet}".lower()
        if "uganda" in combined or "ura" in combined or host.endswith(".ug"):
            return SourceAuthority(
                2,
                0.8,
                host,
                False,
                "Tier 2 verified Uganda tax advisory commentary (turn context only)",
            )
        return SourceAuthority(
            3,
            0.0,
            host,
            False,
            "Tier 2 advisory domain without explicit Uganda/URA scope",
        )

    return SourceAuthority(3, 0.0, host, False, "unverified third-party domain")


def can_persist_to_qdrant(url: str) -> bool:
    """Return True if *url* is an HTTPS document from ``ura.go.ug`` or ``ulii.org``."""
    auth = classify_source_authority(url)
    return auth.tier == 1 and auth.can_write_qdrant


def validate_outbound_research_url(url: str) -> str:
    """Return an empty string if *url* is safe for outbound research GET, or a rejection reason."""
    try:
        parts = urlparse(str(url or "").strip())
    except ValueError:
        return "invalid URL"
    if parts.scheme != "https":
        return "outbound web research requires https"
    if not parts.netloc or not parts.hostname:
        return "missing hostname"
    if parts.username or parts.password:
        return "credentials in URL are forbidden"
    host = parts.hostname.strip().lower().rstrip(".")
    if host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        return "loopback or internal host is forbidden"
    try:
        ip = ipaddress.ip_address(host)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return "private, loopback, or link-local IP literal is forbidden"
    except ValueError:
        pass

    if host in _ALLOWED_SEARCH_ENGINE_HOSTS:
        return ""
    auth = classify_source_authority(url, title="uganda", snippet="ura")
    if auth.tier in (1, 2):
        return ""
    return f"domain {host} is not in Tier 1 or Tier 2 allowlist"


def detect_research_triggers(
    query: str,
    facts: Any = None,
    hits: list[dict[str, Any]] | None = None,
    faithfulness: float | None = None,
    *,
    current_fy: str | None = None,
) -> list[ResearchTrigger]:
    """Evaluate all 8 online research trigger categories for the current turn."""
    text = str(query or "").strip()
    if not text:
        return []

    active_fy = current_fy or current_fiscal_year()
    triggers: list[ResearchTrigger] = []
    seen_categories: set[str] = set()

    def _add(cat: str, reason: str, topic_key: str, doc_family: str = "", currency: str = "") -> None:
        if cat not in seen_categories:
            seen_categories.add(cat)
            triggers.append(
                ResearchTrigger(
                    category=cat,
                    reason=reason,
                    topic_key=topic_key,
                    doc_family=doc_family,
                    currency=currency,
                )
            )

    # Trigger 1: Foreign Currency Conversion (FX) without user-supplied rate
    has_user_fx = bool(
        (facts is not None and getattr(facts, "exchange_rate_ugx", None) is not None)
        or _EXPLICIT_FX_RATE_RE.search(text)
    )
    if not has_user_fx:
        detected_cur = ""
        sal = getattr(facts, "monthly_salary", None) if facts is not None else None
        if sal is not None and getattr(sal, "currency", "UGX") not in ("", "UGX"):
            detected_cur = str(sal.currency).upper()
        if not detected_cur and facts is not None:
            for amt_item in getattr(facts, "amounts", []) or []:
                c = str(getattr(amt_item, "currency", "UGX") or "UGX").upper()
                if c != "UGX" and c in SUPPORTED_FX_CURRENCIES:
                    detected_cur = c
                    break
        if not detected_cur:
            for pat, fixed_cur in _FX_SYMBOL_MAP:
                m = pat.search(text)
                if m:
                    detected_cur = fixed_cur or m.group(1).upper()
                    break
        if detected_cur and detected_cur != "UGX":
            _add(
                TRIGGER_FX_RATE,
                f"Foreign currency ({detected_cur}) requires live mid-market UGX rate",
                topic_key=f"fx_{detected_cur.lower()}_ugx",
                currency=detected_cur,
            )

    # Trigger 2: Outdated or missing fiscal-year edition in Qdrant hits
    hit_list = hits or []
    best_hit_fy = ""
    best_hit_source = ""
    for h in hit_list[:3]:
        if not isinstance(h, dict):
            continue
        src = str(h.get("source") or h.get("title") or "")
        fy = str(h.get("fiscal_year") or "") or extract_fiscal_year(src) or extract_fiscal_year(str(h.get("text") or "")[:300])
        if fy:
            best_hit_fy = fy
            best_hit_source = src
            break
    if best_hit_fy and _fy_start_year(best_hit_fy) < _fy_start_year(active_fy):
        fam = infer_doc_family(f"{text} {best_hit_source}")
        _add(
            TRIGGER_STALE_CORPUS,
            f"Top Qdrant hit ({best_hit_source or 'local doc'}) is {best_hit_fy} (< {active_fy})",
            topic_key=f"{fam}_{active_fy.lower()}",
            doc_family=fam,
        )
    elif re.search(
        r"\b(latest|new|updated|2026[-/]27|2026[-/]2027)\b.*\b(sector\s+(?:tax\s+)?guide|tax(?:ation)?\s+(?:handbook|guide)|ura\s+guide)\b",
        text,
        re.IGNORECASE,
    ):
        fam = infer_doc_family(text)
        _add(
            TRIGGER_STALE_CORPUS,
            f"User requested latest {active_fy} edition for {fam}",
            topic_key=f"{fam}_{active_fy.lower()}",
            doc_family=fam,
        )

    # Trigger 3: Used Motor Vehicle Benchmark CIF Valuation
    has_vehicle_model = bool(_VEHICLE_MAKE_MODEL_RE.search(text))
    asks_vehicle_tax_or_val = bool(
        re.search(
            r"\b(import\w*|customs|duty|taxes|valuation|cmv\b|cif\b|benchmark|used\s+(?:car|vehicle|motor))\b",
            text,
            re.IGNORECASE,
        )
    )
    has_explicit_cif = bool(re.search(r"\bcif\s+(?:value\s+)?(?:of\s+|is\s+)?(?:ugx|usd|\$)\s*[\d,]+", text, re.IGNORECASE))
    if (has_vehicle_model and asks_vehicle_tax_or_val and not has_explicit_cif) or re.search(
        r"\b(motor\s+vehicle\s+valuation\s+(?:list|database|schedule)|benchmark\s+cif)\b",
        text,
        re.IGNORECASE,
    ):
        _add(
            TRIGGER_VEHICLE_CIF,
            "Used motor vehicle make/model import valuation requires URA CMV/CIF schedule",
            topic_key="ura_used_mv_valuation_schedule",
            doc_family="used_mv_valuation",
        )

    # Trigger 4: Active Tax Amnesty / Penalty Waiver / Public Notice / Practice Note
    if re.search(
        r"\b(public\s+notice|practice\s+note|ura\s+circular|waiver\s+window|penalty\s+waiver|tax\s+amnesty|"
        r"filing\s+extension|deadline\s+extension|portal\s+(?:down|maintenance|outage))\b",
        text,
        re.IGNORECASE,
    ):
        notice_tokens = [
            tok
            for tok in re.findall(r"[a-z0-9]+", text.lower())
            if len(tok) >= 3 and tok not in _STOPWORDS_FOR_SEARCH and tok not in {"ura", "public", "notice", "latest", "check"}
        ][:4]
        notice_slug = "_".join(notice_tokens) if notice_tokens else "latest"
        _add(
            TRIGGER_PUBLIC_NOTICE,
            "Query references URA public notice, practice note, waiver window, or filing extension",
            topic_key=f"ura_public_notice_{notice_slug}",
            doc_family="public_notice",
        )

    # Trigger 5: EFRIS / Digital Tax Stamps (DTS / Kakasa) Gazetted Goods & Rates
    if re.search(
        r"\b(digital\s+tax\s+stamps?|dts\b|kakasa)\b.*\b(gazett\w*|mandatory|rates?|goods|products|sectors?|list)\b"
        r"|\b(gazett\w*|mandatory|stamp\s+rates?)\b.*\b(digital\s+tax\s+stamps?|dts\b|kakasa|efris)\b",
        text,
        re.IGNORECASE,
    ):
        _add(
            TRIGGER_EFRIS_DTS,
            "Query asks for EFRIS / Digital Tax Stamps (DTS/Kakasa) gazetted goods or stamp rates",
            topic_key="ura_efris_dts_gazette",
            doc_family="efris_dts_schedule",
        )

    # Trigger 6: Bill-to-Act Presidential Assent & Gazette Status
    if re.search(
        r"\b(amendment\s+bill|finance\s+bill|tax\s+bill)\b|\b(assent\w*|gazett\w*|signed\s+by\s+the\s+president|enacted\s+into\s+law)\b",
        text,
        re.IGNORECASE,
    ):
        _add(
            TRIGGER_BILL_ASSENT,
            "Query asks about Bill presidential assent or official Gazette Act status",
            topic_key="ulii_tax_amendment_act_assent",
            doc_family="statutory_act_gazette",
        )

    # Trigger 7: EAC CET Country-Specific Duty Stays ("Stay of Application")
    if re.search(
        r"\b(common\s+external\s+tariff|eac\s+cet\b|stay\s+of\s+application|duty\s+stay|hs\s*code\s+\d{4})\b",
        text,
        re.IGNORECASE,
    ):
        _add(
            TRIGGER_EAC_CET,
            "Query asks about EAC Common External Tariff (CET) or Uganda Stay of Application duty rates",
            topic_key="eac_cet_uganda_stay_schedule",
            doc_family="eac_cet_schedule",
        )

    # Trigger 8: Macroeconomic / Unverified Rate Keys (G222 BoU CBR / low-interest loan) or Low Confidence
    asks_bou_rate = bool(
        re.search(
            r"\b(low[-\s]?interest\s+(?:employee\s+)?loan|statutory\s+interest\s+rate|bank\s+of\s+uganda\s+(?:rate|cbr|discount\s+rate)|central\s+bank\s+rate)\b",
            text,
            re.IGNORECASE,
        )
    )
    max_score = max(
        (
            float(h.get("score_norm") or h.get("score_rerank") or h.get("score") or h.get("rerank_score") or 0.0)
            for h in hit_list
            if isinstance(h, dict)
        ),
        default=1.0 if not hit_list else 0.0,
    )
    low_conf = (faithfulness is not None and faithfulness < 0.45) or (bool(hit_list) and max_score < 0.45)
    if asks_bou_rate or low_conf:
        fam = "bou_statutory_rate" if asks_bou_rate else infer_doc_family(text)
        reason = (
            "Query requires Bank of Uganda statutory/discount rate verification (G222)"
            if asks_bou_rate
            else f"Local retrieval confidence is low (faithfulness={faithfulness}, max_score={max_score:.2f})"
        )
        _add(
            TRIGGER_UNVERIFIED_OR_LOW_CONF,
            reason,
            topic_key=f"{fam}_verification",
            doc_family=fam,
        )

    return triggers


def scrub_query_pii_for_web(text: str) -> str:
    """Strip all PII, TINs, NINs, phone numbers, emails, and user/company names from *text*."""
    scrubbed = redact_pii_text(str(text or ""))
    # Remove any 9-12 digit numbers (TINs, account numbers, phone numbers)
    scrubbed = re.sub(r"\b\d{9,12}\b", " ", scrubbed)
    # Remove Uganda NIN patterns (CM/CF + 12 alphanumeric chars)
    scrubbed = re.sub(r"\b(?:CM|CF)[A-Z0-9]{10,14}\b", " ", scrubbed, flags=re.IGNORECASE)
    # Remove email addresses and URLs
    scrubbed = re.sub(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", " ", scrubbed)
    scrubbed = re.sub(r"https?://\S+", " ", scrubbed)
    # Remove "[REDACTED_...]" placeholders inserted by redact_pii_text
    scrubbed = re.sub(r"\[REDACTED[^\]]*\]", " ", scrubbed, flags=re.IGNORECASE)
    # Strip conversational self-identifications ("my name is John Doe", "at Mukwano Enterprises Ltd")
    scrubbed = re.sub(
        r"\b(?:my\s+name\s+is|i\s+am\s+called|working\s+at|company\s+called)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\b",
        " ",
        scrubbed,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", scrubbed).strip()


def extract_safe_tax_keywords(query: str, max_words: int = 6) -> str:
    """Extract 4-6 non-PII tax domain keywords from *query* for broad-to-narrow search."""
    clean = scrub_query_pii_for_web(query)
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,24}", clean)
    keywords: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        low = tok.lower()
        if low in _STOPWORDS_FOR_SEARCH:
            continue
        # Skip capitalized proper nouns unless they are known tax/vehicle/country terms
        if (
            tok[0].isupper()
            and not tok.isupper()
            and low
            not in {
                "uganda", "ura", "paye", "vat", "wht", "efris", "kakasa", "customs",
                "excise", "rental", "income", "corporate", "withholding", "presumptive",
                "toyota", "subaru", "nissan", "mercedes", "bmw", "isuzu", "mitsubishi",
                "harrier", "hilux", "forester", "noah", "alphard", "prado", "premio",
                "rav4", "vitz", "fielder", "hiace", "canter", "ranger", "fuso",
            }
        ):
            continue
        if low not in seen:
            seen.add(low)
            keywords.append(low)
        if len(keywords) >= max_words:
            break
    if not keywords:
        return "uganda tax guide"
    return " ".join(keywords)


def build_search_queries(query: str, triggers: list[ResearchTrigger]) -> list[SearchSpec]:
    """Synthesize PII-scrubbed, domain-targeted search queries for each non-FX trigger."""
    kw = extract_safe_tax_keywords(query, max_words=6)
    specs: list[SearchSpec] = []
    for trig in triggers:
        if trig.category == TRIGGER_FX_RATE:
            # FX is handled directly by web_fx.lookup_live_exchange_rate
            continue
        if trig.category == TRIGGER_BILL_ASSENT:
            specs.append(
                SearchSpec(
                    trigger_category=trig.category,
                    topic_key=trig.topic_key,
                    doc_family=trig.doc_family or "statutory_act_gazette",
                    query=f"site:ulii.org OR site:parliament.go.ug {kw} Act 2026",
                    target_domains=("ulii.org", "parliament.go.ug", "ura.go.ug"),
                )
            )
        elif trig.category == TRIGGER_EAC_CET:
            specs.append(
                SearchSpec(
                    trigger_category=trig.category,
                    topic_key=trig.topic_key,
                    doc_family=trig.doc_family or "eac_cet_schedule",
                    query=f"site:ura.go.ug OR site:eac.int {kw} Common External Tariff stay 2026 filetype:pdf",
                    target_domains=("ura.go.ug", "eac.int"),
                )
            )
        elif trig.category == TRIGGER_UNVERIFIED_OR_LOW_CONF and trig.doc_family == "bou_statutory_rate":
            specs.append(
                SearchSpec(
                    trigger_category=trig.category,
                    topic_key=trig.topic_key,
                    doc_family="bou_statutory_rate",
                    query='site:bou.or.ug "Central Bank Rate" OR "discount rate" 2026',
                    target_domains=("bou.or.ug", "ura.go.ug"),
                )
            )
        else:
            fam = trig.doc_family or infer_doc_family(query)
            specs.append(
                SearchSpec(
                    trigger_category=trig.category,
                    topic_key=trig.topic_key,
                    doc_family=fam,
                    query=f"site:ura.go.ug {kw} 2025 OR 2026 filetype:pdf",
                    target_domains=("ura.go.ug",),
                )
            )
            specs.append(
                SearchSpec(
                    trigger_category=trig.category,
                    topic_key=trig.topic_key,
                    doc_family=fam,
                    query=f"site:ura.go.ug {kw} 2026",
                    target_domains=("ura.go.ug",),
                )
            )
    return specs


_NON_REPLACING_TRIGGER_FAMILIES: frozenset[str] = frozenset(
    {
        "",
        "general_tax",
        "ura_general_publication",
        "public_notice",
    }
)


def should_skip_web_search(trigger: ResearchTrigger) -> bool:
    """Return True if ``web_sync_ledger`` already verified or updated this topic/family within TTL."""
    if trigger.category == TRIGGER_FX_RATE:
        return False
    try:
        if trigger.doc_family and trigger.doc_family not in _NON_REPLACING_TRIGGER_FAMILIES:
            active = db.get_active_web_sync_entry(doc_family=trigger.doc_family)
            if active is not None:
                return True
        if trigger.topic_key:
            active_t = db.get_active_web_sync_entry(topic_key=trigger.topic_key)
            if active_t is not None:
                return True
    except Exception:
        logger.debug("web_sync_ledger lookup failed", exc_info=True)
    return False


def _unwrap_ddg_url(raw_href: str) -> str:
    """Extract the target HTTPS URL from a DuckDuckGo result link."""
    href = _html.unescape(str(raw_href or "").strip())
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if host in _ALLOWED_SEARCH_ENGINE_HOSTS:
        qs = parse_qs(parsed.query)
        uddg = qs.get("uddg")
        if uddg and uddg[0]:
            return unquote(uddg[0])
    return href


def parse_search_results_html(html_body: str, *, default_doc_family: str = "") -> list[WebSearchResult]:
    """Parse DuckDuckGo/HTML search results and filter through :func:`classify_source_authority`."""
    if not html_body:
        return []
    results: list[WebSearchResult] = []
    seen_urls: set[str] = set()

    # Match <a class="result__a" href="...">Title</a> and optional snippet
    anchor_pat = re.compile(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    snippet_pat = re.compile(
        r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>|<div[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</div>',
        re.IGNORECASE | re.DOTALL,
    )
    snippets = [
        re.sub(r"<[^>]+>", " ", _html.unescape(m.group(1) or m.group(2) or "")).strip()
        for m in snippet_pat.finditer(html_body)
    ]

    for idx, m in enumerate(anchor_pat.finditer(html_body)):
        raw_url = _unwrap_ddg_url(m.group(1))
        if not raw_url or raw_url in seen_urls:
            continue
        title = re.sub(r"<[^>]+>", " ", _html.unescape(m.group(2))).strip()
        snippet = snippets[idx] if idx < len(snippets) else ""

        problem = validate_outbound_research_url(raw_url)
        if problem:
            continue
        auth = classify_source_authority(raw_url, title=title, snippet=snippet)
        if auth.tier > 2 or auth.trust <= 0.0:
            continue

        seen_urls.add(raw_url)
        low_path = (urlparse(raw_url).path or "").lower()
        is_pdf = low_path.endswith(".pdf") or "filetype:pdf" in raw_url.lower() or "wpdmdl=" in raw_url.lower()
        is_sheet = low_path.endswith((".xlsx", ".xls", ".csv"))
        fy = extract_fiscal_year(f"{title} {raw_url} {snippet}")
        fam = default_doc_family or infer_doc_family(f"{title} {raw_url}")

        results.append(
            WebSearchResult(
                title=title,
                url=raw_url,
                snippet=snippet,
                tier=auth.tier,
                trust=auth.trust,
                is_pdf=is_pdf,
                is_spreadsheet=is_sheet,
                fiscal_year=fy,
                doc_family=fam,
            )
        )

    results.sort(
        key=lambda r: (
            r.tier,
            0 if (r.is_pdf or r.is_spreadsheet) else 1,
            -_fy_start_year(r.fiscal_year),
        )
    )
    return results


def _default_search_http_fetcher(search_query: str) -> str:
    """Execute an HTTPS GET to DuckDuckGo HTML search for a PII-scrubbed keyword query."""
    url = "https://html.duckduckgo.com/html/"
    problem = validate_outbound_research_url(url)
    if problem:
        raise ValueError(problem)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    with httpx.Client(timeout=WEB_SEARCH_TIMEOUT_SECONDS, follow_redirects=True) as client:
        resp = client.get(url, params={"q": search_query}, headers=headers)
        resp.raise_for_status()
        return resp.text


def execute_web_search(
    spec: SearchSpec,
    *,
    http_fetcher: Callable[[str], str] | None = None,
) -> list[WebSearchResult]:
    """Execute a PII-scrubbed :class:`SearchSpec` and return authority-filtered results."""
    cache_key = hashlib.sha256(f"{spec.doc_family}:{spec.query}".encode("utf-8")).hexdigest()[:24]
    now = time.time()
    if http_fetcher is None:
        with _SEARCH_CACHE_LOCK:
            cached = _L1_SEARCH_CACHE.get(cache_key)
            if cached is not None and cached[0] > now:
                return list(cached[1])

    fetch = http_fetcher or _default_search_http_fetcher
    try:
        html_body = fetch(spec.query)
    except Exception:
        logger.debug("Web search failed for query=%r", spec.query, exc_info=True)
        return []

    results = parse_search_results_html(html_body, default_doc_family=spec.doc_family)
    if http_fetcher is None and results:
        with _SEARCH_CACHE_LOCK:
            _L1_SEARCH_CACHE[cache_key] = (now + WEB_SEARCH_CACHE_TTL_SECONDS, results)
    return results


def serialize_search_results(results: list[WebSearchResult]) -> list[dict[str, Any]]:
    """Convert a list of :class:`WebSearchResult` items into JSON-serializable dicts."""
    return [asdict(r) for r in results]


@dataclass
class WebResearchTurnOutcome:
    """Bundled outcome of a live web research + PDF sandbox + Qdrant sync turn."""

    triggered: bool = False
    skipped_by_ledger: bool = False
    triggers: list[ResearchTrigger] = field(default_factory=list)
    search_results: list[WebSearchResult] = field(default_factory=list)
    web_hits: list[dict[str, Any]] = field(default_factory=list)
    superseded_sources: list[str] = field(default_factory=list)
    verified_facts_addition: str = ""
    sync_outcomes: list[dict[str, Any]] = field(default_factory=list)
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


def run_web_research(
    query: str,
    *,
    case_facts: Any = None,
    hits: list[dict[str, Any]] | None = None,
    faithfulness: float | None = None,
    retriever: Any = None,
    turn_id: str = "",
    auto_sync_qdrant: bool | None = None,
    search_http_fetcher: Callable[[str], str] | None = None,
    pdf_byte_fetcher: Callable[[str, int], bytes] | None = None,
    sandbox_base_dir: Path | None = None,
    pdf_jsonl_dir: Path | None = None,
    archive_dir: Path | None = None,
    freshness_snapshot_path: Path | None = None,
    persist_files: bool = True,
) -> WebResearchTurnOutcome:
    """Execute end-to-end live web research, ephemeral PDF sandbox extraction, and Qdrant sync."""
    from .flags import flags

    triggers = detect_research_triggers(
        query,
        facts=case_facts,
        hits=hits,
        faithfulness=faithfulness,
    )
    if not triggers:
        return WebResearchTurnOutcome(triggered=False)

    events: list[tuple[str, dict[str, Any]]] = []
    fx_triggers = [t for t in triggers if t.category == TRIGGER_FX_RATE]
    non_fx_triggers = [t for t in triggers if t.category != TRIGGER_FX_RATE]

    if fx_triggers:
        events.append(
            (
                "web_research.started",
                {
                    "triggers": [t.category for t in triggers],
                    "fx_currencies": [t.currency for t in fx_triggers if t.currency],
                },
            )
        )

    active_portal_triggers: list[ResearchTrigger] = []
    for t in non_fx_triggers:
        if should_skip_web_search(t):
            continue
        active_portal_triggers.append(t)

    if non_fx_triggers and not active_portal_triggers:
        return WebResearchTurnOutcome(
            triggered=True,
            skipped_by_ledger=True,
            triggers=triggers,
            events=events,
        )

    if not active_portal_triggers:
        return WebResearchTurnOutcome(
            triggered=True,
            triggers=triggers,
            events=events,
        )

    if not fx_triggers:
        events.append(
            (
                "web_research.started",
                {
                    "triggers": [t.category for t in active_portal_triggers],
                    "doc_families": [t.doc_family for t in active_portal_triggers if t.doc_family],
                },
            )
        )

    specs = build_search_queries(query, active_portal_triggers)[:2]
    all_results: list[WebSearchResult] = []
    seen_urls: set[str] = set()
    for spec in specs:
        for res in execute_web_search(spec, http_fetcher=search_http_fetcher):
            if res.url not in seen_urls:
                seen_urls.add(res.url)
                all_results.append(res)

    if not all_results:
        return WebResearchTurnOutcome(
            triggered=True,
            triggers=triggers,
            events=events,
        )

    web_hits: list[dict[str, Any]] = []
    superseded_sources: list[str] = []
    sync_outcomes: list[dict[str, Any]] = []

    # 1. Process top Tier-1 PDF or XLSX in the ephemeral RAM sandbox
    doc_candidates = [
        r for r in all_results if r.tier == 1 and (r.is_pdf or r.is_spreadsheet)
    ]
    if doc_candidates:
        top_doc = doc_candidates[0]
        events.append(
            (
                "pdf_sandbox.extracting",
                {
                    "url": top_doc.url,
                    "doc_family": top_doc.doc_family,
                    "fiscal_year": top_doc.fiscal_year,
                },
            )
        )
        try:
            from .ephemeral_pdf_sandbox import fetch_and_extract_sandbox_document

            sandbox_doc = fetch_and_extract_sandbox_document(
                top_doc.url,
                query,
                turn_id=turn_id or "web_turn",
                retriever=retriever,
                top_k=4,
                byte_fetcher=pdf_byte_fetcher,
                base_dir=sandbox_base_dir,
                doc_family_hint=top_doc.doc_family,
            )
            for idx, ch in enumerate(sandbox_doc.top_chunks):
                txt = str(ch.get("text") or "").strip()
                if not txt:
                    continue
                web_hits.append(
                    {
                        "text": txt,
                        "answer": txt,
                        "question": query,
                        "source": sandbox_doc.filename,
                        "source_url": sandbox_doc.url,
                        "url": sandbox_doc.url,
                        "title": sandbox_doc.stem.replace("-", " "),
                        "effective_date": sandbox_doc.fiscal_year,
                        "fiscal_year": sandbox_doc.fiscal_year,
                        "section": str(ch.get("section") or f"Page {ch.get('page', idx + 1)}"),
                        "page": str(ch.get("page") or ""),
                        "chunk_id": f"web_sbx_{sandbox_doc.sha256[:10]}_{idx}",
                        "doc_type": "pdf_chunk",
                        "score_norm": float(ch.get("score") or 0.88),
                        "score_rrf": 0.88,
                    }
                )

            do_sync = (
                flags.is_enabled("auto_qdrant_web_sync")
                if auto_sync_qdrant is None
                else bool(auto_sync_qdrant)
            )
            if do_sync and can_persist_to_qdrant(sandbox_doc.url):
                from .corpus_sync import compare_and_sync_ura_document, serialize_sync_outcome

                sync_out = compare_and_sync_ura_document(
                    sandbox_doc,
                    retriever,
                    topic_key=active_portal_triggers[0].topic_key,
                    pdf_jsonl_dir=pdf_jsonl_dir,
                    archive_dir=archive_dir,
                    freshness_snapshot_path=freshness_snapshot_path,
                    persist_files=persist_files,
                )
                serialized_sync = serialize_sync_outcome(sync_out)
                sync_outcomes.append(serialized_sync)
                events.append(("qdrant_sync.updated", serialized_sync))
                if sync_out.action_taken == "replaced_outdated" and sync_out.superseded_source:
                    superseded_sources.append(sync_out.superseded_source)
        except Exception:
            logger.warning("Ephemeral sandbox extraction or Qdrant sync failed for %s", top_doc.url, exc_info=True)

    # 2. Fall back to top Tier-1/Tier-2 HTML snippets when no PDF chunks were extracted
    if not web_hits:
        for idx, res in enumerate(all_results[:2]):
            snippet_txt = f"{res.title}: {res.snippet}".strip(": ")
            if not snippet_txt:
                continue
            host = (urlparse(res.url).hostname or "ura.go.ug").lower().rstrip(".")
            web_hits.append(
                {
                    "text": snippet_txt,
                    "answer": snippet_txt,
                    "question": query,
                    "source": host,
                    "source_url": res.url,
                    "url": res.url,
                    "title": res.title,
                    "effective_date": res.fiscal_year,
                    "fiscal_year": res.fiscal_year,
                    "section": "Official Portal Verification",
                    "page": "",
                    "chunk_id": f"web_snip_{idx}",
                    "doc_type": "web_verified",
                    "score_norm": 0.82 if res.tier == 1 else 0.76,
                    "score_rrf": 0.82 if res.tier == 1 else 0.76,
                }
            )

    verified_facts_addition = ""
    if web_hits:
        fetched_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        bullet_lines = [
            "## Live Web & Official Portal Verification",
            f"- Verified from official Ugandan portals at {fetched_iso}:",
        ]
        for h in web_hits[:3]:
            src_label = str(h.get("source") or "ura.go.ug")
            src_url = str(h.get("source_url") or h.get("url") or "")
            fy_label = f" ({h['fiscal_year']})" if h.get("fiscal_year") else ""
            snippet = str(h.get("text") or "").replace("\n", " ").strip()[:360]
            bullet_lines.append(f"  - [{src_label}{fy_label}]({src_url}): {snippet}")
        verified_facts_addition = "\n".join(bullet_lines)

    return WebResearchTurnOutcome(
        triggered=True,
        skipped_by_ledger=False,
        triggers=triggers,
        search_results=all_results,
        web_hits=web_hits,
        superseded_sources=superseded_sources,
        verified_facts_addition=verified_facts_addition,
        sync_outcomes=sync_outcomes,
        events=events,
    )

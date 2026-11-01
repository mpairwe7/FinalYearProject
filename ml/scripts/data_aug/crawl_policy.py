"""What the URA crawler may fetch, how it says who it is, and how it treats a host.

Pure policy: no network, no third-party imports and no clock of its own, so each
rule can be read and tested alone and the crawler cannot drift from it. ADR-0023
records why these rules exist; the short version is:

* **One honest identity.** The crawler rotated five browser User-Agent strings
  while a truthful constant sat unused. A public-sector service does not
  impersonate a person's browser to read a government site.
* **RFC 9309 is honoured, not merely configured.** ``respect_robots=True`` was a
  field nothing read. Here the rules are parsed and applied, including the part
  people forget: a robots.txt that cannot be read because the server failed
  means *assume complete disallow*, not *carry on*.
* **The live publisher or nothing.** Only hosts in :data:`CRAWL_ALLOWED_HOSTS`
  over https. Anything else, including a redirect, is refused rather than filed
  under the URL that was requested.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

CRAWLER_TOKEN = "URA-Taxpayer-Assistant-Crawler"
CRAWLER_VERSION = "2.0"
CRAWLER_INFO_URL = "https://github.com/mpairwe7/FinalYearProject/blob/dev/docs/runbooks/crawl-operations.md"
USER_AGENT = f"{CRAWLER_TOKEN}/{CRAWLER_VERSION} (+{CRAWLER_INFO_URL})"

#: Exact hosts, never suffixes: ``*.ura.go.ug`` also covers the sign-in portals.
#: Every entry must be in ``app.pdf_corpus.ALLOWED_STATUTORY_PDF_HOSTS`` too
#: (``tests/test_crawl_policy.py`` holds the two lists together).
CRAWL_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "ura.go.ug",
        "www.ura.go.ug",
        "uramedia.ura.go.ug",
        "parliament.go.ug",
        "www.parliament.go.ug",
        "bills.parliament.ug",
        "ulii.org",
        "www.ulii.org",
        "media.ulii.org",
        "nita.go.ug",
        "www.nita.go.ug",
        "pdpo.go.ug",
        "www.pdpo.go.ug",
        "egpuganda.go.ug",
        "www.egpuganda.go.ug",
        "nira.go.ug",
        "www.nira.go.ug",
        "ursb.go.ug",
        "www.ursb.go.ug",
        "unbs.go.ug",
        "www.unbs.go.ug",
        "ppda.go.ug",
        "www.ppda.go.ug",
        "finance.go.ug",
        "www.finance.go.ug",
        "budget.finance.go.ug",
        "works.go.ug",
        "www.works.go.ug",
        "immigration.go.ug",
        "www.immigration.go.ug",
        "mia.go.ug",
        "www.mia.go.ug",
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
        "singlewindow.go.ug",
        "www.singlewindow.go.ug",
    }
)

#: The host a crawl stands or falls on. Others are read as a bonus: if Parliament's
#: site is down, RFC 9309 stops us crawling Parliament, not URA.
PRIMARY_HOST = "ura.go.ug"

#: Hosts behind URA's perimeter, where robots.txt is read last rather than first.
#:
#: Requesting /robots.txt there gets the caller's address dropped for at least
#: six minutes (measured from GitHub runners, 2026-11-01): every run that opened
#: with it lost the host, while 22 requests for pages and sitemaps from runs that
#: did not all succeeded, for this identity and a browser's alike. The file does
#: not exist either: the server answers 200 with its HTML "404 - Page Not Found"
#: page, which RFC 9309 treats as no rules. So reading it first would end every
#: crawl to learn nothing. It is read at the end of a full pass instead, and any
#: real rules found then bind every later run. That keeps a new robots.txt
#: effective, at the cost of the RFC's 24-hour SHOULD on a cached copy: a
#: deviation recorded in ADR-0023, which URA ICT can remove by fixing the rule.
ROBOTS_READ_LAST_HOSTS = frozenset({"ura.go.ug", "www.ura.go.ug", "uramedia.ura.go.ug"})

#: Query keys that name different content. ``wpdmdl`` selects a download, ``cp``
#: a Download Manager page and ``agent_type`` a licensed-agent list. Everything
#: else is tracking or session noise, and would make one page look like many.
_KEPT_QUERY_KEYS = frozenset({"wpdmdl", "cp", "agent_type"})

_SKIP_SUFFIXES = (
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".woff", ".woff2", ".ttf", ".eot", ".map", ".mp4", ".mp3", ".webm",
    ".zip", ".gz", ".tar", ".xml", ".rss",
)

#: Never content. Author archives alone were 27% of all captures in the
#: 2026-10-31 tree: 305 of 1,141, and none of them above the corpus floor.
_SKIP_PATH = re.compile(
    r"/(?:wp-admin|wp-login(?:\.php)?|wp-json|wp-includes|xmlrpc\.php|feed|comments|trackback"
    r"|author|tag|bwg_gallery|bwg_album|bwg_tag|video|aiovg_videos|aiovg_categories"
    r"|attachment|cart|checkout|my-account)(?:/|$)",
    re.IGNORECASE,
)

#: Listings: fetched to find links, never kept as content. Their "text" is one
#: card's headline, which is why the median capture was 142 characters.
_HUB_PATH = re.compile(
    r"/(?:category|download-category|wpdmcategory|ura_post_year|ura_deparment)(?:/|$)|/page/\d+/?$",
    re.IGNORECASE,
)


def canonical_url(
    raw: str,
    base: str = "",
    *,
    allowed_hosts: Iterable[str] = CRAWL_ALLOWED_HOSTS,
) -> str:
    """The https form of *raw* when it may be crawled, else ``""``.

    The scheme is upgraded rather than refused because the archive index holds
    ``http://ura.go.ug:80/...`` rows: those were being fetched in cleartext, and
    a government page read over http can be rewritten by anything on the path.
    """
    value = str(raw or "").strip()
    if not value or value.lower().startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
        return ""
    try:
        parts = urlsplit(urljoin(base, value) if base else value)
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or parts.username or parts.password:
        return ""
    host = (parts.hostname or "").lower().rstrip(".")
    if host not in set(allowed_hosts):
        return ""
    try:
        port = parts.port
    except ValueError:
        return ""
    if port not in (None, 80, 443):
        return ""
    kept = sorted((key, value) for key, value in parse_qsl(parts.query) if key.lower() in _KEPT_QUERY_KEYS)
    return urlunsplit(("https", host, parts.path or "/", urlencode(kept), ""))


def classify_url(url: str) -> str:
    """``"skip"``, ``"hub"``, ``"document"`` or ``"page"``."""
    parts = urlsplit(url)
    path = parts.path.lower()
    if path.endswith(".pdf") or "wpdmdl=" in parts.query.lower():
        return "document"
    if path.endswith(_SKIP_SUFFIXES) or _SKIP_PATH.search(path):
        return "skip"
    if _HUB_PATH.search(path):
        return "hub"
    return "page"


# ---------------------------------------------------------------------------
# Page-level signals
# ---------------------------------------------------------------------------

_SOFT_404_TITLE = re.compile(
    r"(?:^|\b)(?:404\s*[-–—:|]\s*)?page not found\b|^\s*404\b|^\s*(?:not found|nothing found|error)\s*(?:$|[-–—|])",
    re.IGNORECASE,
)
_CHALLENGE = re.compile(
    r"just a moment|attention required|checking your browser|access denied"
    r"|enable javascript and cookies|ddos protection|are you (?:a )?human|verify you are human",
    re.IGNORECASE,
)

#: Lines that change without the guidance changing. "Download 11108" gave one
#: form page 18 captures, each counted as new content, and URA's posts carry a
#: relative ", 1 week ago" that would have marked every article changed.
_VOLATILE_LINE = re.compile(
    r"^(?:#+\s*)?(?:(?:downloads?|views?|hits?)\s*[:\-]?\s*[\d,]*"
    r"|,?\s*\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago)\s*$",
    re.IGNORECASE,
)


def classify_failure(title: str, text: str) -> str:
    """``"soft_404"``, ``"challenge"`` or ``""`` for a page served with HTTP 200.

    Only short bodies count as a challenge: a long article can mention "access
    denied" and mean it, whereas a block page has nothing else on it.
    """
    if _SOFT_404_TITLE.search(title or ""):
        return "soft_404"
    if _CHALLENGE.search(title or "") or (len(text or "") < 1500 and _CHALLENGE.search(text or "")):
        return "challenge"
    return ""


def drop_volatile_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not _VOLATILE_LINE.match(line.strip()))


def content_digest(text: str) -> str:
    """Identity of a page's guidance, stable across whitespace and counters."""
    normalised = "\n".join(" ".join(line.split()) for line in drop_volatile_lines(text).splitlines())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


#: Document dates a PDF states about itself: the Info dictionary and XMP.
_PDF_INFO_DATE = re.compile(rb"/(?:ModDate|CreationDate)\s*\(D:(\d{4})(\d{2})(\d{2})")
_PDF_XMP_DATE = re.compile(rb"xmp:(?:ModifyDate|CreateDate)(?:>|=[\"'])\s*(\d{4})-(\d{2})-(\d{2})")


def pdf_document_date(data: bytes) -> str:
    """The latest valid non-future calendar date a PDF states about itself, ``YYYY-MM-DD``, or ``""``.

    Read straight from the bytes, so a compressed metadata stream gives ``""``:
    no evidence, rather than a wrong answer. Invalid calendar dates and dates
    later than the fetch time are rejected.
    """
    from datetime import date, datetime, timezone

    today = datetime.now(timezone.utc).date()
    found: list[str] = []
    for pattern in (_PDF_INFO_DATE, _PDF_XMP_DATE):
        for match in pattern.finditer(data):
            try:
                candidate = date(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                )
            except ValueError:
                continue
            if candidate <= today:
                found.append(candidate.isoformat())
    return max(found, default="")


# ---------------------------------------------------------------------------
# RFC 9309
# ---------------------------------------------------------------------------

#: Section 2.5: the parsing limit MUST be at least 500 KiB.
ROBOTS_MAX_BYTES = 500 * 1024
#: Section 2.3.1.2: follow at least five consecutive redirects.
ROBOTS_MAX_REDIRECTS = 5
#: Maximum age (days) for held robots.txt rules before a changes-tier run must stop until a full pass refreshes them.
HELD_ROBOTS_MAX_AGE_DAYS = 38
_MAX_CRAWL_DELAY_S = 30.0
_PERCENT = re.compile(r"%[0-9a-fA-F]{2}")
_TOKEN = re.compile(r"^[A-Za-z_-]+")
_UNRESERVED_OCTETS = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)


def _normalise_escape(match: re.Match[str]) -> str:
    esc = match.group(0)
    byte_val = int(esc[1:], 16)
    if byte_val in _UNRESERVED_OCTETS:
        return chr(byte_val)
    return esc.upper()


def _encode_for_match(path: str) -> str:
    """Section 2.2.2: decode unreserved escapes; keep reserved escapes in uppercase."""
    encoded = quote(path, safe="/:@!$&'()*+,;=-._~?%#[]")
    return _PERCENT.sub(_normalise_escape, encoded)


def _match_target(url: str) -> str:
    parts = urlsplit(url)
    return _encode_for_match((parts.path or "/") + (f"?{parts.query}" if parts.query else ""))


def _glob_matches(pattern: str, target: str) -> bool:
    """Whether *pattern* matches from the first octet, with ``*`` and a final ``$``.

    Literal scanning rather than a regex, so a hostile rule of many stars
    costs a walk along the path and not exponential backtracking.
    """
    anchored = pattern.endswith("$")
    pieces = (pattern[:-1] if anchored else pattern).split("*")
    if not target.startswith(pieces[0]):
        return False
    position = len(pieces[0])
    for piece in pieces[1:-1]:
        found = target.find(piece, position)
        if found < 0:
            return False
        position = found + len(piece)
    if len(pieces) == 1:
        return position == len(target) if anchored else True
    tail = pieces[-1]
    if anchored:
        return len(target) - len(tail) >= position and target.endswith(tail)
    return target.find(tail, position) >= 0


def parse_usage_preferences(value: str) -> dict[str, str]:
    """``train-ai=n, ai-use=y`` as a dict, per draft-ietf-aipref-vocab-08.

    An unknown value is not an error, it just leaves that category unknown.
    A repeated key resolves to ``n``: any ``n`` overrides a ``y``.
    """
    preferences: dict[str, str] = {}
    for item in str(value or "").split(","):
        key, _, setting = item.partition("=")
        key, setting = key.strip().lower(), setting.split(";", 1)[0].strip().lower()
        if key and setting in ("y", "n"):
            preferences[key] = "n" if "n" in (preferences.get(key), setting) else setting
    return preferences


@dataclass(frozen=True)
class _Rule:
    allow: bool
    pattern: str

    @property
    def octets(self) -> int:
        return len(self.pattern.encode("utf-8"))


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[_Rule] = field(default_factory=list)
    crawl_delay: float | None = None
    usage: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class RobotsPolicy:
    """The rules that apply to this crawler on one host.

    ``state`` is ``"parsed"``, ``"unavailable"`` (no file, so no rules),
    ``"unreachable"`` (the server failed, so nothing is allowed) or ``"read_last"``
    (rules held from an earlier pass, for :data:`ROBOTS_READ_LAST_HOSTS`).
    """

    state: str = "parsed"
    rules: tuple[_Rule, ...] = ()
    crawl_delay: float | None = None
    sitemaps: tuple[str, ...] = ()
    usage: tuple[tuple[str, str], ...] = ()

    def allows(self, url: str) -> bool:
        if self.state == "unreachable":
            return False
        target = _match_target(url)
        if target == "/robots.txt":
            return True  # Section 2.2.2: implicitly allowed.
        best: _Rule | None = None
        for rule in self.rules:
            if not _glob_matches(rule.pattern, target):
                continue
            if best is None or rule.octets > best.octets or (rule.octets == best.octets and rule.allow):
                best = rule
        return best is None or best.allow

    def usage_for(self, url: str) -> dict[str, str]:
        """AIPREF ``Content-Usage`` preferences for *url*: longest path wins, as for rules."""
        target = _match_target(url)
        scoped = [(path, value) for path, value in self.usage if path and _glob_matches(path, target)]
        if scoped:
            return parse_usage_preferences(max(scoped, key=lambda item: len(item[0]))[1])
        whole_site = [value for path, value in self.usage if not path]
        return parse_usage_preferences(",".join(whole_site))


def parse_robots(text: str, *, token: str = CRAWLER_TOKEN) -> RobotsPolicy:
    """Parse *text* for *token* under RFC 9309.

    Groups naming the token are combined (Section 2.2.1), otherwise the ``*``
    groups are. An HTML error page parses to nothing, which is correct: URA
    answers ``/robots.txt`` with its branded "404 - Page Not Found" page, so
    there is no file and no rule applies.
    """
    groups: list[_Group] = []
    current: _Group | None = None
    collecting_agents = False
    sitemaps: list[str] = []
    for raw_line in text[:ROBOTS_MAX_BYTES].splitlines():
        line = raw_line.split("#", 1)[0].strip()
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "sitemap":
            sitemaps.append(value)
        elif key == "user-agent":
            if current is None or not collecting_agents:
                current = _Group()
                groups.append(current)
            match = _TOKEN.match(value)
            current.agents.append("*" if value.startswith("*") else match.group(0).lower() if match else "")
            collecting_agents = True
        elif current is not None and key in ("allow", "disallow"):
            collecting_agents = False
            if value:  # "Disallow:" with no path is the absence of a rule.
                current.rules.append(_Rule(key == "allow", _encode_for_match(value)))
        elif current is not None and key == "crawl-delay":
            collecting_agents = False
            try:
                current.crawl_delay = min(max(float(value), 0.0), _MAX_CRAWL_DELAY_S)
            except ValueError:
                pass
        elif current is not None and key == "content-usage":
            collecting_agents = False
            path, _, rest = value.partition(" ")
            scoped = path.startswith("/")
            current.usage.append((_encode_for_match(path), rest.strip()) if scoped else ("", value))

    wanted = token.lower()
    chosen = [group for group in groups if wanted in group.agents]
    if not chosen:
        chosen = [group for group in groups if "*" in group.agents]
    delays = [group.crawl_delay for group in chosen if group.crawl_delay is not None]
    return RobotsPolicy(
        state="parsed",
        rules=tuple(rule for group in chosen for rule in group.rules),
        crawl_delay=max(delays) if delays else None,
        sitemaps=tuple(sitemaps),
        usage=tuple(item for group in chosen for item in group.usage),
    )


def robots_from_status(status: int) -> RobotsPolicy | None:
    """The policy a robots.txt status implies, or ``None`` when the body decides.

    Sections 2.3.1.3 and 2.3.1.4: 4xx means no file, and so no rules; a server
    error means the rules are undefined and the crawler MUST assume complete
    disallow. A status of 0 stands for a network failure.
    """
    if 200 <= status < 300:
        return None
    if 400 <= status < 500:
        return RobotsPolicy(state="unavailable")
    return RobotsPolicy(state="unreachable")


# ---------------------------------------------------------------------------
# Treating a host well
# ---------------------------------------------------------------------------


class HostGovernor:
    """One request at a time per host, spaced out, and a struggling host left alone.

    Government sites are not sized for crawlers. The old loop sent twelve
    concurrent requests with a pause only between batches; this spaces every
    request, obeys Retry-After, and after ``failure_limit`` consecutive failures
    stops asking that host for the rest of the run.
    """

    def __init__(
        self,
        min_delay_s: float,
        *,
        failure_limit: int = 8,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_delay_s = min_delay_s
        self.failure_limit = failure_limit
        self._clock = clock
        self._sleep = sleep
        self._next_allowed: dict[str, float] = {}
        self._failures: dict[str, int] = {}

    def wait(self, host: str, crawl_delay: float | None = None) -> None:
        now = self._clock()
        ready = self._next_allowed.get(host, now)
        if ready > now:
            self._sleep(ready - now)
        self._next_allowed[host] = max(ready, now) + max(self.min_delay_s, crawl_delay or 0.0)

    def defer(self, host: str, seconds: float) -> None:
        base = max(self._next_allowed.get(host, 0.0), self._clock())
        self._next_allowed[host] = base + max(seconds, 0.0)

    def record(self, host: str, *, ok: bool) -> None:
        self._failures[host] = 0 if ok else self._failures.get(host, 0) + 1

    def trip(self, host: str) -> None:
        self._failures[host] = self.failure_limit

    def tripped(self, host: str) -> bool:
        return self._failures.get(host, 0) >= self.failure_limit

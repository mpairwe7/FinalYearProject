"""The crawler's rules, tested alone (ADR-0023).

Nothing here touches a network. Each RFC 9309 case quotes the section it
enforces, because ``respect_robots=True`` was a configuration field nothing read
for as long as the crawler existed: a claim of compliance needs to be checkable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ml.scripts.data_aug.crawl_policy import (
    CRAWL_ALLOWED_HOSTS,
    CRAWLER_TOKEN,
    USER_AGENT,
    HostGovernor,
    canonical_url,
    classify_failure,
    classify_url,
    content_digest,
    parse_robots,
    parse_usage_preferences,
    pdf_document_date,
    robots_from_status,
)


class TestIdentity:
    def test_the_user_agent_says_what_this_is_and_where_to_read_about_it(self):
        assert USER_AGENT.startswith(f"{CRAWLER_TOKEN}/")
        assert "+https://" in USER_AGENT
        assert "Mozilla" not in USER_AGENT, "a public service does not impersonate a browser"


class TestScope:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("http://ura.go.ug:80/download/CGMS/x.pdf", "https://ura.go.ug/download/CGMS/x.pdf"),
            ("https://URA.go.ug/en/vat/#rates", "https://ura.go.ug/en/vat/"),
            ("https://ura.go.ug/en/vat/?utm_source=x&fbclid=y", "https://ura.go.ug/en/vat/"),
            ("https://ura.go.ug/en/f/?wpdmdl=7&refresh=abc", "https://ura.go.ug/en/f/?wpdmdl=7"),
            ("https://ura.go.ug/en/choose-agents/?agent_type=vat_wht", "https://ura.go.ug/en/choose-agents/?agent_type=vat_wht"),
        ],
    )
    def test_urls_are_put_in_one_https_form(self, raw: str, expected: str):
        assert canonical_url(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "https://web.archive.org/web/2024/https://ura.go.ug/en/",
            "https://ura.go.ug.evil.example/en/",
            "https://evil.example/ura.go.ug/",
            "https://ereg.ura.go.ug/eportal/",  # a sign-in portal, not guidance
            "https://user:pw@ura.go.ug/en/",
            "https://ura.go.ug:8443/en/",
            "ftp://ura.go.ug/en/",
            "javascript:alert(1)",
            "mailto:services@ura.go.ug",
            "",
        ],
    )
    def test_anything_else_is_refused(self, raw: str):
        assert canonical_url(raw) == ""

    def test_relative_links_resolve_against_their_page(self):
        assert canonical_url("../vat/", "https://ura.go.ug/en/paye/") == "https://ura.go.ug/en/vat/"

    @pytest.mark.parametrize(
        ("url", "kind"),
        [
            ("https://ura.go.ug/en/value-added-tax-vat/", "page"),
            ("https://ura.go.ug/en/category/publications/public-notices/", "hub"),
            ("https://ura.go.ug/en/category/publications/public-notices/page/3/", "hub"),
            ("https://ura.go.ug/download-category/laws-and-acts/", "hub"),
            ("https://ura.go.ug/en/some-post/page/2/", "hub"),
            ("https://ura.go.ug/en/author/kkabahweza/page/23/", "skip"),
            ("https://ura.go.ug/en/bwg_gallery/talk-show/", "skip"),
            ("https://ura.go.ug/wp-json/wp/v2/posts", "skip"),
            ("https://ura.go.ug/en/category/x/feed/", "skip"),
            ("https://ura.go.ug/logo.png", "skip"),
            ("https://ura.go.ug/download/guide.pdf", "document"),
            ("https://ura.go.ug/en/?wpdmdl=9", "document"),
        ],
    )
    def test_url_classes(self, url: str, kind: str):
        assert classify_url(url) == kind

    def test_a_page_about_page_numbers_is_not_a_listing(self):
        assert classify_url("https://www.parliament.go.ug/page/tax-bills-2026") == "page"

    def test_only_named_hosts_and_never_a_wildcard(self):
        assert all("*" not in host for host in CRAWL_ALLOWED_HOSTS)


class TestRobots:
    """RFC 9309. https://www.rfc-editor.org/rfc/rfc9309"""

    URL = "https://ura.go.ug"

    def test_the_most_specific_match_wins_by_octets(self):
        """2.2.2: "The most specific match is the match that has the most octets"."""
        policy = parse_robots("User-agent: *\nDisallow: /en/\nAllow: /en/public/\n")
        assert policy.allows(f"{self.URL}/en/public/notice") is True
        assert policy.allows(f"{self.URL}/en/private") is False

    def test_an_equivalent_allow_beats_a_disallow(self):
        """2.2.2: "the "allow" rule SHOULD be used"."""
        assert parse_robots("User-agent: *\nDisallow: /en\nAllow: /en\n").allows(f"{self.URL}/en/x") is True

    def test_matching_is_case_sensitive_and_starts_at_the_first_octet(self):
        policy = parse_robots("User-agent: *\nDisallow: /Private\n")
        assert policy.allows(f"{self.URL}/private") is True
        assert policy.allows(f"{self.URL}/en/Private") is True
        assert policy.allows(f"{self.URL}/Private/x") is False

    def test_special_characters(self):
        """2.2.3: ``*`` is 0 or more of any character, ``$`` ends the pattern."""
        policy = parse_robots("User-agent: *\nDisallow: /*.pdf$\nDisallow: /tmp*/x\n")
        assert policy.allows(f"{self.URL}/a/b.pdf") is False
        assert policy.allows(f"{self.URL}/a/b.pdf?x=1") is True
        assert policy.allows(f"{self.URL}/tmp/1/2/x") is False
        assert policy.allows(f"{self.URL}/tmp/1/2/y") is True

    def test_a_query_string_can_be_ruled_on(self):
        policy = parse_robots("User-agent: *\nDisallow: /*?replytocom=\n")
        assert policy.allows(f"{self.URL}/en/post?replytocom=2") is False

    def test_non_ascii_rules_compare_percent_encoded(self):
        """2.2.2: such octets "MUST be percent-encoded ... prior to comparison"."""
        policy = parse_robots("User-agent: *\nDisallow: /en/café\n")
        assert policy.allows(f"{self.URL}/en/caf%C3%A9/x") is False
        assert policy.allows(f"{self.URL}/en/caf%c3%a9/x") is False

    def test_the_agent_group_is_found_case_insensitively(self):
        """2.2.1: "Crawlers MUST use case-insensitive matching"."""
        policy = parse_robots(f"User-agent: {CRAWLER_TOKEN.upper()}\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
        assert policy.allows(f"{self.URL}/x") is False

    def test_the_star_group_applies_only_when_no_group_names_this_crawler(self):
        text = f"User-agent: *\nDisallow: /\n\nUser-agent: {CRAWLER_TOKEN}\nDisallow: /private\n"
        policy = parse_robots(text)
        assert policy.allows(f"{self.URL}/en/") is True
        assert policy.allows(f"{self.URL}/private/x") is False

    def test_matching_groups_are_combined(self):
        """2.2.1: matching groups' rules MUST be combined into one group."""
        text = f"User-agent: {CRAWLER_TOKEN}\nDisallow: /a\n\nUser-agent: {CRAWLER_TOKEN}\nDisallow: /b\n"
        policy = parse_robots(text)
        assert policy.allows(f"{self.URL}/a") is False
        assert policy.allows(f"{self.URL}/b") is False

    def test_consecutive_user_agent_lines_share_a_group(self):
        policy = parse_robots(f"User-agent: other\nUser-agent: {CRAWLER_TOKEN}\nDisallow: /x\n")
        assert policy.allows(f"{self.URL}/x") is False

    def test_robots_txt_itself_is_implicitly_allowed(self):
        assert parse_robots("User-agent: *\nDisallow: /\n").allows(f"{self.URL}/robots.txt") is True

    def test_an_empty_disallow_is_no_rule_at_all(self):
        assert parse_robots("User-agent: *\nDisallow:\n").allows(f"{self.URL}/x") is True

    def test_a_4xx_means_no_file_so_no_rules(self):
        """2.3.1.3: "the crawler MAY access any resources on the server"."""
        policy = robots_from_status(404)
        assert policy is not None and policy.state == "unavailable"
        assert policy.allows(f"{self.URL}/en/") is True

    @pytest.mark.parametrize("status", [500, 502, 503, 0])
    def test_a_server_error_means_complete_disallow(self, status: int):
        """2.3.1.4: "the crawler MUST assume complete disallow"."""
        policy = robots_from_status(status)
        assert policy is not None and policy.state == "unreachable"
        assert policy.allows(f"{self.URL}/en/") is False

    def test_a_2xx_is_decided_by_the_body(self):
        assert robots_from_status(200) is None

    def test_ura_branded_404_page_served_as_robots_txt_parses_to_no_rules(self):
        """URA answers /robots.txt with its HTML "404 - Page Not Found" page."""
        html = "<!DOCTYPE html><html><head><title>404 - Page Not Found</title></head><body>x: y</body></html>"
        assert parse_robots(html).allows(f"{self.URL}/en/") is True

    def test_a_pathological_rule_cannot_hang_the_matcher(self):
        policy = parse_robots("User-agent: *\nDisallow: /" + "*a" * 2000 + "$\n")
        assert policy.allows(f"{self.URL}/" + "a" * 5000 + "b") is True

    def test_sitemaps_and_crawl_delay_are_read(self):
        policy = parse_robots("Sitemap: https://ura.go.ug/s.xml\nUser-agent: *\nCrawl-delay: 5\nDisallow: /x\n")
        assert policy.sitemaps == ("https://ura.go.ug/s.xml",)
        assert policy.crawl_delay == 5.0

    def test_a_huge_crawl_delay_cannot_stall_the_run(self):
        assert parse_robots("User-agent: *\nCrawl-delay: 86400\n").crawl_delay == 30.0


class TestUsagePreferences:
    """draft-ietf-aipref-attach-05 and -vocab-08. Drafts, so recorded and honoured conservatively."""

    def test_the_robots_rule_is_read_whole_site_and_by_path(self):
        policy = parse_robots("User-Agent: *\nAllow: /\nContent-Usage: train-ai=n\nContent-Usage: /ok/ train-ai=y\n")
        assert policy.usage_for("https://ura.go.ug/en/") == {"train-ai": "n"}
        assert policy.usage_for("https://ura.go.ug/ok/x") == {"train-ai": "y"}

    def test_n_overrides_y(self):
        assert parse_usage_preferences("ai-use=y, ai-use=n") == {"ai-use": "n"}

    @pytest.mark.parametrize("value", ["", "train-ai", "train-ai=maybe", "=n"])
    def test_an_unknown_value_is_not_an_error_it_is_simply_unknown(self, value: str):
        assert parse_usage_preferences(value) == {}


class TestPageSignals:
    @pytest.mark.parametrize(
        "title",
        ["404 - Page Not Found", "Page not found - Uganda Revenue Authority", "404", "Not Found", "Nothing Found", "Error"],
    )
    def test_soft_404s(self, title: str):
        assert classify_failure(title, "body") == "soft_404"

    @pytest.mark.parametrize(
        "title",
        [
            "Form DT-404 instructions - Uganda Revenue Authority",
            "Errors in returns - Uganda Revenue Authority",
            "Filing returns - Uganda Revenue Authority",
        ],
    )
    def test_ordinary_titles_are_left_alone(self, title: str):
        assert classify_failure(title, "x" * 2000) == ""

    def test_a_challenge_page_is_recognised_from_a_short_body(self):
        assert classify_failure("Just a moment...", "") == "challenge"
        assert classify_failure("URA", "Checking your browser before accessing the site.") == "challenge"

    def test_a_long_article_may_say_access_denied(self):
        assert classify_failure("URA portal", "Access denied means your TIN is locked. " * 80) == ""

    def test_a_counter_or_relative_date_is_not_a_change(self):
        """One form page had 18 captures from a download counter."""
        assert content_digest("# Form\nDownload 11108\nFile Size 3 KB") == content_digest("# Form\nDownload 11117\nFile Size 3 KB")
        assert content_digest("# Notice\n, 1 week ago\nBody") == content_digest("# Notice\n, 3 years ago\nBody")

    def test_a_change_in_the_guidance_is_a_change(self):
        assert content_digest("VAT is 18%") != content_digest("VAT is 16%")


class TestDocumentDates:
    def test_the_latest_stated_date_is_used(self):
        data = b"%PDF-1.7 /CreationDate (D:20230102000000) /ModDate (D:20260814120000+03'00')"
        assert pdf_document_date(data) == "2026-08-14"

    def test_xmp_metadata_counts(self):
        assert pdf_document_date(b"<xmp:ModifyDate>2025-03-04T00:00:00Z</xmp:ModifyDate>") == "2025-03-04"
        assert pdf_document_date(b'xmp:CreateDate="2024-05-06T00:00:00Z"') == "2024-05-06"

    def test_no_stated_date_is_no_evidence(self):
        assert pdf_document_date(b"%PDF-1.7 compressed metadata only") == ""

    def test_the_backend_reads_the_same_date(self):
        """``app`` may not import this module, so the two must be held together."""
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "App" / "backend"))
        from app.pdf_corpus import pdf_document_date as backend_date

        for data in (b"/ModDate (D:20260814120000)", b"<xmp:ModifyDate>2025-03-04</xmp:ModifyDate>", b"nothing"):
            assert backend_date(data) == pdf_document_date(data)


class TestOneIdentityAndOneAllowlist:
    def test_the_backend_fetchers_use_the_same_identity(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "App" / "backend"))
        from app.pdf_corpus import CORPUS_FETCH_USER_AGENT

        assert CORPUS_FETCH_USER_AGENT == USER_AGENT

    def test_every_crawl_host_is_one_the_corpus_will_accept(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "App" / "backend"))
        from app.pdf_corpus import (
            ALLOWED_STATUTORY_PDF_HOSTS,
            DEFAULT_MDA_PDF_SOURCE_URLS,
            MDA_STATUTORY_PDF_REGISTRY,
        )
        from ml.scripts.data_aug.crawler import MDA_CANONICAL_FILENAMES, MDA_STATUTORY_PDF_URLS

        assert CRAWL_ALLOWED_HOSTS <= ALLOWED_STATUTORY_PDF_HOSTS
        assert MDA_STATUTORY_PDF_URLS == DEFAULT_MDA_PDF_SOURCE_URLS
        assert MDA_CANONICAL_FILENAMES == {
            entry["url"]: entry["filename"] for entry in MDA_STATUTORY_PDF_REGISTRY
        }
        for mda_host in (
            "nita.go.ug",
            "pdpo.go.ug",
            "egpuganda.go.ug",
            "nira.go.ug",
            "ursb.go.ug",
            "unbs.go.ug",
            "ppda.go.ug",
            "finance.go.ug",
            "ulii.org",
            "media.ulii.org",
        ):
            assert mda_host in CRAWL_ALLOWED_HOSTS


class TestHostGovernor:
    def _governor(self, **kwargs: object) -> tuple[HostGovernor, list[float]]:
        clock = [0.0]
        slept: list[float] = []

        def sleep(seconds: float) -> None:
            slept.append(seconds)
            clock[0] += seconds

        return HostGovernor(2.0, clock=lambda: clock[0], sleep=sleep, **kwargs), slept  # type: ignore[arg-type]

    def test_requests_to_one_host_are_spaced(self):
        governor, slept = self._governor()
        governor.wait("ura.go.ug")
        governor.wait("ura.go.ug")
        assert slept == [2.0]

    def test_hosts_are_independent(self):
        governor, slept = self._governor()
        governor.wait("ura.go.ug")
        governor.wait("www.parliament.go.ug")
        assert slept == []

    def test_a_longer_crawl_delay_wins(self):
        governor, slept = self._governor()
        governor.wait("ura.go.ug", 10.0)
        governor.wait("ura.go.ug")
        assert slept == [10.0]

    def test_retry_after_pushes_the_next_request_back(self):
        governor, slept = self._governor()
        governor.wait("ura.go.ug")
        governor.defer("ura.go.ug", 30.0)
        governor.wait("ura.go.ug")
        assert slept == [32.0]

    def test_repeated_failure_stops_asking_and_a_success_resets_it(self):
        governor, _ = self._governor(failure_limit=3)
        governor.record("ura.go.ug", ok=False)
        governor.record("ura.go.ug", ok=False)
        governor.record("ura.go.ug", ok=True)
        governor.record("ura.go.ug", ok=False)
        assert governor.tripped("ura.go.ug") is False
        governor.record("ura.go.ug", ok=False)
        governor.record("ura.go.ug", ok=False)
        assert governor.tripped("ura.go.ug") is True

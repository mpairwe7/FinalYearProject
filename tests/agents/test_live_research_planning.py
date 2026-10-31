"""When a turn needs the live web, and what the taxpayer hears when it cannot be had.

The trigger set is a precision/recall trade: every false positive is an external
search and a slower turn, every false negative is an out-of-date answer that
looks current. Both lists below are the contract.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from time import monotonic, sleep

import pytest

from app import web_research as wr
from app.web_search_providers import ChainResult, RawResult


@pytest.fixture(autouse=True)
def _clean(tmp_db):
    wr.clear_web_search_cache()
    yield
    wr.clear_web_search_cache()


class TestRecencyIntent:
    @pytest.mark.parametrize(
        "question",
        [
            "What is the current URA customs exchange rate this week?",
            "Has the deadline for filing returns been extended this month?",
            "Is the URA portal working today?",
            "What are the latest changes to rental tax this year?",
            "Is there any new URA announcement about EFRIS?",
            "Who is the current Commissioner General of URA?",
            "What did the Minister of Finance say in the budget speech for 2026/27?",
            "Are there any recent changes in VAT?",
            "Has the filing deadline been postponed?",
            "Is EFRIS down?",
        ],
    )
    def test_questions_about_the_present_look_at_the_present(self, question):
        assert wr.detect_research_triggers(question), question

    @pytest.mark.parametrize(
        "question",
        [
            "What is the current VAT rate?",
            "I recently bought a car, how do I register it?",
            "What is the latest date for filing VAT returns?",
            "How do I pay my tax today?",
            "What is PAYE on my salary this month?",
            "What's new?",
            "What is the latest football score?",
            "How do I register for a TIN?",
        ],
    )
    def test_questions_with_a_settled_answer_do_not_spend_a_search(self, question):
        assert wr.detect_research_triggers(question) == [], question

    def test_a_recency_query_uses_the_questions_terms_and_this_year(self):
        query = "What are the latest changes to rental tax?"

        spec = wr.build_search_queries(query, wr.detect_research_triggers(query))[0]

        assert spec.trigger_category == wr.TRIGGER_RECENCY
        assert "rental" in spec.query
        assert str(datetime.now().year) in spec.query
        assert spec.volatile is True


class TestRetrievalSignals:
    QUESTION = "How is rental income taxed for a landlord?"

    def test_an_empty_retrieval_is_a_corpus_gap(self):
        cats = {t.category for t in wr.detect_research_triggers(self.QUESTION, hits=[])}

        assert cats == {wr.TRIGGER_UNVERIFIED_OR_LOW_CONF}

    def test_retrieval_that_has_not_run_is_not_a_gap(self):
        assert wr.detect_research_triggers(self.QUESTION, hits=None) == []

    def test_an_empty_retrieval_with_no_tax_subject_stays_home(self):
        assert wr.detect_research_triggers("Tell me a joke", hits=[]) == []

    def test_an_rrf_only_hit_is_unknown_confidence_not_low(self):
        # Reading RRF's 0.03 as low sent every question to the web whenever the
        # reranker was down, which is when the system can least afford it.
        hits = [{"text": "x", "source": "a", "score_rrf": 0.03}]

        assert wr.detect_research_triggers(self.QUESTION, hits=hits) == []

    def test_a_calibrated_low_score_still_triggers(self):
        hits = [{"text": "x", "source": "a", "score_norm": 0.2}]

        cats = {t.category for t in wr.detect_research_triggers(self.QUESTION, hits=hits)}

        assert wr.TRIGGER_UNVERIFIED_OR_LOW_CONF in cats


class TestStaleEdition:
    OLD = {"text": "x", "source": "HANDBOOK-FY-2024-25", "fiscal_year": "FY2024-25", "score_norm": 0.9}
    CURRENT = {"text": "y", "source": "HANDBOOK-FY-2026-27", "fiscal_year": "FY2026-27", "score_norm": 0.9}

    def _stale(self, question: str, hits: list[dict]) -> bool:
        triggers = wr.detect_research_triggers(question, hits=hits, current_fy="FY2026-27")
        return any(t.category == wr.TRIGGER_STALE_CORPUS for t in triggers)

    def test_a_definition_does_not_change_with_the_edition(self):
        assert not self._stale("What is a TIN?", [self.OLD])

    def test_a_figure_does(self):
        assert self._stale("What is the PAYE threshold?", [self.OLD])

    def test_the_newest_edition_in_view_decides(self):
        assert not self._stale("What is the PAYE threshold?", [self.OLD, self.CURRENT])


class TestBudgets:
    QUESTION = (
        "Is the penalty waiver public notice out, has the Finance Bill been assented, "
        "and what about the EAC CET stay of application?"
    )

    def test_every_trigger_gets_a_search_before_any_gets_two(self):
        seen: list[str] = []

        def fetch(query: str) -> str:
            seen.append(query)
            return ""

        wr.run_web_research(self.QUESTION, search_http_fetcher=fetch, persist_files=False)

        assert len(seen) == wr.WEB_RESEARCH_MAX_QUERIES
        assert any(q.startswith("site:ulii.org") for q in seen), "the Bill was never asked about"
        assert any("site:eac.int" in q for q in seen), "the tariff was never asked about"

    def test_a_trigger_over_the_allowance_is_recorded(self, monkeypatch):
        monkeypatch.setattr(wr, "WEB_RESEARCH_MAX_QUERIES", 1)

        outcome = wr.run_web_research(
            self.QUESTION, search_http_fetcher=lambda _q: "", persist_files=False
        )

        assert len(outcome.audit["unchecked_triggers"]) == 2

    def test_a_topic_left_out_is_disclosed_even_when_another_was_found(self, monkeypatch):
        monkeypatch.setattr(wr, "WEB_RESEARCH_MAX_QUERIES", 1)
        body = (
            '<a class="result__a" href="https://ura.go.ug/en/notice/">Notice</a>'
            '<a class="result__snippet">Penalty waiver.</a>'
        )

        outcome = wr.run_web_research(self.QUESTION, search_http_fetcher=lambda _q: body, persist_files=False)

        assert outcome.web_hits
        assert wr.LIVE_PARTIAL_DISCLOSURE in outcome.disclosure
        assert "Not checked this turn: " in outcome.live_check_notice

    def test_a_topic_whose_search_failed_is_disclosed_too(self):
        body = (
            '<a class="result__a" href="https://ura.go.ug/en/notice/">Notice</a>'
            '<a class="result__snippet">Penalty waiver.</a>'
        )

        def partly_down(query: str) -> str:
            if query.startswith("site:ulii.org"):
                raise RuntimeError("HTTP 503")
            return body

        outcome = wr.run_web_research(self.QUESTION, search_http_fetcher=partly_down, persist_files=False)

        assert wr.TRIGGER_BILL_ASSENT in outcome.audit["unchecked_triggers"]
        assert wr.LIVE_PARTIAL_DISCLOSURE in outcome.disclosure

    def test_a_failed_search_is_on_record_even_when_nothing_was_admitted(self):
        def partly_down(query: str) -> str:
            if query.startswith("site:ulii.org"):
                raise RuntimeError("HTTP 503")
            return ""

        outcome = wr.run_web_research(self.QUESTION, search_http_fetcher=partly_down, persist_files=False)

        assert outcome.status == wr.LIVE_UNAVAILABLE
        assert wr.TRIGGER_BILL_ASSENT in outcome.audit["unchecked_triggers"]
        # "Could not check" already covers it; a second sentence would only repeat it.
        assert wr.LIVE_PARTIAL_DISCLOSURE not in outcome.disclosure

    def test_a_complete_check_says_nothing_about_parts(self):
        outcome = wr.run_web_research(
            "Is there a penalty waiver public notice?",
            search_http_fetcher=lambda _q: (
                '<a class="result__a" href="https://ura.go.ug/en/notice/">Notice</a>'
                '<a class="result__snippet">Penalty waiver.</a>'
            ),
            persist_files=False,
        )

        assert wr.LIVE_PARTIAL_DISCLOSURE not in outcome.disclosure

    def test_the_same_query_from_two_triggers_does_not_share_a_cache_entry(self, monkeypatch):
        calls: list[int] = []

        def chain(*_a, **_k):
            calls.append(1)
            return ChainResult("empty")

        monkeypatch.setattr(wr, "search_with_failover", chain)
        for category in (wr.TRIGGER_PUBLIC_NOTICE, wr.TRIGGER_RECENCY):
            wr.search_spec(wr.SearchSpec(category, "t", "public_notice", "same query", ("ura.go.ug",)))

        assert len(calls) == 2

    def test_a_hung_search_cannot_hold_the_turn(self, monkeypatch):
        monkeypatch.setattr(wr, "WEB_RESEARCH_SEARCH_BUDGET_SECONDS", 0.2)

        def hang(_query: str) -> str:
            sleep(1.5)
            return ""

        started = monotonic()
        outcome = wr.run_web_research(
            "Is there a penalty waiver public notice?", search_http_fetcher=hang, persist_files=False
        )

        assert monotonic() - started < 1.0
        assert outcome.status == wr.LIVE_UNAVAILABLE

    def test_the_cache_is_bounded(self, monkeypatch):
        monkeypatch.setattr(wr, "_L1_SEARCH_CACHE_MAX_ENTRIES", 3)
        monkeypatch.setattr(wr, "search_with_failover", lambda *_a, **_k: ChainResult("empty"))

        for index in range(10):
            spec = wr.SearchSpec(wr.TRIGGER_PUBLIC_NOTICE, "t", "public_notice", f"q{index}", ("ura.go.ug",))
            wr.search_spec(spec)

        assert len(wr._L1_SEARCH_CACHE) == 3

    def test_a_failed_search_is_only_remembered_briefly(self, monkeypatch):
        puts: list[int] = []
        monkeypatch.setattr(wr, "_cache_put", lambda _k, _a, ttl: puts.append(ttl))
        monkeypatch.setattr(wr, "search_with_failover", lambda *_a, **_k: ChainResult("unavailable"))
        spec = wr.SearchSpec(wr.TRIGGER_PUBLIC_NOTICE, "t", "public_notice", "q", ("ura.go.ug",), volatile=True)

        wr.search_spec(spec)

        assert puts == [wr.WEB_SEARCH_NEGATIVE_CACHE_TTL_SECONDS]

    def test_volatile_results_expire_sooner(self, monkeypatch):
        puts: list[int] = []
        rows = (RawResult("Notice", "https://ura.go.ug/en/notice/", "Waiver."),)
        monkeypatch.setattr(wr, "_cache_put", lambda _k, _a, ttl: puts.append(ttl))
        monkeypatch.setattr(wr, "search_with_failover", lambda *_a, **_k: ChainResult("ok", rows, "tavily"))

        for volatile in (True, False):
            wr.search_spec(
                wr.SearchSpec(wr.TRIGGER_PUBLIC_NOTICE, "t", "public_notice", "q", ("ura.go.ug",), volatile=volatile)
            )

        assert puts == [wr.WEB_SEARCH_VOLATILE_CACHE_TTL_SECONDS, wr.WEB_SEARCH_CACHE_TTL_SECONDS]


class TestNoExpiryDates:
    def test_queries_follow_the_clock(self, monkeypatch):
        class January2028(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2028, 1, 15, tzinfo=tz)

        monkeypatch.setattr(wr, "datetime", January2028)
        query = "Is there a penalty waiver public notice?"

        specs = wr.build_search_queries(query, wr.detect_research_triggers(query))

        assert "2027 OR 2028" in specs[0].query
        assert all("2026" not in spec.query for spec in specs)

    @pytest.mark.parametrize(
        ("day", "expected"),
        [
            (date(2026, 6, 30), "FY2025-26"),
            (date(2026, 7, 1), "FY2026-27"),
            (date(2099, 12, 31), "FY2099-00"),
        ],
    )
    def test_the_fiscal_year_turns_on_the_first_of_july(self, day, expected):
        assert wr.fiscal_year_on(day) == expected


class TestOutcomeStatus:
    QUESTION = "Is there a penalty waiver public notice?"

    def test_an_outage_is_reported_as_one(self):
        def down(_query: str) -> str:
            raise RuntimeError("HTTP 503")

        outcome = wr.run_web_research(self.QUESTION, search_http_fetcher=down, persist_files=False)

        assert outcome.status == wr.LIVE_UNAVAILABLE
        assert outcome.disclosure == wr.LIVE_DISCLOSURES[wr.LIVE_UNAVAILABLE]
        assert "No live evidence exists" in outcome.live_check_notice
        assert ("web_research.completed", {"status": wr.LIVE_UNAVAILABLE, "sources": 0}) in outcome.events

    def test_nothing_found_is_not_worded_as_nothing_exists(self):
        outcome = wr.run_web_research(self.QUESTION, search_http_fetcher=lambda _q: "", persist_files=False)

        assert outcome.status == wr.LIVE_NO_RESULT
        assert "does not prove" in outcome.disclosure

    def test_no_provider_configured_is_an_honest_unavailable(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        for name in ("TAVILY_API_KEY", "BRAVE_SEARCH_API_KEY", "SEARXNG_URL"):
            monkeypatch.delenv(name, raising=False)

        outcome = wr.run_web_research(self.QUESTION, persist_files=False)

        assert outcome.status == wr.LIVE_UNAVAILABLE

    def test_the_audit_record_carries_no_taxpayer_text(self):
        query = "my landlord john mukasa asks about a penalty waiver public notice"
        body = (
            '<a class="result__a" href="https://ura.go.ug/en/notice/">Notice</a>'
            '<a class="result__snippet">Penalty waiver.</a>'
        )

        outcome = wr.run_web_research(query, search_http_fetcher=lambda _q: body, persist_files=False)

        assert "mukasa" not in str(outcome.audit).lower()
        assert outcome.audit["sources"][0]["url"] == "https://ura.go.ug/en/notice/"
        assert outcome.audit["queries_sha256"]

    def test_a_query_needing_no_check_owes_no_disclosure(self):
        outcome = wr.run_web_research("How do I register for a TIN?", persist_files=False)

        assert (outcome.triggered, outcome.disclosure) == (False, "")


class _Retriever:
    is_ready = True
    _ready = True
    _reranker = None

    def initialize(self) -> bool:
        return True

    @staticmethod
    def _hit() -> dict:
        text = "URA may waive penalties under the Tax Procedures Code on application by the taxpayer."
        return {
            "text": text,
            "answer": text,
            "question": "Can URA waive penalties?",
            "source": "ura_faq.csv",
            "chunk_id": "c1",
            "page": "",
            "section": "Penalties",
            "doc_type": "csv",
            "score_norm": 0.92,
            "score_rrf": 0.03,
        }

    def search(self, *_a, **_k) -> list[dict]:
        return [self._hit()]

    def search_planned(self, *_a, **_k) -> list[dict]:
        return [self._hit()]


QUESTION = "Is there an active penalty waiver public notice?"
UNAVAILABLE = wr.LIVE_DISCLOSURES[wr.LIVE_UNAVAILABLE]


@pytest.fixture
def model(fresh_registry, monkeypatch):
    # Flags go through the environment: test_integration.py reloads app.flags,
    # so flags.set() can land on a different object from the one service.py holds.
    from app.service import ChatModel

    monkeypatch.setenv("FLAG_LIVE_WEB_RESEARCH", "true")
    monkeypatch.setenv("FLAG_SEMANTIC_CACHE", "true")
    monkeypatch.setattr(wr, "search_with_failover", lambda *_a, **_k: ChainResult("unavailable"))
    instance = ChatModel()
    instance._retriever = _Retriever()
    instance._retriever_ready = True
    return instance


class TestServingPaths:
    def test_rest_tells_the_taxpayer_the_check_failed(self, model):
        result = model.generate(QUESTION)  # nosemgrep: ura-llm01-raw-user-input-to-llm

        assert UNAVAILABLE in result["reply"]
        assert result["_live_check"]["status"] == wr.LIVE_UNAVAILABLE

    def test_rest_says_nothing_extra_when_no_check_was_needed(self, model):
        result = model.generate("What documents do I need to apply for an objection?")  # nosemgrep: ura-llm01-raw-user-input-to-llm

        assert UNAVAILABLE not in result["reply"]

    def test_a_crashed_research_step_is_still_disclosed(self, model, monkeypatch):
        def explode(*_a, **_k):
            raise RuntimeError("boom")

        monkeypatch.setattr(wr, "run_web_research", explode)

        result = model.generate(QUESTION)  # nosemgrep: ura-llm01-raw-user-input-to-llm

        assert UNAVAILABLE in result["reply"]

    def test_the_stream_path_carries_the_same_disclosure(self, model):
        result = model.generate_retrieval_only(QUESTION)  # nosemgrep: ura-llm01-raw-user-input-to-llm

        assert result["_live_check_disclosure"] == UNAVAILABLE
        assert result["_live_check"]["status"] == wr.LIVE_UNAVAILABLE
        assert any(name == "web_research.completed" for name, _ in result["_web_research_events"])

    def test_a_streamed_turn_ends_with_the_disclosure(self, model):
        from app.service import run_chat_turn

        async def collect() -> list:
            return [
                event
                async for event in run_chat_turn(
                    model,
                    message=QUESTION,
                    conversation_id="c-live",
                    top_k=4,
                    locale="en",
                    session_id="s-live",
                    request_id="r-live",
                    user_id="",
                    tenant_id="default",
                )
            ]

        events = asyncio.run(collect())

        served = "".join(str(data) for name, data in events if name in ("token", "revision"))
        assert UNAVAILABLE in served

    def test_a_time_sensitive_answer_is_neither_served_from_nor_stored_in_the_cache(self, model, monkeypatch):
        stale = {"reply": "Yesterday's cached answer.", "retrieval_mode": "hybrid", "sources": [], "citations": []}
        stored: list[str] = []
        monkeypatch.setattr(model._cache, "get", lambda *_a, **_k: dict(stale))
        monkeypatch.setattr(model._cache, "put", lambda query, *_a, **_k: stored.append(query))

        result = model.generate(QUESTION)  # nosemgrep: ura-llm01-raw-user-input-to-llm

        assert "Yesterday" not in result["reply"]
        assert stored == []

    def test_an_ordinary_answer_still_uses_the_cache(self, model, monkeypatch):
        cached = {"reply": "A cached answer.", "retrieval_mode": "hybrid", "sources": [], "citations": []}
        monkeypatch.setattr(model._cache, "get", lambda *_a, **_k: dict(cached))

        result = model.generate("What documents do I need to apply for an objection?")  # nosemgrep: ura-llm01-raw-user-input-to-llm

        assert result["reply"].startswith("A cached answer.")

    def test_the_audit_row_records_the_failed_check(self, model):
        from app.audit.turns import build_turn_payload

        result = model.generate(QUESTION)  # nosemgrep: ura-llm01-raw-user-input-to-llm

        payload = build_turn_payload(message=QUESTION, result=result, channel="rest")

        assert payload["live_check"]["status"] == wr.LIVE_UNAVAILABLE


class TestLocale:
    def test_a_vernacular_turn_searches_on_its_english_form(self, model, monkeypatch):
        # The triggers and the search vocabulary read English. Research ran on
        # the question as typed, so it never fired for most of the locales.
        seen: list[str] = []
        real = wr.run_web_research

        def spy(query, **kwargs):
            seen.append(query)
            return real(query, **kwargs)

        monkeypatch.setattr(wr, "run_web_research", spy)
        monkeypatch.setattr(
            model,
            "_english_router_form",
            lambda message, rewritten, locale: (QUESTION, QUESTION),
        )

        result = model.generate("Waliwo ekiragiro kya gavumenti?", locale="lg")  # nosemgrep: ura-llm01-raw-user-input-to-llm

        assert seen == [QUESTION]
        assert result["_live_check"]["status"] == wr.LIVE_UNAVAILABLE


class TestLedgerFresh:
    def test_a_recently_verified_topic_carries_no_warning(self, monkeypatch):
        monkeypatch.setattr(wr, "should_skip_web_search", lambda _t: True)

        outcome = wr.run_web_research("Is there a penalty waiver public notice?", persist_files=False)

        assert outcome.status == wr.LIVE_LEDGER_FRESH
        assert outcome.skipped_by_ledger is True
        assert (outcome.live_check_notice, outcome.disclosure) == ("", "")


class TestEveryExit:
    """The check is disclosed and audited however the turn ends."""

    @pytest.fixture
    def empty(self, model):
        class Nothing(_Retriever):
            def search(self, *_a, **_k) -> list[dict]:
                return []

            def search_planned(self, *_a, **_k) -> list[dict]:
                return []

        model._retriever = Nothing()
        return model

    def test_an_abstained_rest_turn_says_the_check_failed(self, empty):
        result = empty.generate(QUESTION)  # nosemgrep: ura-llm01-raw-user-input-to-llm

        assert result["retrieval_mode"] == "abstained"
        assert UNAVAILABLE in result["reply"]
        assert result["_live_check"]["status"] == wr.LIVE_UNAVAILABLE

    def test_an_abstained_stream_says_the_check_failed(self, empty):
        from app.service import run_chat_turn

        async def collect() -> list:
            return [
                event
                async for event in run_chat_turn(
                    empty,
                    message=QUESTION,
                    conversation_id="c-abstain",
                    top_k=4,
                    locale="en",
                    session_id="s-abstain",
                    request_id="r-abstain",
                    user_id="",
                    tenant_id="default",
                )
            ]

        events = asyncio.run(collect())

        assert UNAVAILABLE in "".join(str(data) for name, data in events if name in ("token", "revision"))

    @staticmethod
    def _audited(model, result: dict) -> dict:
        from app import service

        token = service._TURN_LIVE_CHECK.set({"audit": {"status": "unavailable"}, "disclosure": UNAVAILABLE})
        try:
            model._audit_turn(message="q", result=result, session_id="s", trace_ctx={})
        finally:
            service._TURN_LIVE_CHECK.reset(token)
        return result

    def test_a_clarifying_question_makes_no_claim_but_is_still_audited(self, model):
        result = self._audited(model, {"reply": "Which return do you mean?", "retrieval_mode": "clarification"})

        assert result["reply"] == "Which return do you mean?"
        assert result["_live_check"] == {"status": "unavailable"}

    @pytest.mark.parametrize("mode", ["hybrid", "calculator", "false_premise_rejected", "abstained"])
    def test_every_answering_exit_carries_it(self, model, mode):
        result = self._audited(model, {"reply": "An answer.", "retrieval_mode": mode})

        assert result["reply"].endswith(UNAVAILABLE)

    def test_a_graph_turn_on_rest_reaches_the_audit_row(self, model, monkeypatch):
        # The check ran inside the graph, out of sight of the REST code around it.
        from app.audit.turns import build_turn_payload

        monkeypatch.setenv("FLAG_LANGGRAPH", "true")
        monkeypatch.setattr("app.retriever.get_shared_retriever", lambda **_k: _Retriever())

        result = model.generate(QUESTION)  # nosemgrep: ura-llm01-raw-user-input-to-llm

        assert build_turn_payload(message=QUESTION, result=result, channel="rest")["live_check"]["status"] == (
            wr.LIVE_UNAVAILABLE
        )


class TestEveryLanguage:
    BODY = "Hakuna taarifa mpya kuhusu msamaha wa adhabu."

    def _localize(self, monkeypatch, text: str) -> str:
        from app import service

        real = service.localize_reply

        def stub(reply, locale, **kwargs):
            if reply == self.BODY:
                return reply  # already in the locale, so localize_reply leaves it alone
            if reply in wr.LIVE_DISCLOSURES.values() or reply == wr.LIVE_PARTIAL_DISCLOSURE:
                return "[sw] " + reply
            return real(reply, locale, **kwargs)

        monkeypatch.setattr(service, "localize_reply", stub)
        return real(text, "sw")

    def test_a_body_already_in_the_locale_does_not_strand_the_english_sentence(self, monkeypatch):
        served = self._localize(monkeypatch, f"{self.BODY}\n\n{UNAVAILABLE}")

        assert served == f"{self.BODY}\n\n[sw] {UNAVAILABLE}"

    def test_a_partial_check_is_translated_sentence_by_sentence(self, monkeypatch):
        unconfirmed = wr.LIVE_DISCLOSURES[wr.LIVE_UNCONFIRMED]

        served = self._localize(monkeypatch, f"{self.BODY}\n\n{unconfirmed} {wr.LIVE_PARTIAL_DISCLOSURE}")

        assert served == f"{self.BODY}\n\n[sw] {unconfirmed}\n\n[sw] {wr.LIVE_PARTIAL_DISCLOSURE}"

    def test_english_is_untouched(self):
        from app.service import localize_reply

        text = f"An answer.\n\n{UNAVAILABLE}"

        assert localize_reply(text, "en") == text


class TestCorpusSyncApproval:
    PDF_URL = "https://ura.go.ug/en/wp-content/uploads/2026/07/Guide-FY2026-27.pdf"

    @pytest.mark.parametrize(
        ("env", "approved", "expected"),
        [
            ("development", "", True),
            ("production", "", False),
            ("production", "false", False),
            ("production", "true", True),
        ],
    )
    def test_production_needs_an_explicit_approval(self, monkeypatch, env, approved, expected):
        monkeypatch.setenv("APP_ENV", env)
        monkeypatch.setenv("WEB_SYNC_PRODUCTION_APPROVED", approved)

        assert wr.corpus_sync_permitted() is expected

    def _research(self, monkeypatch, sync: list[str], query: str):
        from types import SimpleNamespace

        import app.corpus_sync as corpus_sync
        import app.ephemeral_pdf_sandbox as sandbox

        document = SimpleNamespace(
            url=self.PDF_URL, filename="Guide.pdf", stem="Guide", sha256="a" * 64, fiscal_year="FY2026-27", top_chunks=[]
        )
        monkeypatch.setattr(sandbox, "fetch_and_extract_sandbox_document", lambda *_a, **_k: document)

        def record(_doc, _retriever, *, topic_key, **_kw):
            sync.append(topic_key)
            return SimpleNamespace(action_taken="verified", superseded_source="")

        monkeypatch.setattr(corpus_sync, "compare_and_sync_ura_document", record)
        monkeypatch.setattr(corpus_sync, "serialize_sync_outcome", lambda _o: {})

        def fetch(search: str) -> str:
            if "eac.int" not in search:
                return ""
            return f'<a class="result__a" href="{self.PDF_URL}">Guide</a><a class="result__snippet">Tariff.</a>'

        return wr.run_web_research(query, search_http_fetcher=fetch, auto_sync_qdrant=True, persist_files=False)

    QUESTION = "Is the penalty waiver public notice out, and what about the EAC CET stay of application?"

    def test_a_flag_alone_cannot_change_the_production_corpus(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.delenv("WEB_SYNC_PRODUCTION_APPROVED", raising=False)
        synced: list[str] = []

        self._research(monkeypatch, synced, self.QUESTION)

        assert synced == []

    def test_the_ledger_records_the_topic_that_found_the_document(self, monkeypatch):
        # Not the turn's first trigger: that would mark the wrong topic as checked.
        monkeypatch.delenv("APP_ENV", raising=False)
        synced: list[str] = []

        self._research(monkeypatch, synced, self.QUESTION)

        assert synced == ["eac_cet_uganda_stay_schedule"]


class TestGraphParity:
    def _state(self, query: str = QUESTION):
        from app.agents.graphs.state import AgentGraphState

        return AgentGraphState(query=query, rewritten_query=query, locale="en", top_k=3)

    def test_the_graph_runs_the_check_once_however_often_it_retrieves(self, monkeypatch):
        from app.agents.graphs import main_graph

        monkeypatch.setenv("FLAG_LIVE_WEB_RESEARCH", "true")
        calls: list[str] = []

        def spy(*_a, **_k):
            calls.append("searched")
            return ChainResult("unavailable")

        monkeypatch.setattr(wr, "search_with_failover", spy)
        monkeypatch.setattr("app.retriever.get_shared_retriever", lambda **_k: _Retriever())
        state = self._state()

        main_graph.node_retrieve(state)
        searched_once = len(calls)
        main_graph.node_retrieve(state)

        assert searched_once >= 1
        assert len(calls) == searched_once
        assert state.live_check_disclosure == UNAVAILABLE

    def test_a_retried_retrieval_keeps_the_live_evidence(self, monkeypatch):
        # Reflection re-enters retrieval, and the check does not run again, so
        # the evidence the notice describes must survive the second pass.
        from app.agents.graphs import main_graph

        monkeypatch.setenv("FLAG_LIVE_WEB_RESEARCH", "true")
        rows = (RawResult("Notice", "https://ura.go.ug/en/notice/", "Penalty waiver."),)
        monkeypatch.setattr(wr, "search_with_failover", lambda *_a, **_k: ChainResult("ok", rows, "tavily"))
        monkeypatch.setattr("app.retriever.get_shared_retriever", lambda **_k: _Retriever())
        state = self._state()

        main_graph.node_retrieve(state)
        main_graph.node_retrieve(state)

        assert state.hits[0]["source_url"] == "https://ura.go.ug/en/notice/"
        assert sum(1 for hit in state.hits if hit.get("live_authority")) == 1
        assert state.retrieval_mode == "live_web_unconfirmed"

    def test_an_abstained_graph_reply_carries_the_disclosure(self):
        from app.agents.graphs import main_graph
        from app.agents.graphs.state import GraphOutcome

        state = self._state()
        state.outcome = GraphOutcome.ABSTAINED
        state.live_check_disclosure = UNAVAILABLE

        main_graph.node_respond(state)

        assert state.reply.endswith(UNAVAILABLE)

    def test_a_clarifying_graph_reply_does_not(self):
        from app.agents.graphs import main_graph
        from app.agents.graphs.state import GraphOutcome

        state = self._state()
        state.outcome = GraphOutcome.CLARIFY
        state.clarification_question = "Which return?"
        state.live_check_disclosure = UNAVAILABLE

        main_graph.node_respond(state)

        assert state.reply == "Which return?"

    def test_the_graph_appends_the_disclosure_to_an_answer(self):
        from app.agents.graphs import main_graph
        from app.agents.graphs.state import GraphOutcome

        state = self._state()
        state.reply = "URA may waive penalties on application."
        state.outcome = GraphOutcome.ANSWERED
        state.live_check_disclosure = UNAVAILABLE

        main_graph.node_respond(state)

        assert state.reply.endswith(UNAVAILABLE)

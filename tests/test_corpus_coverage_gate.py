"""Release gate for corpus coverage against real taxpayer questions (issue #303).

The harness lives in ``ml/pipelines/corpus_coverage.py``; this is what makes it
a *gate*. Four of the issue's acceptance criteria are structural and belong
here rather than in a report nobody reads:

  * a coverage floor per domain gates release (criterion 3);
  * every abstention is listed by question (criterion 4);
  * the question bank carries a domain-owner review record (criterion 5);
  * new corpus content requires an accompanying question (criterion 6).

Everything runs offline against the committed corpus — no Qdrant, no LLM, no
network — so it can gate every PR. The English figure is the one gated: the
indexed corpus is English, and Luganda/Kiswahili coverage is a property of the
translate-then-retrieve pipeline rather than of the corpus, measured by
``--mode api`` against a running deployment.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for extra in (str(PROJECT_ROOT), str(PROJECT_ROOT / "App" / "backend")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

from ml.pipelines import corpus_coverage as cc  # noqa: E402

#: The languages the taxpayer UI offers. Every bank question must exist in all
#: three or the multilingual run silently probes fewer questions than it says.
REQUIRED_LANGUAGES = ("en", "lg", "sw")


class BankShapeTest(unittest.TestCase):
    """The bank itself, before anything is measured with it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bank = cc.load_bank()
        cls.domains = cc.load_domains()

    def test_the_bank_is_not_empty(self) -> None:
        self.assertGreaterEqual(len(self.bank), 50, "a question bank this small proves nothing")

    def test_ids_are_unique(self) -> None:
        ids = [e.id for e in self.bank]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        self.assertEqual(duplicates, [], "duplicate bank ids make the gap list ambiguous")

    def test_every_question_exists_in_every_language(self) -> None:
        missing = [
            f"{e.id}:{lang}"
            for e in self.bank
            for lang in REQUIRED_LANGUAGES
            if not e.question.get(lang, "").strip()
        ]
        self.assertEqual(missing[:10], [], f"{len(missing)} question/language pairs are empty")

    def test_every_question_names_the_facts_a_correct_answer_carries(self) -> None:
        """``expect_any`` is what separates 'retrieved something' from 'answered'.

        A row without it counts any hit as coverage, which is how a percentage
        stays green while the wrong FAQ is being served.
        """
        bare = [e.id for e in self.bank if not [t for t in e.expect_any if t.strip()]]
        self.assertEqual(bare, [], "bank rows with no expected facts cannot detect a wrong answer")

    def test_the_file_is_one_json_object_per_line(self) -> None:
        raw = cc.BANK_PATH.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(raw, 1):
            if not line.strip():
                continue
            with self.subTest(line=lineno):
                self.assertIsInstance(json.loads(line), dict)


class RegistryTest(unittest.TestCase):
    """Acceptance criteria 5 and 6, enforced rather than asked for."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bank = cc.load_bank()
        cls.domains = cc.load_domains()

    def test_every_corpus_file_is_claimed_and_asked_about(self) -> None:
        """Criterion 6. Adding a CSV without a question fails here, by name."""
        problems = cc.check_bank_covers_corpus(self.bank, self.domains)
        self.assertEqual(problems, [], "\n  " + "\n  ".join(problems))

    def test_the_registry_and_the_corpus_directory_agree(self) -> None:
        claimed = {src for spec in self.domains.values() for src in spec.sources}
        on_disk = {p.name for p in cc.DATASET_DIR.glob("ura_*_faqs.csv")}
        self.assertEqual(claimed, on_disk)

    def test_every_domain_carries_a_review_record(self) -> None:
        """Criterion 5's structural half: who reviewed this bank, and when."""
        problems = cc.check_reviews(self.domains, require_ura=False)
        self.assertEqual(problems, [], "\n  " + "\n  ".join(problems))

    def test_domains_still_awaiting_ura_sign_off_are_reported_not_hidden(self) -> None:
        """Criterion 5's judgement half.

        No domain has URA sign-off yet, so the strict gate is off by default —
        a gate that fails every build on day one gets deleted rather than
        satisfied. What must not happen is the pending state becoming
        invisible: ``pending_ura_signoff`` is reported on every run, and the
        strict mode has to actually fail while entries remain.
        """
        pending = cc.pending_ura_signoff(self.domains)
        self.assertEqual(
            sorted(pending),
            sorted(self.domains),
            "some domains now claim URA sign-off — flip --require-ura-signoff on in CI",
        )
        strict = cc.check_reviews(self.domains, require_ura=True)
        self.assertTrue(strict, "strict mode passed while no domain is URA-reviewed")

    def test_floors_are_in_range(self) -> None:
        for name, spec in self.domains.items():
            with self.subTest(domain=name):
                self.assertGreater(spec.floor, 0.0)
                self.assertLessEqual(spec.floor, 1.0)


class CoverageFloorTest(unittest.TestCase):
    """Criterion 3: the measured English figure has to clear the floors."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bank = cc.load_bank()
        cls.domains = cc.load_domains()
        probe = cc._corpus_prober(translate=False)
        cls.results = cc.run(("en",), probe, cls.bank)
        cls.report = cc.summarise(cls.results, cls.domains, ("en",))

    def test_every_domain_clears_its_floor(self) -> None:
        below = [
            f"{name} {row['coverage']:.0%} < {row['floor']:.0%} "
            f"({row['abstained']} abstained, {row['weak']} weak of {row['total']})"
            for name, row in self.report["by_domain"].items()
            if not row["passed"]
        ]
        self.assertEqual(below, [], "\n  " + "\n  ".join(below))

    def test_the_whole_bank_clears_the_overall_floor(self) -> None:
        """The per-domain floors move in 25-point steps; this one does not."""
        floor = cc.load_overall_floor()
        coverage = self.report["totals"]["coverage"]
        self.assertGreaterEqual(
            coverage,
            floor,
            f"overall coverage {coverage:.1%} fell below the {floor:.0%} floor "
            f"({self.report['totals']['abstained']} abstained, "
            f"{self.report['totals']['weak']} weak of {self.report['totals']['scored']})",
        )

    def test_the_overall_floor_is_actually_set(self) -> None:
        """A floor of 0 is a gate that has been switched off by omission."""
        self.assertGreater(cc.load_overall_floor(), 0.0)

    def test_every_domain_in_the_registry_was_actually_measured(self) -> None:
        """A domain that drops out of the report is a floor that stopped gating."""
        self.assertEqual(set(self.report["by_domain"]), set(self.domains))

    def test_the_run_scored_every_probe(self) -> None:
        totals = self.report["totals"]
        self.assertEqual(totals["probes"], len(self.bank))
        self.assertEqual(totals["scored"], len(self.bank))
        self.assertEqual(totals["errors"], 0)

    def test_every_gap_is_listed_by_question(self) -> None:
        """Criterion 4. A percentage is not a work list."""
        gap_states = {"abstained", "weak", "deflected", "error"}
        expected = sum(1 for r in self.results if r.status in gap_states)
        self.assertEqual(len(self.report["gaps"]), expected)
        for gap in self.report["gaps"]:
            with self.subTest(gap=gap["id"]):
                self.assertTrue(gap["question"].strip(), "a gap with no question is not actionable")
                self.assertTrue(gap["domain"])
                self.assertIn(gap["status"], gap_states)


class TheBugThisGateWasBuiltForTest(unittest.TestCase):
    """Reproduce the objections gap and prove the gate fails on it.

    A gate nobody has watched fail is a gate nobody knows is wired up. This
    removes `ura_objections_and_appeals_faqs.csv` from the index — putting the
    corpus back in the exact state that shipped, where the only indexed
    sentence on the subject was a starter-pack line carrying no procedure —
    and checks that the objections domain drops through its floor with the
    production question named in the gap list.
    """

    def test_removing_the_objections_corpus_fails_the_objections_floor(self) -> None:
        from app import service

        real = service._load_faq_data

        def without_objections(data_dir):
            index, labels = real(data_dir)
            index.pop("objections_and_appeals", None)
            return index, labels

        bank = [e for e in cc.load_bank() if e.domain == "objections"]
        spec = {"objections": cc.load_domains()["objections"]}
        with patch.object(service, "_load_faq_data", without_objections):
            probe = cc._corpus_prober(translate=False)
        results = cc.run(("en",), probe, bank)
        report = cc.summarise(results, spec, ("en",))

        row = report["by_domain"]["objections"]
        self.assertFalse(row["passed"], "the gate passed with the objections corpus removed")
        self.assertLess(row["coverage"], row["floor"])

        asked = {g["question"] for g in report["gaps"]}
        self.assertIn(
            "How do I object to a tax assessment I disagree with?",
            asked,
            "the question that actually abstained in production is not in the gap list",
        )

    def test_the_same_questions_pass_with_the_corpus_present(self) -> None:
        """The control. Without it the test above proves only that the harness
        can fail, not that removing the corpus is what failed it."""
        bank = [e for e in cc.load_bank() if e.domain == "objections"]
        spec = {"objections": cc.load_domains()["objections"]}
        report = cc.summarise(
            cc.run(("en",), cc._corpus_prober(translate=False), bank), spec, ("en",)
        )
        self.assertTrue(report["by_domain"]["objections"]["passed"])


class ScoringTest(unittest.TestCase):
    """The classifier that decides what counts as coverage."""

    def test_nothing_retrieved_is_an_abstention(self) -> None:
        self.assertEqual(cc._classify("", ["18%"], False), ("abstained", ""))

    def test_a_hit_carrying_the_expected_fact_is_coverage(self) -> None:
        status, matched = cc._classify("The VAT rate is 18% of the supply.", ["18%"], True)
        self.assertEqual((status, matched), ("answered", "18%"))

    def test_a_hit_missing_every_expected_fact_is_weak_not_coverage(self) -> None:
        """The failure a hits/no-hits check cannot see: the wrong FAQ, served."""
        status, matched = cc._classify("VAT is an indirect tax.", ["18%"], True)
        self.assertEqual((status, matched), ("weak", ""))

    def test_matching_ignores_case(self) -> None:
        self.assertEqual(cc._classify("EFRIS is URA's system", ["efris"], True)[0], "answered")

    def test_transcript_recall_is_the_share_of_words_that_survived(self) -> None:
        self.assertEqual(cc._token_recall("what is the vat rate", "what is the vat rate"), 1.0)
        self.assertEqual(cc._token_recall("what is the vat rate", ""), 0.0)
        self.assertEqual(cc._token_recall("", "anything"), 0.0)
        self.assertAlmostEqual(cc._token_recall("vat rate", "the vat"), 0.5)


class NonEnglishScoringTest(unittest.TestCase):
    """A correct Luganda answer must not score as a wrong one.

    Replies are generated in English and translated, so `expect_any` terms
    made of English words ("exempt", "register") cannot be looked for in the
    translated reply — a paraphrase and a wrong answer look identical. The
    first trilingual run scored 16 Luganda probes `weak` for exactly that
    reason, which would have read as a product gap.
    """

    @staticmethod
    def _entry(expect, **overrides) -> cc.BankEntry:
        return cc.BankEntry(
            id="x", domain="vat", theme="",
            question={"en": "q", "lg": "q", "sw": "q"},
            expect_any=tuple(expect), expect_by_language=overrides,
        )

    def test_english_uses_the_terms_as_written(self) -> None:
        terms, scorable = self._entry(["exempt"]).expect_for("en")
        self.assertEqual((terms, scorable), (("exempt",), True))

    def test_figures_and_acronyms_survive_translation(self) -> None:
        for term in ("18%", "45", "1.5", "efris", "TIN", "prn"):
            with self.subTest(term=term):
                self.assertTrue(cc._is_language_neutral(term))

    def test_english_words_do_not(self) -> None:
        for term in ("exempt", "register", "commissioner", "grounds"):
            with self.subTest(term=term):
                self.assertFalse(cc._is_language_neutral(term))

    def test_a_row_keeps_only_its_neutral_terms_in_another_language(self) -> None:
        terms, scorable = self._entry(["18%", "standard", "rate"]).expect_for("lg")
        self.assertEqual((terms, scorable), (("18%",), True))

    def test_a_row_with_no_neutral_term_is_not_scorable_without_an_override(self) -> None:
        terms, scorable = self._entry(["exempt", "zero"]).expect_for("sw")
        self.assertEqual((terms, scorable), ((), False))

    def test_an_override_makes_it_scorable_again(self) -> None:
        entry = self._entry(["exempt"], sw=("hasamehewa",))
        terms, scorable = entry.expect_for("sw")
        self.assertEqual((terms, scorable), (("hasamehewa",), True))

    def test_the_bank_loader_reads_expect_any_lg_and_sw(self) -> None:
        import json
        import tempfile

        row = {
            "id": "x", "domain": "vat", "question": {"en": "a", "lg": "b", "sw": "c"},
            "expect_any": ["exempt"], "expect_any_lg": ["tekisasulwa"],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write(json.dumps(row) + "\n")
            path = Path(fh.name)
        try:
            entry = cc.load_bank(path)[0]
        finally:
            path.unlink()
        self.assertEqual(entry.expect_by_language, {"lg": ("tekisasulwa",)})
        self.assertEqual(entry.expect_for("lg"), (("tekisasulwa",), True))

    def test_unscorable_probes_stay_out_of_the_coverage_denominator(self) -> None:
        domains = {"vat": cc.DomainSpec("vat", "VAT", ("ura_vat_faqs.csv",), 0.5, {"by": "x"})}
        results = [
            cc.ProbeResult("a", "vat", "lg", "q", "answered"),
            cc.ProbeResult("b", "vat", "lg", "q", "unscorable"),
        ]
        report = cc.summarise(results, domains, ("lg",))
        self.assertEqual(report["totals"]["scored"], 1)
        self.assertEqual(report["totals"]["coverage"], 1.0)
        self.assertEqual(report["totals"]["unscorable"], 1)
        self.assertEqual(len(report["unscorable"]), 1)


class SamplingTest(unittest.TestCase):
    def test_sampling_takes_the_first_n_of_each_domain(self) -> None:
        bank = cc.load_bank()
        sampled = cc.sample_bank(bank, 1)
        self.assertEqual(len(sampled), len({e.domain for e in bank}))
        self.assertEqual(len({e.domain for e in sampled}), len(sampled))

    def test_sampling_is_stable_across_calls(self) -> None:
        bank = cc.load_bank()
        self.assertEqual(
            [e.id for e in cc.sample_bank(bank, 2)],
            [e.id for e in cc.sample_bank(bank, 2)],
        )


class ProgressTest(unittest.TestCase):
    """A sweep that takes hours must not be all-or-nothing.

    The voice mode is ~2 minutes per probe; an interrupted run used to leave
    nothing behind but the server's access log. `run` now reports after every
    bank entry — after all its languages, so a partial sweep is a whole number
    of questions rather than a ragged edge.
    """

    def test_progress_fires_once_per_entry_after_all_its_languages(self) -> None:
        bank = cc.load_bank()[:3]
        seen: list[tuple[str, int]] = []

        def probe(entry: cc.BankEntry, language: str) -> cc.ProbeResult:
            return cc.ProbeResult(entry.id, entry.domain, language, "q", "answered")

        cc.run(("en", "lg"), probe, bank, on_progress=lambda rows, e: seen.append((e.id, len(rows))))
        self.assertEqual([n for _, n in seen], [2, 4, 6])
        self.assertEqual([i for i, _ in seen], [e.id for e in bank])

    def test_run_still_works_without_a_progress_hook(self) -> None:
        bank = cc.load_bank()[:2]
        results = cc.run(
            ("en",),
            lambda e, l: cc.ProbeResult(e.id, e.domain, l, "q", "answered"),
            bank,
        )
        self.assertEqual(len(results), 2)


class SummaryShapeTest(unittest.TestCase):
    """Skipped probes must not be scored, and deflections must stay visible."""

    def setUp(self) -> None:
        self.domains = {
            "vat": cc.DomainSpec("vat", "VAT", ("ura_vat_faqs.csv",), 0.75, {"by": "x"}),
        }

    def _result(self, status: str, language: str = "en") -> cc.ProbeResult:
        return cc.ProbeResult("id", "vat", language, "q?", status)

    def test_a_skipped_probe_stays_out_of_the_denominator(self) -> None:
        report = cc.summarise(
            [self._result("answered"), self._result("skipped", "lg")],
            self.domains,
            ("en", "lg"),
        )
        self.assertEqual(report["totals"]["scored"], 1)
        self.assertEqual(report["totals"]["coverage"], 1.0)
        self.assertIsNone(report["by_language"]["lg"]["coverage"])
        self.assertEqual(report["by_language"]["lg"]["skipped"], 1)

    def test_a_deflection_counts_against_coverage_and_is_listed(self) -> None:
        report = cc.summarise(
            [self._result("answered"), self._result("deflected")], self.domains, ("en",)
        )
        self.assertEqual(report["totals"]["coverage"], 0.5)
        self.assertEqual([g["status"] for g in report["gaps"]], ["deflected"])

    def test_a_domain_below_its_floor_is_marked_failed(self) -> None:
        report = cc.summarise(
            [self._result("abstained"), self._result("answered")], self.domains, ("en",)
        )
        self.assertFalse(report["by_domain"]["vat"]["passed"])


class VoiceProbeTest(unittest.TestCase):
    """The spoken pipeline's probe, against a stubbed deployment.

    /v1/tts returns the reply audio as ``audio_base64`` and /v1/voice/chat as
    ``reply_audio_base64``. Reading the wrong key reports every spoken answer
    as silent, which is exactly the kind of harness bug that gets mistaken for
    a product bug.
    """

    @staticmethod
    def _entry() -> cc.BankEntry:
        return cc.BankEntry(
            id="vat-rate", domain="vat", theme="rate",
            question={"en": "What is the VAT rate?"}, expect_any=("18%",),
        )

    def _run(self, tts: dict, chat: dict) -> cc.ProbeResult:
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=tts if request.url.path == "/v1/tts" else chat)

        original = httpx.Client
        try:
            httpx.Client = lambda **kw: original(  # type: ignore[assignment]
                **{**kw, "transport": httpx.MockTransport(handler)}
            )
            probe = cc._voice_prober("http://stub", 5.0, 4)
            return probe(self._entry(), "en")
        finally:
            httpx.Client = original  # type: ignore[assignment]

    @staticmethod
    def _audio_b64(nbytes: int = 64) -> str:
        import base64

        return base64.b64encode(b"\x00" * nbytes).decode("ascii")

    def test_a_spoken_answer_is_scored_and_its_audio_measured(self) -> None:
        result = self._run(
            {"audio_base64": self._audio_b64(), "backend": "edge_tts",
             "voice": "en-US-AriaNeural", "sample_rate": 24000},
            {"transcript": "What is the VAT rate?", "reply": "The VAT rate is 18%.",
             "retrieval_mode": "hybrid", "sources": ["ura_vat_faqs.csv"],
             "reply_audio_base64": self._audio_b64(512), "asr_backend": "whisper_lora"},
        )
        self.assertEqual((result.status, result.matched), ("answered", "18%"))
        self.assertEqual(result.voice["reply_audio_bytes"], 512)
        self.assertEqual(result.voice["transcript_recall"], 1.0)
        self.assertEqual(result.voice["tts_backend"], "edge_tts")

    def test_a_stage_error_is_an_error_probe(self) -> None:
        """"No speech detected" is a voice failure, not a corpus gap."""
        result = self._run(
            {"audio_base64": self._audio_b64(), "backend": "mock", "sample_rate": 16000},
            {"transcript": "", "error": "No speech detected. Please speak clearly and try again."},
        )
        self.assertEqual(result.status, "error")
        self.assertIn("No speech detected", result.error)

    def test_tts_producing_nothing_is_reported_before_the_chat_call(self) -> None:
        result = self._run(
            {"audio_base64": "", "backend": "error", "error": "All TTS backends failed"},
            {"reply": "unused"},
        )
        self.assertEqual(result.status, "error")
        self.assertIn("TTS produced no audio", result.error)

    def test_a_mangled_transcript_lowers_recall_without_failing_the_probe(self) -> None:
        result = self._run(
            {"audio_base64": self._audio_b64(), "backend": "edge_tts", "sample_rate": 24000},
            {"transcript": "what is the rate", "reply": "The VAT rate is 18%.",
             "retrieval_mode": "hybrid", "sources": ["ura_vat_faqs.csv"],
             "reply_audio_base64": self._audio_b64(128)},
        )
        self.assertEqual(result.status, "answered")
        self.assertLess(result.voice["transcript_recall"], 1.0)


class LiveProbeClassificationTest(unittest.TestCase):
    """The API/voice probers, against a stubbed deployment.

    These paths decide what a live run reports, and getting them wrong is how
    a green release figure hides an abstaining deployment — so they are tested
    without needing one.
    """

    @staticmethod
    def _entry() -> cc.BankEntry:
        return cc.BankEntry(
            id="vat-rate", domain="vat", theme="rate",
            question={"en": "What is the VAT rate?"}, expect_any=("18%",),
        )

    def _run_api(self, payload: dict) -> cc.ProbeResult:
        import httpx

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        original = httpx.Client
        try:
            httpx.Client = lambda **kw: original(  # type: ignore[assignment]
                **{**kw, "transport": httpx.MockTransport(handler)}
            )
            probe = cc._api_prober("http://stub", 5.0, 4)
            return probe(self._entry(), "en")
        finally:
            httpx.Client = original  # type: ignore[assignment]

    def test_an_abstaining_deployment_is_reported_as_abstained(self) -> None:
        result = self._run_api({"reply": "I couldn't find…", "retrieval_mode": "abstained"})
        self.assertEqual(result.status, "abstained")

    def test_a_workflow_reply_counts_even_though_it_cites_nothing(self) -> None:
        """Guided flows, rate-table lookups and graph answers carry no sources.

        Scoring them on ``sources`` alone marked the TIN registration workflow
        — a correct, useful answer — as an abstention.
        """
        result = self._run_api(
            {"reply": "Happy to help. The VAT rate is 18%.", "retrieval_mode": "workflow",
             "sources": []}
        )
        self.assertEqual((result.status, result.matched), ("answered", "18%"))

    def test_a_clarifying_reply_is_a_deflection_not_an_answer(self) -> None:
        result = self._run_api(
            {"reply": "Which tax did you mean?", "retrieval_mode": "clarification"}
        )
        self.assertEqual(result.status, "deflected")

    def test_a_grounded_reply_without_the_expected_fact_is_weak(self) -> None:
        result = self._run_api(
            {"reply": "VAT is an indirect tax.", "retrieval_mode": "hybrid",
             "sources": ["ura_vat_faqs.csv"]}
        )
        self.assertEqual(result.status, "weak")

    def test_a_transport_failure_is_an_error_probe_not_a_crash(self) -> None:
        import httpx

        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        original = httpx.Client
        try:
            httpx.Client = lambda **kw: original(  # type: ignore[assignment]
                **{**kw, "transport": httpx.MockTransport(handler)}
            )
            probe = cc._api_prober("http://stub", 5.0, 4)
            result = probe(self._entry(), "en")
        finally:
            httpx.Client = original  # type: ignore[assignment]
        self.assertEqual(result.status, "error")
        self.assertIn("ConnectError", result.error)

    def test_a_bank_entry_missing_the_language_is_an_error_probe(self) -> None:
        probe = cc._api_prober("http://stub", 5.0, 4)
        result = probe(self._entry(), "lg")
        self.assertEqual(result.status, "error")
        self.assertIn("lg", result.error)


if __name__ == "__main__":
    unittest.main()

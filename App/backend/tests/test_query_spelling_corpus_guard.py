"""The English spelling corrector may only fix misspellings (G282).

Measured over URA's own 1,120 FAQ questions — correctly spelled text — the
corrector rewrote 261 of them (23%) before retrieval, routing, the cache and the
model saw them. Its fuzzy matcher treated any word outside a few hundred listed
words as a typo, and some of what it produced inverts the question:

    taxes -> taxis          pension -> person        exporting -> importing
    reductions -> deductions  reported -> exported   unregistered -> registered
    deregister -> register    Airport -> Import      fishing -> filing

Its texting shorthand turned legal references into words ("Part B" -> "Part
be", "Country C" -> "Country see"). The self-retrieval gate could not see any
of it: it searches with the FAQ text, not with the rewritten query.

A word the served corpus uses is now never corrected — the BM25 vocabulary the
retriever registers or the state file holds, and the FAQ rows the service
loads — and shorthand applies only to lowercase letters that label nothing.
"""

from __future__ import annotations

import csv
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import query as query_mod
from app.query import _fuzzy_correct_word, correct_spelling, register_corpus_text, rewrite

_FAQ_DIR = Path(__file__).resolve().parents[3] / "Data" / "dataset"


def _faq_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted(_FAQ_DIR.glob("ura_*_faqs.csv")):
        with path.open(encoding="utf-8", errors="replace") as fh:
            rows.extend(csv.DictReader(fh))
    return rows


def _faq_questions() -> list[str]:
    return [q for q in ((row.get("question") or "").strip() for row in _faq_rows()) if q]


def _faq_text() -> list[str]:
    """What ``ChatModel.__init__`` registers: each row's question and answer."""
    return [f"{row.get('question') or ''} {row.get('answer') or ''}" for row in _faq_rows()]


class _IsolatedVocabulary(unittest.TestCase):
    """Run with only the vocabulary a test installs, then restore the module."""

    def setUp(self) -> None:
        saved = (query_mod._REGISTERED_VOCAB, query_mod._STATE_FILE_VOCAB)

        def restore() -> None:
            query_mod._REGISTERED_VOCAB, query_mod._STATE_FILE_VOCAB = saved
            _fuzzy_correct_word.cache_clear()

        self.addCleanup(restore)
        query_mod._REGISTERED_VOCAB = frozenset()
        query_mod._STATE_FILE_VOCAB = frozenset()
        _fuzzy_correct_word.cache_clear()


class FaqCorpusGateTests(_IsolatedVocabulary):
    """The gate: correct text goes through the corrector unchanged."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.questions = _faq_questions()

    def test_the_corpus_is_present(self) -> None:
        self.assertGreater(len(self.questions), 1000)

    def test_no_faq_question_is_rewritten(self) -> None:
        """As a keyword-only deployment sees it: the FAQ rows are the whole corpus."""
        register_corpus_text(_faq_text())
        rewritten = [(q, correct_spelling(q)) for q in self.questions if correct_spelling(q) != q]
        self.assertEqual(rewritten, [], f"{len(rewritten)} correct FAQ questions were rewritten")

    def test_the_bm25_state_alone_protects_the_corpus_words(self) -> None:
        """As the retriever sees it: no FAQ rows registered, only a BM25 vocabulary."""
        vocab = {token for q in self.questions for token in re.findall(r"[a-z]+", q.lower())}
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "bm25_state.json"
            state.write_text(json.dumps({"vocab": {word: i for i, word in enumerate(sorted(vocab))}}))
            query_mod._STATE_FILE_VOCAB = None
            with mock.patch.object(query_mod, "_BM25_STATE_PATHS", (Path(tmp) / "missing.json", state)):
                for q in ("How do I pay my taxes online?", "Is VAT charged when exporting goods?"):
                    self.assertEqual(correct_spelling(q), q)


class InversionTests(_IsolatedVocabulary):
    def setUp(self) -> None:
        super().setUp()
        register_corpus_text(_faq_text())

    def test_words_that_used_to_flip_are_kept(self) -> None:
        for word in ("taxes", "pension", "exporting", "reductions", "reported", "Airport", "fishing", "importers"):
            with self.subTest(word=word):
                self.assertEqual(_fuzzy_correct_word(word), word)

    def test_whole_questions(self) -> None:
        for q in ("How do I pay my taxes online?", "Is my pension taxable?", "What do exporters pay?"):
            with self.subTest(q=q):
                self.assertEqual(rewrite(q, locale="en"), q)


class TyposStillCorrectedTests(_IsolatedVocabulary):
    def test_real_typos_are_fixed_with_or_without_a_corpus(self) -> None:
        for typo, fixed in (
            ("witholdng", "withholding"),
            ("penalti", "penalty"),
            ("regstration", "registration"),
            ("taxpayr", "taxpayer"),
            ("deadlne", "deadline"),
            ("tinn", "tin"),
        ):
            with self.subTest(typo=typo):
                self.assertEqual(_fuzzy_correct_word(typo), fixed)
                register_corpus_text(_faq_text())
                self.assertEqual(_fuzzy_correct_word(typo), fixed, "the FAQ text does not contain the typo")

    def test_registering_a_word_takes_effect_at_once(self) -> None:
        """The matcher is memoised; a registration must not be shadowed by it."""
        self.assertEqual(_fuzzy_correct_word("penaltz"), "penalty")
        query_mod.register_corpus_vocabulary(["penaltz"])
        self.assertEqual(_fuzzy_correct_word("penaltz"), "penaltz")


class ShorthandTests(unittest.TestCase):
    def test_texting_shorthand_is_still_expanded(self) -> None:
        self.assertEqual(correct_spelling("can u help me"), "can you help me")
        self.assertEqual(correct_spelling("how r u"), "how are you")
        self.assertEqual(correct_spelling("c the rules"), "see the rules")

    def test_everyday_nouns_do_not_block_shorthand(self) -> None:
        self.assertEqual(correct_spelling("what category r we"), "what category are we")
        self.assertEqual(correct_spelling("which plan u need"), "which plan you need")

    def test_letters_that_name_provisions_are_left_alone(self) -> None:
        for text in (
            "Item 26 Part B",
            "Item 26 part b",
            "Part B of the Second Schedule",
            "Country C",
            "Is Schedule c still in force?",
            "bank a/c number",
            "balance b/f and c/f",
            "exempt u/s 19",
            "see (b) and (c) below",
            "b) file return",
        ):
            with self.subTest(text=text):
                self.assertEqual(correct_spelling(text), text)

    def test_identity_entries_keep_the_users_capitalisation(self) -> None:
        q = "What does 'Elect To An Objection Decision' mean on the URA portal?"
        self.assertEqual(correct_spelling(q), q)


if __name__ == "__main__":
    unittest.main()

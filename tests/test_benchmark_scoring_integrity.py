"""The 1,000-FAQ harness must not score a locale it cannot measure.

``scripts/evaluate_1000_faqs_ngrok.py`` published "English 73.14%, Luganda
29.70%, Kiswahili 28.06%" and the 43-point gap was read as a model defect. It
was the scorer. Luganda answers were scored against English prose keywords
scraped off the English source answer, plus the anchors ``["omusolo", "ura"]``
— and ``"ura"`` was matched as a bare substring, so it was true of
``"accurate"``, ``"natural"`` and ``"insurance"``. The highest score a
*perfectly translated* Luganda answer set could reach was **30.4%**; the
reported 29.70% was 98% of that ceiling.

Every defect here is one G37 had already found and fixed in
``tests/load/tax_education_accuracy_eval.py`` (``docs/GAPS_AND_AGENTIC_ROADMAP.md``
§2.9), reintroduced in a harness written afterwards. That is what this file
exists to stop: the corrections are now properties with a gate on them, in the
tree CI actually runs.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = REPO_ROOT / "scripts" / "evaluate_1000_faqs_ngrok.py"


def _harness():
    spec = importlib.util.spec_from_file_location("_faq_harness", HARNESS_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_faq_harness"] = module
    spec.loader.exec_module(module)
    return module


ev = pytest.importorskip("httpx") and _harness()


def _faq(**overrides):
    defaults = {
        "faq_id": "T-1",
        "domain": "domestic",
        "topic": "vat",
        "query": "What is the VAT rate?",
        "expected_keywords": [],
    }
    defaults.update(overrides)
    return ev.EvalFAQ(**defaults)


# ---------------------------------------------------------------------------
# Token boundaries (G37)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("haystack", "term"),
    [
        ("this figure is accurate", "ura"),
        ("a natural person", "ura"),
        ("insurance premiums", "ura"),
        ("ugx 1,500,000 per year", "150"),
        ("the threshold is 300,000,000", "30"),
    ],
)
def test_terms_do_not_match_inside_longer_tokens(haystack, term):
    assert not ev._contains_term(haystack, term)


def test_terms_match_on_their_own_boundaries():
    assert ev._contains_term("the rate is 18% flat", "18%")
    assert ev._contains_term("contact ura today", "ura")


# ---------------------------------------------------------------------------
# "ura" is not a vernacular marker in any language
# ---------------------------------------------------------------------------
def test_ura_is_not_a_language_marker():
    for locale, markers in ev.LANGUAGE_MARKERS.items():
        assert "ura" not in markers, f"{locale} still uses 'ura' as a language marker"


def test_vernacular_anchors_exclude_ura():
    for locale, anchors in ev.VERNACULAR_ANCHORS.items():
        assert "ura" not in anchors, f"{locale} still anchors on 'ura'"


def test_an_english_reply_is_not_counted_as_a_local_language():
    english = "You must register for VAT and pay URA the accurate amount that is due."
    for locale in ("lg", "sw"):
        in_target, fallback = ev.language_fidelity(english, locale)
        assert not in_target
        assert fallback


def test_a_luganda_reply_is_recognised():
    luganda = "Omusolo gwa VAT guli ebitundu 18 ku buli kikumi."
    in_target, fallback = ev.language_fidelity(luganda, "lg")
    assert in_target
    assert not fallback


# ---------------------------------------------------------------------------
# The structural ceiling is gone
# ---------------------------------------------------------------------------
def test_a_perfect_vernacular_answer_can_score_full_marks():
    """The defect this whole file is about: a correct answer that could not pass."""
    faq = _faq(
        locale="lg",
        expected_keywords=["registration", "threshold", "VAT"],
        vernacular_keywords=["omusolo"],
        expected_numbers=["18%"],
    )
    reply = (
        "Omusolo gwa VAT guli ebitundu 18 ku buli kikumi. "
        "Okwewandiisa kwetaagisa nga ekkomo lituukiddwa. Ssente zino ze ziri."
    )
    scored = ev.score_reply(faq, reply, "hybrid")
    assert scored["scorable"]
    assert scored["language_ok"]
    assert scored["accuracy"] == 1.0


def test_english_prose_keywords_are_not_counted_against_a_vernacular_answer():
    """An English word with no vernacular rendering is not evidence either way."""
    faq = _faq(
        locale="lg",
        expected_keywords=["fiscalised", "gazetted", "consignment"],
        vernacular_keywords=["omusolo"],
    )
    scored = ev.score_reply(faq, "Omusolo gwa VAT gwe gugwa ku bizinensi.", "hybrid")
    assert scored["missing_terms"] == []
    assert scored["accuracy"] == 1.0


def test_an_english_fallback_reply_scores_low_and_is_flagged():
    """`localize_reply` returns English on every failure path. That must show."""
    faq = _faq(
        locale="lg",
        expected_keywords=["registration", "threshold", "returns"],
        vernacular_keywords=["omusolo"],
    )
    english = "You must complete registration once your turnover crosses the threshold and file returns."
    scored = ev.score_reply(faq, english, "hybrid")
    assert scored["english_fallback"]
    assert not scored["language_ok"]
    assert scored["accuracy"] < 0.25


# ---------------------------------------------------------------------------
# Non-answers (G37)
# ---------------------------------------------------------------------------
def test_a_slot_prompt_scores_zero_not_the_old_elicitation_floor():
    """The removed branch awarded 0.75 to any reply containing "how much"."""
    faq = _faq(expected_keywords=["turnover", "threshold"])
    scored = ev.score_reply(faq, "Sure — how much was your turnover last year?", "hybrid")
    assert scored["non_answer"]
    assert scored["accuracy"] == 0.0


def test_an_abstention_retrieval_mode_is_a_non_answer():
    faq = _faq(expected_keywords=["vat"])
    scored = ev.score_reply(faq, "VAT is a tax on consumption.", "abstained")
    assert scored["non_answer"]
    assert scored["accuracy"] == 0.0


# ---------------------------------------------------------------------------
# Scorability is declared, never silently zeroed
# ---------------------------------------------------------------------------
def test_an_item_with_no_measurable_evidence_is_unscorable():
    faq = _faq(locale="lg", expected_keywords=["gazetted"], vernacular_keywords=[])
    scored = ev.score_reply(faq, "Omusolo gwa VAT.", "hybrid")
    assert not scored["scorable"]


def test_weights_renormalise_over_the_components_an_item_actually_has():
    """An item with no figures must not be scored out of 0.7."""
    faq = _faq(expected_keywords=["vat", "rate"], expected_numbers=[])
    scored = ev.score_reply(faq, "The VAT rate applies to taxable supplies.", "hybrid")
    assert scored["accuracy"] == 1.0


# ---------------------------------------------------------------------------
# The corpus itself
# ---------------------------------------------------------------------------
def test_every_non_english_item_declares_its_query_language():
    for faq in ev.build_1000_faqs_dataset():
        assert faq.query_locale in ("en", "lg", "sw")
        if faq.query_locale != "en":
            assert faq.query_locale == faq.locale


def test_the_corpus_reaches_the_vernacular_input_path_at_all():
    """Every item asking in English exercises output translation and nothing else.

    `FLAG_TRANSLATE_RETRIEVE`, the query rewriter and
    `WorkflowRegistry.match_trigger` all read the question. G39 — the workflow
    router capturing a local-language question as a task — lives behind that
    door and no English-query probe can reach it.
    """
    faqs = ev.build_1000_faqs_dataset()
    vernacular = [f for f in faqs if f.query_locale != "en"]
    assert vernacular, "no probe in this corpus asks in Luganda or Kiswahili"
    assert {f.query_locale for f in vernacular} == {"lg", "sw"}


def test_no_item_scores_a_locale_against_bare_english_keywords():
    """The regression that produced the 30.4% Luganda ceiling."""
    for faq in ev.build_1000_faqs_dataset():
        if faq.locale == "en":
            continue
        assert "ura" not in [k.lower() for k in faq.vernacular_keywords]


def test_a_vernacular_slot_prompt_is_a_non_answer_too():
    """G38's lesson, facing the scorer.

    The service's own workflow escape was English-only and stranded every
    Luganda and Kiswahili user. A scorer that only recognises English slot
    prompts makes the same mistake in reverse — it credits the vernacular ones
    as answers, and the locale that is actually being failed scores best.
    """
    faq = _faq(locale="lg", vernacular_keywords=["omusolo"], expected_keywords=[])
    for prompt in (
        "Ssente mmeka z'ofuna buli mwezi?",
        "Je, wewe ni mtu binafsi au kampuni?",
    ):
        assert ev._is_non_answer(prompt, "workflow"), prompt
    scored = ev.score_reply(faq, "Ssente mmeka z'ofuna buli mwezi?", "workflow")
    assert scored["non_answer"]
    assert scored["accuracy"] == 0.0


def test_a_completed_workflow_is_not_treated_as_a_slot_prompt():
    """`retrieval_mode == "workflow"` covers completion as well as elicitation."""
    faq = _faq(expected_keywords=["paye", "salary"])
    completion = (
        "Your PAYE on a gross monthly salary of UGX 1,500,000 is UGX 235,000. "
        "The return is due by the 15th of the following month, and you can file "
        "it on the URA portal at ura.go.ug or call 0800 117 000 for help."
    )
    assert not ev._is_non_answer(completion, "workflow")
    assert ev.score_reply(faq, completion, "workflow")["accuracy"] == 1.0

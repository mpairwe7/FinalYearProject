"""What a translation may not quietly change on its way to the taxpayer.

Answers are generated and claim-verified in English and translated on the way
out (``service.localize_reply``). Everything the verifier concluded is about
the English draft, so each property it relied on has to be re-checked against
the text actually shipped. ``mt.figures_survived`` already compared the digits.
Three things it does not look at:

*Units.* ``protect_figures`` masks digits and leaves the percent sign and the
currency code visible — deliberately, since they are the cue the target
language needs to build "ebitundu 18 ku buli kikumi". So "18%" arriving as a
bare "18" kept every digit and passed.

*Length.* The collapse guard was ``len(candidate) < max(12, len(text) // 10)``
— a floor at one tenth, which passes a translation that dropped nine tenths of
the answer. It caught a collapsed response and not a truncated one, and a
truncated answer reads as a complete answer that happens to omit the taxpayer's
obligations. The replacement floor is measured from the aligned SALT pairs in
``Data/online_corpora/salt/``.

*Citations.* ``claim_verifier`` keys off the ``[n]`` markers to decide whether
a claim was supported at all. A translation that drops or renumbers one ships
an answer whose provenance no longer matches the report that approved it.
"""

from __future__ import annotations

import pytest
from app import mt


def test_a_percentage_that_loses_its_sign_is_rejected():
    assert not mt.units_survived("The VAT rate is 18%.", "Omusolo gwa VAT guli 18.")


def test_a_percentage_rendered_the_luganda_way_is_accepted():
    """"ebitundu 18 ku buli kikumi" is a percentage; `entailment` reads it as one."""
    assert mt.units_survived(
        "The VAT rate is 18%.", "Omusolo gwa VAT guli ebitundu 18 ku buli kikumi."
    )


def test_a_percentage_rendered_the_swahili_way_is_accepted():
    assert mt.units_survived("The VAT rate is 18%.", "Kodi ya VAT ni asilimia 18.")


def test_an_amount_that_loses_its_currency_is_rejected():
    assert not mt.units_survived(
        "The threshold is UGX 300,000,000.", "Ekkomo liri 300,000,000."
    )


def test_an_amount_keeping_a_vernacular_currency_word_is_accepted():
    assert mt.units_survived(
        "The threshold is UGX 300,000,000.", "Ekkomo liri ssente 300,000,000."
    )
    assert mt.units_survived(
        "The threshold is UGX 300,000,000.", "Kiwango ni shilingi 300,000,000."
    )


def test_text_with_no_units_passes_trivially():
    assert mt.units_survived("File your return on time.", "Waayo alipoota mu budde.")


def test_partial_loss_is_tolerated():
    """One rate rendered as a word keeps the marker for the other, and passes.

    `figures()` pools categories precisely to tolerate this. Failing here would
    buy precision on a rare fault by costing a vernacular answer on a common
    one — `localize_reply` answers a failure by serving English.
    """
    assert mt.units_survived(
        "VAT is 18% and withholding is 6%.",
        "Omusolo gwa VAT guli ebitundu 18 ne withholding nga mukaaga.",
    )


@pytest.mark.parametrize("marker", ["UGX", "USh", "Shs", "shillings", "shilingi", "ssente"])
def test_every_currency_marker_is_recognised(marker):
    assert mt.units_survived("Pay UGX 500,000 now.", f"Sasula {marker} 500,000 kati.")


# ---------------------------------------------------------------------------
# Length plausibility
# ---------------------------------------------------------------------------
def test_a_truncated_translation_is_rejected():
    """The old floor was one tenth of the source, which passed this."""
    source = (
        "You must register for VAT once your annual taxable turnover reaches "
        "UGX 300,000,000. File your return by the 15th of the following month "
        "and pay through the URA portal at ura.go.ug."
    )
    assert not mt.length_plausible(source, "Osaanidde okwewandiisa.")


def test_a_full_length_translation_is_accepted():
    source = (
        "You must register for VAT once your annual taxable turnover reaches "
        "UGX 300,000,000. File your return by the 15th of the following month."
    )
    translated = (
        "Olina okwewandiisa ku VAT ng'ensimbi z'ofuna mu mwaka zituuka ku "
        "UGX 300,000,000. Waayo alipoota yo nga tewannaba lunaku lwa 15 mu mwezi ogugoberera."
    )
    assert mt.length_plausible(source, translated)


def test_the_floor_sits_below_every_realistic_human_ratio():
    """0.35 is under the one-in-a-thousand ratio measured for both languages."""
    assert mt.MT_MIN_LENGTH_RATIO < 0.434  # en->sw p0.1
    assert mt.MT_MIN_LENGTH_RATIO < 0.449  # en->lg p0.1


def test_a_short_source_is_not_held_to_a_ratio():
    """A greeting or a one-line abstention can legitimately render as one word."""
    assert mt.length_plausible("Hello.", "Ki kati?")
    assert not mt.length_plausible("Hello.", "")


def test_an_amount_and_a_rate_both_losing_their_markers_are_rejected():
    assert not mt.units_survived(
        "VAT is 18% on a threshold of UGX 300,000,000.",
        "Omusolo guli 18 ku kkomo 300,000,000.",
    )


# ---------------------------------------------------------------------------
# Citation markers
# ---------------------------------------------------------------------------
def test_a_translation_that_drops_a_citation_is_rejected():
    source = "VAT is charged at 18% [1] and the threshold is UGX 300,000,000 [2]."
    assert not mt.citations_survived(source, "Omusolo gwa VAT guli ebitundu 18 [1].")


def test_a_translation_that_renumbers_a_citation_is_rejected():
    source = "VAT is charged at 18% [1]."
    assert not mt.citations_survived(source, "Omusolo gwa VAT guli ebitundu 18 [2].")


def test_reordered_clauses_keep_their_markers_and_pass():
    """Order is not the property; attachment is, and the marker moves with its clause."""
    source = "VAT is 18% [1] and the threshold is UGX 300,000,000 [2]."
    reordered = "Ekkomo liri UGX 300,000,000 [2], ate omusolo gwa VAT guli ebitundu 18 [1]."
    assert mt.citations_survived(source, reordered)


def test_an_uncited_reply_passes_trivially():
    assert mt.citations_survived("Hello, how can I help?", "Ki kati, nkuyambe ntya?")


def test_the_marker_shape_matches_the_one_claim_verifier_reads():
    """The two must agree, or the report describes a text that was not shipped."""
    from app.claim_verifier import _CITATION_RE

    assert _CITATION_RE.pattern == mt._CITATION_MARKER_RE.pattern

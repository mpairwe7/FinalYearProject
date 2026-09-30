"""Unit tests for speech text normalization (tax domain TTS prep)."""

import pytest
from app.speech_normalization import clean_text_for_speech


def test_strip_citations():
    raw = "Under Section 15 of the VAT Act [1], the standard rate is 18% [2, 3]."
    res = clean_text_for_speech(raw, locale="en")
    assert "[1]" not in res
    assert "[2, 3]" not in res
    assert "Section 15 of the V-A-T Act, the standard rate is 18 percent." in res


def test_markdown_stripping():
    raw = "### VAT Registration\n* **Threshold**: Annual turnover of UGX 150,000,000.\n* Check [e-Services](https://ura.go.ug)."
    res = clean_text_for_speech(raw, locale="en")
    assert "###" not in res
    assert "**" not in res
    assert "[e-Services]" not in res
    assert "https://" not in res
    assert "e-Services" in res
    assert "Threshold: Annual turnover of 150 million Uganda shillings" in res


def test_ugx_currency_formatting():
    # English round millions
    res_en = clean_text_for_speech("The penalty is UGX 50,000,000.", locale="en")
    assert "50 million Uganda shillings" in res_en

    # English thousands
    res_en_k = clean_text_for_speech("Fee of UGX 50,000.", locale="en")
    assert "50 thousand Uganda shillings" in res_en_k

    # Luganda millions
    res_lg = clean_text_for_speech("Omusolo gwa UGX 5,000,000.", locale="lg")
    assert "shilingi obukadde 5" in res_lg

    # Swahili millions
    res_sw = clean_text_for_speech("Kodi ya UGX 10,000,000.", locale="sw")
    assert "shilingi milioni 10 za Uganda" in res_sw


def test_percentage_formatting():
    assert clean_text_for_speech("The rate is 18%.", locale="en") == "The rate is 18 percent."
    assert "ebitundu 18 ku buli kikumi" in clean_text_for_speech("Omusolo gwa 18%.", locale="lg")
    assert "asilimia 18" in clean_text_for_speech("Kodi ya 18%.", locale="sw")


def test_tin_digit_expansion():
    raw = "Your TIN is 1001234567 for filing."
    res = clean_text_for_speech(raw, locale="en")
    assert "T I N 1 0 0 1 2 3 4 5 6 7" in res


def test_standalone_tin_and_prn_and_nin():
    raw = "You must obtain a valid TIN and PRN before paying, or show your NIN."
    res = clean_text_for_speech(raw, locale="en")
    assert "T-I-N" in res
    assert "P-R-N" in res
    assert "N-I-N" in res


def test_prn_digit_expansion():
    raw = "Your PRN is 224000123456."
    res = clean_text_for_speech(raw, locale="en")
    assert "P R N 2 2 4 0 0 0 1 2 3 4 5 6" in res


def test_dts_and_additional_acronyms():
    raw = "DTS stamps apply to excisable goods alongside CIT and LED."
    res = clean_text_for_speech(raw, locale="en")
    assert "Digital Tax Stamps" in res
    assert "C-I-T" in res
    assert "Local Excise Duty" in res


def test_toll_free_number_cadence():
    raw = "Call toll-free 0800 117 000 or 0800 217 000."
    res = clean_text_for_speech(raw, locale="en")
    assert "0 800, 117, 0 0 0" in res
    assert "0 800, 217, 0 0 0" in res


def test_step_pacing():
    raw_en = "1. Visit the portal.\n2. Submit Form DT-1001."
    res_en = clean_text_for_speech(raw_en, locale="en")
    assert "Step 1: Visit the portal." in res_en
    assert "Step 2: Submit Form D-T 1001." in res_en

    raw_lg = "1. Genda ku mukutu.\n2. Sasula omusolo."
    res_lg = clean_text_for_speech(raw_lg, locale="lg")
    assert "Odaala 1: Genda ku mukutu." in res_lg


def test_tax_acronym_expansion():
    raw = "Register on EFRIS with URA for PAYE and WHT compliance."
    res = clean_text_for_speech(raw, locale="en")
    assert "E-F-R-I-S" in res
    assert "U-R-A" in res
    assert "P-A-Y-E" in res
    assert "Withholding Tax" in res


def test_legal_section_expansion():
    raw = "Pursuant to Sec. 118A and Sec 122 of the Act."
    res = clean_text_for_speech(raw, locale="en")
    assert "Section 118A" in res
    assert "Section 122" in res


def test_broken_and_bare_urls_are_not_spoken():
    raw = "Genda ku www.ura.go.ug](http://www.ura.go.ug/) oba ku email."
    res = clean_text_for_speech(raw, locale="lg")
    assert "http" not in res
    assert "www." not in res
    assert "ura.go.ug" not in res
    assert "Genda ku" in res


def test_a_url_goes_with_the_at_that_introduced_it():
    res = clean_text_for_speech("Visit the official URA web portal at https://ura.go.ug. Then log in.", locale="en")
    assert res == "Visit the official U-R-A web portal. Then log in."


def test_the_calculator_s_asides_are_spoken_once_and_plainly():
    # The calculator's VAT line, verbatim: spoken as written, Orpheus ran on
    # past the answer ("…to gwa gwa, isi isi zo…") until its 17 s cap.
    raw = (
        "**The standard rate of Value Added Tax (VAT / omusolo gwa VAT / ushuru wa VAT) is 18% (18%)** "
        "(FY2026-27). That comes from the official URA FY2026-27 rate table."
    )
    assert clean_text_for_speech(raw, locale="en") == (
        "The standard rate of Value Added Tax (V-A-T) is 18 percent, for the 2026 to 2027 financial year. "
        "That comes from the official U-R-A 2026 to 2027 rate table."
    )
    assert clean_text_for_speech(raw, locale="lg") == (
        "The standard rate of Value Added Tax (V-A-T) is ebitundu 18 ku buli kikumi. "
        "That comes from the official U-R-A 2026-27 rate table."
    )


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        # a different figure, not a repeat
        ("It rose from 118% (18%) last year.", "It rose from 118 percent (18 percent) last year."),
        # a date, not a list of names
        ("Filed on (12/05/2026) at noon.", "Filed on (12/05/2026) at noon."),
        # alternatives that are not one name
        ("Deductions (NSSF / withholding) apply.", "Deductions (N-S-S-F / withholding) apply."),
    ],
)
def test_other_asides_are_left_alone(raw, spoken):
    assert clean_text_for_speech(raw, locale="en") == spoken


def test_the_contact_footer_is_not_spoken_in_any_language():
    from app.text_signals import CONTACT_FOOTER, LOCALIZED_CONTACT_FOOTERS

    for locale, footer in {"en": CONTACT_FOOTER, **LOCALIZED_CONTACT_FOOTERS}.items():
        res = clean_text_for_speech(f"Register on the portal.\n\n{footer}\n\nYou might also want to know: X", locale)
        assert "0800" not in res and "0772" not in res
        assert res.startswith("Register on the portal.") and res.endswith("You might also want to know: X")


def test_a_number_the_caller_asked_for_is_still_spoken():
    res = clean_text_for_speech("URA's toll-free line is 0800 117 000.", locale="en")
    assert res == "U-R-A's toll-free line is 0 800, 117, 0 0 0."


def test_the_aside_rules_run_in_linear_time():
    # CodeQL py/polynomial-redos: a leading \s* before "(" was retried from
    # every position in a run of spaces (20k spaces took ~1 s).
    import time

    from app.speech_normalization import _FY_ASIDE_RE, _REPEATED_FIGURE_RE

    for text in (" " * 20000 + "(FY", "0" * 20000 + " (0"):
        start = time.perf_counter()
        _FY_ASIDE_RE.sub("", text)
        _REPEATED_FIGURE_RE.sub(r"\1", text)
        assert time.perf_counter() - start < 0.2


def test_a_fiscal_year_rolls_over_the_century():
    assert clean_text_for_speech("FY2099-00", locale="en") == "2099 to 2100"


def test_empty_and_whitespace():
    assert clean_text_for_speech("") == ""
    assert clean_text_for_speech("   ") == ""
    assert clean_text_for_speech(None) == ""

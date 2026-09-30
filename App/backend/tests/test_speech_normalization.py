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

    # Luganda and Swahili amounts in words: Orpheus dropped the "10" of
    # "obukadde 10" and read "milioni 10" as "milioni ten".
    assert "shilingi obukadde butaano" in clean_text_for_speech("Omusolo gwa UGX 5,000,000.", locale="lg")
    assert "shilingi akakadde kamu" in clean_text_for_speech("Omusolo gwa UGX 1,000,000.", locale="lg")
    assert "shilingi milioni kumi za Uganda" in clean_text_for_speech("Kodi ya UGX 10,000,000.", locale="sw")
    # Beyond Luganda's round millions, English words, as money is commonly said
    res_lg = clean_text_for_speech("Omusaala gwa UGX 335,000.", locale="lg")
    assert "shilingi three hundred and thirty-five thousand" in res_lg


def test_percentage_formatting():
    assert clean_text_for_speech("The rate is 18%.", locale="en") == "The rate is 18 percent."
    assert "ebitundu kkumi na munaana ku buli kikumi" in clean_text_for_speech("Omusolo gwa 18%.", locale="lg")
    assert "asilimia kumi na nane" in clean_text_for_speech("Kodi ya 18%.", locale="sw")


def test_a_percent_the_text_already_names_is_not_named_twice():
    # "ku bitundu 18%" was "ku bitundu ebitundu 18 ku buli kikumi"
    assert clean_text_for_speech("Guwoozebwa ku bitundu 18% ku buli kikumi.", locale="lg") == (
        "Guwoozebwa ku bitundu kkumi na munaana ku buli kikumi."
    )
    assert clean_text_for_speech("Ni asilimia 18%.", locale="sw") == "Ni asilimia kumi na nane."


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
    assert "Odaala emu: Genda ku mukutu." in res_lg


def test_tax_acronym_expansion():
    raw = "Register on EFRIS with URA for PAYE and WHT compliance."
    res = clean_text_for_speech(raw, locale="en")
    assert "Efris" in res
    assert "U-R-A" in res
    assert "P-A-Y-E" in res
    assert "Withholding Tax" in res


def test_ura_specific_terms_and_dotted_acronyms():
    raw = "Provide your N.I.N. and T.I.N. for V.A.T. and TCC clearance under ASYCUDA and e-Tax on E-F-R-I-S."
    res = clean_text_for_speech(raw, locale="en")
    assert "N-I-N" in res
    assert "T-I-N" in res
    assert "V-A-T" in res
    assert "T-C-C" in res
    assert "Asycuda" in res
    assert "E-Tax" in res
    assert "Efris" in res


def test_ugandan_nin_digit_expansion():
    raw = "Your National ID NIN is CM89012345ABCD."
    res = clean_text_for_speech(raw, locale="en")
    assert "N-I-N C M 8 9 0 1 2 3 4 5 A B C D" in res


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
        "The standard rate of Value Added Tax (V-A-T) is ebitundu kkumi na munaana ku buli kikumi. "
        "That comes from the official U-R-A twenty twenty-six okutuuka ku twenty twenty-seven rate table."
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


def test_the_luganda_answer_s_figures_are_said_once_and_in_words():
    # The live Luganda VAT answer: the translated anchor "(eza 18%)" was read
    # aloud, "ebitundu" doubled, and Orpheus said "18" as "e tini".
    raw = (
        "Omusolo ogw'omuwendo ogwongerwako (VAT / omusolo gwa VAT / ushuru wa VAT) guli ebitundu 18% (eza 18%) "
        "(FY2026-27). Guwoozebwa ku bitundu 18% ku bintu."
    )
    assert clean_text_for_speech(raw, locale="lg") == (
        "Omusolo ogw'omuwendo ogwongerwako (V-A-T) guli ebitundu kkumi na munaana ku buli kikumi. "
        "Guwoozebwa ku bitundu kkumi na munaana ku buli kikumi ku bintu."
    )


@pytest.mark.parametrize(
    ("locale", "spoken"),
    [
        ("en", "335,000 Uganda shillings to 410,000 Uganda shillings: 20 percent"),
        ("lg", "shilingi three hundred and thirty-five thousand okutuuka ku shilingi four hundred and ten thousand: "
               "ebitundu amakumi abiri ku buli kikumi"),
        ("sw", "shilingi elfu mia tatu thelathini na tano za Uganda hadi shilingi elfu mia nne na kumi za Uganda: "
               "asilimia ishirini"),
    ],
)
def test_a_band_is_read_from_one_amount_to_the_other(locale, spoken):
    # The PAYE table's dash was read as a pause: "…335,000 Uganda shillings – 410,000…"
    assert clean_text_for_speech("UGX 335,000 – UGX 410,000: **20%**", locale=locale) == spoken


def test_an_expanded_name_is_not_said_twice():
    raw = "Heads include Withholding Tax (**WHT**) and Value Added Tax (**VAT**)."
    assert clean_text_for_speech(raw, locale="en") == "Heads include Withholding Tax and Value Added Tax (V-A-T)."


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("Ennaku 30 okuva leero.", "Ennaku amakumi asatu okuva leero."),
        ("Mu mwaka 2026-27.", "Mu mwaka twenty twenty-six okutuuka ku twenty twenty-seven."),
        ("Ekiwandiiko DT-2027.", "Ekiwandiiko D-T 2027."),
        ("Section 15 y'etteeka.", "Section fifteen y'etteeka."),
    ],
)
def test_luganda_numbers_in_running_text(raw, spoken):
    assert clean_text_for_speech(raw, locale="lg") == spoken


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("Siku 30 kuanzia leo.", "Siku thelathini kuanzia leo."),
        ("Mwaka wa fedha 2026-27.", "Mwaka wa fedha elfu mbili ishirini na sita hadi elfu mbili ishirini na saba."),
        ("Kiwango ni 2.5%.", "Kiwango ni asilimia mbili nukta tano."),
    ],
)
def test_swahili_numbers_in_running_text(raw, spoken):
    assert clean_text_for_speech(raw, locale="sw") == spoken


@pytest.mark.parametrize("locale", ["lg", "sw"])
@pytest.mark.parametrize(
    "raw",
    [
        "TIN 1000123456",  # read digit by digit, as before
        "Piga 0772 123 456.",  # a phone number: its leading zero matters
        "Saa 08:30 asubuhi.",  # a time
        "Tarehe 30/06/2026.",  # a date
        "Tarehe 2026-09-30.",  # an ISO date is not a year range
    ],
)
def test_digit_strings_that_are_not_amounts_are_left_alone(locale, raw):
    spoken = clean_text_for_speech(raw, locale=locale)
    digits = [d for d in raw if d.isdigit()]
    assert [d for d in spoken if d.isdigit()] == digits


def test_the_number_rules_run_in_linear_time():
    import time

    from app.speech_normalization import (
        _BARE_NUMBER_RE,
        _ECHO_ASIDE_RE,
        _LG_PCT_RE,
        _RANGE_RE,
        _REPEATED_FIGURE_RE,
        _YEAR_RANGE_RE,
    )

    for text in ("1" * 20000 + " -", "1," * 10000, "(" + "a" * 20000, "bitundu " * 3000 + "1", "2026-" * 5000):
        start = time.perf_counter()
        for rule in (_BARE_NUMBER_RE, _ECHO_ASIDE_RE, _LG_PCT_RE, _RANGE_RE, _REPEATED_FIGURE_RE, _YEAR_RANGE_RE):
            rule.sub("", text)
        assert time.perf_counter() - start < 0.5


def test_a_fiscal_year_rolls_over_the_century():
    assert clean_text_for_speech("FY2099-00", locale="en") == "2099 to 2100"


def test_empty_and_whitespace():
    assert clean_text_for_speech("") == ""
    assert clean_text_for_speech("   ") == ""
    assert clean_text_for_speech(None) == ""

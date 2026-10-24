"""Unit tests for speech text normalization (tax domain TTS prep)."""

import pytest
from app.speech_normalization import clean_text_for_speech


def test_strip_citations():
    raw = "Under Section 15 of the VAT Act [1], the standard rate is 18% [2, 3]."
    res = clean_text_for_speech(raw, locale="en")
    assert "[1]" not in res
    assert "[2, 3]" not in res
    assert "Section 15 of the VAT Act, the standard rate is 18 percent." in res


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
    assert "TIN 1 0 0 1 2 3 4 5 6 7" in res


def test_standalone_tin_and_prn_and_nin():
    raw = "You must obtain a valid TIN (or tin / T-I-N) and PRN before paying, or show your NIN (or nin / N-I-N)."
    res = clean_text_for_speech(raw, locale="en")
    assert "TIN" in res
    assert "T-I-N" not in res
    assert "P R N" in res
    assert "P-R-N" not in res
    assert "NIN" in res
    assert "N-I-N" not in res


def test_government_mda_terms_and_pronunciations():
    raw_upper = (
        "Coordinate with KCCA, URSB, NEMA, UNBS, NIRA, DCIC, UDLS, MLHUD, UgNLIS, "
        "UPF, EPS, NDA, NDAMIS, PPDA, IFMS, OBRS, UESW, IRAS, BWIMS, NTR, ONTR, "
        "MoWT, MoFPED, MoLG, MAAIF, UCDA, PVoC, CoC, CGV, MZO, EIA, FDN, EFD, OTP, "
        "COMESA, ULII, e-GP, and e-Citie."
    )
    res_upper = clean_text_for_speech(raw_upper, locale="en")
    assert "K C C A" in res_upper
    assert "U R S B" in res_upper
    assert "Nema" in res_upper
    assert "U N B S" in res_upper
    assert "Nira" in res_upper
    assert "D C I C" in res_upper
    assert "U D L S" in res_upper
    assert "M L H U D" in res_upper
    assert "U G N L I S" in res_upper
    assert "U P F" in res_upper
    assert "E P S" in res_upper
    assert "N D A" in res_upper
    assert "N D A M I S" in res_upper
    assert "P P D A" in res_upper
    assert "I F M S" in res_upper
    assert "O B R S" in res_upper
    assert "U E S W" in res_upper
    assert "Iras" in res_upper
    assert "Bwims" in res_upper
    assert "N T R" in res_upper
    assert "O N T R" in res_upper
    assert "M O W T" in res_upper
    assert "M O F P E D" in res_upper
    assert "M O L G" in res_upper
    assert "M A A I F" in res_upper
    assert "U C D A" in res_upper
    assert "P V O C" in res_upper
    assert "C O C" in res_upper
    assert "C G V" in res_upper
    assert "M Z O" in res_upper
    assert "E I A" in res_upper
    assert "F D N" in res_upper
    assert "E F D" in res_upper
    assert "O T P" in res_upper
    assert "Comesa" in res_upper
    assert "Ulii" in res_upper
    assert "E G P" in res_upper
    assert "E-City" in res_upper

    raw_lower = "Check kcca, ursb, nema, unbs, nira, dcic, udls, mlhud, upf, nda, ppda, ifms, obrs, uesw, iras, bwims, ntr, and ontr."
    res_lower = clean_text_for_speech(raw_lower, locale="en")
    assert "K C C A" in res_lower
    assert "U R S B" in res_lower
    assert "Nema" in res_lower
    assert "U N B S" in res_lower
    assert "Nira" in res_lower
    assert "D C I C" in res_lower
    assert "U D L S" in res_lower
    assert "M L H U D" in res_lower
    assert "U P F" in res_lower
    assert "N D A" in res_lower
    assert "P P D A" in res_lower
    assert "I F M S" in res_lower
    assert "O B R S" in res_lower
    assert "U E S W" in res_lower
    assert "Iras" in res_lower
    assert "Bwims" in res_lower
    assert "N T R" in res_lower
    assert "O N T R" in res_lower

    raw_domains = "Check kcca.go.ug, obrs.ursb.go.ug, unbs.go.ug, nema.go.ug, nira.go.ug, udls.co.ug, or nda.or.ug."
    res_domains = clean_text_for_speech(raw_domains, locale="en")
    assert "K C C A dot go dot u g" in res_domains
    assert "O B R S dot U R S B dot go dot u g" in res_domains
    assert "U N B S dot go dot u g" in res_domains
    assert "Nema dot go dot u g" in res_domains
    assert "Nira dot go dot u g" in res_domains
    assert "U D L S dot co dot u g" in res_domains
    assert "N D A dot or dot u g" in res_domains


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
    assert "U R A" in res
    assert "Payee" in res
    assert "Withholding Tax" in res


def test_ura_specific_terms_and_dotted_acronyms():
    raw = "Provide your N.I.N. and T.I.N. for V.A.T. and TCC clearance under ASYCUDA and e-Tax on E-F-R-I-S. Email services@ura.go.ug or check ura.go.ug for paye with ura."
    res = clean_text_for_speech(raw, locale="en")
    assert "NIN" in res
    assert "TIN" in res
    assert "VAT" in res
    assert "T-C-C" in res
    assert "Asycuda" in res
    assert "E-Tax" in res
    assert "Efris" in res
    assert "services at U R A dot go dot u g" in res
    assert "U R A dot go dot u g" in res
    assert "payee" in res or "Payee" in res
    assert "U R A" in res


def test_ugandan_nin_digit_expansion():
    raw = "Your National ID NIN is CM89012345ABCD."
    res = clean_text_for_speech(raw, locale="en")
    assert "NIN C M 8 9 0 1 2 3 4 5 A B C D" in res


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
    assert res == "Visit the official U R A web portal. Then log in."


def test_the_calculator_s_asides_are_spoken_once_and_plainly():
    # The calculator's VAT line, verbatim: spoken as written, Orpheus ran on
    # past the answer ("…to gwa gwa, isi isi zo…") until its 17 s cap.
    raw = (
        "**The standard rate of Value Added Tax (VAT / omusolo gwa VAT / ushuru wa VAT) is 18% (18%)** "
        "(FY2026-27). That comes from the official URA FY2026-27 rate table."
    )
    assert clean_text_for_speech(raw, locale="en") == (
        "The standard rate of Value Added Tax (VAT) is 18 percent, for the 2026 to 2027 financial year. "
        "That comes from the official U R A 2026 to 2027 rate table."
    )
    assert clean_text_for_speech(raw, locale="lg") == (
        "The standard rate of Value Added Tax (VAT) is ebitundu kkumi na munaana ku buli kikumi. "
        "That comes from the official U R A twenty twenty-six okutuuka ku twenty twenty-seven rate table."
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
    assert res == "U R A's toll-free line is 0 800, 117, 0 0 0."


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
        "Omusolo ogw'omuwendo ogwongerwako (VAT) guli ebitundu kkumi na munaana ku buli kikumi. "
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
    assert clean_text_for_speech(raw, locale="en") == "Heads include Withholding Tax and Value Added Tax (VAT)."


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


def test_redaction_markers_and_standalone_tin_prn_are_voiced_naturally():
    raw = (
        "For [REDACTED_UG_NID] and [REDACTED_UG_TIN], records for 1000123456 "
        "and PRN 2260001234567 show NIN CR900123456789."
    )
    en_spoken = clean_text_for_speech(raw, locale="en")
    assert "redacted National ID" in en_spoken
    assert "redacted TIN" in en_spoken
    assert "REDACTED_UG_NID" not in en_spoken
    assert "1 0 0 0 1 2 3 4 5 6" in en_spoken
    assert "2 2 6 0 0 0 1 2 3 4 5 6 7" in en_spoken
    assert "NIN C R 9 0 0 1 2 3 4 5 6 7 8 9" in en_spoken

    lg_spoken = clean_text_for_speech("[REDACTED_UG_NID] ne [REDACTED_UG_PHONE]", locale="lg")
    assert "ennamba ya NIN ekwekeddwa" in lg_spoken
    assert "ennamba y'essimu ekwekeddwa" in lg_spoken

    # Repeated-whitespace currency amounts must not be split as standalone TIN/PRN digits
    ugx_spoken = clean_text_for_speech("Turnover of UGX  1000123456.", locale="en")
    assert "1 0 0 0 1 2 3 4 5 6" not in ugx_spoken
    assert "1000123456 Uganda shillings" in ugx_spoken


@pytest.mark.parametrize("locale", ["en", "lg", "sw", "nyn", "ach"])
def test_contact_numbers_ussd_emails_po_box_and_subdomain_urls_across_all_5_locales(locale):
    raw = (
        "Call +256 (0)323442055 or +256772123456 or 0772123456 or 0800 21 21 21. "
        "Dial *165# on MTN or *185# on Airtel Money. "
        "Email help.desk@ura.go.ug or support@nira.go.ug or cloaketech@gmail.com. "
        "Log in to https://efris.ura.go.ug or https://portal.ura.go.ug or "
        "confirm the TIN at https://ura.go.ug/en/domestic-taxes/tin-application/search-tin/. "
        "Send to P.O. Box 7279, Kampala."
    )
    spoken = clean_text_for_speech(raw, locale=locale)
    assert "plus 2 5 6, 0, 3 2 3, 4 4 2, 0 5 5" in spoken
    assert "plus 2 5 6, 7 7 2, 1 2 3, 4 5 6" in spoken
    assert "0 7 7 2, 1 2 3, 4 5 6" in spoken
    assert "0 800, 21, 21, 21" in spoken
    assert "star 1 6 5 hash on M T N or star 1 8 5 hash" in spoken
    assert "help dot desk at U R A dot go dot u g" in spoken
    assert "support at Nira dot go dot u g" in spoken
    assert "cloaketech at gmail dot com" in spoken
    assert "Efris dot U R A dot go dot u g" in spoken
    assert "portal dot U R A dot go dot u g" in spoken
    assert "confirm the TIN at U R A dot go dot u g." in spoken
    assert "P O Box 7 2 7 9" in spoken
    assert "https://" not in spoken

    # Edge cases: hyphenated toll-free, underscore/hyphen/digit email local part,
    # international .int/.org URLs, and vernacular redundant portal prepositions
    edge_raw = (
        "Call 0800-117-000 or 0800-217-000 or (0414) 258 641 or +256 (414) 258 641 or 999 or 112. "
        "Contact tax_help-24@ura.go.ug or customs+clearance@ura.go.ug or customer_care@kcca.go.ug. "
        "Open https://ura.go.ug/en/download/?wpdmdl=20305&refresh=abc#top or https://efris.ura.go.ug:8443/login. "
        "Check https://www.eac.int/customs or comesa.int or wcoomd.org. "
        "Genda ku mukutu gwa URA ku https://ura.go.ug. "
        "Tembelea tovuti ya URA katika https://ura.go.ug."
    )
    edge_spoken = clean_text_for_speech(edge_raw, locale=locale)
    assert "0 800, 117, 0 0 0" in edge_spoken
    assert "0 800, 217, 0 0 0" in edge_spoken
    assert "0 4 1 4, 2 5 8, 6 4 1" in edge_spoken
    assert "plus 2 5 6, 4 1 4, 2 5 8, 6 4 1" in edge_spoken
    assert "9 9 9 or 1 1 2" in edge_spoken
    assert "tax underscore help dash 2 4 at U R A dot go dot u g" in edge_spoken
    assert "customs plus clearance at U R A dot go dot u g" in edge_spoken
    assert "customer underscore care at K C C A dot go dot u g" in edge_spoken
    assert "wpdmdl" not in edge_spoken and "8443" not in edge_spoken
    assert "E A C dot int" in edge_spoken
    assert "Comesa dot int" in edge_spoken
    assert "wcoomd dot org" in edge_spoken
    assert "Genda ku mukutu gwa U R A." in edge_spoken
    assert "Tembelea tovuti ya U R A." in edge_spoken


def test_contact_and_url_rules_run_in_linear_time():
    import time

    from app.speech_normalization import (
        _GENERAL_EMAIL_RE,
        _GENERIC_URL_DOMAIN_RE,
        _GOV_DOMAIN_RE,
        _GOV_EMAIL_RE,
        _PO_BOX_RE,
        _TOLLFREE_PAIRS_RE,
        _UG_BARE_256_PHONE_RE,
        _UG_INTL_PHONE_RE,
        _UG_LOCAL_PHONE_RE,
        _URA_DOMAIN_RE,
        _URA_EMAIL_RE,
        _USSD_CODE_RE,
    )

    rules = (
        _USSD_CODE_RE,
        _URA_EMAIL_RE,
        _GOV_EMAIL_RE,
        _GENERAL_EMAIL_RE,
        _URA_DOMAIN_RE,
        _GOV_DOMAIN_RE,
        _GENERIC_URL_DOMAIN_RE,
        _UG_INTL_PHONE_RE,
        _UG_BARE_256_PHONE_RE,
        _UG_LOCAL_PHONE_RE,
        _TOLLFREE_PAIRS_RE,
        _PO_BOX_RE,
    )
    for text in (
        "*165*" * 4000,
        "a.b_c-1@" * 2000,
        "https://sub.domain." * 1500,
        "+256 772 " * 2500,
        "0800-117-" * 2500,
        "P.O. Box " * 2500,
    ):
        start = time.perf_counter()
        for rule in rules:
            rule.sub("", text)
        assert time.perf_counter() - start < 0.3


def test_verbalize_digits_for_tts_and_inline_step_structuring_across_5_locales():
    """Verify dynamic locale digit verbalization, 2-7 phone formats, inline steps, and breadcrumbs."""
    from app.speech_normalization import verbalize_digits_for_tts

    raw = (
        "To register: 1. Visit https://askura.ura.go.ug or https://singlewindow.go.ug "
        "2. Select eServices > TIN Registration 3. Call +256 41 4334000 or 041 4334000 "
        "or 0800 117 000 or dial *165*4*1# or write to P.O. Box 7279."
    )
    en_voiced = clean_text_for_speech(raw, locale="en", verbalize_digits=True)
    assert "Step 1: Visit Ask U R A dot U R A dot go dot u g or single window dot go dot u g." in en_voiced
    assert "Step 2: Select e-Services, then TIN Registration." in en_voiced
    assert "Step 3: Call plus two five six, four one four, three three four, zero zero zero" in en_voiced
    assert "zero four one four, three three four, zero zero zero" in en_voiced
    assert "zero eight hundred, one one seven, zero zero zero" in en_voiced
    assert "star one six five star four star one hash" in en_voiced
    assert "P O Box seven two seven nine" in en_voiced

    lg_voiced = verbalize_digits_for_tts(clean_text_for_speech(raw, locale="lg"), locale="lg")
    assert "Odaala emu:" in lg_voiced and ". Odaala bbiri:" in lg_voiced and ". Odaala ssatu:" in lg_voiced
    assert ", oluvannyuma TIN Registration" in lg_voiced
    assert "zeero nnya emu nnya, ssatu ssatu nnya, zeero zeero zeero" in lg_voiced
    assert "star emu mukaaga ttaano star nnya star emu hash" in lg_voiced

    sw_voiced = verbalize_digits_for_tts(clean_text_for_speech(raw, locale="sw"), locale="sw")
    assert "Hatua moja:" in sw_voiced and ". Hatua mbili:" in sw_voiced and ". Hatua tatu:" in sw_voiced
    assert ", kisha TIN Registration" in sw_voiced
    assert "sufuri nne moja nne, tatu tatu nne, sufuri sufuri sufuri" in sw_voiced
    assert "star moja sita tano star nne star moja hash" in sw_voiced

    nyn_voiced = verbalize_digits_for_tts(clean_text_for_speech(raw, locale="nyn"), locale="nyn")
    assert "Idaara emwe:" in nyn_voiced and ". Idaara ibiri:" in nyn_voiced and ". Idaara ishatu:" in nyn_voiced
    assert ", bwanyima TIN Registration" in nyn_voiced
    assert "zeero ina emwe ina, ishatu ishatu ina, zeero zeero zeero" in nyn_voiced
    assert "star emwe mukaaga itaano star ina star emwe hash" in nyn_voiced

    ach_voiced = verbalize_digits_for_tts(clean_text_for_speech(raw, locale="ach"), locale="ach")
    assert "Rwom acel:" in ach_voiced and ". Rwom aryo:" in ach_voiced and ". Rwom adek:" in ach_voiced
    assert ", ci TIN Registration" in ach_voiced
    assert "zero angwen acel angwen, adek adek angwen, zero zero zero" in ach_voiced
    assert "star acel abicel abic star angwen star acel hash" in ach_voiced




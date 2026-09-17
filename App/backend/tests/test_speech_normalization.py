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


def test_empty_and_whitespace():
    assert clean_text_for_speech("") == ""
    assert clean_text_for_speech("   ") == ""
    assert clean_text_for_speech(None) == ""

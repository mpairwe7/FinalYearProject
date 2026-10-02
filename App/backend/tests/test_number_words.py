"""Numbers as the voices say them (app.number_words)."""

import pytest
from app.number_words import decimal_words, en_words, en_year, lg_words, sw_words


@pytest.mark.parametrize(
    ("n", "words"),
    [
        (0, "sifuri"), (8, "nane"), (10, "kumi"), (18, "kumi na nane"), (21, "ishirini na moja"),
        (100, "mia moja"), (305, "mia tatu na tano"), (310, "mia tatu na kumi"), (335, "mia tatu thelathini na tano"),
        (2026, "elfu mbili ishirini na sita"), (2500, "elfu mbili na mia tano"),
        (335000, "elfu mia tatu thelathini na tano"), (1500000, "milioni moja na elfu mia tano"),
        (10000000, "milioni kumi"), (150000000, "milioni mia moja na hamsini"), (2000000000, "bilioni mbili"),
    ],
)
def test_swahili(n, words):
    assert sw_words(n) == words


@pytest.mark.parametrize(
    ("n", "agree", "words"),
    [
        (0, "", "zeero"), (3, "", "ssatu"), (10, "", "kkumi"), (18, "", "kkumi na munaana"),
        (25, "", "amakumi abiri mu ttaano"), (30, "", "amakumi asatu"), (60, "", "nkaaga"), (100, "", "kikumi"),
        (2, "bi", "bibiri"), (12, "bi", "kkumi na bibiri"), (18, "bi", "kkumi na munaana"), (5, "bu", "butaano"),
    ],
)
def test_luganda(n, agree, words):
    assert lg_words(n, agree) == words


def test_luganda_leaves_large_numbers_to_english():
    assert lg_words(101) is None


@pytest.mark.parametrize(
    ("n", "words"),
    [
        (0, "zero"), (15, "fifteen"), (1005, "one thousand and five"), (10500, "ten thousand five hundred"),
        (335000, "three hundred and thirty-five thousand"), (150000000, "one hundred and fifty million"),
    ],
)
def test_english(n, words):
    assert en_words(n) == words


@pytest.mark.parametrize(
    ("year", "words"),
    [(2026, "twenty twenty-six"), (2005, "two thousand and five"), (2000, "two thousand"),
     (1999, "nineteen ninety-nine"), (1905, "nineteen oh five")],
)
def test_years(year, words):
    assert en_year(year) == words


def test_decimals():
    assert decimal_words("2.5", "sw") == "mbili nukta tano"
    assert decimal_words("0.25", "en") == "zero point two five"

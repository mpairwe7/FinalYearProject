"""A real Ugandan NIN is redacted wherever ``redact_pii_text`` runs (G247).

The NIN pattern was ``C[MF]\\d{2}[A-Z]{5}\\d{5}[A-Z]``: a fixed 15-character
layout. A NIN is 14 characters (NIRA; the corpus tells taxpayers to "enter your
14-character NIN"), so the pattern never matched one. Tickets, transcripts,
memory and logs that rely on it kept NINs in clear. Its tests passed because
they used invented 15-character values shaped like the regex
(``CM90ABCDE12345F``), while ``vision.ocr.extract_nin_numbers`` already read
NINs as 14 characters.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "App" / "backend"))

from app.guardrails import contains_pii, redact_pii_text  # noqa: E402
from app.vision.ocr import extract_nin_numbers  # noqa: E402

REAL_SHAPES = [
    "CM930039182LIR",  # C + M + 12 letters/digits: the 14-character card form
    "CF920019284HJA",
    "CM900123456789",
    "CMABCDEFGHIJ1K",  # 14-character C[MFR] with 1 digit (OCR boundary case)
    "cm950019284kla",  # typed in lower case
    "CM93003918LIR",  # one character dropped
    "CM93-003918-2LIR",  # hyphen-grouped NIN
    "CM93 0039 182LIR",  # space-grouped NIN
    "CM 93 0039182 LIR",  # 4-part space-grouped NIN
    "CM 89 ABCDE 12345 F",  # legacy spaced NIN variant
]


@pytest.mark.parametrize("nin", REAL_SHAPES)
def test_a_nin_in_any_sentence_is_redacted(nin: str) -> None:
    for text in (f"My NIN is {nin}", f"NIN: {nin}.", f"({nin}) was rejected by NIRA"):
        redacted = redact_pii_text(text)
        assert nin not in redacted, text
        assert "[REDACTED_UG_NID]" in redacted
        assert contains_pii(text)


def test_zero_width_and_bidi_smuggled_nin_is_redacted() -> None:
    smuggled = "My NIN is CM93\u200b0039\u202e182LIR and TIN is 100\u200b012\u200c3456"
    redacted = redact_pii_text(smuggled)
    assert "[REDACTED_UG_NID]" in redacted
    assert "[REDACTED_UG_TIN]" in redacted
    assert "CM93" not in redacted
    assert contains_pii(smuggled)


@pytest.mark.parametrize(
    "text",
    [
        "How are cryptocurrency gains taxed?",
        "Cryptocurrency exchanges must register",
        "Log in to the CMS portal",
        "My PRN is 2260001234567",
        "Quote reference CM2026",
        "Quote reference CM2026 in your letter",
        "EFRIS invoice FD100123456789 and reference IN202600012345",
    ],
)
def test_text_that_is_not_a_nin_is_left_alone(text: str) -> None:
    assert "[REDACTED_UG_NID]" not in redact_pii_text(text)
    assert extract_nin_numbers(text) == []


def test_data_aug_redactor_and_gitleaks_match_real_nins() -> None:
    import re
    from ml.scripts.data_aug.text_utils import redact_pii

    out, report = redact_pii("Taxpayer NIN CM930039182LIR and cm950019284kla")
    assert report.nin == 2
    assert "CM930039182LIR" not in out
    assert "cm950019284kla" not in out

    gitleaks_raw = (REPO_ROOT / ".gitleaks.toml").read_text()
    m = re.search(r'id\s*=\s*"uganda-nin"[\s\S]*?regex\s*=\s*\'\'\'(.*?)\'\'\'', gitleaks_raw)
    assert m is not None
    pat = re.compile(m.group(1))
    assert pat.search('nin = "CM930039182LIR"')
    assert pat.search("national_id: 'CF920019284HJA'")


def test_every_nin_ocr_reads_from_an_id_card_is_redacted() -> None:
    card = "REPUBLIC OF UGANDA\nName: Robert Okello\nNIN: CM930039182LIR\nDOB: 10/10/1993"
    found = extract_nin_numbers(card)
    assert found == ["CM930039182LIR"]
    redacted = redact_pii_text(card)
    for nin in found:
        assert nin not in redacted


def test_a_ticket_never_stores_the_nin(tmp_db) -> None:
    ticket = tmp_db.create_ticket(
        reason="NIRA name mismatch for CM930039182LIR",
        user_query="My Instant TIN failed: NIN CM930039182LIR does not match",
    )
    stored = tmp_db.get_ticket(ticket["id"])
    assert "CM930039182LIR" not in str(stored)
    raw = tmp_db._get_connection().execute(
        "SELECT reason, user_query FROM tickets WHERE id = ?",
        (ticket["id"],),
    ).fetchone()
    assert raw is not None
    assert raw["reason"] == "NIRA name mismatch for [REDACTED_UG_NID]"
    assert raw["user_query"] == "My Instant TIN failed: NIN [REDACTED_UG_NID] does not match"

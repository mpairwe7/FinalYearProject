"""The false-premise guard must keep catching taxes Uganda does not levy (G244).

G224 brought the guard to 0 false positives on the 105 complex scenarios by
adding question nouns ("refund", "exemption", "waiver", "due", "appeal",
"audit", "arrears", "liability", ...) to ``_NON_TAX_NOUN_FOLLOWERS``. A follower
removes the candidate from the check entirely, so "How do I claim a Wealth Tax
refund?" was no longer challenged: recall on the fabricated set below fell from
19/19 to 5/19, and the scenarios did not need the change (they stay at 0 false
positives without it). The only test of the change asserted the absence of
false positives, so nothing saw recall go.

Both directions are pinned here. The questions are written the way taxpayers
ask, not in the guard's own vocabulary, and the real-question gate runs over
every FAQ in the tracked corpus with that FAQ's own answer as the retrieved
passage — the shape of a production turn, where the guard sees the hits.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "App" / "backend"))

from app.premise_guard import check_false_premise  # noqa: E402

#: Taxes Uganda does not levy, asked the way a taxpayer would.
FABRICATED = [
    "What is the Wealth Tax in Uganda?",
    "How is Inheritance Tax calculated in Uganda?",
    "What is the rate of Robot Tax?",
    "How do I claim a Wealth Tax refund?",
    "When is the Robot Tax due?",
    "Is there a Wealth Tax exemption for pensioners?",
    "How do I apply for an Inheritance Tax waiver?",
    "What happens in a Bachelor Tax audit?",
    "How much Inheritance Tax is payable on a house worth UGX 300,000,000?",
    "I have Wealth Tax arrears, what do I do?",
    "What is my Metaverse Tax liability?",
    "How do I file an Inheritance Tax objection?",
    "How do I resolve a Robot Tax dispute?",
    "What are my Wealth Tax obligations as a landlord?",
    "Do I need a Robot Tax certificate to import machines?",
    "How do I lodge a Wealth Tax appeal at the TAT?",
    "Is the Inheritance Tax refund taxable?",
    "Pay my omitted Inheritance Tax now",
    "Is a 2026 Wealth Tax payable by expatriates?",
]

#: Real Ugandan taxes in the same phrasings, and "<qualifier> tax <noun>"
#: compounds the follower list exists to protect.
REAL = [
    "How do I claim a VAT refund?",
    "When is PAYE due?",
    "Is there a Withholding Tax exemption for NGOs?",
    "How do I apply for an Income Tax waiver of penalties?",
    "How do I get a tax clearance certificate?",
    "Can I appeal my Income Tax assessment?",
    "What happens in a VAT audit?",
    "How much Stamp Duty is payable on a house worth UGX 300,000,000?",
    "I have PAYE arrears, what do I do?",
    "What is my Rental Tax liability?",
    "How do I file an Income Tax objection?",
    "How do I resolve an Excise Duty dispute?",
    "What are my Withholding Tax obligations?",
    "What is the Digital Services Tax rate?",
    "How is Presumptive Tax calculated?",
    "What is the Capital Gains Tax on land?",
    "Is Customs Duty payable on donated goods?",
    "What is the Infrastructure Levy on imports?",
    "How do I issue an electronic tax invoice?",
    "Who is my district tax officer?",
    "Can I claim a foreign tax credit?",
    "What is the standard tax rate?",
    "How do I file a provisional tax return?",
    "What is the final tax assessment?",
    "What is the new tax law for 2026?",
    "What are the corporate tax rates?",
    "Where is the nearest tax office?",
    "How do I amend my income tax return?",
]


def _faq_rows() -> list[tuple[str, str]]:
    rows = []
    for path in sorted((REPO_ROOT / "Data" / "dataset").glob("ura_*_faqs.csv")):
        with path.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                question = str(row.get("question") or "").strip()
                if question:
                    rows.append((question, str(row.get("answer") or "")))
    return rows


@pytest.mark.parametrize("question", FABRICATED)
def test_a_tax_uganda_does_not_levy_is_challenged(question: str) -> None:
    assert check_false_premise(question, hits=[]).is_false_premise, question


@pytest.mark.parametrize("question", REAL)
def test_a_real_tax_is_not_challenged(question: str) -> None:
    result = check_false_premise(question, hits=[])
    assert not result.is_false_premise, f"{question!r} challenged as {result.concept!r}"


def test_no_faq_question_is_challenged_when_its_answer_is_retrieved() -> None:
    rows = _faq_rows()
    assert len(rows) > 500, "FAQ corpus not found"
    challenged = [
        (question, check_false_premise(question, [{"question": question, "answer": answer}]).concept)
        for question, answer in rows
        if check_false_premise(question, [{"question": question, "answer": answer}]).is_false_premise
    ]
    assert not challenged, challenged[:10]


def test_a_hyphenated_name_matches_its_own_passage() -> None:
    # The modifier is split on hyphens; the passage must be normalised the same
    # way, or a real duty is "not found" in the very passage that names it.
    hit = {"answer": "The EAC applies an anti-dumping duty on certain steel imports."}
    assert not check_false_premise("What is the anti-dumping duty on steel?", [hit]).is_false_premise


def test_the_reply_names_the_tax_in_the_singular() -> None:
    result = check_false_premise("How are Robot Taxes collected?", hits=[])
    assert result.is_false_premise
    assert result.concept == "Robot Tax"

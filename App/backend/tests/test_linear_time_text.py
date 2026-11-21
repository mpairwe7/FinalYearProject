"""Chat text cannot make a regex over it run in more than linear time (CodeQL py/polynomial-redos).

Each scan that replaced a regex is checked against that regex, kept here as the oracle, and
each is timed on the input CodeQL names as slow. At 100,000 characters the old patterns took
seconds; these take milliseconds.
"""

from __future__ import annotations

import random
import re
import time

import pytest
from app.tax import calculator_router
from app import mt
from app.guardrails import output_checks, pii
from app.memory.case_facts import extract_all_money
from app.linear_text import strip_blocks, strip_spans
from app.rag import entailment, pipeline
from app.rag.context_manager import extract_conversation_entities

N = 100_000
BUDGET_S = 0.5


def _elapsed(fn, *args) -> float:
    start = time.perf_counter()
    fn(*args)
    return time.perf_counter() - start


@pytest.mark.parametrize(
    ("pattern", "flags", "opener", "closer", "repl", "min_inner", "ignore_case"),
    [
        (r"<[^>]+>", 0, "<", ">", "", 1, False),
        (r"<[^>]+>", 0, "<", ">", " ", 1, False),
        (r"\[User-attached document:[^\]]+\]", 0, "[User-attached document:", "]", "", 1, False),
        (r"\[REDACTED[^\]]*\]", re.IGNORECASE, "[REDACTED", "]", " ", 0, True),
    ],
)
@pytest.mark.parametrize(
    "text",
    [
        "a<b>c",
        "<>x>",
        "<scr<b>ipt>",
        "x < y <b>z",
        "<<<",
        "a<\n>b",
        "",
        "[User-attached document:]x] [User-attached document: a.pdf] tail",
        "[redacted_email] x [ReDaCtEd] [REDACTED",
        "[REDACTED]]",
        "[[REDACTED_PHONE]",
    ],
)
def test_strip_spans_matches_the_regex(
    pattern, flags, opener, closer, repl, min_inner, ignore_case, text
):
    expected = re.sub(pattern, repl, text, flags=flags)
    assert (
        strip_spans(text, opener, closer, repl, min_inner=min_inner, ignore_case=ignore_case)
        == expected
    )


@pytest.mark.parametrize(
    "text",
    [
        "<think>a</think>b",
        "<thinking x>a\n",
        "<THOUGHT>a</ thought >b",
        "x<reasoning>never closed",
        "<script>x</script >y<script>",
        "<script",
        "a<think>b\n",
        "<scratchpad",
        "<think>>a</think>",
        "<SCRIPT src=x>alert(1)</SCRIPT>after",
    ],
)
def test_strip_blocks_matches_the_regex(text):
    flags = re.DOTALL | re.IGNORECASE
    reasoning = re.sub(
        r"<(?:think|thought|reasoning|scratchpad)[^>]*>.*?(?:</(?:think|thought|reasoning|scratchpad)\s*>|$)",
        "",
        text,
        flags=flags,
    )
    # The script oracle is built from the module's own halves: as one literal, CodeQL reads it
    # as a tag filter (py/bad-tag-filter) rather than as the regex being replaced.
    script_pattern = (
        output_checks._SCRIPT_OPEN_RE.pattern + "[^>]*>.*?" + output_checks._SCRIPT_CLOSE_RE.pattern
    )
    script = re.sub(script_pattern, "", text, flags=flags)
    assert (
        strip_blocks(
            text, output_checks._REASONING_OPEN_RE, output_checks._REASONING_CLOSE_RE, or_to_end=True
        )
        == reasoning
    )
    assert strip_blocks(text, output_checks._SCRIPT_OPEN_RE, output_checks._SCRIPT_CLOSE_RE) == script


@pytest.mark.parametrize(
    ("scan", "pattern", "repl", "flags", "texts"),
    [
        (
            output_checks._join_digit_bracket_glitches,
            r"(\d+)\s*\[+(?!\d+\])[^0-9\n\]]+\s*(\d+)",
            r"\1\2",
            0,
            [
                "12 [ab 3",
                "12 [3]",
                "5 [[12]",
                "5 [x٣",
                "5 [x\n7",
                "5 [] 6",
                "1[[2",
                "7 [a]b 8 [c 9",
            ],
        ),
        (
            output_checks._strip_language_tags,
            r"\[+(?:Luganda|Swahili|English|Runyankole|Acholi)[^\]\n]*\]*",
            "",
            re.IGNORECASE,
            ["[[English] x]] y", "a[Luganda\nb", "[swahili]", "[[[acholi", "[Engl"],
        ),
        (
            output_checks._space_after_headings,
            r"(#{1,4}\s+[^\n]+)\n([^\n#])",
            r"\1\n\n\2",
            0,
            [
                "## Title\nBody",
                "# a # b\n#c\nd",
                "##### deep\nx",
                "# \t\n x\n#",
                "#\n\nx",
                "# a\n\n#b",
                "# a\nb # c\nd",
                "#  \n\n \n z",
                "x # \n",
            ],
        ),
        (
            lambda s: output_checks._split_inline_steps(s, "  "),
            r"(?i)(?<!\bsection)(?<!\bsec\.)(?<!\barticle)(?<!\bart\.)(?<!\bcap\.)(?<!\bschedule)(?<!\bform)(?<!\bpart)(?<!\brule)(?<!\btable)(?<!\bband)(?<!\bmethod)(?<!\btier)(?<!\bpage)(?<!\bitem)(?<!\bugx)(?<!\busd)(?<!\bshs)(?<!\bto)(?<!\band)(?<!\bor)[ \t]+([2-9]|1[0-2])[\.\)][ \t]+(?!Jan(?:uary)?\b|Feb(?:ruary)?\b|Mar(?:ch)?\b|Apr(?:il)?\b|May\b|Jun(?:e)?\b|Jul(?:y)?\b|Aug(?:ust)?\b|Sep(?:t(?:ember)?)?\b|Oct(?:ober)?\b|Nov(?:ember)?\b|Dec(?:ember)?\b)(\*{0,2}[A-Z])",
            r"\n\n  \1. \2",
            0,
            [
                "1. Register 2. File 3) Pay",
                "1. See Section 2. The Act",
                "1. See Section  2. The Act",
                "1. into 2. x or 3. y",
                "1. Due 12. May then 11. **Pay",
                "1. a\t\t13. B 10. c",
                "1. per sec. 4. Then",
            ],
        ),
        (
            entailment._join_decimal_gaps,
            r"(\d+)\.\s+(\d+)",
            r"\1.\2",
            0,
            ["1. 2. 3. 4", "5.  6", "a. 1", "1.\n2"],
        ),
        (
            pipeline._strip_page_footers,
            r"(?:DOMESTIC TAX LAWS OF UGANDA\s+)?\d+\s*\|\s*P\s*a\s*g\s*e",
            "",
            0,
            ["DOMESTIC TAX LAWS OF UGANDA 12 | P a g e x", "a 3|Page b 4 | Pag", "12 | x | Page"],
        ),
    ],
)
def test_scans_match_the_regex(scan, pattern, repl, flags, texts):
    for text in texts:
        assert scan(text) == re.sub(pattern, repl, text, flags=flags), text


@pytest.mark.parametrize(
    "text",
    [
        "see `code` and [the form](https://ura.go.ug/x) or www.ura.go.ug now",
        "[[a](b) [x]y](z) [](q) [a]() ``",
        "`a` http:// https://x y www. [a](b",
        "[ `c` ](d) [unclosed `e`",
        "[a](b)`c`www.d",
    ],
)
def test_protected_spans_match_the_regex(text):
    spans: list[str] = []

    def keep(m: re.Match[str]) -> str:
        spans.append(m.group(0))
        return f"\x00PROT{len(spans) - 1}\x00"

    masked = re.sub(r"`[^`]+`|\[[^\]]+\]\([^)]+\)|(?:https?://|www\.)\S+", keep, text)
    assert output_checks._mask_protected_spans(text) == (masked, spans)


@pytest.mark.parametrize("text", ["**Tax", "*`'\"([A", "** a", "", "A", "***", "(x"])
def test_sentence_start_matches_the_regex(text):
    expected = re.match(r"^(?:\*{1,2}|`|['\"(\[])*[A-Z]", text)
    assert bool(output_checks._STARTS_SENTENCE_RE.match(text)) == bool(expected)


@pytest.mark.parametrize(
    "text", ["ug shs 1,500,000 and 300 million", "12345x 7", "1 000 000 m", "UGX 5 and shs 6"]
)
def test_amount_figures_match_the_regex(text):
    assert entailment._amount_figures(text) == [
        m.groups() for m in entailment._AMOUNT_RE.finditer(text)
    ]


@pytest.mark.parametrize("text", ["abc 1,000x 5", "x12 7", "pay 2.5 now", "none"])
def test_first_number_prefix_matches_the_regex(text):
    expected = re.sub(r"(?<![a-zA-Z0-9_])(\d+(?:,\d{3})*(?:\.\d+)?)\b", r"UGX \1", text, count=1)
    assert mt._prefix_first_number(text, "UGX ") == expected


_EMAIL_ORACLE = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"


def _email_oracle(text: str) -> str:
    return re.sub(
        _EMAIL_ORACLE,
        lambda m: m.group(0) if pii.is_official_ura_email(m.group(0)) else "[REDACTED_EMAIL]",
        text,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Write to jane.doe@gmail.com or services@ura.go.ug.",
        "x@a.bc.d@e.fg",
        "a.b.c@d.e.fg.h a@b..co a@.co @b.co a@@b.co a@b@c.co",
        "_a@b.co é@b.co a@b.coé a@b.co_x a@b.co1 a@b.c1 1@2.ab",
        "[mail](mailto:john+tax@yahoo.co.uk) <UPPER@EXAMPLE.COM> (a_b%c-d@mail.example.org)",
        "x@ura.go.ug.evil.com x@mail.ura.go.ug -a@b.co .a@b.co a.@b.co",
    ],
)
def test_email_redaction_matches_the_regex(text):
    assert pii.redact_pii_text(text, kinds=frozenset({"email"})) == _email_oracle(text)


def test_email_redaction_matches_the_regex_on_fuzz():
    rng = random.Random(715)
    alphabet = ["@", ".", "_", "%", "+", "-", " ", "a", "Z", "co", "1", "é", "(", "ura.go.ug", "\n"]
    for _ in range(3_000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 30)))
        assert pii.redact_pii_text(text, kinds=frozenset({"email"})) == _email_oracle(text), text


def test_address_stops_before_a_table_pipe():
    assert pii.redact_pii_text("| jane@x.com|Kampala |") == "| [REDACTED_EMAIL]|Kampala |"


@pytest.mark.parametrize(
    "text",
    [
        "TIN: 1000123456, TIN  # 1000123457, TIN   1000123458 and tin 1000123452",
        "Tax Identification Number - 1000123459, tax identification number:1000123450",
        "TIN : 100012345 TIN:\t\n1000123451x TIN# 1234567890 TIN:: 1000123456 TIN -1000123456",
        "TIN      x 100-012-3456 1000123456",
    ],
)
def test_tin_pattern_matches_the_regex(text):
    old = (
        r"(?i)(?:\bTIN\s*[:#-]?\s*|\bTax Identification Number\s*[:#-]?\s*)(1\d{9})\b"
        r"|\b1\d{2}[\s-]\d{3}[-\s]\d{4}\b|\b1\d{9}\b"
    )
    assert pii._UG_TIN_RE.sub(r"<\1>", text) == re.sub(old, r"<\1>", text)


@pytest.mark.parametrize(
    ("fn", "slow"),
    [
        (output_checks.neutralize_untrusted_links, "![](" + "\t" * N + "x"),
        (lambda s: strip_spans(s, "<", ">", min_inner=1), "<" * N),
        (
            lambda s: strip_blocks(
                s, output_checks._REASONING_OPEN_RE, output_checks._REASONING_CLOSE_RE, or_to_end=True
            ),
            "<think" * (N // 6),
        ),
        (
            lambda s: strip_blocks(s, output_checks._SCRIPT_OPEN_RE, output_checks._SCRIPT_CLOSE_RE),
            "<script" * (N // 7),
        ),
        (output_checks._join_digit_bracket_glitches, "0" * N + "x"),
        (output_checks._strip_language_tags, "[" * N),
        (output_checks._mask_protected_spans, "[" * N),
        (output_checks._mask_protected_spans, "[a](" * (N // 4)),
        (lambda s: output_checks._split_inline_steps(s, ""), "1. a" + " " * N + "x"),
        (output_checks._space_after_headings, "# " * (N // 2)),
        (output_checks._split_prose_sentences, "No. " * (N // 4)),
        (output_checks._STARTS_SENTENCE_RE.match, "*" * 60 + "a"),
        # Whole function at half size: the digit search and the span restore are inline.
        (output_checks.OutputGuard.normalize_structure, "a " * 40 + "1" * (N // 2)),
        (output_checks.OutputGuard.normalize_structure, "`a` " * (N // 8)),
        (entailment._join_decimal_gaps, "0" * N + "x"),
        (entailment._amount_figures, "0" * N + "x"),
        (lambda s: mt._prefix_first_number(s, "UGX "), "a " + "0" * N + "x"),
        (pipeline._strip_page_footers, "0" * N + "x"),
        (extract_all_money, "1 usd = " + "\t" * N + "x"),
        (lambda s: mt._prefix_first_number(s, "UGX "), "\u0661" * N + "x"),
        (calculator_router._asks_about_rates, "PAYE " + "how is " * (N // 7)),
        (calculator_router._asks_about_rates, "PAYE " + "is " * (N // 3)),
        (
            lambda s: extract_conversation_entities([{"user_message": s}]),
            "TIN" + " is" * (N // 3) + "!",
        ),
        (pii.redact_pii_text, "+256 772 140 00 " * (N // 16)),
        (pii.redact_pii_text, "0772-123-45 " * (N // 12)),
        (pii.redact_pii_text, "(0772\u00a0123\u00a045" * (N // 13)),
        (pii.redact_pii_text, "+256" * (N // 4)),
        (pii.redact_pii_text, "0772-123-" * (N // 9)),
        (pii.redact_pii_text, "a." * (N // 2)),
        (pii.contains_pii, "x@" + "a." * (N // 2)),
        (pii.redact_pii_text, "TIN" + " " * (N // 5) + "x"),
        (pii.contains_pii, "Tax Identification Number" + " " * (N // 5) + "x"),
        (pii._STREAM_HOLD_TAIL_RE.search, " 0772 1" * (N // 7) + "x"),
        (pii._STREAM_CONTIGUOUS_TAIL_RE.search, "+2\u00a0" * (N // 3) + "x"),
    ],
)
def test_slow_input_stays_fast(fn, slow):
    assert _elapsed(fn, slow) < BUDGET_S

"""Chat text cannot make a regex over it run in more than linear time (CodeQL py/polynomial-redos).

Each scan that replaced a regex is checked against that regex, kept here as the oracle, and
each is timed on the input CodeQL names as slow. At 100,000 characters the old patterns took
seconds; these take milliseconds.
"""

from __future__ import annotations

import re
import time

import pytest
from app import guardrails, mt
from app.case_facts import extract_all_money
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
    script = re.sub(r"<script[^>]*>.*?</script\s*>", "", text, flags=flags)
    assert (
        strip_blocks(
            text, guardrails._REASONING_OPEN_RE, guardrails._REASONING_CLOSE_RE, or_to_end=True
        )
        == reasoning
    )
    assert strip_blocks(text, guardrails._SCRIPT_OPEN_RE, guardrails._SCRIPT_CLOSE_RE) == script


@pytest.mark.parametrize(
    ("scan", "pattern", "repl", "flags", "texts"),
    [
        (
            guardrails._join_digit_bracket_glitches,
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
            guardrails._strip_language_tags,
            r"\[+(?:Luganda|Swahili|English|Runyankole|Acholi)[^\]\n]*\]*",
            "",
            re.IGNORECASE,
            ["[[English] x]] y", "a[Luganda\nb", "[swahili]", "[[[acholi", "[Engl"],
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
    "text", ["ug shs 1,500,000 and 300 million", "12345x 7", "1 000 000 m", "UGX 5 and shs 6"]
)
def test_amount_matches_match_the_regex(text):
    assert [m.span() for m in entailment._amount_matches(text)] == [
        m.span() for m in entailment._AMOUNT_RE.finditer(text)
    ]


@pytest.mark.parametrize("text", ["abc 1,000x 5", "x12 7", "pay 2.5 now", "none"])
def test_first_number_prefix_matches_the_regex(text):
    expected = re.sub(r"(?<![a-zA-Z0-9_])(\d+(?:,\d{3})*(?:\.\d+)?)\b", r"UGX \1", text, count=1)
    assert mt._prefix_first_number(text, "UGX ") == expected


@pytest.mark.parametrize(
    ("fn", "slow"),
    [
        (guardrails.neutralize_untrusted_links, "![](" + "\t" * N + "x"),
        (lambda s: strip_spans(s, "<", ">", min_inner=1), "<" * N),
        (
            lambda s: strip_blocks(
                s, guardrails._REASONING_OPEN_RE, guardrails._REASONING_CLOSE_RE, or_to_end=True
            ),
            "<think" * (N // 6),
        ),
        (
            lambda s: strip_blocks(s, guardrails._SCRIPT_OPEN_RE, guardrails._SCRIPT_CLOSE_RE),
            "<script" * (N // 7),
        ),
        (guardrails._join_digit_bracket_glitches, "0" * N + "x"),
        (guardrails._strip_language_tags, "[" * N),
        (entailment._join_decimal_gaps, "0" * N + "x"),
        (entailment._amount_matches, "0" * N + "x"),
        (lambda s: mt._prefix_first_number(s, "UGX "), "a " + "0" * N + "x"),
        (pipeline._strip_page_footers, "0" * N + "x"),
        (extract_all_money, "1 usd = " + "\t" * N + "x"),
        (
            lambda s: extract_conversation_entities([{"user_message": s}]),
            "TIN" + " is" * (N // 3) + "!",
        ),
    ],
)
def test_slow_input_stays_fast(fn, slow):
    assert _elapsed(fn, slow) < BUDGET_S

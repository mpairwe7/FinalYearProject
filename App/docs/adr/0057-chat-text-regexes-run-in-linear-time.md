# ADR-0057: Regexes over chat text run in linear time

## Status

Accepted, 2026-11-15.

## Context and Problem Statement

CodeQL reported 23 `py/polynomial-redos` alerts and 7 `py/log-injection` alerts on paths that
start at `POST /v1/chat` (G663). All 30 were open on `dev`. They blocked PR #738, which moved the
chat route and so changed the code at the start of every flow.

Three regex shapes caused the 23:

1. **Two unbounded runs that can match the same characters.** Examples are `\s*(?:ugx)?\s*` and
   `[ \t]*<?(T)>?(?:[ \t]+title)?[ \t]*\)` when the link destination is empty. When the match
   fails, the engine tries every way of splitting the run between them.
2. **A run, then a literal that may never come.** Examples are `<[^>]+>`, `\[User-attached
   document:[^\]]+\]` and `<script[^>]*>.*?</script\s*>`. An unanchored search restarts at
   every opener and scans to the end of the text each time.
3. **A leading `\d+` or `\[+` run, then something that can fail.** The search restarts inside the
   run and rescans it.

The reference regex in `rag/context_manager.py` was worse than polynomial. In
`(?:[-\s#:]|(?:is|no\.?|number)\s*)+`, each space after a keyword could match two ways, so
"TIN" followed by a few hundred " is" did not finish.

A lookbehind does not fix shape 3 for CodeQL. On PR #745, `(?<!\d)(\d+)\.\s+(\d+)` closed the
old alert and opened a new one at the same line. CodeQL's model ignores lookarounds, and so did
the existing `(?<![a-zA-Z0-9_])` in `mt.py`.

## Decision Outcome

- **Shape 1: write the pattern so each character has one way to match.** An optional token
  between two runs takes one run with it: `\s*(?:(?:ugx|shs\.?)\s*)?`. The link destination
  becomes `_LINK_DESTINATION` in `guardrails.py`. The leading whitespace belongs to the branch
  with a destination. Without a destination, the tail's whitespace takes it. The target is read
  through `_link_target`.
- **Shape 2: scan once instead of searching.** `app/linear_text.py` gives `strip_spans`
  (opener, inner text without the closer, closer) and `strip_blocks` (opener tag, then the first
  closing tag, or the end of the text). Each returns what the regex returned.
- **Shape 3: start only where a match can start.** Digit runs come from `\d+`, which always
  matches, and the rest is checked by hand. `_join_decimal_gaps`, `_join_digit_bracket_glitches`
  and `_strip_page_footers` do this. Where the old regex is still used, it runs `\A`-anchored on a
  slice from each start to one character past the furthest a match can reach:
  `entailment._amount_figures` and `mt._prefix_first_number`. `pattern.match(text, pos)` is not
  enough, because CodeQL models it as a search from any position.
- **A branch that only answers yes or no can leave the regex.** Five branches of `_RATE_ASK_RE`
  were "start word, then text without a `?` (or a newline), then a keyword", and rescanned the rest
  of the text from every repeated start word. CodeQL flagged only one of them, but the code review
  measured 8.7 s on 28,677 characters of repeated "how is". `calculator_router._asks_about_rates`
  now checks them from precomputed start, keyword, `?` and newline positions, and keeps the other
  branches as a regex with no scan.
- **Logs:** user values reach a log line through `config.flags._log_safe`, as elsewhere.

Each replacement returns the same output as the regex it replaces. That was checked on
128,999 strings for each fix: the URA FAQ corpus, the eval sets, every string literal in both
test trees, and fuzz.

## Consequences

- A new regex over chat text needs one of these shapes, or CodeQL fails the PR. A lookbehind,
  a lookahead or a `pos` argument does not satisfy CodeQL; `\A` on a slice does.
- `app/linear_text.py` holds scans that replace a regex exactly. It is not a place for general
  text helpers.
- The scans are longer than the regexes they replace. Each docstring names its regex, and
  `App/backend/tests/test_linear_time_text.py` keeps that regex as the test oracle.

## Confirmation

- `App/backend/tests/test_linear_time_text.py` compares each scan with the regex it replaces, and
  times each changed function on CodeQL's slow input at 100,000 characters.
- CodeQL on this PR reports none of the 30 alerts, and #738's CodeQL check passes after this
  merges.

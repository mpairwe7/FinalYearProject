# ADR-0027: Spotlight fence integrity on every prompt path

## Status

Accepted — 2026-11-06

## Context and Problem Statement

Retrieved text is wrapped in hash-bound `<passage>` markers so the model treats instructions inside them as data (OWASP LLM01:2026, OWASP ASI01, Microsoft spotlighting). That boundary holds only when the enclosed text cannot write the delimiter.

Three paths broke it:

1. A passage body was inserted raw, so `</passage>` followed by `## User question` left the data region.
2. Source, page, and live-web labels sit outside the marker and could carry a newline or a heading.
3. The tool-calling prompt used predictable ids `p1`, `p2`, and the cloud fallback pasted passages as raw `Context:` with no scrub and no fence.
4. Consent personalization is appended to the system prompt, so a heading there is a system rule (OWASP LLM08:2026 hidden context).

Regex screening still drops instruction-shaped phrases. It does not stop a delimiter breakout that uses no such phrase.

## Decision Outcome

Every prompt path — standard, tool-calling, and cloud fallback — builds passages through `llm._spotlight_passage`:

- `scan_retrieved_text` runs first.
- `fence_spotlight_body` strips invisible characters, folds fullwidth brackets when one is present, and rewrites a passage tag or an assembler section heading (`## User question`, a bare `## Rules`). A statutory heading such as `## Rules of origin` is left as written.
- The marker is `p{n}-{sha256-8}` of the fenced source and the index, including on the tool path.
- Header fields are single-line and cannot contain `#` or a passage tag.
- Text appended to the system prompt (personalization, tone hint) is scrubbed and fenced the same way.
- A rewritten tag or heading records `guardrail_indirect_injection` with reason `spotlight_breakout`.
- The delimiter matcher keeps whitespace around the optional closing slash in disjoint regex branches, so attacker-controlled tab runs cannot cause polynomial backtracking (CodeQL `py/polynomial-redos`).

The user question is fenced so it cannot forge a passage tag. Verified calculator facts stay outside this fence; they are produced by code.

## Consequences

- Positive: a poisoned page, upload, or web snippet cannot close the spotlight or open a second question, on the local model or the cloud fallback.
- Positive: the tool path no longer teaches a forgeable `p1` id.
- Negative: a taxpayer who types a literal `<passage>` tag sees it replaced. Statutory prose that is not a prompt section heading is unchanged.
- Negative: the fence is a delimiter control. It does not decide whether a planted rate inside an otherwise ordinary sentence is true. Corpus provenance remains the control for that.

## Confirmation

Verified by `tests/agents/test_spotlight_fence.py`, including a long-tab delimiter case. Threat T46 and ASI01 in `governance/ai_risk_manifest.yaml` cite the same fence.

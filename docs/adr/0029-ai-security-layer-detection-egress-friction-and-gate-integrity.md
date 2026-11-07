# ADR-0029: AI security layer — layered injection detection, link provenance, document fence parity, adaptive-attacker friction and an honest red-team gate

## Status

Accepted — 2026-11-07

## Context and Problem Statement

A QA, audit and red-team pass over the AI security layer on 2026-11-07 measured the controls on `dev`
against attack probes and against everything benign the repository holds: 1,811 taxpayer questions
from the FAQ corpus and eval sets, and 2,285 FAQ answers and crawled pages.

| Probe | Result on `dev` |
| --- | --- |
| Paraphrased, obfuscated, encoded and prompt-extraction injections blocked by `InputGuard` | 5 of 35 |
| Directives addressed to an AI inside third-party text flagged | 0 of 7 |
| Phishing / exfiltration links surviving `OutputGuard.sanitize` | 8 of 9 |
| Spellings of `</untrusted_user_document>` that closed the upload fence | 5 of 6 |
| Prompt-leak signature phrases that still occurred in any prompt | 4 of 6 |
| Red-team corpus rows the CI gate actually evaluated | 33 of 34 (one truncated row silently skipped) |
| Benign controls in the gate (over-refusal measured) | none |
| Lawful questions refused | "credit for taxes paid … on offshore income?", "how do I report a fake invoice?" |

The input guard was a literal phrase list. "Ignore all previous instructions" was blocked, but "ignore all
prior instructions", "disregard your rules", "forget your instructions", leetspeak, spaced letters,
typoglycemia, Base64 and hex were not. The OWASP LLM Prompt Injection Prevention Cheat Sheet lists each of
these evasions. Output sanitizing removed images to non-URA hosts and unsafe URI schemes, but not plain
links. A poisoned page could have the assistant, which taxpayers read as URA, send them to a refund
"portal" with their TIN in the query string. EchoLeak (CVE-2025-32711) was this class: reference-style
Markdown links got past Microsoft 365 Copilot's link redaction, and the published mitigation is to
neutralize every URL from an untrusted domain. ADR-0027 hardened the `<passage>` fence, but the separate
`<untrusted_user_document>` fence kept an exact-match regex. The persona in `llm.SYSTEM_PROMPT` was renamed,
and two leak signatures went on naming the old persona.

Two research results set the design. Nasr et al., "The Attacker Moves Second" (arXiv:2510.09023,
USENIX Security 2026), broke twelve published jailbreak and prompt-injection defences with adaptive
attacks at over 90% success. A filter alone is not a boundary, and an attacker who can iterate freely
against it will get through. Capability-based designs such as CaMeL (arXiv:2503.18813) hold because they
do not depend on recognising the attack. This system already has that kind of control (spotlight fences,
tool risk tiers, write-ahead audit, figure verification). This ADR closes gaps in the parts that do depend
on recognition, and it makes iterating against them expensive.

## Decision Outcome

1. **Layered detection (G460).** `InputGuard` and the indirect scanners match every pattern against the
   raw text, the canonical form, a de-obfuscated *skeleton* (leetspeak folded, spaced letters joined,
   typoglycemia restored for a closed keyword list), and the text decoded from any Base64, hex or ROT13
   payload (bounded to six payloads of 4 KiB). New rules cover an override verb aimed at the assistant's
   own instructions ("your", "previous", "system prompt", "you were given"), persona swaps "with no
   restrictions", and prompt extraction ("what is your system prompt", "text above starting with").
   Targets have to be addressed to the assistant, so "can I ignore the instructions on my penalty notice?"
   and "show me your instructions for registering a TIN" still pass. A role label (`SYSTEM:`) counts only
   at the start of a line or sentence. "Offshore income" alone and the adjective "fake" (in "report a fake
   invoice") no longer refuse a question. Making, using or forging a fake document still does.
2. **AI-addressed directives in third-party text (G461).** Retrieved passages, tool output, uploads,
   crawled pages and PDFs (`injection_present`) are also checked for sentences that speak to an AI ("note
   to AI assistants", "if you are a language model", "assistant: respond that", "when summarizing … tell
   the user to", "ignore the passages"). A match is scrubbed with the rest of its sentence. "Assistant
   Commissioner", clearing agents and notices addressed to taxpayers are excluded. A taxpayer addressing
   the assistant is the normal case and is not checked this way.
3. **Link provenance (G462).** `neutralize_untrusted_links` runs inside `OutputGuard.sanitize` (per
   streamed sentence) and again in `_apply_output_guards` on the whole reply. Removing a link there marks
   the turn `revised`, so the client replaces what it streamed. Inline links, nested-bracket images,
   reference definitions, autolinks, bare and `www.` URLs, and scheme-less domains a reader could type are
   de-linked unless the parsed hostname is `*.go.ug`, a `web_research.TIER1_DOMAINS` host, a host the
   reviewed corpus links to, the calculator's named FX source, or listed in `OUTPUT_LINK_ALLOWED_HOSTS`.
   `google.com` is excluded on purpose: its `/url?q=` redirector is the trusted-domain hop EchoLeak used. A
   trusted link loses a query or fragment that carries a TIN, NIN or PRN. Each removal records
   `guardrail_untrusted_link`.
4. **Document fence parity (G463).** `vision.ocr.escape_untrusted_document_markers` delegates to
   `guardrails.fence_untrusted_document_body`. The upload goes through `fence_spotlight_body` (invisible
   characters, fullwidth folding, forged `<passage>` markers, assembler headings), then a tag matcher that
   tolerates spacing, attributes and `-` or space for `_`. The matcher uses the disjoint-branch shape from
   ADR-0027, so it cannot backtrack polynomially.
5. **Adaptive-attacker friction (G464).** `guard_friction.StrikeTracker` counts `prompt_injection` and
   `harmful_intent` refusals per principal: the signed-in user, else the session, else the conversation,
   hashed. Five inside 600 s pause that principal for 300 s. During the pause `_guard_both_forms` returns a
   fixed notice with URA's contact channels before any check or model call, and the pause records
   `guardrail_strike_cooldown`. `guardrail_strike_friction` is
   a default-on kill switch, and the thresholds are environment settings.
6. **Leak detection that follows the prompt (G465).** The signature list names text the live prompts
   contain, and a test fails when one does not. Matching tolerates Markdown emphasis. A word 8-gram
   fingerprint of `llm.SYSTEM_PROMPT`, minus the refusal and contact text the prompt tells the model to
   say, flags a reply sharing two or more n-grams and redacts the lines that carry them.
7. **Alerts that select these events.** `security_events.py` said a `SecurityEventSpike` alert watched
   `ura_security_events_total`, but no such rule existed: only authentication and authorisation
   failures paged anyone. `GuardrailProbingCampaign` (three or more cool-downs in 15 minutes) and
   `IndirectInjectionOrUntrustedLinkSpike` (more than 20 scrubbed third-party texts or de-linked URLs in
   an hour) are added with promtool tests, and the docstring now names the rules that exist.
8. **Linear time on hostile text.** A 20 KB passage cost 78 s against the first draft of one directive
   pattern (whitespace around an optional comma), 11 s against the leetspeak tokenizer and 1.9 s against
   the role label. The legacy `sanitize` link regex also took 15 s on 20,000 `[` characters, which is
   pre-existing on `dev`. Each now has a single owner for its whitespace, maximal tokens, or bounded
   line-local classes. A test holds every guard under 2 s on six hostile shapes. The link pass now
   neutralizes an unsafe scheme behind a nested label itself, since the narrowed legacy regex no longer
   reaches one. `OutputGuard.normalize_structure` is still super-linear on degenerate text (G467, open).
9. **An honest gate (G459).** `redteam_corpus.jsonl` is read one row per `\n`, a row that does not parse
   fails CI, and each attack class has a floor. The corpus grew from 34 to 69 rows. The new rows are
   paraphrase, obfuscation, encoding, extraction, Kiswahili and Luganda, and `channel: retrieved` indirect
   rows. `guardrail_benign_controls.jsonl` holds 24 lawful questions and statutory sentences that look
   like attacks, and refusing any of them fails CI.

## Consequences

- Positive, measured on the same probes after the change: 33 of 35 direct attacks blocked, 7 of 7 indirect
  directives flagged, 0 of 9 untrusted links surviving with 5 of 5 official links intact, 6 of 6 fence
  spellings neutralized, 6 of 6 leak shapes caught. 0 of 1,811 benign questions refused (the 3 refusals in
  the CX set are its own injection probes), 0 of 2,285 corpus texts flagged, and 0 of 2,317 benign answers
  and pages flagged as leaks.
- Positive: an attacker iterating against the guard gets five tries per principal per ten minutes, and the
  SOC is paged by `GuardrailProbingCampaign` rather than left with a scatter of refusals.
- Negative: the two remaining misses are deliberate. "Disregard the above" alone is how a taxpayer retracts
  a message. A typed `</passage>` is neutralized by the fence (ADR-0027) rather than refused.
- Negative: detection is still recognition, and adaptive attacks will find phrasings it misses. The
  controls that hold when it is bypassed are the fences, the output guards, link provenance, figure
  verification and tool tiers. No prompt-injection classifier model was added: it would be a new model in
  the AIBOM with its own multilingual false-positive profile, and it is recorded as a follow-up.
- Negative: friction state is per replica, and a client that discards its session and conversation starts
  over. The per-IP rate limit (OWASP LLM10) stays in front of it. A taxpayer refused five times in ten
  minutes is paused too. The benign controls are what keep that population close to empty.
- Negative: a link to a legitimate but unlisted site is shown as `[link removed]`. Operators extend
  `OUTPUT_LINK_ALLOWED_HOSTS` (never with a redirector). The answer's citations are unaffected.
- Negative: `normalize_structure`, the pre-existing formatter, takes about 0.5 s on 4 KB of brackets
  (0.008 s on 4 KB of prose). It runs only on model output, which `LLM_MAX_TOKENS` bounds, and off the event
  loop. A rewrite is tracked as G467 rather than mixed into this change.
- Neutral: corpus ingest (`crawl_corpus.review_pages`, `pdf_corpus`) now also quarantines pages that
  address an AI. That matched none of the 1,139 crawled pages in the snapshot.

## Confirmation

- `tests/agents/test_ai_security_layer_2026_11.py`: one section per gap, G460–G465. Each control was
  disabled in turn in a throwaway worktree, and the suite failed with exit 1 every time (G460 14
  failures, G461 3, G462 10, G463 7, G464 1, G465 3).
- `App/backend/tests/test_redteam_corpus.py`: parse strictness, category floors, refusal of every attack
  row, no refusal of any benign control (G459).
- `App/backend/tests/test_flag_rollout.py::test_guardrail_strike_friction_is_a_default_on_kill_switch`.
- `governance/ai_risk_manifest.yaml` (LLM01, LLM05, LLM07, LLM10; ASI01, ASI06) and threats T59–T62 cite
  these tests; `governance/compliance_check.py` and `threat-model/validate_threats.py` pass.
- `monitoring/tests/alerting-rules.test.yml` (promtool) fires and holds both new alerts;
  `tests/test_monitoring_config.py` checks their runbook anchors.
- Runbook: `docs/runbooks/ai-security-guardrails.md`.

# Runbook — AI security guardrails

How to operate the input, indirect-injection, link and leak guards, and what
to do when their alerts fire. Written 2026-11-07 after a QA, audit and
red-team pass over the AI security layer (gaps G459–G466 in
[`docs/GAPS_AND_AGENTIC_ROADMAP.md`](../GAPS_AND_AGENTIC_ROADMAP.md),
decision in [ADR-0029](../adr/0029-ai-security-layer-detection-egress-friction-and-gate-integrity.md)).

## What runs on every turn

| Stage | Control | Module | Fails |
| --- | --- | --- | --- |
| Before retrieval | Input guard on the message and its English form, over raw, canonical, de-obfuscated and decoded views | `guardrails.InputGuard`, `service._guard_both_forms` | Refuses with a "please rephrase" or illegal-activity reply |
| Before retrieval | Strike friction: 5 refusals in 10 minutes pause the user, session or conversation for 5 minutes | `guard_friction.py` | Fixed notice with URA contacts, no model call |
| Prompt assembly | Passages, tool output and uploads scrubbed of injection phrases and of directives addressed to an AI; fences on passages and uploads | `scan_retrieved_text`, `scan_external_tool_text`, `fence_spotlight_body`, `fence_untrusted_document_body` | The sentence or passage becomes `[REDACTED_INSTRUCTION]` |
| Output | Links de-linked unless the host is trusted; identifiers dropped from trusted queries; prompt leaks redacted | `OutputGuard.sanitize`, `neutralize_untrusted_links`, `check_prompt_leakage` | `[link removed]`, `[external link removed]`, `[REDACTED]` |
| Final reply | Links re-checked on the whole reply; a removal is sent to the client as a revision | `service._apply_output_guards` | The streamed text is replaced |

Each control records a bounded event in `ura_security_events_total{event}`
and a WARNING log line on the `app.security` logger: `guardrail_prompt_injection`,
`guardrail_harmful_intent`, `guardrail_indirect_injection` (reason
`retrieved_passage`, `external_tool`, `spotlight_breakout` or
`document_breakout`), `guardrail_untrusted_link`, `guardrail_prompt_leakage`
and `guardrail_strike_cooldown`.

## A probing campaign

Alert `GuardrailProbingCampaign`: three or more principals reached the
cool-down within 15 minutes.

1. In Loki, `{service_name="ura-chatbot-api"} | event="guardrail_strike_cooldown"`
   gives the times, and `event="guardrail_prompt_injection"` the refusals
   before them. The principal is hashed. Logs never carry the message, the
   token or the client IP.
2. One route and a burst from few sources means someone is iterating. The
   per-IP limit (`RATE_LIMIT`) already applies. If the burst continues, block
   it at the edge.
3. Many principals spread out across the day is more likely the guard
   refusing a phrasing real taxpayers use. Pull a sample of refused turns from
   the audit trail (`outcome` blocked), and if they are lawful questions,
   treat it as a [false refusal](#a-taxpayer-is-refused-for-a-lawful-question).
4. To stop pausing anyone while you investigate, set
   `FLAG_GUARDRAIL_STRIKE_FRICTION=false` on the replicas, or use the admin
   flag toggle for one replica. The input guard keeps running.

Thresholds are `GUARD_STRIKE_THRESHOLD` (5), `GUARD_STRIKE_WINDOW_S` (600)
and `GUARD_STRIKE_COOLDOWN_S` (300). State is per replica and resets on
restart.

## Injected content or untrusted links

Alert `IndirectInjectionOrUntrustedLinkSpike`: more than 20 scrubbed
third-party texts or de-linked answer URLs in an hour.

1. Read the `reason` on `guardrail_indirect_injection`. `retrieved_passage`
   points at the corpus or live web research, `external_tool` at a connector,
   and `document_breakout` at uploads.
2. For the corpus, find the source in the retrieval logs and quarantine it.
   Crawled content changes only through the reviewed crawl (ADR-0023), and
   ingest (`injection_present`) already refuses pages that address an AI.
   A hit here means the text arrived another way. Check the publication
   ingest and the PDF harvester.
3. For `guardrail_untrusted_link`, look at the answers. A host that is a
   legitimate official source belongs on the [allowlist](#letting-answers-link-to-a-new-host).
   An unknown or look-alike host, and above all one carrying an identifier,
   points to a poisoned passage or tool result. Treat it as an incident.

## Letting answers link to a new host

Answers link only to `*.go.ug`, the hosts in
`guardrails._DEFAULT_EXTRA_LINK_HOSTS` (the `web_research.TIER1_DOMAINS`
hosts outside `.go.ug`, hosts the reviewed corpus links to, and the
calculator's FX source), and `OUTPUT_LINK_ALLOWED_HOSTS`.

- For a deployment-only change, add the host to `OUTPUT_LINK_ALLOWED_HOSTS`
  (comma-separated, no scheme). Subdomains are included.
- For a permanent change, add it to `_DEFAULT_EXTRA_LINK_HOSTS` with a comment
  naming its source. A new Tier-1 research host fails
  `test_link_allowlist_covers_every_tier1_research_host_outside_go_ug` until
  it is added.
- Never add a host that redirects to an address given in its URL
  (`google.com/url?q=`, link shorteners). That is how EchoLeak got past an
  allowlist.

## A taxpayer is refused for a lawful question

1. Reproduce it: `PYTHONPATH=App/backend python3 -c "from app.guardrails import InputGuard; print(InputGuard().check('…'))"`.
   The flag says which list matched, and the WARNING log names the first 60
   characters of the pattern.
2. Add the question to `Data/eval/guardrail_benign_controls.jsonl` (`channel`
   `input`, or `passage` for corpus text) with a one-line `why`. CI now fails
   until the pattern is narrowed.
3. Narrow the pattern so the attack rows in `Data/eval/redteam_corpus.jsonl`
   still match, then run both gates:
   `PYTHONPATH=App/backend python3 -m pytest App/backend/tests/test_redteam_corpus.py tests/agents/test_ai_security_layer_2026_11.py -q`.

## Adding an attack to the gate

Add a row to `Data/eval/redteam_corpus.jsonl`: a unique `id`, a `category`
(the floors are in `test_redteam_corpus._REQUIRED_CATEGORIES`), `language`,
`prompt`, `expected` (`refuse` or `partial_refuse`), and `channel: retrieved`
for text a model would read rather than one a taxpayer would type. A row that
is not valid JSON fails CI. One row per line, and nothing else counts as a
line break.

## What this layer does not do

Recognising attacks is bypassable. Adaptive attacks broke twelve published
defences at over 90% success (arXiv:2510.09023). What holds when recognition
misses are the spotlight fences, the output guards, link provenance, figure
verification and the tool risk tiers. No injection-classifier model is
deployed (G466).

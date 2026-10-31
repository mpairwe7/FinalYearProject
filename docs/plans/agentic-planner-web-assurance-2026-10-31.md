# Agentic planner and live web assurance review — 31 October 2026

Status: phased code fixes implemented with regressions; production acceptance
remains open (see [Residual risk](#residual-risk)). Decision record:
[ADR-0021](../adr/0021-planner-bound-web-evidence-and-honest-live-checks.md).

Baseline: `a1ab7d3a` (`dev`), branch `fix/agentic-planner-web-assurance-2026-10-31`.
Scope: the supervisor and scenario planner, the live web research tool and the FX
source behind it, from the question to the audit row. This is a targeted review,
not a penetration test or a legal compliance certification.

Method: every finding below was reproduced against the running code before it was
fixed, and each has a regression test. Findings that came from reading alone and
did not reproduce were dropped (the planner's `tool_results` overwrite is order-safe
today, so it is recorded as a hardening rather than a defect).

## Verified reference basis

Checked online on 2026-10-31. These are engineering references, not a claim that
foreign instruments bind URA.

| Reference | Quality | What it changed here |
| --- | --- | --- |
| [OWASP Top 10 for Agentic Applications](https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/) (9 Dec 2025) | Primary | ASI01 goal hijack through content the agent reads, ASI08 cascading failures, ASI09 trust exploitation, ASI10 rogue agents frame the findings. |
| [CaMeL, Debenedetti et al.](https://arxiv.org/abs/2503.18813) (2025) | Primary (paper) | Control flow is taken from the trusted query, so untrusted data cannot change it. Basis for keeping search out of the model's tools. |
| [USENIX Security 2026, "Indirect Prompt Injection in the Wild"](https://www.usenix.org/system/files/conference/usenixsecurity26/sec26_prepub_chang.pdf); [Zscaler ThreatLabz](https://www.zscaler.com/blogs/security-research/indirect-prompt-injection-web-content-targets-ai-agents) (Jul 2026) | Primary / vendor | Injection in web content is operational, and pattern filters can be optimised around. Regex scanning is treated as one layer, not the boundary. |
| [OpenTelemetry GenAI conventions](https://github.com/open-telemetry/semantic-conventions-genai) | Primary | Moved to their own repository, status **Development**. `plan` and `execute_tool` are well-known operations; `plan` is for phases that can be told apart reliably. |
| [Tavily](https://docs.tavily.com/documentation/api-reference/endpoint/search), [Brave](https://api-dashboard.search.brave.com/app/documentation/web-search/query), [SearXNG](https://docs.searxng.org/dev/search_api.html) API documentation | Primary | Adapter contracts. Brave's `age` fields and SearXNG's JSON shape are **not** documented on the pages read, so those adapters read them defensively. |
| [NIST AI Agent Standards Initiative](https://www.nist.gov/news-events/news/2026/02/announcing-ai-agent-standards-initiative-interoperable-and-secure) (Feb 2026) | Primary | Direction of travel only. The COSAiS agent overlays were not found in final form; nothing here claims conformance to them. |
| [Singapore IMDA agentic AI framework](https://www.allenandgledhill.com/sg/publication/articles/33285/imda-updates-model-ai-governance-framework-for-agentic-ai) (Jan 2026, updated May 2026) | Secondary (law-firm summary) | Bound risks upfront, keep humans accountable, address automation bias. |
| Uganda Data Protection and Privacy Act 2019, s.19 and 2021 Regulations, reg. 30; [PDPO decision on Google](https://cipesa.org/2025/07/ugandan-regulator-finds-google-in-breach-of-countrys-data-protection-law-orders-local-registration/) (Jul 2025) | Secondary | Cross-border transfer needs demonstrable safeguards, and the regulator has enforced it. The statutory text was not retrieved: this is an engineering control pending DPO review, not legal advice. |
| [Google Custom Search JSON API closing 1 Jan 2027](https://www.firecrawl.dev/blog/google-search-json-api-alternative), Bing Search APIs retired 11 Aug 2025 | Secondary, consistent across sources | Rules out the obvious "official" search options. |
| [DuckDuckGo terms](https://duckduckgo.com/terms) and [acceptable use](https://duckduckgo.com/acceptable-use) | Primary | Neither addresses scraping, so **no breach of terms is claimed**. The finding is that no API contract or availability commitment exists. |

**Could not be verified, and not relied on.** `ura.go.ug` dropped connections from
the fetch environment and `bou.or.ug` served a script-rendered shell. So the text of
Income Tax Act s.57 (Currency conversion), a Bank of Uganda data feed and any URA
WordPress API all remain unconfirmed. The BoU mid-rate rule used below is this
repository's own `fx_conversion_rule`, which is itself marked "citation and snapshot
unverified". The unreachable sites are also evidence: official sources go down, so
the design must degrade honestly (G317, G318).

## Observed in production

Read from the Space (`landwind22/ura-chatbot`, image `sha-6f3047d`) on 2026-10-31.
Secret values are not readable, so a flag's state comes from which stages the
`generate:` log line records, not from the presence of its key.

| Observation | Evidence | Meaning |
| --- | --- | --- |
| Live research is on | `live_web_research` appears in `stages=` | The defects below were serving taxpayers, not sitting behind a flag |
| The check fails silently | `'live_web_research': 12122.73` ms on the one time-sensitive question probed, then an ordinary answer with `retrieval_mode=sparse` and no disclosure. Turns with no trigger took under 30 ms | Consistent with two 6 s search timeouts, though the cause is inferred because that path logged only at debug level. G318 and G319 live |
| Routes are ignored | `supervisor: route=tools`, then `agent_role=rag_answerer` | G309 live |
| The graph is not running | No `langgraph_execution` stage, though `FLAG_LANGGRAPH` is set | G332 is latent |
| No provider exists | No `TAVILY_API_KEY`, `BRAVE_SEARCH_API_KEY` or `SEARXNG_URL` | G326: the check cannot succeed until one is added |
| Corpus sync flag is set | `FLAG_AUTO_QDRANT_WEB_SYNC` key updated with the research flag, value unreadable | G331. No search succeeds today, so nothing could trigger it; a production guard now stops a future provider activating it |

An explicit request for a person was deliberately not probed in production, because it
could open a real ticket. G309's escalation behaviour was reproduced locally.

## Findings and phases

Severity is the consequence for a taxpayer, not the difficulty of the fix.

### Phase 0: stop the line

| Gap | Severity | Evidence before the fix | Fix |
| --- | --- | --- | --- |
| G309 | Critical | `elif tiering_enabled:` inserted above the route handling in #599 (2026-10-19). With hits, "I want to speak to a human officer" returned `mode=hybrid, escalation_required=False, ticket=False`. With `agentic_mode` off and tiering on: `AttributeError: 'NoneType' object has no attribute 'route'`. The suite stayed green because an empty index abstained and opened a ticket by another path. | Route handling applies whichever branch produced the decision, matching the stream path. Tests retrieve a passage on purpose. |
| G310 | Critical | Search titles and snippets were rendered into `verified_facts_addition`, unscanned and outside passage spotlighting, and reached the **system prompt** (no calc block) or the **tool ground truth** (with one). A planted "IMPORTANT NOTICE TO THE ASSISTANT" arrived verbatim. | `live_check_notice` is written wholly by the code and holds no external text. Web text reaches the model only as scanned, spotlighted passages. |
| G311 | Critical | `verify_claims` treats `verified_facts` as deterministic. Numbers in a snippet set `backed_by_verified_facts`, which **disabled the contradiction check** for any claim using them. | Web text never enters `verified_facts`. |
| G312 | High | Commentary and unopened snippets were headed "Verified from official Ugandan portals" and `retrieval_mode=live_web_verified`. | Headers state authority, evidence and age. `live_web_verified` requires a document read this turn; otherwise `live_web_unconfirmed`. |
| G313 | High | Snippets and anchors were zipped by position, so one missing snippet attributed *"Kenya VAT is 16 percent"* to a `ura.go.ug` URL. | A snippet is only sought between its anchor and the next. |
| G314 | High | `"ura" in combined` matched "ins**ura**nce" and "nat**ura**l", passing Kenyan, Nigerian and Tanzanian pages as Uganda-scoped. `xe.com/blog/*` and `google.com/finance` were Tier 1 "official primary source". | Whole-word Uganda scope. Commercial rate sites removed from Tier 1. |
| G315 | High | Name filtering relied on capitalisation: `landlord john mukasa kamwokya owes rental` left the deployment for a US search engine, and the same words reached `web_sync_ledger` topic keys. | Closed vocabulary. Only listed generic tax terms can leave. |
| G316 | High | "This is unacceptable, connect me to a supervisor now" routed to `get_current_date` on the word "now". "Transfer me to a person" went to RAG. | Handoff phrasing and `supervisor`/`manager`/`representative` added to the escalate table, which runs first. |

### Phase 1: web tool reliability

| Gap | Severity | Evidence | Fix |
| --- | --- | --- | --- |
| G317 | High | One backend with no API contract, reached with a browser User-Agent. A bot-challenge page parsed to `[]`, indistinguishable from "URA published nothing". | `web_search_providers.py`: Tavily, Brave and SearXNG behind one contract, with failover, a breaker per provider and named failure kinds. The HTML path cannot run in production. A vendor's generated answer is never requested. |
| G318 | High | With search down: no log at INFO or above, no metric, no event, and the taxpayer got a stale answer that looked current. | Six-valued `status`, `web_research.completed` event, metrics, and a disclosure on every serving path. A crashed step is still disclosed. |
| G319 | Medium | `specs[:2]` let one trigger spend the whole allowance: of three triggers only the first was searched. Sequential 6 s searches plus a 12 s download against a declared 2.5 s budget. Unbounded L1 cache, no negative cache, a Redis prefix nothing read, and `2025 OR 2026` hard-coded. | Fair share across triggers, concurrent searches under a turn deadline, a bulkhead pool, a bounded LRU with negative and volatile TTLs, dates from the clock. Dead Redis config removed. |

### Phase 2: real-time planning

| Gap | Severity | Evidence | Fix |
| --- | --- | --- | --- |
| G320 | High | 10 of 11 sampled time-sensitive questions fired no trigger ("Has the deadline been extended this month?", "Is the URA portal working today?"). A question with zero hits fired nothing. An RRF-only hit read as confidence 0.0, so a reranker outage sent **every** question to the web. Any last-year hit fired `stale_corpus_edition`, even for "What is a TIN?". | `recency_intent` trigger with a precision set as well as a recall set, `gating_relevance` (RRF is unknown, not low), corpus gaps count only when retrieval ran and the question has a tax subject, and edition only matters when the answer varies by edition. |
| G321 | High | A 2019 waiver notice was presented as the current position. No result carried a date. | Dates from the provider, upload path or the page's own month and year (never a bare year, which names laws). Old items are labelled and reported as `stale_only`. |
| G322 | High | `live_web_verified` turns were cacheable for an hour, and a time-sensitive question could be served yesterday's answer before any check ran. | Time-sensitive turns neither read nor write the semantic cache. Volatile search results expire in 15 minutes. |
| — | Medium | `langgraph` had no live check and no disclosure. | One helper for REST, SSE and graph; once per turn however often reflection retrieves. |

### Phase 3: FX integrity

| Gap | Severity | Evidence | Fix |
| --- | --- | --- | --- |
| G323 | High | XE and Google 16% apart: XE served, `is_live=True`. A six-year-old mirror payload: `is_live=True`. A plausibility constant was served as "BOU_mid_rates_2026.json (offline reference snapshot)". A forged `CONSENT=YES` cookie and browser User-Agent were sent. Market quotes outranked the BoU snapshot, though `fx_conversion_rule` is `bou_mid_rate`. `follow_redirects=True` bypassed the host allowlist. | Disagreement fails closed and asks the taxpayer. Stale or failed mirrors are refused. Verified BoU snapshot takes precedence. Market quotes are labelled estimates. No forged consent, redirects re-validated, per-lookup budget. |

The shipped snapshot is `status: unverified` with `rates: []`, so **no official BoU
rate is available in the product today**: every conversion without a taxpayer-supplied
rate is an estimate, and now says so. Dropping the forged cookie and browser identity
may make XE and Google refuse requests, leaving the mirror as a single source
(`ura_fx_quotes_total{outcome="single_source"}`). That was accepted deliberately.

### Phase 4: planner hardening

| Gap | Severity | Evidence | Fix |
| --- | --- | --- | --- |
| G324 | High | A crashed `calculate_paye` vanished: the block still said "present these exact computed figures" with the PAYE line missing, and the model filled the gap. With live FX on and the network down, nothing asked for the rate. A rendering error in one tool lost **every** figure. The plan ran twice per turn (prompt, then verification), so a reply could be judged against figures the model never saw. Nine sequential steps had no budget or deadline. Tier selection planned on untranslated text. The graph bound the first number in the question as the amount. | `PlanRunReport` with four step outcomes, "Not calculated" lines, per-step rendering guard, one execution per turn, `ToolCallBudget` plus deadline, FX chained by currency, English plan text, unambiguous-amount binding, plan digest in the audit row, OTel `plan` span. |
| G325 | Medium, part open | `TurnPipelineOrchestrator` ran only in tests. The "every turn" ASI10 claim rested on a digest in a readiness report. | `attest_turn` holds each REST turn to the manifest from its real stage timings, and the verdict enters the audit row. **SSE records no stage timings, so it is not attested**, and the docstring now says so. |

### Phase 5: governance

G330: threat T43 claimed "pre-connect DNS IP resolution" that did not exist.
Implemented (`resolves_to_public_address`) and the entry reworded: the check narrows
rebinding but cannot close it, because the client resolves again on connect. The
runbook and `.env.example` documented `LIVE_FX_*` variables the code never read;
corrected to the real `FX_*` names.

## What independent review changed

CodeRabbit reviewed the first version of these fixes and found real faults in them, all
confirmed against the code before being fixed: every hit's edition was pooled, so a claim
could pass on another passage's metadata; SearXNG was left off the cross-border list although
it forwards queries to external engines; the disclosure and audit were lost on early exits;
a topic capped out of the search got no warning; and a default-on compose overlay contradicted
the claim that corpus sync stayed off. CI separately caught tests that only passed in isolation,
one of them passing without testing anything. The audit's own first draft had these defects,
which is the argument for independent review of work like this.

## Design decisions worth challenging

* **The model has no search tool.** A query string written by a model that has read
  untrusted text is an exfiltration channel. Search stays planner-initiated, as in
  CaMeL, at the cost of the model being unable to follow up on what it finds.
* **A failed check does not block the answer.** The corpus answer is usually still
  useful, so the turn answers and discloses. An operator wanting refusal instead
  would change `LIVE_DISCLOSURES` handling, not the detection.
* **No provider is not a boot failure.** It degrades to a disclosed "could not check",
  which is safe; refusing to start would turn a missing key into a full outage.
* **Divergent FX sources are not outvoted.** A third source could break the tie, but a
  rare failure asking the taxpayer for the rate is the safer default here.

## Residual risk

| Gap | Open item | Owner |
| --- | --- | --- |
| G325 | Attest the streaming path: it needs stage timings, and most web traffic uses it. | Backend |
| G326 | Choose and fund a search provider; complete the transfer assessment. Until then production reports `unavailable`, disclosed, whenever a live check is needed. Hosted adapters are proven against documented contracts and mocks, **not live APIs**. | Service owner, DPO |
| G327 | Obtain Bank of Uganda rates through a supported route and load a verified snapshot. Confirm ITA s.57 text. | Tax policy, data owner |
| G328 | Recency cues are English; other locales rely on `retrieval_query` being translated. Vernacular cues need reviewed vocabulary, not invented terms. | Language leads |
| G329 | Tier-1 HTML pages are snippet-only, so a notice published as a web page stays `unconfirmed`. The listing URL is known from the crawl (`/en/category/publications/public-notices/`); its structure is unverified, so no parser was written. | Backend |
| G331 | `auto_qdrant_web_sync` can change the corpus from a user turn (ASI06), and `App/docker-compose.gpu-salt.yml` defaults it on. Production now also requires `WEB_SYNC_PRODUCTION_APPROVED`, so a working provider cannot activate it by accident. Reviewing each change before it applies remains open. | Corpus owner |
| G332 | Graph path: `graph.run` passes no `thread_id`, so durable checkpoints and resume never run, and `node_synthesize` skips the claim verifier. Logs show the graph is not running in production; it should stay that way until both are resolved. | Backend |

Production acceptance needs a live probe per status on the deployed Space, asserted
from logs and the audit row rather than the reply text. See the
[runbook](../runbooks/live-web-research-and-qdrant-sync.md#7-acceptance-probes).

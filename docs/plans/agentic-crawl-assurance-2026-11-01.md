# Agentic crawl assurance review — 1 November 2026

Status: phased fixes implemented with regressions; production acceptance open (see
[Residual risk](#residual-risk)). Decision record:
[ADR-0023](../adr/0023-stale-driven-crawl-and-reviewed-corpus-release.md). Operations:
[crawl runbook](../runbooks/crawl-operations.md).

Baseline: `f3d024e7` (`dev`), branch `fix/agentic-crawl-assurance-2026-11-01`. Scope: every
path by which web content becomes something the agent believes, from discovery to the
citation a taxpayer sees: `crawler.py`, `crawl_corpus.py`, the PDF harvester, publications
ingest, the freshness checks, and the workflows that move crawl data to production. This
is a targeted review, not a penetration test or a legal compliance certification.

Method: each finding was reproduced against the running code, the committed data or a
live service before it was fixed, and each has a regression test. Where a hypothesis did
not survive measurement, that is recorded below.

## The owner's requirement

Set during the review: **the crawl should update when information becomes stale and
outdated, and must not replace data that is up to date.** Phase 1 implements it, and it
overrode an earlier design that re-read everything on each pass.

## What the audit found, in one paragraph

The crawl was reporting success while barely reading the site. URA's sitemaps list 2,021
content URLs and the crawl had captured 265 of them. Of 90 URLs changed since 1 July it had
never read 81, including URA's VAT, withholding, rental and stamp duty pages and the notice
giving the commencement date of the 2026 amendment Acts. What it did store included
archive.org's home page filed under a URA address, and Internet Archive fundraising text
filed as URA's employment income guide. Its PAYE page still showed the superseded `235,000`
band, dated as current. All of this reached production unreviewed, while every workflow
stayed green, and one of them could not fail.

## Verified reference basis

Checked online on 2026-11-01. These are engineering references, not a claim that foreign
instruments bind URA.

| Reference | Quality | What it changed here |
| --- | --- | --- |
| [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.txt) | Primary, read in full | 2.2.2 most-octets match, allow wins ties; 2.3.1.3 4xx means no rules; 2.3.1.4 server errors mean "MUST assume complete disallow"; 2.3.1.2 five redirects; 2.5 at least 500 KiB. |
| [Wayback CDX server README](https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server) | Primary (dated 2013), confirmed by live probe | "all captures after the first one that are duplicate are filtered out". Live: `collapse=urlkey` returned `20231124` for `/en/`, whose newest capture is `20261031`. **No rate limit is documented**: third-party figures were not relied on. |
| [GitHub: `GITHUB_TOKEN`](https://docs.github.com/en/actions/concepts/security/github_token) | Primary | Events it triggers "will not create a new workflow run", except dispatch events and, in an approval-required state, pull requests. Explains why no crawl commit ever built. |
| [OWASP Top 10 for Agentic Applications](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) (Dec 2025), ASI06 | Primary, text read | RAG poisoning "via poisoned sources ... or over-trusted pipelines". Mitigations used: scan writes "before commit"; "only authenticated, curated sources"; "source attribution and detect suspicious updates"; "snapshots/rollback"; "human review for high-risk actions"; "expire unverified memory". |
| [OWASP LLM08:2025](https://genai.owasp.org/llmrisk/llm082025-vector-and-embedding-weaknesses/), [LLM04:2025](https://genai.owasp.org/llmrisk/llm042025-data-and-model-poisoning/) | Primary | "text extraction tools that ignore formatting and detect hidden content". "Track data origins and transformations". 2025 IDs: the 2026 edition renumbered these (see the manifest's crosswalk). |
| [Unit 42, Fooling AI Agents](https://unit42.paloaltonetworks.com/ai-agent-prompt-injection/) (3 Mar 2026) | Primary (vendor telemetry) | Visible plaintext 37.8%, HTML attribute cloaking 19.8%, CSS rendering suppression 16.9%; lists zero-sizing, off-screen, transparency and camouflage. Basis for the hidden-text rules, and for not trusting them alone. |
| [PoisonedRAG](https://arxiv.org/abs/2402.07867), to appear USENIX Security 2025 per the authors | Primary (paper) | 90% attack success with five texts per question among millions; evaluated defences "insufficient". Why a person merges. |
| [Stale-Document Poisoning](https://arxiv.org/html/2609.31342v1), Ahmed et al. (25 Sep 2026) | Primary, preprint | Outdated retrieval flipped 30% of Llama and 37% of Qwen answers. Recency re-ranking helps "when dates are accurate", and incorrect dates can favour outdated evidence. Why crawl dates may never stand in for publisher dates. |
| [StaleBench v0.2.0](https://zenodo.org/records/20697152) (Jun 2026) | Primary, preprint | About half of answers stayed stale even with immediate re-indexing, attributed to document position in the context. A fresh corpus is necessary, not sufficient (G408). |
| [NuggetIndex](https://arxiv.org/abs/2604.27306), SIGIR '26 | Primary (paper) | "validity information is required at the fact level". The edition here is a document-level proxy (G408). |
| [draft-ietf-aipref-attach-05](https://www.ietf.org/archive/id/draft-ietf-aipref-attach-05.txt), [vocab-08](https://datatracker.ietf.org/doc/draft-ietf-aipref-vocab/) | Primary, **Internet-Drafts** | Vocabulary "DO NOT REFLECT CONSENSUS". Neither names RAG; reading it as `ai-use` is **our interpretation**. Honoured conservatively, not depended on. |
| [Web Bot Auth](https://datatracker.ietf.org/wg/webbotauth/documents/), [Google](https://developers.google.com/crawling/docs/crawlers-fetchers/web-bot-auth) (13 Oct 2026) | Primary | A working-group draft; Google calls it "experimental". Not adopted (G409). |
| [NITA-U National Information Security Policy 2026](https://www.nita.go.ug/sites/default/files/2026-07/Updated-National%20Information%20Security%20Framework%20(NISF).pdf) | Primary, read | s.5.2.4.3: "Protect AI models and training datasets from poisoning attacks", "adversarial testing", "human oversight". Mandatory for MDAs on its face, but **its version table still lists a draft**, so its in-force status is unconfirmed. |
| [Chambers, AI 2026: Uganda](https://practiceguides.chambers.com/practice-guides/artificial-intelligence-2026/uganda/trends-and-developments) (21 May 2026) | Secondary | No AI Act; DPPA applies; NITA-U standards bind through procurement. Not legal advice. |

**Could not be verified, and not relied on.** The NSA/CISA *AI Data Security* sheet and the
preprints.org provenance paper both answered 403, so only secondary summaries were seen.
MITRE ATLAS IDs come from secondary sources, which disagree on false-entry injection.
`ura.go.ug` refused connections from the review host, for a browser and a crawler identity
alike, so every statement about its pages comes from Wayback captures of 22 May to
31 October 2026, used **for structure only**, and the sitemap counts are from a capture of
5 October.

## Observed in production and on real data

Read on 2026-11-01. Space image `sha-5017060`, `/health` and `/ready` healthy.

| Observation | Evidence |
| --- | --- |
| Crawl coverage | 2,021 sitemap URLs: 1,756 never captured, 2 older than `lastmod`, 263 current. Posts 206 of 1,325, pages 33 of 298, downloads 26 of 398 |
| Recent changes missed | 379 URLs changed in 2026: 284 never captured. 90 changed since 1 July: 81 never captured |
| Notices missed | 11 of the 18 newest in the May 2026 feed, including "Mandatory update of taxpayers' registration details" |
| Stale PAYE page | `/en/domestic-taxes/paye-rates/` holds `0 to 235,000 Nil`, captured 2026-07-24, `lastmod` 2026-09-14. `FY2026-27.json` has 335,000 |
| Last crawl | 1,848 URLs queued, 756 fetched, 204 stored, cap 200: 1,092 never reached |
| Crawl commits built | 0 of the 6 most recent |
| Injection in stored pages | **None**: 0 flagged by `scan_retrieved_text` and `screen_tool_result` on 1,141 captures |
| Commencement date | The app's `effective_from: 2026-07-01` **matches** URA's notice |

**A hypothesis that did not survive.** The review expected most of the corpus to be archive
copies. A sample of 25 pages stamped 2026-10-31, compared against their oldest and newest
archive captures, did not support that: 17 matched both and so prove nothing, 5 matched only
the newest, 2 matched neither, and 1 matched only the oldest. That one
(`/en/bwg_gallery/talk-show/`) was last archived in September 2024. The mechanism is proven,
its scale is not, because the crawler never recorded where a page came from.

## Findings and phases

Severity is the consequence for a taxpayer, not the difficulty of the fix.

### Phase 0: stop the line

| Gap | Severity | Evidence before the fix | Fix |
| --- | --- | --- | --- |
| G377 | Critical | An empty timestamp built `/web/id_/<url>`, which redirects to archive.org's home page, and the redirect was unchecked. That page was stored as `https://ura.go.ug/en/tax-types` and selected for the index. Two more Wayback pages were committed as `.pdf`, and PyMuPDF opens HTML: 10,086 characters of fundraising text extract under the names of URA's employment income and education guides. | Every redirect hop checked against an exact-host https allowlist. The four files deleted. The exporter refuses non-PDF bytes on any route. |
| G378 | Critical | `collapse=urlkey` keeps the **oldest** capture. A 404 fell through to it and it was stamped with today's date, so a withdrawn page returned looking new. | The crawler never fetches an archive. Discovery comes from the publisher. |
| G379 | Critical | 13% coverage, from three causes. `content\|main\|article` matched Bootstrap's `justify-content-between`, so any page with no `<article>` kept a 45-character header bar: the VAT page extracted 0 characters. `_, pdfs = _discover_links` discarded page links, so the docstring's "deep-link BFS" did not exist. The sitemaps were never read. | Named containers then text density. Yoast sitemaps drive discovery, with link following from hubs. VAT page: 2,029 characters. |
| G380 | Critical | Web text was pushed straight to `dev`, breaking the repo's own invariant. `GITHUB_TOKEN` starts no workflow, so 0 of 6 commits built, and the data shipped with the next unrelated PR. The step summary claimed otherwise. | A pull request a person merges. That merge builds and rolls. |
| G381 | High | `respect_robots=True` was read by nothing. | RFC 9309 implemented and applied, including complete disallow on an unreachable file. |
| G382 | High | Five rotated browser User-Agents in the crawler and four more in the backend fetchers, while a truthful constant sat unused. | One honest identity, held equal across `ml` and `app` by a test. |

### Phase 1: update what is stale, never replace what is current

| Gap | Severity | Evidence before the fix | Fix |
| --- | --- | --- | --- |
| G383 | High | No model of staleness. Each run re-read the same prefix of a fixed queue, unchanged pages included, while the PAYE page URA had revised sat unread. | A URL is read when `lastmod` moved, it left the sitemap, it was never verified, or on the monthly pass after 30 days. Otherwise it is not requested. Weekday `changes` tier. |
| G384 | High | "Newest" meant processing time. A restored backup, a stale cache or an older reissue would simply have replaced what was held. | Publisher dates compared before replacing; the held capture stands and is not marked verified. PDFs compare their stated dates. Many older responses hold the run. |
| G385 | High | Crawled pages had no edition, so they skipped the dedupe, demotion and prompt rule protecting everything else. | A page carries the fiscal year of its last revision. Among near-identical passages the newer wins regardless of rank. |
| G386 | High | `effective_date` fell back to `crawled_at`, so the sources panel dated old pages with the last crawl. The model saw no date at all. | Never a crawl date. `published_at` reaches the citation, the panel and the passage header, validated, because that header sits outside the spotlight. |
| G387 | High | The floor ran before the newest capture was chosen, so a page cut to a stub fell back to older text (2 URLs). Tombstones matched `source`, a content hash: they lapsed at the next crawl and never matched an address. | The ledger names the current capture, then the floor applies. Withdrawal is confirmed by a second read. Tombstones match the URL. |
| G388 | High | `if not dest.exists()` kept old bytes while the state recorded new hashes, on all 5 filenames that ever changed. 14 recorded hashes were error pages. | Magic check, replacement with the regression guard, hashes on disk respected. |
| G389 | High | Reproduced: a revised statute was reported `verified_existing` after **0 requests**, and a different statute of equal size was filed as a copy of another. | Name and length must both match. SHA-256 alone decides identity. |
| G390 | Medium | "Download 11108" gave one form 18 captures. Author archives were 27% of captures. Unchanged pages were rewritten, so 102 files changed per crawl. URA's ", 1 week ago" would have marked every post changed. | Volatile lines ignored, author and media URLs skipped, content-addressed captures. |

### Phase 2: what enters the corpus

| Gap | Severity | Evidence before the fix | Fix |
| --- | --- | --- | --- |
| G391 | High | Nothing removed hidden elements, and table text was read before any stripping. | Inline concealment removed and sampled for the gate. Comments and CDATA excluded. `collapse` is kept, because URA files guidance in accordions. |
| G392 | High | Injection was only checked at prompt assembly, so a poisoned page entered the index and relied on a regex forever. | Quarantined at selection, including for hidden instructions: a page carrying one is untrustworthy as a whole. |
| G393 | High | Merged cells were dropped: 70 of 143 tables have padded cells, 46 with figures, and URA's PAYE table opens with a `rowspan`. Tables moved to the end of the page, under the wrong heading. A later row could become the header. | Grid expansion, tables in reading order, pipes escaped. |
| G394 | Medium | 683 TINs on the licensed-agents page and a personal webmail address were indexed. Applying `redact_pii_text` would have erased `0772140000`, URA's helpline, 19 times. | `kinds=` on the redactor. TINs, NINs and personal mailboxes only. On the real corpus: 0 statutory figures lost, helpline intact. |
| G395 | Medium | Lines under 8 characters were dropped, including a bare `18%`. Text directly in a `div` was lost. A 50,000-character cut was silent, with the hash covering text not stored. Charset sniffing read UTF-8 as MacRoman. | Figures kept, block-aware extraction, cut flagged, explicit decoding. |

### Phase 3: release

| Gap | Severity | Evidence before the fix | Fix |
| --- | --- | --- | --- |
| G396 | High | No check stood between a defaced site and the corpus. | `app.crawl_gate`: `pass`, `review` or `hold`, with the figures that moved. Mass change, removal, rollback, planted text or an unanswering site holds the run. |
| G397 | High | The job parsing hostile HTML held the write token, on an unpinned `pip install`. | Read-only job on a hash lock. The token job executes nothing from it, checks names and refuses symlinks. Enforced by policy tests. |
| G398 | Medium | Crawled PDFs skipped the guards the harvester applied. | The gate inspects and scans new PDFs, and says when the scan was skipped. |
| G399 | Medium | The harvester's page fetch and publications ingest followed redirects freely. | Hop validation on both, and a byte cap on publications. |

### Phase 4: reliability and evidence

| Gap | Severity | Evidence before the fix | Fix |
| --- | --- | --- | --- |
| G400 | High | `index-freshness.yml` compared files no checkout contains and passed on "missing". Publications ingest skipped and reported success. `/v1/index/freshness` proved only that an image matched itself. | The nightly job fails on an old crawl or a stalled proposal. `crawl` block on the route, `ura_crawl_*` metrics, three alerts, promtool cases. |
| G401 | High | State saved only at the end, 12 concurrent requests, unbounded bodies, swallowed exceptions, `direct_works = status > 0`, cleartext `http://…:80` fetches. | Checkpoints, one request per host with spacing, Retry-After, a breaker, budgets, a mid-stream cap, https only. A crash is `interrupted`, never complete. |
| G402 | Medium | The offline fallback invented `https://ura.go.ug/en/faq/<topic>` URLs. None were in the data. | Removed. A crawl that cannot reach the site says so. |
| G403 | Medium | `crawler.py` had no tests and was excluded from coverage. | Tests added and the exclusion removed. |
| G404 | Medium | The training loader used every capture ever made, superseded rates included. | Current captures only, honouring `train-ai=n`. |
| G405 | Low | Crawl hosts and corpus hosts were separate lists, and `uramedia.ura.go.ug` was in one only. | Held together by a test. |

## Design decisions worth challenging

* **A person merges every crawl.** On a quiet day nothing is opened, but the corpus cannot
  move without someone. That is deliberate while merging deploys.
* **No archive at all.** If `ura.go.ug` is down for a month the corpus simply ages, and an
  alert says so. Stale and honest was preferred to fresh-looking and unverifiable.
* **No date means no evidence.** An undated page can still replace a dated one, through
  review. The alternative freezes every undated page.
* **The held page is not re-verified** when the site serves something older, so it ages
  toward expiry and forces a decision rather than standing forever.
* **Hidden instructions condemn the whole page**, though the crawler had already removed
  them. An instruction on a government page says the page was tampered with.
* **Rate tables are never updated by a crawl.** A page and a table can disagree, and the
  report shows it, but changing a statutory figure stays a reviewed code change.

## Residual risk

| Gap | Open item | Owner |
| --- | --- | --- |
| G406 | **Nothing here has run against the live site.** `ura.go.ug` refuses the review host, and every test uses scripted responses or archived markup. The honest identity could be blocked in a way the old browser strings were not. Run the [acceptance probes](../runbooks/crawl-operations.md#acceptance-probes). | Backend |
| G407 | `dev` has no branch protection, so the review this design depends on can be bypassed by a direct push. | Repository owner |
| G408 | Validity is document-level. A page revised for one reason keeps a stale figure with a new date, and no check compares crawled figures with the rate tables. StaleBench also suggests a fresh corpus does not guarantee a fresh answer. | Backend, tax policy |
| G409 | Web Bot Auth not adopted, and AIPREF handling rests on drafts and our reading of `ai-use`. Revisit when they stabilise. | Backend |
| G410 | Text hidden by a stylesheet class is not detected, and a change written to read as ordinary guidance passes every content check by design. | Security |
| G411 | Whether company TINs on URA's public list should be held, and whether a DPIA covers crawled personal data, need the DPO. The NISP's in-force status needs confirming. | DPO |
| G412 | The first crawl grows the corpus from about 220 pages toward 2,000 and includes procurement notices. Ranking may move. The ranking gate runs on that PR, but no answer-quality baseline exists for a corpus that size. | ML |
| G331 | Live corpus sync would bypass this gate. It stays off in production and should not be approved until it writes proposals. | Corpus owner |

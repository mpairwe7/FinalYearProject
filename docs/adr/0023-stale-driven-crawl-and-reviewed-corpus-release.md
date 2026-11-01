# ADR-0023: Stale-driven crawl, no regressions and a reviewed corpus release

## Status

Accepted for implementation, 2026-11-01; production acceptance pending the first full
crawl from a GitHub runner. Extends
[ADR-0009](0009-statutory-and-10-mda-pdf-harvester-and-qdrant-indexing.md) and
[ADR-0019](0019-retrieval-answer-integrity-cache-generations-and-in-force-editions.md).
Does not close G331, the live corpus-sync path, which stays off in production.

## Context and Problem Statement

The bot answers taxpayers from text a crawler took from a government website, so that
crawler is a write path into shared agent memory. The
[assurance review](../plans/agentic-crawl-assurance-2026-11-01.md) found it failing in
ways that all its checks reported as success:

* It had captured 265 of the 2,021 content URLs the site lists. 81 of 90 URLs changed
  since July had never been read, including URA's VAT, withholding, rental and stamp
  duty pages and the notice giving the commencement date of the 2026 amendment Acts.
* Its copy of the PAYE rates page still showed the superseded `235,000` band, and the
  sources panel dated it with the crawl, so it looked current.
* archive.org's home page was stored as `https://ura.go.ug/en/tax-types` and selected
  for the index.
* A failed fetch fell back to the *oldest* archived copy of a page and stamped today's
  date on it, so a withdrawn page could come back looking new.
* Every run pushed unreviewed web text straight to `dev`. The push used `GITHUB_TOKEN`,
  so no build ran, and the text shipped with whichever unrelated PR merged next.
* The nightly freshness job could not fail.

The owner added the requirement that sets the direction of this decision: **the crawl
should update when information becomes stale and outdated, and must not replace data
that is up to date.**

## Decision Drivers

* A crawl acts because something became stale, and leaves current data alone.
* Older never replaces newer. A valid HTTP 200 proves nothing about version order.
* Provenance is the base; content checks are detection. PoisonedRAG showed five planted
  texts per question reach 90% attack success and that common defences fall short.
* Retrieval's handling of stale evidence is only as good as its dates ("Stale-Document
  Poisoning", 2026), so a date must be the publisher's and never a guess.
* Writes to shared memory are validated before commit, sources are authenticated,
  unverified entries expire, and high-risk changes get human review (OWASP ASI06).
* A control that cannot fail is not a control.

## Considered Options

* **Keep the Wayback fallback, labelled.** Rejected. An archive copy cannot be told from
  current law, and a labelled copy still answers a taxpayer. If the site is down the
  corpus should keep its last verified text and age visibly. CDX discovery went too:
  URA's sitemaps are complete, carry `lastmod`, and need no third party.
* **Re-read everything on every pass.** Rejected at the owner's direction, and on the
  merits: it re-reads what is current, misses what changed between passes, and loads a
  government server for nothing. `lastmod` is free and says what changed.
* **Decide "newer" by capture time.** Rejected: that was the defect. A capture time says
  when we looked. Content hashes say two versions differ, not which is newer.
* **Refuse any change without a date.** Rejected. It would freeze every undated page.
  No date is no evidence, so the change goes to a reviewer.
* **A new recency score in the reranker.** Rejected for now. It needs an evaluation this
  corpus does not have yet, and a mechanism already exists: giving crawled pages a
  fiscal-year edition brings them under the dedupe, demotion, prompt rule and header
  that handbooks already pass through.
* **Auto-merge clean crawls.** Rejected. Merging to `dev` deploys, and PoisonedRAG-style
  text passes content checks by design. One click on a report is an acceptable price.
* **Apply `redact_pii_text` to crawled pages.** Rejected after measuring: it removes
  `0772140000`, URA's WhatsApp helpline, 19 times. Redaction is narrow instead.
* **Sign requests with Web Bot Auth, require AIPREF.** Deferred. Web Bot Auth is a
  working-group draft that Google calls experimental (checked 2026-11-01), and AIPREF's
  vocabulary states it does not reflect consensus. A `Content-Usage: ai-use=n` is
  honoured anyway, as the conservative reading, and neither is depended on.

## Decision Outcome

**The crawler reads the live publisher or nothing.** Exact-host https allowlist, checked
on every redirect hop. One honest identity. No archive is ever requested. RFC 9309 is
applied, including complete disallow when robots.txt is unreachable, with one recorded
deviation: on URA's hosts robots.txt is read at the end of a full pass, because
requesting it gets the caller's address dropped for at least six minutes. That was
measured from GitHub runners, and it is not specific to this identity. The file does
not exist there, the server answers 200 with its HTML error page, and real rules found
at the end of a pass bind every later run. So a cached copy can outlive the RFC's
24-hour SHOULD. The fix belongs to URA ICT (G414).

**Crawling is stale-driven.** A URL is read when the sitemap says it changed, when the
publisher stopped listing it, when it has never been verified, or on the monthly pass
when its verification is 30 days old. Otherwise it is not requested. A page read and
found unchanged writes nothing. Weekdays run `changes`; the last day of the month runs
`full`.

**Older never replaces newer.** If the publisher's date on a response is earlier than
the same kind of date held and the text differs, the held capture stands, unverified.
PDFs compare their stated dates, in the crawler and the harvester. Many such responses hold the run,
because that is what a restored backup looks like. A name match no longer counts as
verification, and a size match no longer counts as identity.

**Dates are the publisher's.** `published_at` and `modified_at` come from the page.
`crawled_at` is never offered as an effective date. A page carries the fiscal year of
its last revision as its edition, so an older page cannot displace a newer statement:
among near-identical passages the newer edition wins, and the model sees each date.

**A ledger is the source of truth.** `Data/crawl/crawl_state.json` holds, per URL, the
current capture, status, verification and publisher dates, and a record of each run.
Selection follows it, so a page that reverted is on the right capture, a shortened page
does not fall back to older text, and a withdrawn page leaves. A page unverified for 45
days expires, measured from the last complete crawl so that an outage cannot empty the
corpus. Tombstones match a crawled page by address.

**Content is screened before it is written.** Hidden markup is stripped and kept as
evidence. A page holding an instruction aimed at a model is quarantined, not scrubbed.
Tables keep their columns through merged cells and stay under their headings.

**A gate decides, and a person releases.** `app.crawl_gate` returns `pass`, `review` or
`hold`, showing which statutory figures moved. It holds on a site that did not answer, a
mass rewrite or removal, planted instructions across pages, or pages going backwards.
The read-only job parses the web from a hash-locked install; the job with the token
executes nothing from it and can only open a pull request.

**Freshness means freshness.** `/v1/index/freshness` reports the corpus's age, metrics
and alerts follow it, and the nightly job fails on an old crawl or a stalled proposal.

### Consequences

* Good: a stale statement is now visible to the reviewer, the model and the taxpayer.
* Good: the crawl's load on URA's server falls to what actually changed.
* Bad: someone must merge a pull request for the corpus to move. That is the point, and
  `UraCrawlCorpusStale` is what tells them if it stops happening.
* Bad: a site restored from backup leaves us holding text the site no longer shows, and
  a page edited with no date change gets through to review rather than being stopped.
* Bad: without the archive, a site outage means no updates at all. Accepted.
* Bad: each full pass trips URA's robots rule once, on a runner's address, and that may
  show in URA's monitoring. The identity in the request says who it was and links here.
* Neutral: the first crawl rewrites every capture and grows the corpus from about 220
  pages toward 2,000, which is a retrieval change. It lands as a reviewed pull request
  with the ranking gate in CI, with `baseline` waiving only the mass-change limits.

### Confirmation

`tests/test_crawler.py`, `tests/test_crawl_policy.py`, `tests/test_crawl_gate.py`,
`App/backend/tests/test_crawl_corpus.py`, the crawl cases in
`App/backend/tests/test_retrieval_temporal_and_tombstones.py`,
`App/backend/tests/test_pdf_corpus.py` and `tests/test_ci_supply_chain_policy.py`.
Production acceptance: the probes in the
[crawl runbook](../runbooks/crawl-operations.md#acceptance-probes).

## More Information

Open items and the verified reference basis are in the
[assurance review](../plans/agentic-crawl-assurance-2026-11-01.md#residual-risk).

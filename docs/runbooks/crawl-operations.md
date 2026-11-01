# Crawl operations

How the crawled part of the corpus stays current, and what to do when it stops.
Decision record: [ADR-0023](../adr/0023-stale-driven-crawl-and-reviewed-corpus-release.md).
Findings behind it: [assurance review](../plans/agentic-crawl-assurance-2026-11-01.md).

The crawler identifies itself as `URA-Taxpayer-Assistant-Crawler/2.0`, with a link to
this page. It reads only `ura.go.ug`, `www.ura.go.ug`, `uramedia.ura.go.ug` and
`parliament.go.ug` over https, and it never reads an archive.

## What runs and when

| When | Tier | What it reads | What it publishes |
| --- | --- | --- | --- |
| Weekdays, 04:30 UTC | `changes` | Sitemaps and notice listings, then only pages that changed, are new, or left the sitemap | A pull request, only if the corpus would differ |
| Last day of the month, 04:00 UTC | `full` | The same, plus anything not verified for 30 days | Always a pull request, because the ledger proves what was checked |
| Nightly, 02:15 UTC | `Index freshness` | The ledger on `dev` and any open proposal | Fails if the last full crawl is over 38 days old, or a proposal has waited over 7 |

Nothing reaches `dev` without a person. **Merging a crawl proposal is a production
deploy**: the merge starts the image build, and a green build rolls the Space.

## What counts as stale

A page is read when one of these is true:

| Reason | Meaning |
| --- | --- |
| `changed` | The sitemap's `lastmod` is newer than our last verification |
| `delisted` | The publisher stopped listing a page it used to list |
| `unseen` | It has never been verified live |
| `due` | Full pass only: not verified for 30 days (`reverify_days`) |

Anything else is left alone and counted as `fresh_skipped`. A page that is read and
turns out unchanged writes no new capture.

**Older never replaces newer.** If the publisher's own date on what came back is
earlier than the date on what we hold, the held capture stands, the response counts as
`older_than_held`, and the page is **not** marked verified. Like is compared with like:
modified date with modified date, otherwise published with published, because URA does
not always emit a modified date. PDFs compare the dates they state about themselves. With
nothing comparable there is no evidence of age, so the change goes to the reviewer like
any other.

A page leaves the corpus when it is gone, moved, disallowed, marked `noindex`, opted out
of AI use, emptied, quarantined, or unverified for 45 days
(`CRAWL_MAX_UNVERIFIED_DAYS`, measured from the last **complete** crawl, so a crawler
outage ages the corpus visibly rather than emptying it).

## Reading a proposal

The pull request body is the gate's report. Read it in this order:

1. **Verdict.** `pass` means nothing needs weighing. `review` means the sections below
   need a reader.
2. **Statutory figures changed.** Check each against the Act or
   `App/backend/app/tax/data/`. If a page and the rate table disagree, the rate table
   needs review first, because the calculators answer from it.
3. **Kept the newer capture.** The site served an older version. Find out why before
   merging.
4. **Text changed with no new publisher date.** Ordinary edits move `lastmod`. This one
   did not, so read it.
5. **Withdrawn or excluded.** A taxpayer loses this page. Confirm that was intended.
6. **Quarantined.** See [A page was quarantined](#a-page-was-quarantined).

A pull request opened by `GITHUB_TOKEN` starts its checks in an approval-required state.
Press **Approve workflows to run**, wait for CI, then merge. The rate tables are not
updated by a crawl, ever: a real rate change is a code change with its own review.

## First run after ADR-0023

The ledger starts empty, so the first full pass reads everything, about 2,000 requests
and an hour. Every page gets a new capture, because extraction changed.

```bash
# Dry run: crawl and judge, publish nothing. Read the job summary.
gh workflow run scheduled-crawl.yml -f tier=full -f baseline=true -f publish=false

# Then for real. `baseline` waives the mass-change limits and nothing else.
gh workflow run scheduled-crawl.yml -f tier=full -f baseline=true -f publish=true
```

Check before merging that `fetches` is near the sitemap size, `ok` dominates the
outcomes, and `stopped_by` is `frontier_exhausted`. The corpus grows a lot on this
merge, so read the **Retrieval ranking gate** in CI closely and compare a sample of
pages by eye. Until this merges, `Index freshness` is red and `UraCrawlCorpusStale`
fires, which is accurate: no complete crawl is on record.

## The crawl was held

The job fails with `The crawl gate held this run`, nothing is proposed, and the report
is in the job summary and the `crawl-candidate` artifact for 14 days.

| Held because | Likely cause | Do this |
| --- | --- | --- |
| Few requests answered | Site down, or blocking the runner | Check the site, then [URA blocks the crawler](#ura-blocks-the-crawler) |
| robots.txt unreachable | Server errors on that host | RFC 9309 forbids crawling until it answers. Wait for the next run |
| Many pages would leave | URL restructure, outage, or an unwanted mass unpublish | Open a sample of the listed URLs in a browser |
| Many pages changed | Defacement, or an extraction regression | Compare a page with its capture. Compromise: call URA ICT **and do not re-run with `baseline`** |
| Pages came back older | The site was restored from a backup | Keep what we hold, and ask URA when the site will be current |
| Several pages hold instructions | The site may be compromised | [A page was quarantined](#a-page-was-quarantined) |

Thresholds are `CRAWL_GATE_MAX_REMOVED_SHARE`, `CRAWL_GATE_MAX_CHANGED_SHARE`,
`CRAWL_GATE_MAX_QUARANTINED`, `CRAWL_GATE_MAX_OLDER_RESPONSES` and
`CRAWL_GATE_MIN_ANSWERED_SHARE`. Raise one only for a reason you can write down.
`baseline` never waives planted content or a site that did not answer.

## The corpus is stale

`UraCrawlCorpusStale`: production has not been checked against the site for over 38 days.

1. `gh pr list --head data/crawl-candidate`. An open proposal is the usual cause: review
   and merge it.
2. `gh run list --workflow=scheduled-crawl.yml --limit 5`. A red run means
   [the crawl was held](#the-crawl-was-held) or failed.
3. If `dev` has a recent crawl and production does not, the image was not rebuilt or the
   roll failed. Compare `crawl.last_full_run_at` from `GET /v1/index/freshness` with
   `Data/crawl/crawl_state.json`.

`ok: true` on that route only says the index matches the image. `crawl` says how old the
image's corpus is.

## Pages are unverified

`UraCrawlPagesUnverified`: a complete crawl ran, yet some served pages have no live
verification. They were captured before ADR-0023, or carry `source_kind` other than
`live`. They should have been re-read, or expired after 45 days. Run a full crawl, and
if the count stays above zero read `excluded` in the manifest's `crawl` block.

## A page was quarantined

`UraCrawlPagesQuarantined`, or a Quarantined section in a proposal. A page held an
instruction aimed at a model, in its text or in markup a reader cannot see. It is out of
the corpus, and taxpayers are not affected.

An instruction on a government page is evidence of tampering, not a data quality issue.

1. Open the page in a browser and view its source. Do not paste it into an assistant.
2. One page with an innocent explanation, such as an article quoting an attack: leave it
   out, or add a tombstone. There is no allowlist on purpose.
3. Anything else, or several pages: treat it as a possible compromise of `ura.go.ug` and
   tell URA ICT Security. Do not merge any proposal until it is explained.

## The site served an older version

`Kept the newer capture` in a proposal. One or two pages usually mean a stale cache, and
the next run settles it. The held copy stays but is not re-verified, so if the older
version persists the page expires after 45 days. That is deliberate: it forces someone
to decide. If URA did reissue the older text on purpose, tombstone the held page and let
the next crawl capture the new one.

## URA blocks the crawler

`ura.go.ug` refuses connections from some networks: this was true of the host this
review ran on, for browser and crawler identities alike. GitHub's runners were reaching
it on 2026-10-31.

If runners get blocked, do **not** add a browser User-Agent or route through an archive.
Both were removed deliberately. Ask URA ICT to allow `URA-Taxpayer-Assistant-Crawler`, or
to expose the sitemaps and pages to it. Until then the corpus ages, the alert stays on,
and the bot keeps serving the last verified copy with its date.

## Withdraw a page now

To pull a page before the next crawl, add a tombstone on its **address**, as an admin:

```bash
curl -X POST "$API/v1/admin/tombstones" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_uri": "https://ura.go.ug/en/the-page/", "reason": "Withdrawn by URA notice of ..."}'
```

A tombstone on the address survives the page changing. One on a `*.json` source name
would lapse at the next crawl. It needs `knowledge_discrepancy_reporting` on.

## Acceptance probes

Production acceptance needs these, asserted from the run record and the served API:

| Check | Pass |
| --- | --- |
| Runner reaches the site with the honest identity | `outcomes.ok` dominates; `robots` is not `unreachable` |
| Discovery works | `sitemap_urls` is around 2,000 |
| Coverage | The run's `ledger_status.live` is near `sitemap_urls` |
| Stale-driven | A second run the next day has `fresh_skipped` near the total and few fetches |
| Dates reach taxpayers | A cited page shows `Published` in the sources panel, never a crawl date |
| Freshness reports | `GET /v1/index/freshness` has a populated `crawl` block |

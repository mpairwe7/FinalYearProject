# URA rate tables

Fiscal rates are **data**, not code. Each fiscal year is one JSON file
in `backend/app/tax/data/`, named for the year it covers
(`FY2026-27.json`). Adding a year is a data file plus a test; no
calculator changes.

## Why it is built this way

Two failure modes drove the design, and both had already happened:

- **A frozen "current" year.** Rates lived in a module-level dict with
  `fiscal_year: str = "FY2025-26"` as every calculator's default, so on
  1 July 2026 the assistant kept quoting last year's PAYE bands with
  full confidence and no signal that anything was stale.
- **Hand-maintained cumulative constants.** Bands were stored as
  `(lower, upper, rate, tax_at_bottom_of_band)`. That last number has to
  be recomputed by hand every time a boundary moves, and a wrong one is
  invisible until a taxpayer in that band checks their payslip — this
  repo shipped one that was off by UGX 500. Bands are now pure marginal
  rates, integrated at calculation time.

## File format

```jsonc
{
  "fiscal_year": "FY2026-27",        // must equal the filename stem
  "effective_from": "2026-07-01",
  "effective_to": "2027-06-30",      // null only for the open-ended latest
  "status": "provisional",           // or "confirmed"
  "verification_note": "…",          // required when status is provisional
  "currency": "UGX",
  "rates": {
    "vat_standard": 0.18,
    "paye_bands_resident": [[0, 335000, 0.0], [335000, 410000, 0.10]]
  },
  "legal_basis": { "vat_standard": "VAT Act (Cap 349), s.78 …" },
  "notes":       { "paye_bands_resident": "…how the top band is modelled…" },
  "carried_forward": ["paye_bands_non_resident"],
  "sources": [{ "id": "…", "title": "…", "publisher": "…", "url": "…" }]
}
```

Bands are `[lower, upper_or_null, marginal_rate]`. They are validated at
load time and rejected if they do not start at 0, are not contiguous, or
do not end with an open-ended top band — a gap would silently mis-tax an
entire income range.

`carried_forward` lists keys copied from the previous year because the
amendment published no replacement. Any result that uses one says so.

## Status and strict mode

| status | meaning | behaviour |
|---|---|---|
| `confirmed` | reconciled against the primary legislative text | quoted plainly |
| `provisional` | compiled from professional-firm / press summaries ahead of that reconciliation | quoted **with a caveat in the reply**, and every tool result carries `verification_warning` |

Verification is **per figure, not per table**. An Act can settle the PAYE
bands while a levy in a *different* Act is still only press-reported, so a
table-wide flag would either overstate the second or understate the first.
List those keys in `unverified`:

```jsonc
"unverified": ["vat_registration_threshold_annual"]
```

A result that used one gets a `verification_warning` naming the key; a
result that used only verified figures gets none. That way the caveat
still means something — caveating everything trains users to ignore it.

`TAX_RATES_REQUIRE_CONFIRMED=true` makes provisional tables fail closed:
`get_table()` raises and the calculator returns `ok: false` rather than
quoting an unverified figure. It defaults to **true** when
`APP_ENV=production` and false otherwise, mirroring
`REQUIRE_FRESH_AUTHORITY` in `app/authority.py`.

There is deliberately **no fallback to the previous year**. Once
FY2026-27 is in force, answering with FY2025-26 numbers is wrong, not
safe — the choice is a caveated current answer or a refusal.

## Resolution

```python
from app.tax.tables import get_table, resolve_fiscal_year

get_table()                            # in force today
get_table(as_of=date(2026, 3, 1))      # FY2025-26
get_table("FY2025-26")                 # explicit
```

Tools take `fiscal_year` **or** `as_of`; omitting both resolves to
today. Dates outside every table clamp to the nearest one, and the
caller sees the mismatch via `effective_from` in `rate_basis`.

## FY2026-27 at a glance

Effective 1 July 2026, per the Income Tax / VAT (Amendment) Acts 2026:

Source: **Income Tax (Amendment) Act 2026** (Bill No. 6), s.20 substituting
Schedule 4 Part I, plus Parts X, XI, XIII and XVI. Assented 18 May 2026.

Schedule 4 Part I states **annual** chargeable income; the table stores the
monthly equivalents:

| Annual (the Act) | Monthly (stored) | Rate |
|---|---|---|
| ≤ 4,020,000 | ≤ 335,000 | nil |
| 4,020,000 – 4,920,000 | 335,000 – 410,000 | 20% |
| 4,920,000 – 5,820,000 | 410,000 – 485,000 | 25% (cumulative 180,000/yr at the lower bound) |
| 5,820,000 – 120,000,000 | 485,000 – 10,000,000 | 30% (cumulative 405,000/yr) |
| > 120,000,000 | > 10,000,000 | 30% + additional 10% |

The two cumulative figures appear verbatim in the Act and are asserted in
`test_tax_calculators.py` as a check on the annual→monthly conversion.

| Change | FY2025-26 | FY2026-27 |
|---|---|---|
| PAYE tax-free threshold (monthly) | 235,000 | **335,000** |
| Band above the threshold | 10% to 335,000, then 20% to 410,000 | **20% to 410,000** |
| New band | — | **25% on 410,000–485,000** |
| 30% band / +10% above 10m | retained | retained |
| VAT registration threshold | 150,000,000 | **300,000,000** *(unverified)* |
| WHT — public entertainers (s.135B) | — | **6%** |
| WHT — betting/gaming winnings (s.131) | — | **15%** |
| WHT — telecom / mobile-money commission (s.133) | — | **10%** |
| WHT — purchase of a non-business asset (s.130(3)) | — | **6%** |
| WHT — debenture interest to non-residents (s.82(5)) | — | **5%** |
| Environmental levy, used clothing | 15% of CIF | **30% of CIF** *(unverified)* |

Non-resident PAYE bands are **unchanged**: the Act substitutes only Part I,
which by its terms applies to resident individuals, so Part II continues to
apply. That is different from a carry-forward — it is current law.

The royalty *rate* was not amended; the Act only adds "software" to the s.2
definition of royalty. The 15% stored is the pre-existing Cap 338 rate and is
marked `unverified` until reconciled against s.83.

VAT (18%), corporation tax (30%), capital gains (30%) and rental tax
(12% individual / 30% company) are unchanged.

Non-resident PAYE bands are carried forward: the sources covering the
2026 amendment do not restate them, so they are flagged rather than
guessed.

`compare_tax_years` answers "what changed this year" straight from the
two tables.

## Adding a fiscal year

1. Copy the newest file to `FY<next>.json`, set the dates, and set
   `effective_to` on the year it supersedes.
2. Set `status` honestly. If it is `provisional`, write a
   `verification_note` saying what has not been reconciled yet.
3. Fill `legal_basis` and `sources` for every key you change. A key with
   no basis is a key nobody can defend to a taxpayer.
4. Omit a rate the year's law does not define. Omission is correct —
   lookups fail closed and name the years that do define it, which is
   far better than a plausible wrong number.
5. Add tests to `backend/tests/test_tax_calculators.py` pinning the new
   year's statutory figures, and update the table above.

Tests must always name a `fiscal_year`. A test that omits it starts
testing next year's law the morning a new table lands.

## Promoting provisional → confirmed

Reconcile each figure against the gazetted Act or URA's published rate
card, replace the secondary `sources` with those, delete
`verification_note`, and set `status` to `confirmed`. The caveat then
disappears from replies automatically.

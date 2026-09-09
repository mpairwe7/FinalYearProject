# Runbook — multilingual figure fidelity

Operator guide for the path a reply takes from English generation to a
taxpayer reading Luganda or Swahili: `app/mt.py`, `service.localize_reply`,
and the decoding parameters in `app/llm.py` that feed it.

Decisions recorded here reflect the code as of 2026-09-09. Where a decision
reversed an earlier one, the earlier one is stated too — the reversals are
the part that is easy to re-litigate by accident.

## What problem this solves

Machine translation is paraphrastic, and paraphrasing a number changes it. A
reply that said `UGX 235,000` comes back saying `UGX 253,000`. On a revenue
authority's assistant that is indistinguishable from the assistant inventing
a figure, and it is the one failure the service cannot ship.

The original guard, `mt.figures_survived`, compared the money amounts and
percentages on both sides and served the English text when they disagreed.
Correct, and expensive in a way that does not show up in a metric: a Luganda
speaker who asks about the VAT threshold gets an English wall of text back,
which is the outcome the multilingual work exists to prevent.

## Decision 1 — figures are masked before translation, not repaired after

**Current behaviour.** `mt.protect_figures` replaces every digit group with an
opaque sentinel (`#NMBRA#`, `#NMBRB#`, …) before the text reaches a
translator. `mt.restore_figures` puts the original digits back afterwards. A
translator that is never shown a digit cannot paraphrase one.

Only the digits are masked. `UGX` and `%` stay visible, because they are the
cue the target language needs to build the right construction — Luganda
renders a rate as *"ebitundu 18 ku buli kikumi"* and can only do that if it
can still see that 18 was a percentage.

Sentinel labels are letters, never digits (`A`…`Z`, `AA`, …). A numeric index
would be exposed to exactly the failure the sentinel exists to prevent.

**What this replaced, and why.** `heal_vernacular_figures` (PR #481, narrowed
in #482) took the opposite approach: let the translation come back wrong, then
re-insert the statutory figure. Repairing after the fact means guessing where
the number belonged, and CodeRabbit found the guess writing figures into
sentences that never had one. #482 narrowed it so it could not guess — and
that made it unreachable. `mt.figures()` pools percentages and amounts as
plain numbers, so a bare `18` already satisfies the guard; a figure therefore
only counted as missing once its digits were absent, while every insertion
path #482 left required those digits to be present. The two conditions are
mutually exclusive. Verified against all nine realistic translation outcomes:
the function changed nothing in any of them. Masking before the fact needs no
guess, so it has no equivalent failure.

**Do not reintroduce output repair.** If a figure is wrong in a vernacular
reply, the fix is upstream — masking, or the translation tier — never a
regex that edits the answer on its way out.

## Decision 2 — a tier that cannot carry sentinels is retried unprotected

`service.localize_reply` runs the protected pass first. If it comes back with
sentinel fragments still in the text, with a figure missing, collapsed, or
empty, the reply is translated again **without** masking, which is exactly the
behaviour that shipped before this change. Only when that also fails does the
taxpayer get English.

This is what makes the change safe to default on: protection can add
vernacular coverage and cannot remove it. The cost is one extra round trip,
and only on a path that was already failing.

Sentinel residue is never shipped. A leftover `#NMBRA#` in an answer is
visible garbage, so any residue fails the pass outright regardless of what the
figures say.

**Kill switch.** `MT_PROTECT_FIGURES=false` disables masking entirely. Reach
for it only to rule masking out while debugging a translation-quality report;
the automatic retry already covers a tier that mishandles sentinels.

## Decision 3 — decoding parameters reach different paths, and that is load-bearing

vLLM serves production; the local Transformers path is the CPU/no-GPU
fallback. They do not take the same sampling parameters, and the docs
previously implied they did.

| Parameter | Default | Reaches |
|---|---|---|
| `LLM_REPETITION_PENALTY` | 1.1 | both paths |
| `LLM_MIN_P` | 0.08 | **vLLM only** |
| `LLM_PRESENCE_PENALTY` | 0.05 | **vLLM only** |
| `LLM_NO_REPEAT_NGRAM_SIZE` | **0 (off)** | **Transformers only** |

`no_repeat_ngram_size` is not a vLLM `SamplingParams` field, so it never
reaches `Sunflower-14B-FP8` however it is set. `docs/MODEL_CARD.md` claimed it
as a property of the served model; that was wrong and is corrected. Its code
default, `.env.example` and the docs also disagreed three ways (6 / 0 / 6),
which is the `.env`-versus-Space-secret drift pattern that has caused silent
production differences before — an operator copying `.env.example` got one
value and the Space, carrying no such secret, got the code default.

It is now 0 everywhere on purpose. A hard block on every repeated 6-gram is
the wrong instrument for statutory text: a correct tax answer repeats phrases
like *"value added tax (VAT) registration threshold"* and repeats a citation
string verbatim, and banning that forces the model off a correct phrasing.
The graded penalties break loops without banning anything. Set it to 6 only
when debugging a Transformers-path loop the penalties did not catch.

Note also that at `LLM_TEMPERATURE=0.2` the distribution is already sharp
enough that `min_p=0.08` rarely binds. Loop-breaking on the served path is
carried mostly by the repetition and presence penalties. Raising `min_p` is
only meaningful alongside a higher temperature.

## Verifying a change here

Masking is unit-tested and does not need a live model:

```bash
PYTHONPATH=App/backend python3 -m pytest \
  App/backend/tests/test_mt_cache.py \
  App/backend/tests/test_reply_localization.py -q
```

`FigureProtectionTest` covers the round trip, the sentinel-tolerance cases a
real translator produces (lowercased, spaced, hashes stripped, noun-class
prefix glued on), and the `#NMBRA#`-inside-`#NMBRAA#` boundary.
`ProtectedLocalizationTest` covers what the taxpayer receives: a
digit-transposing tier no longer costs the figure, an echoing tier never ships
a fragment, and a tier that drops sentinels still yields a vernacular answer
through the unprotected retry.

Against a live deployment, do not read success off the response body. The
translation chain has four tiers (`local` → `sunbird` → Gemini → CF Llama) and
a failure in one is invisible downstream — probe per locale and compare the
figures in the reply against the English one, rather than checking that a
reply came back at all.

## What the metrics mean

| Metric | Reading |
|---|---|
| `reply_localization_protected_retry_total{locale,reason}` | A *recovered* condition, not a served failure. A steady low rate is the mechanism working. |
| `reply_localization_figures_changed_total{locale}` | Both passes failed and the taxpayer got English. This is the correctness signal. |

`reason=sentinel_residue` climbing for one locale says that locale's MT tier
mangles sentinels and is now running every figure-bearing reply through two
round trips. That is a latency problem, not a correctness one, and the reason
to look at `REPLY_MT_BACKEND` for that locale — or, if it persists, to turn
masking off for that deployment.

Full metric definitions: `docs/MONITORING.md`.

## Known gaps

Tracked as **G56** in `docs/GAPS_AND_AGENTIC_ROADMAP.md`, not fixed here:

- `llm.extract_statutory_context` re-reads raw passage text after
  `scan_retrieved_text` has scrubbed and trimmed the same passages in
  `_build_messages`, so a poisoned or budget-trimmed passage's numbers can
  reach the prompt through a privileged `## Statutory Parameters` header
  without the LLM01 scrub.
- Those slots carry no entity label and no passage marker, so several rates in
  context arrive as an unattributed menu of numbers.
- The cross-lingual eval stemmer in `scripts/evaluate_1000_faqs_ngrok.py` was
  loosened in the same PR series that reports accuracy gains; those scores are
  not comparable to the pre-#478 baseline until it is re-run under the new
  scorer.

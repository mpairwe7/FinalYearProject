# Taxpayer education: teaching, not just answering

Answering a tax question and teaching tax are different jobs. The RAG
path already does the first one well. Doing only the first has a cost
that the education research is now explicit about.

## Why a separate tool

The 2025–26 literature on generative AI in learning converges on a
finding it calls **metacognitive laziness**: learners given finished
answers offload the thinking, and while their output improves, their
*knowledge gain and transfer do not*. Fan et al. found the assisted
group outperformed on the task while showing no significant difference
in knowledge gain — the work got better, the learner did not.

For URA's audience that is the wrong trade. A first-time filer, a small
trader, someone who will meet the same return next quarter — they need
to be able to do it without a chatbot in front of them. A system that is
correct every time and teaches nothing has moved the dependency, not
removed it.

The prescription in that literature is not *more* help. It is
**calibrated, fading scaffolding** that keeps the learner's own
retrieval and self-explanation engaged. That is what this tool
implements.

## The three mechanisms

### Fading

`level` controls how much of the work is done for the learner:

| Level | Worked example | Check question |
| --- | --- | --- |
| `beginner` | Every step worked out | Recognition-level, answer stored |
| `intermediate` | **Completion problem** — final step blank | Recognition-level, answer stored |
| `advanced` | None | Transfer question, no stored answer |

The `intermediate` completion problem is the classic faded worked
example: the learner has the scaffolding of the earlier steps and
performs the last one. The withheld value is **removed from the payload
entirely**, not flagged — a value present in the result is a value the
model reads out loud.

### Retrieval practice

`check_answer` is absent unless the caller passes `reveal_answer=true`.
A tool that always returns the answer cannot ask a question. Making the
withholding structural rather than advisory is the point: the model
cannot reveal what it was not given, so the learner gets a real attempt
before confirmation.

The result carries an `instruction` telling the caller to pose the
question and call again with `reveal_answer=true` once the learner has
tried, or if they ask outright.

### Misconception-first

Each concept names what learners actually get wrong. Correcting a wrong
model beats adding to an absent one, and in tax the wrong models are
well known and expensive:

- *"A raise pushed me into a higher bracket so I take home less."*
  `progressive_taxation` exists for this one. People decline raises,
  overtime and promotions over it.
- *"The client withheld tax from my invoice, so I lost that money."*
- *"VAT I collected is my turnover."*
- *"I can't pay this month, so I'll file late too."* — filing and paying
  are penalised separately.

## Figures come from the rate tables, never from this module

No worked example contains a written-in number. Each declares a
calculator and arguments, and the steps name fields in that calculator's
payload:

```python
Example(
    scenario="A shop sells goods for UGX 500,000 before VAT.",
    tool="calculate_vat",
    arguments={"amount": 500_000},
    steps=(
        Step("Price before VAT", "net"),
        Step("VAT rate applied", "rate", "rate"),
        Step("VAT to add", "vat"),
        Step("Price the customer pays", "gross"),
    ),
)
```

This is the same discipline as `tools/empathy.py` delegating to
`text_signals`: one definition, so a lesson cannot teach a rate the
calculators have stopped using. Three consequences follow for free:

- An explicit `fiscal_year` or `as_of` teaches from the table in force
  then, so a lesson about a past period uses that period's law.
- A provisional or unreconciled table's `verification_warning` is
  carried **into the lesson**. A caveat that appears on a calculation
  but not on the lesson explaining it would be the wrong way round.
- `rate_basis` gives the statutory citation for the figures shown.

A step whose payload field is missing means a calculator's output shape
moved. The example is dropped with a logged warning and the explanation
stands alone — rendering `—` where a figure belongs looks like a tax
answer and teaches nothing. `test_education.py` asserts this never
happens, so it fails loudly in CI rather than quietly in production.

## Curriculum

Twelve concepts with a prerequisite DAG. `learning_path` returns the
topological order ending at the requested topic, which answers "where do
I start?" without a second tool:

```
tin ─┬─ fiscal_year ── filing_deadlines
     ├─ progressive_taxation ── paye ── withholding_tax
     ├─ vat ─┬─ vat_registration
     │       └─ customs_duty
     ├─ rental_tax
     └─ corporation_tax ── capital_gains
```

`progressive_taxation` sits before `paye` deliberately. Teaching PAYE to
someone who thinks brackets replace rather than stack produces a
confident wrong model.

## Routing

The supervisor sends learning intents here. The hard part is that a
learning question and a calculation question share vocabulary — *"what
is VAT"* wants the concept, *"what is the VAT on 5 million"* wants the
arithmetic. The discriminator is what the user brought with them, and
every guard **defers**:

| Guard | Example | Goes to |
| --- | --- | --- |
| An amount is present | "What is the VAT on 5 million?" | Calculators |
| A rate word is present | "What's the applicable VAT rate?" | Rate table |
| A time word is present | "Tell me about the current fiscal year" | Calendar |
| Topic not in the curriculum | "What is EFRIS?" | RAG |

Because each guard hands the query back rather than claiming it, adding
this route could not change where any previously-routed query went —
asserted by `TestToolsRoute_Education::test_education_defers_rather_than_stealing`.

The whitelist is `["explain_tax_concept", "search_ura_knowledge_base"]`,
so retrieval stays available in the same turn for anything the
curriculum does not cover.

## Adding a concept

1. Append a `Concept` to `_CONCEPTS` in `app/tools/education.py`.
2. Prose describes *mechanisms* only — stable under rate changes. Any
   figure belongs in an `Example`, computed by a calculator.
3. `check` is a `(question, answer)` pair whose answer is conceptual, so
   it cannot go stale when a rate changes. Numeric retrieval practice is
   the `intermediate` completion problem, whose answer is computed live.
4. Add the topic to `_LEARN_TOPIC` in `agents/supervisor.py` if it needs
   its own routing vocabulary.
5. `test_education.py` covers the rest: every concept renders at every
   level, every example resolves against its calculator, prerequisites
   exist, and the graph is acyclic.

## References

- Fan et al., *Beware of metacognitive laziness: effects of generative
  artificial intelligence on learning motivation, processes, and
  performance*, British Journal of Educational Technology (2025).
  <https://doi.org/10.1111/bjet.13544>
- *Retrieval Interruption Framework: AI-Assisted Cognition and
  Retrieval-Dependent Learning in Higher Education*, Education Sciences
  (2026). <https://doi.org/10.3390/educsci16081179>

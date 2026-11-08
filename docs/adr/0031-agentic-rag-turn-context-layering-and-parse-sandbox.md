# ADR-0031: Agentic RAG rewriter, turn-context layering and the parse sandbox

## Status

Accepted — 2026-11-07

## Context and Problem Statement

A QA, architecture and audit pass over agentic RAG, module organisation, context management and
sandboxing on 2026-11-07 measured each area before changing it.

**Sandboxing.** The schedule sandbox (`tax/schedule_sandbox.py`) is a whitelisting AST interpreter with
no `eval`. Twenty hostile expressions, covering imports, dunders, nested powers and extreme literals,
were each refused, with nothing uncontained, so it is unchanged. The document parse worker is where
the gaps are:

| Gap | Measurement |
| --- | --- |
| The child's environment was scrubbed by a denylist (G478) | 18 secret-looking names in `.env.example` passed it, including `AUDIT_TAXPAYER_HMAC_SALT`, `DEPLOY_KEY`, `WEBHOOK_NOTIFY_URL`, and the Qdrant, vLLM and reasoner addresses (CWE-526) |
| No address-space cap by default (`DOCUMENT_PARSE_MAX_MEM_BYTES=0`) (G479) | Four concurrent parses, each unbounded, on a 16 GB host |

The deadline holds. An isolated parse of the 50 MB Stamp Duty Act returns in about 1 s, and a 1.5 GiB
flate bomb ends at the 30 s deadline.

**Agentic RAG.** The follow-up rewriter turns "and for exports?" into the query retrieval runs. A turn
that steers it therefore chooses what the answer is built from. A 2025 RAG poisoning benchmark
(arXiv:2505.18543) describes this rewrite-then-retrieve pattern as the surface history feeds.

| Gap | Measurement |
| --- | --- |
| History went into the prompt raw (G480) | No replay scrub, no fence, forgeable `History:`/`Follow-up:`/`Rewritten:` labels, and a statutory figure (UGX 150 million) in a few-shot example |
| Output acceptance (G481) | Any first line of three or more characters not starting with "i cannot", "sorry" or "here is" |
| Non-chat fallback (G482) | On a local HF backend the full RAG answerer was asked. Its abstention, "I don't have enough information…", would have become the search query |
| It never ran (G483) | `from . import flags` gave the module, which has no `is_enabled`. The `AttributeError` was swallowed by `except Exception: logger.debug` |

**Module organisation and context management.** `service.py` is 15,440 lines in a flat package of 112
modules. Its size is a long-term refactor, and moving files would break the HF Space build patch,
which edits files by path. The defect is in layering. Turn-scoped context (stated identifiers, case
facts, tenant, conversation) was ambient `ContextVar` state inside the orchestrator. The security layer
(`OutputGuard.redact_pii`), the evaluator and the empathy tool each imported `service` to read one
value, and the redaction import fell back silently on failure (G484). The agentic-RAG graph imports
eight private `service` helpers, so graph and orchestrator depend on each other, and nothing stopped
the next upward import (G485).

## Decision Outcome

1. **Allowlisted child environment (G478).** `document_worker.child_environment` keeps only locale and
   runtime plumbing, `FLAG_*`, and the settings the extractors read (`DOCUMENT_*`, `OCR_*`, `VISION_*`,
   `DOCLING_*`, `SANDBOX_*` and a few more). It then drops anything secret-shaped by name and any value
   carrying `user:password@`. CA-bundle paths and proxy settings without credentials pass, so an OCR
   service behind a private CA or a proxy still works. A test derives the extractors' `os.getenv`
   names (single- or double-quoted literals) from their source, so a setting named that way cannot be
   starved. One built from a variable is not seen.
2. **1 GiB address-space cap by default (G479).** Every corpus PDF up to 50 MB parsed identically under
   512 MiB, and a 1.5 GiB flate bomb was contained. The worker and the production gate read the setting
   the same way (`parse_mem_cap`). Production refuses zero, negative and unreadable values, and an
   unreadable value falls back to the default rather than failing the import.
3. **A fenced, scrubbed rewriter prompt (G480).** `REWRITE_SYSTEM` says the conversation and the
   follow-up are data. They sit in `<conversation>` and `<follow_up>`, each line spotlight-fenced and
   angle brackets escaped. A turn that carried an injection is left out whole, not scrubbed, because a
   scrubbed phrase leaves its payload. The examples carry no statutory figure, and a figure the taxpayer
   typed may still be carried over.
4. **Validated output (G481).** `accept_rewrite` takes one line of 3–300 characters, refuses refusals,
   answers (citations, links), anything injection-shaped or refused by `InputGuard`, and any identifier
   the taxpayer did not type.
5. **Chat backend only (G482), behind its own flag (G483).** The rewriter runs only on vLLM's chat
   API, never the full answerer. Its import bug is fixed, but it never ran, so turning it on is an
   unmeasured retrieval change. It stays behind `llm_query_rewrite`, default off, like `hyde` and
   `graph_fusion`, until measured on the multilingual retrieval benchmark.
6. **Turn context as a leaf (G484).** `app/turn_context.py` owns the four turn `ContextVar`s and
   imports nothing from the app. `service` re-exports the same objects under their old names, so
   callers and tests are unchanged. Guardrails, the evaluator and the empathy tool read from the leaf.
7. **Layering as a fitness function (G485).** An AST test (`test_agentic_rag_context_sandbox_2026_11.py`)
   holds three rules. Only the transports and composition roots may import `service`. The security,
   retrieval, query, context and tool modules never do. And the graph may take no `service` private
   beyond the eight it takes today, whose extraction is tracked as G486. Closed on 2026-11-08: the
   graph now imports nothing from `service` (amendment below).

### Review amendments (PR #703)

CodeRabbit's review produced 11 findings, and all were fixed:

- CA bundles and credential-free proxies pass to the child.
- The memory cap is parsed once, the same way in the worker and the production gate.
- Both stored forms of a replayed turn are checked for injection.
- A rewrite's identifiers must each be ones the taxpayer typed (`guardrails.pii_values`), not merely
  any identifier when the conversation holds one.
- The residual address exposure is stated here and in T66.
- Two test defects were fixed: a TIN assertion that checked a number not in the input, and a layering
  scan that skipped `memory/service.py` by name. The default-off flag test is isolated from
  environment and in-memory overrides.

### Amendment 2026-11-08: the graph's eight helpers extracted (G486)

The graph imported eight `service` privates. Their closure is 15 functions and constants, plus the
FAQ state they read. Its only other `service` dependency was the case-facts variable, which item 6
had already moved to the leaf. They now live in four modules, none of which imports `service` or
the graph:

| Module | Holds |
| --- | --- |
| `turn_context` | `TURN_GENERATION` and `TURN_LIVE_CHECK`, next to the other turn variables |
| `turn_record` | `note_generation`, `note_passage_served`, `endpoint_model` |
| `live_research_turn` | `live_research_for_turn`, `with_live_check_disclosure` |
| `english_rendering` | `answer_in_english` |
| `faq_binding` | the FAQ binding and promotion helpers, their constants, and `install_faq_acronyms` |

The move had three hazards, and each is handled:

- **Patch points.** Five tests stubbed `service._answer_in_english`, and `service` passed it to
  `asyncio.to_thread` as a function object. A re-export would have left the graph's copy
  unpatched. `service` and the graph now call `english_rendering.answer_in_english` through the
  module at call time. `service` no longer has the old name, so a stale patch fails loudly
  instead of silently missing.
- **Rebound state.** `install_faq_acronyms` rebinds `_FAQ_ACRONYM_RE` with `global`. Left in
  `service`, it would have rebound `service`'s copy while `faq_binding` read `None`, which the
  acronym tests caught. The writer moved with the state, and `service` does not re-export the
  rebound name.
- **Same objects.** Every other old name is re-exported from `service` as the same object, so
  the ContextVars, the shared acronym dictionary and the cached functions are one thing each.

The layering test now holds the end state:

- the graph does not import `service`;
- the extracted modules are lower layers and never import it;
- none of them imports the graph.

`service.py` is 14,809 lines. The HF Space build patches five lines in it, and none is in
the moved code.

**Review of the move (PR #705).** CodeRabbit read the moved code as new and found pre-existing
defects in what it decides. Five were fixed with tests, each of which fails when its fix is removed:

- **Alias chains.** `magari` named `vehicles`, itself an alias, so a Luganda or Swahili query and
  its English FAQ ended on different terms. Aliases now resolve to their final term in place, for
  every consumer.
- **Timing evidence.** "By the 15th of the following month" is a timing rule, but no
  timing-evidence word was in it. A deadline-shaped date or period now counts: "by", "before",
  "on or before" or "not later than" plus a date, "within N days", or "the 15th of the following
  month". A bare "each year" or month name does not, so a definition is not taken for a deadline
  (the re-review caught the first, looser version). "May" is not a month here, because it is
  usually the modal verb.
- **Question intent.** "When are capital gains taxed?" and "How are capital gains taxed?" had the
  same terms and were both promoted. Questions whose main interrogatives differ (when, how, how
  much, who, where, why) are no longer equivalent. A leading subordinate clause ("When filing
  returns, how is…") is context, not the question.
- **Multi-word aliases.** "nambari" named "number plate", and that phrase stayed one term while
  the English text gave two. Alias targets now split into words.
- **Self-reflection.** A revision replaced the English-checked draft without the check. It now
  gets the check before the guards read it.
- **Async dispatch.** `GraphRuntime.arun` ran synchronous nodes on the caller's event loop. They
  now run in a worker thread, which copies the context; no node sets a ContextVar.

The layering scanner also reads real `importlib.import_module` and `__import__` calls (not a
method of the same name), including the `name=`, `package=` and `level=` arguments, and catches
`from . import agents`.

One finding was answered with evidence: acronym folding when no local FAQ corpus exists. The
deployed Space installs 135 acronym expansions from the corpus it ships, and a deployment without
that corpus has no FAQ index for them to serve.

The FAQ promotion, match-gate, acronym, grounding and retrieval-regression suites pass (113 tests).

## Consequences

- Positive: a compromised parser sees no secret, no database, cache, vector store or model address, and no
  webhook, and cannot take the host's memory.
- Negative: the addresses the extractors themselves call stay visible to the child: `OCR_SERVICE_URL`,
  `TRITON_OCR_URL` and the vision settings, when credential-free, plus any proxy. A compromised parser
  can reach those services as the extractor would. Network isolation is the control for that (below).
- Positive: the agentic retrieval step cannot be steered by an earlier turn, and cannot retrieve with a
  refusal or an answer. When it is turned on, it will do what it was written to do.
- Positive: the lowest layer no longer depends on the highest, and new upward imports fail CI.
- Negative: an extractor that comes to need a new kind of setting must have its name added to the
  allowlist. The source-derived test fails until it is.
- Negative: the 1 GiB cap may be too tight for a future model-backed extractor in the child. Raise it
  with `DOCUMENT_PARSE_MAX_MEM_BYTES`. Production refuses only zero, negative and unreadable values.
- Negative: the child still shares the parent's network and filesystem view. Network and filesystem
  isolation (gVisor, a microVM, or a dedicated pool) remains the follow-up named in `production_readiness`.
- Neutral: `service.py` shrank from 15,433 to 14,809 lines when G486 extracted the graph's helpers.

## Confirmation

- `tests/agents/test_agentic_rag_context_sandbox_2026_11.py`, one section per gap. Each of the seven
  controls was disabled in turn and the suite failed with exit 1 every time. The G482 test was
  strengthened after the first teeth run showed the new flag was masking it.
- `tests/agents/test_november_2026_gap_closures.py` (the existing scrub test) and the journey-copilot
  suites that set the turn context pass unchanged.
- `App/backend/tests/test_flag_rollout.py::test_llm_query_rewrite_is_off_until_measured`.
- G486: `test_the_graph_does_not_import_the_orchestrator` and
  `test_the_extracted_modules_do_not_import_the_graph`. Re-adding a `service` import to the graph,
  or to an extracted module, fails them. Leaving the acronym writer in `service` fails
  `test_faq_acronyms.py`.

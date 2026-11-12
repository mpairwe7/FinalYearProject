# ADR-0046: A superseded call turn stops its model run at the next stage

## Status

Accepted, 2026-11-12. Phase 7 part B of the
[11 November call receptionist review](../plans/receptionist-assurance-2026-11-11.md);
it finishes G591, which [ADR-0040](0040-call-records-and-accountability.md)
mitigated. Approved by the service owner on 2026-11-12 as a short design in the
session that made it.

## Context and Problem Statement

A call turn can be superseded while its answer is being worked out:

- the caller talks over the assistant;
- the caller says or types a new question;
- the call moves to another language;
- the answer takes longer than `GENERATION_TIMEOUT_S` (25 s) and the call is
  handed over.

The brain stopped waiting for the answer. The worker thread did not stop.
`ChatModel.generate` is synchronous, so it went on through retrieval, the model
call, its continuation rounds, the cloud fallbacks and translation. Then the
brain threw the answer away.

ADR-0040 capped a call at two model runs at once and skipped a turn that was
stale when its slot came. A run that had already started still ran to the end.
That spent model quota and held one of the call's two slots. The threads it used
come from pools that every other call shares: the model pool (12 threads) and
the default pool that also runs speech recognition and synthesis.

There was nothing inside `generate` to stop it. The streaming chat path has a
`cancel_event`; `generate` takes none. The turn deadline is read at a few points,
but a superseded turn has not run out of time.

## Considered Options

1. **A cancel token in the context**, checked at stage boundaries, as the turn
   deadline is. No signature changes.
2. **A `cancel_event` parameter on `generate`**, as the streaming path has.
3. **Run `generate` in a child process** and kill it.
4. **Keep ADR-0040's cap only.**

## Decision Outcome

Option 1.

Option 2 changes a signature with six callers and every test double, and the
event would then have to be passed by hand to each helper of `_generate_en`. The
context already reaches them, the same way the turn deadline does. Option 3 costs
a process per turn and loses the caches and models loaded in the API process.
Option 4 leaves the waste.

How it works:

- **The token.** `resilience._TURN_CANCEL` is a context variable that holds
  anything with `is_set()`. `turn_cancelled()` is False when none is set. Only a
  call turn sets one, so on the chat path every check is a no-op.
  `raise_if_turn_cancelled(stage)` counts `turn_cancelled_total{stage}` and
  raises `TurnCancelled`.
- **`TurnCancelled` is a `BaseException`**, like `asyncio.CancelledError`. The
  pipeline has many `except Exception` fallbacks. They would otherwise catch it
  and go on to the next model, which is the work this is meant to stop.
- **The brain's token** (`brain._RunCancel`). One per model run, set into the
  context the run starts in, next to its slot. It is set when:
  - the run's task was cancelled (a done callback): a barge-in, a new turn, the
    25 s timeout;
  - the call's `generation_id` moved on. This covers the language router, which
    moves it without cancelling the task;
  - the call left `ai` mode: a transfer, a bridge or an end. The brain already
    threw such an answer away; now the run stops too.
- **Where it is checked** (the `stage` label):
  - `slot`: a step of a model run starting in its thread (`_GenerationSlot.run`).
    This also stops a Luganda answer between its steps.
  - `pipeline`: entry to `trace_stage`, so before every stage of `_generate_en`.
  - `generation`: before the local model call takes its breaker slot, when its
    pool thread starts, and before a reasoner falls back to T1.
  - `agentic`: before the tool loop. The loop's `CancelScope` also follows the
    token, so it stops before its next round or tool call.
  - `continuation`: before each continuation round of `complete_with_continuation`.
  - `cloud_fallback`: before the cloud chain, and before each hop (Sunbird, each
    Workers AI model).
  - `localize`: before translation in `_generate_localized`.
- **The breakers.** A run stopped before a model call takes no breaker slot. A run
  stopped inside the model call (its pool thread raised) gives the slot back with
  `release()`. Nothing was learned about the model, so it is neither a failure
  nor a success. The cloud chain checks before `admit`, so no provider breaker is
  touched.
- **The brain.** A `TurnCancelled` that comes out of the run is a stale turn. The
  turn's disposition is `cancelled`, as for an answer dropped after it arrived. It
  is not counted as a failed turn, nothing is said to the caller and no transfer
  starts. The slot is given back when the thread ends, as before.
- **Nothing is recorded as a completed turn.** Memory
  (`_persist_personalization_turn`), the semantic cache, the case record
  (`_record_turn_case`) and the deferred audit fields are written after the
  stages. A run that stops at a stage writes none of them.
- The unused `_Superseded` class in `brain.py` is removed.

## Consequences

- A superseded run stops at its next stage boundary instead of running to the end.
  It makes no further model call, continuation round, cloud hop or translation.
- **An HTTP request already in flight runs to its own timeout.** The checks are
  between steps, and the model clients use blocking `httpx`. The thread, and the
  call's slot, stay busy until that request returns. This limit is accepted.
- Work in a thread the pipeline starts without copying the context (the local
  dense retrieval leg, for one) does not see the token and runs to its end. The
  next stage boundary stops the run.
- A turn whose call moves to an officer (a transfer, a bridge or an end) stops
  at its next stage, because the token reads the call's mode. Its answer would
  have been dropped anyway (ADR-0040).
- A stopped run keeps what stages it finished had written, such as the turn's
  case facts. A run dropped after it completed kept all of it.
- **The chat path is unchanged.** It sets no token. `trace_stage` and the model
  chain each read one context variable. `CancelScope` now takes any number of
  parents; with one, it behaves as before.
- The turn's trace span records `TurnCancelled` as its error, which is what
  happened to it.
- New series `ura_turn_cancelled_total{stage}`, declared at zero for every stage
  by the call process (`receptionist/telemetry.declare`). It has no alert. A rise
  means callers interrupt, change language or wait past the timeout, not that
  something is broken. Nothing scrapes the Space (G614); there the call process
  logs `Turn superseded: its run stops at <stage>` at INFO.

## Confirmation

`App/backend/tests/test_turn_cancellation.py`. The tests of the token itself
first failed because the module did not import. Every other test, except the two
that check a turn still wanted runs as before, then failed against the old code
with only the token in place. The tests cover:

- **The token:** no token stops nothing; a set token stops the run and is counted;
  `TurnCancelled` is not an `Exception`; a thread started with the turn's context
  sees the token; `trace_stage` does not start a stage for a cancelled turn; a
  `CancelScope` follows the token; every stage is declared at zero.
- **The model chain:**
  - a continuation loop stops at its next round, and continues with no token;
  - a run cancelled while the model answers tries no cloud fallback, calls
    `release()` and not `record_failure()`;
  - a run cancelled before the model takes no breaker slot;
  - the cloud chain is not walked for a cancelled turn, and is with no token;
  - the agentic loop's scope follows the token, and its breaker slot is released.
- **`ChatModel.generate`, the real pipeline:** a turn still wanted reaches the
  model; a run cancelled before it starts makes no model call and records
  nothing; a run cancelled during retrieval stops before the model; a run
  cancelled before translation is not translated.
- **The brain:**
  - a turn nothing supersedes runs every stage;
  - a turn replaced by a new one stops at its next stage, is counted, frees its
    slot, and nobody hears an apology;
  - a language switch through the real router stops the run it made stale, and
    the turn is recorded `cancelled`;
  - a call moved to an officer mid-run stops the AI's run;
  - a turn that ran out of time stops its run;
  - a superseded Luganda turn does not start its next step.

Each part of the change was also reverted on its own, and a test failed each
time: the brain's handling of `TurnCancelled`; the done callback setting the
token; the token reading `generation_id`; the slot check; the `trace_stage`
check; `release()` on a cancel; the check before the local model call; the
agentic scope following the token; the cloud hop check; the translation check;
the continuation check; declaring the series.

`test_receptionist_records_accountability.py` and `test_receptionist_brain.py`
pass unchanged.

Not verified here: a live call on the Space. After the roll, interrupt an answer
and look for `Turn superseded: its run stops at` in the call process's log.

# ADR-0041: The call receptionist starts safely and fails visibly — a degraded start instead of a stopped API, a worker gate that reads what the image runs, and call failures that are counted

## Status

Accepted, 2026-11-11. Phase 3 of the
[11 November call receptionist review](../plans/receptionist-assurance-2026-11-11.md);
it follows [ADR-0040](0040-call-records-and-accountability.md). It closes G373
and G593–G598, mitigates G306, G599 and G600.

## Context and Problem Statement

Phases 1 and 2 were about a call that is running. This phase is about whether
calls can run at all, and who finds out when they cannot.

- **One optional feature could stop the API** (G373). With the receptionist
  flag on and Pipecat missing, `main.py` re-raised from startup. On 2026-10-31
  an image that lost its Pipecat layer took the chat down for eight hours.
- **The single-worker gate read a variable the image does not use** (G593).
  Call rooms, the staff lobby, claims and admission caps live in the process.
  The gate checked `WORKERS`; the supervisord image starts uvicorn from
  `UVICORN_WORKERS`, default 2. The production Space was seen running two
  workers with calls on: an officer's console and a caller's call reached
  different processes about half the time.
- **A call was admitted with nothing to hear or speak with** (G594). A missing
  speech model was allowed. Inside a call, a recognizer or voice that raised
  was logged and dropped: the caller heard "are you still there?" and was hung
  up on, and the record said `caller_idle`.
- **Nothing was counted** (G595). No call series existed, so no alert could.
- **One setting was validated** (G596). A transfer timeout of `0`, `nan` or
  `inf` passed the gate; `0` made every transfer "officers are busy".
- **A flag switched on at runtime skipped the engine checks** (G597).
- **CI never installed the receptionist stack** (G598). Every test that needs
  Pipecat was skipped, and the requirements file was in no dependency audit.
- **Rows outlived their process** (G306). A call row stays `ai`,
  `transferring` or `bridged` when its process dies; the console shows it live
  and its officer stays `on_call`.
- **Two server-only features promised what they could not do** (G599): a
  low-bandwidth mode that agreed to Opus and sent raw PCM, and resume tickets
  signed with the development secret.

## Decision Outcome

**A receptionist that cannot start is unavailable, not fatal.** Chosen by the
service owner over failing loud, for every environment.
`receptionist.start()` never raises. Whatever stops it (no Pipecat, an unusable
setting, call tables that cannot be created, no speech model, and in
production the G36 gate) is recorded, logged once at error, and exported as
`ura_receptionist_available = 0`. Call sockets are refused with the usual 1001
while it lasts, and `GET /ready` says why under
`capabilities.voice_receptionist` (`off`, `ready`, or `unavailable: <why>`).
The probe itself stays ready: the chat does not depend on calls.

The G36 gate (LiveKit over `wss://`, production credentials, one worker and one
replica, the acknowledgement, the local engine) used to be part of the checks
that refuse to start the whole API in production. It is now applied by the
receptionist's own start, so a production deployment that fails it serves the
chat and refuses calls. The go-live report
(`python -m app.production_readiness`) still lists it as a blocker.

`ura_receptionist_available` is recomputed at every scrape from the flag and
the start (`MetricsStore.on_scrape`): 0 while calls are switched on and cannot
be taken, 1 otherwise. Set only at start, it went on paging after calls were
switched off, and had no value for a flag switched on later.

**The worker gate reads every variable an image may start workers from.**
`WORKERS`, `UVICORN_WORKERS` and `WEB_CONCURRENCY`: at least one must be set,
and every one that is set must be `1`. An unset count is not taken as one
worker, because an entrypoint can default it without exporting it.

**Every duration a call depends on has a range.** `config.validate()` refuses
a value that is not a finite number inside it, and so does the production gate.
A call already running never reads a bad value: the getter falls back to the
default. Zero still switches hold updates and idle checks off, where zero
meant that.

**A flag switched on at runtime is held to the same gate.** In production
(`APP_ENV=production`) every call socket checks the local-engine requirements
with the flag's real state, not the environment variable's, and a flag
switched on after the API started is refused until a restart, because nothing
has checked the stack or ended the rows a previous process left. Outside
production these checks do not run; the Space is outside them today.

**A call that cannot hear or speak says so and goes to a person.** The
recognizer and the voice report each failure to the call's brain. An unheard
caller is asked to say it again; two failures in a row start a transfer, once,
through the same path as any other: an officer takes the call when
`ticket_queue` is on and someone is on shift, and otherwise the caller is given
the toll-free line or offered a callback. If the transfer itself raises, the
next failure tries again; if it did not take, an unheard caller is still asked
to repeat. A line the voice could not say outside the pipeline (an "are you
still there?", a goodbye) counts too, and so does an Orpheus sentence cut
short. An officer hears the caller over the media room, not through the
recognizer. Silence after an unheard utterance ends the call as
`speech_failed`, never `caller_idle`.

**The call path is counted.** `receptionist/telemetry.py` exports calls
started, ended by reason, refused by reason, failures by stage, and rows a
start found orphaned. Every label is a closed vocabulary of code constants,
declared at zero when the receptionist starts so that `increase()` sees the
first failure. Four alert rules read them, each with a promtool case that
fires and one that must not; `UraCallsRefused` fires per refusal reason and
carries it. The receptionist's requirements are audited as resolved on each
Python an image installs them on (3.11 for the Space, 3.14 for the API image),
because the two resolve to different versions.

**Rows a dead process left are ended at startup**, before anything else is
checked, so a restart that cannot take calls (Pipecat missing) still ends its
predecessor's calls and flags their callbacks. A row older than a call may
last is ended whichever process asks. Every live row is ended only when the
deployment declares one worker and one replica, since then the process that
has just started owns no calls. A caller who was waiting for or talking to an
officer is flagged for a callback (`service_interrupted`), and the officers
named on those rows are taken off them.

**CI runs the receptionist on its real stack.** A second job installs
`requirements-receptionist.txt` as the images do and runs the receptionist
tests; the dependency audit resolves the same file with the same Pillow
override and audits the result. Four tests that had never run in CI were
stale and are corrected.

**Server-only features promise only what they deliver.** Low-bandwidth mode
agrees to mu-law alone and not at all on a call whose audio is in the media
room; its checkpoint is written off the event loop and its messages are
rate-limited. Resume tickets are signed with `RECEPTIONIST_RESUME_SECRET`; the
development secret stands in only outside production.

Review of this change also found: the mu-law framing was continuous mu-law
companding quantised to a byte, not ITU-T G.711, so a standard decoder played
silence at full scale; it is G.711 now. Network telemetry spent the caller's
own control budget and could drop their next question; it has its own. A full
list of used resume tickets evicted one that had not expired, which let it be
used again; it now refuses the resume instead.

Also: the language router is closed with the call; synthesised audio is decoded
off the event loop; the live-caption buffer stops growing past what it can
decode; the LiveKit signer trims the credentials the gate trimmed; a call opens
only in a language the deployment takes calls in.

## Consequences

- A deployment with the flag on and a broken receptionist now starts, in
  production too: a G36 failure used to stop the API at deploy time and now
  refuses calls at run time. Nothing fails at deploy time, so the readiness
  field, the error log and the `UraReceptionistUnavailable` alert are how it is
  found. A deployment with no alerting learns from a caller.
- `SPEECH_ENABLED=false` with the receptionist flag on now means calls are
  unavailable. It used to mean a call that greeted in text and then hung up.
- A production deployment that set only `WORKERS=1` under the supervisord image
  was passing the gate with two workers. It now fails the gate, and calls are
  unavailable there until `UVICORN_WORKERS=1` is set, which is the point.
- **The production Space is not changed by this decision.** It runs two
  workers and is outside the gate: the base image sets `APP_ENV=production`,
  but the Space overrides it with a secret. Its value cannot be read; calls
  are accepted there with no LiveKit configured, which the gate would refuse,
  so it is not `production`. With this change the Space logs a warning at
  every start. Setting `UVICORN_WORKERS=1` there fixes call handoffs and
  halves the chat's worker count; that is the service owner's call and is left
  open.
- A deployment that does not declare one worker and one replica keeps a dead
  process's young rows until they are older than a call may last (about 17
  minutes at the default ceiling).
- More transfers reach officers: calls the recognizer or the voice fails on
  twice. Before, those callers were dropped.
- Durations outside their range, accepted before, now make calls unavailable
  until fixed; `/ready` names the variable. `RECEPTIONIST_TURN_TIMEOUT_S`
  accepts up to 30 s for that reason. No deployment in this repository sets a
  value outside the ranges.
- `RECEPTIONIST_TURN_TIMEOUT_S` still does not reach the turn-stop strategy,
  which reads the semantic endpoint value (500 ms by default); it drives the
  language sentinel's own timer. Changing which one governs changes
  end-of-turn timing, which needs measurement on real speech first (G600
  stays open for this).
- `low_bandwidth_voice_codec` and `call_drop_resume` remain on in the Space
  image with no client that uses them. Resume tickets are single-use within a
  process only (G599 stays open for a client and a shared nonce store).
- No new flag, route or model. One new readiness field, six new metric
  families, four alert rules, one CI job, one audit matrix entry.

## Confirmation

`App/backend/tests/test_receptionist_startup_readiness.py`: each test failed
before its fix. The stale-row sweep, presence reset and call telemetry run on
the real SQLite store and the real metrics registry; the readiness test calls
the real `/ready` route.

The receptionist tests were run against Pipecat 1.15.0 in a separate
environment before the CI job existed: 810 passed after four stale tests were
corrected. The speech-failure tests drive the real `UraWhisperSTT` and
`UraSpeechTTS` with a speech model that raises; they do not place a call.

The alert rules are checked two ways: `tests/test_monitoring_config.py` (every
series named is one the code exports; every runbook anchor exists) and the
promtool cases in `monitoring/tests/alerting-rules.test.yml`, which could not
be run locally (no promtool) and are first exercised by the
`Monitoring config` CI job.

Not verified here: the behaviour on a real deployment restart (the sweep and
the callback flags were tested on rows written by the test), and the alert
routing end to end.

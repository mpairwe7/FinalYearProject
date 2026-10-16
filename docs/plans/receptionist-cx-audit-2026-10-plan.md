# Call receptionist: QA, CX and robustness audit, and Phases R1–R4

Status: **R1 merged (#581); R2 in review** (delivery log in §8); R3–R5 proposed.
Written 2026-10-16 against `dev` @ `73dbfe6eb7` (after Phase 39, #580). Tracking issue: #441.
Gap register rows: G175–G199; G91 reopened, then fixed in R2. (G174 is left free: an uncommitted "CXP business hours" change
in the main checkout already uses it.)

Roles taken for this audit: QA engineer (does the call behave), auditor (does it meet the
reference standards) and reviewer (is the code the right shape). Method: a line-by-line read of
all 37 modules in `App/backend/app/receptionist/` (12.4k lines), the call screen
(`useCall.ts`, `CallScreen.tsx`) and the staff lobby store; the existing suites (415 receptionist
tests green on the CI interpreter); the replay harness (`scripts/replay_call_audio.py`); and
current research and regulation, verified online and cited in §2. Every finding was traced to
a line of code before it was written down; the ones marked *reproduced* also have a failing
test written before the fix.

The phases are numbered R1–R5 (receptionist) so they do not collide with the #441 phase
numbers. Scope added at the owner's request on 2026-10-16: Runyankole and Acholi everywhere the
receptionist speaks or listens, and escalations and handoffs **outside officers' working hours**
(the CXP desk closes at 11 PM EAT; the AI answers all night), for calls and for the chat.

---

## 1. Summary

| Area | What a caller (or officer) met | Severity |
|---|---|---|
| Safety | A caller waiting for an officer who said they wanted to end their life got **no reply**: speech while transferring is recorded and dropped before the crisis check runs, and the call kept its normal priority. | Critical |
| Officer desk | "Terminate call" on a waiting call an officer had dropped off raised `AttributeError` (`presence.clear_officer` does not exist): HTTP 500, and because `desk.end` marked the room ended *before* `CallRoom.end()` ran, the teardown (LiveKit room, timers, pipeline, DB status) was skipped. | High |
| Integrity | The spoken-confirmation branch told the caller an action was "confirmed and completed" without executing anything. Nothing sets the challenge today, so it is unreachable — but one producer away from a false claim. | High (latent) |
| State | "Talk to an officer" was acted on in any mode: a second request re-opened the transfer (a bridged call was set back to `transferring` under the officer), duplicated the alert and left an orphan timer. | High |
| State | The AI's transfer timer outlived the bridge: after an officer passed the call on, or dropped and lapsed, the old timer could tell the caller "all our officers are busy" early — and a re-queued call otherwise had no timeout, hold line or callback at all. | High |
| State | A call an officer passed on came back to the queue as `general_tax_support` / `normal`: the call's topic and priority were never kept on it, so an urgent call sank. | High |
| Availability | With language detection off (its registry default), the single-language pipeline kept Pipecat's 300 s idle cancel: every officer call longer than five minutes was cut and recorded as a caller hang-up. | High |
| Documentation | **G91 is recorded as fixed but is not.** The register names `is_staff_present_for_tenant`, which does not exist; the helper that was added (`transfer.is_staff_available`) is never called. Callers still hold 90 s when nobody is signed in. | High |
| Correctness | A retrieval confidence of `0.0` was replaced by the ASR word probability (`confidence or mean_word_prob`), two different measures. | Medium |
| Parity | On LiveKit (production) the language chip accepted only en/sw/lg; Runyankole and Acholi were dropped. The greeting names three of the five call languages. | Medium |
| Dead air | One filler at 450 ms, then silence until the 25 s cap. Luganda answers measure 4–13 s. | Medium |
| Time limits | The 15-minute ceiling ends a call with no warning — including a call an officer is in the middle of. | Medium |
| Silent drops | When the figure guard blocks speech, or synthesis fails, a voice caller hears nothing. | Medium |
| Queue | Waiting callers get no position, no "call me back" and no way to cancel; the officer's hold is silent (`on_hold` is defined in five languages and never spoken). | Medium |
| Honesty | Call-drop resume tickets are minted but no endpoint or client consumes them; the runbook says the flag enables resume. | Medium |
| Working hours | After 11 PM a transfer still promised a person; a chat handoff said an officer "has been asked to look at this" at 2 AM; the SLA clock ran through the night. | Medium (owner's request) |
| Performance | The risk monitor runs synchronously on the event loop on every turn (two DB reads and an O(n²) similarity scan). | Low (measure first) |
| Process | mypy is configured but not run in CI; it had reported the G176 crash (`attr-defined`) on `dev`. | Medium |

## 2. Reference standards and research (verified 2026-10-16)

| Source | What it says | Consequence here |
|---|---|---|
| EU AI Act Art. 50(1) — applies from 2 Aug 2026; Commission guidelines final 20 Jul 2026 ([AI Act explorer](https://artificialintelligenceact.eu/transparency-rules-article-50/), [Commission guidelines](https://digital-strategy.ec.europa.eu/en/policies/guidelines-ai-transparency-obligations)) | People must be told they are dealing with an AI "at the latest at the time of the first interaction", clearly and accessibly; automated phone systems are named; in sensitive contexts a one-time notice may not suffice. | The greeting already discloses; R3 keeps the disclosure in every language and on the call screen. Uganda is not bound; the Act is the most specific published standard. |
| WCAG 2.2 SC 2.2.1 Timing Adjustable, Level A ([W3C Understanding 2.2.1](https://www.w3.org/WAI/WCAG22/Understanding/timing-adjustable.html)) | A time limit set by the content must be turned off, adjusted, or the user "warned before time expires and given at least 20 seconds to extend". | G184: warn before the ceiling, offer a reference/callback, never cut an officer mid-call. The idle check ("Are you still there?") already follows the pattern. |
| Delay information in queues: Ibrahim, *Queueing Systems* 89 (2018) ([UCL Discovery](https://discovery.ucl.ac.uk/id/eprint/10047294/)); Armony & Maglaras, *Operations Research* 52(2) and 52(4) (2004) ([call-back option](https://ideas.repec.org/a/inm/oropre/v52y2004i2p271-292.html), [Columbia PDF](https://business.columbia.edu/sites/default/files-efs/pubfiles/1291/Maglaras_CallbackOption.pdf)) | Announcing delay reduces uncertainty and lets callers choose; a call-back option with a stated bound is a distinct service channel callers choose between. | G186: position on screen, a spoken "you are next" when true, and "call me back" while waiting. G180: when nobody is online, the callback comes at once. |
| CFPB, *Chatbots in Consumer Finance* (2023) ([summary](https://www.consumerfinancemonitor.com/2023/06/08/new-cfpb-report-highlights-concerns-with-growing-use-of-chatbots-by-financial-institutions/)) | Harm comes from "doom loops" without an off-ramp to a person, unreasonable waits for one, and failing to recognise when a consumer invokes their rights. | G180, G186: no hold for nobody, a way out of the queue; disputes already transfer at once. |
| OMB M-25-21 high-impact AI minimum practices ([Winston & Strawn](https://www.winston.com/print/v2/content/1100706/white-house-memorandum-elaborates-on-prior-executive-order-with-requirements-for-high-impact-ai-used-by-federal-agencies.pdf)) | Pre-deployment testing, ongoing monitoring, human oversight and intervention, a fail-safe, remedies and appeals. | R1 fail-safes (crisis in queue, fail-closed confirmation); R4 monitoring. |
| OWASP Top 10 for Agentic Applications 2026 ([Cycode](https://cycode.com/blog/owasp-top-10-agentic-applications/)) | ASI09 Human-Agent Trust Exploitation: people over-trust confident agent output; ASI08 cascading failures. | G177: never claim an action completed that no executor confirmed. G176: a desk error must not leave a half-ended call. |
| Turn-taking: Stivers et al., *PNAS* 106 (2009) ([MPI](https://forms.mpi.nl/node/50939)) | Across ten languages, conversation minimises silence between turns. | G183: no long silence after a filler. |
| Voice-agent practice 2026 ([Hamming](https://hamming.ai/resources/voice-agent-drop-off-analysis-abandonment-framework), [Coval](https://www.coval.ai/blog/voice-ai-latency)) | Vendor rules of thumb, not studies: ~800 ms voice-to-voice; gaps over 2–3 s read as failures; measure p95, not means. | G183 progress cues; R4 p95 metrics. Treated as heuristics. |
| OpenAI Realtime prompting guide, "preambles" ([OpenAI](https://developers.openai.com/api/docs/guides/realtime-models-prompting)) | Short spoken updates while the agent works reassure the caller; overused they become filler. | G183: a progress line only after real waiting, never the same line twice. |
| LiveKit Agents ≥1.5 adaptive interruption / false-interruption resume ([LiveKit docs](https://docs.livekit.io/agents/logic/turns/adaptive-interruption-handling/)) | Backchannels ("okay", "uh-huh") are told apart from barge-ins; a false interruption resumes playback. Runs on LiveKit Cloud only. | Already approximated (two-word barge-in rule, backchannel-over-offer re-ask, G-series). Not adopted: self-hosted only. |
| Uganda Data Protection and Privacy Act 2019 ([ULII](https://ulii.org/en/akn/ug/act/2019/9/eng@2019-05-03)) | Personal data, voice included, is collected with consent and for a stated purpose. | Already: consent before audio. Unchanged here. |

## 3. Findings and evidence

* **G175** *(reproduced)* — `UraReceptionistBrain.handle_external_question` returns for
  `mode in ("bridged", "transferring", "ended")` before step 0's `detect_crisis`. In
  `transferring` nobody is listening yet: officers see the line only if they open the call.
* **G176** *(reproduced)* — `desk.end`, waiting branch: `room.state.mode = "ended"`, then
  `presence.clear_officer(...)` (undefined) whenever `room.state.officer_id` is still set —
  which `_expire_reconnect_grace` leaves set. Even without the crash, `hang_up_caller` →
  `CallRoom.end()` returns at once because the mode is already `ended`. The lapsed officer's
  presence also stayed `on_call` for a call they were no longer on.
* **G177** *(reproduced)* — brain step 0c speaks `confirmed_msg` ("…has been confirmed and
  completed") on a yes; no code sets `pending_confirm_challenge` outside tests.
* **G178** *(reproduced)* — `process_frame(RequestOfficerFrame)` → `_transfer` with no mode check;
  `open_transfer` sets `mode = "transferring"` unconditionally; the previous
  `transfer_timer_task` is overwritten, not cancelled.
* **G179** *(reproduced)* — `_transfer_timeout_countdown` is cancelled only by `CallRoom.end()`.
  `desk.transfer` and `_expire_reconnect_grace` re-enter `transferring` without a timer of their
  own; the old one acts on the new wait if it is still sleeping.
* **G180** — `docs/GAPS_AND_AGENTIC_ROADMAP.md` G91 row vs. `grep -rn is_staff_present_for_tenant`
  (no hits) and `grep -rn is_staff_available` (definition only).
* **G181** — `confidence = float(result.get("confidence") or mean_word_prob or 1.0)`.
* **G182** — `ws.py` LiveKit control loop: `if language in ("en", "sw", "lg")`; the WebSocket
  serializer accepts `KNOWN_LANGUAGES`. Greeting: `phrases.py` en/lg/sw lines list three.
* **G183** — `_generate_and_speak`: `asyncio.wait(timeout=filler_ms)`, one filler, then
  `wait_for(gen_task, 25.0)`.
* **G184** — `ws.py` `max_call_s` check → `room.end("max_call_duration")`; the WebSocket path
  uses Pipecat's `session_timeout`; neither speaks first, and neither spares a bridged call.
* **G185** — `speak_allowed = False` when `verify_spoken_figures` fails in both languages;
  `UraSpeechTTS._voice` returns on a synthesis error or an empty clip.
* **G186** — transferring speech is dropped (see G175); the caller screen offers only hang-up
  while waiting; `still_holding` is the only hold line.
* **G187** — `desk.hold` sends `{"status": "on_hold"}` and mutes the officer; `phrase("on_hold")`
  has no caller.
* **G188** — `mint_call_resume_ticket` / `verify_call_resume_ticket` /
  `restore_call_drop_state`: no caller outside `low_bandwidth.py` and its tests.
* **G189** — greeting lines name English, Luganda, Swahili only.
* **G190** — `store.create_turn` → `_notify_turn_observers` → `risk.evaluate_call_risk`
  (synchronous `get_call`, `list_turns`, pairwise `SequenceMatcher`).
* **G191** — after "What else can I help you with?" a bare "No" / "Nothing, thanks" is not a
  goodbye (`_DONE_RE` needs "that's all"/"bye"), so it is sent to retrieval. *To be measured
  live before fixing.*
* **G192** *(reproduced)* — `open_transfer` computes topic and priority but sets neither on
  `CallState`; `desk.transfer` and `_expire_reconnect_grace` publish `room.state.topic or
  "general_tax_support"` and `room.state.priority or "normal"`.
* **G193** *(reproduced)* — `pipeline.build_call_pipeline`'s `PipelineTask` had no
  `cancel_on_idle_timeout=False` (Pipecat 1.11: `idle_timeout_secs=300`, idle frames are bot
  speech, transcripts and user speech); `CallerAudioTap` swallows caller audio while bridged.
  The multilingual builder has disabled it since 2026-09-23.
* **G198** *(live replay, reproduced on pre-R1 `dev` by A/B on the same stack)* — the yes/no
  question that ends an answer can be voiced seconds after the answer's last sentence. In
  scenario 14 the three sentences were synthesised at 09:48:27.7, :37.1 and :42.2 and "Would
  you like to speak to an officer about this?" only at 09:49:00.6 — after the caller's "No thank
  you" (09:49:00.4). A caller who answers in that pause is treated, correctly by the rule, as
  having spoken over a question not yet heard, and is asked again.
* **G194** — `pyproject.toml [tool.mypy]` exists; no workflow runs mypy. On `dev`:
  `desk.py: error: Module has no attribute "clear_officer"  [attr-defined]`.

## 4. Phases

### R1 — Safety and integrity (this change)

| Gap | Fix | Acceptance |
|---|---|---|
| G175 | In `transferring`, a crisis turn gets the crisis lines (numbers on screen), the call and its ticket go to `urgent`, and the lobby alert is raised again with the original wait (`call.transfer_requested`, reason `safety_concern`). | Unit: transferring + crisis → crisis line spoken, `priority == "urgent"`, ticket updated, lobby event; non-crisis speech still silent. |
| G176 | `desk.end` lets `hang_up_caller` → `CallRoom.end()` do the teardown; a lapsed officer's presence is restored and `officer_id` cleared when their grace expires. | Unit: terminate a re-queued call → 200, `CallRoom.end` ran (end_event set, timer cancelled). |
| G177 | Fail closed: a confirmed action is completed only by an executor that reports success; otherwise the caller is told it could not be completed on the call and is offered an officer. Lines move to `phrases.py` (five languages, no asides). | Unit: no executor → no "completed"; executor ok → completed; executor fails → officer offer. |
| G178 | `_transfer` acts only in `ai` mode; any previous timer is cancelled. | Unit: request while transferring/bridged → mode, ticket and timer unchanged. |
| G179 | A queue epoch on `CallState`, bumped on every entry into `transferring`; the countdown acts only for its own epoch; bridging cancels it; a re-queue (officer transfer, lapsed reconnect) starts a fresh countdown through `room.brain`. | Unit: stale timer after re-queue says nothing; re-queued call times out to a callback. |
| G181 | Missing confidence is `None`, not falsy: a `0.0` retrieval confidence stays `0.0`. | Unit: confidence 0.0 with sources → officer offered, ASR probability not used. |
| G182 | LiveKit `set_language` accepts `get_languages()`. | Unit. |
| G192 | `open_transfer` keeps topic and priority on the call. | Unit: officer transfer re-publishes them. |
| G193 | `cancel_on_idle_timeout=False` in the single-language pipeline. | Unit (Pipecat): the built task has it off. |

### R2 — Waiting for a person: availability, queue, callback, hold, working hours (calls)

| Gap | Fix | Acceptance |
|---|---|---|
| G180 (G91) | Presence pre-check before queueing: nobody signed in → the ticket kept, callback owed (`no_officer_online`), the caller told at once; a lookup error queues as before. | Unit: no officer → no wait, `officers_unavailable`, reference on screen; replay `19_no_officer_online_is_a_callback_at_once`. |
| G195 | `app/officer_hours.py` (desk schedule, configurable, default 07:00–23:00 `Africa/Kampala`); outside it with nobody signed in → `officers_off_duty`, hours on screen, callback `outside_working_hours`. An officer working late still takes the call. | Unit: boundaries, night shifts, holidays, bad settings; off-duty path. |
| G186 | Queue position as a `queue` message (never a transcript line); "You're next in line" once; "Call me back instead" by button or voice (en/lg/sw/nyn/ach) → callback `caller_requested_callback`. No expected-wait estimate (no honest data for one). | Unit + replay `20_call_me_back_while_waiting`. |
| G187 | Spoken hold (`on_hold`), reassurance every `RECEPTIONIST_HOLD_UPDATE_S`, stopped on resume, transfer or end. | Unit. |
| G189 | Greetings name all five call languages. | Unit. |

### R3 — Chat handoffs outside working hours

| Gap | Fix | Acceptance |
|---|---|---|
| G196 | `_maybe_create_ticket` puts `officer_hours` on every handoff (both chat paths carry `handoff`); the banner says when officers are next on duty; `/v1/escalate` and `escalate_to_human` word their reply for the hour — English source, localized once by `localize_reply`; no toll-free line when the desk is closed. | Root test: the funnel attaches hours on and off duty; endpoint and tool tests; banner tests in sw and ach. |
| G197 | SLA breaches count `officer_hours.working_seconds`; `sla_clock` and the hours in the payload; medians stay wall-clock. | Unit: a 22:00 handoff at 08:00 is two working hours; population tests pinned to a desk that never closes. |
| G199 | The escalation banner's text in all five locales. | Unit. |

### R4 — Dead air, time limits, degraded modes

G183 (progress cues at about 5 s and 12 s, in the call language, never repeating), G184
(WCAG 2.2.1's *Extend* option for a call the AI is handling: a spoken and on-screen warning a
minute before the ceiling — more than the 20 s the criterion asks — and one simple action, a
"yes" or a tap, extends it, at least ten times; a call an officer is on is not cut at the AI's
ceiling at all), G185 (a spoken pointer to the screen when speech is withheld or
fails), G191 if the live check confirms it, G198 (find why the closing question waits, and voice it
with the answer's last sentence).

### R5 — Honesty and observability

G188 (implement resume, or mark the flag as minting only and say so in the runbook), G190
(measure on the GPU stack; move off the event loop if it costs), G194 (a mypy gate for the
receptionist package), queue metrics (callback offered
and accepted, abandoned while waiting, wait time, crisis-in-queue) through `analytics.metrics`
with bounded labels, and replay scenarios for every new behaviour.

## 5. Coordination note

The main checkout carries an uncommitted, idle change (last edited 2026-10-16 12:26 JST) that
adds `cxp_business_hours` (its own G174) and edits `brain._transfer`, `phrases.py` and
`transfer.close_transfer_on_timeout`. R2 and R3 cover the same ground (G195–G197) and keep its
`CXP_*` setting names and its `callback_reason` keyword, but not its shape: it appended an English
notice to replies in three places, after localization (so a Luganda reply ended in English), and
de-duplicated by matching the text "11:00 PM EAT". R3 composes the message once, before
localization. That change should be dropped rather than merged once R3 lands.

## 6. Human-only follow-ups

Native review of every new lg/sw/nyn/ach line (all are drafts, `phrases.UNREVIEWED`); an
Orpheus listening check of the new lines (round trip through Whisper, per
`orpheus-voice-limits`); a staffed-desk game day for the officer paths, which the harness cannot
sign in to.

## 7. Out of scope

Carrier telephony (this remains a browser simulation); multi-replica call actors (blocked on
shared state, see the runbook); LiveKit Cloud's adaptive interruption model.

## 8. Delivery log

| Phase | PR | State |
|---|---|---|
| R1 | #581 | **Merged 2026-10-16** (`98c7c9e550`). Unit: 504 receptionist tests green on the CI interpreter. Live, isolated GPU stack (Sunflower vLLM, SunflowerASR, Orpheus): 14/18 replay scenarios, the new `18_crisis_while_waiting` and `11` (hold, then callback through the epoch-bound countdown) included (`evals/reports/call_replay_2026-10-16_r1_isolated.json`). The four failures (2, 8, 13, 14) fail the same way on pre-R1 `dev` on the same stack (`…_dev_baseline_ab.json`, 0/4): a Luganda answer over the 25 s cap, SunflowerASR mishearing the synthetic Luganda officer request, and G198. |
| R2 | #582 | **Merged 2026-10-16** (`ee26e89df0`). Live, isolated GPU stack: unstaffed 10/13, the new `19_no_officer_online_is_a_callback_at_once` included; staffed 3/5 (`11` hold then callback, `18` crisis while waiting, `20` call me back while waiting); off duty (`CXP_CLOSED_DATES` = today) 1/1 with the off-duty wording and the hours on screen (`evals/reports/call_replay_2026-10-16_r2_*.json`). Failures as on pre-R1 `dev` (`2`, `8`, `13`, `14`) plus `5`, an English fallback after two failed figure checks (G185, R4); the `14` trace gave G198's real cause. |
| R3 | — | in review |

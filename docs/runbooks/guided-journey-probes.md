# Runbook — guided-journey probes

How to check, against a running deployment, that taxpayer prompts land where
they should: the guided flow they ask for, the step-by-step offer an answer
should carry, the officer handoff, the crisis reply, the reply language. Also
how to put a branch build on the local GPU stack to run them, and how to read
the journey funnel.

Written 2026-09-29 alongside gaps G60–G65 in
[`docs/GAPS_AND_AGENTIC_ROADMAP.md`](../GAPS_AND_AGENTIC_ROADMAP.md).
Retention and subject export/erasure behavior updated 2026-09-30.

## When to run it

After any change to routing (`agents/patterns/*`, `service._maybe_handle_workflow`),
to a flow under `app/workflows/flows/`, to `app/text_signals.py` or
`app/turn_guidance.py`, and after every deploy of the api image. It is a
canary: exit status 1 means at least one case missed.

## Run it

```bash
# Local GPU stack, through the web app's proxy (what a taxpayer's browser hits)
python3 scripts/probe_guided_journeys.py \
  --base https://struttingly-nongeological-briella.ngrok-free.dev/api \
  --out docs/Reports/data/guided_journey_probes_$(date +%F).json

# Local api container directly
python3 scripts/probe_guided_journeys.py --base http://localhost:8083

# Hugging Face Space (no /api prefix)
python3 scripts/probe_guided_journeys.py --base https://landwind22-ura-chatbot.hf.space
```

Each case is a fresh conversation (one to three turns). The JSON record keeps
what was observed for every case, so a failure can be read without re-running.

The probes reject non-HTTP(S) base URLs and use `httpx` for requests.
Only `http` and `https` bases are accepted.

**Side effect:** the escalation cases open real tickets in that deployment's
officer queue, and every turn is logged as a conversation. Run it against test
stacks, not production.

## What the cases pin

| Behaviour | Cases | Code |
| --- | --- | --- |
| A how-to question is answered and offers the matching journey | how-to return, TCC need, vehicle how-to | `turn_guidance.guided_mode_action`; G39 entrance rule in `service._maybe_handle_workflow` |
| An explicit request starts the journey | help-me return/TIN, VAT walkthrough, PRN, customs, objection, TCC guide, imported car | `WorkflowRegistry.match_trigger` (normalised, name as trigger) |
| "Help me …" gets no stress opener | help-me return/TIN | `text_signals._DISTRESS_PATTERNS` |
| Only account-state questions escalate | how-to return (no), "What is my balance?" (yes) | `HOW_TO_QUESTION_RE` in `agents/patterns/en.py` |
| An explicit human request escalates | "I want to talk to a person" | supervisor escalation rules |
| Self-harm gets crisis lines, no tax content | crisis | `text_signals.detect_crisis`, `service._crisis_support_result` |
| A feeling with no task gets a clarifying question | feeling-only turn | `text_signals.is_feeling_only`, `service._conversation_repair_result` |
| Distress across turns brings in an officer | sustained frustration (3 turns) | repair's second reply, or `distress_trajectory` |
| "I'm …" still reads as a feeling | contraction | `_FEELING_WORDS` includes contraction fragments |
| A past event does not start a flow | "I filed my return yesterday but …" | `_VERB_FORMS` folds "-ing" forms only |
| "nil return" still starts Return Filing | nil return | `_RETURN_QUALIFIER_RE` leaves "nil" alone |
| Asking for another flow inside one switches | TCC, then "help me file my return" | `service._leaves_flow_for_another` |
| "Help me" does not exempt an account problem | "Please help me, my account is locked" | `HOW_TO_QUESTION_RE` has no "help me" |
| Luganda / Kiswahili questions are answered in that language | Luganda TIN, Kiswahili TIN | `localize_reply` (G64 open for Kiswahili) |

"I need a tax clearance certificate" is **answered, not captured**: "I need …"
reads as a question at the flow entrance (G39). The answer carries the
one-tap offer. Do not widen the entrance to make that case start the flow.

## Results, 2026-09-29 (local GPU stack, Sunflower-14B-FP8)

| Prompt | Before (image `app-api:gpu-pre-journeys`) | After (branch `feat/journeys-emotional-qa`) |
| --- | --- | --- |
| How do I file my return? | escalated, officer ticket opened | answered, "Guide me step by step through Return Filing" |
| Walk me through filing my VAT return | VAT explainer (education) | Return Filing flow |
| Help me register for a TIN / file my return | flow, opened with "I understand this can feel stressful" | flow, no opener |
| I need a tax clearance certificate | Document Authentication FAQ (wrong answer) | TCC answer + Tax Clearance Certificate offer |
| Guide me through getting a tax clearance certificate | same wrong FAQ | Tax Clearance Certificate flow |
| Guide me through registering my imported car | generic retrieved text | Motor Vehicle Registration flow |
| This is useless → It still does not work | off-topic passage about URA's funding and staffing (measured on the Phase 1 build, before repair) | clarifying question, then officer first |
| I can't pay this tax, I want to kill myself | (not tested before) | crisis lines, officer offered |
| Nitasajili vipi … TIN … ? (Kiswahili) | English reply | English reply — **G64, open** |

**After: 24 of 25 cases pass** (Phases 1–5, including the code-review fixes);
English cases answered in 0.5–5.2 s on an idle stack with caches cold after the
restart, Luganda in 5.3 s (translation). The one miss is G64. Record:
[`docs/Reports/data/guided_journey_probes_2026-09-29_local_after.json`](../Reports/data/guided_journey_probes_2026-09-29_local_after.json).

**Latency caveat.** The "before" run shared the stack with a 2,000-FAQ
evaluation at 24 concurrent requests, and retrieval answers took 26–42 s. Check
for concurrent load before quoting latency: `pgrep -af "evaluate_|locust|k6"`.

## Put a branch build on the local GPU stack

App code is baked into `app-api:gpu`; the stack must be rebuilt to see a change
(bring-up of the whole stack: "Full-stack live verification — GPU-pinned,
ngrok-exposed" in [`salt-speech-backends.md`](salt-speech-backends.md)).

**Do not run `docker compose` from a git worktree.** The api's bind mounts
(`App/Data/*`, `data_store/`, `artifacts/`, `App/Model`, `fine-tuning/adapters`)
are relative to the compose directory, and those directories are gitignored
generated state that a worktree does not have: the api would start with no
corpus. Build from the branch, recreate from the main checkout:

```bash
# 1. keep the running image for rollback
docker tag app-api:gpu app-api:gpu-pre-<change>

# 2. build from the branch checkout (context = repo root, as in docker-compose.yml)
cd <branch checkout> && docker build -q -f Dockerfile.gpu -t app-api:gpu .

# 3. recreate only the api, from the MAIN checkout, same GPU pinning as the stack
cd ~/Mpairwe7/FinalYearProject/App
GPU_ID=2 VLLM_GPU_ID=5 SUNFLOWER_GPU_ID=5 docker compose \
  -f docker-compose.yml -f docker-compose.local-retrieval.yml \
  -f docker-compose.local-sunflower.yml -f docker-compose.gpu-salt.yml \
  up -d --no-deps --no-build api
```

Read the GPU ids off the running containers first
(`docker inspect <container> --format '{{json .HostConfig.DeviceRequests}}'`);
2 and 5 were the cards on 2026-09-29. `--no-deps` leaves vLLM, Qdrant and Redis
alone. The api takes about two minutes to report healthy; the proxy answers 500
until then. A logged "prototype seed skipped" traceback at startup is expected
(no seed file is mounted) and is not a failure.

**Roll back:** `docker tag app-api:gpu-pre-<change> app-api:gpu`, then step 3.

## Read the journey funnel

**For the CX team: the "Guided journeys" panel on `/analytics`** (staff sign-in,
`ura_admin` or `ura_auditor`). One row per journey for the selected period:
started, finished (with rate), stopped (cancelled + abandoned), the step where
most journeys stop, and the step taxpayers rated least helpful. Fix the first
drop-off, reword the least helpful reply, then re-run the probes.

The panel reads `GET /v1/analytics/journeys`, built from the stored
`workflow_sessions` and `feedback` tables: the same on every replica, kept
across restarts, scoped by the period picker. A journey still active but
untouched for `JOURNEY_ABANDON_AFTER_HOURS` (default 24) counts as abandoned.
Ratings reach a step because the web client sends `workflow_id` and `step_id`
with each thumbs up or down (identifiers only; the API refuses anything else).
`WORKFLOW_SESSION_TTL_DAYS` (365 by default) controls how long outcome rows
remain available to the funnel, and the API caps `period_days` to that window.
`slots_json` and `last_prompt` are cleared after `CONVERSATION_TTL_DAYS`
(7 by default). Authenticated journey rows are available in `/v1/me/export`
and removed by `/v1/me` erasure.

**For operators: `journey_events_total{workflow, event, step}` on `/metrics`**
(admin token required; 401 without one), events `started`, `step_entered`,
`step_invalid`, `completed`, `cancelled`. It is per replica and resets on
restart, so use it for live debugging, not for the funnel: a high
`step_invalid` on a step means its question or validator confuses people.
The metrics counter and funnel response contain no slot values. The durable
journey row can contain collected answers until conversation retention expires;
it is then reduced to flow, status and step metadata. The applicable lawful
basis for guided service state and aggregate measurement remains a privacy
governance decision, documented in
[`PRIVACY_COMPLIANCE_306.md`](../compliance/PRIVACY_COMPLIANCE_306.md).

## Crisis lines

`text_signals.crisis_support_reply` names 999/112 (Uganda Police Force,
upf.go.ug) and Mental Health Uganda's 0800 21 21 21 (Mon–Fri 8:30–17:00), checked
on `CRISIS_LINES_VERIFIED_ON`. Re-check both sources before changing that date,
and at least every quarter.

## Committing

The `ggshield` pre-commit hook needs a GitGuardian API key; without one it
fails before scanning. `SECURITY.md` records that ggshield skips without a key,
so `SKIP=ggshield git commit …` matches that policy — TruffleHog, Gitleaks,
detect-secrets, Semgrep and Bandit still run on every commit.

# ADR-0067: The flat backend modules move into job-named packages

## Status

Accepted, 2026-11-20. The project owner asked for redundant files to be removed
and modules consolidated under `App/` and in the backend. This record continues
ADR-0056, ADR-0060 and ADR-0061. It is amended as each phase lands; the phase
table says which have.

## Context and Problem Statement

After the three earlier moves, `App/backend/app` still held 79 single-file
modules (50,360 lines) beside 28 packages. Ten of them were speech and voice.
Others were live web research, ticketing and officer handoff, document
handling, and the connectors. A reader looking for "where is speech" had to
know the file names.

A survey of the tree on 2026-11-20 measured what else could go.

- **No re-export shims remain.** The earlier moves left none.
- **Three true duplicates.** One conversation-context builder copied between
  the two stores, and one file-hash loop copied between two modules, are gone
  (G696). A third pair is five lines and keeps separate state.
- **Twenty-four modules have no importer in runtime code.** Eighteen are
  command-line tools, MCP server entry points or service entry points that a
  workflow, a Dockerfile or a Compose file starts. Six are imported only by
  tests.

## Considered Options

1. Move each group into a package named for its job. A module keeps its file
   name. Old paths are removed in the same change.
2. The same, and drop a prefix that repeats the package name
   (`speech/service.py`, `speech/normalization.py`).
3. Leave the flat modules, and delete the six that only tests import.

Option 2 follows `guardrails/premise.py` in ADR-0061, but the codemod then
renames the bound name in every caller. Eighteen modules would read
`service.transcribe(...)`, and `service` was the old name of the RAG
orchestrator until ADR-0056. Option 3 removes code the governance registry
cites: `tax/schedule_sandbox.py` is evidence for a control, and the others are
tested features that are not wired in yet. They are unused, not redundant, and
removing them is the owner's decision.

## Decision Outcome

Option 1, in phases of under 100 files so each pull request is reviewed.

| Phase | Package | Modules | Landed |
| --- | --- | --- | --- |
| 1 | `speech/` | `speech_service`, `speech_normalization`, `accent_detector`, `orpheus_tts`, `spark_tts_salt` | yes |
| 2 | `speech/` | `voice_consent`, `voice_stream`, `voice_stream_v2`, `voice_ws`, `voice_ws_v2` || yes |
| 3 | `research/` | `web_research`, `web_search_providers`, `web_fx`, `egress`, `doh_resolver`, `ephemeral_pdf_sandbox` | yes |
| 4 | `tickets/` | `escalation_policy`, `escalation_notify`, `officer_handoff`, `officer_chat`, `officer_hours`, `ticket_events`, `ticket_ws`, `hitl_routing` | yes |
| 5 | `quality/` | `quality_audit`, `service_ratings`, `audit_reviews`, `evaluation`, `discrepancy_detector`, `journey_analytics` | yes |
| 6 | `tax/` (it existed) | `calculator_router`, `scenario_planner` | yes |

**What stays flat, and why.**

- `main.py`, and shared infrastructure that belongs to no one job: `_root`,
  `redis_shared`, `resilience`, `tenancy`, `linear_text`, `text_signals`.
- `_root`, `freshness`, `malware_scan` and `mt`. The seven files whose hash is
  the dense cache key import them. Moving one changes an import line in a key
  file, and the next image build re-embeds the corpus for 35 to 40 minutes.
- `number_words`. Speech uses it, and so do the calculator, memory, entailment
  and the MCP validators.
- The rest. The chat helpers (`chat_ws_v2`, `turn_pipeline`, `conversational`
  and others), the connector and portal modules, the two offline modules and the
  operational gates were sized and left: each group is two or three files, or
  has no one job a package name would state.
- `documents.py` and the modules beside it. A package cannot take the name of a
  module that still exists, so moving them means renaming `documents`, and the
  codemod would then rename it in 24 importing files.

**The tool.** The ADR-0056 codemod performs each phase. It was deleted as a
finished one-shot (G686), so it is read from git
(`9a8e344c^:App/backend/scripts/restructure_rag_layout.py`) and driven by one
move table per phase. It rewrites every import, patch-target string and path,
and it is not committed again. ADRs, gap rows, plans and reports are left as
written: they are dated records.

**Not done.** The experiment scripts in `App/scripts` stay as they are. Several
are near-copies at different sizes, but each writes its own published report,
so merging them would change how a reported number is reproduced.

## Consequences

- Speech and voice code is found in `app/speech/`, live web research in
  `app/research/`, handoff to officers in `app/tickets/`, service quality in
  `app/quality/`, and the calculators beside the rate tables in `app/tax/`.
  Forty-seven modules stay flat, down from 79. Imports read `from .speech import speech_service`; a
  caller's own code does not change.
- A moved module's logger takes its new name, for example
  `app.speech.speech_service`. A saved log query on the old name needs the new
  one.
- Each phase is a backend change, so each merge rebuilds the image and rolls
  the Space. No phase touches a dense cache key file, so no build re-embeds
  because of one.
- Phases merge one at a time. A second backend merge cancels a build that is
  still running.
- The Space layer patches files inside the image by path
  (`HF_CPU_PATCH_B64` in `App/deploy/hf-space/Dockerfile`). The script is
  base64-encoded, so the codemod cannot reach it, and it also inserts code with
  its own imports. Phase 1 updates it by hand. It looks for
  `speech/speech_service.py` first and the old path second, because a pinned
  image can predate the move, and the code it inserts imports absolutely, so it
  runs at either depth. Left as it was, the Space build would have failed on the
  missing file; with only the path fixed, `from . import sunbird` inside
  `speech/` would have failed at run time, behind the fallback's own
  `try/except`. The script still names `pdf_export.py` and
  `providers/gateway.py` by their current paths, so a phase that moves either
  must update it too.

## Confirmation

- The codemod's `--verify` reports no new problem after each phase: every
  `app` import and patch-target string resolves, and no old path survives
  outside the dated records. Nine findings were already there before phase 1.
- `app.main` imports, and no module is loaded under an old name.
- The test files a phase changes pass locally; CI runs both full suites.
- `App/tests/test_hf_space_layer.py` loads `speech_service.py` as the Space
  builds it and runs the inserted Gemini fallback. With a relative import put
  back into the layer, 17 of its 32 tests fail.
- After each merge the Space is checked on the new image: `/ready`, the startup
  log, and live answers.

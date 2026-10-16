# ADR-0002: 5-Locale (`en`, `lg`, `sw`, `nyn`, `ach`) Multimodal Speech/RAG Parity & CXP Operating Hours (`07:00–23:00 EAT`) SLA Calendar

- **Status**: Accepted
- **Date**: 2026-10-17
- **Deciders**: URA Capstone Architecture & CX Engineering Team
- **Technical Story**: Issue #441, Gaps G174, G180, G186, G189, G195–G199 (`docs/plans/receptionist-cx-audit-2026-10-plan.md`)

## Context and Problem Statement

Uganda Revenue Authority taxpayers interact across text chat, voice narration, and the live WebSocket/LiveKit call receptionist in five primary languages: **English (`en`)**, **Luganda (`lg`)**, **Kiswahili (`sw`)**, **Runyankole (`nyn`)**, and **Acholi (`ach`)**. Furthermore, while the AI assistant operates 24/7, live URA Contact Experience (CXP) officers work `07:00–23:00 EAT` (`Africa/Kampala`). Previously, escalations after `23:00 EAT` promised immediate live officer handoffs, placed voice callers on a 90-second hold queue when nobody was on duty, and counted overnight closed hours (`23:00–07:00 EAT`) against officer SLA breach timers.

## Decision Drivers

- **5-Locale End-to-End Parity**: Every taxpayer surface — UI dictionary (`App/frontend/src/lib/i18n/`), English-pivot RAG (`TraSe`), figure-guarded machine translation (`mt.py`), neural TTS (`Sunbird/orpheus-3b-tts-multilingual`), neural ASR (`Sunbird/SunflowerASR-51-african-languages`), and call receptionist phrases (`receptionist/phrases.py`) — must support `en`, `lg`, `sw`, `nyn`, and `ach`.
- **Honest After-Hours Escalation & Callback**: Never place a voice caller on hold when no officer is online or outside `07:00–23:00 EAT`, and never tell a chat user at night that an officer is looking at their conversation right now.
- **Fair Business-Hours SLA Accounting**: Officer first-response and next-reply breach clocks must pause while CXP is closed (`23:00–07:00 EAT`).

## Decision Outcome

1. **5-Locale Multimodal Stack**:
   - `Sunbird/Sunflower-Qwen3.5-9B` (`262,144` native context window) serves English-pivot RAG generation and prompted local MT with `#NMBR...#` sentinel figure protection (`mt.protect_figures` / `mt.figures_survived`).
   - `Sunbird/orpheus-3b-tts-multilingual` serves 5-locale neural speech synthesis with catalog speaker validation (`orpheus_tts.speaker_for`), paired with `Sunbird/SunflowerASR-51-african-languages` (`whisper_salt`) for 5-locale ASR.
2. **Timezone-Aware Officer & CXP Operating Hours (`07:00–23:00 EAT`)**:
   - `app/officer_hours.py` and `app/escalation_policy.py` (`evaluate_cxp_schedule`, `business_hours_elapsed_seconds` behind `cxp_business_hours`) evaluate the `Africa/Kampala` `07:00–23:00 EAT` window with active officer presence override (`count_active_officers() > 0`).
   - Outside working hours (when no officer is online), `POST /v1/escalate`, `EscalateToHumanTool`, auto-escalated chat turns, and `UraReceptionistBrain._transfer` queue the ticket/callback immediately (`after_hours_cxp_closed` / `outside_working_hours`), disclose CXP hours and the next shift in all 5 locales, and pause SLA breach clocks until `07:00 EAT`.

## Consequences

- **Positive**: 100% pass rate across the **40,000-scenario 5-locale (`en`, `lg`, `sw`, `nyn`, `ach`) multimodal (`TTT`, `STT`, `TTS`) benchmark** and **166-probe live ngrok stress/load suite** (`docs/Reports/MULTILINGUAL_40000_CX_MULTIMODAL_NGROK_REPORT_2026-10-17.md`).
- **Negative**: Time-of-day-sensitive integration tests must pin `CXP_OPEN_HOUR` / `CXP_CLOSE_HOUR` (or `CXP_FORCE_AFTER_HOURS`) so assertions remain deterministic regardless of when CI runs.

## Confirmation

Verified by:
- `PYTHONPATH=App/backend python3 -m pytest App/backend/tests/test_officer_hours.py App/backend/tests/test_ticket_ops_sla.py tests/agents/test_agentic_voice_2026_enhancements.py tests/test_answer_integrity_integration.py -q`
- `scripts/benchmark_40000_cx_multimodal_ngrok.py`

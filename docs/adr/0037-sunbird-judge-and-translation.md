# ADR-0037: The FAQ judge and the translation fallback run on Sunbird, not Gemini

## Status

Accepted — 2026-11-10

## Context and Problem Statement

ADR-0036 moved answer generation from Gemini to Sunflower through Sunbird's API because the
Gemini key is IP-restricted (`API_KEY_IP_ADDRESS_BLOCKED`) and fails from the HF Space. Two other
features still called Gemini on that key, and so failed on the Space on every call:

- **The FAQ judge** (`service._judge_rescue`): the last-resort step that asks a model which
  candidate FAQ answers a question once every other match has failed.
- **The "fast" translation tier**:
  - `SpeechModel._gemini_translate`, used by the speech translator (`_do_translate`);
  - reply localization (`localize_reply`) and retrieval translation (`query.py`);
  - the tier ahead of Sunbird's `/tasks/translate` for any pair that endpoint does not serve.

The project owner asked for both to move to Sunbird.

## Decision Outcome

- **The judge asks Sunflower** through `sunbird.chat_completion`, at temperature 0 with
  `FAQ_JUDGE_TIMEOUT` (20 s).
  - Thinking text is stripped before the JSON verdict is parsed.
  - It runs only when Sunbird is available, and shares `SUNBIRD_LLM_BREAKER`, so a failing
    account skips the judge too.
  - `FAQ_JUDGE_MODEL` (a Gemini model name) is gone.
  - The judge stays off unless `FAQ_JUDGE_ENABLED=true`, and the Space does not set it.
- **The translation tier is Sunflower**: `SpeechModel._sunbird_llm_translate`, through
  `sunbird.translate_with_sunflower`.
  - Its prompt names both languages and tells the model to keep every number, amount, date and
    code as written.
  - Thinking text is stripped, and an empty translation is a failure.
  - It has the same gating as before (`cloudflare_fallback` and `TRANSLATE_FALLBACK_BACKEND`), and
    the retired value `gemini` is read as `sunbird`.
  - It shares the Sunbird breaker. A call turn never reaches it.
  - The whole call, account failover included, is bounded by the speech tier's deadline
    (`SPEECH_CLOUD_DEADLINE_S`, 40 s), so slow accounts cannot hold a shared speech worker past it.
- **Tier order in `_do_translate`.**
  - Pairs `/tasks/translate` serves: that endpoint, then Sunflower, then Cloudflare Llama, local
    MT and prompted Qwen.
  - Any other pair: Sunflower first.
  - The result's backend label is `sunbird_llm`.
- **Figure checking is unchanged.** Every localized reply still passes `mt.figures_survived` in
  `localize_reply`, whichever tier translated it.
- **Startup validation.** `TRANSLATE_FALLBACK_BACKEND=sunbird` (or `gemini`) now needs a Sunbird
  token, and no longer needs a Gemini key.

## Consequences

- **Good:** the judge and the translation fallback work from the Space again with the Sunbird
  tokens updated on 2026-11-10.
- **Good:** Sunflower is trained on the Ugandan languages.
- **Good:** one fewer foreign processor sees taxpayer text.
- **Bad:** Sunflower is slower than Gemini flash-lite (seconds rather than 1.5 s). Only questions
  that would otherwise abstain pay that cost for the judge. Translation only reaches this tier when
  `/tasks/translate` cannot serve the pair or fails.
- **Bad:** one breaker now covers Sunbird generation, judging and translation. A failing account
  stops all three at once and lets the other tiers serve.
- **Unchanged:** Gemini still backs the TTS and ASR fallbacks (`_synthesize_gemini_tts`,
  `STT_FALLBACK_BACKEND=gemini`) and the call receptionist's Gemini Live, which is off on the Space
  (G538).

## Confirmation

- **Tests:** `tests/agents/test_sunbird_judge_translation_2026_11.py` (12 tests) covers:
  - the translation prompt (language names, figures kept);
  - thinking stripped and empty translations failing;
  - the tier asking Sunflower and never Gemini, including under the retired `gemini` setting;
  - the tier staying off unless selected;
  - the shared breaker opening;
  - call turns;
  - a pair `/tasks/translate` does not serve leading with Sunflower;
  - the judge reading past a decoy object in its thinking, its timeout, its availability gate and
    its breaker.
- **Updated tests:** `test_providers`, `test_translate_then_retrieve`, `test_call_carryover`,
  `test_sunbird_translation_routing` and `test_production_hardening`.
- **Sabotage pass:** eleven controls were each disabled in turn, and a test failed every time.

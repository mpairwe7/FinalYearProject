# Memory, context and session across en / lg / sw — follow-up review, 2026-10-06

Follows [the 2026-10-03 review](MEMORY_CONTEXT_SESSION_REVIEW_2026-10-03.md),
which settled ownership and tenancy. This one asks whether a Luganda or Swahili
conversation gets the same continuity as an English one. It did not: the system
treated the answer language as a per-message guess, and several components that
carry context between turns only understood English. Gap ids are in
[the gap register](GAPS_AND_AGENTIC_ROADMAP.md) (G113–G123).

Findings marked *reproduced* were confirmed by running the code's own functions
on the load suite's Luganda and Swahili questions
(`tests/load/ngrok_multilang_suite.py`); the rest are from reading the code.

## Delivery

| Phase | Scope | Gaps | Status |
|---|---|---|---|
| 1 | The answer language as conversation state; streaming/REST routing parity | G113–G118 | **Shipped** (this PR) |
| 2 | Each turn stored in English beside the original; English history for the model; the context window and token budget | G119, G120 | **Shipped** |
| 3 | Memory lifecycle: one episode per conversation, word-boundary topic tags on the English form, lg/sw fact cues, fact validity periods | G121 | Open |
| 4 | Sessions and channels: anonymous history across tabs, chat context carried into a call, multi-turn Luganda calls, no Gemini in the call path | G122 | Open |
| 5 | History scrubbed before it is replayed, `gen_ai.conversation.id` on traces, a trilingual multi-turn evaluation | G123 | Open |

## Phase 1 — what changed and why

**The web client lost auto-detection (G113, reproduced).** PR #529 made any
requested supported locale, `en` included, count as the taxpayer's choice. The
browser sends `locale: "en"` until someone touches the picker, so "Nnyinza ntya
okwewandiisa okufuna TIN?" resolved to `en`. Clients now say whether the
taxpayer picked the language (`locale_explicit`). A client that omits it keeps
the pre-#529 contract: a requested `lg`/`sw` is a choice and `en` is only the
default. The web client starts in Auto-detect (an option in the language menu
and in Settings), sends the flag, and treats a returned language as a hint
unless the backend says the taxpayer asked for it (`locale_source`).

**The language is conversation state (G116, reproduced).** `app/language_state.py`
decides, highest precedence first: a request typed into the message; a picked
language; the language the conversation was last answered in (the stored
`conversations.locale`, which `normalize_history_turns` used to drop); detection;
the profile's saved language for an ambiguous first message; English. The
thread language holds until a substantive message (4+ words, 2+ words of the new
language, none of the old) clearly switches it, so "What about 150m?" stays in
Luganda while "What documents do I need for the application?" moves a Luganda
thread to English. Word evidence is the call receptionist's `lexical_hits`, so
chat and calls read a sentence the same way. Voice and call callers pass the
language they already decided (ASR, the call's `LanguagePolicy`) as explicit.

**Streaming did not route like REST (G114, G115, G117).** The web and WebSocket
clients use `generate_retrieval_only`, which never built the English form of a
turn: the FAQ authorization gate, guided workflows, the calculator, PRN
generation and topic tracking all read raw Luganda or Swahili, and officer
replies were delivered on REST only. Both paths now share
`_english_router_form`, and the input guard checks the English form too, because
its patterns are English. Separately, since 2026-09-21 the supervisor's
ESCALATE and specialist routes on streaming turns sat after a `return` and never
ran; that is fixed.

**The profile language (G118).** `primary_language` accepts Swahili, and with
personalization consent a saved Luganda or Swahili answers a new conversation
whose first message gives no language of its own ("TIN?").

**Verification.** `App/backend/tests/test_language_state.py`,
`App/backend/tests/test_streaming_language_parity.py`, and
`tests/test_language_session_integration.py` (real app over HTTP, sending what
the browser sends); frontend `useChatStore`, `LanguageMenu` and `SettingsDialog`
tests. Live verification on the GPU stack is recorded in the PR.

## Phase 2 — what changed and why

**History in English for the models (G119).** Each Luganda or Swahili turn is
now stored with its English form beside the original: the question as it was
routed and the answer before translation (`conversations.user_message_en`,
`bot_reply_en`), on every transport that logs a turn — REST, SSE, WebSocket,
voice chat and calls. `context_manager.english_view` hands the generator, the
rewriter, entity extraction and the rolling summary the English, while
transcripts, exports and the audit trail keep what the taxpayer typed and saw.
This keeps the prompt in one language (mixed-language input degrades answers)
and roughly halves the tokens a Luganda thread spends on history. Search text
that is already English — what the history-aware rewriter now tends to return —
is not translated again.

**No hole between the prompt and the summary, and a real budget (G120).** The
prompt replays the last three turns and the summary now covers every older one.
`llm._fit_to_context` asks vLLM for its served window and the prompt's exact
token count, drops the oldest exchanges whole, then shortens passages without
touching the question or the answer-language instruction. The streamed answer —
the web client's path — had no budget at all; all four vLLM paths now share this
one.

**Verified live (GPU stack, 2026-10-06).** The turn phase 1 left wrong — decided
English, answered in Luganda — is answered in English. A six-turn Luganda thread
completed with every reply in Luganda, and the log shows prompts trimmed to the
4,096-token window (3,349 and 3,790 prompt tokens). Every Luganda question was
stored with its English form.

**Known limit.** A reply has a stored English form only when it was translated
from English. When the model writes Luganda directly — the calculator's
trilingual lines, or Sunflower answering a Luganda question in Luganda despite
the instruction — there is no English text to store, and that turn is replayed
in Luganda. Translating such replies back for history would cost a model call
per turn; it is left until it shows up as a problem.

## Open findings (phases 3–5)
- **G121 — memory lifecycle.** The episode is rewritten by every turn
  (`turn_count` sticks at 2); topic tags match substrings ("private" → VAT,
  "city" → CIT, "getting" → registration); industry cues are English-only; only
  `taxpayer_type` is superseded.
- **G122 — sessions and channels.** Anonymous history is keyed on the per-tab
  analytics id while the browser keeps 50 conversations with no expiry (server
  TTL 7 days); a call starts a new conversation carrying only a language hint;
  the Luganda call path is single-turn and still falls back to Gemini, against
  the 2026-09-30 local-only rule.
- **G123 — safety, traces and evaluation.** Stored history is replayed
  unscrubbed; traces carry no `gen_ai.conversation.id`; no test covers
  multi-turn behaviour in all three languages.

## Standards and research behind the design

- Output language in code-switched chat: models often answer in the wrong
  language and lean towards non-English replies ([OLA, ACL 2026](https://aclanthology.org/2026.acl-long.2162/)).
  Here the language is decided in code, not by the model, which is why the
  decision's inputs matter.
- Users deliberately switch to the language they want the answer in
  ([CHI 2026 study](https://dl.acm.org/doi/10.1145/3772363.3798492)) — the
  reason a full sentence switches a thread and a short follow-up does not.
- Mixed-language input degrades question answering ([language anchoring,
  EMNLP 2026](https://arxiv.org/abs/2606.19668)); translated evidence weakens
  attribution ([cross-lingual BrowseComp-Plus](https://arxiv.org/pdf/2606.15345)).
  Phase 2 keeps model-facing history in English and citations on English sources.
- Token cost: Luganda takes 2.07x and Swahili 1.54x English's tokens on o200k
  ([The African Language Tax](https://huggingface.co/papers/2606.24460)), and
  long inputs degrade answers ([Chroma, context rot](https://research.trychroma.com/context-rot)).
- Memory lifecycle and evaluation: validity periods instead of overwrites
  ([Zep/Graphiti](https://arxiv.org/pdf/2501.13956.pdf)); state scoped by
  session, user, app and invocation ([Google ADK state](https://adk.dev/sessions/state/));
  remember/update/forget evaluation ([MemOps](https://arxiv.org/pdf/2607.12893),
  [LongMemEval](https://arxiv.org/abs/2410.10813)).
- Safety: multi-turn Kiswahili jailbreaks reached 41.8–70.9% harmful-response
  rates on commercial models ([Marx & Dunaiski 2026](https://www.alphaxiv.org/abs/2605.18239));
  memory and context poisoning ([OWASP ASI06](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)).
- Sessions and telemetry: [NIST SP 800-63B-4](https://pages.nist.gov/800-63-4/sp800-63b/aal/)
  (AAL2: at most 24 h overall, 1 h idle); [OpenTelemetry `gen_ai.conversation.id`](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/);
  BCP 47 tags ([RFC 5646](https://www.rfc-editor.org/info/rfc5646/)).

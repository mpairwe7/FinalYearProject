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
| 3 | Memory lifecycle: one episode per conversation, word-boundary topic tags on the English form, lg/sw fact cues, fact validity periods | G121 | **Shipped** |
| 4 | Sessions and channels: anonymous history across tabs, chat context carried into a call, multi-turn Luganda calls, no Gemini in the call path | G122 | **Shipped** |
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

## Phase 3 — what changed and why

**One episode per conversation (G121).** Each consented turn used to rewrite its
conversation's episode from that turn alone, so the summary was always the
latest turn's topic and `turn_count` stayed at 2. The episode is now derived
from the whole conversation each time: its topics in the order they were first
named ("Discussed VAT and PAYE."), and the count of turns actually logged plus
this one. Topic tags match whole words and read each turn's English form.

**Facts.** The rules read a Luganda or Swahili turn's English form as well as
the taxpayer's own words. A fact found only in the translation keeps 0.95 of
its confidence and says so in `extractor_model` (`rules-v1+en`), so an export
shows which facts rest on machine translation. Common Swahili and Luganda trade
words (duka, rejareja, kilimo, ujenzi, edduuka, obulimi…) map to industries.
Single-valued facts — taxpayer type, fiscal year, preferred language, detail
level — supersede the previous value, which is kept with `invalidated_at`, the
time it stopped being true, rather than overwritten.

**Where memory is written.** Streamed turns — the web client's — were never
written to long-term memory; only the LangGraph branch, off by default, wrote
them. They are now written once the reply is final. A call turn heard below
`RECEPTIONIST_MEMORY_MIN_ASR_CONF` (0.6 mean word probability) is answered but
not remembered: code-switched Luganda speech recognition is still error-prone,
and a mishearing must not become a fact about the caller. Working memory's
"last topic" is the topic the taxpayer named, not the role that answered.

**Found live, fixed in the same phase.** Two defects only the live stack showed:

- *Memory could never start.* `_load_personalization_state` returned nothing
  for a consented taxpayer with no profile and no memory yet (there was nothing
  to put in the prompt), and every memory write checks that state — so a new
  user's turns were never remembered. It now returns the consent state either
  way; a consented turn is therefore never cache-served, so each can be kept.
- *Questions were stored as facts.* "Do I need to pay PAYE for my employees?"
  became "registered for PAYE": the PAYE, WHT and CIT rules fired on any
  mention. A registration is now recorded only when the taxpayer states it
  ("I'm registered for…", "we pay / file / deduct…"), never from a clause
  that asks.

**Verified live (GPU stack, 2026-10-06).** A signed-in, consented taxpayer's
three streamed turns (Swahili, Swahili, Luganda) produced one episode —
"Discussed VAT, TIN registration and PAYE.", 3 turns — the retail fact from
"duka la rejareja", and working memory naming the topic (VAT). Withdrawing
consent erased all three tiers.

## Phase 4 — what changed and why

**Anonymous history across tabs (G122).** A signed-out taxpayer's history was
bound to the analytics session id, which lives in sessionStorage. A new tab, or
the browser reopened, began a new session. A conversation reopened from the
sidebar then continued with no server-side history while the page still showed
it. Every chat-bound request (chat, streaming, voice turns, uploads, report
downloads, escalation) now sends one browser-wide chat session id
(`lib/chatSession.ts`). It is kept in localStorage beside the conversations and
moves forward while it is used. It is replaced after
`NEXT_PUBLIC_CONVERSATION_TTL_DAYS` (7) without use, when the server has already
deleted everything bound to it, and whenever the taxpayer clears their chat
history. Analytics keep their per-tab id. The voice-first page sent the
conversation id itself as the session, so the id alone unlocked the history; it
now sends the chat session too.

**The browser keeps conversations no longer than the server.** The browser kept
50 conversations with no expiry, while the server deletes one 7 days after its
last turn. A conversation older than that is dropped when the page loads,
unless the taxpayer pinned it.

**A call carries the chat it came from.** A call started from the chat sends
the chat's conversation id. If the caller owns that chat (their account, or the
chat session when signed out), the call starts on the chat's task. It also gets
a short English account of the chat: the tax topics named, the taxpayer type,
the figures mentioned, and the current task. It never gets the taxpayer's own
words. The account is stored on the call (`voice_calls.parent_conversation_id`,
`chat_context`). The officer's brief reads it as context and never cites it as
a turn. Before, the call opened with only the chat's language as a hint.

**Luganda calls are multi-turn.** English and Swahili call turns go through the
chat pipeline, which has the call's history. The Luganda path turned each
question into an English search query on its own, so "kiki ekyetaagisa?" ("what
is needed?") was searched with no subject. The query is now made with the call's
last two exchanges (their English forms) and the chat's account.

**No hosted model on a call.** The receptionist still fell back to Gemini for
the officer brief, the call summary, the Luganda query and the Luganda answer,
against the 2026-09-30 local-only rule. These now run on Sunflower only, with
deterministic fallbacks when vLLM is down, and `RECEPTIONIST_BRIEF_MODEL` is
gone. A call's English or Swahili turn reaches the chat service, whose own cloud
fallbacks are switched by environment. Until now only the GPU stack's settings
kept them off. `providers/scope.py` holds a call turn to local models whatever
chat allows: no LLM fallback chain, FAQ judge, Gemini, Workers AI or Sunbird
translation, and no dense-retrieval fallback. The scope travels as a context
variable. The speech translation pool did not copy the context, so its submit
now does.

**Verified live (GPU stack, 2026-10-07).**

- *Chat to call:* an anonymous chat turn ("…my turnover is above 150 million
  shillings. Do I need to register for VAT?") set the chat's task to VAT
  registration. A real call start on `WS /v1/calls/stream` naming that chat
  stored this account: "Tax domains discussed: Value Added Tax (VAT). Financial
  figures mentioned: 150 million. Current task: VAT registration." The account
  did not contain the taxpayer's words, and the call opened on the
  VAT-registration task. The same call start with another session's id carried
  nothing.
- *Luganda follow-up:* Sunflower on vLLM turned "Kiki ekyetaagisa?" ("what is
  needed?") into "What is required?" alone, and into "What is required to
  register for VAT?" with the call so far.
- *Local-only translation:* a call turn's Swahili translation came from local
  Sunflower, with every hosted provider replaced by a tripwire.
- *Two tabs:* the phase-4 frontend build drove the live API in a browser. A
  question in one tab and a follow-up in a new tab sent the same chat session
  and conversation, and the server stored both turns under one session.

That follow-up ("What documents do I need for it?") was correctly read as VAT
registration but answered with customs import documents (commercial invoice,
bill of lading, packing list). This is an answer-quality defect outside this
phase, registered as G124.

## Open findings (phase 5)
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

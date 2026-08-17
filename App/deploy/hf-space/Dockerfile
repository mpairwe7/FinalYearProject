# Hugging Face Docker Space — second deployment pipeline for the URA Chatbot,
# independent of Crane Cloud. It reuses the SAME prebuilt image that Crane Cloud
# runs (built by .github/workflows/ura-chatbot-build-push.yml and pushed to
# Docker Hub), so there is no separate build — HF just pulls and runs it.
#
# The image bundles nginx + uvicorn (FastAPI) + Next.js under supervisord and
# listens on 8080; HF routes to it via `app_port: 8080` in README.md. Runtime
# config (Sunbird/Gemini/Cloudflare secrets, SPEECH_ENABLED, …) is supplied as
# Space secrets, mirroring the Crane Cloud app's environment.
#
# sha-459533c makes OIDC_ROLE_CLAIM fall back instead of locking staff out. The
# override used to be exclusive, so setting it stopped the default claim probes
# entirely — a typo, or setting it before the provider mapping exists, resolved
# every officer to "public" and refused every staff member with nothing in the
# logs to say why. The configured claim is still authoritative when PRESENT
# (including present-but-empty, which legitimately means "no roles"); only an
# absent claim falls through, and it logs a warning naming the variable.
#
# This Space now runs with OIDC_ROLE_CLAIM=https://ura.go.ug/roles, matching the
# Auth0 Post-Login Action that copies event.authorization.roles into a namespaced
# claim. Assigning an Auth0 role does not by itself put it in the token, and the
# RBAC toggle emits permissions rather than role names, so the action is required
# — deployed AND attached to the post-login trigger.
#
# sha-9c69489 is the first image with staff sign-in and the operations
# dashboards: /signin (+/signin/callback), /admin and /agent, gated by StaffGuard.
# Auth is real OIDC — authorization-code + PKCE S256 to Auth0, RS256 tokens
# verified against the tenant's live JWKS. The backend verifies and never issues,
# so there is no password form and no client secret anywhere; the public client's
# issuer, client id and audience are baked into the bundle by design (build args,
# from repo variables). Runtime verification vars are Space variables. See
# docs/STAFF_AUTH_DEPLOYMENT.md.
#
# Five defects were found by testing against a real provider rather than a mock,
# every one of which made sign-in impossible on any deployment:
#   * The app's own CSP blocked the token exchange. connect-src was 'self', which
#     was right while every browser call went through the /api/* rewrite — but the
#     callback exchanges its code directly with the provider, because a public
#     client has no secret for a proxy to protect. Failed as a bare "NetworkError".
#   * Roles were read from a flat `role` claim no real provider sends. Keycloak
#     nests them under realm_access.roles, Auth0 uses permissions or a namespaced
#     claim. Every genuine officer resolved to "public" and was refused by the
#     dashboard they had just been granted.
#   * An unmapped role value reached AuthUser.role, which is a Literal, so a valid
#     sign-in 500'd instead of degrading. Unknown vocabulary is now "public".
#   * Endpoints were hardcoded to Keycloak's paths. Auth0 serves /authorize and
#     /oauth/token, Entra and Okta differ again; they are now discovered from
#     /.well-known/openid-configuration.
#   * No `audience` was sent, so Auth0 returns an OPAQUE access token — not a JWT
#     at all — and the backend rejected every sign-in as malformed.
#
# Also fixes the frontend lint gate, red since the dashboards landed (four
# set-state-in-effect violations, resolved by not keeping derived state, plus a
# raw <a> to a route), and trims whitespace from OIDC config: the issuer had been
# set with a trailing space, invisible in a variables list, which encodes into the
# discovery host as %20.
#
# Verified: both an admin and a staff identity 15/15 end-to-end against a live
# Keycloak 26 (re-run after every refactor), 1285 backend tests, 101 frontend
# tests, and the dashboards' layout on chromium + mobile-chrome in CI.
#
# sha-b970c3d adds two fixes found by verifying 20 FAQs against this Space:
#   * The Sources block was built from a set, so it had no order and the
#     document the answer came from was not reliably first — citations and
#     sources disagreed. Now ordered by hit rank, so the two agree.
#   * A document chunk could outrank the curated FAQ row that answers the
#     question word for word: "What are business records?" was answered from an
#     agriculture-sector PDF. A verbatim FAQ counterpart is now promoted ahead
#     of chunk prose, keyed on symmetric term-equivalence so a near-miss like
#     "withholding tax exemption" cannot displace "withholding tax".
#
# 20-FAQ battery on the previous image: 18/20 acceptable, 19/20 cited the
# originating file, 0 abstentions, median 3.4s.
#
# sha-fdd680b restores the roll withheld earlier, with the abstention defect fixed
# and verified. The withheld image answered off-domain questions: should_abstain
# reads hit_relevance, which is None when only an RRF score exists, and the
# sparse-only sidecar has no reranker — so it took its "cannot assess relevance"
# branch and answered anyway. Safe over 499 curated FAQ rows behind the question-F1
# gate; not over 7,000+ raw chunks, where BM25 returns something for any query.
#
# The retriever now stamps each hit with an IDF-weighted lexical coverage score and
# should_abstain uses it when no reranker score exists. Floor 0.50, calibrated over
# 8 off-domain and 130 on-domain questions. Measured on this image:
#   off-domain  5 of 6 abstain   (was 0 of 6)
#   on-domain   8 of 8 answer    (no regression)
#
# Two other defects the battery surfaced, both fixed here:
#   * 956 of 7,035 chunks carried pymupdf4llm's "picture [W x H] intentionally
#     omitted" placeholder, which was once served as an answer. Stripped at export;
#     the corpus is now 7,874 documents.
#   * correct_spelling substituted without word boundaries, so "withholdin" ->
#     "withholding" rewrote "withholding" as "withholdingg", dropping the term from
#     the BM25 query. "What is withholding tax?" was answered from VAT and EFRIS
#     documents; it now answers from the withholding-tax material. Pre-existing.
#
# Known residual: "What is the capital of France?" still answers, from an EAC
# tax-cases compendium where both content terms genuinely occur. Four lexical
# variants were measured and none separate it; closing it needs a reranker or a
# judge over the candidates. See app/retriever.py LEXICAL_RELEVANCE_FLOOR.
#
# Pin to an immutable sha tag. Bump this line to roll the Space to a newer
# image.
#
# sha-41b7296 adds two in-container sidecars under supervisord — Qdrant for
# retrieval and Redis for the response cache — plus a readiness gate so the
# backend does not start before Qdrant is serving. Without the gate the first
# minute after a roll answered from 499 FAQ rows: the retriever initialised
# before the sidecar and fell back to keyword until a later request re-inited it.
#
# Redis matters here for a non-obvious reason. Both cache backends returned None
# whenever no dense model was present, and this image ships no torch, so the
# response cache missed on EVERY request — it was not slow, it was off. cache.py
# now has a model-free exact tier (casefolded, punctuation-stripped query +
# locale), which is what makes a cache worth running here. Measured in the built
# image: 211ms cold, 27ms on a repeat, 25ms on a normalised variant, with the LLM
# disabled — the saving is larger once generation is in the path. Redis also
# gives the 4 uvicorn workers a shared cache and cluster-wide rate limits.
#
# It also carries the biggest retrieval change this Space has had: it runs Qdrant
# as an in-container sidecar against a collection baked into the image, so the
# whole corpus is finally reachable here. Retrieval was BM25 over the 499 FAQ
# rows via the keyword fallback — the 300+ MB of URA handbooks under Data/pdfs
# and the monthly crawl were invisible, because the FAQ path scores candidates on
# an F1 against each FAQ's own question and a document chunk has no question.
# The collection now holds 7,943 documents: 499 FAQ, 6 teacher-QA, 7,035 PDF
# chunks (135 unique PDFs, 11 byte-identical duplicates skipped) and 403 crawl
# chunks. /ready reports retrieval_mode "hybrid" instead of "keyword", and
# questions answerable only from the handbooks now cite them.
#
# The sidecar serves SPARSE-ONLY (BM25). That is a necessity, not a compromise: a
# BM25 query vector is computed from bm25_state.json in pure Python, whereas a
# dense query needs bge-m3 and this image ships no torch, so dense vectors would
# be unqueryable. Storage is 33 MB and the Qdrant binary ~85 MB.
#
# Chunks keep the structure the hierarchical chunker recovers — heading trail as
# the citation locator, rate tables kept atomic — and carry a fiscal_year, so
# among near-identical passages the newer edition wins and a repealed rate cannot
# silently evict the one in force.
#
# It also carries PR #264: inline [n] citation markers are stripped
# from the answer body — a pill landing on a lead-in read as a stray number
# ("Core services include: 1" directly above a list). References stay in the
# Sources block, and the strip covers Copy and Listen too so the marker does
# not reach the clipboard or get voiced mid-sentence.
#
# It also carries PRs #261 #262. Retrieved answers are rendered as
# prose a person can scan: the corpus writes lists as prose, so "What services
# does URA provide?" arrived as one 600-character sentence holding nine
# services. Prose enumerations and one-line numbered procedures now become real
# lists, `--` becomes an em dash, a trailing citation no longer strands itself
# as a floating superscript, and sources show "About URA" rather than
# ura_about_ura_faqs.csv. Verified across all 506 answers: 102 reformatted,
# none lossy. #261 pins the FAQ coverage gate with the measurement showing why
# lowering it takes wrong answers from 1 in 6 off-domain questions to 5 in 6.
#
# It also carries PR #259: a question asked in Luganda shares no terms
# with the English FAQ corpus, so BM25 had nothing to score and ALL 12 Luganda
# golden questions returned zero candidates. They are now translated to English
# and retried — lazily, only after the untranslated attempt comes back empty,
# so English is byte-identical and pays no translation latency. 5 of the 12 are
# rescued. A static-embedding dense path was built for this and rejected on its
# numbers: it cost 14-26 points of Hit@1 at every fusion weight, and its
# cosines ranked "buy cheap flight tickets to Dubai" above every genuine
# rescue.
#
# It also carries PR #256: the composer has ONE primary action that
# changes job — Enter voice mode while the box is empty, Send once there is
# text — with the mic beside it for dictation. The Voice and Narrate
# checkboxes are gone; entering voice mode turns narration on by itself.
# That freed enough of the row to give every composer control a full 44x44
# touch target, and fixed a 9px toolbar overflow that predated it. Also
# raises the consent banner's body text off a 3.6:1 contrast failure.
#
# It also carries PR #255: Workers AI calls now try the AI Gateway and
# fall through to Cloudflare's direct REST API on a transport failure. The
# Space cannot reach gateway.ai.cloudflare.com at all (TLS handshake dies with
# SSL: UNEXPECTED_EOF_WHILE_READING), and the relay built for that points at
# the Crane Cloud host and answers 500. api.cloudflare.com is a two-label
# hostname like every other host this Space reaches successfully, so it is
# worth a try; the fallback is correct either way.
#
# It also carries PRs #247 #248 #250 #252, which make native TTS
# actually work rather than merely reachable, unblock speech INPUT for the two
# locales that could never use it, and stop the API reporting invented timings:
#
#   * /v1/tts and /v1/asr both promise timing metadata, and in both only the
#     LOCAL backends computed it from real audio. sunbird_cloud advertised a
#     hardcoded 22050 Hz for 24 kHz audio with duration 0 and latency 0 after
#     two network hops; edge_tts derived duration with the raw-PCM16 formula
#     despite returning MP3, reporting a 4.15s phrase as 0.52s; and the three
#     cloud ASR paths left duration_s and rtf null — which is every
#     non-English locale, since those all reach Sunbird. Metadata is now read
#     from the container's own header (WAV parse, or one MPEG frame header),
#     with no decode. Verified against a frame count of a real clip: 0.0%
#     error where the old formula was 87% out.
#
#   * /v1/asr and /v1/voice/chat validated `language` with ^[a-z]{2}$. Two of
#     the five locales the picker offers, nyn and ach, have no two-letter
#     639-1 code, so both endpoints returned 400 before doing any work.
#     /v1/voice/chat is the whole audio->ASR->MT->LLM->TTS->audio pipeline,
#     so voice conversation was impossible in Runyankole and Acholi, not
#     merely degraded. Every request model in models.py already allowed
#     {2,3} — one of them documents its field as "(en, lg, sw, nyn, ach)",
#     naming the exact codes the routes refused.
#
#   * /tasks/audio/speech serves orpheus-3b-tts, which addresses voices by
#     CATALOG TAG. Our table held numeric speaker ids read off the
#     spark-tts model card — a different model — so every non-English
#     request named a voice the serving model does not have. #243 fixed the
#     endpoint and turned 405 into 422; this fixes the payload. Measured
#     against the live API before deploying: numeric ids return 422 naming
#     `body.voice`, catalog tags return 200 with fetchable WAV audio, for
#     all four local languages.
#   * Luganda had been returning "All TTS backends failed" — total silence,
#     the only locale with no working fallback. Its edge-tts voice was
#     en-UG-MaleNeural and edge-tts has no Uganda locale at all, so that
#     stage always raised "No audio was received". Now en-KE-ChilembaNeural.
#   * Repairing that edge voice would have QUIETLY DOWNGRADED Luganda:
#     with stage ② working, edge answers `lg` with an English voice and
#     returns, so Sunbird's native Luganda speaker is never reached — the
#     repair would have hidden the regression it caused. Sunbird now runs
#     before edge-tts for every non-English locale. edge stays beneath it,
#     so a Sunbird outage still degrades to audio, not silence.
#   * STT sent `language` only when truthy, but the field is required —
#     confirmed live: omitting it returns 422 "Field required".
#   * Sunbird errors now log the response body. A bare status cost a full
#     build-and-deploy cycle to diagnose; the body names the bad field.
#
# The previous pin, sha-bf22059, carried PRs #234 #235 #238 #241 #243 #245:
#
#   * Speech now honours all five locales the picker offers. The same
#     two-entry {"en","lg"} language map had been copy-pasted into three
#     places, so Swahili/Runyankole/Acholi audio went to Sunbird tagged as
#     English (garbage transcripts), and TTS "succeeded" with an English
#     voice before the chain could reach Sunbird's native Runyankole (243)
#     and Swahili (246) speakers.
#   * The gradient-mesh canvas is back, with every text-bearing surface
#     kept opaque so the old ~1.9:1 gold-on-gradient failure cannot return.
#   * Mobile interaction feel: press feedback, spring easing, 44px targets
#     on coarse pointers, scroll containment.
#   * The one corpus file that could never be indexed (ura_faqs.csv — the
#     loader's glob requires a topic segment it lacks) had its 18 unique
#     AEOI rows merged into the existing exchange_of_information tag.
#   * The mobile composer no longer clips its send button. Two causes: a
#     44px min-width from the touch-target pass overflowed the row, and
#     underneath that the toolbar already overflowed by 9px because the
#     Voice and Narrate toggles were spending 173px of a 370px row on
#     padding. Verified on a Pixel 7 with both the narrowest ("EN") and
#     widest ("NYN") locale codes.
#   * Acholi finally gets its own voice. It had been falling back to an
#     English one on the belief that Sunbird ships no Acholi speaker —
#     wrong: that was read off this repo's own speaker table, not their
#     catalogue. Speaker 241 is Acholi female, corroborated by the three
#     IDs already configured matching that catalogue exactly, gender
#     included. All five offered locales now reach a native voice.
#   * Sunbird retired /tasks/modal/tts and /tasks/modal/stt. Every call to
#     the former had been answering 405, so non-English narration was dead
#     in production — English hid it, because edge-tts serves English
#     before Sunbird is reached. Migrated to /tasks/audio/speech and
#     /tasks/audio/transcriptions. Native voices are now preferred by
#     ORDER rather than by removing the other backends, so a Sunbird
#     outage degrades to a generic voice instead of returning silence.
#   * The URA ghost logo is behind the transcript again — globals.css had
#     always painted it; chatv2 had only switched it off.
#   * The language selector moved from the composer toolbar to the header.
#     It is a session-level setting, and the composer row had 25px of
#     slack against a 63px control; the header had 138px.
# sha-8649917 routes by language: English generates on gemini-3.7-flash with
# gemini-3.5-flash-lite behind it, and lg/nyn/ach/teo/lgg/sw are refused before
# any network call so they stay on the Sunbird tier that actually speaks them.
#
# Gemini had to become reachable from this Space first, or the change would
# have shipped inert. gemini_generate went ONLY through gateway.ai.cloudflare.com
# — the host this Space cannot reach at all — so routing English to Gemini would
# have failed on every call. It now tries generativelanguage.googleapis.com,
# which is not Cloudflare and is the same infrastructure every Sunbird TTS clip
# is already fetched from.
#
# Two fallbacks operate at different layers, and the first cut only had one:
# a withdrawn model returns HTTP 404, not a connection error, so it sailed past
# the transport fallback and nothing recovered. Model-level retry now covers
# 404/429/5xx; 400/401/403 raise immediately, since another model repeats them.
#
# The locale guard defaults to ALLOW when no locale is passed — which made it
# inert on the one path that serves production chat. _llm_cloud_fallback did not
# pass a locale, so every gateway test passed while Luganda still reached Gemini
# where it mattered. The check now sits in that function's guard condition,
# before try_consume_gemini_call() (which spends the free-tier allowance) and as
# a condition rather than a raise, because the except below records a breaker
# failure — enough Luganda turns would have opened the circuit for English.
#
# Requires Space SECRETS (not variables — writing them as variables is what
# caused the CONFIG_ERROR before): GEMINI_MODEL=gemini-3.7-flash and
# GEMINI_FALLBACK_MODEL=gemini-3.5-flash-lite. GEMINI_MODEL already existed as a
# secret holding the old model, so it overrode the new code default.
#
# Verified live before shipping: English reaches 3.7-flash; a withdrawn primary
# recovers on 3.5-flash-lite in 4.0s; a malformed request raises instead of
# retrying; lg/nyn/ach/sw are refused with no call made. 1338 backend tests.
#
# Also present but DORMANT: _judge_rescue, a last-resort model judge behind
# FAQ_JUDGE_ENABLED (default false). Measured, and the measurements say leave it
# off: on the Luganda turns it was built to rescue it now recovers 0 of 3,
# because translate-then-retrieve already absorbed the recoverable ones, and it
# fires on only 2 of 200 well-formed English questions.
#
# sha-167c5cb decouples Gemini from Cloudflare. is_gemini_configured() required
# all four Cloudflare values, because Gemini once reached Google only through
# the AI Gateway — so this Space, which cannot reach Cloudflare at all, reported
# "Gemini is not configured" while holding a valid key, and every caller skipped
# the Gemini branch silently. These are guard conditions, not failures, so there
# was no log line: production served raw retrieved passages with nothing to say
# why. Found by reading the Space's own logs after the previous roll and seeing
# no Gemini line of any kind, success or failure.
#
# sha-138f8ea sizes the Gemini output budget for a THINKING model. gemini-3.x
# flash bill reasoning tokens against maxOutputTokens before emitting a single
# answer token, so the old 512 truncated every production reply. The truncated
# text then failed _looks_truncated and the chain walked all three Workers AI
# models against a Cloudflare host this deployment cannot reach — a good Gemini
# answer discarded at the cost of a ~25s round trip. Logs: "looks truncated
# (41 chars)", "(84 chars)". Measured live: 512 -> 337 chars truncated,
# 2048 -> 455 chars complete.
#
# This roll is also the first on which English generation is actually LIVE.
# The routing shipped correct but dormant behind three gates that all fail
# silently, because they are guard conditions rather than errors:
#   LLM_FALLBACK_BACKEND was empty            -> now the secret is "gemini"
#   is_gemini_configured() demanded Cloudflare -> decoupled in sha-167c5cb
#   FLAG_CLOUDFLARE_FALLBACK defaults False    -> now the secret is "true"
# Nothing in the logs said any of this; the only symptom was that every reply
# was a raw retrieved passage.
#
# Confirmed in the Space's own logs on the previous roll:
#   "Gemini: gateway.ai.cloudflare.com unreachable (ConnectError);
#    trying Google directly"
#   "LLM fallback via Gemini succeeded"
# So the AI Gateway really is dead from here and the direct
# generativelanguage.googleapis.com route added in sha-8649917 is what carries
# English. Luganda replies stayed byte-identical to the pre-change passages,
# which is the guard holding.
#
# sha-ec06de0 fixes two defects found by verifying the routing against this
# Space.
#
# The locale policy was enforced on the Gemini branch ONLY. Execution fell
# through to the Workers AI chain underneath it, so a Luganda turn still walked
# all three Cloudflare models. It looked harmless purely because this Space
# cannot reach Cloudflare — every attempt failed. The moment that host became
# reachable, Workers AI would have answered Luganda. A guard that holds only
# while a dependency is broken is not a guard. The predicate is now named for
# the capability (cloud_generation_allowed_for) and both branches check it.
#
# The other defect threw away every Gemini answer. Production served corpus
# excerpts under "Here's the most relevant guidance I found..." even though
# Gemini had answered correctly and cited its sources — and faithfulness_score
# read 1.0 the whole time, because it scores the REVISED text. Three causes,
# all in what counts as a citable CLAIM, none in what counts as SUPPORT:
#   * a model told to cite "like [1]" writes "[1, 3]", which the citation
#     readers (shaped \[(\d{1,3})\]) do not match at all — so a cited sentence
#     parsed as uncited AND unsupported. Now normalised once, where generated
#     text enters the system, which also stops a literal "[1, 3]" reaching the
#     rendered answer through the frontend's marker stripper.
#   * the model's own context disclaimer ("the provided context does not
#     contain ...") scored as an uncited factual claim.
#   * its closing pleasantry: "happy to help" was exempt, "happy to assist" was
#     not; "i don't have enough" was exempt, "i do not have enough" was not.
#
# Measured against live Gemini on 8 questions: 0/8 revised, was 3/8. Safety
# counterweight on 6 bad answers (contradicted rate, invented amounts and
# deadlines, uncited and mis-cited inventions): 0 missed, 0 good blocked.
# 1360 backend tests, 104 frontend tests.
#
# Known gap, deliberately NOT changed here: sunbird.translate_from_english()
# has no callers, so a Luganda question is translated to English, answered from
# the English corpus, and returned IN ENGLISH. That is why lg and en replies
# are byte-identical in every probe.
#
# sha-5159e35 routes every Ugandan language Sunbird covers to Sunbird, not just
# Luganda. Two configuration defects sent four of them to Gemini instead:
#
#   * LOCALE_TO_SUNBIRD had no entry for teo or lgg, while TRANSLATION_LANGUAGES
#     listed both as supported — so every caller resolving a locale through that
#     table got None and fell through. Easy to miss because those locale codes
#     EQUAL their Sunbird codes: anything passing the raw string through
#     unmapped worked, and only the lookups broke.
#   * the tier order gated on `"lg" in (source, target)`, so only Luganda led
#     with Sunbird. Runyankole, Acholi, Ateso and Lugbara led with Gemini, which
#     answers — so Sunbird was never reached for languages it is trained on.
#
# Measured on this Space before the fix: en->lg 3.2s on sunbird_cloud, while
# en->nyn 17.7s, en->ach 18.1s and en->teo 18.4s all went to gemini_flash.
# Swahili deliberately still leads with Gemini — it has a Sunbird TTS voice but
# their translate endpoint does not serve it.
#
# The same table feeds SPEECH-TO-TEXT with an "eng" default, so the missing
# entries meant Ateso and Lugbara audio was submitted TAGGED AS ENGLISH and
# transcribed by an English model — confident nonsense nothing downstream can
# detect. That path is fixed by the same change.
#
# Also set on this Space: SUNBIRD_FALLBACK_API_TOKEN, which was absent. The
# account-failover path in _post was dead code here — one account, no second
# chance — despite the token existing in .env all along.
#
# sha-9c0f594 stops _post retrying a Sunbird TIMEOUT on the same account. It
# retried every httpx.HTTPError, and that includes timeouts — but a timeout
# means the model is slow right now (usually a cold start), so the retry buys
# an identical wait for the same answer.
#
# Measured across 8 live TTS calls: median 7.2s, max 79.1s. _cloud_call bounds
# the caller at SPEECH_CLOUD_DEADLINE_S (40s), so the 79.1s call was abandoned
# and TTS fell through to edge-tts — which has no Ugandan voice and narrated
# Luganda in en-US-AriaNeural. The retry meant to add resilience was causing the
# exact failure this tier exists to prevent. Worst case drops from
# SUNBIRD_TIMEOUT x SUNBIRD_RETRIES x 2 accounts (120s) to 60s. Transport errors
# (connection reset, DNS blip) are still retried — genuinely transient.
#
# This depends on a SECOND ACCOUNT existing. SUNBIRD_FALLBACK_API_TOKEN was
# absent from this Space while sitting in .env all along, so the failover path
# was dead code here — with one account, "fail over instead of retrying" would
# have meant failing outright. That secret is now set and was verified
# standalone first (eng->lug translate, lg TTS, eng->teo).
#
# sha-c1f8d99 adds account creation and a settings surface, and passes a
# 2026 mobile/tablet audit.
#
# /signup is the same OAuth 2.1 + PKCE redirect /signin already used, plus a
# registration hint (prompt=create for Entra/Google/Curity, screen_hint=signup
# for Auth0 — both sent, providers ignore what they don't recognise). It needs
# self-registration enabled at the provider or it just shows the login screen.
# Settings (opened from the sidebar's new account block or the header menu) is
# five tabs, every control backed by a real endpoint rather than a preference
# with nowhere to go: General writes the existing theme/locale stores, Voice
# writes the narration voice used by /v1/tts, Tax profile is
# GET/PUT /v1/me/profile, Privacy & data covers /v1/me/consents plus the UDPA
# 2019 pair (GET /v1/me/export, DELETE /v1/me — both confirmed first), and
# Account shows identity/role/tenant with sign in/up/out. None of it gates the
# assistant: chat, documents and voice all still work signed out, and every
# auth entry point says so.
#
# Three defects surfaced by testing this against a live browser rather than
# just the component tests:
#   * globals.css's `:root[data-theme="light"] a` (and its :hover) is more
#     specific than a plain `.chatv2 .thing`, so every accent button built as
#     an anchor rendered navy text on its own navy fill in light mode — and
#     six more controls turned an unreadable teal on hover. Anchor-based
#     controls now name their element to win the cascade; every hover states
#     its own colour rather than inheriting the global one.
#   * The settings dialog's open/focus effect depended on `onClose`, a fresh
#     closure every render, so it re-ran continuously and pulled focus back to
#     the panel — arrow-keying the tab list moved once and went dead. Split
#     into a focus-on-open effect keyed on `open` alone and a separate key trap.
#   * The OIDC callback sent every non-staff account back to /signin with
#     nowhere else to go. It now honours a returnTo the /signup leg sets, so a
#     taxpayer who just registered lands in the assistant, not on a dead end.
#
# Also a full mobile/tablet pass across a 12-point device matrix (a 280px
# foldable cover screen through a 1366px iPad Pro landscape, both
# orientations): the settings switch, the header/rail auth pair and several
# settings buttons were under the Apple HIG/Material 3 44px touch floor;
# fixing the switch specifically needed technique, not just a size bump —
# interactions.css's existing `(pointer: coarse)` rule stretches every button
# to 44px tall, which turned the 24px switch track into a 44px oval with the
# knob stranded at the top, so the track keeps its own height and the hit area
# moves to an invisible ::after. Every settings `<select>`/`<input>` and the
# composer's own textarea were 13-15px, under the size that makes iOS Safari
# zoom on focus and not zoom back out. The touch-sizing rules had also landed
# earlier in the cascade than the component rules they were meant to override,
# which made them dead code. A phone turned sideways (844x390) left the
# landing hero 3px taller than the viewport. The settings sheet and the
# sidebar's account block now both reserve env(safe-area-inset-bottom) at the
# bottom edge, which on a modern phone is the home indicator.
#
# Verified: 130/130 frontend unit tests, clean tsc/eslint, axe WCAG 2.1 AA
# clean on every settings tab and both auth pages in both themes, zero
# targets under the 44px touch floor and zero sub-16px form fields across the
# full device matrix, 62/67 Playwright (5 failures are a documented Firefox/
# StaffGuard environment gap in the sandbox this was built in, not the code).
#
# sha-6332de0 gives every language its own narration voice, and fixes the
# reason that could not work before.
#
# `sunbird.text_to_speech` took only a locale and looked its speaker up from
# TTS_VOICES[locale], so the `voice` a caller sent to /v1/tts was dropped before
# the request was built. English hid it — edge-tts serves English and takes a
# voice name directly — so picking a voice appeared to work while lg/nyn/ach/sw
# silently always used one speaker.
#
# The catalog has far more than the one speaker per language the code used, and
# every tag it documents is now offered: 8 Luganda, 5 Acholi, 5 Runyankole, 2
# Swahili, plus English. Each was confirmed against the live
# /tasks/audio/speech before shipping — 21 requests, 21 with fetchable audio, 0
# rejected. That check is the point rather than diligence for its own sake: an
# unusable tag is not a loud failure but a voice a person can select and never
# hear, because the request 400s and the chain degrades to an English speaker
# reading Luganda.
#
# A requested speaker is validated against its own locale's catalog. A tag from
# another language is NOT forwarded — orpheus either rejects it or synthesises
# the wrong language, both worse than quietly using the right default — and
# neither are the Piper/edge names `synthesize` fills in when no voice is named.
# GET /v1/speech/voices serves the catalog so the client never hardcodes it;
# this Space HAS a Sunbird key, so it reports sunbird_configured true and all
# 24 voices are selectable here.
#
# Frontend: the store moved from one global `voiceId` to `voiceByLocale` with a
# persist migration, because a voice only exists inside its own language and one
# id meant switching language carried a speaker that could not synthesise the
# new one. The Voice tab lists speakers per language, current language first,
# each with its own preview button. Voices are numbered rather than given
# invented personas — the catalog exposes opaque tags and nothing about the
# people behind them.
#
# Also in this image, from the settings work that preceded it: the refused-token
# state is now reachable (an expired token returns 401, which the UI read as
# "backend unreachable" and reported backwards), the tax-profile panel no longer
# claims that fields nothing reads will shape answers, consent receipts show a
# loading state instead of five toggles that all read "off" while in flight, and
# withdrawing personalization — which purges server-side memory — asks first.
#
# Verified: 1381 backend tests, 743 repo-root, 133 frontend, 12 Playwright; the
# picker driven in a browser on desktop and phone; axe clean and every control
# at or above the touch floor across phone/tablet/desktop in both themes.
#
# sha-3990a66 is a correctness and consistency pass over the sign-up/settings
# and per-language-voice work, plus the repo's security guards.
#
# User-facing:
#   * /signin was titled "Staff sign-in". Five entry points now send ordinary
#     taxpayers there — the sidebar account block, the header pair, the landing
#     note, Settings > Account and the tax-profile panel — so most people
#     arriving were told, in the largest text on the page, that they were in the
#     wrong place. Staff use the same OIDC redirect; what differs is the role
#     their token carries, resolved after the exchange.
#   * The analytics switch in Privacy & data snapshotted consent on open and
#     never subscribed. The privacy banner writes the same key and renders ABOVE
#     the dialog, so accepting it with settings open left the switch reading
#     "off" while storage said otherwise. It subscribes now.
#   * Narration and voice mode were coupled one way only: the composer set both,
#     the settings toggle wrote narration alone, so turning it off there left
#     voice mode listening with nothing to say back. Both go through one owner.
#
# Consistency: the landing page linked to /signin and /signup with raw anchors
# while four other call sites used next/link; the OIDC callback still inlined
# role checks instead of the shared table; accountApi typed the withdraw
# response as a list when the backend returns a {purpose: bool} dict; and the
# consent panel filtered receipts on a field the endpoint never returns (it
# filters server-side) while describing them as "dated" when no date is shown.
#
# Security guards, which had been failing on pre-existing findings and are now
# green — none by blanket suppression:
#   * Bandit B324: SHA-1 in the phrase cache is declared usedforsecurity=False.
#     It keys an in-process cache, so it needs speed and stability rather than
#     collision resistance, and this keeps it working on FIPS-restricted builds.
#   * Semgrep ura-llm01 flagged Whisper ASR's model.generate(input_features) as
#     unguarded user input to an LLM. The argument is a mel spectrogram, so
#     there is no prompt to inject into; the transcript it produces IS guarded,
#     downstream in service.generate() whose first step is InputGuard.check().
#     Suppressed per-line, rule named, reasoning recorded.
#   * detect-secrets: two keyword false positives given the hook's documented
#     pragma.
#
# Verified: 1381 backend tests, 743 repo-root, 134 frontend, 62/67 Playwright
# (the 5 are a documented Firefox/StaffGuard sandbox gap, each cause confirmed
# to match the baseline), axe clean, all contrast at or above 4.5:1, and the
# voice picker driven in a browser on desktop and phone. All 8 CI workflows
# green on this commit.
#
# ---------------------------------------------------------------------------
# sha-255a56f — the English narration voices, live dictation, and one speech
# surface.
#
# The defect this rolls out was found by running the e2e suite against the
# previous image: /v1/speech/voices advertised four English voices and the
# settings picker offered all four, but _synthesize_edge_tts ignored the
# requested one, so every English voice played as en-US-AriaNeural. Nothing
# failed, because every frontend spec mocks /api/** and the backend tests stop
# at the layer above the edge call. scripts/probe_deploy.py exists so that class
# of thing cannot hide again — unmocked HTTP at a running deployment, and the
# one to run after this roll.
#
# Also here: dictation now transcribes live (interimResults + continuous were
# off, so the composer filled in one jump at the end); the Dictate button did
# nothing at all where the browser has no Speech API, which is Firefox and
# Chromium on several platforms; the recording panel's checkmark said "Send
# recording" while dictating, when it inserts text rather than sending; and the
# header's "Voice ready" pill and voice-overlay mic are gone, leaving one place
# to start speaking.
#
# Verified: 1393 backend tests, 743 repo-root, 141 frontend, e2e at the
# documented 64/5 firefox baseline, the voice specs confirmed on real chromium,
# axe clean, and the live probe at 58/60 against the previous image — the two
# failures being exactly the defect this image fixes. All 7 CI workflows green
# on this commit.
#
# ---------------------------------------------------------------------------
# sha-5b1b61b — the sign-in redirect works again.
#
# The defect this rolls out was reported from a real browser console:
#
#   Could not start the sign-in redirect: Unexpected token '<', "<!DOCTYPE "...
#   is not valid JSON
#
# The service worker's "network-first for pages" branch had no origin check, so
# it also caught cross-origin GETs — including the OIDC discovery document. It
# re-issued that request from the worker's context, where a worker keeps the CSP
# it was *installed* with: an instance registered before the provider's origin
# was added to connect-src blocked it, while the page's own current CSP allowed
# it. The worker then fell back through its cache to /offline.html, so the OIDC
# client received an HTML page where it expected JSON. Hence the parse error,
# and hence its intermittence — only browsers carrying a stale worker saw it.
#
# Cross-origin requests are now handed back to the browser untouched, and
# CACHE_NAME goes v4 to v5 so anything stored under the old rules is dropped.
# /sw.js is served max-age=0 and the worker calls skipWaiting() + clients.claim(),
# so the replacement takes effect on the next page load rather than lingering.
#
# Also carried: the ASR chain no longer reports "All ASR backends failed" when a
# transcriber ran and simply heard nothing, so /v1/voice/chat reaches its "No
# speech detected" branch instead of naming five internal components at someone
# who paused; and the composer says "Didn't catch that" rather than closing the
# recording panel onto an empty box.
#
# Verified against this exact image pulled and run locally: 113/113 on
# scripts/probe_deploy.py (TTS and STT across all five languages) and 34/34 on
# e2e/verify-auth-flows.mjs (PKCE S256, state, prompt=create, a forged callback
# refused, zero CSP violations). All CI green on this commit.
#
# ---------------------------------------------------------------------------
# sha-d8037c8 — registration works from inside this Space.
#
# Reported here: "dev-….auth0.com refused to connect." on the registration CTA.
#
# The authorize request was correct — reproduced against the real
# huggingface.co page, it reaches Auth0's /u/signup with prompt=create intact.
# It arrived in the wrong kind of window. This Space embeds the app in an
# iframe, the flow called window.location.assign, and that navigates the frame;
# identity providers refuse to be framed, so Auth0's X-Frame-Options turned the
# redirect into "refused to connect".
#
# Escaping in place is not available: the Space's iframe sandbox grants
# allow-popups and allow-popups-to-escape-sandbox but NOT allow-top-navigation.
# A popup can reach the provider, but window.open returns null there, so it
# inherits no sessionStorage — and the PKCE verifier lives there, so the
# callback would have nothing to exchange.
#
# So the flow moves to a new top-level tab that starts it itself, with its own
# sessionStorage, exactly as an unframed visit does. The frame stays put and
# says where it went.
#
# Verified under this Space's real sandbox tokens: framed click opens a
# top-level tab, that tab auto-starts and lands on /authorize. Unframed is
# unchanged, 34/34 on e2e/verify-auth-flows.mjs. All 8 CI workflows green.
#
# ---------------------------------------------------------------------------
# sha-3431644 — the pod now reports its own network reality, and /ready stops
# overstating retrieval.
#
# Answering "does Cloudflare work from this Space?" turned out to be impossible
# from outside. The only evidence was one line — "gateway.ai.cloudflare.com
# unreachable (ConnectError)" — which appears only when a request happens to
# take that path, and Crane Cloud exposes no log API at all. Both deployments
# generate (Space ~20s, Crane Cloud ~8s) and nobody could follow up the gap.
#
# So startup now states whether DoH activated and which of
# gateway.ai.cloudflare.com, api.cloudflare.com, the Gemini endpoint and
# VLLM_BASE_URL's host actually resolve. DNS is the right thing to report: it is
# the failure mode doh_resolver already exists for — outbound 443 open, no
# upstream resolver, every hostname failing before TCP connect. Milliseconds, no
# credentials, and never fatal.
#
# /ready also stops reporting "hybrid" when dense retrieval is off. It read
# _retriever_ready alone and never the retriever's own _sparse_only, so a
# collection with no embedder stamp — what this Space logs — still looked
# healthy. It reports "sparse" now, which likely also explains abstention on
# questions the knowledge base should cover.
#
# Also carried since sha-d8037c8: workflow slot answers are read for intent
# rather than string-matched ("as an individual", "organization", "individul",
# "sole trader"), prompted translation guards its own input, and the ASR chain
# distinguishes hearing nothing from every backend failing.
#
# All CI green on this commit; merged to main as 34316440cc.
#
# Pin to an immutable sha tag. Bump this line to roll the Space to a newer
# image.
FROM landwind/ura-chatbot:sha-126089b

# Documented for clarity; HF uses README.md `app_port`. The base image's
# CMD/ENTRYPOINT (supervisord → nginx:8080) is inherited unchanged.
EXPOSE 8080

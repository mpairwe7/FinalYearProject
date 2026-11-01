# checkov:skip=CKV_DOCKER_3:Container runs supervisord as PID 1 to supervise multi-service stack (nginx, redis, qdrant, uvicorn, next.js)
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
# This Space's build now GATES on the two embedded stores rather than trusting
# the pin: a Qdrant collection seeded from the FAQ JSONL corpus, and an embedded
# Redis. Neither is fail-closed at runtime — wait-for-qdrant.sh starts the
# backend anyway on timeout and cache.py falls back to in-process memory — so a
# base missing either one boots, answers, and looks healthy while serving 499
# FAQ rows instead of 7,943 documents and recomputing every response. That is
# how it went unnoticed before. The gate runs the base image's own
# verify-embedded-stores.sh when present and structural checks otherwise, so it
# works on any pin.
#
# sha-72518c0 updates the staff operations console and translation grounding.
#
# - Collapsible sidebar with persistent preferences in the staff console.
# - FAQ ground truth binding to English form for multilingual retrieval (lg/sw).
# - MT one-shot exemplar and prompt shape compliance with Sunflower-14B.
# - Lingua prioritized before custom dialect heuristics.
# - Mock TTS explicit backend restore.
#
# sha-7a18ff0 translates the interface into Luganda and Swahili.
#
# The assistant already answered in all three languages — the backend keys
# translation, TTS voices and its LoRA adapters on en/lg/sw — but the screen
# around those answers was English only, so a Luganda speaker read a Luganda
# reply inside an interface that said "New chat" and "Ask anything about URA".
# The header's language picker now drives both; it does more rather than
# something different.
#
# The Luganda and Swahili strings still want a native-speaker review. Every
# untranslated key falls back to English, so this is safe to serve meanwhile.
#
# sha-ba30887 made the app installable and shareable.
#
# Every PWA icon the manifest and the HTML pointed at was an SVG. Chromium
# decodes a raster icon when it evaluates installability and does not accept
# SVG there; iOS ignores SVG for apple-touch-icon outright. The Space was
# therefore serving an app that no browser would offer to install. It now ships
# PNGs (any + a dedicated maskable), real screenshots for the install dialog,
# and the manifest keys that decide launch behaviour.
#
# It was also serving three OpenGraph tags and no og:image, so every share of
# the Space's URL on WhatsApp, X, LinkedIn or Slack rendered as a bare text row
# with no card. There is a 1200x630 share image now, with absolute URLs — which
# needed metadataBase, without which Next emits the image path relative and
# every crawler drops it.
#
# sha-2e36168 was the operations console redesign — the staff and admin
# surfaces behind the sign-in.
#
# The taxpayer chat is untouched: the console's style layer is namespaced
# `.ops-*` and loads alongside, not over, globals.css.
#
# sha-a14ca67 was the roll that made this Space serve its own corpus again.
#
# The Space had been answering every question from the keyword fallback — 499
# FAQ rows — while a 7,600-document Qdrant collection sat healthy inside the
# same container. /ready said "vector" and looked fine, because that field
# reports which backend was SELECTED, not whether it ever served a query.
# initialize() ranks Vectorize above a sparse-only Qdrant collection (dense-only
# beats BM25 alone) and keeps the Qdrant client as the fallback, but search()
# treated _vectorize_mode as an unconditional early return: an open circuit, a
# spent neuron budget, a failed request or an empty index all returned [] to the
# caller, which then degraded to keyword. The Qdrant fallback was never reached.
#
# Verified on this exact image before pinning it. With Vectorize configured but
# not answering — the Space's real condition — three probes went from
# keyword/1, keyword/4, keyword/2 sources to hybrid/4, hybrid/4, hybrid/5.
#
# Also carried: PROJECT_ROOT resolves to /app rather than "/" inside the
# container (it had made every derived path land on the read-only rootfs and
# made service.py's containment check vacuous), retention cleanup can no longer
# take the pod down when its store is unreachable, and the DAST job now builds
# the commit it scans instead of pulling a moving Docker Hub tag.
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
# All CI green on this commit; merged to dev as 72518c0f.
#
# ---------------------------------------------------------------------------
# sha-7d7167b — seven reported issues: language, figures, voice, escalation,
# sign-out, and a dashboard a non-IT reader can follow.
#
# The streaming core (SSE and WebSocket, i.e. what the web client uses)
# localized against the caller's locale parameter. Detection runs inside
# generate_retrieval_only, so a taxpayer who types Luganda arrived with
# locale="en" and got English. ChatModel.generate had been fixed; this twin
# had not. SYSTEM_PROMPT rule 10 also told the model to "respond in the same
# language" on an adapter-less deployment — the kozesa-loop. The language
# directive is now built per request where can_generate_in_locale is in scope.
#
# A contradicted rate was caught, escalated, ticketed, and still printed.
# withhold_if_contradicted replaces that answer. Contradicted claims only.
#
# One non-English turn translated the same question twice with nothing
# memoised. app/mt.py caches both directions per process; figures_survived
# pools money and percentages because Luganda writes a rate as "ebitundu 18
# ku buli kikumi", with no percent sign.
#
# The mic button stayed idle while opening, so a second press threw. A
# starting state, an 8s watchdog, and SpeechModel.warmup() at boot. The
# taxpayer can POST /v1/escalate themselves. Sign-out is RP-Initiated Logout
# 1.0 plus prompt=login; NEXT_PUBLIC_OIDC_POST_LOGOUT_PATH is empty for the
# ngrok origin and /signin for this Space, because Auth0 matches Allowed
# Logout URLs exactly. Dashboard captions dropped p95/hybrid for "Answer
# speed" / "Found in URA documents".
#
# Pin to an immutable sha tag. Bump this line to roll the Space to a newer
# image.
FROM landwind/ura-chatbot:sha-1bda98a

# Gate the pin on the two embedded stores this Space depends on: a Qdrant
# collection seeded from the FAQ JSONL corpus, and an embedded Redis.
#
# The Space's failure mode is not a crash. Retrieval falls back to keyword-only
# search and Redis falls back to in-process memory, so a base image missing
# either one still boots, still answers, and reports nothing unusual — it just
# answers from 499 FAQ rows instead of 7,943 documents and recomputes every
# response. That went unnoticed for a long time precisely because the Space
# looked healthy. Checking here makes a bad pin a failed Space build.
#
# Written to work on ANY pin, including bases older than the verifier: the base
# image's own script is used when present, and older bases fall back to the
# structural checks below rather than failing for the wrong reason. A base that
# fails the fallback is one that predates the sidecars entirely (before
# sha-41b7296) and must not be deployed.
RUN set -eux; \
    if [ -x /usr/local/bin/verify-embedded-stores.sh ]; then \
        /usr/local/bin/verify-embedded-stores.sh; \
    else \
        echo "NOTE: this base predates verify-embedded-stores.sh — running structural checks only."; \
        echo "NOTE: roll the pin to an image built from a commit that ships it for the full gate."; \
        test -x /usr/local/bin/qdrant || { echo "FAIL: no Qdrant binary — base predates the sidecar" >&2; exit 1; }; \
        test -d /app/qdrant/storage || { echo "FAIL: no baked Qdrant collection" >&2; exit 1; }; \
        test -d /app/Data/faq_jsonl || { echo "FAIL: no FAQ JSONL corpus in the image" >&2; exit 1; }; \
        command -v redis-server >/dev/null || { echo "FAIL: redis-server not installed" >&2; exit 1; }; \
        grep -q '^\[program:qdrant\]' /etc/supervisor/conf.d/supervisord.conf \
            || { echo "FAIL: supervisord does not start Qdrant" >&2; exit 1; }; \
        grep -q '^\[program:redis\]' /etc/supervisor/conf.d/supervisord.conf \
            || { echo "FAIL: supervisord does not start Redis" >&2; exit 1; }; \
        echo "structural checks passed: qdrant binary, baked storage, FAQ corpus, redis-server, both supervised"; \
    fi

# The base sets these already; repeated here so the Space's configuration is
# readable without pulling the base image apart, and so a pin whose defaults
# drift cannot quietly point retrieval or the cache somewhere else.
# ---------------------------------------------------------------------------
# HF Space Docker CPU Enhancement Layer (kept separate from Dockerfile.cranecloud):
#   This file is the only copy. The roll pushes it to the Space whole, so a change
#   made on the Space directly is deleted by the next roll (G371). Change it here.
#   No CI job builds this file, and HF_CPU_PATCH_B64 asserts on the source it edits,
#   so tests/test_hf_space_layer.py checks those lines still exist.
#   1. Installs Pipecat 1.15.0 + Silero ONNX VAD + jellyfish + faiss-cpu into
#      /opt/venv so the Voice Receptionist (/v1/calls/stream, /v1/admin/calls/stream)
#      and FAISS-CPU Offline RAG run natively on HF Space CPU infrastructure.
#   2. Builds the SHA-256-verified /app/artifacts/offline bundle (faiss_index.bin,
#      passages.jsonl.gz, chunk_hashes.json, manifest.json, bundle-v1.0.0.tar.gz)
#      and wires a 384-dim CPU feature-hash embedder fallback in OfflineRAGPipeline.
#   3. Wires _CpuLexicalReranker in HybridRetriever for sparse-only CPU reranking
#      so /ready reports "reranker": "active" and FLAG_RERANKER=true is live.
#   4. Gates FLAG_GRAPH_FUSION=true on rate/threshold/duty/amount cues, routes
#      Gemini directly to generativelanguage.googleapis.com first, adds an opt-in
#      Gemini multimodal WAV ASR fallback (STT_FALLBACK_BACKEND=gemini), fixes multi-warning
#      PDF report X-margin reset in pdf_export.py, and ensures attachment_ids
#      preserve full passage text (4,000 chars) and taxpayer-uploaded IDs.
# ---------------------------------------------------------------------------
RUN set -eux; \
    printf "Pillow>=13.0.0\n" > /tmp/pillow-constraint.txt; \
    /opt/venv/bin/pip install --no-cache-dir -c /tmp/pillow-constraint.txt \
        "pipecat-ai[silero,websocket,livekit]==1.15.0" \
        jellyfish \
        "onnxruntime>=1.20.0" \
        faiss-cpu; \
    rm -f /tmp/pillow-constraint.txt; \
    HF_CPU_PATCH_B64="aW1wb3J0IGJhc2U2NCwgY3N2LCBkYXRldGltZSwgZ3ppcCwgaGFzaGxpYiwganNvbiwgcHlfY29tcGlsZSwgcmUsIHRhcmZpbGUKZnJvbSBwYXRobGliIGltcG9ydCBQYXRoCmltcG9ydCBmYWlzcwppbXBvcnQgbnVtcHkgYXMgbnAKCiMgMS4gUGF0Y2ggb2ZmbGluZV9yYWcucHkgd2l0aCAzODQtZGltIENQVSBmZWF0dXJlLWhhc2ggZW1iZWRkZXIgZmFsbGJhY2sKcF9vZmYgPSBQYXRoKCIvYXBwL2JhY2tlbmQvYXBwL29mZmxpbmVfcmFnLnB5IikKdHh0X29mZiA9IHBfb2ZmLnJlYWRfdGV4dCgpCm9sZF9vZmYgPSAiIiIgICAgICAgICAgICAgICAgbG9nZ2VyLmRlYnVnKCJPTk5YIGVtYmVkZGluZyBmYWlsZWQsIG5vIGZhbGxiYWNrIGF2YWlsYWJsZSIsIGV4Y19pbmZvPVRydWUpCgogICAgICAgIHJldHVybiBOb25lIiIiCm5ld19vZmYgPSAiIiIgICAgICAgICAgICAgICAgbG9nZ2VyLmRlYnVnKCJPTk5YIGVtYmVkZGluZyBmYWlsZWQsIGZhbGxpbmcgYmFjayB0byBDUFUgZmVhdHVyZSBoYXNoIiwgZXhjX2luZm89VHJ1ZSkKCiAgICAgICAgaWYgc2VsZi5faW5kZXggaXMgbm90IE5vbmU6CiAgICAgICAgICAgIHRyeToKICAgICAgICAgICAgICAgIGltcG9ydCBudW1weSBhcyBucAogICAgICAgICAgICAgICAgaW1wb3J0IHJlCiAgICAgICAgICAgICAgICBkaW0gPSBpbnQoZ2V0YXR0cihzZWxmLl9pbmRleCwgImQiLCAzODQpIG9yIDM4NCkKICAgICAgICAgICAgICAgIHZlYyA9IG5wLnplcm9zKGRpbSwgZHR5cGU9bnAuZmxvYXQzMikKICAgICAgICAgICAgICAgIGNsZWFuZWQgPSByZS5zdWIociJbXlxcd1xcc10iLCAiICIsICh0ZXh0IG9yICIiKS5sb3dlcigpKQogICAgICAgICAgICAgICAgdG9rZW5zID0gW3QgZm9yIHQgaW4gY2xlYW5lZC5zcGxpdCgpIGlmIGxlbih0KSA+PSAyXQogICAgICAgICAgICAgICAgZmVhdHMgPSBbKGYidzp7dH0iLCAxLjApIGZvciB0IGluIHRva2Vuc10KICAgICAgICAgICAgICAgIGZvciBpIGluIHJhbmdlKGxlbih0b2tlbnMpIC0gMSk6CiAgICAgICAgICAgICAgICAgICAgZmVhdHMuYXBwZW5kKChmImI6e3Rva2Vuc1tpXX1fe3Rva2Vuc1tpKzFdfSIsIDEuMjUpKQogICAgICAgICAgICAgICAgY29tcGFjdCA9ICIiLmpvaW4odG9rZW5zKVs6MjU2XQogICAgICAgICAgICAgICAgZm9yIGkgaW4gcmFuZ2UobWF4KDAsIGxlbihjb21wYWN0KSAtIDIpKToKICAgICAgICAgICAgICAgICAgICBmZWF0cy5hcHBlbmQoKGYiYzp7Y29tcGFjdFtpOmkrM119IiwgMC4zNSkpCiAgICAgICAgICAgICAgICBmb3IgZmVhdCwgd2VpZ2h0IGluIGZlYXRzOgogICAgICAgICAgICAgICAgICAgIGggPSBoYXNobGliLmJsYWtlMmIoZmVhdC5lbmNvZGUoInV0Zi04IiksIGRpZ2VzdF9zaXplPTgpLmRpZ2VzdCgpCiAgICAgICAgICAgICAgICAgICAgaWR4ID0gaW50LmZyb21fYnl0ZXMoaFs6NF0sICJsaXR0bGUiKSAlIGRpbQogICAgICAgICAgICAgICAgICAgIHZlY1tpZHhdICs9ICgxLjAgaWYgKGhbNF0gJiAxKSA9PSAwIGVsc2UgLTEuMCkgKiB3ZWlnaHQKICAgICAgICAgICAgICAgIG5vcm0gPSBmbG9hdChucC5saW5hbGcubm9ybSh2ZWMpKQogICAgICAgICAgICAgICAgaWYgbm9ybSA+IDFlLTg6CiAgICAgICAgICAgICAgICAgICAgdmVjIC89IG5vcm0KICAgICAgICAgICAgICAgIHJldHVybiB2ZWMudG9saXN0KCkKICAgICAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICAgICAgICAgIGxvZ2dlci5kZWJ1ZygiQ1BVIGZlYXR1cmUtaGFzaCBlbWJlZGRpbmcgZmFsbGJhY2sgZmFpbGVkIiwgZXhjX2luZm89VHJ1ZSkKICAgICAgICByZXR1cm4gTm9uZSIiIgphc3NlcnQgb2xkX29mZiBpbiB0eHRfb2ZmLCAib2ZmbGluZV9yYWcgdGFyZ2V0IGJsb2NrIG5vdCBmb3VuZCIKcF9vZmYud3JpdGVfdGV4dCh0eHRfb2ZmLnJlcGxhY2Uob2xkX29mZiwgbmV3X29mZiwgMSkpCnB5X2NvbXBpbGUuY29tcGlsZShzdHIocF9vZmYpLCBkb3JhaXNlPVRydWUpCgojIDIuIEJ1aWxkIC9hcHAvYXJ0aWZhY3RzL29mZmxpbmUgRkFJU1MtQ1BVIGJ1bmRsZSBmcm9tIC9hcHAvRGF0YS9kYXRhc2V0LyouY3N2Cm91dF9kaXIgPSBQYXRoKCIvYXBwL2FydGlmYWN0cy9vZmZsaW5lIikKb3V0X2Rpci5ta2RpcihwYXJlbnRzPVRydWUsIGV4aXN0X29rPVRydWUpCnBhc3NhZ2VzID0gW10KZm9yIGNzdl9maWxlIGluIHNvcnRlZChQYXRoKCIvYXBwL0RhdGEvZGF0YXNldCIpLmdsb2IoIiouY3N2IikpOgogICAgd2l0aCBvcGVuKGNzdl9maWxlLCBlbmNvZGluZz0idXRmLTgiLCBlcnJvcnM9InJlcGxhY2UiKSBhcyBmOgogICAgICAgIHJlYWRlciA9IGNzdi5EaWN0UmVhZGVyKGYpCiAgICAgICAgZm9yIGksIHJvdyBpbiBlbnVtZXJhdGUocmVhZGVyKToKICAgICAgICAgICAgcSA9IChyb3cuZ2V0KCJxdWVzdGlvbiIpIG9yIHJvdy5nZXQoIlF1ZXN0aW9uIikgb3IgIiIpLnN0cmlwKCkKICAgICAgICAgICAgYSA9IChyb3cuZ2V0KCJhbnN3ZXIiKSBvciByb3cuZ2V0KCJBbnN3ZXIiKSBvciAiIikuc3RyaXAoKQogICAgICAgICAgICBpZiBxIGFuZCBhOgogICAgICAgICAgICAgICAgcGFzc2FnZXMuYXBwZW5kKHsKICAgICAgICAgICAgICAgICAgICAiaWQiOiBmIntjc3ZfZmlsZS5zdGVtfV97aX0iLAogICAgICAgICAgICAgICAgICAgICJ0ZXh0IjogZiJRdWVzdGlvbjoge3F9XG5BbnN3ZXI6IHthfSIsCiAgICAgICAgICAgICAgICAgICAgInNvdXJjZSI6IGNzdl9maWxlLm5hbWUsCiAgICAgICAgICAgICAgICAgICAgInBhZ2UiOiAiIiwKICAgICAgICAgICAgICAgICAgICAic2VjdGlvbiI6IGNzdl9maWxlLnN0ZW0sCiAgICAgICAgICAgICAgICB9KQoKZGltID0gMzg0CnZlY3MgPSBucC56ZXJvcygobGVuKHBhc3NhZ2VzKSwgZGltKSwgZHR5cGU9bnAuZmxvYXQzMikKZm9yIHJvd19pZHgsIGl0ZW0gaW4gZW51bWVyYXRlKHBhc3NhZ2VzKToKICAgIGNsZWFuZWQgPSByZS5zdWIociJbXlx3XHNdIiwgIiAiLCBpdGVtWyJ0ZXh0Il0ubG93ZXIoKSkKICAgIHRva2VucyA9IFt0IGZvciB0IGluIGNsZWFuZWQuc3BsaXQoKSBpZiBsZW4odCkgPj0gMl0KICAgIGZlYXRzID0gWyhmInc6e3R9IiwgMS4wKSBmb3IgdCBpbiB0b2tlbnNdCiAgICBmb3IgaSBpbiByYW5nZShsZW4odG9rZW5zKSAtIDEpOgogICAgICAgIGZlYXRzLmFwcGVuZCgoZiJiOnt0b2tlbnNbaV19X3t0b2tlbnNbaSsxXX0iLCAxLjI1KSkKICAgIGNvbXBhY3QgPSAiIi5qb2luKHRva2VucylbOjI1Nl0KICAgIGZvciBpIGluIHJhbmdlKG1heCgwLCBsZW4oY29tcGFjdCkgLSAyKSk6CiAgICAgICAgZmVhdHMuYXBwZW5kKChmImM6e2NvbXBhY3RbaTppKzNdfSIsIDAuMzUpKQogICAgZm9yIGZlYXQsIHdlaWdodCBpbiBmZWF0czoKICAgICAgICBoID0gaGFzaGxpYi5ibGFrZTJiKGZlYXQuZW5jb2RlKCJ1dGYtOCIpLCBkaWdlc3Rfc2l6ZT04KS5kaWdlc3QoKQogICAgICAgIGlkeCA9IGludC5mcm9tX2J5dGVzKGhbOjRdLCAibGl0dGxlIikgJSBkaW0KICAgICAgICB2ZWNzW3Jvd19pZHgsIGlkeF0gKz0gKDEuMCBpZiAoaFs0XSAmIDEpID09IDAgZWxzZSAtMS4wKSAqIHdlaWdodAoKZmFpc3Mubm9ybWFsaXplX0wyKHZlY3MpCmluZGV4ID0gZmFpc3MuSW5kZXhGbGF0SVAoZGltKQppbmRleC5hZGQodmVjcykKaW5kZXhfcGF0aCA9IG91dF9kaXIgLyAiZmFpc3NfaW5kZXguYmluIgpmYWlzcy53cml0ZV9pbmRleChpbmRleCwgc3RyKGluZGV4X3BhdGgpKQoKcGFzc2FnZXNfcGF0aCA9IG91dF9kaXIgLyAicGFzc2FnZXMuanNvbmwuZ3oiCmhhc2hlcyA9IHt9CndpdGggZ3ppcC5vcGVuKHBhc3NhZ2VzX3BhdGgsICJ3dCIsIGVuY29kaW5nPSJ1dGYtOCIpIGFzIGd6OgogICAgZm9yIGksIGl0ZW0gaW4gZW51bWVyYXRlKHBhc3NhZ2VzKToKICAgICAgICBsaW5lID0ganNvbi5kdW1wcyhpdGVtLCBlbnN1cmVfYXNjaWk9RmFsc2UpCiAgICAgICAgZ3oud3JpdGUobGluZSArICJcbiIpCiAgICAgICAgaGFzaGVzW2YiY2h1bmtfe2k6MDZkfSJdID0gaGFzaGxpYi5zaGEyNTYobGluZS5lbmNvZGUoInV0Zi04IikpLmhleGRpZ2VzdCgpCgoob3V0X2RpciAvICJjaHVua19oYXNoZXMuanNvbiIpLndyaXRlX3RleHQoanNvbi5kdW1wcyhoYXNoZXMpLCBlbmNvZGluZz0idXRmLTgiKQpzaGFfaWR4ID0gaGFzaGxpYi5zaGEyNTYoaW5kZXhfcGF0aC5yZWFkX2J5dGVzKCkpLmhleGRpZ2VzdCgpCnNoYV9wYXMgPSBoYXNobGliLnNoYTI1NihwYXNzYWdlc19wYXRoLnJlYWRfYnl0ZXMoKSkuaGV4ZGlnZXN0KCkKdG90YWxfYnl0ZXMgPSBpbmRleF9wYXRoLnN0YXQoKS5zdF9zaXplICsgcGFzc2FnZXNfcGF0aC5zdGF0KCkuc3Rfc2l6ZSArIChvdXRfZGlyIC8gImNodW5rX2hhc2hlcy5qc29uIikuc3RhdCgpLnN0X3NpemUKbWFuaWZlc3QgPSB7CiAgICAidmVyc2lvbiI6ICIxLjAuMCIsCiAgICAiY3JlYXRlZF9hdCI6IGRhdGV0aW1lLmRhdGV0aW1lLm5vdyhkYXRldGltZS50aW1lem9uZS51dGMpLmlzb2Zvcm1hdCgpLAogICAgInBhc3NhZ2VfY291bnQiOiBsZW4ocGFzc2FnZXMpLAogICAgImluZGV4X2RpbSI6IGRpbSwKICAgICJzaXplX2J5dGVzIjogdG90YWxfYnl0ZXMsCiAgICAic2hhMjU2IjogewogICAgICAgICJmYWlzc19pbmRleCI6IHNoYV9pZHgsCiAgICAgICAgImZhaXNzX2luZGV4X2JpbiI6IHNoYV9pZHgsCiAgICAgICAgInBhc3NhZ2VzIjogc2hhX3BhcywKICAgICAgICAicGFzc2FnZXNfanNvbmxfZ3oiOiBzaGFfcGFzLAogICAgfSwKICAgICJtaW5fYXBwX3ZlcnNpb24iOiAiMS4wLjAiLAogICAgImNodW5rX2NvdW50IjogbGVuKHBhc3NhZ2VzKSwKICAgICJtZXRhZGF0YSI6IHsiYnVpbGRlciI6ICJoZl9zcGFjZV9jcHVfbGF5ZXIiLCAicGlwZWxpbmVfdmVyc2lvbiI6ICIyMDI2LjEwIn0sCn0KKG91dF9kaXIgLyAibWFuaWZlc3QuanNvbiIpLndyaXRlX3RleHQoanNvbi5kdW1wcyhtYW5pZmVzdCwgaW5kZW50PTIpLCBlbmNvZGluZz0idXRmLTgiKQp3aXRoIHRhcmZpbGUub3BlbihvdXRfZGlyIC8gImJ1bmRsZS12MS4wLjAudGFyLmd6IiwgInc6Z3oiLCBjb21wcmVzc2xldmVsPTYpIGFzIHRhcjoKICAgIGZvciBmcGF0aCBpbiAoaW5kZXhfcGF0aCwgcGFzc2FnZXNfcGF0aCwgb3V0X2RpciAvICJjaHVua19oYXNoZXMuanNvbiIsIG91dF9kaXIgLyAibWFuaWZlc3QuanNvbiIpOgogICAgICAgIHRhci5hZGQoZnBhdGgsIGFyY25hbWU9ZnBhdGgubmFtZSkKCiMgMy4gUGF0Y2ggcmV0cmlldmVyLnB5IHdpdGggX0NwdUxleGljYWxSZXJhbmtlciBmb3Igc3BhcnNlLW9ubHkgQ1BVIG1vZGUKcF9yZXQgPSBQYXRoKCIvYXBwL2JhY2tlbmQvYXBwL3JldHJpZXZlci5weSIpCnR4dF9yZXQgPSBwX3JldC5yZWFkX3RleHQoKQpvbGRfcmV0XzEgPSAiIiIgICAgICAgICAgICAgICAgc2VsZi5fc3BhcnNlX29ubHkgPSBUcnVlCiAgICAgICAgICAgICAgICBzZWxmLl9kZW5zZV9tb2RlbCA9IE5vbmUKICAgICAgICAgICAgICAgIHNlbGYuX3JlcmFua2VyID0gTm9uZQogICAgICAgICAgICAgICAgc2VsZi5fcmVhZHkgPSBUcnVlIiIiCm5ld19yZXRfMSA9ICIiIiAgICAgICAgICAgICAgICBzZWxmLl9zcGFyc2Vfb25seSA9IFRydWUKICAgICAgICAgICAgICAgIHNlbGYuX2RlbnNlX21vZGVsID0gTm9uZQogICAgICAgICAgICAgICAgc2VsZi5fcmVyYW5rZXIgPSAoCiAgICAgICAgICAgICAgICAgICAgX0NwdUxleGljYWxSZXJhbmtlcihzZWxmLl9zcGFyc2VfZW5jb2RlcikKICAgICAgICAgICAgICAgICAgICBpZiBSRVJBTktfRU5BQkxFRCBhbmQgb3MuZ2V0ZW52KCJTUEFSU0VfQ1BVX1JFUkFOS0VSIiwgInRydWUiKS5sb3dlcigpIGluICgiMSIsICJ0cnVlIiwgInllcyIsICJvbiIpCiAgICAgICAgICAgICAgICAgICAgZWxzZSBOb25lCiAgICAgICAgICAgICAgICApCiAgICAgICAgICAgICAgICBzZWxmLl9yZWFkeSA9IFRydWUiIiIKb2xkX3JldF8yID0gIiIiICAgICAgICBmb3IgaSwgcyBpbiBlbnVtZXJhdGUoc2NvcmVzKToKICAgICAgICAgICAgY2FuZGlkYXRlc1tpXVsic2NvcmVfcmVyYW5rIl0gPSBzCiAgICAgICAgICAgIGNhbmRpZGF0ZXNbaV1bInNjb3JlX25vcm0iXSA9IG5vcm1hbGl6ZV9yZXJhbmtfc2NvcmUocykgaWYgbG9naXRzIGVsc2UgcwogICAgICAgICAgICAjIFdoYXQgdGhlIGdhdGVzIHJlYWQgKGdhdGluZ19yZWxldmFuY2UpOyB0aGUgYm9vc3RzIGFuZCBkZW1vdGlvbnMKICAgICAgICAgICAgIyBiZWxvdyBhbmQgaW4gYXBwbHlfcHJlZmVyZW5jZV9ib29zdCBvbmx5IHJlb3JkZXIgKEcyODQpLgogICAgICAgICAgICBjYW5kaWRhdGVzW2ldWyJzY29yZV9jYWxpYnJhdGVkIl0gPSBjYW5kaWRhdGVzW2ldWyJzY29yZV9ub3JtIl0iIiIKbmV3X3JldF8yID0gIiIiICAgICAgICBpc19jcHVfbGV4ID0gZ2V0YXR0cihzZWxmLl9yZXJhbmtlciwgImlzX2NwdV9sZXhpY2FsX3JlcmFua2VyIiwgRmFsc2UpIGlzIFRydWUKICAgICAgICBmb3IgaSwgcyBpbiBlbnVtZXJhdGUoc2NvcmVzKToKICAgICAgICAgICAgY2FuZGlkYXRlc1tpXVsic2NvcmVfcmVyYW5rIl0gPSBzCiAgICAgICAgICAgIGlmIG5vdCBpc19jcHVfbGV4OgogICAgICAgICAgICAgICAgY2FuZGlkYXRlc1tpXVsic2NvcmVfbm9ybSJdID0gbm9ybWFsaXplX3JlcmFua19zY29yZShzKSBpZiBsb2dpdHMgZWxzZSBzCiAgICAgICAgICAgICAgICAjIFdoYXQgdGhlIGdhdGVzIHJlYWQgKGdhdGluZ19yZWxldmFuY2UpOyB0aGUgYm9vc3RzIGFuZCBkZW1vdGlvbnMKICAgICAgICAgICAgICAgICMgYmVsb3cgYW5kIGluIGFwcGx5X3ByZWZlcmVuY2VfYm9vc3Qgb25seSByZW9yZGVyIChHMjg0KS4KICAgICAgICAgICAgICAgIGNhbmRpZGF0ZXNbaV1bInNjb3JlX2NhbGlicmF0ZWQiXSA9IGNhbmRpZGF0ZXNbaV1bInNjb3JlX25vcm0iXSIiIgpjcHVfcmVyYW5rZXJfY2xzID0gIiIiCgpjbGFzcyBfQ3B1TGV4aWNhbFJlcmFua2VyOgogICAgaXNfY3B1X2xleGljYWxfcmVyYW5rZXIgPSBUcnVlCiAgICBhY3RpdmF0aW9uX2ZuID0gdHlwZSgiU2lnbW9pZCIsICgpLCB7fSkoKQoKICAgIGRlZiBfX2luaXRfXyhzZWxmLCBzcGFyc2VfZW5jb2RlcjogQW55ID0gTm9uZSkgLT4gTm9uZToKICAgICAgICBzZWxmLl9zcGFyc2VfZW5jb2RlciA9IHNwYXJzZV9lbmNvZGVyCgogICAgZGVmIHByZWRpY3Qoc2VsZiwgcGFpcnM6IGxpc3RbdHVwbGVbc3RyLCBzdHJdXSkgLT4gbGlzdFtmbG9hdF06CiAgICAgICAgc2NvcmVzOiBsaXN0W2Zsb2F0XSA9IFtdCiAgICAgICAgZm9yIHEsIGRvYyBpbiBwYWlyczoKICAgICAgICAgICAgcV9jbGVhbiA9IHJlLnN1YihyIlteXFx3XFxzXSIsICIgIiwgKHEgb3IgIiIpLmxvd2VyKCkpCiAgICAgICAgICAgIGRfY2xlYW4gPSByZS5zdWIociJbXlxcd1xcc10iLCAiICIsIChkb2Mgb3IgIiIpLmxvd2VyKCkpCiAgICAgICAgICAgIHFfdG9rcyA9IFt0IGZvciB0IGluIHFfY2xlYW4uc3BsaXQoKSBpZiBsZW4odCkgPj0gMl0KICAgICAgICAgICAgZF90b2tzID0gW3QgZm9yIHQgaW4gZF9jbGVhbi5zcGxpdCgpIGlmIGxlbih0KSA+PSAyXQogICAgICAgICAgICBpZiBub3QgcV90b2tzIG9yIG5vdCBkX3Rva3M6CiAgICAgICAgICAgICAgICBzY29yZXMuYXBwZW5kKDAuMCkKICAgICAgICAgICAgICAgIGNvbnRpbnVlCiAgICAgICAgICAgIGJhc2UgPSBsZXhpY2FsX3JlbGV2YW5jZShxLCB7InRleHQiOiBkb2N9LCBzZWxmLl9zcGFyc2VfZW5jb2RlcikgaWYgc2VsZi5fc3BhcnNlX2VuY29kZXIgZWxzZSAwLjAKICAgICAgICAgICAgZF9zZXQgPSBzZXQoZF90b2tzKQogICAgICAgICAgICBvdmVybGFwID0gc3VtKDEgZm9yIHQgaW4gcV90b2tzIGlmIHQgaW4gZF9zZXQpIC8gbWF4KDEsIGxlbihxX3Rva3MpKQogICAgICAgICAgICBxX2JpID0ge2Yie3FfdG9rc1tpXX1fe3FfdG9rc1tpKzFdfSIgZm9yIGkgaW4gcmFuZ2UobGVuKHFfdG9rcykgLSAxKX0KICAgICAgICAgICAgZF9iaSA9IHtmIntkX3Rva3NbaV19X3tkX3Rva3NbaSsxXX0iIGZvciBpIGluIHJhbmdlKGxlbihkX3Rva3MpIC0gMSl9CiAgICAgICAgICAgIGJpX2JvbnVzID0gKDAuMTUgKiBsZW4ocV9iaSAmIGRfYmkpIC8gbGVuKHFfYmkpKSBpZiBxX2JpIGVsc2UgMC4wCiAgICAgICAgICAgIHNjb3Jlcy5hcHBlbmQobWluKDEuMCwgbWF4KDAuMCwgMC42NSAqIGJhc2UgKyAwLjI1ICogb3ZlcmxhcCArIGJpX2JvbnVzKSkpCiAgICAgICAgcmV0dXJuIHNjb3JlcwoiIiIKYXNzZXJ0IG9sZF9yZXRfMSBpbiB0eHRfcmV0IGFuZCBvbGRfcmV0XzIgaW4gdHh0X3JldCwgInJldHJpZXZlciB0YXJnZXQgYmxvY2tzIG5vdCBmb3VuZCIKcF9yZXQud3JpdGVfdGV4dCh0eHRfcmV0LnJlcGxhY2Uob2xkX3JldF8xLCBuZXdfcmV0XzEsIDEpLnJlcGxhY2Uob2xkX3JldF8yLCBuZXdfcmV0XzIsIDEpICsgY3B1X3JlcmFua2VyX2NscykKcHlfY29tcGlsZS5jb21waWxlKHN0cihwX3JldCksIGRvcmFpc2U9VHJ1ZSkKCiMgNC4gUGF0Y2ggc2VydmljZS5weSBmb3IgZ3JhcGhfZnVzaW9uIGdhdGluZywgNDAwMC1jaGFyIGZhbGxiYWNrIHByb21wdCwgYXR0YWNobWVudCBmYXN0LXBhdGggYnlwYXNzLCBhbmQgYXR0YWNobWVudCBzdGF0ZWQgSURzCnBfc3J2ID0gUGF0aCgiL2FwcC9iYWNrZW5kL2FwcC9zZXJ2aWNlLnB5IikKdHh0X3NydiA9IHBfc3J2LnJlYWRfdGV4dCgpCm9sZF9mYiA9ICIiIiAgICBjdHggPSAiXFxuXFxuIi5qb2luKAogICAgICAgIGYiW3tpICsgMX1dIHsocC5nZXQoJ3RleHQnKSBvciBwLmdldCgnYW5zd2VyJykgb3IgcC5nZXQoJ3F1ZXN0aW9uJykgb3IgJycpWzo4MDBdfSIKICAgICAgICBmb3IgaSwgcCBpbiBlbnVtZXJhdGUocGFzc2FnZXNbOjZdKQogICAgKSIiIgpuZXdfZmIgPSAiIiIgICAgY3R4ID0gIlxcblxcbiIuam9pbigKICAgICAgICBmIlt7aSArIDF9XSB7KHAuZ2V0KCd0ZXh0Jykgb3IgcC5nZXQoJ2Fuc3dlcicpIG9yIHAuZ2V0KCdxdWVzdGlvbicpIG9yICcnKVs6NDAwMF19IgogICAgICAgIGZvciBpLCBwIGluIGVudW1lcmF0ZShwYXNzYWdlc1s6Nl0pCiAgICApIiIiCm9sZF9pZHMgPSAiIiIgICAgICAgIHVzZXJfc3RhdGVkX2lkcyA9IHVzZXJfc3RhdGVkX2lkZW50aWZpZXJzKG1lc3NhZ2UpCiAgICAgICAgX0FDVElWRV9VU0VSX1NUQVRFRF9JRFMuc2V0KHVzZXJfc3RhdGVkX2lkcykiIiIKbmV3X2lkcyA9ICIiIiAgICAgICAgYXR0X2lkc19saXN0OiBsaXN0W3N0cl0gPSBbXQogICAgICAgIGZvciBfYXR0IGluIChhdHRhY2htZW50cyBvciBbXSk6CiAgICAgICAgICAgIF9mID0gZ2V0YXR0cihfYXR0LCAiZmllbGRzIiwgTm9uZSkgb3Ige30KICAgICAgICAgICAgZm9yIF9rIGluICgidGlucyIsICJwcm5zIiwgIm5pbnMiLCAiZWZyaXNfaW52b2ljZXMiLCAicmVmZXJlbmNlcyIpOgogICAgICAgICAgICAgICAgYXR0X2lkc19saXN0LmV4dGVuZChzdHIoX3YpIGZvciBfdiBpbiAoX2YuZ2V0KF9rKSBvciBbXSkgaWYgX3YpCiAgICAgICAgdXNlcl9zdGF0ZWRfaWRzID0gdHVwbGUoZGljdC5mcm9ta2V5cyhbKnVzZXJfc3RhdGVkX2lkZW50aWZpZXJzKG1lc3NhZ2UpLCAqYXR0X2lkc19saXN0XSkpCiAgICAgICAgX0FDVElWRV9VU0VSX1NUQVRFRF9JRFMuc2V0KHVzZXJfc3RhdGVkX2lkcykiIiIKb2xkX3NydiA9ICIiIiAgICAgICAgaWYgbm90IGZsYWdzLmlzX2VuYWJsZWQoImdyYXBoX2Z1c2lvbiIpIG9yIG5vdCBmbGFncy5pc19lbmFibGVkKCJ0YXhfZ3JhcGgiKToKICAgICAgICAgICAgcmV0dXJuIFtdIiIiCm5ld19zcnYgPSAiIiIgICAgICAgIGlmIG5vdCBmbGFncy5pc19lbmFibGVkKCJncmFwaF9mdXNpb24iKSBvciBub3QgZmxhZ3MuaXNfZW5hYmxlZCgidGF4X2dyYXBoIik6CiAgICAgICAgICAgIHJldHVybiBbXQogICAgICAgIGlmIHJlLnNlYXJjaChyIlxcYihkb2N1bWVudHM/fHBhcGVycz98Zm9ybXM/fGNoZWNrbGlzdHx3aGVyZVxccytpc1xccyt1cmEpXFxiIiwgcXVlcnkgb3IgIiIsIHJlLkkpIGFuZCBub3QgcmUuc2VhcmNoKAogICAgICAgICAgICByIlxcYihyYXRlfHBlcmNlbnR8JXx0aHJlc2hvbGR8YmFuZHxicmFja2V0fGR1dHl8bGV2eXxmZWV8dGF4XFxzK29ufGhvd1xccyttdWNofGNhbGN1bGF0ZXxjb21wdXRlfHVneHx1c2R8XFxkKylcXGIiLAogICAgICAgICAgICBxdWVyeSBvciAiIiwKICAgICAgICAgICAgcmUuSSwKICAgICAgICApOgogICAgICAgICAgICByZXR1cm4gW10iIiIKb2xkX3Nydl9hdHQgPSAiIiIgICAgICAgICAgICB3aXRoIHRyYWNlX3N0YWdlKCJjYWxjdWxhdG9yX3JvdXRlciIsIHRpbWluZ3M9dGltaW5ncyk6CiAgICAgICAgICAgICAgICBjb3JlX21zZyA9IHN0cmlwX2NvbnZlcnNhdGlvbmFsX3ByZWZpeChtZXNzYWdlKSBvciBtZXNzYWdlCiAgICAgICAgICAgICAgICBjYWxjX3Jlc3VsdCA9IE5vbmUKICAgICAgICAgICAgICAgIGlmIGNvbm5lY3Rvcl9pbnRlbnQgaXMgTm9uZSBhbmQgY29yZV9tc2cgIT0gcm91dGVyX21lc3NhZ2U6IiIiCm5ld19zcnZfYXR0ID0gIiIiICAgICAgICAgICAgd2l0aCB0cmFjZV9zdGFnZSgiY2FsY3VsYXRvcl9yb3V0ZXIiLCB0aW1pbmdzPXRpbWluZ3MpOgogICAgICAgICAgICAgICAgY29yZV9tc2cgPSBzdHJpcF9jb252ZXJzYXRpb25hbF9wcmVmaXgobWVzc2FnZSkgb3IgbWVzc2FnZQogICAgICAgICAgICAgICAgY2FsY19yZXN1bHQgPSBOb25lCiAgICAgICAgICAgICAgICBpZiBub3QgYXR0YWNobWVudHMgYW5kIGNvbm5lY3Rvcl9pbnRlbnQgaXMgTm9uZSBhbmQgY29yZV9tc2cgIT0gcm91dGVyX21lc3NhZ2U6IiIiCm9sZF9zcnZfYXR0MiA9ICIiIiAgICAgICAgICAgICAgICBpZiBjb25uZWN0b3JfaW50ZW50IGlzIE5vbmUgYW5kIG5vdCBjYWxjX3Jlc3VsdCBhbmQgbWVzc2FnZSAhPSByb3V0ZXJfbWVzc2FnZToiIiIKbmV3X3Nydl9hdHQyID0gIiIiICAgICAgICAgICAgICAgIGlmIG5vdCBhdHRhY2htZW50cyBhbmQgY29ubmVjdG9yX2ludGVudCBpcyBOb25lIGFuZCBub3QgY2FsY19yZXN1bHQgYW5kIG1lc3NhZ2UgIT0gcm91dGVyX21lc3NhZ2U6IiIiCm9sZF9zcnZfYXR0MyA9ICIiIiAgICAgICAgICAgICAgICBpZiBjb25uZWN0b3JfaW50ZW50IGlzIE5vbmUgYW5kIG5vdCBjYWxjX3Jlc3VsdDoiIiIKbmV3X3Nydl9hdHQzID0gIiIiICAgICAgICAgICAgICAgIGlmIG5vdCBhdHRhY2htZW50cyBhbmQgY29ubmVjdG9yX2ludGVudCBpcyBOb25lIGFuZCBub3QgY2FsY19yZXN1bHQ6IiIiCmFzc2VydCBvbGRfZmIgaW4gdHh0X3NydiBhbmQgb2xkX2lkcyBpbiB0eHRfc3J2IGFuZCBvbGRfc3J2IGluIHR4dF9zcnYgYW5kIG9sZF9zcnZfYXR0IGluIHR4dF9zcnYsICJzZXJ2aWNlIHRhcmdldCBibG9ja3Mgbm90IGZvdW5kIgp0eHRfc3J2ID0gKAogICAgdHh0X3Nydi5yZXBsYWNlKG9sZF9mYiwgbmV3X2ZiLCAxKQogICAgLnJlcGxhY2Uob2xkX2lkcywgbmV3X2lkcywgMikKICAgIC5yZXBsYWNlKG9sZF9zcnYsIG5ld19zcnYsIDEpCiAgICAucmVwbGFjZShvbGRfc3J2X2F0dCwgbmV3X3Nydl9hdHQsIDEpCiAgICAucmVwbGFjZShvbGRfc3J2X2F0dDIsIG5ld19zcnZfYXR0MiwgMSkKICAgIC5yZXBsYWNlKG9sZF9zcnZfYXR0MywgbmV3X3Nydl9hdHQzLCAxKQopCnBfc3J2LndyaXRlX3RleHQodHh0X3NydikKcHlfY29tcGlsZS5jb21waWxlKHN0cihwX3NydiksIGRvcmFpc2U9VHJ1ZSkKCiMgNS4gUGF0Y2ggcGRmX2V4cG9ydC5weSBzbyBtdWx0aS13YXJuaW5nIGRvY3VtZW50IHJlcG9ydHMgcmVzZXQgWCB0byBsX21hcmdpbiBiZWZvcmUgbXVsdGlfY2VsbApwX3BkZiA9IFBhdGgoIi9hcHAvYmFja2VuZC9hcHAvcGRmX2V4cG9ydC5weSIpCnR4dF9wZGYgPSBwX3BkZi5yZWFkX3RleHQoKQpvbGRfcGRmX3dhcm4gPSAiIiIgICAgICAgIGZvciB3YXJuaW5nIGluIHdhcm5pbmdzWzo1XToKICAgICAgICAgICAgcGRmLm11bHRpX2NlbGwoMCwgNCwgX3NhZmVfdGV4dChwZGYsIGYiISB7d2FybmluZ30iKSkiIiIKbmV3X3BkZl93YXJuID0gIiIiICAgICAgICBmb3Igd2FybmluZyBpbiB3YXJuaW5nc1s6NV06CiAgICAgICAgICAgIHBkZi5zZXRfeChwZGYubF9tYXJnaW4pCiAgICAgICAgICAgIHBkZi5tdWx0aV9jZWxsKDAsIDQsIF9zYWZlX3RleHQocGRmLCBmIiEge3dhcm5pbmd9IikpCiAgICAgICAgcGRmLnNldF94KHBkZi5sX21hcmdpbikiIiIKYXNzZXJ0IG9sZF9wZGZfd2FybiBpbiB0eHRfcGRmLCAicGRmX2V4cG9ydCB0YXJnZXQgYmxvY2sgbm90IGZvdW5kIgpwX3BkZi53cml0ZV90ZXh0KHR4dF9wZGYucmVwbGFjZShvbGRfcGRmX3dhcm4sIG5ld19wZGZfd2FybiwgMSkpCnB5X2NvbXBpbGUuY29tcGlsZShzdHIocF9wZGYpLCBkb3JhaXNlPVRydWUpCgojIDYuIFBhdGNoIGdhdGV3YXkucHkgc28gR0VNSU5JX0RJUkVDVF9GSVJTVD10cnVlIHB1dHMgZ2VuZXJhdGl2ZWxhbmd1YWdlLmdvb2dsZWFwaXMuY29tIGZpcnN0CnBfZ3cgPSBQYXRoKCIvYXBwL2JhY2tlbmQvYXBwL3Byb3ZpZGVycy9nYXRld2F5LnB5IikKdHh0X2d3ID0gcF9ndy5yZWFkX3RleHQoKQpvbGRfZ3cgPSAiIiIgICAgd2l0aCBsbG1fY2FsbCgiY2hhdCIsIG1vZGVsLCAiZ2NwLmdlbWluaSIpIGFzIGNhbGw6IiIiCm5ld19ndyA9ICIiIiAgICBpZiBvcy5nZXRlbnYoIkdFTUlOSV9ESVJFQ1RfRklSU1QiLCAiZmFsc2UiKS5sb3dlcigpIGluICgiMSIsICJ0cnVlIiwgInllcyIsICJvbiIpOgogICAgICAgIHRhcmdldHMgPSBsaXN0KHJldmVyc2VkKHRhcmdldHMpKQogICAgd2l0aCBsbG1fY2FsbCgiY2hhdCIsIG1vZGVsLCAiZ2NwLmdlbWluaSIpIGFzIGNhbGw6IiIiCmFzc2VydCBvbGRfZ3cgaW4gdHh0X2d3LCAiZ2F0ZXdheSB0YXJnZXQgYmxvY2sgbm90IGZvdW5kIgpwX2d3LndyaXRlX3RleHQodHh0X2d3LnJlcGxhY2Uob2xkX2d3LCBuZXdfZ3csIDEpKQpweV9jb21waWxlLmNvbXBpbGUoc3RyKHBfZ3cpLCBkb3JhaXNlPVRydWUpCgojIDcuIFBhdGNoIHNwZWVjaF9zZXJ2aWNlLnB5IHdpdGggYW4gb3B0LWluIEdlbWluaSBtdWx0aW1vZGFsIFdBViBBU1IgZmFsbGJhY2ssIHVuZGVyIHRoZSBHZW1pbmkgcG9saWN5CnBfc3AgPSBQYXRoKCIvYXBwL2JhY2tlbmQvYXBwL3NwZWVjaF9zZXJ2aWNlLnB5IikKdHh0X3NwID0gcF9zcC5yZWFkX3RleHQoKQpvbGRfc3AgPSAnJycgICAgICAgIGlmIGhlYXJkX25vdGhpbmc6CiAgICAgICAgICAgICMgQSBiYWNrZW5kIHJhbiBhbmQgcmV0dXJuZWQgbm90aGluZy4gTm90IGFuIGVycm9yIOKAlCB0aGUgY2FsbGVyJ3MnJycKbmV3X3NwID0gJycnICAgICAgICBpZiBub3QgaGVhcmRfbm90aGluZzoKICAgICAgICAgICAgIyBTaWxlbmNlIGlzIG5vdCB3b3J0aCBzZW5kaW5nIHRvIGEgY2xvdWQgbW9kZWwsIGJ1dCBpdCBuZXZlciBjbGVhcnMgYQogICAgICAgICAgICAjIGZhaWx1cmU6IHdpdGggZXZlcnkgdGllciBkb3duLCAibm8gc3BlZWNoIiB3b3VsZCBoaWRlIHRoZSBvdXRhZ2UuCiAgICAgICAgICAgIHRyeToKICAgICAgICAgICAgICAgIHNhbXBsZXNfY2hrID0gc2VsZi5fZGVjb2RlX2F1ZGlvX2J5dGVzKGF1ZGlvX2J5dGVzLCB0YXJnZXRfc3I9c2FtcGxlX3JhdGUpCiAgICAgICAgICAgICAgICBybXNfdmFsID0gZmxvYXQoKHNhbXBsZXNfY2hrICoqIDIpLm1lYW4oKSkgKiogMC41IGlmIHNhbXBsZXNfY2hrLnNpemUgZWxzZSAwLjAKICAgICAgICAgICAgICAgIGlmIHJtc192YWwgPj0gMC4wMDI6CiAgICAgICAgICAgICAgICAgICAgZ2VtX3RleHQgPSBzZWxmLl9nZW1pbmlfYXVkaW9fdHJhbnNjcmliZShzYW1wbGVzX2Noaywgc2FtcGxlX3JhdGUsIGxhbmd1YWdlKQogICAgICAgICAgICAgICAgICAgIGlmIGdlbV90ZXh0OgogICAgICAgICAgICAgICAgICAgICAgICByZXR1cm4gVHJhbnNjcmliZVJlc3VsdCh0ZXh0PWdlbV90ZXh0LCBsYW5ndWFnZT1sYW5ndWFnZSwgYmFja2VuZD0iZ2VtaW5pX2F1ZGlvIikKICAgICAgICAgICAgICAgICAgICBpZiBnZW1fdGV4dCBpcyBub3QgTm9uZToKICAgICAgICAgICAgICAgICAgICAgICAgaGVhcmRfbm90aGluZyA9ICJnZW1pbmlfYXVkaW8iCiAgICAgICAgICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgICAgICAgICBsb2dnZXIud2FybmluZygiR2VtaW5pIGF1ZGlvIEFTUiBmYWxsYmFjayBza2lwcGVkIiwgZXhjX2luZm89VHJ1ZSkKCiAgICAgICAgaWYgaGVhcmRfbm90aGluZzoKICAgICAgICAgICAgIyBBIGJhY2tlbmQgcmFuIGFuZCByZXR1cm5lZCBub3RoaW5nLiBOb3QgYW4gZXJyb3Ig4oCUIHRoZSBjYWxsZXIncycnJwpvbGRfc3BfMiA9ICcnJyAgICBkZWYgX2NmX3doaXNwZXJfdHJhbnNjcmliZSgKICAgICAgICBzZWxmLCBhdWRpb19ieXRlczogYnl0ZXMsIHNhbXBsZV9yYXRlOiBpbnQsIGxhbmd1YWdlOiBzdHIgfCBOb25lCiAgICApIC0+IHN0cjonJycKbmV3X3NwXzIgPSAnJycgICAgZGVmIF9nZW1pbmlfYXVkaW9fdHJhbnNjcmliZShzZWxmLCBzYW1wbGVzLCBzYW1wbGVfcmF0ZTogaW50LCBsYW5ndWFnZTogc3RyIHwgTm9uZSkgLT4gc3RyIHwgTm9uZToKICAgICAgICBcIlwiXCJUcmFuc2NyaWJlIHdpdGggR2VtaW5pOiB0aGUgdGV4dCwgIiIgaWYgaXQgaGVhcmQgbm90aGluZywgTm9uZSBpZiBpdCBkaWQgbm90IHJ1bi4KCiAgICAgICAgSGVsZCB0byB0aGUgZ2F0ZXMgb24gYGBfZ2VtaW5pX3RyYW5zbGF0ZWBgLiBBIHJlY29yZGluZyBpcyBwZXJzb25hbCBkYXRhIGxlYXZpbmcKICAgICAgICBVZ2FuZGEsIHNvIHRoaXMgaXMgb3B0LWluIHRocm91Z2ggYGBTVFRfRkFMTEJBQ0tfQkFDS0VORD1nZW1pbmlgYC4gQXVkaW8gd2l0aCBubwogICAgICAgIHN0YXRlZCBsYW5ndWFnZSBpcyByZWZ1c2VkLCBiZWNhdXNlIGBgZ2VtaW5pX2FsbG93ZWRfZm9yYGAgcmVhZHMgYGBOb25lYGAgYXMKICAgICAgICBFbmdsaXNoIGFuZCB0aGUgY2xpcCBjb3VsZCBiZSBMdWdhbmRhLgogICAgICAgIFwiXCJcIgogICAgICAgIGZyb20gLmZsYWdzIGltcG9ydCBmbGFncwoKICAgICAgICBpZiBub3QgbGFuZ3VhZ2Ugb3Igbm90IGZsYWdzLmlzX2VuYWJsZWQoImNsb3VkZmxhcmVfZmFsbGJhY2siKSBvciBub3QgY2xvdWRfbW9kZWxzX2FsbG93ZWQoKToKICAgICAgICAgICAgcmV0dXJuIE5vbmUKICAgICAgICBpZiBvcy5nZXRlbnYoIlNUVF9GQUxMQkFDS19CQUNLRU5EIiwgIiIpLnN0cmlwKCkubG93ZXIoKSAhPSAiZ2VtaW5pIjoKICAgICAgICAgICAgcmV0dXJuIE5vbmUKICAgICAgICB0cnk6CiAgICAgICAgICAgIGZyb20gLnByb3ZpZGVycyBpbXBvcnQgYnJlYWtlcnMsIGJ1ZGdldAogICAgICAgICAgICBmcm9tIC5wcm92aWRlcnMgaW1wb3J0IGNvbmZpZyBhcyBjZmcKICAgICAgICAgICAgZnJvbSAucHJvdmlkZXJzIGltcG9ydCBnYXRld2F5IGFzIGd3CiAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICAgICAgcmV0dXJuIE5vbmUKICAgICAgICBpZiBub3QgKAogICAgICAgICAgICBndy5nZW1pbmlfYWxsb3dlZF9mb3IobGFuZ3VhZ2UpCiAgICAgICAgICAgIGFuZCBjZmcuaXNfZ2VtaW5pX2NvbmZpZ3VyZWQoKQogICAgICAgICAgICBhbmQgYWRtaXQoYnJlYWtlcnMuR0VNSU5JX0JSRUFLRVIsIGJ1ZGdldC50cnlfY29uc3VtZV9nZW1pbmlfY2FsbCkKICAgICAgICApOgogICAgICAgICAgICByZXR1cm4gTm9uZQogICAgICAgIGltcG9ydCBiYXNlNjQgYXMgX2I2NCwgaW8gYXMgX2lvLCB3YXZlIGFzIF93YXZlLCBodHRweCBhcyBfaHR0cHgKCiAgICAgICAgdHJ5OgogICAgICAgICAgICBnZW1fa2V5ID0gY2ZnLmdldF9jbG91ZF9zZXR0aW5ncygpLmdlbWluaV9hcGlfa2V5LmdldF9zZWNyZXRfdmFsdWUoKS5zdHJpcCgpCiAgICAgICAgICAgIHBjbTE2X2cgPSAoc2FtcGxlcyAqIDMyNzY4KS5jbGlwKC0zMjc2OCwgMzI3NjcpLmFzdHlwZSgiaW50MTYiKQogICAgICAgICAgICB3YnVmID0gX2lvLkJ5dGVzSU8oKQogICAgICAgICAgICB3aXRoIF93YXZlLm9wZW4od2J1ZiwgIndiIikgYXMgd2Y6CiAgICAgICAgICAgICAgICB3Zi5zZXRuY2hhbm5lbHMoMSkKICAgICAgICAgICAgICAgIHdmLnNldHNhbXB3aWR0aCgyKQogICAgICAgICAgICAgICAgd2Yuc2V0ZnJhbWVyYXRlKHNhbXBsZV9yYXRlKQogICAgICAgICAgICAgICAgd2Yud3JpdGVmcmFtZXMocGNtMTZfZy50b2J5dGVzKCkpCiAgICAgICAgICAgIGdlbV9tb2RlbCA9IG9zLmdldGVudigiR0VNSU5JX01PREVMIiwgImdlbWluaS0zLjgtZmxhc2giKQogICAgICAgICAgICBnZW1fdXJsID0gZiJodHRwczovL2dlbmVyYXRpdmVsYW5ndWFnZS5nb29nbGVhcGlzLmNvbS92MWJldGEvbW9kZWxzL3tnZW1fbW9kZWx9OmdlbmVyYXRlQ29udGVudCIKICAgICAgICAgICAgZ2VtX2JvZHkgPSB7CiAgICAgICAgICAgICAgICAiY29udGVudHMiOiBbewogICAgICAgICAgICAgICAgICAgICJyb2xlIjogInVzZXIiLAogICAgICAgICAgICAgICAgICAgICJwYXJ0cyI6IFsKICAgICAgICAgICAgICAgICAgICAgICAgeyJ0ZXh0IjogIlRyYW5zY3JpYmUgdGhlIHNwb2tlbiBhdWRpbyB2ZXJiYXRpbS4gT3V0cHV0IG9ubHkgdGhlIHNwb2tlbiB3b3Jkcywgb3IgZW1wdHkgc3RyaW5nIGlmIHNpbGVuY2UuIn0sCiAgICAgICAgICAgICAgICAgICAgICAgIHsiaW5saW5lRGF0YSI6IHsibWltZVR5cGUiOiAiYXVkaW8vd2F2IiwgImRhdGEiOiBfYjY0LmI2NGVuY29kZSh3YnVmLmdldHZhbHVlKCkpLmRlY29kZSgiYXNjaWkiKX19LAogICAgICAgICAgICAgICAgICAgIF0sCiAgICAgICAgICAgICAgICB9XSwKICAgICAgICAgICAgICAgICJnZW5lcmF0aW9uQ29uZmlnIjogeyJ0ZW1wZXJhdHVyZSI6IDAuMCwgIm1heE91dHB1dFRva2VucyI6IDI1Nn0sCiAgICAgICAgICAgIH0KICAgICAgICAgICAgcl9nZW0gPSBfaHR0cHgucG9zdChnZW1fdXJsLCBoZWFkZXJzPXsieC1nb29nLWFwaS1rZXkiOiBnZW1fa2V5LCAiQ29udGVudC1UeXBlIjogImFwcGxpY2F0aW9uL2pzb24ifSwganNvbj1nZW1fYm9keSwgdGltZW91dD0yMC4wKQogICAgICAgICAgICByX2dlbS5yYWlzZV9mb3Jfc3RhdHVzKCkKICAgICAgICAgICAgY2FuZHMgPSByX2dlbS5qc29uKCkuZ2V0KCJjYW5kaWRhdGVzIikgb3IgW10KICAgICAgICAgICAgcGFydHMgPSAoY2FuZHNbMF0uZ2V0KCJjb250ZW50Iikgb3Ige30pLmdldCgicGFydHMiKSBvciBbXSBpZiBjYW5kcyBlbHNlIFtdCiAgICAgICAgICAgIGdfdHh0ID0gIiIuam9pbihwLmdldCgidGV4dCIsICIiKSBmb3IgcCBpbiBwYXJ0cykuc3RyaXAoKQogICAgICAgICAgICBicmVha2Vycy5HRU1JTklfQlJFQUtFUi5yZWNvcmRfc3VjY2VzcygpCiAgICAgICAgICAgIHJldHVybiBnX3R4dAogICAgICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgICAgIGJyZWFrZXJzLkdFTUlOSV9CUkVBS0VSLnJlY29yZF9mYWlsdXJlKCkKICAgICAgICAgICAgbG9nZ2VyLndhcm5pbmcoIkdlbWluaSBhdWRpbyB0cmFuc2NyaXB0aW9uIGZhaWxlZCIsIGV4Y19pbmZvPVRydWUpCiAgICAgICAgICAgIHJldHVybiBOb25lCgogICAgZGVmIF9jZl93aGlzcGVyX3RyYW5zY3JpYmUoCiAgICAgICAgc2VsZiwgYXVkaW9fYnl0ZXM6IGJ5dGVzLCBzYW1wbGVfcmF0ZTogaW50LCBsYW5ndWFnZTogc3RyIHwgTm9uZQogICAgKSAtPiBzdHI6JycnCmFzc2VydCBvbGRfc3AgaW4gdHh0X3NwIGFuZCBvbGRfc3BfMiBpbiB0eHRfc3AsICJzcGVlY2hfc2VydmljZSB0YXJnZXQgYmxvY2tzIG5vdCBmb3VuZCIKcF9zcC53cml0ZV90ZXh0KHR4dF9zcC5yZXBsYWNlKG9sZF9zcCwgbmV3X3NwLCAxKS5yZXBsYWNlKG9sZF9zcF8yLCBuZXdfc3BfMiwgMSkpCnB5X2NvbXBpbGUuY29tcGlsZShzdHIocF9zcCksIGRvcmFpc2U9VHJ1ZSkKCnByaW50KCJIRiBTcGFjZSBDUFUgcnVudGltZSBlbmhhbmNlbWVudHMgYXBwbGllZCBhbmQgdmVyaWZpZWQ6IiwgbGVuKHBhc3NhZ2VzKSwgIm9mZmxpbmUgcGFzc2FnZXMgaW5kZXhlZC4iKQo=" /opt/venv/bin/python -c 'import base64, os; exec(base64.b64decode(os.environ["HF_CPU_PATCH_B64"]).decode("utf-8"))'

ENV QDRANT_ENABLED=true \
    QDRANT_URL=http://127.0.0.1:6333 \
    QDRANT_COLLECTION=ura_knowledge_base_active \
    QDRANT_SIDECAR=true \
    CACHE_BACKEND=redis \
    REDIS_URL=redis://127.0.0.1:6379/0 \
    SLOWAPI_STORAGE_URI=redis://127.0.0.1:6379/1 \
    REDIS_MAXMEMORY=192mb \
    PROJECT_ROOT=/app \
    OFFLINE_BUNDLE_DIR=/app/artifacts/offline \
    DENSE_FALLBACK_BACKEND=none \
    TTS_FALLBACK_BACKEND=none \
    STT_FALLBACK_BACKEND=none \
    GEMINI_DIRECT_FIRST=true \
    SPARSE_CPU_RERANKER=true \
    OFFLINE_RAG_CPU_HASH_FALLBACK=true \
    URA_ACCOUNT_API_MODE=off \
    URA_PUBLICATIONS_URL=https://ura.go.ug/en/news \
    DOCUMENT_PARSE_ISOLATED=true \
    SEED_PROTOTYPE=false \
    NOTIFICATION_LIVE=false \
    MULTI_TENANT_RLS_APPLIED=true \
    FLAG_AUDIT_LEDGER=true \
    FLAG_TICKET_QUEUE=true \
    FLAG_MULTI_TENANT=true \
    FLAG_VOICE_CONSENT=true \
    FLAG_ACTION_LEVEL_AUDIT_V3=true \
    FLAG_CONTINUOUS_QUALITY_SCORECARD=true \
    FLAG_RISK_TIERED_AUTONOMY_GATES=true \
    FLAG_QUALITY_CIRCUIT_BREAKER=true \
    FLAG_VERIFIABLE_DECISION_RECEIPTS=true \
    FLAG_AGENTIC_MODE=true \
    FLAG_CORRECTIVE_RAG=true \
    FLAG_SEMANTIC_CACHE=true \
    FLAG_QUERY_REWRITE=true \
    FLAG_QUERY_DECOMPOSITION=true \
    FLAG_TRANSLATE_RETRIEVE=true \
    FLAG_RERANKER=true \
    FLAG_EVALUATOR_OPTIMIZER=true \
    FLAG_TAX_GRAPH=true \
    FLAG_GRAPH_FUSION=true \
    FLAG_TOOL_RAG=true \
    FLAG_TOOL_USE=false \
    FLAG_LANGGRAPH=false \
    FLAG_MULTI_AGENT_DAG=true \
    FLAG_SELF_REFLECT=true \
    FLAG_STRUCTURED_OUTPUT=true \
    FLAG_MULTILINGUAL_ROUTING=true \
    FLAG_PORTAL_COPILOT=true \
    FLAG_WORKFLOWS=true \
    FLAG_WORKFLOW_SPECIALIST_BRIDGE=true \
    FLAG_WORKFLOW_TANGENT_RESUME=true \
    FLAG_WORKFLOW_EMOTIONAL_OFFRAMP=true \
    FLAG_ZERO_EFFORT_SLOT_PREFILL=true \
    FLAG_AUTONOMOUS_OUTCOME_VERIFICATION=true \
    FLAG_MEMORY_ENABLED=true \
    FLAG_CONTINUOUS_RELATIONSHIP_MEMORY=true \
    FLAG_PROACTIVE_RESOLUTION_CONTINUATION=true \
    FLAG_MCP_TASKS=true \
    FLAG_EQ_ADAPTIVE_RETRIEVAL=true \
    FLAG_EQ_MULTIMODAL_FUSION=true \
    FLAG_EQ_STAKES_ESCALATION=true \
    FLAG_WELLBEING_CHECKIN=true \
    FLAG_HANDOFF_SUMMARIES=true \
    FLAG_STRUCTURED_TEXT_HANDOFF=true \
    FLAG_CXP_BUSINESS_HOURS=true \
    FLAG_OFFICER_AI_CO_EXECUTION=true \
    FLAG_OFFICER_PREFERENCE_CAPTURE=true \
    FLAG_ANSWER_OVERRIDES=true \
    FLAG_KNOWLEDGE_DISCREPANCY_REPORTING=true \
    FLAG_WS_CHAT=true \
    FLAG_VOICE_ENABLED=true \
    FLAG_VOICE_STREAMING=true \
    FLAG_SPOKEN_FIGURE_GUARD=true \
    FLAG_VOICE_RECEPTIONIST=true \
    FLAG_RECEPTIONIST_LANGUAGE_DETECTION=true \
    FLAG_HYBRID_SPEECH_S2S=true \
    FLAG_VOICE_AGENTIC_WORKFLOWS=true \
    FLAG_EMOTION_AWARE_PROSODY=true \
    FLAG_OFFICER_VOICE_COPILOT=true \
    FLAG_LOW_BANDWIDTH_VOICE_CODEC=true \
    FLAG_CALL_DROP_RESUME=true \
    FLAG_OFFLINE_ACTION_OUTBOX=true \
    FLAG_OFFLINE_RAG=true \
    FLAG_OFFLINE_SYNC=true \
    FLAG_OFFLINE_BUNDLE_API=true \
    FLAG_ON_DEVICE_SEARCH=true \
    FLAG_LIVE_WEB_RESEARCH=true \
    FLAG_AUTO_QDRANT_WEB_SYNC=true \
    FLAG_HYDE=false

# Documented for clarity; HF uses README.md `app_port`. The base image's
# CMD/ENTRYPOINT (supervisord → nginx:8080) is inherited unchanged.
# checkov:skip=CKV_DOCKER_3:Container runs supervisord as PID 1 to supervise multi-service stack (nginx, redis, qdrant, uvicorn, next.js)
EXPOSE 8080

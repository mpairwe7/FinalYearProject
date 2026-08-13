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
FROM landwind/ura-chatbot:sha-3e7857b

# Documented for clarity; HF uses README.md `app_port`. The base image's
# CMD/ENTRYPOINT (supervisord → nginx:8080) is inherited unchanged.
EXPOSE 8080

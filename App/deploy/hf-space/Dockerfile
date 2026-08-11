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
# Pin to an immutable sha tag. Bump this line to roll the Space to a newer
# image. sha-cd0f107 adds PR #255: Workers AI calls now try the AI Gateway and
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
FROM landwind/ura-chatbot:sha-cd0f107

# Documented for clarity; HF uses README.md `app_port`. The base image's
# CMD/ENTRYPOINT (supervisord → nginx:8080) is inherited unchanged.
EXPOSE 8080

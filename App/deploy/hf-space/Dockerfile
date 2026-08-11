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
# image. sha-7fd708d carries PRs #234 + #235 + #238 + #241 + #243:
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
FROM landwind/ura-chatbot:sha-7fd708d

# Documented for clarity; HF uses README.md `app_port`. The base image's
# CMD/ENTRYPOINT (supervisord → nginx:8080) is inherited unchanged.
EXPOSE 8080

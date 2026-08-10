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
# image. sha-8f1c1fc carries PRs #234 + #235 together:
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
FROM landwind/ura-chatbot:sha-8f1c1fc

# Documented for clarity; HF uses README.md `app_port`. The base image's
# CMD/ENTRYPOINT (supervisord → nginx:8080) is inherited unchanged.
EXPOSE 8080

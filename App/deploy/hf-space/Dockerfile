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
# image. sha-9d40610 = dev merge of PR #157: FAQ grounding hallucination
# fixes — decimal points in figures (e.g. "37.5m") no longer get split into
# fake unsupported claims, the semantic cache no longer cross-reuses answers
# between neighbouring FAQ intents (e.g. VAT rate vs VAT threshold), and
# keyword/hybrid FAQ retrieval now binds to the query's actual intent
# instead of just topic-word overlap — on top of #154 (fpdf2 + Pillow) and
# earlier.
FROM landwind/ura-chatbot:sha-9d40610

# Documented for clarity; HF uses README.md `app_port`. The base image's
# CMD/ENTRYPOINT (supervisord → nginx:8080) is inherited unchanged.
EXPOSE 8080

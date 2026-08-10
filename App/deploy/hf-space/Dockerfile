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
# image. sha-2104a8e = PR #232: adds a "What services does URA provide?"
# FAQ entry (Data/dataset/ura_about_ura_faqs.csv) — that's the first
# starter prompt on the landing page, and no document in the corpus
# answered it (confirmed live: no_retrieval_results -> escalation
# fallback), independent of the separate infra outage also affecting
# this Space (Cloudflare embed-relay 500 via Crane Cloud; VLLM DNS
# failure). _load_faq_data (service.py) reads every CSV under DATA_DIR
# fresh at ChatModel startup, so this image's first boot picks it up —
# no separate indexing step for this deployment's BM25 path.
FROM landwind/ura-chatbot:sha-2104a8e

# Documented for clarity; HF uses README.md `app_port`. The base image's
# CMD/ENTRYPOINT (supervisord → nginx:8080) is inherited unchanged.
EXPOSE 8080

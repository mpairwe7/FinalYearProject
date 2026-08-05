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
# image. sha-0f6b273 = dev merge of PR #164: relays LLM chat generation
# (workers_ai_chat, used by cloud-primary routing) through the Crane Cloud
# relay too — #160 only relayed embed/Vectorize, so cloud-primary chat
# generation was still making a direct, unrelayed call into this Space's
# broken Cloudflare egress. On top of #157 (FAQ grounding hallucination
# fixes), #160-#163 (Cloudflare relay proxy + nginx routing + timeout
# hardening), and earlier.
FROM landwind/ura-chatbot:sha-0f6b273

# Documented for clarity; HF uses README.md `app_port`. The base image's
# CMD/ENTRYPOINT (supervisord → nginx:8080) is inherited unchanged.
EXPOSE 8080

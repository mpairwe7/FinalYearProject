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
# image. sha-0b53911 = PR #230: fixing the nginx 404 above (sha-c48fe98)
# let streaming actually reach the frontend's SSE parser for the first
# time in production, which exposed a second, previously-latent bug in
# the same motion — the parser had no case for the `agent_trace` event
# (buffered retrieval/tool-call trace, emitted just before `grounding`),
# so it fell through to the token branch and rendered raw internal JSON
# inline in the reply. Confirmed live on sha-c48fe98 before this fix.
FROM landwind/ura-chatbot:sha-0b53911

# Documented for clarity; HF uses README.md `app_port`. The base image's
# CMD/ENTRYPOINT (supervisord → nginx:8080) is inherited unchanged.
EXPOSE 8080

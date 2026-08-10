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
# image. sha-c48fe98 = PR #228: the nginx SSE-streaming location block was
# missing the `rewrite ^/api/(.*)$ /$1 break;` line every sibling /api/*
# block has, so /api/v1/chat/stream reached the backend unrewritten and
# 404'd on every single request — confirmed via a live 10-question
# regression pass against this Space before the fix. The frontend's
# fallback to non-streaming /api/v1/chat covered for it (every message
# still got answered), but real token-by-token streaming never actually
# ran in production until this bump.
FROM landwind/ura-chatbot:sha-c48fe98

# Documented for clarity; HF uses README.md `app_port`. The base image's
# CMD/ENTRYPOINT (supervisord → nginx:8080) is inherited unchanged.
EXPOSE 8080

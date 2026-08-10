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
# image. sha-13663b9 = the first push to `main` since project inception
# (PR #226 reconciled main's disconnected initial-scaffold history with
# dev, which had carried all 639 commits of real development on its own
# until now) — so this single bump carries everything dev has shipped
# since sha-0f6b273 (PR #164), most notably the chatv2 redesign, the
# multilingual routing/statutory-graph/model-tiering architecture (#223),
# and this session's 5-locale language switching + voice UI theming fix
# (#225). This file had also drifted behind what was actually live: the
# Space's own Dockerfile had already been bumped by hand past this repo's
# copy, to sha-c4c08d6 (PR #174) — this bump supersedes that drift too.
FROM landwind/ura-chatbot:sha-13663b9

# Documented for clarity; HF uses README.md `app_port`. The base image's
# CMD/ENTRYPOINT (supervisord → nginx:8080) is inherited unchanged.
EXPOSE 8080

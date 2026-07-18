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
# image. sha-ae4df29 = dev merge of PR #154: fpdf2 + Pillow declared (all
# PDF endpoints previously 500'd — fpdf2 was never in the image) and the
# attachment registry mirrored to a shared spool so UVICORN_WORKERS=2
# workers see the same documents — on top of #152 (document attachments)
# and earlier.
FROM landwind/ura-chatbot:sha-ae4df29

# Documented for clarity; HF uses README.md `app_port`. The base image's
# CMD/ENTRYPOINT (supervisord → nginx:8080) is inherited unchanged.
EXPOSE 8080

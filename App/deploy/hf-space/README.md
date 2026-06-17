---
title: URA Chatbot
emoji: 🗣️
colorFrom: yellow
colorTo: red
sdk: docker
app_port: 8080
pinned: false
short_description: URA assistant — Sunbird speech + edge-tts English
---

# URA Chatbot — Hugging Face Docker Space (second pipeline)

A deployment of the URA Chatbot independent of Crane Cloud. It reuses the
**same prebuilt image** that Crane Cloud runs (`landwind/ura-chatbot`, built by
`.github/workflows/ura-chatbot-build-push.yml`), so there is no separate build —
HF pulls the image and runs it. The container bundles FastAPI + Next.js + nginx
under supervisord and listens on port 8080 (`app_port` above).

- **Speech:** Sunbird AI (STT/TTS, native Luganda speaker 248) + edge-tts
  (`en-US-AriaNeural`) for English.
- **Translation:** Gemini (`gemini_flash`) via the Cloudflare AI Gateway.
- **Config:** supplied as Space **secrets**, mirroring the Crane Cloud app's
  environment (see `App/deploy/hf-space/` and `docs/CRANE_CLOUD_DEPLOYMENT.md` §7).

To roll to a newer image, bump the `FROM` tag in `Dockerfile`.

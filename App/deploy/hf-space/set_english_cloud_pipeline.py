#!/usr/bin/env python3
"""Point the ENGLISH speech pipeline at Cloudflare Workers AI.

English is the one locale Sunbird has no good voice for, and it lands on
edge-tts for TTS and on Sunbird for ASR (rtf > 4 on a cold path). The Workers AI
stages already exist in speech_service.py, English-only and ahead of both — they
are simply gated off. This sets the switches that open them.

    DO NOT RUN THIS AGAINST THE HF SPACE.

The HF Space free tier cannot reach Cloudflare at all. Every call dies in the
TLS handshake with

    httpx.ConnectError: [SSL: UNEXPECTED_EOF_WHILE_READING]

which relay_client.py already documents ("this deployment's own egress to
Cloudflare is blocked (the HF Space free-tier network path)"). The relay that
exists to work around it points at the Crane Cloud host and answers 500, so
that route is gone too.

Enabling it there is worse than leaving it off: the chain tries both TTS models
before falling through, and each one hangs until it times out. Measured on the
Space, uncached English TTS went from ~0.3s to 32.6s, easing to ~4s only as the
circuit breaker opened. Run this only where egress to Cloudflare is confirmed
working — a local dev machine, or a deployment that can reach it. Pass
--revert to put a Space back.

These are config rather than credentials, so *variables* would be the natural
home — but replicate_secrets.py already pushed the whole env as secrets, and a
Space rejects a key that exists as both, with

    CONFIG_ERROR: Collision on variables and secrets names

which takes the Space down until one side is removed. So these are written as
secrets, matching where the keys already live. The Cloudflare credentials
themselves (CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN / CF_AIG_GATEWAY /
CF_AIG_TOKEN) are separate and also come from replicate_secrets.py.

    ! python3 App/deploy/hf-space/set_english_cloud_pipeline.py

Pure standard library. Reads HF_TOKEN from .env. Prints key names and HTTP
status only — never a value that could be sensitive.
"""
import json
import os
import urllib.error
import urllib.request

REPO = "landwind22/ura-chatbot"
ENV = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

# Mirrors the same keys in the repo-root .env so local and Space agree.
SETTINGS = {
    # Master gate for the Workers AI / Gemini tier. The chat-side gates ALSO
    # require LLM_FALLBACK_BACKEND, which is deliberately left empty, so this
    # changes the speech path only.
    "FLAG_CLOUDFLARE_FALLBACK": "true",
    # Selects Workers AI as the cloud tier for each direction.
    "STT_FALLBACK_BACKEND": "workers_ai",
    "TTS_FALLBACK_BACKEND": "workers_ai",
    # whisper-large-v3-turbo is the model that answers; plain @cf/openai/whisper
    # returns 400 on this gateway.
    "STT_FALLBACK_MODEL": "@cf/openai/whisper-large-v3-turbo",
    # Deepgram Aura-2 first: for the same sentence it returns ~21 KB of MP3
    # against MeloTTS's ~288 KB of WAV — a 13x smaller payload, which matters
    # more than anything else on a Ugandan mobile connection. MeloTTS stays as
    # the resilience fallback.
    "TTS_FALLBACK_MODEL": "@cf/deepgram/aura-2-en",
    "TTS_FALLBACK_MODEL_2": "@cf/myshell-ai/melotts",
}


def env_val(key):
    for line in open(ENV):
        s = line.strip()
        if s.startswith(key + "="):
            return s.split("=", 1)[1].strip().strip('"').strip("'")
    return None


# Restores the pre-change state. Model names are deliberately not reverted:
# they are inert while the selectors are empty, and they record the right
# choice for any environment that CAN reach Cloudflare.
REVERT = {
    "FLAG_CLOUDFLARE_FALLBACK": "false",
    "STT_FALLBACK_BACKEND": "",
    "TTS_FALLBACK_BACKEND": "",
}


def main():
    import sys

    hf = env_val("HF_TOKEN")
    if not hf:
        raise SystemExit("HF_TOKEN missing from .env")
    settings = REVERT if "--revert" in sys.argv else SETTINGS
    for key, value in settings.items():
        data = json.dumps({"key": key, "value": value}).encode()
        req = urllib.request.Request(
            f"https://huggingface.co/api/spaces/{REPO}/secrets",
            data=data,
            method="POST",
            headers={"Authorization": f"Bearer {hf}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                print(f"  {key} = {value}  -> HTTP {r.status}")
        except urllib.error.HTTPError as e:
            print(f"  {key} -> HTTP {e.code}: {e.read()[:160]}")
    print(f"\nSet on {REPO}. The Space restarts to pick these up.")


if __name__ == "__main__":
    main()

"""Cloudflare AI Gateway client — the single secret-safe egress for all fallbacks.

Routes Workers AI + Google AI Studio (Gemini) through ONE AI Gateway so caching,
rate-limiting, retries and observability apply uniformly. The two-credential
pattern is enforced here and ONLY here:

  - ``Authorization: Bearer <Cloudflare API token>``   — Workers AI provider auth
  - ``cf-aig-authorization: Bearer <AI Gateway token>``  — the gateway's own auth
  - ``x-goog-api-key: <Gemini key>``                     — Google provider auth

Secrets are read via ``SecretStr.get_secret_value()`` ONLY inside the private
``_*_headers()`` builders, never logged and never placed in a URL query string.
Vectorize/R2 use the direct ``api.cloudflare.com`` endpoint (the gateway proxies
inference providers only); that path uses just the CF token.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

import httpx

from .config import get_cloud_settings

logger = logging.getLogger("ura.providers.gateway")

_GATEWAY_BASE = "https://gateway.ai.cloudflare.com/v1"
_HTTP_TIMEOUT = float(os.getenv("CF_HTTP_TIMEOUT", "30"))

_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    """Lazy shared sync client (mirrors ``sunbird._get_client``)."""
    global _client
    if _client is None:
        _client = httpx.Client(timeout=_HTTP_TIMEOUT)
    return _client


def _gateway_url(provider: str) -> str:
    """Build the AI Gateway base URL for *provider* (e.g. 'workers-ai')."""
    s = get_cloud_settings()
    return f"{_GATEWAY_BASE}/{s.cloudflare_account_id}/{s.cf_aig_gateway}/{provider}"


# ── header builders (the ONLY place secrets are read) ────────────────────────

def _workers_ai_headers() -> dict[str, str]:
    """CF token + AI Gateway token (two separate headers) for Workers AI calls."""
    s = get_cloud_settings()
    return {
        "Authorization": f"Bearer {s.cloudflare_api_token.get_secret_value()}",
        "cf-aig-authorization": f"Bearer {s.cf_aig_token.get_secret_value()}",
        "Content-Type": "application/json",
    }


def _gemini_headers() -> dict[str, str]:
    """Google key (x-goog-api-key) + AI Gateway token for Gemini calls."""
    s = get_cloud_settings()
    return {
        "x-goog-api-key": s.gemini_api_key.get_secret_value(),
        "cf-aig-authorization": f"Bearer {s.cf_aig_token.get_secret_value()}",
        "Content-Type": "application/json",
    }


def cf_api_headers() -> dict[str, str]:
    """Direct api.cloudflare.com auth (Vectorize, R2 mgmt) — CF token only."""
    s = get_cloud_settings()
    return {
        "Authorization": f"Bearer {s.cloudflare_api_token.get_secret_value()}",
        "Content-Type": "application/json",
    }


def _post_json(url: str, headers: dict[str, str], payload: dict) -> dict[str, Any]:
    """POST JSON and return the parsed response (raises on non-2xx)."""
    # Log shape only — never headers or bodies (they carry secrets / PII).
    logger.debug("gateway POST %s (payload keys=%s)", url.rsplit("/", 1)[-1], list(payload))
    resp = _get_client().post(url, headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json()


# ── Workers AI target selection ──────────────────────────────────────────────
#
# The AI Gateway is preferred: it adds caching, rate limiting and analytics.
# But `gateway.ai.cloudflare.com` is unreachable from some hosts — from the HF
# Space every call dies in the TLS handshake with
#
#     httpx.ConnectError: [SSL: UNEXPECTED_EOF_WHILE_READING]
#
# which is why relay_client.py exists. The HF forum reports Spaces failing on
# three-label hostnames while two-label ones resolve, and the hosts this app
# reaches successfully from there (api.sunbird.ai, storage.googleapis.com) are
# all two-label. `api.cloudflare.com` is two-label and serves the same models
# through Cloudflare's documented REST API, so it is tried as a fallback.
#
# Fallback is TRANSPORT-ONLY. An HTTP 4xx/5xx is a real answer from the model
# API — retrying it elsewhere would repeat the same error and bill twice.

_CF_API_BASE = "https://api.cloudflare.com/client/v4"


def _workers_ai_targets(model: str) -> list[tuple[str, dict[str, str]]]:
    """(url, headers) pairs to try for *model*, in preference order."""
    targets = [(f"{_gateway_url('workers-ai')}/{model}", _workers_ai_headers())]
    account = get_cloud_settings().cloudflare_account_id.strip()
    if account:
        targets.append((f"{_CF_API_BASE}/accounts/{account}/ai/run/{model}", cf_api_headers()))
    return targets


def _workers_ai_call(model: str, send: Any) -> Any:
    """Run ``send(url, headers)`` against each target, falling through on
    transport failure only. Raises the last error if every target is
    unreachable."""
    last: Exception | None = None
    for index, (url, headers) in enumerate(_workers_ai_targets(model)):
        try:
            return send(url, headers)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            last = exc
            host = url.split("/")[2]
            logger.warning(
                "Workers AI: %s unreachable (%s); %s",
                host,
                type(exc).__name__,
                "trying the direct REST API" if index == 0 else "no targets left",
            )
    raise last if last else RuntimeError("workers_ai: no targets configured")


# ── Workers AI ───────────────────────────────────────────────────────────────

def workers_ai_embed(texts: list[str], model: str = "@cf/baai/bge-m3") -> list[list[float]]:
    """Embed via Workers AI bge-m3 (1024-dim) — same space as the local corpus."""
    if get_cloud_settings().cf_relay_base_url.strip():
        from . import relay_client

        return relay_client.relay_workers_ai_embed(texts)
    data = _workers_ai_call(model, lambda url, h: _post_json(url, h, {"text": texts}))
    result = data.get("result", {})
    vectors = result.get("data") or result.get("embedding")
    if not vectors:
        raise RuntimeError("workers_ai_embed: empty embedding response")
    return vectors


def workers_ai_chat(
    messages: list[dict[str, str]],
    model: str = "@cf/meta/llama-3.1-8b-instruct",
    *,
    max_tokens: int = 512,
    temperature: float = 0.2,
) -> str:
    """Chat-completion via a Workers AI text model; returns the reply string."""
    if get_cloud_settings().cf_relay_base_url.strip():
        from . import relay_client

        return relay_client.relay_workers_ai_chat(
            messages, model, max_tokens=max_tokens, temperature=temperature
        )
    data = _workers_ai_call(
        model,
        lambda url, h: _post_json(
            url, h, {"messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        ),
    )
    return (data.get("result", {}).get("response") or "").strip()


def workers_ai_stt(audio: bytes, model: str = "@cf/openai/whisper") -> dict[str, Any]:
    """Transcribe audio via Workers AI; returns ``{text, language}``.

    Two request shapes by model family (confirmed against the live gateway):
      - ``@cf/openai/whisper`` (original) takes the raw audio bytes as the body.
      - newer models (``whisper-large-v3-turbo``, Deepgram ``nova-3``/``flux``)
        take JSON ``{"audio": "<base64>"}`` and nest language in
        ``result.transcription_info``.
    """
    if model.rstrip("/").endswith("/whisper"):

        def _send(url: str, headers: dict[str, str]):
            headers = {k: v for k, v in headers.items() if k != "Content-Type"}
            resp = _get_client().post(url, headers=headers, content=audio)
            resp.raise_for_status()
            return resp.json().get("result", {})

        result = _workers_ai_call(model, _send)
    else:
        payload = {"audio": base64.b64encode(audio).decode("ascii")}
        result = _workers_ai_call(model, lambda url, h: _post_json(url, h, payload)).get(
            "result", {}
        )
    language = result.get("language") or (result.get("transcription_info") or {}).get("language")
    return {"text": result.get("text", ""), "language": language}


def workers_ai_tts(text: str, model: str = "@cf/myshell-ai/melotts", *, lang: str = "en") -> dict[str, Any]:
    """Synthesize speech via Workers AI; returns ``{audio: bytes, fmt, sample_rate}``.

    Two response shapes by model family (confirmed against the live gateway):
      - MeloTTS (``@cf/myshell-ai/melotts``) → JSON ``{result:{audio:<base64 WAV>}}``.
      - Deepgram Aura (``@cf/deepgram/aura-*``) → raw audio bytes (``audio/mpeg``).
    """
    def _fmt(b: bytes) -> str:
        if b[:4] == b"RIFF":
            return "wav"
        if b[:3] == b"ID3" or b[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
            return "mp3"
        return "bin"

    if "aura" in model:  # Deepgram Aura — binary audio response

        def _send(url: str, headers: dict[str, str]) -> bytes:
            resp = _get_client().post(url, headers=headers, json={"text": text[:2000]})
            resp.raise_for_status()
            return resp.content

        audio = _workers_ai_call(model, _send)
    else:  # MeloTTS and similar — JSON {prompt, lang} -> base64 audio
        data = _workers_ai_call(
            model, lambda url, h: _post_json(url, h, {"prompt": text[:2000], "lang": lang})
        )
        b64 = (data.get("result") or {}).get("audio") or ""
        if not b64:
            raise RuntimeError("workers_ai_tts: empty audio response")
        audio = base64.b64decode(b64)
    if not audio:
        raise RuntimeError("workers_ai_tts: empty audio")
    return {"audio": audio, "fmt": _fmt(audio), "sample_rate": 24000}


# ── Gemini (via the google-ai-studio provider on the Gateway) ────────────────

def gemini_generate(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.2,
) -> str:
    """Generate text via Gemini through the AI Gateway; returns the answer string."""
    s = get_cloud_settings()
    model = model or s.gemini_model
    url = f"{_gateway_url('google-ai-studio')}/v1beta/models/{model}:generateContent"
    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    data = _post_json(url, _gemini_headers(), payload)
    cands = data.get("candidates", [])
    if not cands:
        raise RuntimeError("gemini_generate: no candidates returned")
    parts = cands[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise RuntimeError("gemini_generate: empty text")
    return text

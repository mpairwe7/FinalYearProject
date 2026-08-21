"""Cloud-provider settings (Cloudflare + Gemini) — additive, ``SecretStr``-backed.

Only the NEW fallback keys live here; the rest of the app keeps its existing
scattered ``os.getenv`` config untouched. Every key is a pydantic ``SecretStr``
so an accidental ``print(settings)`` / structured-log dump yields ``**********``
rather than the value. Values come from the real process environment (Crane
Cloud injects them) or, for local dev, a gitignored ``.env`` (real env always
takes precedence). ``get_secret_value()`` is only ever called at the HTTP-header
build site in ``gateway.py``.
"""

from __future__ import annotations

import functools
import os

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class CloudSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None if os.getenv("APP_ENV") == "testing" or "PYTEST_CURRENT_TEST" in os.environ else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Cloudflare account + API token (provider auth for Workers AI / Vectorize / R2 mgmt)
    cloudflare_account_id: str = ""
    cloudflare_api_token: SecretStr = SecretStr("")

    # AI Gateway (the gateway's own auth, sent in cf-aig-authorization)
    cf_aig_gateway: str = ""
    cf_aig_token: SecretStr = SecretStr("")

    # Vectorize (dense-retrieval fallback index; bge-m3 1024-dim, cosine)
    vectorize_index: str = "ura-kb-bge-m3"

    # R2 (S3-compatible object storage)
    r2_account_id: str = ""
    r2_access_key_id: SecretStr = SecretStr("")
    r2_secret_access_key: SecretStr = SecretStr("")
    r2_bucket: str = "ura-chatbot"

    # Google Gemini (Luganda translation + optional LLM fallback)
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-2.5-flash"

    # Free-tier guardrails
    cf_neuron_daily_budget: int = 10000
    gemini_rpm: int = 10

    # Cloudflare relay — when this deployment's own egress to Cloudflare is
    # blocked (e.g. the HF Space free-tier network path), route Vectorize/
    # Workers AI calls through another deployment with confirmed working
    # egress instead (e.g. Crane Cloud). Leave unset to call Cloudflare
    # directly, which remains the default on deployments that don't need it.
    # ``cf_relay_secret`` doubles as the shared secret the relay ENDPOINT
    # itself checks incoming requests against (see main.py) — the same value
    # is set on both sides, distinct from the real Cloudflare token so a
    # leaked relay secret can't reach the Cloudflare account directly.
    cf_relay_base_url: str = ""
    cf_relay_secret: SecretStr = SecretStr("")


@functools.lru_cache(maxsize=1)
def get_cloud_settings() -> CloudSettings:
    """Cached singleton. Tests should call ``get_cloud_settings.cache_clear()``."""
    import sys

    if "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ or os.getenv("TESTING") == "1":
        return CloudSettings(_env_file=None)
    return CloudSettings()


def _has(secret: SecretStr) -> bool:
    """True when the secret holds a non-empty value."""
    return bool(secret.get_secret_value().strip())


def is_cloudflare_configured() -> bool:
    """True when the Cloudflare account + API token + AI Gateway are all set."""
    s = get_cloud_settings()
    return (
        bool(s.cloudflare_account_id.strip())
        and _has(s.cloudflare_api_token)
        and bool(s.cf_aig_gateway.strip())
        and _has(s.cf_aig_token)
    )


def is_gemini_configured() -> bool:
    """True when Gemini can be called — which now needs only its own API key.

    This used to also require ``is_cloudflare_configured()``, because Gemini
    reached Google exclusively through the Cloudflare AI Gateway. It no longer
    does: ``gemini_generate`` falls through to generativelanguage.googleapis.com,
    which needs no Cloudflare account, token or gateway.

    Leaving the coupling in place made the direct route unreachable in exactly
    the deployment it was written for. The HF Space cannot reach Cloudflare at
    all, so any Space missing one of the four Cloudflare values answered "Gemini
    is not configured" and every caller skipped the Gemini branch SILENTLY —
    no log line, since these are guard conditions rather than failures. English
    generation stayed dark on a Space holding a perfectly good GEMINI_API_KEY.

    The gateway is still tried first when it is configured; it is simply no
    longer a precondition for using Gemini at all.
    """
    return _has(get_cloud_settings().gemini_api_key)


def is_vectorize_configured() -> bool:
    return is_cloudflare_configured() and bool(get_cloud_settings().vectorize_index.strip())


def is_r2_configured() -> bool:
    s = get_cloud_settings()
    return (
        bool(s.r2_account_id.strip())
        and _has(s.r2_access_key_id)
        and _has(s.r2_secret_access_key)
        and bool(s.r2_bucket.strip())
    )

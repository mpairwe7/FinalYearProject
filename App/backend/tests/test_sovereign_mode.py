"""Unit tests for the SOVEREIGN_MODE startup and runtime guard (Phase C.2)."""

from __future__ import annotations

import pytest
from app.flags import flags
from app.production_readiness import (
    COMMERCIAL_KEY_ENV_VARS,
    assert_sovereign_mode,
    gap_gate_errors,
    is_sovereign_mode,
    sovereign_mode_errors,
)
from app.providers import gateway as gw
from app.service import _llm_cloud_fallback


def _clean_sovereign_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure a clean sovereign-mode environment with all commercial paths unset."""
    monkeypatch.setenv("SOVEREIGN_MODE", "true")
    monkeypatch.setenv("FLAG_CLOUDFLARE_FALLBACK", "false")
    monkeypatch.setenv("LLM_FALLBACK_BACKEND", "")
    monkeypatch.setenv("LLM_PRIMARY_BACKEND", "vllm")
    monkeypatch.setenv("LLM_BACKEND", "vllm")
    monkeypatch.setenv("VLLM_BASE_URL", "http://vllm:8001/v1")
    monkeypatch.setenv("REASONER_BASE_URL", "http://vllm-reasoner:8001/v1")
    for key_var in COMMERCIAL_KEY_ENV_VARS:
        monkeypatch.delenv(key_var, raising=False)
    flags.clear("cloudflare_fallback")


def test_clean_sovereign_config_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a clean local-only sovereign configuration passes startup validation."""
    _clean_sovereign_env(monkeypatch)
    assert is_sovereign_mode() is True
    assert sovereign_mode_errors() == []
    assert_sovereign_mode()

    # Localhost and RFC 1918 private addresses are also permitted
    for valid_url in (
        "http://localhost:8011/v1",
        "http://127.0.0.1:8012/v1",
        "http://10.24.0.15:8001/v1",
        "http://172.18.0.5:8001/v1",
        "http://192.168.1.50:8001/v1",
    ):
        monkeypatch.setenv("VLLM_BASE_URL", valid_url)
        monkeypatch.setenv("REASONER_BASE_URL", valid_url)
        assert sovereign_mode_errors() == []


def test_sovereign_mode_rejects_cloudflare_fallback_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify SOVEREIGN_MODE fails startup when cloudflare_fallback is enabled."""
    _clean_sovereign_env(monkeypatch)
    monkeypatch.setenv("FLAG_CLOUDFLARE_FALLBACK", "true")
    with pytest.raises(SystemExit) as exc_info:
        assert_sovereign_mode()
    assert "cloudflare_fallback" in str(exc_info.value)


def test_sovereign_mode_rejects_non_empty_llm_fallback_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify SOVEREIGN_MODE fails startup when LLM_FALLBACK_BACKEND is non-empty."""
    _clean_sovereign_env(monkeypatch)
    monkeypatch.setenv("LLM_FALLBACK_BACKEND", "gemini")
    with pytest.raises(SystemExit) as exc_info:
        assert_sovereign_mode()
    assert "LLM_FALLBACK_BACKEND" in str(exc_info.value)


def test_sovereign_mode_rejects_non_local_primary_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify SOVEREIGN_MODE fails startup when LLM_PRIMARY_BACKEND is not local."""
    _clean_sovereign_env(monkeypatch)
    monkeypatch.setenv("LLM_PRIMARY_BACKEND", "workers_ai")
    with pytest.raises(SystemExit) as exc_info:
        assert_sovereign_mode()
    assert "LLM_PRIMARY_BACKEND" in str(exc_info.value)


@pytest.mark.parametrize(
    ("url_var", "bad_url"),
    [
        ("VLLM_BASE_URL", "https://api.openai.com/v1"),
        ("REASONER_BASE_URL", "https://openrouter.ai/api/v1"),
        ("VLLM_BASE_URL", "http://8.8.8.8:8001/v1"),
        ("REASONER_BASE_URL", "https://llm.external.example.com/v1"),
    ],
)
def test_sovereign_mode_rejects_non_private_llm_base_urls(
    monkeypatch: pytest.MonkeyPatch,
    url_var: str,
    bad_url: str,
) -> None:
    """Verify SOVEREIGN_MODE rejects non-private or external domain LLM base URLs."""
    _clean_sovereign_env(monkeypatch)
    monkeypatch.setenv(url_var, bad_url)
    with pytest.raises(SystemExit) as exc_info:
        assert_sovereign_mode()
    assert url_var in str(exc_info.value)


@pytest.mark.parametrize("key_var", COMMERCIAL_KEY_ENV_VARS)
def test_sovereign_mode_rejects_commercial_keys_without_leaking_value(
    monkeypatch: pytest.MonkeyPatch,
    key_var: str,
) -> None:
    """Verify each commercial API key env var raises and logs only the variable name, never its value."""
    _clean_sovereign_env(monkeypatch)
    sentinel_secret = f"super-secret-value-for-{key_var.lower()}-do-not-leak"  # pragma: allowlist secret
    monkeypatch.setenv(key_var, sentinel_secret)

    with pytest.raises(SystemExit) as exc_info:
        assert_sovereign_mode()

    err_text = str(exc_info.value)
    assert key_var in err_text
    assert sentinel_secret not in err_text
    assert any(key_var in e and sentinel_secret not in e for e in gap_gate_errors())


def test_runtime_cloud_paths_short_circuit_when_sovereign_mode_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify _llm_cloud_fallback, workers_ai_chat, and gemini_generate return empty string in SOVEREIGN_MODE."""
    _clean_sovereign_env(monkeypatch)
    monkeypatch.setenv("FLAG_CLOUDFLARE_FALLBACK", "true")
    monkeypatch.setenv("LLM_FALLBACK_BACKEND", "gemini")

    assert (
        _llm_cloud_fallback(
            query="What is VAT?",
            passages=[],
            conversation_history=None,
            locale="en",
        )
        == ""
    )
    assert gw.workers_ai_chat([{"role": "user", "content": "Hello"}]) == ""
    assert gw.gemini_generate("Hello") == ""

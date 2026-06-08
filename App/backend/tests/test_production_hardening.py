from __future__ import annotations

import os
import datetime as dt
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, Request

from app.auth.dependencies import current_user, optional_user
from app.main import _validate_production_env


SECURE_PROD_ENV = {
    "APP_ENV": "production",
    "AUTH_ALG": "RS256",
    "AUTH_DEV_SECRET": "prod-secret-with-enough-entropy",
    "OIDC_ISSUER": "https://idp.example.gov/realms/ura",
    "OIDC_AUDIENCE": "ura-chatbot",
    "OIDC_JWKS_URL": "https://idp.example.gov/realms/ura/protocol/openid-connect/certs",
    "CORS_ORIGINS": "https://chat.ura.go.ug",
    "WORKERS": "2",
    "SLOWAPI_STORAGE_URI": "redis://:redis-password@redis:6379/1",
    "REDIS_URL": "redis://:redis-password@redis:6379/0",
    "LLM_TRUST_REMOTE_CODE": "false",
    "LLM_MODEL_REVISION": "abc123",
    "LLM_BACKEND": "local",
    "LLM_SERIALIZE_LOCAL_GENERATION": "true",
    "STORE_RAW_PROMPTS": "false",
    "ANALYTICS_BACKEND": "postgres",
    "POSTGRES_DSN": "postgresql://ura:secret@postgres:5432/ura",
    "INDEX_API_KEY": "prod-index-key",
    "QDRANT_URL": "http://qdrant:6333",
    "QDRANT_API_KEY": "qdrant-secret",
    "ANALYTICS_DB_DIR": "/data/ura",
    "SPEECH_ENABLED": "true",
    "FLAG_AUTH_REQUIRED": "true",
    "FLAG_MULTI_TENANT": "true",
    "FLAG_AUDIT_LEDGER": "true",
    "FLAG_VOICE_CONSENT": "true",
    "WS_CONFIRM_HMAC_SECRET": "prod-confirm-hmac-secret-with-entropy",
    "REQUIRE_FRESH_AUTHORITY": "true",
}


class ProductionHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        source = root / "rates.json"
        source.write_text('{"vat_standard": 0.18}', encoding="utf-8")
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "version": "test",
                    "generated_at": dt.datetime.now(dt.UTC).isoformat(),
                    "max_age_days": 30,
                    "sources": [
                        {
                            "id": "rates",
                            "path": source.name,
                            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.secure_env = {**SECURE_PROD_ENV, "URA_AUTHORITY_MANIFEST": str(manifest)}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_production_validation_rejects_dev_defaults(self) -> None:
        env = {
            **self.secure_env,
            "CORS_ORIGINS": "http://localhost:3032,https://demo.ngrok-free.dev",
            "AUTH_ALG": "HS256",
            "INDEX_API_KEY": "dev-index-key",
            "ANALYTICS_BACKEND": "sqlite",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as raised:
                _validate_production_env()

        message = str(raised.exception)
        self.assertIn("AUTH_ALG must be RS256", message)
        self.assertIn("development tunnel/local origin", message)
        self.assertIn("ANALYTICS_BACKEND must be postgres", message)
        self.assertIn("INDEX_API_KEY must be a strong non-dev", message)

    def test_production_validation_accepts_secure_baseline(self) -> None:
        with patch.dict(os.environ, self.secure_env, clear=True):
            _validate_production_env()

    def test_external_qdrant_required_in_prod(self) -> None:
        # P0-4: an in-container / localhost Qdrant is not durable.
        env = {**self.secure_env, "QDRANT_URL": "http://localhost:6333"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as raised:
                _validate_production_env()
        self.assertIn("QDRANT_URL must not be localhost", str(raised.exception))

    def test_missing_qdrant_url_rejected_in_prod(self) -> None:
        env = {k: v for k, v in self.secure_env.items() if k != "QDRANT_URL"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as raised:
                _validate_production_env()
        self.assertIn("QDRANT_URL must point at an external/managed Qdrant", str(raised.exception))

    def test_persistent_data_dir_required_in_prod(self) -> None:
        # P0-4: SQLite-backed audit ledger + memory need a durable volume.
        env = {k: v for k, v in self.secure_env.items() if k != "ANALYTICS_DB_DIR"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as raised:
                _validate_production_env()
        self.assertIn("ANALYTICS_DB_DIR must be set", str(raised.exception))

    def test_ephemeral_data_dir_rejected_in_prod(self) -> None:
        env = {**self.secure_env, "ANALYTICS_DB_DIR": "/tmp/ura"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as raised:
                _validate_production_env()
        self.assertIn("ANALYTICS_DB_DIR must be an absolute path on a persistent volume", str(raised.exception))

    def test_native_voice_requires_auth_required_in_prod(self) -> None:
        # P1-9: enabling a voice socket without auth in prod must fail closed.
        env = {
            **self.secure_env,
            "FLAG_NATIVE_VOICE": "true",
            "FLAG_AUTH_REQUIRED": "false",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as raised:
                _validate_production_env()
        self.assertIn(
            "FLAG_NATIVE_VOICE=true requires FLAG_AUTH_REQUIRED", str(raised.exception)
        )

    def test_auth_required_rejects_missing_bearer_token(self) -> None:
        request = Request({"type": "http", "headers": []})
        with patch.dict(os.environ, {"FLAG_AUTH_REQUIRED": "true"}, clear=False):
            with self.assertRaises(HTTPException) as raised:
                current_user(request, None)

        self.assertEqual(raised.exception.status_code, 401)

    def test_optional_user_allows_public_anonymous_chat_context(self) -> None:
        request = Request({"type": "http", "headers": []})
        with patch.dict(os.environ, {"FLAG_AUTH_REQUIRED": "true"}, clear=False):
            ctx = optional_user(request, None)

        self.assertFalse(ctx.authenticated)
        self.assertEqual(ctx.role, "public")
        self.assertEqual(ctx.tenant_id, "default")


if __name__ == "__main__":
    unittest.main()

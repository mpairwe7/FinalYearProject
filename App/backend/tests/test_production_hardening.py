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
    "SPEECH_ENABLED": "true",
    "FLAG_AUTH_REQUIRED": "true",
    "FLAG_MULTI_TENANT": "true",
    "FLAG_AUDIT_LEDGER": "true",
    "FLAG_VOICE_CONSENT": "true",
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

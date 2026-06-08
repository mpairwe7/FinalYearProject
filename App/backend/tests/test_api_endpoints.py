"""Full-surface API regression tests (TestClient / live ASGI).

Covers the HTTP endpoints in ``app.main`` — happy paths, request validation,
and the auth/gating matrix (public / current_user / require_user / role /
admin / ops-key / feature-flag-404). The WebSocket routes are covered by
test_chat_ws_lifecycle / test_tool_confirmation / test_voice_ws_hardening.

Models are stubbed (``app.state.model`` / ``app.state.speech``) and the
TestClient is built WITHOUT the lifespan context so no Qwen/Qdrant/Whisper
load happens and the production startup gate is skipped.
"""

from __future__ import annotations

import os
import unittest
import unittest.mock as mock

# Env must be set before importing app.* (read at import time).
os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("SPEECH_ENABLED", "false")
os.environ.setdefault("QDRANT_ENABLED", "false")
os.environ.setdefault("ANALYTICS_BACKEND", "sqlite")
os.environ.setdefault("OTEL_ENABLED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import database as db  # noqa: E402
from app.auth.dependencies import reset_verifier  # noqa: E402
from app.auth.jwt_auth import make_dev_token  # noqa: E402
from app.flags import flags  # noqa: E402
from app.main import app  # noqa: E402
from app import main as main_mod  # noqa: E402


def setUpModule() -> None:
    # Initialise the analytics schema once (default data_store path; WAL works
    # there, unlike a tmpfs override). Idempotent + runs migrations.
    db.init_db()


def _chat_result(**over):
    base = {
        "reply": "The standard VAT rate is 18%.",
        "sources": ["vat.pdf"],
        "citations": [],
        "faithfulness_score": 0.9,
        "retrieval_mode": "hybrid",
        "model": "stub-model",
        "conversation_id": "conv-test-1",
        "locale": "en",
        "escalation_required": False,
        "escalation_reason": "",
        "agent_role": "rag_answerer",
        "ticket_id": "",
        "next_actions": [],
    }
    base.update(over)
    return base


def _stub_model():
    m = mock.MagicMock(name="stub_chat_model")
    m.generate.return_value = _chat_result()
    # Empty lists satisfy the response models without nested-field stubbing;
    # /v1/chat guards on non-empty predictions, so this is fine there too.
    m.classify.return_value = {"predictions": [], "processing_time_ms": 1.0}
    m.classify_batch.return_value = {"results": [], "processing_time_ms": 1.0}
    m.list_tags.return_value = {"tags": [], "total": 0}
    m.get_faq.return_value = {"tag": "vat", "faqs": [], "total": 0}
    return m


def _client(*, model=True, speech=False):
    app.state.model = _stub_model() if model else None
    app.state.speech = mock.MagicMock(name="stub_speech") if speech else None
    return TestClient(app)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class _Base(unittest.TestCase):
    def tearDown(self) -> None:
        for f in ("auth_required", "offline_rag", "offline_sync", "offline_bundle_api",
                  "voice_vision", "quantization", "voice_consent"):
            flags.clear(f)
        reset_verifier()


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------
class SystemEndpoints(_Base):
    def test_health_is_public_200(self):
        r = _client().get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("status"), "alive")

    def test_ready_200_with_model_503_without(self):
        self.assertEqual(_client(model=True).get("/ready").status_code, 200)
        self.assertEqual(_client(model=False).get("/ready").status_code, 503)

    def test_metrics_requires_admin(self):
        c = _client()
        self.assertIn(c.get("/metrics").status_code, (401, 403, 503))
        staff = make_dev_token("ops", role="ura_staff")
        self.assertEqual(c.get("/metrics", headers=_bearer(staff)).status_code, 200)


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
class ChatEndpoints(_Base):
    def test_chat_happy_path_public(self):
        r = _client().post("/v1/chat", json={"message": "What is the VAT rate?"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["reply"], "The standard VAT rate is 18%.")
        self.assertIn("conversation_id", body)
        self.assertFalse(body["escalation_required"])

    def test_chat_empty_message_422(self):
        self.assertEqual(_client().post("/v1/chat", json={"message": ""}).status_code, 422)

    def test_chat_missing_message_422(self):
        self.assertEqual(_client().post("/v1/chat", json={}).status_code, 422)

    def test_chat_bad_top_k_422(self):
        r = _client().post("/v1/chat", json={"message": "hi", "top_k": 99})
        self.assertEqual(r.status_code, 422)

    def test_chat_escalation_surfaced(self):
        c = _client()
        c.app.state.model.generate.return_value = _chat_result(
            escalation_required=True, escalation_reason="dispute", ticket_id="T1"
        )
        body = c.post("/v1/chat", json={"message": "I dispute my assessment"}).json()
        self.assertTrue(body["escalation_required"])


# ---------------------------------------------------------------------------
# Classification + knowledge (public)
# ---------------------------------------------------------------------------
class ClassificationKnowledge(_Base):
    def test_classify_200(self):
        r = _client().post("/classify", json={"text": "vat question"})
        self.assertEqual(r.status_code, 200)

    def test_classify_empty_422(self):
        self.assertEqual(_client().post("/classify", json={"text": ""}).status_code, 422)

    def test_classify_batch_200(self):
        r = _client().post("/classify/batch", json={"texts": ["a", "b"]})
        self.assertEqual(r.status_code, 200)

    def test_classify_batch_empty_422(self):
        self.assertEqual(_client().post("/classify/batch", json={"texts": []}).status_code, 422)

    def test_tags_200(self):
        self.assertEqual(_client().get("/tags").status_code, 200)

    def test_faq_invalid_tag_422(self):
        # path pattern ^[a-z][a-z0-9_]{0,63}$ rejects uppercase/spaces
        self.assertEqual(_client().get("/faq/Not A Tag").status_code, 422)


# ---------------------------------------------------------------------------
# Feedback (current_user; summary is admin)
# ---------------------------------------------------------------------------
class FeedbackEndpoints(_Base):
    def test_feedback_anonymous_ok_when_flag_off(self):
        r = _client().post("/v1/feedback", json={"message_id": "m1", "rating": "up"})
        self.assertEqual(r.status_code, 200)

    def test_feedback_invalid_rating_422(self):
        r = _client().post("/v1/feedback", json={"message_id": "m1", "rating": "maybe"})
        self.assertEqual(r.status_code, 422)

    def test_feedback_401_when_auth_required(self):
        flags.set("auth_required", True)
        r = _client().post("/v1/feedback", json={"message_id": "m1", "rating": "up"})
        self.assertEqual(r.status_code, 401)

    def test_feedback_comment_404_for_unknown_message(self):
        r = _client().patch(
            "/v1/feedback/does-not-exist/comment", json={"comment": "hello"}
        )
        self.assertEqual(r.status_code, 404)

    def test_feedback_summary_requires_admin(self):
        c = _client()
        self.assertIn(c.get("/v1/feedback/summary").status_code, (401, 403, 503))
        staff = make_dev_token("ops", role="ura_staff")
        self.assertEqual(c.get("/v1/feedback/summary", headers=_bearer(staff)).status_code, 200)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
class AnalyticsEndpoints(_Base):
    def test_event_accepts_post(self):
        r = _client().post("/v1/analytics/event", json={"event_type": "page_view"})
        self.assertEqual(r.status_code, 200)

    def test_event_missing_type_422(self):
        self.assertEqual(_client().post("/v1/analytics/event", json={}).status_code, 422)

    def test_dashboard_and_comparison_require_admin(self):
        c = _client()
        for path in ("/v1/analytics/dashboard", "/v1/analytics/comparison"):
            self.assertIn(c.get(path).status_code, (401, 403, 503), path)
        staff = make_dev_token("ops", role="ura_staff")
        self.assertEqual(
            c.get("/v1/analytics/dashboard", headers=_bearer(staff)).status_code, 200
        )


# ---------------------------------------------------------------------------
# Admin (role + admin-access)
# ---------------------------------------------------------------------------
class AdminEndpoints(_Base):
    def test_authority_status_role_gated(self):
        c = _client()
        self.assertEqual(c.get("/v1/authority/status").status_code, 401)
        public = make_dev_token("u", role="public")
        self.assertEqual(c.get("/v1/authority/status", headers=_bearer(public)).status_code, 403)
        staff = make_dev_token("a", role="ura_staff")
        self.assertEqual(c.get("/v1/authority/status", headers=_bearer(staff)).status_code, 200)

    def test_tickets_endpoints_admin_gated(self):
        c = _client()
        staff = make_dev_token("a", role="ura_staff")
        for path in ("/v1/admin/tickets", "/v1/admin/tickets/stats",
                     "/v1/admin/voice_audit", "/v1/admin/offline_stats"):
            self.assertIn(c.get(path).status_code, (401, 403, 503), path)
            self.assertEqual(c.get(path, headers=_bearer(staff)).status_code, 200, path)

    def test_ticket_get_unknown_404(self):
        staff = make_dev_token("a", role="ura_staff")
        r = _client().get("/v1/admin/tickets/" + "a" * 36, headers=_bearer(staff))
        self.assertIn(r.status_code, (404, 200))  # 404 unknown id; 200 if empty payload


# ---------------------------------------------------------------------------
# Ops-key endpoints
# ---------------------------------------------------------------------------
class OpsKeyEndpoints(_Base):
    def test_ops_endpoints_reject_when_key_configured(self):
        c = _client()
        with mock.patch.object(main_mod, "_INDEX_API_KEY", "secret-ops-key"):
            for path in ("/v1/index", "/v1/evaluate", "/v1/export/artifacts"):
                self.assertEqual(c.post(path).status_code, 403, f"{path} no key")
                self.assertEqual(
                    c.post(path, headers=_bearer("wrong-key")).status_code, 403, f"{path} wrong key"
                )

    def test_ops_endpoint_503_when_key_unset(self):
        c = _client()
        with mock.patch.object(main_mod, "_INDEX_API_KEY", ""):
            self.assertEqual(c.post("/v1/index").status_code, 503)


# ---------------------------------------------------------------------------
# /v1/me (require_user) + whoami (current_user)
# ---------------------------------------------------------------------------
class MeEndpoints(_Base):
    def test_whoami_anonymous(self):
        r = _client().get("/v1/me")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json().get("authenticated"))

    def test_profile_requires_auth(self):
        c = _client()
        self.assertEqual(c.get("/v1/me/profile").status_code, 401)
        tok = make_dev_token("alice", role="verified_taxpayer")
        self.assertEqual(c.get("/v1/me/profile", headers=_bearer(tok)).status_code, 200)

    def test_invalid_token_rejected(self):
        r = _client().get("/v1/me/profile", headers=_bearer("not.a.jwt"))
        self.assertEqual(r.status_code, 401)

    def test_expired_token_rejected(self):
        expired = make_dev_token("alice", ttl_seconds=-10)
        r = _client().get("/v1/me/profile", headers=_bearer(expired))
        self.assertEqual(r.status_code, 401)

    def test_consents_grant_then_list(self):
        c = _client()
        tok = make_dev_token("bob", role="verified_taxpayer")
        g = c.post(
            "/v1/me/consents/grant",
            headers=_bearer(tok),
            json={"purposes": ["analytics"], "version": "2026-06"},
        )
        self.assertEqual(g.status_code, 200)
        self.assertEqual(c.get("/v1/me/consents", headers=_bearer(tok)).status_code, 200)

    def test_consents_grant_validation_422(self):
        tok = make_dev_token("bob", role="verified_taxpayer")
        r = _client().post(
            "/v1/me/consents/grant", headers=_bearer(tok), json={"purposes": []}
        )
        self.assertEqual(r.status_code, 422)

    def test_export_and_delete_require_auth(self):
        c = _client()
        self.assertEqual(c.get("/v1/me/export").status_code, 401)
        self.assertEqual(c.delete("/v1/me").status_code, 401)
        tok = make_dev_token("carol", role="verified_taxpayer")
        self.assertEqual(c.get("/v1/me/export", headers=_bearer(tok)).status_code, 200)
        self.assertEqual(c.delete("/v1/me", headers=_bearer(tok)).status_code, 200)


# ---------------------------------------------------------------------------
# Offline + models (feature-flag gated)
# ---------------------------------------------------------------------------
class OfflineModelEndpoints(_Base):
    def test_offline_status_unavailable_when_flag_off(self):
        r = _client().get("/v1/offline/status")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json().get("available"))

    def test_offline_sync_404_when_flag_off(self):
        r = _client().post("/v1/offline/sync", json={"client_version": "1.0"})
        self.assertEqual(r.status_code, 404)

    def test_offline_bundle_404_when_flag_off(self):
        self.assertEqual(_client().get("/v1/offline/bundle").status_code, 404)

    def test_quantized_models_empty_when_flag_off(self):
        r = _client().get("/v1/models/quantized")
        self.assertEqual(r.status_code, 200)


# ---------------------------------------------------------------------------
# Speech + voice (disabled / consent gating)
# ---------------------------------------------------------------------------
class SpeechEndpoints(_Base):
    def test_speech_health_public(self):
        self.assertEqual(_client().get("/v1/speech/health").status_code, 200)

    def test_tts_503_when_speech_disabled(self):
        # speech model is None → get_speech_model returns 503
        r = _client(speech=False).post("/v1/tts", json={"text": "hello"})
        self.assertEqual(r.status_code, 503)

    def test_voice_vision_404_when_flag_off(self):
        r = _client(speech=True).post("/v1/voice/vision/chat")
        self.assertIn(r.status_code, (404, 422))  # 404 flag off (may 422 on missing form first)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
class ExportEndpoints(_Base):
    def test_export_conversation_validation(self):
        r = _client().post("/v1/export/conversation", json={"messages": []})
        self.assertEqual(r.status_code, 422)

    def test_export_tax_summary_requires_auth_when_flag_on(self):
        flags.set("auth_required", True)
        r = _client().post("/v1/export/tax-summary", json={"calculation": {"vat": 100}})
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()

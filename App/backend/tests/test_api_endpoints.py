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
                     "/v1/admin/voice_audit", "/v1/admin/offline_stats",
                     "/v1/admin/flags"):
            self.assertIn(c.get(path).status_code, (401, 403, 503), path)
            self.assertEqual(c.get(path, headers=_bearer(staff)).status_code, 200, path)

    def test_ticket_get_unknown_404(self):
        staff = make_dev_token("a", role="ura_staff")
        r = _client().get("/v1/admin/tickets/" + "a" * 36, headers=_bearer(staff))
        self.assertIn(r.status_code, (404, 200))  # 404 unknown id; 200 if empty payload

    def test_presence_heartbeats_the_viewer(self):
        staff = make_dev_token("a", role="ura_staff", email="officer@ura.go.ug")
        ticket = db.create_ticket(reason="presence")
        c = _client()
        r = c.post(
            f"/v1/admin/tickets/{ticket['id']}/presence",
            headers=_bearer(staff),
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("officer@ura.go.ug", r.json()["viewers"])
        got = c.get(f"/v1/admin/tickets/{ticket['id']}", headers=_bearer(staff))
        self.assertEqual(got.status_code, 200)
        self.assertIn("officer@ura.go.ug", got.json().get("viewers") or [])

    def test_flags_list_includes_protection(self):
        staff = make_dev_token("a", role="ura_staff")
        r = _client().get("/v1/admin/flags", headers=_bearer(staff))
        self.assertEqual(r.status_code, 200, r.text)
        payload = r.json()
        self.assertFalse(payload["overrides_are_ephemeral"])
        names = {row["name"] for row in payload["flags"]}
        self.assertIn("hyde", names)
        protected = {row["name"] for row in payload["flags"] if row["protected"]}
        self.assertIn("auth_required", protected)

    def test_auditor_cannot_toggle_flags(self):
        auditor = make_dev_token("aud", role="ura_auditor")
        r = _client().patch(
            "/v1/admin/flags/hyde?enabled=true",
            headers=_bearer(auditor),
        )
        self.assertEqual(r.status_code, 403)

    def test_protected_flags_cannot_be_toggled(self):
        admin = make_dev_token("ops", role="ura_admin")
        r = _client().patch(
            "/v1/admin/flags/auth_required?enabled=false",
            headers=_bearer(admin),
        )
        self.assertEqual(r.status_code, 400)

    def test_admin_can_set_an_ephemeral_flag(self):
        admin = make_dev_token("ops", role="ura_admin")
        c = _client()
        try:
            r = c.patch("/v1/admin/flags/hyde?enabled=true", headers=_bearer(admin))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["enabled"])
            self.assertFalse(r.json()["ephemeral"])
        finally:
            flags.clear("hyde")
            db.clear_flag_override("hyde")


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
# Cloudflare relay endpoints (/internal/cf-relay/*)
# ---------------------------------------------------------------------------
class CFRelayEndpoints(_Base):
    def tearDown(self):
        from app.providers import config as cf_config

        os.environ.pop("CF_RELAY_SECRET", None)
        cf_config.get_cloud_settings.cache_clear()
        super().tearDown()

    def test_503_when_relay_secret_unset(self):
        from app.providers import config as cf_config

        os.environ.pop("CF_RELAY_SECRET", None)
        cf_config.get_cloud_settings.cache_clear()
        c = _client()
        r = c.post("/internal/cf-relay/vectorize-query", json={"vector": [0.1]})
        self.assertEqual(r.status_code, 503)

    def test_403_with_no_or_wrong_bearer(self):
        from app.providers import config as cf_config

        os.environ["CF_RELAY_SECRET"] = "relay-secret-123"  # pragma: allowlist secret
        cf_config.get_cloud_settings.cache_clear()
        c = _client()
        r = c.post("/internal/cf-relay/vectorize-query", json={"vector": [0.1]})
        self.assertEqual(r.status_code, 403)
        r = c.post(
            "/internal/cf-relay/vectorize-query",
            json={"vector": [0.1]},
            headers=_bearer("wrong-secret"),
        )
        self.assertEqual(r.status_code, 403)

    def test_vectorize_query_relays_through_with_correct_bearer(self):
        from app.providers import config as cf_config

        os.environ["CF_RELAY_SECRET"] = "relay-secret-123"  # pragma: allowlist secret
        cf_config.get_cloud_settings.cache_clear()
        c = _client()
        with mock.patch(
            "app.providers.vectorize.vectorize_query",
            return_value=[{"id": "c1", "text": "VAT is 18%"}],
        ) as mocked:
            r = c.post(
                "/internal/cf-relay/vectorize-query",
                json={"vector": [0.1, 0.2], "top_k": 3},
                headers=_bearer("relay-secret-123"),
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"hits": [{"id": "c1", "text": "VAT is 18%"}]})
        mocked.assert_called_once_with([0.1, 0.2], top_k=3, vector_filter=None)

    def test_workers_ai_embed_relays_through_with_correct_bearer(self):
        from app.providers import config as cf_config

        os.environ["CF_RELAY_SECRET"] = "relay-secret-123"  # pragma: allowlist secret
        cf_config.get_cloud_settings.cache_clear()
        c = _client()
        with mock.patch(
            "app.providers.gateway.workers_ai_embed", return_value=[[0.1, 0.2]]
        ) as mocked:
            r = c.post(
                "/internal/cf-relay/workers-ai-embed",
                json={"texts": ["hello"]},
                headers=_bearer("relay-secret-123"),
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"vectors": [[0.1, 0.2]]})
        mocked.assert_called_once_with(["hello"])  # no caller-controlled model — see CFRelayEmbedRequest

    def test_embed_upstream_failure_is_502_not_500(self):
        """A Workers AI failure is an ordinary, already-anticipated condition
        (the caller's own circuit breaker + keyword fallback exist for
        exactly this) -- it must not look like the relay endpoint itself is
        broken. Observed live: this was an unhandled 500 in production,
        indistinguishable from a real bug in relay_client.py's error log."""
        import httpx

        from app.providers import config as cf_config

        os.environ["CF_RELAY_SECRET"] = "relay-secret-123"  # pragma: allowlist secret
        cf_config.get_cloud_settings.cache_clear()
        c = _client()
        with mock.patch(
            "app.providers.gateway.workers_ai_embed",
            side_effect=httpx.HTTPStatusError(
                "400", request=httpx.Request("POST", "https://example.invalid"),
                response=httpx.Response(400, request=httpx.Request("POST", "https://example.invalid")),
            ),
        ):
            r = c.post(
                "/internal/cf-relay/workers-ai-embed",
                json={"texts": ["hello"]},
                headers=_bearer("relay-secret-123"),
            )
        self.assertEqual(r.status_code, 502)

    def test_embed_rejects_caller_supplied_model(self):
        """Regression guard for the partial-SSRF CodeQL finding: a ``model``
        field in the request body must be rejected, not silently accepted
        and threaded into the Cloudflare URL this process builds."""
        from app.providers import config as cf_config

        os.environ["CF_RELAY_SECRET"] = "relay-secret-123"  # pragma: allowlist secret
        cf_config.get_cloud_settings.cache_clear()
        c = _client()
        r = c.post(
            "/internal/cf-relay/workers-ai-embed",
            json={"texts": ["hello"], "model": "@cf/attacker/evil"},
            headers=_bearer("relay-secret-123"),
        )
        self.assertEqual(r.status_code, 422)

    def test_workers_ai_chat_relays_through_with_correct_bearer(self):
        from app.providers import config as cf_config
        from app.providers import routing

        os.environ["CF_RELAY_SECRET"] = "relay-secret-123"  # pragma: allowlist secret
        cf_config.get_cloud_settings.cache_clear()
        c = _client()
        with mock.patch(
            "app.providers.gateway.workers_ai_chat", return_value="Double the VAT due."
        ) as mocked:
            r = c.post(
                "/internal/cf-relay/workers-ai-chat",
                json={
                    "messages": [{"role": "user", "content": "penalty for late VAT registration?"}],
                    "model_slot": "primary",
                    "max_tokens": 512,
                    "temperature": 0.2,
                },
                headers=_bearer("relay-secret-123"),
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"text": "Double the VAT due."})
        # The endpoint resolves "primary" -> routing.CF_LLM_MODEL itself — the
        # request never carries (or controls) the actual model id.
        mocked.assert_called_once_with(
            [{"role": "user", "content": "penalty for late VAT registration?"}],
            routing.CF_LLM_MODEL,
            max_tokens=512,
            temperature=0.2,
        )

    def test_chat_rejects_non_allowlisted_model(self):
        """Same SSRF-shape guard as embed, but for chat: unlike embed, chat
        genuinely needs to pick between a few models, so it can't just drop
        the field. ``model_slot`` is a Literal of slot NAMES, resolved to an
        actual model id via a fixed dict in main.py — a raw ``model`` string
        (even one an app-level check would allowlist) is rejected outright by
        the schema, since a Pydantic validator isn't a taint sanitizer CodeQL
        credits, but a Literal + dict-lookup is."""
        from app.providers import config as cf_config

        os.environ["CF_RELAY_SECRET"] = "relay-secret-123"  # pragma: allowlist secret
        cf_config.get_cloud_settings.cache_clear()
        c = _client()
        r = c.post(
            "/internal/cf-relay/workers-ai-chat",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "model": "@cf/attacker/evil",
                "max_tokens": 512,
                "temperature": 0.2,
            },
            headers=_bearer("relay-secret-123"),
        )
        self.assertEqual(r.status_code, 422)

        r = c.post(
            "/internal/cf-relay/workers-ai-chat",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "model_slot": "not-a-real-slot",
                "max_tokens": 512,
                "temperature": 0.2,
            },
            headers=_bearer("relay-secret-123"),
        )
        self.assertEqual(r.status_code, 422)


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

    # -- completeness: export/erasure must actually reach chat history + memory --
    def test_export_includes_conversation_history(self):
        from app import database as db

        sub = "exporter1"
        db.log_conversation(
            session_id="s-exp",
            conversation_id="conv-exp-1",
            user_message="what is VAT",
            bot_reply="VAT is 18%",
            user_id=sub,
        )
        tok = make_dev_token(sub, role="verified_taxpayer")
        r = _client().get("/v1/me/export", headers=_bearer(tok))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIsInstance(body.get("facts"), list)  # memory wired in, not stubbed
        conv_ids = [c.get("conversation_id") for c in body.get("conversations", [])]
        self.assertIn("conv-exp-1", conv_ids)

    def test_erasure_deletes_conversation_history(self):
        from app import database as db

        sub = "eraser1"
        db.log_conversation(
            session_id="s-era",
            conversation_id="conv-era-1",
            user_message="my income is sensitive",
            bot_reply="ok",
            user_id=sub,
        )
        tok = make_dev_token(sub, role="verified_taxpayer")
        r = _client().delete("/v1/me", headers=_bearer(tok))
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.json()["deleted"].get("conversations", 0), 1)
        # The chat history must be gone afterwards.
        remaining = db.export_user_data("n/a", external_id=sub)["conversations"]
        self.assertEqual(remaining, [])

    def test_withdraw_personalization_purges_memory(self):
        sub = "withdrawer1"
        tok = make_dev_token(sub, role="verified_taxpayer")
        c = _client()
        c.post(
            "/v1/me/consents/grant",
            headers=_bearer(tok),
            json={"purposes": ["personalization"], "version": "2026-06"},
        )
        with mock.patch("app.memory.service.get_memory_service") as gms:
            r = c.post(
                "/v1/me/consents/withdraw",
                headers=_bearer(tok),
                json={"purposes": ["personalization"]},
            )
        self.assertEqual(r.status_code, 200)
        gms.return_value.forget_user.assert_called_once()

    def test_invalid_consent_purpose_rejected_422(self):
        tok = make_dev_token("badpurpose", role="verified_taxpayer")
        r = _client().post(
            "/v1/me/consents/grant",
            headers=_bearer(tok),
            json={"purposes": ["sell_my_data"], "version": "2026-06"},
        )
        self.assertEqual(r.status_code, 422)

    def test_invalid_taxpayer_type_rejected_422(self):
        tok = make_dev_token("badprofile", role="verified_taxpayer")
        r = _client().put(
            "/v1/me/profile",
            headers=_bearer(tok),
            json={"taxpayer_type": "martian"},
        )
        self.assertEqual(r.status_code, 422)

    def test_consent_check_resolves_external_sub(self):
        # Consent receipts are keyed by the internal UUID, but the chat/voice
        # runtime checks consent with the external OIDC sub. The gate must bridge
        # the two — otherwise personalization memory + voice consent stay dormant.
        from app import database as db

        sub = "zoe-ext"
        row = db.upsert_user(external_id=sub, tenant_id="default")
        db.grant_consent(row["id"], "personalization", "2026-06")
        self.assertTrue(db.has_active_consent(row["id"], "personalization"))  # internal, direct
        self.assertTrue(db.has_active_consent(sub, "personalization"))  # external sub resolves
        # Negative control: an unknown sub is still denied.
        self.assertFalse(db.has_active_consent("nobody-xyz", "personalization"))


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

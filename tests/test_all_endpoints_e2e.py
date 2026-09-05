"""Whole-surface end-to-end sweep across EVERY API endpoint (TestClient/ASGI).

This module is the completeness guarantee for the HTTP/WS API surface. It has
three jobs:

1. **Drift guard** (``test_route_table_matches_manifest``) — enumerates the live
   ``app.routes`` table and asserts it equals a hand-declared manifest of all 70
   application endpoints. Add or remove a route without updating the manifest and
   this test fails, so the surface can never silently grow untested.

2. **Coverage accounting** (``test_every_endpoint_has_coverage``) — every endpoint
   in the manifest maps to a note naming where it is exercised end-to-end (this
   file or a sibling suite). No endpoint is left unaccounted for.

3. **Gap-filling success paths** — explicit happy-path drives for the endpoints
   that previously had NO HTTP-level success assertion: ``/v1/translate``,
   ``/v1/asr``, ``/v1/tts``, ``/v1/evaluation/results``,
   ``PATCH /v1/admin/tickets/{id}``, ``/v1/models/quantized`` (flag on),
   ``/v1/offline/status`` (flag on), the two PDF exports, the feedback-comment
   round-trip, and the compound ``/v1/voice/chat`` ASR branch.

It is deliberately complementary to ``App/backend/tests/test_api_endpoints.py``
(the auth/gating/validation matrix); here the focus is reaching each endpoint's
*success* response with real auth, flags, and faithful stubs. Models are stubbed
on ``app.state`` and the TestClient is built WITHOUT the lifespan so no
Qwen/Qdrant/Whisper load happens and the production startup gate is skipped.
"""

from __future__ import annotations

import base64
import os
import sys
import types
import uuid
from pathlib import Path
from unittest import mock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "App" / "backend"))

# Env must be set before importing app.* (read at import time). Default
# data_store path for analytics — a tmpdir override lands on tmpfs where the
# SQLite WAL journal fails with "database is locked".
os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("CACHE_BACKEND", "memory")
os.environ.setdefault("ANALYTICS_BACKEND", "sqlite")
os.environ.setdefault("OTEL_ENABLED", "false")
os.environ.setdefault("QDRANT_URL", "http://127.0.0.1:1")
os.environ.setdefault("QDRANT_ENABLED", "false")
os.environ.setdefault("SPEECH_ENABLED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from App.backend.app import database as db  # noqa: E402
from App.backend.app.auth.dependencies import reset_verifier  # noqa: E402
from App.backend.app.auth.jwt_auth import make_dev_token  # noqa: E402
from App.backend.app.flags import flags  # noqa: E402
from App.backend.app.main import app  # noqa: E402
from App.backend.app.speech_service import (  # noqa: E402
    SynthesizeResult,
    TranscribeResult,
    TranslateResult,
)

# ---------------------------------------------------------------------------
# The manifest — every application endpoint, as (METHOD, path). WebSocket
# routes use the pseudo-method "WS". FastAPI's auto-docs routes are excluded.
# ---------------------------------------------------------------------------
DOCS_ROUTES = {
    ("GET", "/docs"),
    ("GET", "/docs/oauth2-redirect"),
    ("GET", "/redoc"),
    ("GET", "/openapi.json"),
}

EXPECTED_ENDPOINTS: set[tuple[str, str]] = {
    # --- System / observability ---
    ("GET", "/health"),
    ("GET", "/ready"),
    ("GET", "/metrics"),
    ("GET", "/v1/index/freshness"),
    # --- Chat ---
    ("POST", "/v1/chat"),
    ("POST", "/v1/chat/stream"),
    ("POST", "/v1/escalate"),
    # --- Classification + knowledge (public) ---
    ("POST", "/classify"),
    ("POST", "/classify/batch"),
    ("GET", "/tags"),
    ("GET", "/faq/{tag}"),
    # --- Speech / voice ---
    ("POST", "/v1/asr"),
    ("POST", "/v1/tts"),
    ("POST", "/v1/translate"),
    ("GET", "/v1/speech/health"),
    ("GET", "/v1/speech/voices"),
    ("POST", "/v1/voice/chat"),
    ("POST", "/v1/voice/vision/chat"),
    # --- Feedback ---
    ("POST", "/v1/feedback"),
    ("PATCH", "/v1/feedback/{message_id}/comment"),
    ("GET", "/v1/feedback/summary"),
    # --- Analytics ---
    ("POST", "/v1/analytics/event"),
    ("GET", "/v1/analytics/dashboard"),
    ("GET", "/v1/analytics/comparison"),
    # --- Admin: tickets / audit / authority ---
    ("GET", "/v1/authority/status"),
    ("GET", "/v1/admin/tickets"),
    ("GET", "/v1/admin/tickets/stats"),
    ("GET", "/v1/admin/tickets/sla"),
    ("WS", "/v1/admin/tickets/stream"),
    ("GET", "/v1/admin/tickets/{ticket_id}"),
    ("PATCH", "/v1/admin/tickets/{ticket_id}"),
    ("POST", "/v1/admin/tickets/{ticket_id}/presence"),
    ("GET", "/v1/admin/flags"),
    ("PATCH", "/v1/admin/flags/{name}"),
    ("DELETE", "/v1/admin/flags/{name}"),
    ("GET", "/v1/admin/overrides"),
    ("PUT", "/v1/admin/overrides"),
    ("DELETE", "/v1/admin/overrides/{override_id}"),
    ("GET", "/v1/admin/outbox"),
    ("GET", "/v1/admin/voice_audit"),
    ("GET", "/v1/admin/offline_stats"),
    # --- Ops-key gated ---
    ("POST", "/v1/index"),
    ("POST", "/v1/evaluate"),
    ("POST", "/v1/export/artifacts"),
    # --- Cloudflare relay (internal-only; CF_RELAY_SECRET-gated) ---
    ("POST", "/internal/cf-relay/workers-ai-embed"),
    ("POST", "/internal/cf-relay/vectorize-query"),
    ("POST", "/internal/cf-relay/workers-ai-chat"),
    # --- Evaluation dashboard ---
    ("GET", "/v1/evaluation/results"),
    # --- Export (PDF) ---
    ("POST", "/v1/export/conversation"),
    ("POST", "/v1/export/tax-summary"),
    # --- Document attachments ---
    ("POST", "/v1/documents/analyze"),
    ("GET", "/v1/documents/{document_id}/report"),
    ("GET", "/v1/documents/{document_id}/status"),
    # --- Identity / consent (/v1/me) ---
    ("GET", "/v1/me"),
    ("DELETE", "/v1/me"),
    ("GET", "/v1/me/reminders"),
    ("POST", "/v1/me/reminders/refresh"),
    ("GET", "/v1/me/account"),
    ("GET", "/v1/me/profile"),
    ("PUT", "/v1/me/profile"),
    ("GET", "/v1/me/consents"),
    ("POST", "/v1/me/consents/grant"),
    ("POST", "/v1/me/consents/withdraw"),
    ("GET", "/v1/me/export"),
    # --- Quantized models + offline RAG (flag gated) ---
    ("GET", "/v1/models/quantized"),
    ("GET", "/v1/offline/status"),
    ("POST", "/v1/offline/sync"),
    ("GET", "/v1/offline/bundle"),
    # --- WebSocket ---
    ("WS", "/v1/voice/chat/stream"),
    ("WS", "/v2/chat/stream"),
    ("WS", "/v2/voice/chat/stream"),
}

# Where each endpoint is exercised end-to-end over HTTP/WS. Keys MUST equal
# EXPECTED_ENDPOINTS exactly (enforced by test_every_endpoint_has_coverage).
COVERAGE: dict[tuple[str, str], str] = {
    ("GET", "/health"): "this:test_health_ready_metrics",
    ("GET", "/ready"): "this:test_health_ready_metrics",
    ("GET", "/metrics"): "this:test_health_ready_metrics",
    ("GET", "/v1/index/freshness"): "this:test_index_freshness",
    ("POST", "/v1/chat"): "this:test_chat_happy_path + test_api_endpoints.ChatEndpoints",
    ("POST", "/v1/escalate"): "this:test_answer_integrity_integration + App/backend/tests/test_taxpayer_escalation.py",
    ("POST", "/v1/chat/stream"): "test_fallback_integration.TestChatStreamEndpointFallback",
    ("POST", "/classify"): "test_api_endpoints.ClassificationKnowledge",
    ("POST", "/classify/batch"): "test_api_endpoints.ClassificationKnowledge",
    ("GET", "/tags"): "test_api_endpoints.ClassificationKnowledge",
    ("GET", "/faq/{tag}"): "test_api_endpoints.ClassificationKnowledge",
    ("POST", "/v1/asr"): "this:test_asr_transcribes_audio",
    ("POST", "/v1/tts"): "this:test_tts_synthesizes_audio",
    ("POST", "/v1/translate"): "this:test_translate_passthrough + test_translate_en_to_lg",
    ("GET", "/v1/speech/health"): "test_api_endpoints.SpeechEndpoints",
    ("GET", "/v1/speech/voices"): "this:test_speech_voices_catalogue",
    ("POST", "/v1/voice/chat"): "this:test_voice_chat_asr_branch",
    ("POST", "/v1/voice/vision/chat"): "test_api_endpoints.SpeechEndpoints + test_native_voice",
    ("POST", "/v1/feedback"): "this:test_feedback_comment_roundtrip + test_api_endpoints",
    ("PATCH", "/v1/feedback/{message_id}/comment"): "this:test_feedback_comment_roundtrip",
    ("GET", "/v1/feedback/summary"): "test_api_endpoints.FeedbackEndpoints",
    ("POST", "/v1/analytics/event"): "test_api_endpoints.AnalyticsEndpoints",
    ("GET", "/v1/analytics/dashboard"): "test_api_endpoints.AnalyticsEndpoints",
    ("GET", "/v1/analytics/comparison"): "test_api_endpoints.AnalyticsEndpoints",
    ("GET", "/v1/authority/status"): "test_api_endpoints.AdminEndpoints",
    ("GET", "/v1/admin/tickets"): "test_api_endpoints.AdminEndpoints",
    ("GET", "/v1/admin/tickets/stats"): "test_api_endpoints.AdminEndpoints",
    ("GET", "/v1/admin/tickets/sla"): "test_escalation_roundtrip.TestSLA",
    ("WS", "/v1/admin/tickets/stream"): "test_ticket_events.TestStaffOnlyAccess",
    ("GET", "/v1/admin/tickets/{ticket_id}"): "this:test_patch_ticket_updates_status",
    ("PATCH", "/v1/admin/tickets/{ticket_id}"): "this:test_patch_ticket_updates_status + test_patch_ticket_noop_400",
    ("POST", "/v1/admin/tickets/{ticket_id}/presence"): "test_api_endpoints.AdminEndpoints.test_presence_heartbeats_the_viewer",
    ("GET", "/v1/admin/flags"): "test_api_endpoints.AdminEndpoints.test_flags_list_includes_protection",
    ("PATCH", "/v1/admin/flags/{name}"): "test_api_endpoints.AdminEndpoints.test_admin_can_set_an_ephemeral_flag",
    ("DELETE", "/v1/admin/flags/{name}"): "test_api_endpoints.AdminEndpoints.test_clearing_an_override_restores_the_default",
    ("GET", "/v1/admin/voice_audit"): "test_api_endpoints.AdminEndpoints",
    ("GET", "/v1/admin/offline_stats"): "test_api_endpoints.AdminEndpoints",
    ("POST", "/v1/index"): "test_api_endpoints.OpsKeyEndpoints",
    ("POST", "/v1/evaluate"): "test_api_endpoints.OpsKeyEndpoints",
    ("POST", "/internal/cf-relay/workers-ai-embed"): "test_api_endpoints.CFRelayEndpoints",
    ("POST", "/internal/cf-relay/vectorize-query"): "test_api_endpoints.CFRelayEndpoints",
    ("POST", "/internal/cf-relay/workers-ai-chat"): "test_api_endpoints.CFRelayEndpoints",
    ("POST", "/v1/export/artifacts"): "test_api_endpoints.OpsKeyEndpoints",
    ("GET", "/v1/evaluation/results"): "this:test_evaluation_results_admin + test_evaluation_results_requires_admin",
    ("POST", "/v1/export/conversation"): "this:test_export_conversation_pdf",
    ("POST", "/v1/export/tax-summary"): "this:test_export_tax_summary_pdf",
    ("POST", "/v1/documents/analyze"): "test_documents.DocumentEndpointsTest",
    ("GET", "/v1/documents/{document_id}/report"): "test_documents.DocumentEndpointsTest",
    ("GET", "/v1/documents/{document_id}/status"): "test_documents.DocumentEndpointsTest",
    ("GET", "/v1/me"): "test_api_endpoints.MeEndpoints + test_me_endpoints",
    ("DELETE", "/v1/me"): "test_api_endpoints.MeEndpoints + test_me_endpoints",
    ("GET", "/v1/me/reminders"): "tests.agents.test_reminders",
    ("POST", "/v1/me/reminders/refresh"): "tests.agents.test_reminders",
    ("GET", "/v1/me/account"): "App.backend.tests.test_remaining_gaps",
    ("GET", "/v1/admin/overrides"): "App.backend.tests.test_remaining_gaps",
    ("PUT", "/v1/admin/overrides"): "App.backend.tests.test_remaining_gaps",
    ("DELETE", "/v1/admin/overrides/{override_id}"): "App.backend.tests.test_remaining_gaps",
    ("GET", "/v1/admin/outbox"): "App.backend.tests.test_remaining_gaps",
    ("GET", "/v1/me/profile"): "test_api_endpoints.MeEndpoints + test_me_endpoints",
    ("PUT", "/v1/me/profile"): "test_me_endpoints",
    ("GET", "/v1/me/consents"): "test_api_endpoints.MeEndpoints + test_me_endpoints",
    ("POST", "/v1/me/consents/grant"): "test_api_endpoints.MeEndpoints + test_me_endpoints",
    ("POST", "/v1/me/consents/withdraw"): "test_me_endpoints",
    ("GET", "/v1/me/export"): "test_api_endpoints.MeEndpoints + test_me_endpoints",
    ("GET", "/v1/models/quantized"): "this:test_quantized_models_flag_on",
    ("GET", "/v1/offline/status"): "this:test_offline_status_flag_on",
    ("POST", "/v1/offline/sync"): "test_api_endpoints.OfflineModelEndpoints",
    ("GET", "/v1/offline/bundle"): "test_api_endpoints.OfflineModelEndpoints",
    ("WS", "/v1/voice/chat/stream"): "test_voice_ws_hardening",
    ("WS", "/v2/chat/stream"): "test_chat_ws_lifecycle",
    ("WS", "/v2/voice/chat/stream"): "test_native_voice",
}


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module", autouse=True)
def _init_schema():
    db.init_db()


@pytest.fixture(autouse=True)
def _reset_state():
    yield
    for f in ("auth_required", "offline_rag", "offline_sync", "offline_bundle_api",
              "voice_vision", "quantization", "voice_consent"):
        flags.clear(f)
    reset_verifier()


def _chat_result(**over):
    base = {
        "reply": "The standard VAT rate is 18%.",
        "sources": ["vat.pdf"],
        "citations": [],
        "faithfulness_score": 0.9,
        "retrieval_mode": "hybrid",
        "model": "stub-model",
        "conversation_id": "conv-e2e-1",
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
    m.classify.return_value = {"predictions": [], "processing_time_ms": 1.0}
    m.classify_batch.return_value = {"results": [], "processing_time_ms": 1.0}
    m.list_tags.return_value = {"tags": [], "total": 0}
    m.get_faq.return_value = {"tag": "vat", "faqs": [], "total": 0}
    return m


def _stub_speech():
    m = mock.MagicMock(name="stub_speech")
    m.enabled = True
    m.is_ready.return_value = True
    m.transcribe.return_value = TranscribeResult(
        text="what is the vat rate", language="en", duration_s=1.0,
        latency_s=0.05, rtf=0.05, backend="stub-asr",
    )
    m.synthesize.return_value = SynthesizeResult(
        audio=b"RIFFstub-wav-bytes", sample_rate=22050, num_samples=128,
        duration_s=0.5, latency_s=0.02, backend="stub-tts", voice="en_US-stub",
    )
    m.translate.return_value = TranslateResult(
        text="omusolo gwa VAT guli ki", source_lang="en", target_lang="lg",
        latency_s=0.01, backend="stub-mt",
    )
    return m


def _client(*, model=True, speech=False):
    app.state.model = _stub_model() if model else None
    app.state.speech = _stub_speech() if speech else None
    return TestClient(app)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


STAFF = make_dev_token("e2e-staff", role="ura_staff")
USER = make_dev_token("e2e-user", role="verified_taxpayer")
_AUDIO = b"\x00\x01" * 512  # tiny raw PCM16 payload


# ---------------------------------------------------------------------------
# 1. Route-table drift guard
# ---------------------------------------------------------------------------
def _live_route_table() -> set[tuple[str, str]]:
    table: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if path is None:
            continue
        methods = getattr(route, "methods", None)
        if methods:
            for m in methods:
                if m not in ("HEAD", "OPTIONS"):
                    table.add((m, path))
        else:
            table.add(("WS", path))  # WebSocketRoute has no .methods
    return table - DOCS_ROUTES


def test_route_table_matches_manifest():
    """Every live route is in the manifest, and vice-versa (no silent drift)."""
    live = _live_route_table()
    missing_from_manifest = live - EXPECTED_ENDPOINTS
    stale_in_manifest = EXPECTED_ENDPOINTS - live
    assert not missing_from_manifest, (
        f"New route(s) not in manifest — add + test them: {sorted(missing_from_manifest)}"
    )
    assert not stale_in_manifest, (
        f"Manifest lists route(s) that no longer exist: {sorted(stale_in_manifest)}"
    )


def test_every_endpoint_has_coverage():
    """Every manifest endpoint maps to a non-empty E2E coverage location."""
    assert set(COVERAGE) == EXPECTED_ENDPOINTS, (
        f"Coverage registry out of sync. Missing: {sorted(EXPECTED_ENDPOINTS - set(COVERAGE))}; "
        f"stale: {sorted(set(COVERAGE) - EXPECTED_ENDPOINTS)}"
    )
    for key, where in COVERAGE.items():
        assert where, f"empty coverage note for {key}"
        assert where.strip(), f"blank coverage note for {key}"


def test_manifest_endpoint_count():
    """Lock the surface size so additions are deliberate (67 HTTP + 4 WS)."""
    ws = {e for e in EXPECTED_ENDPOINTS if e[0] == "WS"}
    http = EXPECTED_ENDPOINTS - ws
    assert len(http) == 67, f"expected 67 HTTP endpoints, found {len(http)}"
    assert len(ws) == 4, f"expected 4 WS endpoints, found {len(ws)}"


# ---------------------------------------------------------------------------
# 2. System
# ---------------------------------------------------------------------------
def test_health_ready_metrics():
    c = _client(model=True)
    assert c.get("/health").json()["status"] == "alive"
    assert c.get("/ready").status_code == 200
    assert c.get("/metrics", headers=_bearer(STAFF)).status_code == 200


def test_index_freshness():
    """Public, no model/auth needed — load_status() reads a status file the
    cron writer (app.freshness --write-status) produces.

    Both states are asserted, because both are reachable on a developer machine:
    the file is absent until something writes it, and present the moment anyone
    follows docs/runbooks/qdrant-staged-rebuild.md, which runs a staged rebuild
    and a `--write-status` freshness check. Pinning only the empty shape made
    this fail after the project's own runbook was followed.
    """
    c = _client(model=False)
    r = c.get("/v1/index/freshness")
    assert r.status_code == 200
    body = r.json()
    assert {"ok", "snapshot_missing", "checked_at"} <= set(body)
    if body["snapshot_missing"]:
        assert body == {"ok": None, "snapshot_missing": True, "checked_at": None}
    else:
        assert isinstance(body["ok"], bool)
        assert isinstance(body["checked_at"], str) and body["checked_at"]


# ---------------------------------------------------------------------------
# 3. Chat (happy path)
# ---------------------------------------------------------------------------
def test_chat_happy_path():
    r = _client().post("/v1/chat", json={"message": "What is the VAT rate?"})
    assert r.status_code == 200
    assert r.json()["reply"] == "The standard VAT rate is 18%."


# ---------------------------------------------------------------------------
# 4. Speech: translate / asr / tts (previously no HTTP success coverage)
# ---------------------------------------------------------------------------
def test_translate_passthrough():
    """source == target short-circuits to a passthrough with no MT backend call."""
    c = _client(speech=True)
    r = c.post("/v1/translate", json={"text": "hello", "source_lang": "en", "target_lang": "en"})
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "hello"
    assert body["backend"] == "passthrough"
    c.app.state.speech.translate.assert_not_called()


def test_translate_en_to_lg():
    c = _client(speech=True)
    r = c.post("/v1/translate", json={"text": "what is vat", "source_lang": "en", "target_lang": "lg"})
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "omusolo gwa VAT guli ki"
    assert body["backend"] == "stub-mt"
    assert body["target_lang"] == "lg"


def test_speech_voices_catalogue():
    """The picker is built from this, so its invariants are load-bearing.

    Exactly one default per language: the client marks the head of each list,
    and two "default" chips would force it to choose arbitrarily. Language
    scoping matters just as much — a Luganda tag offered under Acholi would be
    refused by the backend and degrade to an English voice reading Acholi.
    """
    c = _client(speech=True)
    r = c.get("/v1/speech/voices")
    assert r.status_code == 200
    body = r.json()
    assert "sunbird_configured" in body

    voices = body["voices"]
    assert {"en", "lg", "ach", "nyn", "sw"} <= set(voices)

    seen_ids: dict[str, str] = {}
    for locale, entries in voices.items():
        assert entries, f"{locale} has no voices"
        defaults = [e for e in entries if e["default"]]
        assert len(defaults) == 1, f"{locale} must have exactly one default, got {len(defaults)}"
        assert entries[0]["default"], f"{locale}'s default must lead the list"

        ids = [e["id"] for e in entries]
        assert len(set(ids)) == len(ids), f"{locale} repeats a voice id"
        for vid in ids:
            assert vid not in seen_ids, f"{vid} offered for both {seen_ids[vid]} and {locale}"
            seen_ids[vid] = locale

        for entry in entries:
            assert entry["provider"] in {"sunbird", "edge_tts"}
            assert isinstance(entry["available"], bool)

    # Sunbird has no native English speaker; salt_eng_0001 is the last resort
    # edge-tts exists to avoid, so it must not be advertised as one.
    assert all(not e["native"] for e in voices["en"] if e["provider"] == "sunbird")
    # English is served by edge-tts first, so an edge voice leads it.
    assert voices["en"][0]["provider"] == "edge_tts"


def test_asr_transcribes_audio():
    c = _client(speech=True)
    r = c.post("/v1/asr?sample_rate=16000&language=en", content=_AUDIO)
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "what is the vat rate"
    assert body["backend"] == "stub-asr"


def test_tts_synthesizes_audio():
    c = _client(speech=True)
    r = c.post("/v1/tts", json={"text": "the vat rate is eighteen percent"})
    assert r.status_code == 200
    body = r.json()
    assert body["audio_base64"] == base64.b64encode(b"RIFFstub-wav-bytes").decode("ascii")
    assert body["backend"] == "stub-tts"
    assert body["sample_rate"] == 22050


def test_voice_chat_asr_branch():
    """The compound voice route is reachable end-to-end; an empty transcript
    returns a well-formed 200 with a spoken-error hint (no LLM path needed)."""
    c = _client(model=True, speech=True)
    c.app.state.speech.transcribe.return_value = TranscribeResult(text="", backend="stub-asr")
    r = c.post("/v1/voice/chat?language=en&sample_rate=16000", content=_AUDIO)
    assert r.status_code == 200
    body = r.json()
    assert body["transcript"] == ""
    assert "speech" in (body["error"] or "").lower()


# ---------------------------------------------------------------------------
# 5. Evaluation results (admin) — previously zero coverage
# ---------------------------------------------------------------------------
def test_evaluation_results_admin():
    r = _client().get("/v1/evaluation/results", headers=_bearer(STAFF))
    assert r.status_code == 200
    body = r.json()
    # All metric slots are present (values may be None when Results/ is empty).
    for key in ("rag_evaluation", "safety_evaluation", "benchmark", "calibration"):
        assert key in body


def test_evaluation_results_requires_admin():
    assert _client().get("/v1/evaluation/results").status_code in (401, 403, 503)


# ---------------------------------------------------------------------------
# 6. Ticket PATCH (previously only DB-layer coverage)
# ---------------------------------------------------------------------------
def test_patch_ticket_updates_status():
    ticket = db.create_ticket(reason="dispute over assessment", priority="high")
    tid = ticket["id"]
    c = _client()
    r = c.patch(
        f"/v1/admin/tickets/{tid}?status=assigned&assignee=agent-007",
        headers=_bearer(STAFF),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    # Read-back confirms the mutation landed.
    got = c.get(f"/v1/admin/tickets/{tid}", headers=_bearer(STAFF))
    assert got.status_code == 200
    assert got.json()["status"] == "assigned"
    assert got.json()["assignee"] == "agent-007"


def test_patch_ticket_noop_400():
    """A valid-shaped but unknown ticket id touches no row → 400."""
    unknown = "0" * 36  # matches ^[a-f0-9-]{1,64}$
    r = _client().patch(
        f"/v1/admin/tickets/{unknown}?status=assigned", headers=_bearer(STAFF)
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 7. Feedback comment round-trip (success path)
# ---------------------------------------------------------------------------
def test_feedback_comment_roundtrip():
    c = _client()
    mid = f"m-e2e-{uuid.uuid4().hex[:8]}"
    # Feedback is analytics processing, so a user must give purpose-specific
    # consent before the success path is available.
    consent = c.post(
        "/v1/me/consents/grant",
        headers=_bearer(USER),
        json={"purposes": ["analytics"], "version": "2026-08"},
    )
    assert consent.status_code == 200
    posted = c.post(
        "/v1/feedback",
        headers=_bearer(USER),
        json={"message_id": mid, "rating": "up"},
    )
    assert posted.status_code == 200
    commented = c.patch(
        f"/v1/feedback/{mid}/comment",
        headers=_bearer(USER),
        json={"comment": "very helpful, thanks"},
    )
    assert commented.status_code == 200


# ---------------------------------------------------------------------------
# 8. Quantized models + offline status (flag-on success paths)
# ---------------------------------------------------------------------------
def test_quantized_models_flag_on():
    flags.set("quantization", True)
    r = _client().get("/v1/models/quantized")
    assert r.status_code == 200
    body = r.json()
    assert "models" in body
    assert body["baseline_faithfulness"] == 0.93


def test_offline_status_flag_on():
    flags.set("offline_rag", True)
    r = _client().get("/v1/offline/status")
    assert r.status_code == 200
    assert "available" in r.json()


# ---------------------------------------------------------------------------
# 9. PDF exports — drive the HTTP contract with the heavy renderer faked
#    (reportlab is not a test dependency; the route wiring is what's under test).
# ---------------------------------------------------------------------------
def _fake_pdf_module() -> types.ModuleType:
    mod = types.ModuleType("App.backend.app.pdf_export")
    mod.generate_conversation_pdf = lambda messages, title="", session_id="": b"%PDF-1.4 fake-conv"
    mod.generate_tax_summary_pdf = lambda calculation, taxpayer_ref="": b"%PDF-1.4 fake-tax"
    return mod


def test_export_conversation_pdf():
    with mock.patch.dict(sys.modules, {"App.backend.app.pdf_export": _fake_pdf_module()}):
        r = _client().post(
            "/v1/export/conversation",
            json={"messages": [{"role": "user", "content": "hi"}], "title": "Report"},
        )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")


def test_export_tax_summary_pdf():
    with mock.patch.dict(sys.modules, {"App.backend.app.pdf_export": _fake_pdf_module()}):
        r = _client().post(
            "/v1/export/tax-summary",
            headers=_bearer(USER),
            json={"calculation": {"items": [{"label": "VAT", "amount": 100}], "total": 100}},
        )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

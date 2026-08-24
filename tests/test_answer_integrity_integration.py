"""Endpoint- and pipeline-level integration for the answer-integrity changes.

Sibling of ``test_fallback_integration.py`` and here for the same reason: the
suite under ``App/backend/tests`` does not run in CI, while
``pytest tests/ --cov=App/backend`` *measures* it. Code covered only there is
code CI protects on paper. These drive the real objects — a real ``ChatModel``,
the real FastAPI app, the real translation cache — over the paths a taxpayer
actually hits:

* the **effective locale** in the streaming core. Detection runs inside
  ``generate_retrieval_only``; ``run_chat_turn`` used the caller's parameter, so
  the path the web and WebSocket clients use answered auto-detected Luganda in
  English while the REST path handled it correctly.
* the **figure guard** on a translated reply. Machine translation paraphrases,
  and a paraphrased amount is a different amount.
* the **translation memo**, which is why a non-English turn is no longer two to
  three times slower than the same question in English.
* **withholding** an answer whose figures contradict its cited passage — caught
  and escalated for a long time, and still printed.
* **POST /v1/escalate**, the taxpayer's own way into the officer queue.
"""

from __future__ import annotations

import asyncio
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

os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("CACHE_BACKEND", "memory")
os.environ.setdefault("ANALYTICS_BACKEND", "sqlite")
os.environ.setdefault("OTEL_ENABLED", "false")
os.environ.setdefault("QDRANT_URL", "http://127.0.0.1:1")
os.environ.setdefault("QDRANT_ENABLED", "false")
os.environ.setdefault("SPEECH_ENABLED", "false")
# NB: the analytics DB stays on the default cwd `data_store/` path — a tmpdir
# override would land on tmpfs, where SQLite WAL fails with "database is locked".

from fastapi.testclient import TestClient  # noqa: E402

from App.backend.app import database  # noqa: E402
from App.backend.app import mt  # noqa: E402
from App.backend.app import service as service_module  # noqa: E402
from App.backend.app.flags import flags  # noqa: E402
from App.backend.app.main import app  # noqa: E402
from App.backend.app.text_signals import CONTRADICTED_CLAIM_REPLY  # noqa: E402


@pytest.fixture(scope="module")
def client():
    database.init_db()
    app.state.model = service_module.ChatModel()
    app.state.speech = types.SimpleNamespace(enabled=False)
    # No `with` block: skips the lifespan (model download / prod gate).
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_translation_cache():
    """Each case starts with an empty memo; a hit from a neighbour would make
    a translation backend look uncalled when it was simply not needed."""
    mt.cache.clear()
    yield
    mt.cache.clear()


def _conversation_id() -> str:
    """A fresh id per case.

    The analytics DB lives on the default `data_store/` path and survives
    between runs, and `_maybe_create_ticket` deliberately reuses an open ticket
    for a conversation — so a fixed id makes the *second* run of this file see
    yesterday's ticket and read "already looking at this conversation". That is
    the dedup working, not a failure, but it makes the assertion wrong.
    """
    return f"conv-int-{uuid.uuid4().hex[:12]}"


def _drain(agen):
    async def _run():
        return [event async for event in agen]

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# The translation memo
# ---------------------------------------------------------------------------
class TestTheTranslationMemo:
    def test_a_repeated_reply_is_translated_once(self):
        english = "Visit ura.go.ug and choose Get a TIN."
        luganda = "Genda ku ura.go.ug olonde Funa TIN."
        with mock.patch.object(
            service_module, "_translate_reply", return_value=luganda
        ) as backend:
            first = service_module.localize_reply(english, "lg")
            second = service_module.localize_reply(english, "lg")
        assert first == second == luganda
        backend.assert_called_once()

    def test_the_question_is_translated_once_per_turn_not_per_caller(self):
        """service.py translates the question for the deterministic routers and
        the hybrid retriever translates it again for the corpus. That second
        call was a whole extra round trip for a string translated milliseconds
        earlier."""
        from App.backend.app.query import translate_query_for_retrieval

        calls = []

        def _fake_llm_translate(text, source_lang="en", target_lang="lg"):
            calls.append((text, source_lang, target_lang))
            return "How do I register for a TIN?"

        with mock.patch("App.backend.app.llm.translate_text", _fake_llm_translate):
            first = translate_query_for_retrieval("TIN nnyingira ntya", "lg")
            second = translate_query_for_retrieval("TIN nnyingira ntya", "lg")

        assert first == second == "How do I register for a TIN?"
        assert len(calls) == 1

    def test_the_memo_holds_a_digest_and_not_the_question(self):
        """Taxpayer questions reach this cache and can carry a TIN or a name."""
        question_with_a_tin = "My TIN is 1000123456 — how much do I owe?"
        mt.cache.put("lg", "en", question_with_a_tin, "translated")
        assert question_with_a_tin not in str(list(mt.cache._entries))


# ---------------------------------------------------------------------------
# Figures across translation
# ---------------------------------------------------------------------------
class TestFiguresSurviveTranslation:
    def test_a_changed_amount_serves_the_english_answer(self):
        english = "The PAYE due on this salary is UGX 235,000 for the month."
        with mock.patch.object(
            service_module,
            "_translate_reply",
            return_value="Omusolo gwa PAYE ye UGX 253,000 buli mwezi.",
        ):
            assert service_module.localize_reply(english, "lg") == english

    def test_a_rate_marked_differently_is_still_the_same_rate(self):
        """Luganda writes a rate as parts per hundred, with no percent sign.
        Comparing money and percentages as separate categories threw away a
        translation that was exactly right."""
        english = "The standard VAT rate in Uganda is 18% on taxable supplies."
        luganda = "Omusolo gwa VAT mu Uganda guli ebitundu 18 ku buli kikumi."
        with mock.patch.object(service_module, "_translate_reply", return_value=luganda):
            assert service_module.localize_reply(english, "lg") == luganda

    def test_a_refused_translation_is_not_memoised(self):
        english = "The registration threshold is UGX 150,000,000 per year."
        with mock.patch.object(
            service_module,
            "_translate_reply",
            return_value="Ekipimo kya UGX 100,000,000 buli mwaka.",
        ) as backend:
            service_module.localize_reply(english, "lg")
            service_module.localize_reply(english, "lg")
        assert backend.call_count == 2


# ---------------------------------------------------------------------------
# The streaming core
# ---------------------------------------------------------------------------
class TestTheStreamingCoreAnswersInTheDetectedLanguage:
    def _turn(self, result, caller_locale="en"):
        model = mock.MagicMock()
        model.generate_retrieval_only.return_value = result
        with mock.patch.object(service_module, "localize_reply") as localize:
            localize.side_effect = lambda text, locale: f"[{locale}] {text}"
            events = _drain(
                service_module.run_chat_turn(
                    model,
                    message="TIN nnyingira ntya",
                    conversation_id=None,
                    top_k=4,
                    locale=caller_locale,
                    session_id=None,
                    request_id=None,
                    user_id=None,
                    tenant_id="default",
                )
            )
        return events, localize

    def test_an_auto_detected_locale_reaches_localization(self):
        events, localize = self._turn(
            {
                "reply": "Register on ura.go.ug.",
                "retrieval_mode": "abstained",
                "locale": "lg",
                "_hits": [],
            }
        )
        assert localize.call_args.args[1] == "lg"
        assert [p for k, p in events if k == "token"] == ["[lg] Register on ura.go.ug."]

    def test_the_client_is_told_which_language_was_resolved(self):
        events, _ = self._turn(
            {
                "reply": "Register on ura.go.ug.",
                "retrieval_mode": "abstained",
                "locale": "lg",
                "_hits": [],
            }
        )
        completed = next(p for k, p in events if k == "retrieval.completed")
        assert completed["locale"] == "lg"
        assert [k for k, _ in events if k.startswith("translation.")] == [
            "translation.started",
            "translation.completed",
        ]

    def test_an_english_turn_announces_no_translation(self):
        events, _ = self._turn(
            {
                "reply": "Register on ura.go.ug.",
                "retrieval_mode": "abstained",
                "locale": "en",
                "_hits": [],
            }
        )
        assert [k for k, _ in events if k.startswith("translation.")] == []


# ---------------------------------------------------------------------------
# Withholding
# ---------------------------------------------------------------------------
class TestAContradictedFigureIsNotPrinted:
    def _guard(self, claim_report):
        model = mock.MagicMock()
        model._evaluate_response_judge.return_value = {"decision": "approve", "reasons": []}
        model._build_handoff_packet.return_value = {"topic": "vat", "priority": "normal"}
        model._maybe_create_ticket.return_value = "ticket-int-1"
        output_guard = mock.MagicMock()
        output_guard.should_escalate.return_value = (False, "")
        with mock.patch.object(
            service_module, "verify_claims", return_value=claim_report
        ), mock.patch.object(
            service_module.HybridRetriever, "compute_faithfulness", return_value=0.9
        ):
            return service_module._apply_output_guards(
                model,
                message="What is the VAT rate?",
                reply="Value Added Tax is charged at 20% on taxable supplies. [1]",
                hits=[{"text": "VAT is charged at 18%.", "source": "vat.csv"}],
                citations=[
                    {"ref": "[1]", "source": "vat.csv", "passage": "VAT is charged at 18%."}
                ],
                conversation_history=[],
                session_id="sess-int",
                conversation_id="conv-int",
                output_guard=output_guard,
            )

    def test_the_wrong_figure_never_reaches_the_client(self):
        out = self._guard(
            {"decision": "escalate", "contradicted_claims": [{"text": "VAT is 20%"}]}
        )
        assert out["reply"] == CONTRADICTED_CLAIM_REPLY
        assert "20%" not in out["reply"]
        assert out["escalate"] is True
        # No score: 0.9 described text that is no longer being sent, and
        # reporting it would put a "well grounded" badge on a withholding notice.
        assert out["faithfulness"] is None
        # And an officer is waiting — withholding without a handoff leaves the
        # taxpayer with nothing at all.
        assert out["ticket_id"]

    def test_an_unsupported_but_uncontradicted_answer_is_still_served(self):
        out = self._guard(
            {
                "decision": "revise",
                "contradicted_claims": [],
                "unsupported_claims": [{"text": "You may register online."}],
            }
        )
        assert "20%" in out["reply"]


# ---------------------------------------------------------------------------
# The taxpayer's own escalation
# ---------------------------------------------------------------------------
class TestTaxpayerEscalationEndpoint:
    def test_it_queues_a_real_ticket_and_returns_its_reference(self, client):
        flags.set("ticket_queue", True)
        try:
            conversation_id = _conversation_id()
            response = client.post(
                "/v1/escalate",
                json={
                    "conversation_id": conversation_id,
                    "reason": "The VAT answer does not match my assessment notice.",
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["ok"] is True
            assert body["ticket_id"]
            assert body["status"] == "open"
            assert "here in this conversation" in body["message"]

            ticket = database.get_ticket(body["ticket_id"])
            assert ticket is not None
            assert ticket["conversation_id"] == conversation_id
            # Officers need to tell an answer the system doubted from a person
            # who asked for help; they need different first replies.
            assert ticket["handoff"]["requested_by"] == "taxpayer"
        finally:
            flags.clear("ticket_queue")

    def test_asking_twice_puts_one_officer_on_it(self, client):
        flags.set("ticket_queue", True)
        try:
            conversation_id = _conversation_id()
            first = client.post(
                "/v1/escalate", json={"conversation_id": conversation_id}
            ).json()
            second = client.post(
                "/v1/escalate", json={"conversation_id": conversation_id}
            ).json()
            assert first["ticket_id"] == second["ticket_id"]
            assert second["reused_existing"] is True
            assert "already looking at this conversation" in second["message"]
        finally:
            flags.clear("ticket_queue")

    def test_the_queue_being_off_is_said_plainly(self, client):
        flags.set("ticket_queue", False)
        try:
            body = client.post(
                "/v1/escalate", json={"conversation_id": _conversation_id()}
            ).json()
            assert body["ok"] is False
            assert body["status"] == "queue_disabled"
            assert "0800 117 000" in body["message"]
        finally:
            flags.clear("ticket_queue")

    def test_self_declared_urgency_is_ignored(self, client):
        """A queue where every taxpayer can mark their own ticket urgent stops
        sorting anything."""
        flags.set("ticket_queue", True)
        try:
            body = client.post(
                "/v1/escalate",
                json={"conversation_id": _conversation_id(), "priority": "urgent"},
            ).json()
            ticket = database.get_ticket(body["ticket_id"])
            assert ticket["priority"] == "normal"
        finally:
            flags.clear("ticket_queue")

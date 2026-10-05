"""Every answered turn reaches the audit ledger, with the text actually served.

Before: only ``ChatModel.generate`` (REST ``/v1/chat``) wrote a ``generate``
row, so the SSE and WebSocket streams — what the web client uses — left no
tamper-evident record; failures were swallowed at DEBUG; and the reply digest
was taken on the English draft before localization.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import unittest
import unittest.mock as mock

os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("QDRANT_ENABLED", "false")
os.environ.setdefault("ANALYTICS_BACKEND", "sqlite")
os.environ.setdefault("OTEL_ENABLED", "false")

from app import database as db  # noqa: E402
from app import service  # noqa: E402
from app.analytics import metrics  # noqa: E402
from app.audit import turns  # noqa: E402
from app.flags import flags  # noqa: E402


def setUpModule() -> None:
    db.init_db()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _last_generate_row() -> dict:
    row = db.query_one(
        "SELECT payload, user_id FROM audit_events WHERE event_type = 'generate' ORDER BY ts DESC, seq DESC LIMIT 1"
    )
    assert row is not None
    return {"payload": json.loads(row["payload"]), "user_id": row["user_id"]}


class PayloadTest(unittest.TestCase):
    def test_payload_carries_decision_context_not_content(self) -> None:
        result = {
            "reply": "Omusolo gwa VAT guli 18%.",
            "reply_locale": "lg",
            "locale": "lg",
            "retrieval_mode": "hybrid",
            "sources": ["vat_act.pdf", "vat_faq.csv"],
            "citations": [{"source": "vat_act.pdf", "page": "4", "section": "s.24", "passage": "18 percent"}],
            "faithfulness_score": 0.91,
            "response_judge": {"verdict": "approve"},
            "model": "Sunflower-14B",
        }
        payload = turns.build_turn_payload(
            message="VAT ki?",
            result=result,
            channel="sse",
            usage={"input_tokens": 1800, "output_tokens": 95, "source": "provider"},
        )
        self.assertEqual(payload["schema"], turns.TURN_PAYLOAD_SCHEMA)
        self.assertEqual(payload["channel"], "sse")
        self.assertEqual(payload["reply_sha256"], _sha("Omusolo gwa VAT guli 18%."))
        self.assertEqual(payload["query_sha256"], _sha("VAT ki?"))
        self.assertEqual(payload["reply_locale"], "lg")
        self.assertEqual(payload["sources"], ["vat_act.pdf", "vat_faq.csv"])
        self.assertEqual(len(payload["citation_sha256"]), 1)
        self.assertEqual(payload["usage"], {"input_tokens": 1800, "output_tokens": 95, "source": "provider"})
        self.assertEqual(payload["response_judge"], "approve")
        self.assertEqual(set(payload["provenance"]), {"app_version", "build_sha", "prompt_template_sha256", "index_corpus_hash"})
        flattened = json.dumps(payload)
        self.assertNotIn("Omusolo", flattened)
        self.assertNotIn("18 percent", flattened)

    def test_usage_without_provider_counts_is_labelled_estimate(self) -> None:
        payload = turns.build_turn_payload(message="hello", result={"reply": "hi there"}, channel="rest")
        self.assertEqual(payload["usage"]["source"], "estimate")


class AppendTest(unittest.TestCase):
    def setUp(self) -> None:
        flags.set("audit_ledger", True)

    def tearDown(self) -> None:
        flags.clear("audit_ledger")

    def test_off_means_no_row(self) -> None:
        flags.set("audit_ledger", False)
        self.assertFalse(turns.append_turn(message="m", result={"reply": "r"}, channel="rest"))

    def test_row_written_with_actor(self) -> None:
        self.assertTrue(
            turns.append_turn(message="q", result={"reply": "served"}, channel="ws", user_id="user-1", tenant_id="t-a")
        )
        row = _last_generate_row()
        self.assertEqual(row["user_id"], "user-1")
        self.assertEqual(row["payload"]["channel"], "ws")
        self.assertEqual(row["payload"]["reply_sha256"], _sha("served"))

    def test_failure_is_counted_and_logged(self) -> None:
        key = 'audit_append_failed_total{event_type="generate"}'
        before = metrics.snapshot()["counters"].get(key, 0)
        with mock.patch("app.audit.get_ledger", side_effect=RuntimeError("db down")), \
             self.assertLogs("app.audit.turns", level="WARNING"):
            self.assertFalse(turns.append_turn(message="q", result={"reply": "r"}, channel="rest"))
        self.assertEqual(metrics.snapshot()["counters"].get(key, 0) - before, 1)


class StreamedTurnTest(unittest.TestCase):
    """``run_chat_turn`` (SSE and WebSocket) audits and counts each turn once."""

    def _run(self, channel: str, events: list[tuple[str, object]], model: object) -> list[tuple[str, object]]:
        async def fake_events(_model, **_kwargs):
            for event in events:
                yield event

        async def collect() -> list[tuple[str, object]]:
            out = []
            async for event in service.run_chat_turn(
                model,
                channel=channel,
                message="What is the VAT rate?",
                conversation_id="conv-stream",
                top_k=4,
                locale="en",
                session_id="sess-1",
                request_id="req-1",
                user_id="user-9",
                tenant_id="default",
            ):
                out.append(event)
            return out

        with mock.patch.object(service, "_run_chat_turn_events", fake_events):
            return asyncio.run(collect())

    def test_sse_turn_is_audited_with_served_text(self) -> None:
        model = mock.Mock()
        log = {"result": {"retrieval_mode": "hybrid", "faithfulness_score": 0.9}, "full_reply": "The rate is 18%.", "elapsed_ms": 1200.0}
        before = metrics.snapshot()["counters"].get('chat_turns_total{channel="sse",outcome="answered"}', 0)
        events = self._run("sse", [("token", "The rate is 18%."), ("done", ""), ("_log", log)], model)
        self.assertEqual(events[-1], ("_log", log))  # adapters still receive the frame
        model.record_turn_audit.assert_called_once()
        kwargs = model.record_turn_audit.call_args.kwargs
        self.assertEqual(kwargs["channel"], "sse")
        self.assertEqual(kwargs["result"]["reply"], "The rate is 18%.")
        self.assertEqual(kwargs["user_id"], "user-9")
        after = metrics.snapshot()["counters"].get('chat_turns_total{channel="sse",outcome="answered"}', 0)
        self.assertEqual(after - before, 1)

    def test_failed_turn_is_counted_not_audited(self) -> None:
        model = mock.Mock()
        log = {"result": {}, "full_reply": "", "elapsed_ms": 10.0}
        before = metrics.snapshot()["counters"].get('chat_turns_total{channel="ws",outcome="error"}', 0)
        self._run("ws", [("error", {"code": "internal"}), ("done", ""), ("_log", log)], model)
        model.record_turn_audit.assert_not_called()
        after = metrics.snapshot()["counters"].get('chat_turns_total{channel="ws",outcome="error"}', 0)
        self.assertEqual(after - before, 1)

    def test_models_without_the_hook_are_tolerated(self) -> None:
        log = {"result": {"retrieval_mode": "faq"}, "full_reply": "ok", "elapsed_ms": 5.0}
        self._run("sse", [("_log", log)], object())


class GenerateDefersAuditTest(unittest.TestCase):
    """The REST row is written after localization, so it hashes the served reply."""

    def test_localized_reply_is_what_gets_hashed(self) -> None:
        model = service.ChatModel.__new__(service.ChatModel)
        model.name = "test-model"

        def fake_generate_en(**kwargs):
            result = {"reply": "The rate is 18%.", "locale": "lg", "retrieval_mode": "hybrid"}
            model._audit_turn(message=kwargs["message"], result=result, session_id="s", trace_ctx={"user_id": "u-7"})
            return result

        recorded: list[dict] = []
        model._generate_en = fake_generate_en
        model._localize_reply = lambda text, locale: "Omusolo guli 18%."
        model.record_turn_audit = lambda **kwargs: recorded.append(kwargs)
        with mock.patch.object(service, "resources_for_turn", return_value=[]), \
             mock.patch.object(service, "apply_turn_guidance"), \
             mock.patch.object(service, "_recent_turns_for_guidance", return_value=[]):
            result = model.generate(message="VAT ki?", locale="lg", channel="voice")
        self.assertEqual(result["reply"], "Omusolo guli 18%.")
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["result"]["reply"], "Omusolo guli 18%.")
        self.assertEqual(recorded[0]["channel"], "voice")
        self.assertEqual(recorded[0]["user_id"], "u-7")


if __name__ == "__main__":
    unittest.main()

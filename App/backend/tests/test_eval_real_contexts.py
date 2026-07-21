"""P0-2 tests: the eval harness scores faithfulness against REAL persisted
retrieved contexts, not the answer itself.

Previously ``collect_samples`` set ``contexts=[answer]``, so faithfulness was
structurally ~1.0 and the SLO gate could never fail. These tests prove the
gate can now fail on an unsupported answer and that contexts round-trip
through the DB.
"""

from __future__ import annotations

import json
import unittest
import unittest.mock as mock
import uuid


class ContextsJsonTest(unittest.TestCase):
    def test_extracts_topk_hit_texts(self) -> None:
        from app.service import ChatModel

        result = {
            "_hits": [
                {"text": "VAT standard rate is 18%."},
                {"answer": "PAYE is computed in bands."},
                {"text": "   "},  # blank → skipped
            ]
        }
        ctx = json.loads(ChatModel.contexts_json(result, limit=8))
        self.assertEqual(ctx, ["VAT standard rate is 18%.", "PAYE is computed in bands."])

    def test_empty_when_no_hits(self) -> None:
        from app.service import ChatModel

        self.assertEqual(ChatModel.contexts_json({}), "[]")
        self.assertEqual(ChatModel.contexts_json(None), "[]")

    def test_respects_limit(self) -> None:
        from app.service import ChatModel

        result = {"_hits": [{"text": f"passage {i}"} for i in range(10)]}
        self.assertEqual(len(json.loads(ChatModel.contexts_json(result, limit=3))), 3)


class CollectSamplesTest(unittest.TestCase):
    def test_uses_real_contexts_and_skips_empty(self) -> None:
        from app import evaluation

        fake_rows = [
            {
                "user_query": "what is the vat rate?",
                "bot_reply": "It is 18%.",
                "contexts": json.dumps(["The VAT standard rate is 18%."]),
            },
            {"user_query": "no ctx", "bot_reply": "ans", "contexts": "[]"},  # skipped
            {"user_query": "blank ctx", "bot_reply": "ans", "contexts": ""},  # skipped
        ]
        with mock.patch.object(evaluation.db, "export_eval_samples", return_value=fake_rows):
            samples = evaluation.collect_samples(sample_size=10, days=30)

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["contexts"], ["The VAT standard rate is 18%."])
        # The fix: contexts are NOT the answer itself.
        self.assertNotEqual(samples[0]["contexts"], [samples[0]["answer"]])


class EvalGateCanFailTest(unittest.TestCase):
    def test_faithfulness_low_for_unsupported_answer(self) -> None:
        from app.evaluation import EVAL_FAITHFULNESS_MIN, _heuristic_faithfulness

        ctx = ["The VAT standard rate in Uganda is 18 percent."]
        supported = _heuristic_faithfulness("The VAT rate is 18 percent.", ctx)
        unsupported = _heuristic_faithfulness(
            "The capital gains tax deadline is next Friday.", ctx
        )
        self.assertGreater(supported, unsupported)
        # The whole point of P0-2: the gate CAN now fail.
        self.assertLess(unsupported, EVAL_FAITHFULNESS_MIN)

    def test_old_vacuous_behaviour_always_passed(self) -> None:
        # Demonstrates the bug we removed: answer-as-its-own-context ~1.0.
        from app.evaluation import EVAL_FAITHFULNESS_MIN, _heuristic_faithfulness

        fabricated = "A totally fabricated tax claim with no retrieval support whatsoever."
        self.assertGreaterEqual(_heuristic_faithfulness(fabricated, [fabricated]), EVAL_FAITHFULNESS_MIN)


class DbRoundtripTest(unittest.TestCase):
    def test_contexts_persist_and_export(self) -> None:
        from app import database as db

        db.init_db()  # ensures the contexts column migration has run
        marker = "evalctx-" + uuid.uuid4().hex[:8]
        ctx = [f"Persisted retrieved passage about {marker}."]
        db.log_conversation(
            session_id=marker,
            conversation_id=None,
            user_message=f"question {marker}",
            bot_reply=f"answer {marker}",
            contexts=json.dumps(ctx),
            confidence=0.9,
        )
        rows = db.export_eval_samples(days=1, limit=500)
        mine = [r for r in rows if marker in str(r.get("user_query", ""))]
        self.assertTrue(mine, "logged row with contexts should be exported for eval")
        self.assertEqual(json.loads(mine[0]["contexts"]), ctx)


if __name__ == "__main__":
    unittest.main()

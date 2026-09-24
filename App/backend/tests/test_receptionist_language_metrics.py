"""Per-call language metrics, and the call row ending on the call's final language."""

from __future__ import annotations

import unittest
import uuid

from app import database as db
from app.receptionist.metrics import compute_call_metrics, record_call_end_metrics
from app.receptionist.state import CallState
from app.receptionist.store import create_call, get_call, init_receptionist_schema


class LanguageMetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        init_receptionist_schema()

    def state(self) -> CallState:
        s = CallState(call_id="c", conversation_id="conv", locale="lg", initial_locale="en")
        s.languages_used = ["en", "lg"]
        s.preferred_locale = "en"
        s.language_source = "auto"
        s.language_switches = 1
        s.lid_latencies_ms = [100.0, 300.0]
        s.lid_confidences = [0.9, 0.7]
        s.held_ms = [120.0]
        return s

    def test_the_language_block(self):
        m = compute_call_metrics(self.state(), {"started_at": 0, "ended_at": 10, "locale": "en"}, [])
        lang = m["language"]
        self.assertEqual((lang["initial"], lang["final"], lang["used"]), ("en", "lg", ["en", "lg"]))
        self.assertEqual((lang["switches"], lang["overrides"], lang["source"]), (1, 0, "auto"))
        self.assertEqual(lang["detection_latency_ms_p50"], 200.0)
        self.assertEqual(lang["detection_confidence_mean"], 0.8)
        self.assertEqual(lang["held_ms_p95"], 120.0)

    def test_without_state_the_row_locale_is_the_answer(self):
        m = compute_call_metrics(None, {"started_at": 0, "ended_at": 10, "locale": "sw"}, [])
        self.assertEqual(m["language"], {"initial": "sw", "final": "sw", "used": ["sw"], "switches": 0, "overrides": 0})

    def test_the_call_row_ends_on_the_final_language(self):
        call_id = f"call_{uuid.uuid4().hex[:8]}"
        create_call(call_id, locale="en")
        state = self.state()
        state.call_id = call_id
        metrics = record_call_end_metrics(call_id, state)
        row = get_call(call_id)
        self.assertEqual(row["locale"], "lg")
        self.assertEqual(row["metrics"]["language"]["final"], metrics["language"]["final"])


if __name__ == "__main__":
    unittest.main()

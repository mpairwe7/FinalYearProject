"""Guided-journey funnel: the durable query and the per-journey assembly."""

from __future__ import annotations

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app import database as db
from app.journey_analytics import build_journey_funnel
from app.workflows.loader import load_workflow

FLOWS_DIR = Path(__file__).resolve().parents[1] / "app" / "workflows" / "flows"


def _flows():
    return [load_workflow(path) for path in sorted(FLOWS_DIR.glob("*.yaml"))]


class BuildJourneyFunnelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = {
            "sessions": [
                {"workflow_id": "return_filing", "status": "completed", "step_idx": 8, "stale": 0, "n": 3},
                {"workflow_id": "return_filing", "status": "cancelled", "step_idx": 2, "stale": 0, "n": 1},
                {"workflow_id": "return_filing", "status": "active", "step_idx": 0, "stale": 1, "n": 2},
                {"workflow_id": "return_filing", "status": "active", "step_idx": 1, "stale": 0, "n": 1},
                {"workflow_id": "calc_paye", "status": "completed", "step_idx": 3, "stale": 0, "n": 4},
            ],
            "feedback": [
                {"workflow_id": "return_filing", "step_id": "collect_taxpayer_type", "rating": "down", "n": 2},
                {"workflow_id": "return_filing", "step_id": "collect_taxpayer_type", "rating": "up", "n": 5},
                {"workflow_id": "return_filing", "step_id": "no_such_step", "rating": "down", "n": 9},
            ],
        }
        self.view = build_journey_funnel(self.raw, _flows(), days=30, abandon_after_hours=24)
        self.by_id = {j["workflow_id"]: j for j in self.view["journeys"]}

    def test_statuses_are_counted_separately(self) -> None:
        rf = self.by_id["return_filing"]
        self.assertEqual(rf["started"], 7)
        self.assertEqual(rf["completed"], 3)
        self.assertEqual(rf["cancelled"], 1)
        self.assertEqual(rf["abandoned"], 2)
        self.assertEqual(rf["in_progress"], 1)
        self.assertEqual(rf["completion_pct"], 42.9)

    def test_unfinished_journeys_are_placed_at_their_step(self) -> None:
        steps = self.by_id["return_filing"]["steps"]
        self.assertEqual(steps[0]["stopped"], 2)  # abandoned at step 0
        self.assertEqual(steps[2]["stopped"], 1)  # cancelled at step 2
        self.assertEqual(steps[1]["stopped"], 0)  # still in progress: not a stop

    def test_ratings_land_on_their_step_and_unknown_steps_are_ignored(self) -> None:
        first = self.by_id["return_filing"]["steps"][0]
        self.assertEqual((first["helpful"], first["not_helpful"]), (5, 2))
        total_down = sum(s["not_helpful"] for s in self.by_id["return_filing"]["steps"])
        self.assertEqual(total_down, 2)

    def test_every_nameable_journey_is_listed_even_unused(self) -> None:
        self.assertIn("motor_vehicle_registration", self.by_id)
        self.assertEqual(self.by_id["motor_vehicle_registration"]["started"], 0)
        # Calculators appear only once used.
        self.assertIn("calc_paye", self.by_id)
        self.assertNotIn("calc_vat", self.by_id)

    def test_busiest_journey_first(self) -> None:
        self.assertEqual(self.view["journeys"][0]["workflow_id"], "return_filing")


class JourneyFunnelQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = TemporaryDirectory(dir="/tmp")
        conn = getattr(db._local, "conn", None)
        if conn is not None:
            conn.close()
            delattr(db._local, "conn")
        db._DB_DIR = Path(cls.tmp.name)
        db._DB_PATH = db._DB_DIR / "analytics.db"
        db.init_db()

    @classmethod
    def tearDownClass(cls) -> None:
        conn = getattr(db._local, "conn", None)
        if conn is not None:
            conn.close()
            delattr(db._local, "conn")
        cls.tmp.cleanup()

    def test_sessions_and_step_feedback_are_grouped(self) -> None:
        db.upsert_workflow_session("c-done", "tax_clearance", 12, {}, status="completed")
        db.upsert_workflow_session("c-stale", "tax_clearance", 2, {}, status="active")
        db.upsert_workflow_session("c-fresh", "tax_clearance", 2, {}, status="active")
        conn = db._get_connection()
        conn.execute(
            "UPDATE workflow_sessions SET updated_at = ? WHERE conversation_id = 'c-stale'",
            (time.time() - 7200,),
        )
        conn.commit()
        db.save_feedback(
            message_id="m1", rating="down", workflow_id="tax_clearance",
            step_id="collect_returns_filed", retrieval_mode="workflow",
        )
        db.save_feedback(message_id="m2", rating="up")  # no journey: not in the funnel

        raw = db.get_journey_funnel(days=30, abandon_after_s=3600)
        sessions = {(r["status"], r["step_idx"], r["stale"]): r["n"] for r in raw["sessions"]}
        self.assertEqual(sessions[("completed", 12, 0)], 1)
        self.assertEqual(sessions[("active", 2, 1)], 1)
        self.assertEqual(sessions[("active", 2, 0)], 1)
        self.assertEqual(
            raw["feedback"],
            [{"workflow_id": "tax_clearance", "step_id": "collect_returns_filed", "rating": "down", "n": 1}],
        )


if __name__ == "__main__":
    unittest.main()

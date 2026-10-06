"""The Prometheus metrics facade (app.analytics).

Pins the failures the in-process store had (docs/GAPS_AND_AGENTIC_ROADMAP.md
G99): counters that differed per uvicorn worker, "histogram" buckets that
went down when latency got worse, raw URL paths as labels, and duplicate
bare + ``ura_`` series.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

os.environ.setdefault("OTEL_ENABLED", "false")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import analytics  # noqa: E402
from app.analytics import (  # noqa: E402
    AnalyticsMiddleware,
    MetricsStore,
    bucket_quantile,
    exported_name,
    record_chat_turn,
    record_mapped,
    turn_outcome,
)

BACKEND = Path(__file__).resolve().parents[1]


def _sample(text: str, prefix: str) -> float:
    for line in text.splitlines():
        if line.startswith(prefix):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"{prefix} not in exposition")


class ExpositionTest(unittest.TestCase):
    def test_names_are_namespaced_once(self) -> None:
        store = MetricsStore()
        store.inc("http_requests_total", labels={"method": "GET", "path": "/health", "status": "200"})
        text = store.to_prometheus()
        self.assertIn('ura_http_requests_total{method="GET",path="/health",status="200"} 1.0', text)
        # No second, un-namespaced copy of every series.
        self.assertNotIn("\nhttp_requests_total{", text)
        self.assertEqual(text.count("# TYPE ura_http_requests_total counter"), 1)

    def test_exported_name(self) -> None:
        self.assertEqual(exported_name("chat_turns_total"), "ura_chat_turns_total")
        self.assertEqual(exported_name("ura_eval_metric"), "ura_eval_metric")
        with self.assertRaises(ValueError):
            exported_name("bad name")

    def test_histogram_buckets_are_cumulative_counters(self) -> None:
        """Worse latency must not make a bucket count go down (it did, by 3000)."""
        store = MetricsStore()
        for _ in range(5000):
            store.observe("chat_response_time_ms", 100.0)
        before = _sample(store.to_prometheus(), 'ura_chat_response_time_ms_bucket{le="1000.0"}')
        for _ in range(3000):
            store.observe("chat_response_time_ms", 4000.0)
        text = store.to_prometheus()
        self.assertEqual(before, 5000.0)
        self.assertEqual(_sample(text, 'ura_chat_response_time_ms_bucket{le="1000.0"}'), 5000.0)
        self.assertEqual(_sample(text, "ura_chat_response_time_ms_count"), 8000.0)

    def test_unit_suffix_picks_buckets(self) -> None:
        self.assertEqual(analytics.default_buckets("x_ms")[0], 5)
        self.assertEqual(analytics.default_buckets("x_seconds")[0], 0.005)
        self.assertEqual(analytics.default_buckets("faithfulness_score")[-1], 1.0)


class LabelSafetyTest(unittest.TestCase):
    def test_series_per_metric_are_capped(self) -> None:
        store = MetricsStore()
        cap = analytics._MAX_SERIES_PER_METRIC
        for i in range(cap + 200):
            store.inc("probe_total", labels={"path": f"/scan/{i}"})
        family = store._families["ura_probe_total"]
        self.assertEqual(len(family.seen), cap + 1)  # cap distinct + one overflow series
        self.assertIn('path="__other__"', store.to_prometheus())

    def test_unknown_labels_are_dropped_not_raised(self) -> None:
        store = MetricsStore()
        store.inc("labelled_total", labels={"a": "1"})
        store.inc("labelled_total", labels={"a": "2", "unexpected": "x"})
        store.inc("labelled_total")  # missing label -> ""
        snap = store.snapshot()["counters"]
        self.assertEqual(snap['labelled_total{a="2"}'], 1)
        self.assertEqual(snap['labelled_total{a=""}'], 1)

    def test_type_conflict_is_ignored(self) -> None:
        store = MetricsStore()
        store.inc("dual_total")
        store.observe("dual_total", 1.0)  # logged once, not raised
        self.assertEqual(store.snapshot()["counters"]["dual_total"], 1)

    def test_route_template_not_raw_path(self) -> None:
        app = FastAPI()
        app.add_middleware(AnalyticsMiddleware)

        @app.get("/v1/tickets/{ticket_id}")
        def ticket(ticket_id: str) -> dict:
            return {"id": ticket_id}

        client = TestClient(app)
        for i in range(3):
            client.get(f"/v1/tickets/{i}-abc")
        client.get("/wp-admin.php")
        counters = analytics.metrics.snapshot()["counters"]
        self.assertGreaterEqual(
            counters.get('http_requests_total{method="GET",path="/v1/tickets/{ticket_id}",status="200"}', 0), 3
        )
        self.assertGreaterEqual(
            counters.get('http_requests_total{method="GET",path="__unmatched__",status="404"}', 0), 1
        )
        self.assertFalse(any("/v1/tickets/0-abc" in key for key in counters))


class SnapshotTest(unittest.TestCase):
    def test_snapshot_shape_for_dashboard(self) -> None:
        store = MetricsStore()
        for value in (100.0, 200.0, 300.0, 4000.0):
            store.observe("http_request_duration_ms", value, labels={"method": "POST", "path": "/v1/chat"})
        hist = store.snapshot()["histograms"]['http_request_duration_ms{method="POST",path="/v1/chat"}']
        self.assertEqual(hist["count"], 4)
        self.assertEqual(hist["avg"], 1150.0)
        self.assertLessEqual(hist["p50"], hist["p95"])
        self.assertLessEqual(hist["p95"], hist["p99"])

    def test_bucket_quantile_matches_histogram_quantile(self) -> None:
        buckets = [(100.0, 50.0), (200.0, 100.0), (float("inf"), 100.0)]
        self.assertEqual(bucket_quantile(0.5, buckets, 100.0), 100.0)
        self.assertEqual(bucket_quantile(0.75, buckets, 100.0), 150.0)
        self.assertEqual(bucket_quantile(0.99, [(1.0, 1.0), (float("inf"), 10.0)], 10.0), 1.0)
        self.assertEqual(bucket_quantile(0.5, [], 0.0), 0.0)


class ChatTurnRecorderTest(unittest.TestCase):
    def test_outcomes(self) -> None:
        self.assertEqual(turn_outcome({"retrieval_mode": "hybrid"}), "answered")
        self.assertEqual(turn_outcome({"retrieval_mode": "abstained"}), "abstained")
        self.assertEqual(turn_outcome({"retrieval_mode": "out_of_jurisdiction"}), "out_of_scope")
        self.assertEqual(turn_outcome({"retrieval_mode": "hybrid", "escalation_required": True}), "escalated")
        self.assertEqual(turn_outcome({"retrieval_mode": "blocked", "escalation_required": True}), "blocked")

    def test_every_channel_lands_in_the_same_families(self) -> None:
        before = analytics.metrics.snapshot()["counters"]
        for channel in ("rest", "sse", "ws", "voice"):
            record_chat_turn({"retrieval_mode": "hybrid", "faithfulness_score": 0.8}, elapsed_ms=900, channel=channel)
        record_chat_turn({}, elapsed_ms=10, channel="sse", error=True)
        after = analytics.metrics.snapshot()["counters"]
        for channel in ("rest", "sse", "ws", "voice"):
            key = f'chat_turns_total{{channel="{channel}",outcome="answered"}}'
            self.assertEqual(after.get(key, 0) - before.get(key, 0), 1, key)
        key = 'chat_turns_total{channel="sse",outcome="error"}'
        self.assertEqual(after.get(key, 0) - before.get(key, 0), 1)

    def test_record_mapped(self) -> None:
        table = {"_c": ("counter", "mapped_total", None), "_g": ("gauge", "mapped_active", None)}
        record_mapped(table, "_c")
        record_mapped(table, "_g", 1.0)
        record_mapped(table, "_g", -1.0)
        record_mapped(table, "_missing")
        snap = analytics.metrics.snapshot()
        self.assertGreaterEqual(snap["counters"]["mapped_total"], 1)
        self.assertEqual(snap["gauges"]["mapped_active"], 0)


class MultiProcessAggregationTest(unittest.TestCase):
    """Two worker processes, one scrape: the counts add up (they used not to)."""

    def test_workers_are_aggregated(self) -> None:
        with tempfile.TemporaryDirectory() as multiproc, tempfile.TemporaryDirectory() as data:
            env = {
                **os.environ,
                "PROMETHEUS_MULTIPROC_DIR": multiproc,
                "ANALYTICS_DB_DIR": data,
                "PYTHONPATH": os.pathsep.join([str(BACKEND), os.environ.get("PYTHONPATH", "")]),
            }
            worker = textwrap.dedent(
                """
                import sys
                from app.analytics import metrics
                for _ in range(int(sys.argv[1])):
                    metrics.inc("http_requests_total", labels={"method": "GET", "path": "/x", "status": "200"})
                    metrics.observe("chat_response_time_ms", 250.0, labels={"channel": "sse", "mode": "hybrid"})
                """
            )
            for count in ("3", "5"):
                subprocess.run([sys.executable, "-c", worker, count], env=env, check=True, timeout=120)
            reader = "from app.analytics import metrics; print(metrics.to_prometheus())"
            out = subprocess.run(
                [sys.executable, "-c", reader], env=env, check=True, timeout=120, capture_output=True, text=True
            ).stdout
        self.assertEqual(_sample(out, 'ura_http_requests_total{method="GET",path="/x",status="200"}'), 8.0)
        self.assertEqual(_sample(out, 'ura_chat_response_time_ms_count{channel="sse",mode="hybrid"}'), 8.0)


if __name__ == "__main__":
    unittest.main()

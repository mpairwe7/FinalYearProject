"""Guard strikes are counted once across workers when Redis is configured (G679)."""

from __future__ import annotations

import time
import unittest
from typing import Any

from app.guardrails.friction import StrikeTracker


class _FakeRedis:
    """The six calls the tracker makes, on dicts."""

    def __init__(self) -> None:
        self.sets: dict[str, dict[str, float]] = {}
        self.expiry: dict[str, float] = {}

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    def zremrangebyscore(self, name: str, _low: str, high: float) -> None:
        members = self.sets.get(name, {})
        for member in [m for m, score in members.items() if score <= high]:
            members.pop(member)

    def zadd(self, name: str, mapping: dict[str, float]) -> None:
        self.sets.setdefault(name, {}).update(mapping)

    def zcard(self, name: str) -> int:
        return len(self.sets.get(name, {}))

    def expire(self, _name: str, _seconds: int) -> bool:
        return True

    def set(self, name: str, _value: str, px: int) -> None:
        self.expiry[name] = time.time() + px / 1000

    def pttl(self, name: str) -> int:
        left = self.expiry.get(name, 0.0) - time.time()
        return int(left * 1000) if left > 0 else -2

    def delete(self, name: str) -> None:
        self.sets.pop(name, None)
        self.expiry.pop(name, None)

    def scan_iter(self, pattern: str) -> list[str]:
        prefix = pattern.rstrip("*")
        return [name for name in (*self.sets, *self.expiry) if name.startswith(prefix)]


class _FakePipeline:
    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        return lambda *args, **kwargs: self._calls.append((name, args, kwargs))

    def execute(self) -> list[Any]:
        return [getattr(self._redis, name)(*args, **kwargs) for name, args, kwargs in self._calls]


class _Down:
    def __getattr__(self, _name: str) -> Any:
        raise ConnectionError("redis down")


class SharedStrikeTests(unittest.TestCase):
    def _worker(self, shared: Any) -> StrikeTracker:
        return StrikeTracker(threshold=4, window_s=60, cooldown_s=30, shared=lambda: shared)

    def test_two_workers_count_one_principal_together(self) -> None:
        store = _FakeRedis()
        first, second = self._worker(store), self._worker(store)

        started = [w.record_strike("probe") for w in (first, second, first, second)]

        self.assertEqual(started, [False, False, False, True])
        self.assertGreater(first.cooldown_remaining("probe"), 0)
        self.assertGreater(second.cooldown_remaining("probe"), 0)

    def test_one_principal_does_not_pause_another(self) -> None:
        store = _FakeRedis()
        worker = self._worker(store)
        for _ in range(4):
            worker.record_strike("probe")

        self.assertEqual(worker.cooldown_remaining("someone-else"), 0.0)

    def test_an_unreachable_store_falls_back_to_this_process(self) -> None:
        worker = StrikeTracker(threshold=2, window_s=60, cooldown_s=30, shared=lambda: _Down())

        self.assertEqual([worker.record_strike("probe"), worker.record_strike("probe")], [False, True])
        self.assertGreater(worker.cooldown_remaining("probe"), 0)

    def test_reset_clears_the_shared_store(self) -> None:
        store = _FakeRedis()
        worker = self._worker(store)
        for _ in range(4):
            worker.record_strike("probe")

        worker.reset()

        self.assertEqual(worker.cooldown_remaining("probe"), 0.0)


if __name__ == "__main__":
    unittest.main()

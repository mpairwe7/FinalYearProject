"""One place connects to Redis, and connects again after a failure (G685)."""

from __future__ import annotations

import unittest
from unittest import mock

from app import redis_shared


class SharedRedisTests(unittest.TestCase):
    def setUp(self) -> None:
        redis_shared.reset()
        self.addCleanup(redis_shared.reset)
        env = mock.patch.dict("os.environ", {"REDIS_URL": "redis://redis:6379/0"})
        env.start()
        self.addCleanup(env.stop)
        self.clock = {"now": 1000.0}
        clock = mock.patch.object(redis_shared.time, "monotonic", side_effect=lambda: self.clock["now"])
        clock.start()
        self.addCleanup(clock.stop)

    def _from_url(self, outcomes: list) -> mock.Mock:
        """`redis.from_url` raising or returning a client per call, in order."""

        def connect(*_args, **_kwargs):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        patcher = mock.patch("redis.from_url", side_effect=connect)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_without_a_url_nothing_is_attempted(self) -> None:
        from_url = self._from_url([])
        with mock.patch.dict("os.environ", {"REDIS_URL": ""}):
            self.assertIsNone(redis_shared.shared_redis())
        from_url.assert_not_called()

    def test_a_failed_connection_is_tried_again_after_the_interval(self) -> None:
        client = mock.Mock()
        from_url = self._from_url([ConnectionError("redis down"), client])

        self.assertIsNone(redis_shared.shared_redis())
        self.assertIsNone(redis_shared.shared_redis())  # inside the interval: no new attempt
        self.assertEqual(from_url.call_count, 1)

        self.clock["now"] += redis_shared.RETRY_SECONDS + 1
        self.assertIs(redis_shared.shared_redis(), client)
        self.assertIs(redis_shared.shared_redis(), client)  # kept, not rebuilt
        self.assertEqual(from_url.call_count, 2)

    def test_a_client_that_cannot_answer_a_ping_is_not_handed_out(self) -> None:
        dead = mock.Mock(ping=mock.Mock(side_effect=TimeoutError("no answer")))
        self._from_url([dead])

        self.assertIsNone(redis_shared.shared_redis())

    def test_callers_with_the_same_settings_share_one_client(self) -> None:
        raw, text = mock.Mock(), mock.Mock()
        from_url = self._from_url([raw, text])

        self.assertIs(redis_shared.shared_redis(timeout=1.0), raw)
        self.assertIs(redis_shared.shared_redis(timeout=1.0), raw)
        self.assertIs(redis_shared.shared_redis(decode_responses=True, timeout=0.5), text)
        self.assertEqual(from_url.call_count, 2)

    def test_reset_can_keep_redis_off(self) -> None:
        from_url = self._from_url([])
        redis_shared.reset(enabled=False)

        self.assertIsNone(redis_shared.shared_redis())
        from_url.assert_not_called()

    def test_every_module_uses_this_helper(self) -> None:
        """A new `redis.from_url` outside this module would bring the one-shot connect back."""
        from pathlib import Path

        app = Path(redis_shared.__file__).parent
        others = [
            str(path.relative_to(app))
            for path in app.rglob("*.py")
            if path.name != "redis_shared.py" and "redis.from_url" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(others, [])


if __name__ == "__main__":
    unittest.main()

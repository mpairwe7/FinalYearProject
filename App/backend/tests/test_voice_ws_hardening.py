"""P1-9: voice WebSocket hardening.

Covers:
  * ws_concurrency per-user + global slot caps
  * voice sockets (v1 + v2) reject anonymous connections when auth is required
  * voice sockets still accept anonymous connections when auth is NOT required
    (so the auth gate is conditional, not always-on)
"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


class WsConcurrencyCapTest(unittest.TestCase):
    def setUp(self) -> None:
        from app import ws_concurrency

        ws_concurrency.reset()

    def tearDown(self) -> None:
        from app import ws_concurrency

        ws_concurrency.reset()

    def test_per_user_cap_blocks_excess(self) -> None:
        from app import ws_concurrency

        self.assertTrue(ws_concurrency.try_acquire("voice", "u1", per_user_cap=2, global_cap=10))
        self.assertTrue(ws_concurrency.try_acquire("voice", "u1", per_user_cap=2, global_cap=10))
        # Third for the same user is blocked.
        self.assertFalse(ws_concurrency.try_acquire("voice", "u1", per_user_cap=2, global_cap=10))
        # A different user is unaffected.
        self.assertTrue(ws_concurrency.try_acquire("voice", "u2", per_user_cap=2, global_cap=10))
        # Releasing frees a slot back up.
        ws_concurrency.release("voice", "u1")
        self.assertTrue(ws_concurrency.try_acquire("voice", "u1", per_user_cap=2, global_cap=10))

    def test_global_cap_blocks_excess(self) -> None:
        from app import ws_concurrency

        self.assertTrue(ws_concurrency.try_acquire("voice", "a", per_user_cap=5, global_cap=2))
        self.assertTrue(ws_concurrency.try_acquire("voice", "b", per_user_cap=5, global_cap=2))
        # Global cap reached even though no single user is over their cap.
        self.assertFalse(ws_concurrency.try_acquire("voice", "c", per_user_cap=5, global_cap=2))
        self.assertEqual(ws_concurrency.active("voice"), 2)

    def test_pools_have_independent_budgets(self) -> None:
        from app import ws_concurrency

        self.assertTrue(ws_concurrency.try_acquire("voice", "u", per_user_cap=1, global_cap=1))
        self.assertFalse(ws_concurrency.try_acquire("voice", "u", per_user_cap=1, global_cap=1))
        # A different pool has its own budget.
        self.assertTrue(ws_concurrency.try_acquire("chat", "u", per_user_cap=1, global_cap=1))


class VoiceWsAuthTest(unittest.TestCase):
    def tearDown(self) -> None:
        from app import ws_concurrency
        from app.flags import flags

        flags.clear("native_voice")
        flags.clear("voice_streaming")
        flags.clear("auth_required")
        ws_concurrency.reset()

    def _expect_rejected(self, path: str) -> None:
        from app.main import app

        client = TestClient(app)
        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect(path):
                pass

    def test_v2_voice_rejects_anonymous_when_auth_required(self) -> None:
        from app.flags import flags

        flags.set("native_voice", True)
        flags.set("auth_required", True)
        self._expect_rejected("/v2/voice/chat/stream")

    def test_v1_voice_rejects_anonymous_when_auth_required(self) -> None:
        from app.flags import flags

        flags.set("voice_streaming", True)
        flags.set("auth_required", True)
        self._expect_rejected("/v1/voice/chat/stream")

    def test_v2_voice_allows_anonymous_when_auth_not_required(self) -> None:
        # With auth NOT required the handshake completes (auth passes, slot is
        # acquired, accept happens) — proving the gate is conditional.
        from app.flags import flags
        from app.main import app

        flags.set("native_voice", True)
        flags.clear("auth_required")
        client = TestClient(app)
        connected = False
        with client.websocket_connect("/v2/voice/chat/stream"):
            connected = True
        self.assertTrue(connected)


if __name__ == "__main__":
    unittest.main()

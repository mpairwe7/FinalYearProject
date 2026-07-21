"""Unit tests for the Sunbird primary→fallback account resilience (no network)."""

from __future__ import annotations

import unittest
import unittest.mock as mock

from app import sunbird


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _fake_client_factory(behaviour):
    """Return a _client_for(token) substitute; ``behaviour`` maps token→callable(path,**kw)."""
    seen = []

    def _client_for(token):
        c = mock.Mock()
        def post(path, **kw):
            seen.append(token)
            return behaviour[token](path, **kw)
        c.post.side_effect = post
        return c

    return _client_for, seen


class SunbirdFallbackTest(unittest.TestCase):
    def setUp(self):
        sunbird._clients.clear()

    def tearDown(self):
        sunbird._clients.clear()

    def test_is_available_with_either_token(self):
        with mock.patch.object(sunbird, "SUNBIRD_API_TOKEN", ""), \
             mock.patch.object(sunbird, "SUNBIRD_FALLBACK_API_TOKEN", ""):
            self.assertFalse(sunbird.is_available())
        with mock.patch.object(sunbird, "SUNBIRD_API_TOKEN", "p"), \
             mock.patch.object(sunbird, "SUNBIRD_FALLBACK_API_TOKEN", ""):
            self.assertTrue(sunbird.is_available())
        with mock.patch.object(sunbird, "SUNBIRD_API_TOKEN", ""), \
             mock.patch.object(sunbird, "SUNBIRD_FALLBACK_API_TOKEN", "f"):
            self.assertTrue(sunbird.is_available())  # fallback alone is enough

    def test_account_order_primary_then_fallback(self):
        with mock.patch.object(sunbird, "SUNBIRD_API_TOKEN", "primary"), \
             mock.patch.object(sunbird, "SUNBIRD_FALLBACK_API_TOKEN", "fallback"):
            self.assertEqual(sunbird._account_tokens(), ["primary", "fallback"])

    def test_post_falls_back_on_primary_failure(self):
        def primary(path, **kw):
            raise RuntimeError("HTTP 429")
        def fallback(path, **kw):
            return _Resp({"ok": True})
        cf, seen = _fake_client_factory({"primary": primary, "fallback": fallback})
        with mock.patch.object(sunbird, "SUNBIRD_API_TOKEN", "primary"), \
             mock.patch.object(sunbird, "SUNBIRD_FALLBACK_API_TOKEN", "fallback"), \
             mock.patch.object(sunbird, "_client_for", side_effect=cf):
            resp = sunbird._post("/tasks/translate", json={})
        self.assertEqual(resp.json(), {"ok": True})
        self.assertEqual(seen, ["primary", "fallback"])

    def test_post_primary_success_skips_fallback(self):
        cf, seen = _fake_client_factory({
            "primary": lambda p, **kw: _Resp({"ok": 1}),
            "fallback": lambda p, **kw: _Resp({"never": True}),
        })
        with mock.patch.object(sunbird, "SUNBIRD_API_TOKEN", "primary"), \
             mock.patch.object(sunbird, "SUNBIRD_FALLBACK_API_TOKEN", "fallback"), \
             mock.patch.object(sunbird, "_client_for", side_effect=cf):
            sunbird._post("/x", json={})
        self.assertEqual(seen, ["primary"])  # fallback untouched

    def test_post_all_accounts_fail_raises(self):
        def boom(path, **kw):
            raise RuntimeError("HTTP 500")
        cf, _ = _fake_client_factory({"primary": boom, "fallback": boom})
        with mock.patch.object(sunbird, "SUNBIRD_API_TOKEN", "primary"), \
             mock.patch.object(sunbird, "SUNBIRD_FALLBACK_API_TOKEN", "fallback"), \
             mock.patch.object(sunbird, "_client_for", side_effect=cf):
            with self.assertRaises(RuntimeError):
                sunbird._post("/x", json={})

    def test_translate_recovers_via_fallback(self):
        def primary(path, **kw):
            raise RuntimeError("HTTP 401")
        def fallback(path, **kw):
            return _Resp({"output": {"translated_text": "Omusolo"}})
        cf, _ = _fake_client_factory({"primary": primary, "fallback": fallback})
        with mock.patch.object(sunbird, "SUNBIRD_API_TOKEN", "primary"), \
             mock.patch.object(sunbird, "SUNBIRD_FALLBACK_API_TOKEN", "fallback"), \
             mock.patch.object(sunbird, "_client_for", side_effect=cf):
            out = sunbird.translate("VAT rate", "eng", "lug")
        self.assertEqual(out, "Omusolo")


if __name__ == "__main__":
    unittest.main()

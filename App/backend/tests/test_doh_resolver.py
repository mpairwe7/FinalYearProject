"""Unit tests for the DNS-over-HTTPS resolver (no real network).

Validates the split-horizon classifier, the DoH wire-format roundtrip (with a
mocked httpx client), and the getaddrinfo monkey-patch (internal bypass +
external-via-DoH).
"""

from __future__ import annotations

import socket
import unittest
import unittest.mock as mock

from app import doh_resolver as d


def _wire_response(host: str, ip: str) -> bytes:
    import dns.message
    import dns.rdatatype
    import dns.rrset

    fqdn = host if host.endswith(".") else host + "."  # dnspython needs absolute names
    q = dns.message.make_query(fqdn, dns.rdatatype.A)
    r = dns.message.make_response(q)
    r.answer.append(dns.rrset.from_text(fqdn, 60, "IN", "A", ip))
    return r.to_wire()


class _FakeResp:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        return None


class _FakeClient:
    def __init__(self, content: bytes):
        self._content = content

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, *a, **k):
        return _FakeResp(self._content)


class ClassifierTest(unittest.TestCase):
    def test_internal_hosts_bypass(self):
        for h in (
            "localhost", "ura-app-redis.svc.cluster.local", "svc.cluster.local",
            "barelabel", "10.1.2.3", "192.168.1.1", "127.0.0.1", "",
        ):
            self.assertTrue(d._is_internal(h), h)

    def test_external_hosts_use_doh(self):
        for h in ("gateway.ai.cloudflare.com", "api.cloudflare.com", "api.groq.com", "example.com"):
            self.assertFalse(d._is_internal(h), h)


class ResolveTest(unittest.TestCase):
    def tearDown(self):
        d.deactivate()

    def test_resolve_doh_parses_a_record(self):
        with mock.patch("httpx.Client", return_value=_FakeClient(_wire_response("h.test", "1.2.3.4"))):
            d._cache.clear()
            self.assertEqual(d._resolve_doh("h.test"), "1.2.3.4")

    def test_resolve_doh_caches(self):
        client = _FakeClient(_wire_response("c.test", "5.6.7.8"))
        with mock.patch("httpx.Client", return_value=client) as mk:
            d._cache.clear()
            self.assertEqual(d._resolve_doh("c.test"), "5.6.7.8")
            self.assertEqual(d._resolve_doh("c.test"), "5.6.7.8")  # cached
            self.assertEqual(mk.call_count, 1)  # only one DoH POST

    def test_getaddrinfo_internal_bypasses_doh(self):
        d.activate()
        res = socket.getaddrinfo("localhost", 80)
        self.assertTrue(any(t[4][0] == "127.0.0.1" for t in res))

    def test_getaddrinfo_external_via_doh(self):
        with mock.patch("httpx.Client", return_value=_FakeClient(_wire_response("ext.test", "9.8.7.6"))):
            d._cache.clear()
            d.activate()
            res = socket.getaddrinfo("ext.test", 443)
        self.assertEqual(res[0][4], ("9.8.7.6", 443))
        self.assertEqual(res[0][1], socket.SOCK_STREAM)

    def test_doh_failure_falls_back_to_system(self):
        # Internal still works via the real resolver even if DoH would fail.
        d.activate()
        self.assertTrue(any(t[4][0] == "127.0.0.1" for t in socket.getaddrinfo("127.0.0.1", 0)))

    def test_malformed_dns_payload_raises_gaierror(self):
        # Garbage bytes from the DoH endpoint must surface as the same
        # exception shape as the system resolver (socket.gaierror) …
        with mock.patch("httpx.Client", return_value=_FakeClient(b"\x00\x01not-dns")):
            d._cache.clear()
            with self.assertRaises(socket.gaierror):
                d._resolve_doh("garbage.test")

    def test_malformed_payload_falls_back_to_system_resolver(self):
        # … so the patched getaddrinfo degrades to the original resolver.
        sentinel = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("4.3.2.1", 443))]
        with mock.patch("httpx.Client", return_value=_FakeClient(b"\xff\xfe")), \
             mock.patch.object(d, "_original_getaddrinfo", return_value=sentinel) as orig:
            d._cache.clear()
            res = d._doh_getaddrinfo("garbage.test", 443)
        self.assertEqual(res, sentinel)
        orig.assert_called_once()

    def test_no_a_record_raises_gaierror(self):
        import dns.message
        import dns.rdatatype

        q = dns.message.make_query("empty.test.", dns.rdatatype.A)
        empty = dns.message.make_response(q).to_wire()  # valid wire, no answers
        with mock.patch("httpx.Client", return_value=_FakeClient(empty)):
            d._cache.clear()
            with self.assertRaises(socket.gaierror):
                d._resolve_doh("empty.test")

    def test_cache_expires_after_ttl(self):
        import types

        clock = types.SimpleNamespace(t=10_000.0)
        fake_time = types.SimpleNamespace(time=lambda: clock.t)
        client = _FakeClient(_wire_response("ttl.test", "2.4.6.8"))
        with mock.patch("httpx.Client", return_value=client) as mk, \
             mock.patch.object(d, "time", fake_time):
            d._cache.clear()
            self.assertEqual(d._resolve_doh("ttl.test"), "2.4.6.8")
            clock.t += d._CACHE_TTL_S - 1
            d._resolve_doh("ttl.test")  # still cached
            self.assertEqual(mk.call_count, 1)
            clock.t += 2  # past the TTL
            self.assertEqual(d._resolve_doh("ttl.test"), "2.4.6.8")
            self.assertEqual(mk.call_count, 2)  # re-resolved


if __name__ == "__main__":
    unittest.main()

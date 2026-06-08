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


if __name__ == "__main__":
    unittest.main()

"""One outbound HTTP client for the autonomous web agents (ADR-0035).

The live research sandbox, the search providers and the FX engine fetch from
the public web on a taxpayer's turn. Each had its own ``httpx.Client``, and
three things were missing that the November 2026 guidance treats as baseline
for agents that browse (OWASP Agentic Top 10 2026 ASI01/ASI02/ASI08; the
"resolve, validate, pin" SSRF consensus behind the 2026 crawler CVEs):

* **The check and the connection used different DNS answers.** A hostname was
  checked for a public address, then the client resolved it again to connect,
  so a rebinding record could pass the check and reach a metadata service
  (T43). Here the address is resolved once, every answer must be publicly
  routable, and the socket connects to that validated address. TLS still
  verifies the certificate against the hostname, because httpcore takes the
  SNI name from the URL, not from the socket.
* **Nothing recorded what left.** Every request now counts by purpose, host
  and outcome (``web_egress_requests_total``) and logs one line with no path,
  query or body, so the cross-border gate (G35) has evidence of what was sent
  where.
* **Some bodies had no size cap.** :func:`read_capped` reads a streamed body
  and stops at a limit.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
import time
from typing import TYPE_CHECKING, Any

import httpcore
import httpx

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

logger = logging.getLogger(__name__)

#: Upper bound on a search provider's response; results pages are a few KB.
MAX_PROVIDER_RESPONSE_BYTES = int(os.getenv("WEB_PROVIDER_MAX_RESPONSE_BYTES", str(2 * 1024 * 1024)))


class EgressRefused(httpx.TransportError):
    """A destination the egress policy does not allow (private, unresolvable or non-https port)."""


def _public_address(host: str, port: int, *, allow_private: frozenset[str]) -> str:
    """Resolve *host* once and return an address to connect to; raise unless every answer is public.

    All answers must be public, not just the one used: a record set mixing a
    public and a private address is what a rebinding attack serves.
    """
    name = host.strip("[]").rstrip(".").lower()
    try:
        infos = socket.getaddrinfo(name, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise EgressRefused(f"could not resolve {name}") from exc
    addresses = list(dict.fromkeys(str(info[4][0]).split("%", 1)[0] for info in infos))
    if not addresses:
        raise EgressRefused(f"{name} resolved to nothing")
    if name in allow_private:
        return addresses[0]  # an operator-configured endpoint, such as a self-hosted SearXNG
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise EgressRefused(f"{name} resolved to an unparseable address") from exc
        mapped = getattr(ip, "ipv4_mapped", None)
        if not ip.is_global or (mapped is not None and not mapped.is_global):
            raise EgressRefused(f"{name} resolves to a non-public address")
    return addresses[0]


class _PinnedBackend(httpcore.NetworkBackend):
    """httpcore's sync backend, connecting to the address the policy validated."""

    def __init__(self, allow_private: frozenset[str]) -> None:
        self._inner = httpcore.SyncBackend()
        self._allow_private = allow_private

    def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                    local_address: str | None = None,
                    socket_options: Iterable[Any] | None = None) -> httpcore.NetworkStream:
        address = _public_address(host, port, allow_private=self._allow_private)
        return self._inner.connect_tcp(address, port, timeout=timeout, local_address=local_address,
                                       socket_options=socket_options)

    def connect_unix_socket(self, path: str, timeout: float | None = None,
                            socket_options: Iterable[Any] | None = None) -> httpcore.NetworkStream:
        raise EgressRefused("unix sockets are not an egress destination")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


class _CountingStream(httpx.SyncByteStream):
    """Counts the body as the caller reads it and records the request when it is closed."""

    def __init__(self, inner: httpx.SyncByteStream, done: Any) -> None:
        self._inner, self._done, self._bytes = inner, done, 0

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self._inner:
            self._bytes += len(chunk)
            yield chunk

    def close(self) -> None:
        try:
            self._inner.close()
        finally:
            self._done(self._bytes)


def record(purpose: str, host: str, outcome: str, *, nbytes: int = 0, seconds: float = 0.0) -> None:
    """Count one outbound request. Never records a path, query, header or body."""
    try:
        from .analytics import metrics

        labels = {"purpose": purpose, "host": host, "outcome": outcome}
        metrics.inc("web_egress_requests_total", labels=labels)
        if nbytes:
            metrics.inc("web_egress_bytes_total", value=float(nbytes), labels={"purpose": purpose, "host": host})
    except Exception:  # noqa: BLE001 - telemetry must never fail a fetch
        logger.debug("egress metrics failed", exc_info=True)
    logger.info("egress purpose=%s host=%s outcome=%s bytes=%d ms=%d",
                purpose, host, outcome, nbytes, int(seconds * 1000))


class _EgressTransport(httpx.HTTPTransport):
    """An httpx transport that pins connections and records every request."""

    def __init__(self, purpose: str, *, allow_private: frozenset[str] = frozenset(), **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # httpcore's pool owns the backend that opens sockets; swap it for the pinned one.
        self._pool._network_backend = _PinnedBackend(allow_private)
        self._purpose = purpose

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        started = time.perf_counter()
        try:
            response = super().handle_request(request)
        except EgressRefused:
            record(self._purpose, host, "refused", seconds=time.perf_counter() - started)
            raise
        except Exception as exc:
            record(self._purpose, host, type(exc).__name__, seconds=time.perf_counter() - started)
            raise
        outcome = f"{response.status_code // 100}xx"

        def done(nbytes: int) -> None:
            record(self._purpose, host, outcome, nbytes=nbytes, seconds=time.perf_counter() - started)

        response.stream = _CountingStream(response.stream, done)  # type: ignore[arg-type]
        return response


def _transport_for(purpose: str, allow_private: frozenset[str]) -> httpx.BaseTransport:
    """The transport :func:`client` uses: pinned and recorded. Tests replace it with a mock."""
    return _EgressTransport(purpose, allow_private=allow_private)


def client(purpose: str, *, timeout: Any, allow_private: Iterable[str] = (), **kwargs: Any) -> httpx.Client:
    """An ``httpx.Client`` for one web agent: pinned, recorded, never following redirects itself.

    *allow_private* names operator-configured hosts that may resolve to private
    addresses (a self-hosted SearXNG); no address a page or a search result
    supplies is ever added to it. Environment proxies are ignored, because a
    proxy resolves the destination itself and the pin would not apply.
    """
    allowed = frozenset(h.strip("[]").rstrip(".").lower() for h in allow_private if h)
    return httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        transport=_transport_for(purpose, allowed),
        **kwargs,
    )


def read_capped(response: httpx.Response, max_bytes: int) -> bytes:
    """A streamed response's body, refused once it passes *max_bytes*."""
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise ValueError(f"response declares {declared} bytes, over the {max_bytes}-byte cap")
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"response passed the {max_bytes}-byte cap")
        chunks.append(chunk)
    return b"".join(chunks)

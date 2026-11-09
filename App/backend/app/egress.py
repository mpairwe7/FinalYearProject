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
    """A destination the egress policy does not allow.

    ``outcome`` separates a DNS outage (``unresolvable``) from a security event
    (``refused``: a non-public answer), so an operator does not read one as the other.
    """

    def __init__(self, message: str, *, outcome: str = "refused") -> None:
        super().__init__(message)
        self.outcome = outcome


#: Hosts grouped into a bounded label vocabulary (metric labels must be bounded); the
#: log line keeps the full hostname. A host matches the longest suffix it ends with.
_HOST_GROUPS = (
    "ura.go.ug", "parliament.go.ug", "ulii.org", "finance.go.ug", "bou.or.ug", "ursb.go.ug", "unbs.go.ug",
    "eac.int", "xe.com", "google.com", "er-api.com", "api.tavily.com", "api.search.brave.com", "duckduckgo.com",
)


def host_group(host: str, *, operator_hosts: frozenset[str] = frozenset()) -> str:
    """The bounded metric label for *host*: its known group, ``operator`` or ``other``."""
    name = host.strip("[]").rstrip(".").lower()
    if name in operator_hosts:
        return "operator"
    for group in sorted(_HOST_GROUPS, key=len, reverse=True):
        if name == group or name.endswith("." + group):
            return group
    return "other"


def _public_addresses(host: str, port: int, *, allow_private: frozenset[str]) -> list[str]:
    """Resolve *host* once and return its addresses; raise unless every answer is public.

    All answers must be public, not just the one used: a record set mixing a
    public and a private address is what a rebinding attack serves.
    """
    name = host.strip("[]").rstrip(".").lower()
    try:
        infos = socket.getaddrinfo(name, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise EgressRefused(f"could not resolve {name}", outcome="unresolvable") from exc
    addresses = list(dict.fromkeys(str(info[4][0]).split("%", 1)[0] for info in infos))
    if not addresses:
        raise EgressRefused(f"{name} resolved to nothing", outcome="unresolvable")
    if name in allow_private:
        return addresses  # an operator-configured endpoint, such as a self-hosted SearXNG
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise EgressRefused(f"{name} resolved to an unparseable address") from exc
        mapped = getattr(ip, "ipv4_mapped", None)
        if not ip.is_global or (mapped is not None and not mapped.is_global):
            raise EgressRefused(f"{name} resolves to a non-public address")
    return addresses


class _PinnedBackend(httpcore.NetworkBackend):
    """httpcore's sync backend, connecting to the address the policy validated."""

    def __init__(self, allow_private: frozenset[str]) -> None:
        self._inner = httpcore.SyncBackend()
        self._allow_private = allow_private

    def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                    local_address: str | None = None,
                    socket_options: Iterable[Any] | None = None) -> httpcore.NetworkStream:
        last_error: Exception | None = None
        # Every answer was validated together; try them in turn without resolving again,
        # so one unreachable address does not fail a host that has another.
        for address in _public_addresses(host, port, allow_private=self._allow_private):
            try:
                return self._inner.connect_tcp(address, port, timeout=timeout, local_address=local_address,
                                               socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    def connect_unix_socket(self, path: str, timeout: float | None = None,
                            socket_options: Iterable[Any] | None = None) -> httpcore.NetworkStream:
        raise EgressRefused("unix sockets are not an egress destination")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


class _CountingStream(httpx.SyncByteStream):
    """Counts the body as the caller reads it and records the request when it is closed.

    A body that fails part-way (a reset after the headers) is recorded with the
    error's class, not the status class it started with.
    """

    def __init__(self, inner: httpx.SyncByteStream, done: Any) -> None:
        self._inner, self._done, self._bytes, self._error = inner, done, 0, ""

    def __iter__(self) -> Iterator[bytes]:
        try:
            for chunk in self._inner:
                self._bytes += len(chunk)
                yield chunk
        except Exception as exc:
            self._error = type(exc).__name__
            raise

    def close(self) -> None:
        try:
            self._inner.close()
        finally:
            self._done(self._bytes, self._error)


def record(purpose: str, host: str, outcome: str, *, nbytes: int = 0, sent: int = 0, seconds: float = 0.0,
           group: str = "") -> None:
    """Count one outbound request. Never records a path, query, header or body.

    ``sent`` is what left the deployment (URL plus request body), ``nbytes`` what
    came back. Metric labels use the bounded host *group*; the log keeps the host.
    """
    label = group or host_group(host)
    try:
        from .analytics import metrics

        metrics.inc("web_egress_requests_total", labels={"purpose": purpose, "host": label, "outcome": outcome})
        if sent:
            metrics.inc("web_egress_sent_bytes_total", value=float(sent), labels={"purpose": purpose, "host": label})
        if nbytes:
            metrics.inc("web_egress_received_bytes_total", value=float(nbytes),
                        labels={"purpose": purpose, "host": label})
    except Exception:  # noqa: BLE001 - telemetry must never fail a fetch
        logger.debug("egress metrics failed", exc_info=True)
    logger.info("egress purpose=%s host=%s outcome=%s sent=%d received=%d ms=%d",
                purpose, host, outcome, sent, nbytes, int(seconds * 1000))


class _EgressTransport(httpx.HTTPTransport):
    """An httpx transport that pins connections and records every request."""

    def __init__(self, purpose: str, *, allow_private: frozenset[str] = frozenset(), **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # httpcore's pool owns the backend that opens sockets; swap it for the pinned one.
        self._pool._network_backend = _PinnedBackend(allow_private)
        self._purpose = purpose
        self._operator_hosts = allow_private

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        group = host_group(host, operator_hosts=self._operator_hosts)
        # What leaves: the target (search terms travel in the query string) and the body.
        sent = len(request.url.raw_path) + len(request.url.host) + len(request.content or b"")
        started = time.perf_counter()
        try:
            response = super().handle_request(request)
        except EgressRefused as exc:
            record(self._purpose, host, exc.outcome, seconds=time.perf_counter() - started, group=group)
            raise
        except Exception as exc:
            record(self._purpose, host, type(exc).__name__, sent=sent, seconds=time.perf_counter() - started,
                   group=group)
            raise
        outcome = f"{response.status_code // 100}xx"

        def done(nbytes: int, error: str) -> None:
            record(self._purpose, host, error or outcome, nbytes=nbytes, sent=sent,
                   seconds=time.perf_counter() - started, group=group)

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


#: zlib window bits per content coding; anything else is refused rather than decoded.
_DECODERS = {"gzip": 31, "x-gzip": 31, "deflate": 15}


def read_capped(response: httpx.Response, max_bytes: int) -> bytes:
    """A streamed response's decoded body, refused once it passes *max_bytes*.

    The cap holds before anything is allocated past it: the raw stream is read
    (``iter_raw``) and decompressed with a bounded window, so a small gzip body
    cannot expand into worker memory before its size is known.
    """
    import zlib

    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise ValueError(f"response declares {declared} bytes, over the {max_bytes}-byte cap")
    coding = response.headers.get("content-encoding", "").strip().lower()
    if coding in ("", "identity"):
        decoder = None
    elif coding in _DECODERS:
        decoder = zlib.decompressobj(_DECODERS[coding])
    else:
        raise ValueError(f"unsupported content encoding {coding!r}")
    if getattr(response, "_content", None) is not None:
        # Already read whole (a body handed over in memory): nothing left to bound but its size.
        if len(response.content) > max_bytes:
            raise ValueError(f"response passed the {max_bytes}-byte cap")
        return response.content
    chunks: list[bytes] = []
    total = 0
    for raw in response.iter_raw():
        pieces = [raw] if decoder is None else []
        if decoder is not None:
            data = raw
            while data:
                piece = decoder.decompress(data, max_bytes + 1 - total)
                pieces.append(piece)
                total_after = total + sum(len(p) for p in pieces)
                if total_after > max_bytes:
                    raise ValueError(f"response passed the {max_bytes}-byte cap")
                data = decoder.unconsumed_tail
        for piece in pieces:
            total += len(piece)
            if total > max_bytes:
                raise ValueError(f"response passed the {max_bytes}-byte cap")
            chunks.append(piece)
    return b"".join(chunks)

"""DNS-over-HTTPS resolver for environments where standard DNS is broken.

Ported from AgriLinkUganda's proven Crane Cloud / RENU workaround
(``docs/operations/dns-workaround-doh.md``). The RENU pod has outbound
TCP/443 open but **no working upstream DNS** — literal ``1.1.1.1`` connects,
but every hostname (``gateway.ai.cloudflare.com``, the vLLM endpoint,
``api.cloudflare.com`` …) fails before TCP connect. That breaks the Cloudflare
fallback (Workers AI / Vectorize) *and* the external LLM.

When ``USE_DOH=true`` this module patches ``socket.getaddrinfo`` so external
hostname resolutions are sent as DNS A-record queries to
``https://1.1.1.1/dns-query`` over httpx (using dnspython for the wire format).
Split-horizon: cluster-internal names (``*.svc.cluster.local``, ``localhost``,
single-label, IP literals) bypass DoH and use the system resolver — that keeps
Redis / Qdrant service-discovery healthy. Once RENU adds an upstream resolver to
the pod, set ``USE_DOH=false`` and the patch becomes a no-op.
"""

from __future__ import annotations

import logging
import socket
import threading
import time

import httpx

logger = logging.getLogger("ura.doh")

_DOH_URL = "https://1.1.1.1/dns-query"
_CACHE_TTL_S = 60.0
_DOH_TIMEOUT_S = 5.0

_cache: dict[str, tuple[str, float]] = {}
_cache_lock = threading.Lock()
_original_getaddrinfo = socket.getaddrinfo
_activated = False


def _looks_like_ip(host: str) -> bool:
    """Cheap check for an IPv4 literal."""
    parts = host.split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit() or not 0 <= int(p) <= 255:
            return False
    return True


def _is_internal(host: str) -> bool:
    """Hostnames that should bypass DoH and use the system resolver."""
    if not host:
        return True
    h = host.lower()
    if h == "localhost" or h.endswith((".svc.cluster.local", ".cluster.local")):
        return True
    if "." not in h:  # bare label — local / service-discovery
        return True
    return _looks_like_ip(h)


def _resolve_doh(host: str) -> str:
    """Return one resolved A-record IP for *host* via DoH, or raise
    ``socket.gaierror`` (same exception shape as the original resolver)."""
    with _cache_lock:
        cached = _cache.get(host)
        if cached and cached[1] > time.time():
            return cached[0]

    try:
        import dns.exception
        import dns.message
        import dns.rdatatype

        query = dns.message.make_query(host, dns.rdatatype.A)
        # POST to a literal IP (1.1.1.1) → no DNS recursion is triggered.
        with httpx.Client(timeout=_DOH_TIMEOUT_S) as client:
            resp = client.post(
                _DOH_URL,
                content=query.to_wire(),
                headers={
                    "Content-Type": "application/dns-message",
                    "Accept": "application/dns-message",
                },
            )
            resp.raise_for_status()
            msg = dns.message.from_wire(resp.content)
    except (httpx.HTTPError, Exception) as exc:  # noqa: BLE001 - normalise to gaierror
        raise socket.gaierror(socket.EAI_AGAIN, f"DoH lookup failed for {host}: {exc}") from exc

    import dns.rdatatype

    for rrset in msg.answer:
        if rrset.rdtype != dns.rdatatype.A:
            continue
        for item in rrset:
            ip = item.address  # type: ignore[attr-defined]
            with _cache_lock:
                _cache[host] = (ip, time.time() + _CACHE_TTL_S)
            return ip

    raise socket.gaierror(socket.EAI_NONAME, f"No A record returned for {host}")


def _doh_getaddrinfo(host, port, *args, **kwargs):
    """Drop-in ``socket.getaddrinfo``: external hostnames via DoH; internal /
    IP / bare-label hosts via the original resolver."""
    if _is_internal(host):
        return _original_getaddrinfo(host, port, *args, **kwargs)

    try:
        ip = _resolve_doh(host)
    except socket.gaierror as exc:
        logger.warning("doh fallback to system resolver for %s: %s", host, exc)
        return _original_getaddrinfo(host, port, *args, **kwargs)

    if isinstance(port, int):
        port_num = port
    elif port is None:
        port_num = 0
    elif isinstance(port, str) and port.isdigit():
        port_num = int(port)
    elif isinstance(port, str):
        try:
            port_num = socket.getservbyname(port)
        except OSError:
            port_num = 0
    else:
        port_num = 0
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port_num))]


def is_enabled() -> bool:
    """True when USE_DOH is set truthy in the environment."""
    import os

    return os.getenv("USE_DOH", "false").strip().lower() in ("1", "true", "yes", "on")


def activate() -> None:
    """Idempotently install the DoH resolver as ``socket.getaddrinfo``."""
    global _activated
    if _activated:
        return
    socket.getaddrinfo = _doh_getaddrinfo  # type: ignore[assignment]
    _activated = True
    logger.info(
        "DoH resolver activated (upstream=%s, cache_ttl=%.0fs, timeout=%.0fs)",
        _DOH_URL,
        _CACHE_TTL_S,
        _DOH_TIMEOUT_S,
    )


def deactivate() -> None:
    """Restore the original ``socket.getaddrinfo`` (useful for tests)."""
    global _activated
    socket.getaddrinfo = _original_getaddrinfo  # type: ignore[assignment]
    _activated = False
    with _cache_lock:
        _cache.clear()

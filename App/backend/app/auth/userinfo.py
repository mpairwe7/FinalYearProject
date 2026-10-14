"""The signed-in person's name and email, from the identity provider's profile.

An Auth0 access token for the console's API carries ``sub``, ``permissions``
and the standard claims — no name, no email. Without them the console fell
back to the account id: a taxpayer was told "Officer Auth0|6a7d7b1b… has
joined", the duty roster listed the same, and ``/v1/me`` returned no email.

The console asks for ``openid profile email`` with an API audience, so the
same access token is accepted at the provider's ``/userinfo`` endpoint. This
module asks it once per person and keeps the answer in memory:

- only for a verified RS256 token that has no ``email`` of its own (a dev
  HS256 token never reaches the network);
- only the profile of the token's own subject — a response for another
  ``sub`` is ignored;
- only display fields (``email``, ``name``, ``given_name``, ``nickname``,
  ``preferred_username``), each capped at 128 characters;
- an hour per person (``OIDC_USERINFO_CACHE_TTL_S``), and a failure is
  remembered for five minutes so an outage costs one request per person, not
  one per API call;
- never raising: without a profile the claims are left as they were.

``OIDC_USERINFO_URL`` overrides the endpoint (default ``<OIDC_ISSUER>/userinfo``,
as Auth0 serves it); ``OIDC_USERINFO_ENABLED=false`` turns the lookup off.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from collections import OrderedDict
from typing import Any

from .jwt_auth import APP_ENV, OIDC_ISSUER, _JWKSRedirectHandler

logger = logging.getLogger(__name__)

PROFILE_FIELDS: tuple[str, ...] = ("email", "name", "given_name", "nickname", "preferred_username")
_MAX_ENTRIES = 2048
_FAILURE_TTL_S = 300.0

_lock = threading.Lock()
_cache: OrderedDict[str, tuple[float, dict[str, str]]] = OrderedDict()


def _enabled() -> bool:
    return os.getenv("OIDC_USERINFO_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def _url() -> str:
    explicit = os.getenv("OIDC_USERINFO_URL", "").strip()
    if explicit:
        return explicit
    issuer = os.getenv("OIDC_ISSUER", OIDC_ISSUER).strip()
    return f"{issuer.rstrip('/')}/userinfo" if issuer else ""


def _ttl_s() -> float:
    try:
        return float(os.getenv("OIDC_USERINFO_CACHE_TTL_S", "3600"))
    except ValueError:
        return 3600.0


def _timeout_s() -> float:
    try:
        return float(os.getenv("OIDC_USERINFO_TIMEOUT_S", "2"))
    except ValueError:
        return 2.0


def _fetch(url: str, token: str, timeout_s: float) -> dict[str, Any]:
    """GET the profile with the caller's own bearer token. Raises on any failure."""
    if APP_ENV == "production" and not url.startswith("https://"):
        raise ValueError("OIDC_USERINFO_URL must start with https:// in production")
    if not url.startswith(("https://", "http://")):
        raise ValueError("OIDC_USERINFO_URL must start with https:// or http://")
    opener = urllib.request.build_opener(_JWKSRedirectHandler())
    req = urllib.request.Request(url, headers={"Accept": "application/json", "Authorization": f"Bearer {token}"})
    with opener.open(req, timeout=timeout_s) as resp:  # nosec B310 # noqa: S310 # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("malformed userinfo payload")
    return payload


def _remember(sub: str, profile: dict[str, str], ttl_s: float) -> None:
    with _lock:
        _cache[sub] = (time.monotonic() + ttl_s, profile)
        _cache.move_to_end(sub)
        while len(_cache) > _MAX_ENTRIES:
            _cache.popitem(last=False)


def profile(token: str, claims: dict[str, Any]) -> dict[str, str]:
    """The display fields the provider holds for this token's subject; ``{}`` when unknown."""
    sub = str(claims.get("sub") or "")
    if not sub or claims.get("email") or not _enabled():
        return {}
    url = _url()
    if not url:
        return {}
    now = time.monotonic()
    with _lock:
        hit = _cache.get(sub)
        if hit and hit[0] > now:
            _cache.move_to_end(sub)
            return dict(hit[1])
    try:
        payload = _fetch(url, token, _timeout_s())
    except Exception as err:  # noqa: BLE001 — a missing name never fails a request
        logger.info("userinfo lookup failed for a signed-in user: %s", str(err)[:160])
        _remember(sub, {}, _FAILURE_TTL_S)
        return {}
    if str(payload.get("sub") or "") != sub:
        logger.warning("userinfo answered for a different subject; ignored")
        _remember(sub, {}, _FAILURE_TTL_S)
        return {}
    found = {
        key: str(payload[key]).strip()[:128]
        for key in PROFILE_FIELDS
        if isinstance(payload.get(key), str) and str(payload[key]).strip()
    }
    _remember(sub, found, _ttl_s())
    return dict(found)


def with_profile(token: str, claims: dict[str, Any]) -> dict[str, Any]:
    """The claims with the profile's display fields filled in; the token's own claims win."""
    found = profile(token, claims)
    return {**found, **claims} if found else claims


def reset_cache() -> None:
    """Forget every cached profile (tests)."""
    with _lock:
        _cache.clear()

"""A session can be revoked before its token expires (G304, ADR-0053).

The service kept no revocation list: a signed-out or compromised token kept
working on the API, and a staff socket opened with it stayed open, until the
token's own expiry. Now ``POST /v1/me/logout`` (any signed-in person) and
``POST /v1/admin/users/{user_id}/revoke`` (an administrator) store a cutoff
for the person in ``revoked_sessions``; every process re-reads that table every
5 s, and reads it before it serves; a token issued before the cutoff is
refused on the API (401 "session revoked") and at socket sign-in (4401), and an
open staff, call, chat or voice socket opened with one is closed 4401 within
about 5 s of the process knowing. An officer's LiveKit media is revoked at
once, and refused if it rejoins. A token with no usable ``iat`` could never be
revoked, so verification refuses it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import sys
import threading
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock, patch

import pytest
from app import database as db
from app import main as main_mod
from app.analytics import metrics
from app.auth import make_dev_token
from app.receptionist import desk, livekit, ws
from app.receptionist.hub import hub
from app.receptionist.state import registry
from app.receptionist.store import create_call, init_receptionist_schema
from fastapi.testclient import TestClient


def _revocation():
    from app.auth import revocation

    return revocation


def _counter(name: str) -> float:
    return float(metrics.snapshot()["counters"].get(name, 0))


def _signed(user: str, role: str = "public", *, iat: float | None, ttl: float = 600.0) -> str:
    """A token the development verifier accepts, issued at *iat* (no ``iat`` claim when None)."""
    from app.auth.dependencies import _get_verifier
    from app.auth.jwt_auth import _b64url_encode, _hs256_sign

    verifier = _get_verifier()
    now = time.time()
    claims: dict = {
        "sub": user,
        "tenant_id": "default",
        "iss": verifier.issuer or "ura-chatbot-dev",
        "aud": verifier.audience or "ura-chatbot",
        "nbf": int(now) - 5,
        "exp": now + ttl,
        "role": role,
    }
    if iat is not None:
        claims["iat"] = iat
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url_encode(json.dumps(claims).encode())
    return f"{header}.{payload}.{_hs256_sign(header, payload, verifier.dev_secret)}"


def _user(prefix: str = "u") -> str:
    """A user id no other test, or a row left in a shared store, can share."""
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


@pytest.fixture
def memory_db(monkeypatch: pytest.MonkeyPatch):
    """One in-memory store, as the Space's two processes share one."""
    conn = sqlite3.connect(":memory:", timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(db, "_get_connection", lambda: conn)
    db.init_db()
    yield db
    conn.close()


@pytest.fixture
def clean_map():
    """This process's revocation map and sync state, empty for the test and after it."""
    revocation = _revocation()
    revocation.reset()
    yield revocation
    revocation.reset()


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


def test_a_revocation_is_stored_and_keeps_the_later_cutoff(memory_db) -> None:
    alice, bob = _user("alice"), _user("bob")
    memory_db.revoke_sessions(alice, revoked_before=200.0, reason="logout", actor=alice)
    memory_db.revoke_sessions(bob, revoked_before=50.0, reason="compromised", actor="ops")

    # An earlier cutoff never moves it back: tokens it revoked stay revoked.
    memory_db.revoke_sessions(alice, revoked_before=100.0, reason="logout", actor=alice)
    assert memory_db.load_session_revocations() == {alice: 200.0, bob: 50.0}

    memory_db.revoke_sessions(alice, revoked_before=300.0, reason="logout", actor=alice)
    assert memory_db.load_session_revocations()[alice] == 300.0


def test_an_unreadable_sqlite_store_raises_instead_of_reading_empty(monkeypatch) -> None:
    """Read as {}, a broken store would look like nobody was ever signed out."""
    broken = sqlite3.connect(":memory:", check_same_thread=False)
    broken.close()
    monkeypatch.setattr(db, "_get_connection", lambda: broken)
    with pytest.raises(sqlite3.ProgrammingError):
        db.load_session_revocations()


def test_the_postgres_store_without_a_pool_raises() -> None:
    from app import postgres

    with mock.patch.object(postgres, "_get_pool", return_value=None):
        with pytest.raises(RuntimeError):
            postgres.load_session_revocations()
        # A write that silently did nothing would report a sign-out no process sees.
        with pytest.raises(RuntimeError):
            postgres.revoke_sessions("u", revoked_before=1.0, reason="logout", actor="u")


def test_the_postgres_backend_is_dispatched_like_the_other_helpers() -> None:
    from pathlib import Path

    source = Path(db.__file__).read_text(encoding="utf-8")
    assert "load_session_revocations = _pg.load_session_revocations" in source
    assert "revoke_sessions = _pg.revoke_sessions" in source
    pg_source = (Path(db.__file__).parent / "postgres.py").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS revoked_sessions" in pg_source


# ---------------------------------------------------------------------------
# This process's map
# ---------------------------------------------------------------------------


def test_a_token_issued_before_the_cutoff_is_revoked_and_one_issued_at_or_after_it_is_not(
    clean_map,
) -> None:
    clean_map.record("okello", 1000.5)

    assert clean_map.revoked_before("okello") == 1000.5
    assert clean_map.is_revoked("okello", 1000.0) is True  # the logout's own second
    assert clean_map.is_revoked("okello", 1000.5) is False
    assert clean_map.is_revoked("okello", 1001.0) is False  # a sign-in after it
    # Unknown and anonymous (no user, no `iat`) are never revoked.
    assert clean_map.is_revoked("nakato", 1.0) is False
    assert clean_map.is_revoked("", 1.0) is False
    assert clean_map.is_revoked("okello", None) is False


def test_a_cutoff_never_moves_back_in_a_running_process(clean_map) -> None:
    clean_map.record("okello", 2000.0)
    clean_map.record("okello", 1000.0)
    assert clean_map.revoked_before("okello") == 2000.0

    # The store is read every 5 s; an older row (read before this process
    # wrote its own) must not undo a revocation made here.
    clean_map.apply({"okello": 1500.0, "nakato": 10.0})
    assert clean_map.revoked_before("okello") == 2000.0
    assert clean_map.revoked_before("nakato") == 10.0


def test_the_claims_of_a_token_say_whether_it_is_revoked(clean_map) -> None:
    clean_map.record("okello", 1000.5)

    assert clean_map.claims_revoked({"sub": "okello", "iat": 1000}) is True
    assert clean_map.claims_revoked({"sub": "okello", "iat": 1001}) is False
    # No usable `iat`: not placeable against a cutoff. Verification refuses such
    # a token before it gets here (test_a_signed_token_without_a_usable_iat_is_refused).
    assert clean_map.claims_revoked({"sub": "okello"}) is False
    assert clean_map.claims_revoked({"sub": "okello", "iat": "1000"}) is False
    assert clean_map.claims_revoked({"sub": "okello", "iat": True}) is False
    assert clean_map.claims_revoked(None) is False


# ---------------------------------------------------------------------------
# One sync pass, and the loop the lifespan runs
# ---------------------------------------------------------------------------


def test_one_sync_pass_applies_the_store(clean_map) -> None:
    before = time.time()
    with mock.patch.object(db, "load_session_revocations", return_value={"okello": 1000.5}):
        clean_map.sync()

    assert clean_map.is_revoked("okello", 1000.0) is True
    assert clean_map.state()["synced_at"] >= before


def test_an_unreadable_store_keeps_the_map_logs_once_and_recovers(clean_map, caplog) -> None:
    with mock.patch.object(db, "load_session_revocations", return_value={"okello": 1000.5}):
        clean_map.sync()
    failures = _counter("session_revocation_sync_failures_total")
    caplog.set_level(logging.INFO, logger=clean_map.__name__)

    with mock.patch.object(
        db, "load_session_revocations", side_effect=RuntimeError("database is locked")
    ):
        clean_map.sync()
        clean_map.sync()

    assert clean_map.is_revoked("okello", 1000.0) is True
    assert _counter("session_revocation_sync_failures_total") - failures == 2
    unreadable = [r for r in caplog.records if "could not be read" in r.getMessage()]
    assert len(unreadable) == 1
    assert unreadable[0].levelno == logging.ERROR

    with mock.patch.object(
        db, "load_session_revocations", return_value={"okello": 1000.5, "nakato": 5.0}
    ):
        clean_map.sync()
    assert clean_map.is_revoked("nakato", 4.0) is True
    assert any("readable again" in r.getMessage() for r in caplog.records)


def test_the_sync_loop_runs_at_once_then_on_its_interval_and_stops_with_the_app() -> None:
    passes: list[float] = []
    revocation = _revocation()

    async def scenario() -> None:
        stop = asyncio.Event()
        with mock.patch.object(revocation, "sync", side_effect=lambda: passes.append(time.time())):
            task = asyncio.create_task(main_mod._session_revocation_sync_loop(stop, 0.01))
            async with asyncio.timeout(5):
                while len(passes) < 2:
                    await asyncio.sleep(0.01)
            stop.set()
            async with asyncio.timeout(2):
                await task

    asyncio.run(scenario())
    assert len(passes) >= 2


def test_the_lifespan_runs_the_sync_in_every_process() -> None:
    from app import receptionist

    revocation = _revocation()
    synced = threading.Event()
    with (
        mock.patch.object(revocation, "sync", side_effect=synced.set),
        mock.patch.object(receptionist, "start"),
        TestClient(main_mod.app),
    ):
        ran = synced.wait(timeout=5)
    assert ran
    assert main_mod._SESSION_REVOCATION_SYNC_INTERVAL_SECONDS <= 5


def test_a_restarted_process_refuses_a_revoked_token_from_its_first_request(memory_db, clean_map) -> None:
    """The sync loop's first pass ran only once the app was serving (review of ADR-0053).

    The revocations are read during startup, before the first request; the
    loop is stubbed out here so only that read can have applied them.
    """
    from app import receptionist

    user = _user("taxpayer")
    iat = int(time.time())
    memory_db.revoke_sessions(user, revoked_before=iat + 0.5, reason="logout", actor=user)
    token = _signed(user, "verified_taxpayer", iat=iat)

    async def no_periodic_sync(stop: asyncio.Event, _interval: float) -> None:
        await stop.wait()

    with (
        mock.patch.object(main_mod, "_session_revocation_sync_loop", no_periodic_sync),
        mock.patch.object(receptionist, "start"),
        TestClient(main_mod.app) as client,
    ):
        first = client.get("/v1/me/profile", headers=_bearer(token))

    assert first.status_code == 401, first.text
    assert first.json()["detail"] == "session revoked"


def _initialise_with_unreadable_revocations(monkeypatch, app_env: str) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    with (
        mock.patch.object(main_mod.db, "init_db"),
        mock.patch.object(main_mod, "_should_seed", return_value=False),
        mock.patch.object(main_mod.db, "load_flag_overrides", return_value={}),
        mock.patch.object(
            main_mod.db, "load_session_revocations", side_effect=RuntimeError("database is locked")
        ),
    ):
        main_mod._initialize_analytics_database()


def test_in_production_unreadable_revocations_at_start_refuse_to_start(monkeypatch, clean_map) -> None:
    """It would serve tokens signed out of, until a sync pass managed to read them."""
    with pytest.raises(SystemExit, match="session revocations"):
        _initialise_with_unreadable_revocations(monkeypatch, "production")


def test_elsewhere_unreadable_revocations_at_start_are_logged_and_start_goes_on(
    monkeypatch, clean_map, caplog
) -> None:
    failures = _counter("session_revocation_sync_failures_total")
    caplog.set_level(logging.ERROR)

    _initialise_with_unreadable_revocations(monkeypatch, "development")

    assert _counter("session_revocation_sync_failures_total") - failures == 1
    assert any("could not be read" in r.getMessage() for r in caplog.records)
    assert clean_map.state()["failing"] is True


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_sign_out_everywhere_refuses_the_token_and_a_later_sign_in_works(
    memory_db, clean_map
) -> None:
    user = _user("taxpayer")
    token = _signed(user, "verified_taxpayer", iat=int(time.time()))
    client = TestClient(main_mod.app)

    assert client.get("/v1/me/profile", headers=_bearer(token)).status_code == 200
    r = client.post("/v1/me/logout", headers=_bearer(token))
    assert r.status_code == 200, r.text
    cutoff = r.json()["revoked_before"]
    assert memory_db.load_session_revocations()[user] == cutoff

    refused = client.get("/v1/me/profile", headers=_bearer(token))
    assert refused.status_code == 401
    assert refused.json()["detail"] == "session revoked"
    assert refused.headers.get("www-authenticate") == "Bearer"

    fresh = _signed(user, "verified_taxpayer", iat=int(cutoff) + 1)
    assert client.get("/v1/me/profile", headers=_bearer(fresh)).status_code == 200


def test_sign_out_needs_a_signed_in_person(memory_db, clean_map) -> None:
    r = TestClient(main_mod.app).post("/v1/me/logout")
    assert r.status_code == 401


def test_sign_out_is_audited(memory_db, clean_map) -> None:
    user = _user("taxpayer")
    with mock.patch.object(main_mod, "_audit_staff_action") as audit:
        r = TestClient(main_mod.app).post(
            "/v1/me/logout",
            headers=_bearer(_signed(user, "verified_taxpayer", iat=int(time.time()))),
        )
    assert r.status_code == 200, r.text
    (ctx, event, payload), _kw = audit.call_args
    assert ctx.user_id == user
    assert event == "auth.signed_out_everywhere"
    assert payload["revoked_before"] == r.json()["revoked_before"]


@pytest.mark.parametrize("iat", [None, "1000", True, 0, -5], ids=["none", "text", "bool", "zero", "negative"])
def test_a_signed_token_without_a_usable_iat_is_refused(memory_db, clean_map, iat) -> None:
    """It could never be revoked: a sign-out would "succeed" while it went on working.

    Verification refuses it as it refuses any invalid token (401), before the
    revocation check, so no token that reaches the service escapes a cutoff.
    """
    user = _user("taxpayer")
    r = TestClient(main_mod.app).get(
        "/v1/me/profile", headers=_bearer(_signed(user, "verified_taxpayer", iat=iat))
    )
    assert r.status_code == 401, r.text
    assert "iat" in r.json()["detail"]


@pytest.mark.parametrize("iat", [None, "1000", True, 0], ids=["none", "text", "bool", "zero"])
def test_the_verifier_refuses_a_token_without_a_usable_iat(iat) -> None:
    from app.auth import JWTAuthError, JWTVerifier
    from app.auth.dependencies import _get_verifier
    from app.auth.jwt_auth import _hs256_verify

    configured = _get_verifier()
    verifier = JWTVerifier(
        alg="HS256",
        dev_secret=configured.dev_secret,
        issuer=configured.issuer,
        audience=configured.audience,
    )
    token = _signed("okello", iat=iat)
    with pytest.raises(JWTAuthError, match="iat"):
        verifier.verify(token)
    # The development fallback (an HS256 token under an RS256 verifier) too.
    with pytest.raises(JWTAuthError, match="iat"):
        _hs256_verify(token, configured.dev_secret)
    # A token with one verifies.
    assert verifier.verify(_signed("okello", iat=int(time.time())))["sub"] == "okello"


def test_a_sign_out_the_store_cannot_keep_changes_nothing(memory_db, clean_map) -> None:
    user = _user("taxpayer")
    token = _signed(user, "verified_taxpayer", iat=int(time.time()))
    client = TestClient(main_mod.app, raise_server_exceptions=False)
    with mock.patch.object(main_mod.db, "revoke_sessions", side_effect=RuntimeError("disk full")):
        r = client.post("/v1/me/logout", headers=_bearer(token))
    assert r.status_code >= 500
    assert clean_map.revoked_before(user) is None


def test_only_an_administrator_revokes_someone_else(memory_db, clean_map) -> None:
    target = _user("officer")
    target_token = _signed(target, "ura_staff", iat=int(time.time()))
    staff = make_dev_token(_user("staff"), role="ura_staff")
    admin_id = _user("admin")
    admin = make_dev_token(admin_id, role="ura_admin")
    client = TestClient(main_mod.app)

    r = client.post(
        f"/v1/admin/users/{target}/revoke", headers=_bearer(staff), json={"reason": "x"}
    )
    assert r.status_code == 403
    assert target not in memory_db.load_session_revocations()

    with mock.patch.object(main_mod, "_audit_staff_action") as audit:
        r = client.post(
            f"/v1/admin/users/{target}/revoke",
            headers=_bearer(admin),
            json={"reason": "laptop stolen"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == target
    assert memory_db.load_session_revocations()[target] == r.json()["revoked_before"]
    (ctx, event, payload), _kw = audit.call_args
    assert (ctx.user_id, event, payload["subject"]) == (admin_id, "staff.session_revoked", target)

    refused = client.get("/v1/me/profile", headers=_bearer(target_token))
    assert (refused.status_code, refused.json()["detail"]) == (401, "session revoked")


def test_an_administrator_may_revoke_without_a_reason(memory_db, clean_map) -> None:
    target = _user("officer")
    admin = make_dev_token(_user("admin"), role="ura_admin")
    r = TestClient(main_mod.app).post(f"/v1/admin/users/{target}/revoke", headers=_bearer(admin))
    assert r.status_code == 200, r.text
    assert clean_map.revoked_before(target) == r.json()["revoked_before"]


# ---------------------------------------------------------------------------
# Sockets: sign-in
# ---------------------------------------------------------------------------


class _Socket:
    """A browser socket: scripted inbound text frames, recorded outbound ones."""

    def __init__(self, inbound: list[str], query: dict | None = None) -> None:
        self._inbound = list(inbound)
        self.sent: list = []
        self.closed: int | None = None
        self.headers = {"origin": "http://localhost:3300"}
        self.query_params = query or {}
        self.client = SimpleNamespace(host="127.0.0.1")

    async def accept(self) -> None:
        return None

    async def receive_text(self) -> str:
        if self._inbound:
            return self._inbound.pop(0)
        await asyncio.sleep(3600)
        return ""

    async def receive(self) -> dict:
        return {"type": "websocket.receive", "text": await self.receive_text()}

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        if self.closed is None:
            self.closed = code

    def frames(self, kind: str) -> list[dict]:
        return [m for m in self.sent if isinstance(m, dict) and m.get("type") == kind]


class _FeedSocket(_Socket):
    """A socket whose frames after sign-in are fed by the test, binary ones included."""

    def __init__(self, inbound: list[str], query: dict | None = None) -> None:
        super().__init__(inbound, query)
        self._fed: asyncio.Queue[dict] = asyncio.Queue()

    def feed(self, message: dict) -> None:
        self._fed.put_nowait(message)

    async def receive(self) -> dict:
        if self._inbound:
            return {"type": "websocket.receive", "text": self._inbound.pop(0)}
        return await self._fed.get()


def _authenticate(token: str) -> str:
    return json.dumps({"type": "authenticate", "access_token": token})


async def _until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


async def _stop(task: asyncio.Task) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


class SocketSignInTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app import ws_concurrency

        self.revocation = _revocation()
        self.revocation.reset()
        self.addCleanup(self.revocation.reset)
        ws_concurrency.reset()
        self.addCleanup(ws_concurrency.reset)

    def test_the_session_a_socket_signs_in_with_carries_its_issue_time(self) -> None:
        from app.chat_ws_v2 import resolve_ws_session

        iat = int(time.time())
        socket = _Socket([])
        user_id, _tenant, role, expiry, issued_at = resolve_ws_session(
            socket, required=True, token_override=_signed("okello", "ura_staff", iat=iat)
        )
        self.assertEqual((user_id, role, issued_at), ("okello", "ura_staff", float(iat)))
        self.assertIsNotNone(expiry)

    def test_a_revoked_token_does_not_sign_a_socket_in(self) -> None:
        from app.auth import JWTAuthError
        from app.chat_ws_v2 import _resolve_ws_principal, resolve_ws_session

        iat = int(time.time())
        token = _signed("okello", "ura_staff", iat=iat)
        self.revocation.record("okello", iat + 0.5)
        for resolve in (resolve_ws_session, _resolve_ws_principal):
            with pytest.raises(JWTAuthError, match="session revoked"):
                resolve(_Socket([]), required=True, token_override=token)

    def test_a_token_without_iat_does_not_sign_a_socket_in(self) -> None:
        from app.auth import JWTAuthError
        from app.chat_ws_v2 import _resolve_ws_principal, resolve_ws_session

        token = _signed("okello", "ura_staff", iat=None)
        for resolve in (resolve_ws_session, _resolve_ws_principal):
            with pytest.raises(JWTAuthError, match="iat"):
                resolve(_Socket([]), required=True, token_override=token)

    async def test_a_token_without_iat_cannot_open_the_lobby(self) -> None:
        socket = _Socket([_authenticate(_signed(_user("okello"), "ura_staff", iat=None))])
        with patch.object(ws, "_voice_receptionist_enabled", return_value=True):
            task = asyncio.create_task(ws.staff_calls_stream_endpoint(socket, call_id=None))
            await _until(lambda: socket.closed is not None or task.done(), timeout=1.0)
            await _stop(task)

        self.assertEqual(socket.closed, 4401)
        self.assertEqual(socket.frames("ready"), [])

    async def test_a_revoked_token_cannot_open_the_lobby(self) -> None:
        user = _user("okello")
        iat = int(time.time())
        self.revocation.record(user, iat + 0.5)
        socket = _Socket([_authenticate(_signed(user, "ura_staff", iat=iat))])
        with patch.object(ws, "_voice_receptionist_enabled", return_value=True):
            task = asyncio.create_task(ws.staff_calls_stream_endpoint(socket, call_id=None))
            await _until(lambda: socket.closed is not None or task.done(), timeout=1.0)
            await _stop(task)

        self.assertEqual(socket.closed, 4401)
        self.assertEqual(socket.frames("ready"), [])


@pytest.mark.parametrize("module_name", ["app.voice_ws", "app.voice_ws_v2"])
def test_a_revoked_token_does_not_open_a_voice_socket(module_name, clean_map) -> None:
    import importlib

    from app.auth import JWTAuthError

    module = importlib.import_module(module_name)
    user, iat = _user("taxpayer"), int(time.time())
    socket = SimpleNamespace(
        headers={"authorization": f"Bearer {_signed(user, iat=iat)}"}, query_params={}
    )
    assert module._resolve_ws_auth(socket, required=True)[0] == user

    clean_map.record(user, iat + 0.5)
    with pytest.raises(JWTAuthError, match="session revoked"):
        module._resolve_ws_auth(socket, required=True)


@pytest.mark.parametrize("module_name", ["app.voice_ws", "app.voice_ws_v2"])
def test_a_token_without_iat_does_not_open_a_voice_socket(module_name, clean_map) -> None:
    import importlib

    from app.auth import JWTAuthError

    module = importlib.import_module(module_name)
    socket = SimpleNamespace(
        headers={"authorization": f"Bearer {_signed(_user('taxpayer'), iat=None)}"}, query_params={}
    )
    with pytest.raises(JWTAuthError, match="iat"):
        module._resolve_ws_auth(socket, required=True)


# ---------------------------------------------------------------------------
# Sockets already open
# ---------------------------------------------------------------------------


class LobbyRevocationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app import ws_concurrency

        self.revocation = _revocation()
        self.revocation.reset()
        self.addCleanup(self.revocation.reset)
        ws_concurrency.reset()
        self.addCleanup(ws_concurrency.reset)

    def test_every_staff_loop_checks_the_session_at_least_every_five_seconds(self) -> None:
        self.assertLessEqual(ws._SESSION_POLL_S, 5.0)

    async def test_an_open_lobby_closes_when_the_session_is_revoked(self) -> None:
        user = _user("okello")
        iat = int(time.time())
        socket = _Socket([_authenticate(_signed(user, "ura_staff", iat=iat, ttl=3600))])
        with (
            patch.object(ws, "_voice_receptionist_enabled", return_value=True),
            patch.object(ws, "_SESSION_POLL_S", 0.05, create=True),
        ):
            task = asyncio.create_task(ws.staff_calls_stream_endpoint(socket, call_id=None))
            self.assertTrue(await _until(lambda: socket.frames("ready")), "the lobby never opened")
            self.revocation.record(user, iat + 0.5)
            done, _pending = await asyncio.wait({task}, timeout=2.0)
            if not done:
                await _stop(task)

        self.assertTrue(done, "the lobby outlived the revoked session")
        self.assertEqual(socket.closed, 4401)

    async def test_another_persons_revocation_leaves_the_lobby_open(self) -> None:
        user = _user("okello")
        iat = int(time.time())
        socket = _Socket([_authenticate(_signed(user, "ura_staff", iat=iat, ttl=3600))])
        with (
            patch.object(ws, "_voice_receptionist_enabled", return_value=True),
            patch.object(ws, "_SESSION_POLL_S", 0.05, create=True),
        ):
            task = asyncio.create_task(ws.staff_calls_stream_endpoint(socket, call_id=None))
            self.assertTrue(await _until(lambda: socket.frames("ready")))
            self.revocation.record(_user("nakato"), iat + 0.5)
            await asyncio.sleep(0.3)
            still_open = not task.done()
            await _stop(task)

        self.assertTrue(still_open)
        self.assertIsNone(socket.closed)


class _FakeVoiceSession:
    """Stands in for the voice engine: the tests below only need the socket's loop."""

    def __init__(self, **kwargs) -> None:
        self.session_id = kwargs.get("session_id", "")
        self.user_id = kwargs.get("user_id", "")
        self._turn_count = 0

    def close(self) -> None:
        return None


class OpenChatAndVoiceSocketTests(unittest.IsolatedAsyncioTestCase):
    """The chat and voice sockets checked a session only at sign-in (review of ADR-0053).

    A signed-in chat or voice socket now re-checks it at least every
    ``revocation.SOCKET_CHECK_INTERVAL_S`` while it waits for a frame, and on
    every frame, and closes 4401 once it is revoked. An anonymous one has no
    session and is not affected.
    """

    async def asyncSetUp(self) -> None:
        from app import chat_ws_v2, ws_concurrency

        self.revocation = _revocation()
        self.revocation.reset()
        self.addCleanup(self.revocation.reset)
        ws_concurrency.reset()
        self.addCleanup(ws_concurrency.reset)
        with chat_ws_v2._active_per_user_lock:
            chat_ws_v2._active_per_user.clear()
        poll = patch.object(self.revocation, "SOCKET_CHECK_INTERVAL_S", 0.05, create=True)
        poll.start()
        self.addCleanup(poll.stop)

    def socket(self, token: str = "") -> _Socket:
        socket = _Socket([json.dumps({"type": "session_start", "locale": "en", "language": "en"})])
        if token:
            socket.headers["authorization"] = f"Bearer {token}"
        return socket

    async def open(self, handler, socket: _Socket, flag: str, **patches) -> asyncio.Task:
        app = SimpleNamespace(state=SimpleNamespace(model=object(), speech=object()))
        module = sys.modules[handler.__module__]
        for patcher in (
            patch.object(module.flags, "is_enabled", side_effect=lambda name: name == flag),
            *(patch.object(module, name, value) for name, value in patches.items()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        task = asyncio.create_task(handler(socket, app))
        self.addAsyncCleanup(_stop, task)
        self.assertTrue(
            await _until(lambda: socket.frames("session_ready") or task.done()), "the socket never opened"
        )
        self.assertFalse(task.done(), f"the socket closed at once: {socket.sent}")
        return task

    async def assert_closed_on_revocation(self, handler, flag: str, **patches) -> None:
        user, iat = _user("taxpayer"), int(time.time())
        socket = self.socket(_signed(user, "verified_taxpayer", iat=iat))
        task = await self.open(handler, socket, flag, **patches)

        self.revocation.record(user, iat + 0.5)
        done, _pending = await asyncio.wait({task}, timeout=2.0)

        self.assertTrue(done, "the socket outlived the revoked session")
        self.assertEqual(socket.closed, 4401)

    async def test_an_open_chat_socket_closes_when_the_session_is_revoked(self) -> None:
        from app.chat_ws_v2 import chat_stream_ws

        await self.assert_closed_on_revocation(chat_stream_ws, "ws_chat")

    async def test_an_open_voice_socket_closes_when_the_session_is_revoked(self) -> None:
        from app.voice_ws import voice_stream_ws

        await self.assert_closed_on_revocation(
            voice_stream_ws, "voice_streaming", VoiceSession=_FakeVoiceSession
        )

    async def test_an_open_v2_voice_socket_closes_when_the_session_is_revoked(self) -> None:
        from app.voice_ws_v2 import voice_stream_ws_v2

        await self.assert_closed_on_revocation(
            voice_stream_ws_v2, "native_voice", VoiceSessionV2=_FakeVoiceSession
        )

    async def test_a_chat_socket_whose_session_stands_stays_open(self) -> None:
        from app.chat_ws_v2 import chat_stream_ws

        user, iat = _user("taxpayer"), int(time.time())
        socket = self.socket(_signed(user, "verified_taxpayer", iat=iat))
        task = await self.open(chat_stream_ws, socket, "ws_chat")

        self.revocation.record(_user("nakato"), iat + 0.5)
        await asyncio.sleep(0.3)

        self.assertFalse(task.done())
        self.assertIsNone(socket.closed)

    async def test_an_anonymous_chat_socket_is_not_affected(self) -> None:
        from app.chat_ws_v2 import chat_stream_ws

        socket = self.socket()
        task = await self.open(chat_stream_ws, socket, "ws_chat")

        self.revocation.record(_user("taxpayer"), time.time() + 1)
        await asyncio.sleep(0.3)

        self.assertFalse(task.done())
        self.assertIsNone(socket.closed)


class _CallCase(unittest.IsolatedAsyncioTestCase):
    """A call waiting in the officers' queue, on the real store."""

    async def asyncSetUp(self) -> None:
        from app import ws_concurrency

        db.init_db()
        init_receptionist_schema()
        ws_concurrency.reset()
        self.addCleanup(ws_concurrency.reset)
        self.revocation = _revocation()
        self.revocation.reset()
        self.addCleanup(self.revocation.reset)
        self.call_id = f"call_rev_{uuid.uuid4().hex[:8]}"
        create_call(
            self.call_id,
            conversation_id=f"conv_{self.call_id}",
            status="transferring",
            tenant_id="default",
        )
        self.caller = _Socket([])
        self.room = await registry.create(
            self.call_id, f"conv_{self.call_id}", tenant_id="default", caller_ws=self.caller
        )
        self.room.state.mode = "transferring"
        self.iat = float(int(time.time()))
        for patcher in (
            patch.object(hub, "publish_lobby"),
            patch.object(ws, "_voice_receptionist_enabled", return_value=True),
            patch.object(ws, "_SESSION_POLL_S", 0.05, create=True),
        ):
            patcher.start()
        self.addCleanup(patch.stopall)

    async def asyncTearDown(self) -> None:
        desk._cancel_claim_timer(self.room)
        desk._cancel_reconnect_timer(self.room)
        desk._cancel_reservation(self.room)
        await registry.remove(self.call_id)

    def staff(self, user: str, role: str = "ura_staff"):
        """Signs a fake socket in as *user*, with a token issued at ``self.iat`` and no expiry."""
        session = patch.object(
            ws, "_staff_session", return_value=(user, "default", role, None, self.iat)
        )
        session.start()
        self.addCleanup(session.stop)

    def revoke(self, user: str) -> None:
        self.revocation.record(user, self.iat + 0.5)


class OfficerAudioRevocationTests(_CallCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        patch.object(ws.livekit, "enabled", return_value=False).start()

    async def test_an_officers_audio_socket_closes_and_the_call_goes_into_the_grace(self) -> None:
        await desk.claim(self.call_id, "okello")
        socket = _Socket([_authenticate("t")])
        self.staff("okello")
        task = asyncio.create_task(ws.officer_audio_endpoint(socket, self.call_id))
        self.addAsyncCleanup(_stop, task)
        self.assertTrue(
            await _until(lambda: self.room.state.mode == "bridged"),
            "the officer never joined the call",
        )

        self.revoke("okello")
        done, _pending = await asyncio.wait({task}, timeout=2.0)

        self.assertTrue(done, "the officer's audio socket outlived the revoked session")
        self.assertEqual(socket.closed, 4401)
        # The disconnect path: the caller is kept while the officer signs in again.
        self.assertEqual(
            (self.room.state.mode, self.room.state.reconnecting_officer), ("transferring", "okello")
        )
        self.assertIsNone(self.room.officer)

    async def test_a_send_to_the_caller_that_never_returns_does_not_keep_a_revoked_officer_on(self) -> None:
        """The session was checked only between frames; a frame stuck on its way to the caller stopped it."""
        await desk.claim(self.call_id, "okello")
        socket = _FeedSocket([_authenticate("t")])
        self.staff("okello")
        task = asyncio.create_task(ws.officer_audio_endpoint(socket, self.call_id))
        self.addAsyncCleanup(_stop, task)
        self.assertTrue(
            await _until(lambda: self.room.state.mode == "bridged"), "the officer never joined the call"
        )
        stuck = asyncio.Event()

        async def never_returns(_data: bytes) -> None:
            stuck.set()
            await asyncio.Event().wait()  # a caller who stopped reading

        self.caller.send_bytes = never_returns
        socket.feed({"type": "websocket.receive", "bytes": b"\x00\x00" * 320})
        self.assertTrue(await _until(stuck.is_set), "the officer's audio never reached the caller")

        self.revoke("okello")
        done, _pending = await asyncio.wait({task}, timeout=2.0)

        self.assertTrue(done, "a blocked send kept the revoked officer's audio socket open")
        self.assertEqual(socket.closed, 4401)
        self.assertIsNone(self.room.officer)
        self.assertEqual(
            (self.room.state.mode, self.room.state.reconnecting_officer), ("transferring", "okello")
        )

    async def test_a_hangup_under_way_is_not_cut_short_when_the_watch_closes_the_socket(self) -> None:
        """The watch cancels the officer's loop; the call's end that loop started must still finish."""
        await desk.claim(self.call_id, "okello")
        socket = _FeedSocket([_authenticate("t")])
        self.staff("okello")
        task = asyncio.create_task(ws.officer_audio_endpoint(socket, self.call_id))
        self.addAsyncCleanup(_stop, task)
        self.assertTrue(
            await _until(lambda: self.room.state.mode == "bridged"), "the officer never joined the call"
        )
        started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def slow_end(_reason: str) -> None:
            started.set()
            await release.wait()
            finished.set()

        with patch.object(self.room, "end", slow_end):
            socket.feed({"type": "websocket.receive", "text": json.dumps({"type": "hangup"})})
            self.assertTrue(await _until(started.is_set), "the hangup never reached the call")
            self.revoke("okello")
            done, _pending = await asyncio.wait({task}, timeout=2.0)
            release.set()
            ended = await _until(finished.is_set, timeout=1.0)

        self.assertTrue(done, "the revoked officer's socket stayed open during the hangup")
        self.assertEqual(socket.closed, 4401)
        self.assertTrue(ended, "the call's end was cut short")

    async def test_a_listener_whose_socket_stopped_reading_is_cut_off_when_revoked(self) -> None:
        await desk.claim(self.call_id, "okello")
        await desk.bridge(self.room, None, "okello", speech_model=None)
        socket = _FeedSocket([_authenticate("t")], query={"listen": "true"})
        stuck = asyncio.Event()

        async def never_returns(_data: bytes) -> None:
            stuck.set()
            await asyncio.Event().wait()

        socket.send_bytes = never_returns
        self.staff("auditor1", "ura_auditor")
        task = asyncio.create_task(ws.officer_audio_endpoint(socket, self.call_id))
        self.addAsyncCleanup(_stop, task)
        self.assertTrue(await _until(lambda: bool(self.room.listeners)), "the listener never joined")
        self.room.broadcast_audio(b"\x00\x00" * 320)
        self.assertTrue(await _until(stuck.is_set), "the call's audio never reached the listener")

        self.revoke("auditor1")
        done, _pending = await asyncio.wait({task}, timeout=2.0)

        self.assertTrue(done, "a blocked send kept the revoked listener on the call")
        self.assertEqual(socket.closed, 4401)
        self.assertEqual(self.room.listeners, [])

    async def test_a_listener_is_cut_off_when_their_session_is_revoked(self) -> None:
        await desk.claim(self.call_id, "okello")
        await desk.bridge(self.room, None, "okello", speech_model=None)
        socket = _Socket([_authenticate("t")], query={"listen": "true"})
        self.staff("auditor1", "ura_auditor")
        task = asyncio.create_task(ws.officer_audio_endpoint(socket, self.call_id))
        self.addAsyncCleanup(_stop, task)
        self.assertTrue(
            await _until(lambda: bool(self.room.listeners)), "the listener never joined"
        )

        self.revoke("auditor1")
        done, _pending = await asyncio.wait({task}, timeout=2.0)

        self.assertTrue(done, "the listen socket outlived the revoked session")
        self.assertEqual(socket.closed, 4401)
        self.assertEqual(self.room.listeners, [])
        self.assertEqual(self.room.state.mode, "bridged")  # the call itself goes on


class LiveKitOfficerRevocationTests(_CallCase):
    async def asyncSetUp(self) -> None:
        import os

        await super().asyncSetUp()
        self.room.state.livekit_room = f"ura-{self.call_id}"
        self.revoke_participant = AsyncMock()
        for patcher in (
            patch.dict(os.environ, {"LIVEKIT_URL": "wss://rtc.example.test"}),
            patch.object(hub, "publish_call"),
            patch.object(ws.livekit, "enabled", return_value=True),
            patch.object(ws.livekit, "mint_token", return_value="room-jwt"),
            patch.object(ws.livekit, "close_room", AsyncMock()),
            patch.object(ws.livekit, "revoke_participant", self.revoke_participant),
            patch.object(self.room, "wait_for_participant", AsyncMock(return_value=True)),
        ):
            patcher.start()

    async def test_the_officers_media_is_revoked_at_once(self) -> None:
        await desk.claim(self.call_id, "okello")
        socket = _Socket([_authenticate("t")])
        self.staff("okello")
        task = asyncio.create_task(ws.officer_audio_endpoint(socket, self.call_id))
        self.addAsyncCleanup(_stop, task)
        self.assertTrue(
            await _until(lambda: self.room.state.mode == "bridged"), "the officer never joined"
        )
        identity = livekit.participant_identity("officer", "okello")
        self.assertEqual(
            (self.room.state.livekit_officer_user, self.room.state.livekit_officer_issued_at),
            ("okello", self.iat),
        )

        self.revoke("okello")
        done, _pending = await asyncio.wait({task}, timeout=2.0)

        self.assertTrue(done, "the officer's control socket outlived the revoked session")
        self.assertEqual(socket.closed, 4401)
        # Not left in the room until the reconnect grace ends.
        self.revoke_participant.assert_any_await(self.room.state.livekit_room, identity)
        self.assertEqual(
            (self.room.state.mode, self.room.state.reconnecting_officer), ("transferring", "okello")
        )

    async def test_an_expired_session_keeps_its_media_for_the_grace(self) -> None:
        """Expiry is not revocation: the officer may sign in again and carry on."""
        await desk.claim(self.call_id, "okello")
        socket = _Socket([_authenticate("t")])
        session = patch.object(
            ws,
            "_staff_session",
            return_value=("okello", "default", "ura_staff", time.time() + 0.3, self.iat),
        )
        session.start()
        self.addCleanup(session.stop)
        task = asyncio.create_task(ws.officer_audio_endpoint(socket, self.call_id))
        self.addAsyncCleanup(_stop, task)
        done, _pending = await asyncio.wait({task}, timeout=3.0)

        self.assertTrue(done)
        self.assertEqual(socket.closed, 4401)
        self.revoke_participant.assert_not_awaited()


# ---------------------------------------------------------------------------
# The caller's socket, and the officer's media rejoining
# ---------------------------------------------------------------------------


class _LiveKitCallerCase(unittest.IsolatedAsyncioTestCase):
    """A LiveKit call through the real endpoint, with a stand-in Pipecat runner and transport."""

    async def asyncSetUp(self) -> None:
        import os

        from app import ws_concurrency

        db.init_db()
        init_receptionist_schema()
        ws_concurrency.reset()
        self.addCleanup(ws_concurrency.reset)
        self.revocation = _revocation()
        self.revocation.reset()
        self.addCleanup(self.revocation.reset)
        seen = self.seen = SimpleNamespace(cancelled=asyncio.Event())
        handlers = self.handlers = {}

        class FakeTask:
            stop: asyncio.Event | None = None

            async def cancel(self) -> None:
                seen.cancelled.set()
                if self.stop is not None:
                    self.stop.set()

            async def queue_frame(self, frame) -> None:
                return None

        class FakeRunner:
            async def run(self, task: FakeTask) -> None:
                task.stop = asyncio.Event()
                await task.stop.wait()

        class FakeTransport:
            def event_handler(self, name):
                def register(fn):
                    handlers[name] = fn
                    return fn

                return register

            def get_participants(self):
                return []

        self.transport = FakeTransport()
        self.revoke_participant = AsyncMock()
        brain = SimpleNamespace(say_greeting=AsyncMock())
        for patcher in (
            patch.dict(
                sys.modules, {"pipecat.pipeline.runner": SimpleNamespace(PipelineRunner=FakeRunner)}
            ),
            patch.dict(os.environ, {"LIVEKIT_URL": "wss://rtc.example.test"}),
            patch.object(hub, "publish_lobby"),
            patch.object(hub, "publish_call"),
            patch.object(ws, "_voice_receptionist_enabled", return_value=True),
            patch.object(ws, "_SESSION_POLL_S", 0.05, create=True),
            patch.object(ws.livekit, "enabled", return_value=True),
            patch.object(ws.livekit, "mint_token", return_value="room-jwt"),
            patch.object(ws.livekit, "close_room", AsyncMock()),
            patch.object(ws.livekit, "revoke_participant", self.revoke_participant),
            patch.object(
                ws, "build_call_pipeline", return_value=(FakeTask(), self.transport, brain)
            ),
            patch.object(ws.call_brief, "start"),
            patch.object(ws.call_brief, "stop"),
            patch.object(ws.risk, "start"),
            patch.object(ws.risk, "stop"),
            patch.object(ws, "generate_call_summary"),
        ):
            patcher.start()
        self.addCleanup(patch.stopall)

    async def start_call(self, token: str = "") -> tuple[asyncio.Task, _Socket, object]:
        start = {"type": "call_start", "locale": "en", "voice_consent_accepted": True}
        if token:
            start["access_token"] = token
        caller = _Socket([json.dumps(start)])
        task = asyncio.create_task(ws.call_stream_endpoint(caller))
        self.addAsyncCleanup(_stop, task)
        await _until(
            lambda: caller.frames("call_ready") or caller.closed is not None or task.done()
        )
        ready = caller.frames("call_ready")
        room = registry.get(ready[0]["call_id"]) if ready else None
        return task, caller, room


class CallerRevocationTests(_LiveKitCallerCase):
    async def test_a_signed_in_callers_call_ends_when_their_session_is_revoked(self) -> None:
        user = _user("taxpayer")
        iat = int(time.time())
        task, caller, room = await self.start_call(_signed(user, "verified_taxpayer", iat=iat))
        self.assertIsNotNone(room, "the call never started")

        self.revocation.record(user, iat + 0.5)
        done, _pending = await asyncio.wait({task}, timeout=3.0)

        self.assertTrue(done, "the caller's call outlived the revoked session")
        self.assertEqual(caller.closed, 4401)
        self.assertEqual([e.get("code") for e in caller.frames("error")], ["signin"])
        self.assertEqual(room.state.end_reason, "session_revoked")
        self.assertTrue(self.seen.cancelled.is_set(), "the media pipeline outlived the call")

    async def test_an_anonymous_caller_is_not_affected(self) -> None:
        task, caller, room = await self.start_call()
        self.assertIsNotNone(room)

        self.revocation.record(_user("taxpayer"), time.time() + 1)
        await asyncio.sleep(0.3)

        self.assertFalse(task.done())
        self.assertIsNone(caller.closed)
        await room.end("caller_hangup")
        await asyncio.wait({task}, timeout=3.0)

    async def test_a_revoked_token_cannot_start_a_call(self) -> None:
        user = _user("taxpayer")
        iat = int(time.time())
        self.revocation.record(user, iat + 0.5)
        task, caller, room = await self.start_call(_signed(user, "verified_taxpayer", iat=iat))
        await asyncio.wait({task}, timeout=3.0)

        self.assertIsNone(room)
        self.assertEqual(caller.closed, 4401)
        self.assertEqual([e.get("code") for e in caller.frames("error")], ["signin"])


class OfficerMediaRejoinTests(_LiveKitCallerCase):
    """The officer's media came back in the reconnect grace: it is bridged again only for a live session."""

    async def waiting_for(self, room, officer: str) -> str:
        identity = livekit.participant_identity("officer", officer)
        room.state.mode = "transferring"
        room.state.claimed_by = officer
        room.state.reconnecting_officer = officer
        room.state.livekit_officer_identity = identity
        room.state.livekit_officer_user = officer
        room.state.livekit_officer_issued_at = float(int(time.time()))
        return identity

    async def test_a_revoked_officers_media_is_refused_when_it_rejoins(self) -> None:
        task, _caller, room = await self.start_call()
        self.assertIn("on_participant_connected", self.handlers)
        identity = await self.waiting_for(room, "okello")
        self.revocation.record("okello", room.state.livekit_officer_issued_at + 0.5)

        with patch.object(ws.desk, "bridge", AsyncMock()) as bridge:
            await self.handlers["on_participant_connected"](self.transport, identity)

        bridge.assert_not_awaited()
        self.revoke_participant.assert_any_await(room.state.livekit_room, identity)
        self.assertEqual(room.state.mode, "transferring")
        await room.end("caller_hangup")
        await asyncio.wait({task}, timeout=3.0)

    async def test_an_officer_whose_session_stands_is_bridged_again(self) -> None:
        task, _caller, room = await self.start_call()
        identity = await self.waiting_for(room, "okello")
        self.revocation.record("nakato", time.time() + 1)

        with patch.object(ws.desk, "bridge", AsyncMock()) as bridge:
            await self.handlers["on_participant_connected"](self.transport, identity)

        bridge.assert_awaited_once()
        self.revoke_participant.assert_not_awaited()
        await room.end("caller_hangup")
        await asyncio.wait({task}, timeout=3.0)

"""A flag changed through one API process reaches every process (G612, ADR-0044).

The Space runs a chat process and a call process (ADR-0043). A console change
(``PATCH``/``DELETE /v1/admin/flags/{name}``) used to reach only the process
that answered it; the other read the stored row at its next start. So the
calls kill switch, switched off from the console, could leave the call process
taking calls. Every process now re-reads ``flag_overrides`` on an interval and
makes the overrides that came from it match.

Two ``FeatureFlags`` registries sharing one database stand in for the two
processes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
import time
from unittest import mock

import pytest
from app import database as db
from app import main as main_mod
from app.analytics import metrics
from app.auth import make_dev_token
from app.flags import FeatureFlags, flags
from fastapi.testclient import TestClient

FLAG = "hyde"


def _off_default() -> bool:
    """The value that makes an override of ``FLAG`` visible: not its default."""
    return not flags.describe(FLAG)["default"]


def _counter(name: str) -> float:
    return float(metrics.snapshot()["counters"].get(name, 0))


@pytest.fixture
def shared_db(monkeypatch: pytest.MonkeyPatch):
    """One store both "processes" read, as the Space's two processes share one."""
    conn = sqlite3.connect(":memory:", timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(db, "_get_connection", lambda: conn)
    db.init_db()
    yield db
    conn.close()


@pytest.fixture
def clean_flags():
    """The process-wide registry and sync state, as each test found them."""
    state = dict(main_mod._flag_sync_state)
    yield flags
    for name in (FLAG, "voice_receptionist", "auth_required"):
        flags.clear(name)
    main_mod._flag_sync_state.clear()
    main_mod._flag_sync_state.update(state)


# ---------------------------------------------------------------------------
# The registry: overrides that came from the store follow the store
# ---------------------------------------------------------------------------


def test_a_stored_override_reaches_a_process_that_did_not_write_it(shared_db) -> None:
    writer, other = FeatureFlags(), FeatureFlags()
    value = _off_default()

    shared_db.save_flag_override(FLAG, value)
    writer.set(FLAG, value, stored=True)

    assert other.apply_stored(shared_db.load_flag_overrides()) == {FLAG: value}
    assert other.is_enabled(FLAG) is value
    assert other.describe(FLAG)["stored"] is True


def test_a_deleted_override_is_cleared_in_every_process(shared_db) -> None:
    writer, other = FeatureFlags(), FeatureFlags()
    value = _off_default()
    shared_db.save_flag_override(FLAG, value)
    writer.set(FLAG, value, stored=True)
    other.apply_stored(shared_db.load_flag_overrides())

    shared_db.clear_flag_override(FLAG)
    writer.clear(FLAG)

    assert other.apply_stored(shared_db.load_flag_overrides()) == {FLAG: None}
    assert other.describe(FLAG)["overridden"] is False
    assert other.describe(FLAG)["stored"] is False


def test_an_override_set_only_in_this_process_is_left_alone() -> None:
    registry = FeatureFlags()
    value = _off_default()
    registry.set(FLAG, value)

    assert registry.apply_stored({}) == {}
    assert registry.is_enabled(FLAG) is value
    assert registry.describe(FLAG)["stored"] is False


def test_a_stored_row_beats_a_process_local_override_and_then_follows_the_store() -> None:
    registry = FeatureFlags()
    value = _off_default()
    registry.set(FLAG, not value)

    assert registry.apply_stored({FLAG: value}) == {FLAG: value}
    assert registry.is_enabled(FLAG) is value
    # Now that it came from the store, the row's removal clears it.
    assert registry.apply_stored({}) == {FLAG: None}
    assert registry.describe(FLAG)["overridden"] is False


def test_an_unchanged_store_changes_nothing_and_unknown_names_are_skipped() -> None:
    registry = FeatureFlags()
    value = _off_default()
    registry.apply_stored({FLAG: value})

    assert registry.apply_stored({FLAG: value, "no_such_flag": True}) == {}
    assert registry.is_enabled(FLAG) is value


# ---------------------------------------------------------------------------
# One sync pass in this process
# ---------------------------------------------------------------------------


def test_one_sync_pass_applies_the_store_to_this_process(clean_flags) -> None:
    value = _off_default()
    before = time.time()
    with mock.patch.object(main_mod.db, "load_flag_overrides", return_value={FLAG: value}):
        main_mod._sync_flag_overrides()

    assert flags.is_enabled(FLAG) is value
    assert main_mod._flag_sync_state["synced_at"] >= before


def test_calls_switched_off_in_the_store_read_off_on_the_call_process(clean_flags) -> None:
    """The kill switch: the console's change reaches the process that takes calls."""
    from app import receptionist

    with mock.patch.object(
        main_mod.db, "load_flag_overrides", return_value={"voice_receptionist": False}
    ):
        main_mod._sync_flag_overrides()

    assert receptionist.status() == "off"


def test_an_unreadable_store_keeps_the_flags_logs_once_and_recovers(clean_flags, caplog) -> None:
    value = _off_default()
    with mock.patch.object(main_mod.db, "load_flag_overrides", return_value={FLAG: value}):
        main_mod._sync_flag_overrides()
    failures = _counter("flag_sync_failures_total")
    caplog.set_level(logging.INFO, logger=main_mod.__name__)

    with mock.patch.object(
        main_mod.db, "load_flag_overrides", side_effect=RuntimeError("database is locked")
    ):
        main_mod._sync_flag_overrides()
        main_mod._sync_flag_overrides()

    assert flags.is_enabled(FLAG) is value
    assert _counter("flag_sync_failures_total") - failures == 2
    unreadable = [r for r in caplog.records if "could not be read" in r.getMessage()]
    assert len(unreadable) == 1
    assert unreadable[0].levelno == logging.ERROR

    with mock.patch.object(main_mod.db, "load_flag_overrides", return_value={}):
        main_mod._sync_flag_overrides()

    assert flags.describe(FLAG)["overridden"] is False
    assert any("readable again" in r.getMessage() for r in caplog.records)


def test_production_refuses_a_stored_off_for_a_protected_control_without_exiting(
    clean_flags, caplog
) -> None:
    """Startup refuses to start on such a row; a running process keeps the control on."""
    refused = _counter("flag_sync_refused_total")
    caplog.set_level(logging.INFO, logger=main_mod.__name__)
    with (
        mock.patch.dict(os.environ, {"APP_ENV": "production"}),
        mock.patch.object(
            main_mod.db, "load_flag_overrides", return_value={"auth_required": False}
        ),
    ):
        main_mod._sync_flag_overrides()
        main_mod._sync_flag_overrides()
        assert flags.is_enabled("auth_required") is True

    assert _counter("flag_sync_refused_total") - refused == 2
    critical = [
        r
        for r in caplog.records
        if r.levelno == logging.CRITICAL and "auth_required" in r.getMessage()
    ]
    assert len(critical) == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 5.0),
        ("", 5.0),
        ("2", 2.0),
        ("0", 0.0),
        ("0.2", 1.0),
        ("-1", 5.0),
        ("abc", 5.0),
        ("nan", 5.0),
        # asyncio.timeout(inf) never fires: the sync would never run while the
        # API reported it on.
        ("inf", 5.0),
        ("1e309", 5.0),
    ],
)
def test_the_interval_comes_from_the_environment(raw, expected) -> None:
    env = {} if raw is None else {"FLAG_SYNC_INTERVAL_SECONDS": raw}
    with mock.patch.dict(os.environ, env, clear=False):
        if raw is None:
            os.environ.pop("FLAG_SYNC_INTERVAL_SECONDS", None)
        assert main_mod._flag_sync_interval_seconds() == expected


# ---------------------------------------------------------------------------
# The loop, and the lifespan that runs it
# ---------------------------------------------------------------------------


def test_the_sync_loop_runs_on_its_interval_and_stops_with_the_app() -> None:
    passes: list[float] = []

    async def scenario() -> None:
        stop = asyncio.Event()
        with mock.patch.object(
            main_mod, "_sync_flag_overrides", side_effect=lambda: passes.append(time.time())
        ):
            task = asyncio.create_task(main_mod._flag_sync_loop(stop, 0.01))
            async with asyncio.timeout(5):
                while len(passes) < 2:
                    await asyncio.sleep(0.01)
            stop.set()
            async with asyncio.timeout(2):
                await task

    asyncio.run(scenario())
    assert len(passes) >= 2


def test_the_sync_loop_ends_when_cancelled() -> None:
    async def scenario() -> None:
        with mock.patch.object(main_mod, "_sync_flag_overrides"):
            task = asyncio.create_task(main_mod._flag_sync_loop(asyncio.Event(), 60))
            await asyncio.sleep(0)
            task.cancel()
            async with asyncio.timeout(2):
                await asyncio.gather(task, return_exceptions=True)
        assert task.done()

    asyncio.run(scenario())


@pytest.mark.parametrize("interval", [0.05, 0.0])
def test_the_lifespan_runs_the_sync_only_when_it_has_an_interval(interval) -> None:
    from app import receptionist

    synced = threading.Event()
    with (
        mock.patch.object(main_mod, "_FLAG_SYNC_INTERVAL_SECONDS", interval),
        mock.patch.object(main_mod, "_sync_flag_overrides", side_effect=synced.set),
        mock.patch.object(receptionist, "start"),
        TestClient(main_mod.app),
    ):
        ran = synced.wait(timeout=0.5 if interval == 0 else 5)
    assert ran is (interval > 0)


# ---------------------------------------------------------------------------
# The admin API
# ---------------------------------------------------------------------------


def _admin() -> dict:
    return {"Authorization": f"Bearer {make_dev_token('ops', role='ura_admin')}"}


def test_a_console_change_reaches_another_process_and_so_does_its_removal(clean_flags, shared_db) -> None:
    other = FeatureFlags()
    value = _off_default()
    client = TestClient(main_mod.app)
    try:
        r = client.patch(f"/v1/admin/flags/{FLAG}?enabled={str(value).lower()}", headers=_admin())
        assert r.status_code == 200, r.text
        assert r.json()["scope"] == "every_process_sharing_this_database"
        assert r.json()["sync_interval_seconds"] == main_mod._FLAG_SYNC_INTERVAL_SECONDS
        assert flags.describe(FLAG)["stored"] is True
        assert other.apply_stored(db.load_flag_overrides()).get(FLAG) is value

        r = client.delete(f"/v1/admin/flags/{FLAG}", headers=_admin())
        assert r.status_code == 200, r.text
        assert other.apply_stored(db.load_flag_overrides()).get(FLAG, "absent") is None
        assert other.describe(FLAG)["overridden"] is False
    finally:
        shared_db.clear_flag_override(FLAG)


def test_a_change_that_cannot_be_stored_changes_nothing(clean_flags, shared_db) -> None:
    """Store first, as DELETE does: a failed write must not leave a process-only override."""
    client = TestClient(main_mod.app, raise_server_exceptions=False)
    with mock.patch.object(
        main_mod.db, "save_flag_override", side_effect=RuntimeError("disk full")
    ):
        r = client.patch(
            f"/v1/admin/flags/{FLAG}?enabled={str(_off_default()).lower()}", headers=_admin()
        )
    assert r.status_code == 500
    assert flags.describe(FLAG)["overridden"] is False


def test_the_flag_list_says_how_far_a_change_reaches(clean_flags, shared_db) -> None:
    staff = make_dev_token("a", role="ura_staff")
    r = TestClient(main_mod.app).get(
        "/v1/admin/flags", headers={"Authorization": f"Bearer {staff}"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "every_process_sharing_this_database"
    assert body["sync_interval_seconds"] == main_mod._FLAG_SYNC_INTERVAL_SECONDS
    assert "synced_at" in body
    assert all("stored" in row for row in body["flags"])


# ---------------------------------------------------------------------------
# The Postgres store: no pool is a failure, not an empty table
# ---------------------------------------------------------------------------


def test_the_postgres_store_without_a_pool_fails_instead_of_reading_empty() -> None:
    """Read as {}, a lost pool would clear every stored override in every process.

    That includes a stored calls kill switch. Raising sends the sync down its
    "keep the current flags" path, and startup down its fail-closed path.
    """
    from app import postgres

    with mock.patch.object(postgres, "_get_pool", return_value=None):
        with pytest.raises(RuntimeError):
            postgres.load_flag_overrides()
        # A write that silently did nothing let PATCH report a change no other
        # process would ever see.
        with pytest.raises(RuntimeError):
            postgres.save_flag_override(FLAG, True)
        with pytest.raises(RuntimeError):
            postgres.clear_flag_override(FLAG)


def test_a_lost_postgres_pool_keeps_this_processs_flags(clean_flags) -> None:
    from app import postgres

    value = _off_default()
    with mock.patch.object(main_mod.db, "load_flag_overrides", return_value={FLAG: value}):
        main_mod._sync_flag_overrides()
    with (
        mock.patch.object(postgres, "_get_pool", return_value=None),
        mock.patch.object(main_mod.db, "load_flag_overrides", postgres.load_flag_overrides),
    ):
        main_mod._sync_flag_overrides()

    assert flags.is_enabled(FLAG) is value
    assert main_mod._flag_sync_state["failing"] is True

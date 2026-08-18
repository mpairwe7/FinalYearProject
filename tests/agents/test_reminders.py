"""Deadline reminders — Phase 20's first step.

Everything so far has been reactive: the taxpayer asks, the system
answers. A reminder is the opposite, and reaching someone unasked is a
different kind of act — a new processing purpose, not a new feature on
an existing one.

So consent is checked first, on its own purpose, inside the selector.
Not by a caller trusted to remember: the act being authorised is the
contact, so the authorisation belongs where the contact is decided.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3

import pytest

from app.reminders import (
    DEFAULT_LEAD_DAYS,
    MAX_LEAD_DAYS,
    REMINDER_PURPOSE,
    Reminder,
    ReminderPreferences,
    due_reminders,
    refresh_inbox,
)

#: Three days before the 15th monthly deadline.
JAN_12 = _dt.date(2026, 1, 12)

ENABLED_PROFILE = {
    "display_name": "Amina",
    "registered_tax_types": ["vat", "paye"],
    "reminder_preferences": {"enabled": True, "lead_days": 5},
}


@pytest.fixture
def user(tmp_db):
    return tmp_db.upsert_user("sub-1")


class TestConsentGate:
    def test_nothing_is_sent_without_consent(self, tmp_db, user):
        result = due_reminders(user["id"], profile=ENABLED_PROFILE, today=JAN_12)
        assert not result
        assert result.skipped_reason == "no consent for deadline_reminders"

    def test_consent_to_personalisation_is_not_consent_to_be_contacted(
        self, tmp_db, user
    ):
        # The distinction the separate purpose exists to make.
        tmp_db.grant_consent(user["id"], "personalization", "v1")
        assert not due_reminders(user["id"], profile=ENABLED_PROFILE, today=JAN_12)

    def test_with_consent_a_reminder_is_selected(self, tmp_db, user):
        tmp_db.grant_consent(user["id"], REMINDER_PURPOSE, "v1")
        result = due_reminders(user["id"], profile=ENABLED_PROFILE, today=JAN_12)
        assert result
        assert result.reminders[0].days_until == 3

    def test_withdrawal_stops_contact_immediately(self, tmp_db, user):
        tmp_db.grant_consent(user["id"], REMINDER_PURPOSE, "v1")
        assert due_reminders(user["id"], profile=ENABLED_PROFILE, today=JAN_12)
        tmp_db.withdraw_consent(user["id"], REMINDER_PURPOSE)
        result = due_reminders(user["id"], profile=ENABLED_PROFILE, today=JAN_12)
        assert not result
        assert "no consent" in result.skipped_reason

    def test_no_user_id_selects_nothing(self, tmp_db):
        assert not due_reminders("", profile=ENABLED_PROFILE, today=JAN_12)

    def test_a_broken_consent_store_does_not_stop_the_scheduler(
        self, tmp_db, user, monkeypatch
    ):
        # A scheduler iterating thousands of users must not be halted by
        # one bad row — and must not fall open either.
        def _boom(*args, **kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr("app.database.has_active_consent", _boom)
        result = due_reminders(user["id"], profile=ENABLED_PROFILE, today=JAN_12)
        assert not result
        assert result.skipped_reason == "consent lookup failed"


class TestPreferences:
    def test_defaults_are_the_quiet_ones(self):
        # A profile written by an older client has no preferences; a
        # missing preference must never turn messaging on.
        prefs = ReminderPreferences.from_profile({})
        assert prefs.enabled is False
        assert prefs.lead_days == DEFAULT_LEAD_DAYS

    def test_a_missing_profile_is_not_an_error(self):
        assert ReminderPreferences.from_profile(None).enabled is False

    def test_disabled_means_nothing_is_selected(self, tmp_db, user):
        tmp_db.grant_consent(user["id"], REMINDER_PURPOSE, "v1")
        profile = {**ENABLED_PROFILE, "reminder_preferences": {"enabled": False}}
        result = due_reminders(user["id"], profile=profile, today=JAN_12)
        assert result.skipped_reason == "reminders not enabled"

    def test_lead_days_is_clamped(self):
        assert ReminderPreferences.from_profile(
            {"reminder_preferences": {"lead_days": 9999}}
        ).lead_days == MAX_LEAD_DAYS
        assert ReminderPreferences.from_profile(
            {"reminder_preferences": {"lead_days": -5}}
        ).lead_days == 0

    def test_a_nonsense_lead_value_falls_back(self):
        assert ReminderPreferences.from_profile(
            {"reminder_preferences": {"lead_days": "soon"}}
        ).lead_days == DEFAULT_LEAD_DAYS

    def test_a_malformed_preferences_blob_is_ignored(self):
        assert ReminderPreferences.from_profile(
            {"reminder_preferences": "yes please"}
        ).enabled is False

    def test_a_short_lead_window_selects_nothing_yet(self, tmp_db, user):
        tmp_db.grant_consent(user["id"], REMINDER_PURPOSE, "v1")
        profile = {**ENABLED_PROFILE, "reminder_preferences": {"enabled": True, "lead_days": 1}}
        result = due_reminders(user["id"], profile=profile, today=JAN_12)
        assert result.skipped_reason == "no deadline within the lead window"


class TestMessageText:
    def _reminder(self, days: int, name: str = "Amina") -> Reminder:
        return Reminder(
            deadline_name="VAT monthly",
            description="Returns for the prior month are due.",
            due_date="2026-01-15",
            days_until=days,
            display_name=name,
        )

    def test_it_names_the_taxpayer_when_known(self):
        assert self._reminder(3).message().startswith("Amina, your")

    def test_it_reads_correctly_without_a_name(self):
        assert self._reminder(3, name="").message().startswith("Your")

    @pytest.mark.parametrize(
        ("days", "phrase"), [(0, "due today"), (1, "due tomorrow"), (5, "due in 5 days")]
    )
    def test_the_timing_reads_naturally(self, days, phrase):
        assert phrase in self._reminder(days).message()

    def test_the_date_is_always_stated(self):
        # "in 3 days" is ambiguous once a message sits unread.
        assert "2026-01-15" in self._reminder(3).message()


class TestInboxChannel:
    def test_refresh_writes_when_consent_is_granted(self, tmp_db, user):
        tmp_db.grant_consent(user["id"], REMINDER_PURPOSE, "v1")
        result = refresh_inbox(user["id"], ENABLED_PROFILE, today=JAN_12)
        assert result["written"] >= 1
        assert tmp_db.list_reminder_inbox(user["id"])


class TestConsentPurposeIsRegistered:
    def test_the_enum_knows_about_it(self):
        from typing import get_args

        from app.auth.models import ConsentPurpose

        assert REMINDER_PURPOSE in get_args(ConsentPurpose)

    def test_the_schema_constraint_is_derived_from_the_enum(self, tmp_db):
        # The list used to be written twice — enum and CHECK — so adding
        # a purpose left the database rejecting it.
        from typing import get_args

        from app.auth.models import ConsentPurpose

        assert set(tmp_db.consent_purposes()) == set(get_args(ConsentPurpose))

    def test_the_new_purpose_is_accepted_by_the_database(self, tmp_db, user):
        receipt = tmp_db.grant_consent(user["id"], REMINDER_PURPOSE, "v1")
        assert receipt["purpose"] == REMINDER_PURPOSE

    def test_an_invalid_purpose_is_still_rejected(self, tmp_db, user):
        with pytest.raises(sqlite3.IntegrityError):
            tmp_db.grant_consent(user["id"], "sell_their_data", "v1")


class TestStaleDatabaseMigration:
    """SQLite cannot alter a CHECK, so an old database keeps the old list."""

    def _stale_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE users (id TEXT PRIMARY KEY, tenant_id TEXT, external_id TEXT,
                                email TEXT, role TEXT, created_at REAL, last_seen_at REAL);
            CREATE TABLE consent_receipts (
              receipt_id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              purpose TEXT NOT NULL CHECK(purpose IN ('personalization','analytics')),
              version TEXT NOT NULL, granted_at REAL NOT NULL, withdrawn_at REAL,
              legal_basis TEXT NOT NULL DEFAULT 'consent'
                          CHECK(legal_basis IN ('consent','public_task','legal_obligation')));
            INSERT INTO users VALUES ('u1','default','sub-1','','public',0,0);
            INSERT INTO consent_receipts
              VALUES ('r1','u1','personalization','v1',1.0,NULL,'consent');
            """
        )
        conn.commit()
        return conn

    def test_the_rebuild_preserves_existing_receipts(self):
        from app import database as db

        conn = self._stale_db()
        db._refresh_consent_purpose_check(conn)
        assert conn.execute("SELECT COUNT(*) FROM consent_receipts").fetchone()[0] == 1

    def test_the_new_purpose_is_accepted_afterwards(self):
        from app import database as db

        conn = self._stale_db()
        db._refresh_consent_purpose_check(conn)
        conn.execute(
            "INSERT INTO consent_receipts VALUES (?,?,?,?,?,?,?)",
            ("r2", "u1", REMINDER_PURPOSE, "v1", 2.0, None, "consent"),
        )
        assert conn.execute(
            "SELECT purpose FROM consent_receipts WHERE receipt_id='r2'"
        ).fetchone()[0] == REMINDER_PURPOSE

    def test_the_constraint_is_not_simply_dropped(self):
        # The remedy must not be "remove the check".
        from app import database as db

        conn = self._stale_db()
        db._refresh_consent_purpose_check(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO consent_receipts VALUES (?,?,?,?,?,?,?)",
                ("r3", "u1", "sell_their_data", "v1", 3.0, None, "consent"),
            )

    def test_it_is_a_no_op_when_the_schema_is_current(self, tmp_db):
        from app import database as db

        conn = tmp_db._get_connection()
        before = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='consent_receipts'"
        ).fetchone()[0]
        db._refresh_consent_purpose_check(conn)
        after = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='consent_receipts'"
        ).fetchone()[0]
        assert before == after

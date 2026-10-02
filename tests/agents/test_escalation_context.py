"""Context-aware escalation — the officer gets the conversation, not a fragment.

The point of the pipeline is that a taxpayer who has been escalated does
not have to explain themselves again. Two properties carry that, and
both are easy to lose:

* The transcript is **snapshotted onto the ticket**, not joined on read.
  ``conversations`` is purged after ``CONVERSATION_TTL_DAYS`` (7) while a
  ticket can sit in the queue far longer, so a live join would show an
  empty transcript for exactly the tickets that have been waiting.
* Because the ticket now holds the taxpayer's words, **erasure has to
  reach it** even after the conversation it came from is gone.
"""

from __future__ import annotations

import time

import pytest


@pytest.fixture
def seeded(tmp_db):
    """A three-turn conversation, ready to escalate."""
    turns = [
        ("how do I register for VAT", "You register on the URA portal."),
        ("I already did that last month", "Registration usually completes in 3 days."),
        ("it is still not showing", "Let me check what could cause that."),
    ]
    for user, bot in turns:
        tmp_db.log_conversation(
            session_id="sess1",
            conversation_id="conv-abc",
            user_message=user,
            bot_reply=bot,
            user_id="oidc-sub-123",
        )
    return tmp_db


class TestTranscriptCapture:
    def test_the_whole_conversation_is_returned_not_a_window(self, seeded):
        # get_recent_turns exists to seed a prompt and returns the last
        # few; an officer needs all of it.
        transcript = seeded.get_conversation_transcript(conversation_id="conv-abc")
        assert len(transcript) == 3

    def test_both_sides_are_present(self, seeded):
        # The old handoff carried user messages only, so an officer could
        # not see what the taxpayer had already been told — which is what
        # makes them ask for it all again.
        transcript = seeded.get_conversation_transcript(conversation_id="conv-abc")
        assert all(turn["user_message"] and turn["bot_reply"] for turn in transcript)

    def test_it_is_oldest_first(self, seeded):
        transcript = seeded.get_conversation_transcript(conversation_id="conv-abc")
        assert transcript[0]["user_message"] == "how do I register for VAT"
        assert transcript[-1]["user_message"] == "it is still not showing"

    def test_nothing_is_truncated(self, seeded):
        long_message = "x" * 4000
        seeded.log_conversation(
            session_id="sess1",
            conversation_id="conv-abc",
            user_message=long_message,
            bot_reply="ok",
        )
        transcript = seeded.get_conversation_transcript(conversation_id="conv-abc")
        assert transcript[-1]["user_message"] == long_message

    def test_timestamps_travel_with_the_turns(self, seeded):
        # So the officer can see where the conversation stalled.
        transcript = seeded.get_conversation_transcript(conversation_id="conv-abc")
        assert all(turn["created_at"] > 0 for turn in transcript)

    def test_session_id_works_when_there_is_no_conversation_id(self, seeded):
        assert seeded.get_conversation_transcript(session_id="sess1")

    def test_no_identifier_returns_nothing_rather_than_everything(self, seeded):
        assert seeded.get_conversation_transcript() == []

    def test_the_limit_is_bounded(self, seeded):
        assert seeded.get_conversation_transcript(conversation_id="conv-abc", limit=10**9)


class TestTicketCarriesTheTranscript:
    def _ticket(self, db, **kw):
        transcript = db.get_conversation_transcript(conversation_id="conv-abc")
        return db.create_ticket(
            reason="User explicitly asked for a human",
            conversation_id="conv-abc",
            session_id="sess1",
            transcript=transcript,
            user_id="oidc-sub-123",
            **kw,
        )

    def test_the_detail_view_returns_the_transcript(self, seeded):
        created = self._ticket(seeded)
        assert len(seeded.get_ticket(created["id"])["transcript"]) == 3

    def test_the_queue_view_does_not_ship_every_transcript(self, seeded):
        self._ticket(seeded)
        # A 50-ticket queue must not carry 50 conversations.
        assert seeded.list_tickets()[0]["transcript"] == []

    def test_the_transcript_outlives_the_conversation_purge(self, seeded):
        created = self._ticket(seeded)
        conn = seeded._get_connection()
        conn.execute("UPDATE conversations SET created_at = ?", (time.time() - 999_999,))
        conn.commit()
        seeded.cleanup_expired_data()

        assert seeded.get_conversation_transcript(conversation_id="conv-abc") == []
        assert len(seeded.get_ticket(created["id"])["transcript"]) == 3

    def test_a_ticket_without_a_transcript_still_works(self, seeded):
        created = seeded.create_ticket(reason="no conversation attached")
        assert seeded.get_ticket(created["id"])["transcript"] == []


class TestErasureReachesTheTranscript:
    """A ticket holding the taxpayer's words must honour an erasure request."""

    def test_erasure_deletes_the_ticket(self, seeded):
        transcript = seeded.get_conversation_transcript(conversation_id="conv-abc")
        created = seeded.create_ticket(
            reason="human requested",
            conversation_id="conv-abc",
            transcript=transcript,
            user_id="oidc-sub-123",
        )
        seeded.delete_user_cascade("user-row", external_id="oidc-sub-123")
        assert seeded.get_ticket(created["id"]) is None

    def test_erasure_reaches_a_ticket_whose_conversation_was_purged(self, seeded):
        # Erasure used to find tickets only via `conversations`. Once that
        # row is purged the lookup returns nothing, so the ticket — and the
        # transcript inside it — survived the erasure request.
        transcript = seeded.get_conversation_transcript(conversation_id="conv-abc")
        created = seeded.create_ticket(
            reason="human requested",
            conversation_id="conv-abc",
            transcript=transcript,
            user_id="oidc-sub-123",
        )
        conn = seeded._get_connection()
        conn.execute("DELETE FROM conversations")
        conn.commit()

        seeded.delete_user_cascade("user-row", external_id="oidc-sub-123")
        assert seeded.get_ticket(created["id"]) is None

    def test_another_taxpayers_ticket_is_untouched(self, seeded):
        mine = seeded.create_ticket(reason="a", user_id="oidc-sub-123")
        theirs = seeded.create_ticket(reason="b", user_id="oidc-sub-999")
        seeded.delete_user_cascade("user-row", external_id="oidc-sub-123")
        assert seeded.get_ticket(mine["id"]) is None
        assert seeded.get_ticket(theirs["id"]) is not None


class TestBackendParity:
    """Tickets must live wherever conversations live.

    Production mandates ``ANALYTICS_BACKEND=postgres``. The dispatch block
    re-binds conversation functions to Postgres; tickets were left behind
    on SQLite, so every escalation went to a per-replica file — invisible
    to an officer served by another pod, lost on restart, and referencing
    a conversation in a different database.
    """

    def test_every_dispatched_name_exists_in_the_postgres_module(self):
        import re
        from pathlib import Path

        from app import postgres as pg

        source = Path(pg.__file__).with_name("database.py").read_text()
        bound = set(re.findall(r"^\s+(\w+) = _pg\.", source, re.M))
        assert bound, "dispatch block not found"
        missing = sorted(name for name in bound if not hasattr(pg, name))
        assert not missing, f"re-bound but absent from postgres.py: {missing}"

    @pytest.mark.parametrize(
        "name",
        ["create_ticket", "get_ticket", "list_tickets", "update_ticket", "ticket_stats"],
    )
    def test_the_ticket_surface_is_mirrored(self, name):
        from app import postgres as pg

        assert hasattr(pg, name), f"postgres backend is missing {name}"

    def test_no_dispatched_function_has_drifted(self):
        """Every re-bound name must take the same arguments in both backends.

        ``postgres.log_conversation`` was missing ``contexts`` and
        ``user_id`` while all three call sites passed them, so it raised
        TypeError — swallowed by the callers' ``except Exception``. The
        backend production mandates logged no conversations at all, which
        would have left every escalation transcript empty in production
        and nothing for erasure to find. A signature check is cheap; the
        failure mode was silent.
        """
        import inspect
        import re
        from pathlib import Path

        from app import database as db
        from app import postgres as pg

        source = Path(pg.__file__).with_name("database.py").read_text()
        drift = []
        for name in sorted(set(re.findall(r"^\s+(\w+) = _pg\.", source, re.M))):
            sqlite_fn, pg_fn = getattr(db, name, None), getattr(pg, name, None)
            if sqlite_fn is None or pg_fn is None:
                continue
            try:
                sqlite_params = list(inspect.signature(sqlite_fn).parameters)
                pg_params = list(inspect.signature(pg_fn).parameters)
            except (TypeError, ValueError):
                continue
            if sqlite_params != pg_params:
                drift.append(f"{name}: sqlite={sqlite_params} postgres={pg_params}")
        assert not drift, "backend signature drift:\n" + "\n".join(drift)

    def test_callers_can_actually_call_the_postgres_logger(self):
        # The exact kwargs main.py passes must bind.
        import inspect

        from app import postgres as pg

        inspect.signature(pg.log_conversation).bind(
            session_id="s",
            conversation_id="c",
            user_message="u",
            bot_reply="b",
            sources="[]",
            contexts="[]",
            response_time_ms=1.0,
            user_id="sub-1",
        )


class TestConversationContextUserIsolation:
    def test_get_recent_turns_isolates_by_user_id(self, seeded):
        turns = seeded.get_recent_turns(conversation_id="conv-abc", user_id="oidc-sub-123")
        assert len(turns) > 0
        other_turns = seeded.get_recent_turns(conversation_id="conv-abc", user_id="intruder-456")
        assert len(other_turns) == 0

    def test_get_conversation_context_isolates_by_user_id(self, seeded):
        ctx = seeded.get_conversation_context(conversation_id="conv-abc", user_id="oidc-sub-123")
        assert len(ctx["recent_turns"]) > 0
        other_ctx = seeded.get_conversation_context(conversation_id="conv-abc", user_id="intruder-456")
        assert len(other_ctx["recent_turns"]) == 0

    def test_null_owned_row_not_accessible_by_authenticated_user(self, seeded):
        seeded.log_conversation(
            session_id="sess-anon",
            conversation_id="conv-anon",
            user_message="anonymous question",
            bot_reply="anonymous answer",
            user_id=None,
        )
        # Authenticated user should NOT be able to read unowned/anonymous rows
        auth_turns = seeded.get_recent_turns(conversation_id="conv-anon", user_id="oidc-sub-123")
        assert len(auth_turns) == 0

    def test_ws_chat_session_anonymous_resume_rejected(self, seeded):
        from app.chat_ws_v2 import WsChatSession

        session = WsChatSession(
            session_id="ws-anon",
            conversation_id="conv-abc",
            user_id="",
            tenant_id="default",
            locale="en",
        )
        assert session.try_resume("resp-999") is False
        assert session.history == []

    def test_ws_chat_session_owner_resume_accepted(self, seeded, monkeypatch):
        from app.chat_ws_v2 import WsChatSession
        import app.chat_ws_v2 as chat_ws_mod

        monkeypatch.setattr(chat_ws_mod, "db", seeded)
        session = WsChatSession(
            session_id="ws-owner",
            conversation_id="conv-abc",
            user_id="oidc-sub-123",
            tenant_id="default",
            locale="en",
        )
        assert session.try_resume("resp-999") is True
        assert len(session.history) > 0

    def test_ws_chat_session_non_owner_resume_rejected(self, seeded, monkeypatch):
        from app.chat_ws_v2 import WsChatSession
        import app.chat_ws_v2 as chat_ws_mod

        monkeypatch.setattr(chat_ws_mod, "db", seeded)
        session = WsChatSession(
            session_id="ws-intruder",
            conversation_id="conv-abc",
            user_id="intruder-999",
            tenant_id="default",
            locale="en",
        )
        assert session.try_resume("resp-999") is False
        assert session.history == []

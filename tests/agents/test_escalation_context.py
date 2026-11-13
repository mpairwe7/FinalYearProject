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
        transcript = seeded.get_conversation_transcript(
            conversation_id="conv-abc", user_id="oidc-sub-123"
        )
        assert len(transcript) == 3

    def test_both_sides_are_present(self, seeded):
        # The old handoff carried user messages only, so an officer could
        # not see what the taxpayer had already been told — which is what
        # makes them ask for it all again.
        transcript = seeded.get_conversation_transcript(
            conversation_id="conv-abc", user_id="oidc-sub-123"
        )
        assert all(turn["user_message"] and turn["bot_reply"] for turn in transcript)

    def test_it_is_oldest_first(self, seeded):
        transcript = seeded.get_conversation_transcript(
            conversation_id="conv-abc", user_id="oidc-sub-123"
        )
        assert transcript[0]["user_message"] == "how do I register for VAT"
        assert transcript[-1]["user_message"] == "it is still not showing"

    def test_nothing_is_truncated(self, seeded):
        long_message = "x" * 4000
        seeded.log_conversation(
            session_id="sess1",
            conversation_id="conv-abc",
            user_message=long_message,
            bot_reply="ok",
            user_id="oidc-sub-123",
        )
        transcript = seeded.get_conversation_transcript(
            conversation_id="conv-abc", user_id="oidc-sub-123"
        )
        assert transcript[-1]["user_message"] == long_message

    def test_timestamps_travel_with_the_turns(self, seeded):
        # So the officer can see where the conversation stalled.
        transcript = seeded.get_conversation_transcript(
            conversation_id="conv-abc", user_id="oidc-sub-123"
        )
        assert all(turn["created_at"] > 0 for turn in transcript)

    def test_session_id_works_when_there_is_no_conversation_id(self, seeded):
        assert seeded.get_conversation_transcript(
            session_id="sess1", user_id="oidc-sub-123"
        )

    def test_transcript_is_scoped_to_tenant_and_subject(self, seeded):
        seeded.log_conversation(
            session_id="other-session",
            conversation_id="conv-abc",
            user_message="other subject turn",
            bot_reply="other reply",
            user_id="other-subject",
            tenant_id="other-tenant",
        )
        owner = seeded.get_conversation_transcript(
            conversation_id="conv-abc", user_id="oidc-sub-123", tenant_id="default"
        )
        other = seeded.get_conversation_transcript(
            conversation_id="conv-abc", user_id="other-subject", tenant_id="other-tenant"
        )
        assert len(owner) == 3
        assert [turn["user_message"] for turn in other] == ["other subject turn"]

    def test_no_identifier_returns_nothing_rather_than_everything(self, seeded):
        assert seeded.get_conversation_transcript() == []

    def test_the_limit_is_bounded(self, seeded):
        assert seeded.get_conversation_transcript(
            conversation_id="conv-abc", limit=10**9, user_id="oidc-sub-123"
        )


class TestTicketCarriesTheTranscript:
    def _ticket(self, db, **kw):
        transcript = db.get_conversation_transcript(
            conversation_id="conv-abc", user_id="oidc-sub-123"
        )
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

        assert seeded.get_conversation_transcript(
            conversation_id="conv-abc", user_id="oidc-sub-123"
        ) == []
        assert len(seeded.get_ticket(created["id"])["transcript"]) == 3

    def test_a_ticket_without_a_transcript_still_works(self, seeded):
        created = seeded.create_ticket(reason="no conversation attached")
        assert seeded.get_ticket(created["id"])["transcript"] == []


class TestErasureReachesTheTranscript:
    """A ticket holding the taxpayer's words must honour an erasure request."""

    def test_erasure_deletes_the_ticket(self, seeded):
        transcript = seeded.get_conversation_transcript(
            conversation_id="conv-abc", user_id="oidc-sub-123"
        )
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
        transcript = seeded.get_conversation_transcript(
            conversation_id="conv-abc", user_id="oidc-sub-123"
        )
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


class TestConversationStateScope:
    def test_topic_and_workflow_keys_are_owner_and_tenant_scoped(self, tmp_db):
        key_t1 = tmp_db.conversation_state_key(
            "shared-conversation", user_id="shared-subject", tenant_id="t1"
        )
        key_t2 = tmp_db.conversation_state_key(
            "shared-conversation", user_id="shared-subject", tenant_id="t2"
        )
        key_other_user = tmp_db.conversation_state_key(
            "shared-conversation", user_id="other-subject", tenant_id="t1"
        )
        assert len({key_t1, key_t2, key_other_user}) == 3

        tmp_db.upsert_workflow_session(
            key_t1,
            "tin_procedure_help",
            0,
            {"taxpayer_kind": "individual"},
            user_id="shared-subject",
            tenant_id="t1",
        )
        assert tmp_db.get_workflow_session(key_t1, tenant_id="t1")["slots"] == {
            "taxpayer_kind": "individual"
        }
        assert tmp_db.get_workflow_session(key_t1, tenant_id="t2") is None

    def test_unambiguous_legacy_state_is_rekeyed_to_its_owner(self, tmp_db):
        tmp_db.log_conversation(
            session_id="legacy-session",
            conversation_id="legacy-conversation",
            user_message="register for VAT",
            bot_reply="I can help.",
            user_id="legacy-subject",
            tenant_id="legacy-tenant",
        )
        tmp_db.upsert_conversation_topic(
            "legacy-conversation",
            topic_id="vat_registration",
            label="VAT registration",
            tax_type="vat",
            confidence=0.9,
        )
        tmp_db.upsert_workflow_session(
            "legacy-conversation",
            "tin_procedure_help",
            1,
            {"taxpayer_kind": "individual"},
            user_id="legacy-subject",
        )

        migrated = tmp_db.migrate_legacy_conversation_state_keys()
        state_id = tmp_db.conversation_state_key(
            "legacy-conversation",
            user_id="legacy-subject",
            tenant_id="legacy-tenant",
        )
        assert migrated["conversation_topics"] == 1
        assert migrated["workflow_sessions"] == 1
        assert tmp_db.get_conversation_topic(state_id)["topic_id"] == "vat_registration"
        assert tmp_db.get_workflow_session(state_id, tenant_id="legacy-tenant")["slots"] == {
            "taxpayer_kind": "individual"
        }


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

    def test_history_is_tenant_scoped(self, seeded):
        seeded.log_conversation(
            session_id="sess-cross-tenant",
            conversation_id="conv-shared",
            user_message="tenant one question",
            bot_reply="tenant one answer",
            user_id="same-subject",
            tenant_id="tenant-one",
        )
        seeded.log_conversation(
            session_id="sess-cross-tenant",
            conversation_id="conv-shared",
            user_message="tenant two question",
            bot_reply="tenant two answer",
            user_id="same-subject",
            tenant_id="tenant-two",
        )
        tenant_one = seeded.get_recent_turns(
            conversation_id="conv-shared", user_id="same-subject", tenant_id="tenant-one"
        )
        assert [turn["user_message"] for turn in tenant_one] == ["tenant one question"]

    def test_anonymous_history_requires_matching_session(self, seeded):
        seeded.log_conversation(
            session_id="anon-session-a",
            conversation_id="anon-shared",
            user_message="private anonymous question",
            bot_reply="private answer",
        )
        assert seeded.get_recent_turns(conversation_id="anon-shared") == []
        assert seeded.get_recent_turns(
            session_id="anon-session-b", conversation_id="anon-shared"
        ) == []
        assert len(seeded.get_recent_turns(
            session_id="anon-session-a", conversation_id="anon-shared"
        )) == 1

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
        seeded.execute(
            """INSERT INTO conversation_responses
               (response_id, conversation_id, user_id, tenant_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("resp-owned", "conv-abc", "oidc-sub-123", "default", time.time()),
        )
        assert session.try_resume("resp-owned") is True
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
        assert session.try_resume("resp-owned") is False
        assert session.history == []


class TestTheEscalatedTurnNamesItsSources:
    """G640: the turn that triggered the handoff was stored with no sources, so
    the officer could not see what the answer they were taking over rested on."""

    def test_the_sources_the_handoff_reviewed_are_on_the_escalated_turn(self, seeded, monkeypatch):
        from app.flags import flags
        from app.service import ChatModel

        monkeypatch.setenv("FLAG_TICKET_QUEUE", "true")
        flags.set("ticket_queue", True)
        model = ChatModel.__new__(ChatModel)
        ticket_id = model._maybe_create_ticket(
            reason="taxpayer asked for an officer",
            user_query="it is still not showing",
            bot_reply="Let me check what could cause that.",
            session_id="sess1",
            conversation_id="conv-abc",
            handoff={"summary": "VAT registration not showing", "sources_reviewed": ["vat_registration_faqs.csv", "vat_act.pdf"]},
            user_id="oidc-sub-123",
        )
        assert ticket_id
        last = seeded.get_ticket(ticket_id)["transcript"][-1]
        assert last["topic_tag"] == "escalated"
        assert last["sources"] == ["vat_registration_faqs.csv", "vat_act.pdf"]


class TestAReusedTicketIsBroughtUpToDate:
    """G641: AI answers given after escalation never reached the ticket, and a
    second escalation reused it without raising its priority, so the officer
    worked from the first snapshot."""

    def test_later_turns_and_a_higher_priority_reach_the_open_ticket(self, seeded, monkeypatch):
        from app.flags import flags
        from app.service import ChatModel

        monkeypatch.setenv("FLAG_TICKET_QUEUE", "true")
        flags.set("ticket_queue", True)
        model = ChatModel.__new__(ChatModel)
        first = model._maybe_create_ticket(
            reason="taxpayer asked for an officer", user_query="it is still not showing",
            bot_reply="An officer will look at this.", session_id="sess1", conversation_id="conv-abc",
            user_id="oidc-sub-123", priority="normal",
        )
        time.sleep(0.01)
        seeded.log_conversation(
            session_id="sess1", conversation_id="conv-abc",
            user_message="what is the penalty if I file late?",
            bot_reply="The penalty is UGX 200,000 or 2% of the tax, whichever is higher.",
            user_id="oidc-sub-123",
        )
        time.sleep(0.01)
        second = model._maybe_create_ticket(
            reason="deadline today", user_query="my deadline is today, please help",
            bot_reply="An officer will look at this.", session_id="sess1", conversation_id="conv-abc",
            user_id="oidc-sub-123", priority="urgent",
        )
        assert second == first
        ticket = seeded.get_ticket(first)
        assert ticket["priority"] == "urgent"
        replies = [t.get("bot_reply", "") for t in ticket["transcript"]]
        assert any("UGX 200,000" in r for r in replies)
        assert ticket["transcript"][-1]["user_message"] == "my deadline is today, please help"

    def _ticket(self, seeded) -> str:
        return seeded.create_ticket(
            reason="taxpayer asked for an officer",
            user_query="it is still not showing",
            conversation_id="conv-race",
            transcript=[{"user_message": "hello", "bot_reply": "hi", "created_at": 1.0}],
        )["id"]

    def test_a_refresh_keeps_a_taxpayer_reply_its_stale_read_missed(self, seeded, monkeypatch):
        """The refresh read the ticket, then wrote the whole transcript back; a
        taxpayer follow-up appended in between was lost. It now reads inside
        its own write transaction."""
        tid = self._ticket(seeded)
        stale = seeded.get_ticket(tid)
        seeded.append_taxpayer_reply(tid, "I have now uploaded the receipt")
        # Only within this block: the fixture's own patches must stay.
        with monkeypatch.context() as m:
            m.setattr(seeded, "get_ticket", lambda _tid: stale)
            assert seeded.refresh_open_ticket(
                tid, new_turns=[{"user_message": "again", "bot_reply": "ok", "created_at": time.time() + 5}]
            )
        messages = [t.get("user_message") for t in seeded.get_ticket(tid)["transcript"]]
        assert "I have now uploaded the receipt" in messages
        assert messages[-1] == "again"

    def test_a_taxpayer_reply_keeps_turns_a_refresh_added(self, seeded, monkeypatch):
        tid = self._ticket(seeded)
        stale = seeded.get_ticket(tid)
        seeded.refresh_open_ticket(
            tid, new_turns=[{"user_message": "again", "bot_reply": "ok", "created_at": time.time() + 5}]
        )
        with monkeypatch.context() as m:
            m.setattr(seeded, "get_ticket", lambda _tid: stale)
            seeded.append_taxpayer_reply(tid, "any news?")
        messages = [t.get("user_message") for t in seeded.get_ticket(tid)["transcript"]]
        assert "again" in messages and messages[-1] == "any news?"

    def test_turns_added_by_a_refresh_are_redacted_at_rest(self, seeded):
        """create_ticket redacts the transcript it stores; the refresh stored
        the new turns as they came."""
        tid = self._ticket(seeded)
        seeded.refresh_open_ticket(
            tid,
            new_turns=[{"user_message": "call me on 0772123456", "bot_reply": "ok", "created_at": time.time() + 5}],
        )
        seeded.append_taxpayer_reply(tid, "or email me at kato@example.com")
        raw = seeded.query_one("SELECT transcript_json FROM tickets WHERE id = ?", (tid,))["transcript_json"]
        assert "0772123456" not in raw and "kato@example.com" not in raw

    def test_a_lower_priority_does_not_lower_it(self, seeded, monkeypatch):
        from app.flags import flags
        from app.service import ChatModel

        monkeypatch.setenv("FLAG_TICKET_QUEUE", "true")
        flags.set("ticket_queue", True)
        model = ChatModel.__new__(ChatModel)
        kw = dict(reason="r", bot_reply="b", session_id="sess1", conversation_id="conv-abc", user_id="oidc-sub-123")
        first = model._maybe_create_ticket(user_query="q1", priority="high", **kw)
        model._maybe_create_ticket(user_query="q2", priority="low", **kw)
        assert seeded.get_ticket(first)["priority"] == "high"

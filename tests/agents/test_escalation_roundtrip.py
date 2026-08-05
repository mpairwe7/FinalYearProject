"""Phase 18 round trip: the officer's answer reaches the taxpayer.

Escalation was one-way. A taxpayer was told a human would follow up, the
officer resolved the ticket, and nothing ever went back — the answer sat
in a queue only staff could see.

Two things have to hold. The reply must actually reach them, exactly
once. And the officer's *internal* note must never be what reaches them:
``staff_note`` and ``officer_reply`` are separate fields precisely
because a candid note ("caller obstructive, refer to audit") would be a
serious incident if the taxpayer read it.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def ticket(tmp_db):
    return tmp_db.create_ticket(
        reason="User explicitly asked for a human",
        conversation_id="conv-1",
        priority="high",
    )


class TestReplyDelivery:
    def test_a_reply_is_pending_until_delivered(self, tmp_db, ticket):
        tmp_db.update_ticket(ticket["id"], officer_reply="Your TIN was reactivated.")
        pending = tmp_db.pending_officer_reply("conv-1")
        assert pending is not None
        assert pending["officer_reply"] == "Your TIN was reactivated."

    def test_delivery_happens_once(self, tmp_db, ticket):
        tmp_db.update_ticket(ticket["id"], officer_reply="Sorted.")
        assert tmp_db.mark_reply_delivered(ticket["id"]) is True
        assert tmp_db.pending_officer_reply("conv-1") is None
        # Idempotent — a retry must not resurrect it.
        assert tmp_db.mark_reply_delivered(ticket["id"]) is False

    def test_a_ticket_with_no_reply_is_not_pending(self, tmp_db, ticket):
        tmp_db.update_ticket(ticket["id"], status="resolved")
        assert tmp_db.pending_officer_reply("conv-1") is None

    def test_another_conversation_is_not_matched(self, tmp_db, ticket):
        tmp_db.update_ticket(ticket["id"], officer_reply="Sorted.")
        assert tmp_db.pending_officer_reply("conv-other") is None

    def test_no_conversation_id_matches_nothing(self, tmp_db, ticket):
        tmp_db.update_ticket(ticket["id"], officer_reply="Sorted.")
        assert tmp_db.pending_officer_reply("") is None

    def test_the_oldest_reply_is_delivered_first(self, tmp_db, ticket):
        second = tmp_db.create_ticket(reason="another", conversation_id="conv-1")
        tmp_db.update_ticket(ticket["id"], officer_reply="first reply")
        tmp_db.update_ticket(second["id"], officer_reply="second reply")
        assert tmp_db.pending_officer_reply("conv-1")["officer_reply"] == "first reply"


class TestInternalNotesStayInternal:
    def test_a_staff_note_is_not_a_reply(self, tmp_db, ticket):
        tmp_db.update_ticket(ticket["id"], staff_note="INTERNAL: refer to audit")
        assert tmp_db.pending_officer_reply("conv-1") is None

    def test_a_staff_note_does_not_leak_into_the_delivered_payload(self, tmp_db, ticket):
        tmp_db.update_ticket(
            ticket["id"],
            officer_reply="Your TIN was reactivated.",
            staff_note="INTERNAL: caller was obstructive",
        )
        assert "INTERNAL" not in str(tmp_db.pending_officer_reply("conv-1"))


class TestChatSurfacing:
    def test_the_reply_is_returned_to_the_taxpayer(self, tmp_db, ticket):
        from app.service import ChatModel

        tmp_db.update_ticket(
            ticket["id"], officer_reply="Your TIN was reactivated on 4 Aug.",
            assignee="officer.jane",
        )
        text = ChatModel._deliver_officer_reply("conv-1")
        assert "Your TIN was reactivated on 4 Aug." in text
        assert "officer.jane" in text

    def test_it_is_delivered_only_once(self, tmp_db, ticket):
        from app.service import ChatModel

        tmp_db.update_ticket(ticket["id"], officer_reply="Sorted.")
        assert ChatModel._deliver_officer_reply("conv-1")
        assert ChatModel._deliver_officer_reply("conv-1") == ""

    def test_no_pending_reply_returns_empty(self, tmp_db, ticket):
        from app.service import ChatModel

        assert ChatModel._deliver_officer_reply("conv-1") == ""

    def test_a_broken_ticket_store_does_not_take_out_the_chat(self, tmp_db, monkeypatch):
        from app.service import ChatModel

        def _boom(_):
            raise RuntimeError("db down")

        monkeypatch.setattr("app.database.pending_officer_reply", _boom)
        assert ChatModel._deliver_officer_reply("conv-1") == ""


class TestSLA:
    def test_first_response_is_stamped_once(self, tmp_db, ticket):
        assert not tmp_db.get_ticket(ticket["id"])["first_response_at"]
        tmp_db.update_ticket(ticket["id"], assignee="officer.jane")
        first = tmp_db.get_ticket(ticket["id"])["first_response_at"]
        assert first
        tmp_db.update_ticket(ticket["id"], staff_note="looking into it")
        assert tmp_db.get_ticket(ticket["id"])["first_response_at"] == first

    def test_leaving_a_ticket_open_is_not_a_response(self, tmp_db, ticket):
        tmp_db.update_ticket(ticket["id"], status="open")
        assert not tmp_db.get_ticket(ticket["id"])["first_response_at"]

    def test_resolution_is_stamped(self, tmp_db, ticket):
        tmp_db.update_ticket(ticket["id"], status="resolved")
        assert tmp_db.get_ticket(ticket["id"])["resolved_at"]

    def test_wontfix_counts_as_resolved_for_sla(self, tmp_db, ticket):
        tmp_db.update_ticket(ticket["id"], status="wontfix")
        assert tmp_db.get_ticket(ticket["id"])["resolved_at"]

    def test_stats_count_what_is_still_waiting(self, tmp_db):
        answered = tmp_db.create_ticket(reason="a", conversation_id="c1")
        tmp_db.create_ticket(reason="b", conversation_id="c2")
        tmp_db.update_ticket(answered["id"], assignee="officer.jane")
        stats = tmp_db.sla_stats()
        assert stats["tickets"] == 2
        assert stats["responded"] == 1
        assert stats["awaiting_first_response"] == 1

    def test_medians_are_none_with_no_data(self, tmp_db):
        stats = tmp_db.sla_stats()
        assert stats["median_response_seconds"] is None
        assert stats["median_resolution_seconds"] is None

    def test_the_median_resists_one_stale_ticket(self, tmp_db):
        # A mean would let one ticket left over a holiday weekend make
        # the whole queue look broken.
        conn = tmp_db._get_connection()
        for i, (created, responded) in enumerate(
            [(1000, 1010), (1000, 1020), (1000, 1030), (1000, 900_000)]
        ):
            t = tmp_db.create_ticket(reason=str(i))
            conn.execute(
                "UPDATE tickets SET created_at = ?, first_response_at = ? WHERE id = ?",
                (created, responded, t["id"]),
            )
        conn.commit()
        assert tmp_db.sla_stats(days=10**6)["median_response_seconds"] == 25.0

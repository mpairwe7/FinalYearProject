"""Live escalation events for staff.

The queue polls every 20 seconds — fine on a quiet day, wrong for the
case the pipeline exists to serve: a taxpayer in hardship whose ticket
sits unseen because nobody refreshed.

Two properties matter and neither is obvious from the happy path.

A WebSocket lives on one pod while tickets are created on whichever pod
served the taxpayer, so an in-process hub alone would show an officer
only the tickets that happened to land beside them — the per-replica
failure this codebase has spent several changes removing.

And the event carries no transcript. It fans out to every connected
staff socket, which is the wrong shape for a taxpayer's tax affairs.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time

import pytest

from app.ticket_events import (
    QUEUE_MAXSIZE,
    TicketEventHub,
    _offer,
    build_event,
    hub,
)

TICKET = {
    "id": "tkt-1",
    "priority": "urgent",
    "status": "open",
    "team": "customs",
    "reason": "User explicitly asked for a human",
    "created_at": 1_700_000_000.0,
    "user_query": "my TIN is not working",
    "bot_reply": "I could not resolve that.",
    "transcript": [{"user_message": "my TIN is not working", "bot_reply": "..."}],
    "staff_note": "INTERNAL: caller obstructive",
    "handoff": {
        "topic": "objection_or_dispute",
        "sentiment": "frustration",
        "transfer_style": "warm",
        "summary": "User needs human help.",
    },
}


class TestEventPayload:
    def test_the_transcript_never_goes_out(self):
        event = build_event(TICKET)
        assert "transcript" not in event
        assert "user_query" not in event
        assert "bot_reply" not in event
        assert "my TIN is not working" not in str(event)

    def test_the_internal_note_never_goes_out(self):
        assert "INTERNAL" not in str(build_event(TICKET))

    def test_triage_fields_are_present(self):
        event = build_event(TICKET)
        assert event["id"] == "tkt-1"
        assert event["priority"] == "urgent"
        assert event["team"] == "customs"
        assert event["topic"] == "objection_or_dispute"
        assert event["sentiment"] == "frustration"
        assert event["transfer_style"] == "warm"

    def test_it_is_a_named_event(self):
        assert build_event(TICKET)["event"] == "escalation.created"
        assert build_event(TICKET, "escalation.updated")["event"] == "escalation.updated"

    def test_new_ticket_fields_are_opt_in(self):
        # Allowlist, not blocklist — same discipline as the webhook.
        event = build_event({**TICKET, "officer_reply": "do not broadcast"})
        assert "officer_reply" not in event

    def test_a_bare_ticket_does_not_break_it(self):
        assert build_event({"id": "x"})["id"] == "x"


class TestHubFanOut:
    def test_every_subscriber_gets_the_event(self):
        async def scenario():
            local = TicketEventHub()
            loop = asyncio.get_running_loop()
            first, second = local.subscribe(loop), local.subscribe(loop)
            local.publish_local({"event": "escalation.created", "id": "t1"})
            got_first = await asyncio.wait_for(first.get(), 2)
            got_second = await asyncio.wait_for(second.get(), 2)
            assert got_first == got_second == {"event": "escalation.created", "id": "t1"}

        asyncio.run(scenario())

    def test_publishing_from_another_thread_works(self):
        # Ticket creation happens on the request path, not the loop.
        async def scenario():
            local = TicketEventHub()
            queue = local.subscribe(asyncio.get_running_loop())
            thread = threading.Thread(
                target=local.publish_local, args=({"event": "escalation.created"},)
            )
            thread.start()
            thread.join()
            assert await asyncio.wait_for(queue.get(), 2)

        asyncio.run(scenario())

    def test_unsubscribe_stops_delivery(self):
        async def scenario():
            local = TicketEventHub()
            queue = local.subscribe(asyncio.get_running_loop())
            local.unsubscribe(queue)
            assert local.subscriber_count() == 0
            local.publish_local({"event": "escalation.created"})
            assert queue.empty()

        asyncio.run(scenario())

    def test_a_slow_client_cannot_grow_memory_without_limit(self):
        async def scenario():
            queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
            for i in range(QUEUE_MAXSIZE * 3):
                _offer(queue, {"n": i})
            assert queue.qsize() == QUEUE_MAXSIZE
            # Oldest dropped, so the newest survived — a reconnect
            # re-reads the queue view anyway.
            assert queue.get_nowait()["n"] > 0

        asyncio.run(scenario())

    def test_the_module_hub_is_shared(self):
        async def scenario():
            queue = hub.subscribe(asyncio.get_running_loop())
            try:
                assert hub.subscriber_count() >= 1
            finally:
                hub.unsubscribe(queue)

        asyncio.run(scenario())


class TestPublishIsBestEffort:
    def test_publish_never_raises_without_a_backend(self, monkeypatch):
        from app import ticket_events

        # SQLite: no cross-replica channel, and that is correct — a
        # single node has no other replicas to tell.
        monkeypatch.setattr(ticket_events, "_pg_pool", lambda: None)
        ticket_events.publish(build_event(TICKET))

    def test_a_broken_channel_does_not_take_out_the_request(self, monkeypatch):
        from app import ticket_events

        def _boom():
            raise RuntimeError("pool gone")

        monkeypatch.setattr(ticket_events, "_pg_pool", _boom)
        # The ticket is already committed; a failed broadcast costs a
        # notification, not an escalation.
        ticket_events.publish(build_event(TICKET))


class TestStaffOnlyAccess:
    def test_only_staff_roles_may_watch(self):
        from app.ticket_ws import STAFF_ROLES

        assert "ura_staff" in STAFF_ROLES
        assert "ura_admin" in STAFF_ROLES
        # A taxpayer must never see other people's escalations.
        assert "public" not in STAFF_ROLES
        assert "verified_taxpayer" not in STAFF_ROLES


@pytest.mark.skipif(
    not os.getenv("POSTGRES_DSN"),
    reason="set POSTGRES_DSN to verify cross-replica delivery",
)
class TestCrossReplicaDelivery:
    """The property an in-process hub cannot provide on its own."""

    def test_an_event_published_here_reaches_a_listener_there(self):
        import app.database as db
        from app.ticket_events import listen_for_events, publish

        db.init_db()
        received: list[dict] = []
        stop = threading.Event()

        def listener() -> None:
            for event in listen_for_events(stop):
                received.append(event)
                break

        thread = threading.Thread(target=listener, daemon=True)
        thread.start()
        time.sleep(1.5)  # let LISTEN register

        publish(build_event({**TICKET, "id": "cross-replica"}))
        thread.join(timeout=8)
        stop.set()

        assert received, "event did not cross the replica boundary"
        assert received[0]["id"] == "cross-replica"
        assert "transcript" not in received[0]

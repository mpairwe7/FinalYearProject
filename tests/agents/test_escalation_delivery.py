"""Escalation delivery: notification, de-duplication, and queue order.

Before this the pipeline ended at a database row. Nobody was told, an
urgent ticket sorted below a low-priority one raised later, and a
taxpayer who asked for a human three times produced three tickets for
three officers to each start from scratch.
"""

from __future__ import annotations

import pytest

from app.escalation_notify import (
    PRIORITY_ORDER,
    build_payload,
    meets_priority,
    notify_ticket_created,
)


@pytest.fixture
def ticket() -> dict:
    return {
        "id": "tkt-1",
        "priority": "high",
        "status": "open",
        "reason": "User explicitly asked for a human",
        "conversation_id": "conv-abc",
        "created_at": 1_700_000_000.0,
        "user_query": "my TIN is not working",
        "bot_reply": "I could not resolve that.",
        "transcript": [{"user_message": "my TIN is not working", "bot_reply": "..."}],
        "handoff": {
            "topic": "account_specific",
            "sentiment": "frustration",
            "transfer_style": "warm",
            "turns_before_handoff": 4,
            "summary": "User needs human help with account specific.",
        },
    }


class TestPayloadCarriesTriageDataNotPII:
    def test_the_transcript_never_leaves_the_platform(self, ticket):
        # A webhook is an external system; the conversation is the
        # taxpayer's tax affairs, and erasure cannot reach a third party.
        payload = build_payload(ticket)
        assert "transcript" not in payload
        assert "user_query" not in payload
        assert "bot_reply" not in payload
        assert "my TIN is not working" not in str(payload)

    def test_triage_fields_are_present(self, ticket):
        payload = build_payload(ticket)
        for field in ("id", "priority", "reason", "conversation_id"):
            assert payload[field] == ticket[field]
        assert payload["topic"] == "account_specific"
        assert payload["sentiment"] == "frustration"
        assert payload["transfer_style"] == "warm"

    def test_it_is_a_named_event(self, ticket):
        assert build_payload(ticket)["event"] == "escalation.created"

    def test_new_ticket_fields_are_opt_in(self, ticket):
        # Allowlist, not blocklist: a field added later must be chosen
        # deliberately rather than leak by default.
        ticket["internal_officer_note"] = "do not send"
        assert "internal_officer_note" not in build_payload(ticket)

    def test_a_missing_handoff_does_not_break_the_payload(self, ticket):
        ticket.pop("handoff")
        assert build_payload(ticket)["id"] == "tkt-1"


class TestPriorityFloor:
    def test_the_order_runs_low_to_high(self):
        assert PRIORITY_ORDER == ("low", "normal", "high", "urgent")

    @pytest.mark.parametrize(
        ("priority", "floor", "expected"),
        [
            ("urgent", "high", True),
            ("high", "high", True),
            ("normal", "high", False),
            ("low", "low", True),
            ("normal", "normal", True),
        ],
    )
    def test_floor_comparison(self, priority, floor, expected):
        assert meets_priority(priority, floor) is expected

    def test_an_unknown_priority_is_treated_as_normal(self):
        assert meets_priority("bogus", "normal") is True
        assert meets_priority("bogus", "high") is False


class TestDispatch:
    def test_no_url_configured_means_no_delivery(self, ticket, monkeypatch):
        monkeypatch.delenv("ESCALATION_WEBHOOK_URL", raising=False)
        assert notify_ticket_created(ticket) is False

    def test_a_configured_url_is_delivered(self, ticket, monkeypatch):
        monkeypatch.setenv("ESCALATION_WEBHOOK_URL", "https://ops.example/hook")
        sent: list[dict] = []
        monkeypatch.setattr(
            "app.escalation_notify._post", lambda payload, url: sent.append(payload)
        )
        assert notify_ticket_created(ticket, blocking=True) is True
        assert sent and sent[0]["id"] == "tkt-1"

    def test_a_below_floor_ticket_is_not_sent(self, ticket, monkeypatch):
        monkeypatch.setenv("ESCALATION_WEBHOOK_URL", "https://ops.example/hook")
        monkeypatch.setenv("ESCALATION_WEBHOOK_MIN_PRIORITY", "urgent")
        assert notify_ticket_created(ticket) is False

    def test_a_dead_endpoint_never_raises(self, ticket, monkeypatch):
        # The ticket is already committed; a failed page must degrade to
        # "nobody was notified", not lose the escalation — and it must
        # not surface as an unhandled thread exception either.
        monkeypatch.setenv("ESCALATION_WEBHOOK_URL", "https://ops.example/hook")

        def _boom(payload, url):
            raise RuntimeError("connection refused")

        monkeypatch.setattr("app.escalation_notify._post", _boom)
        assert notify_ticket_created(ticket, blocking=True) is True

    def test_a_transport_error_is_swallowed_by_post_itself(self, ticket, monkeypatch):
        monkeypatch.setenv("ESCALATION_WEBHOOK_URL", "https://ops.example/hook")

        class _Client:
            def __init__(self, *a, **kw):
                raise RuntimeError("no network")

        import httpx

        monkeypatch.setattr(httpx, "Client", _Client)
        assert notify_ticket_created(ticket, blocking=True) is True

    def test_the_token_travels_in_a_header_not_the_url(self, ticket, monkeypatch):
        monkeypatch.setenv("ESCALATION_WEBHOOK_URL", "https://ops.example/hook")
        monkeypatch.setenv("ESCALATION_WEBHOOK_TOKEN", "s3cret-value")
        captured: dict = {}

        class _Response:
            status_code = 200

        class _Client:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, url, json=None, headers=None):
                captured["url"] = url
                captured["headers"] = headers or {}
                return _Response()

        import httpx

        monkeypatch.setattr(httpx, "Client", _Client)
        notify_ticket_created(ticket, blocking=True)
        # An httpx error message includes the request URL, so a token in
        # the query string ends up in logs and tracebacks.
        assert "s3cret-value" not in captured["url"]
        assert captured["headers"]["Authorization"].startswith("Bearer ")

    def test_a_ticket_without_an_id_is_not_sent(self, ticket, monkeypatch):
        monkeypatch.setenv("ESCALATION_WEBHOOK_URL", "https://ops.example/hook")
        ticket["id"] = ""
        assert notify_ticket_created(ticket) is False


class TestDeduplication:
    def test_an_open_ticket_is_found_for_the_conversation(self, tmp_db):
        created = tmp_db.create_ticket(reason="first", conversation_id="conv-1")
        found = tmp_db.find_open_ticket("conv-1")
        assert found is not None
        assert found["id"] == created["id"]

    def test_a_resolved_ticket_does_not_block_a_new_one(self, tmp_db):
        created = tmp_db.create_ticket(reason="first", conversation_id="conv-1")
        tmp_db.update_ticket(created["id"], status="resolved")
        assert tmp_db.find_open_ticket("conv-1") is None

    def test_an_assigned_ticket_still_counts_as_open(self, tmp_db):
        created = tmp_db.create_ticket(reason="first", conversation_id="conv-1")
        tmp_db.update_ticket(created["id"], status="assigned")
        assert tmp_db.find_open_ticket("conv-1") is not None

    def test_other_conversations_are_not_matched(self, tmp_db):
        tmp_db.create_ticket(reason="first", conversation_id="conv-1")
        assert tmp_db.find_open_ticket("conv-2") is None

    def test_no_conversation_id_matches_nothing(self, tmp_db):
        tmp_db.create_ticket(reason="first", conversation_id="conv-1")
        assert tmp_db.find_open_ticket("") is None


class TestQueueOrdering:
    def test_urgent_outranks_a_newer_low_priority_ticket(self, tmp_db):
        tmp_db.create_ticket(reason="urgent one", priority="urgent")
        tmp_db.create_ticket(reason="low one", priority="low")
        assert [t["priority"] for t in tmp_db.list_tickets()][0] == "urgent"

    def test_full_priority_order(self, tmp_db):
        for priority in ("normal", "low", "urgent", "high"):
            tmp_db.create_ticket(reason=priority, priority=priority)
        assert [t["priority"] for t in tmp_db.list_tickets()] == [
            "urgent",
            "high",
            "normal",
            "low",
        ]

    def test_the_oldest_comes_first_within_a_priority(self, tmp_db):
        # A waiting taxpayer moves up rather than being buried.
        first = tmp_db.create_ticket(reason="first", priority="high")
        second = tmp_db.create_ticket(reason="second", priority="high")
        conn = tmp_db._get_connection()
        conn.execute("UPDATE tickets SET created_at = 1000 WHERE id = ?", (first["id"],))
        conn.execute("UPDATE tickets SET created_at = 2000 WHERE id = ?", (second["id"],))
        conn.commit()
        assert [t["id"] for t in tmp_db.list_tickets()] == [first["id"], second["id"]]

    def test_priority_filter(self, tmp_db):
        tmp_db.create_ticket(reason="a", priority="urgent")
        tmp_db.create_ticket(reason="b", priority="low")
        rows = tmp_db.list_tickets(priority="urgent")
        assert len(rows) == 1
        assert rows[0]["priority"] == "urgent"

    def test_status_and_priority_filters_combine(self, tmp_db):
        keep = tmp_db.create_ticket(reason="a", priority="urgent")
        other = tmp_db.create_ticket(reason="b", priority="urgent")
        tmp_db.update_ticket(other["id"], status="resolved")
        rows = tmp_db.list_tickets(status="open", priority="urgent")
        assert [t["id"] for t in rows] == [keep["id"]]

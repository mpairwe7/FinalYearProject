"""Tests for the Phase-D ticket queue (database + escalate tool)."""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Direct CRUD
# ---------------------------------------------------------------------------
class TestTicketCRUD:
    def test_create_ticket_returns_structured_dict(self, tmp_db):
        t = tmp_db.create_ticket(
            reason="user asked for help",
            user_query="what's the VAT rate?",
            bot_reply="",
            session_id="s1",
            priority="normal",
        )
        assert "id" in t
        assert len(t["id"]) == 36        # UUID format
        assert t["status"] == "open"
        assert t["priority"] == "normal"
        assert t["reason"] == "user asked for help"

    def test_list_tickets_newest_first(self, tmp_db):
        t1 = tmp_db.create_ticket(reason="first")
        t2 = tmp_db.create_ticket(reason="second")
        t3 = tmp_db.create_ticket(reason="third")
        rows = tmp_db.list_tickets(status="open", limit=10)
        assert len(rows) == 3
        # Ordered newest-first, so t3 is index 0
        assert rows[0]["id"] == t3["id"]
        assert rows[-1]["id"] == t1["id"]

    def test_list_tickets_status_filter(self, tmp_db):
        t1 = tmp_db.create_ticket(reason="a")
        t2 = tmp_db.create_ticket(reason="b")
        tmp_db.update_ticket(t1["id"], status="resolved")
        open_only = tmp_db.list_tickets(status="open")
        assert len(open_only) == 1
        assert open_only[0]["id"] == t2["id"]
        resolved_only = tmp_db.list_tickets(status="resolved")
        assert len(resolved_only) == 1
        assert resolved_only[0]["id"] == t1["id"]

    def test_list_tickets_pagination(self, tmp_db):
        for i in range(5):
            tmp_db.create_ticket(reason=f"ticket-{i}")
        page1 = tmp_db.list_tickets(limit=2, offset=0)
        page2 = tmp_db.list_tickets(limit=2, offset=2)
        page3 = tmp_db.list_tickets(limit=2, offset=4)
        assert len(page1) == 2
        assert len(page2) == 2
        assert len(page3) == 1
        # No overlap
        ids = {t["id"] for t in page1 + page2 + page3}
        assert len(ids) == 5

    def test_get_ticket_returns_full_row(self, tmp_db):
        created = tmp_db.create_ticket(reason="r", user_query="q")
        fetched = tmp_db.get_ticket(created["id"])
        assert fetched is not None
        assert fetched["id"] == created["id"]
        assert fetched["reason"] == "r"
        assert fetched["user_query"] == "q"

    def test_get_ticket_unknown_returns_none(self, tmp_db):
        assert tmp_db.get_ticket("00000000-0000-0000-0000-000000000000") is None

    def test_update_ticket_status(self, tmp_db):
        t = tmp_db.create_ticket(reason="r")
        assert tmp_db.update_ticket(t["id"], status="assigned") is True
        detail = tmp_db.get_ticket(t["id"])
        assert detail["status"] == "assigned"

    def test_update_ticket_assignee_and_note(self, tmp_db):
        t = tmp_db.create_ticket(reason="r")
        ok = tmp_db.update_ticket(
            t["id"],
            status="assigned",
            assignee="officer@ura.go.ug",
            staff_note="reviewing",
        )
        assert ok
        d = tmp_db.get_ticket(t["id"])
        assert d["assignee"] == "officer@ura.go.ug"
        assert d["staff_note"] == "reviewing"

    def test_update_ticket_priority(self, tmp_db):
        t = tmp_db.create_ticket(reason="r", priority="normal")
        assert tmp_db.update_ticket(t["id"], priority="urgent") is True
        assert tmp_db.get_ticket(t["id"])["priority"] == "urgent"

    def test_update_ticket_no_op_returns_false(self, tmp_db):
        t = tmp_db.create_ticket(reason="r")
        # No fields to update
        assert tmp_db.update_ticket(t["id"]) is False

    def test_update_rejects_invalid_status(self, tmp_db):
        t = tmp_db.create_ticket(reason="r")
        assert tmp_db.update_ticket(t["id"], status="garbage") is False
        assert tmp_db.get_ticket(t["id"])["status"] == "open"

    def test_update_rejects_invalid_priority(self, tmp_db):
        t = tmp_db.create_ticket(reason="r")
        assert tmp_db.update_ticket(t["id"], priority="super-urgent") is False


class TestTicketValidation:
    def test_invalid_priority_coerced_to_normal(self, tmp_db):
        t = tmp_db.create_ticket(reason="r", priority="extreme")
        assert t["priority"] == "normal"

    def test_valid_priorities_accepted(self, tmp_db):
        for p in ["low", "normal", "high", "urgent"]:
            t = tmp_db.create_ticket(reason=f"r-{p}", priority=p)
            assert t["priority"] == p


class TestTicketStats:
    def test_empty_stats(self, tmp_db):
        s = tmp_db.ticket_stats(days=1)
        assert s["total"] == 0
        assert s["open"] == 0
        assert s["assigned"] == 0
        assert s["resolved"] == 0
        assert s["wontfix"] == 0

    def test_stats_with_mixed_statuses(self, tmp_db):
        ids = [tmp_db.create_ticket(reason=f"r{i}")["id"] for i in range(5)]
        tmp_db.update_ticket(ids[0], status="assigned")
        tmp_db.update_ticket(ids[1], status="assigned")
        tmp_db.update_ticket(ids[2], status="resolved")
        tmp_db.update_ticket(ids[3], status="wontfix")
        # ids[4] stays open

        s = tmp_db.ticket_stats(days=1)
        assert s["total"] == 5
        assert s["open"] == 1
        assert s["assigned"] == 2
        assert s["resolved"] == 1
        assert s["wontfix"] == 1

    def test_by_priority_breakdown(self, tmp_db):
        tmp_db.create_ticket(reason="h1", priority="high")
        tmp_db.create_ticket(reason="h2", priority="high")
        tmp_db.create_ticket(reason="n1", priority="normal")
        tmp_db.create_ticket(reason="u1", priority="urgent")

        s = tmp_db.ticket_stats(days=1)
        assert s["by_priority"] == {"high": 2, "normal": 1, "urgent": 1}


# ---------------------------------------------------------------------------
# escalate_to_human tool — round-trips through the registry + DB
# ---------------------------------------------------------------------------
class TestEscalateTool:
    def test_tool_creates_ticket_via_registry(self, tmp_db, fresh_registry):
        result = fresh_registry.call("escalate_to_human", {
            "reason": "multi-country customs dispute",
            "priority": "high",
            "summary": "importing vehicles from Kenya and Tanzania",
        })
        assert result["ok"] is True
        assert result["priority"] == "high"
        assert len(result["ticket_id"]) == 36

        # The ticket should now be visible in the DB
        rows = tmp_db.list_tickets(status="open")
        assert len(rows) == 1
        assert rows[0]["id"] == result["ticket_id"]
        assert rows[0]["priority"] == "high"
        assert "multi-country" in rows[0]["reason"]

    def test_default_priority_normal(self, tmp_db, fresh_registry):
        result = fresh_registry.call("escalate_to_human",
                                     {"reason": "general help"})
        assert result["ok"] is True
        assert result["priority"] == "normal"

    def test_returns_user_facing_message(self, tmp_db, fresh_registry):
        result = fresh_registry.call("escalate_to_human", {"reason": "test"})
        assert result["ok"] is True
        msg = result["message"]
        assert "ticket" in msg.lower()
        # The first 8 chars of the UUID should be in the message
        assert result["ticket_id"][:8] in msg

    def test_tool_schema_is_medium_risk(self, fresh_registry):
        tool = fresh_registry.get("escalate_to_human")
        assert tool.schema.risk == "medium"
        assert "reason" in tool.schema.parameters["required"]

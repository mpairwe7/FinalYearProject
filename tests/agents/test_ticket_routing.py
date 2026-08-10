"""Assignment routing — an officer should see their own queue.

`_handoff_topic` has classified every escalation since the handoff
packet was introduced, and nothing acted on it: customs disputes sat
next to TIN registrations and officers triaged by reading each row.

Routing is deliberately boring — a topic-to-team map with an env
override per topic, so a deployment can match its own org chart without
a code change, and an unrecognised topic still lands somewhere rather
than nowhere.
"""

from __future__ import annotations

import pytest

from app.escalation_notify import (
    FALLBACK_TEAM,
    build_payload,
    known_teams,
    team_for_topic,
)


class TestTopicToTeam:
    @pytest.mark.parametrize(
        ("topic", "team"),
        [
            ("objection_or_dispute", "disputes"),
            ("account_specific", "taxpayer_accounts"),
            ("customs", "customs"),
            ("registration", "registration"),
            ("general_tax_support", "general"),
        ],
    )
    def test_each_classified_topic_has_an_owner(self, topic, team):
        assert team_for_topic(topic) == team

    def test_an_unrecognised_topic_still_lands_somewhere(self):
        # Never drop a ticket on the floor because the classifier
        # returned something the map has not caught up with.
        assert team_for_topic("newly_invented_topic") == FALLBACK_TEAM
        assert team_for_topic("") == FALLBACK_TEAM

    def test_case_and_padding_do_not_matter(self):
        assert team_for_topic("  CUSTOMS  ") == "customs"

    def test_a_deployment_can_override_a_topic(self, monkeypatch):
        monkeypatch.setenv("ESCALATION_TEAM_CUSTOMS", "border-ops")
        assert team_for_topic("customs") == "border-ops"
        # Others are untouched.
        assert team_for_topic("registration") == "registration"

    def test_known_teams_reflects_overrides(self, monkeypatch):
        monkeypatch.setenv("ESCALATION_TEAM_CUSTOMS", "border-ops")
        teams = known_teams()
        assert "border-ops" in teams
        assert "customs" not in teams

    def test_the_notification_says_which_team(self):
        payload = build_payload({"id": "t1", "priority": "high", "team": "customs"})
        assert payload["team"] == "customs"


class TestQueueFiltering:
    def _seed(self, db):
        for topic in ("customs", "objection_or_dispute", "registration"):
            db.create_ticket(reason=topic, team=team_for_topic(topic), priority="high")

    def test_a_team_sees_only_its_own(self, tmp_db):
        self._seed(tmp_db)
        rows = tmp_db.list_tickets(team="customs")
        assert [r["reason"] for r in rows] == ["customs"]

    def test_no_team_filter_returns_everything(self, tmp_db):
        self._seed(tmp_db)
        assert len(tmp_db.list_tickets()) == 3

    def test_team_combines_with_status(self, tmp_db):
        self._seed(tmp_db)
        rows = tmp_db.list_tickets(status="open", team="disputes")
        assert [r["reason"] for r in rows] == ["objection_or_dispute"]

    def test_team_combines_with_priority(self, tmp_db):
        self._seed(tmp_db)
        assert len(tmp_db.list_tickets(priority="high", team="customs")) == 1
        assert len(tmp_db.list_tickets(priority="low", team="customs")) == 0

    def test_an_unknown_team_matches_nothing(self, tmp_db):
        self._seed(tmp_db)
        assert tmp_db.list_tickets(team="nonexistent") == []

    def test_the_team_survives_into_the_queue_row(self, tmp_db):
        # It was dropped on the first attempt: list_tickets has an
        # explicit column list, so a new column vanishes silently.
        tmp_db.create_ticket(reason="a", team="customs")
        assert tmp_db.list_tickets()[0]["team"] == "customs"

    def test_the_team_survives_into_the_detail_view(self, tmp_db):
        created = tmp_db.create_ticket(reason="a", team="customs")
        assert tmp_db.get_ticket(created["id"])["team"] == "customs"

    def test_a_ticket_raised_without_a_team_is_still_listed(self, tmp_db):
        tmp_db.create_ticket(reason="legacy")
        assert len(tmp_db.list_tickets()) == 1
        assert tmp_db.list_tickets()[0]["team"] == ""


class TestRoutingIsAppliedAtEscalation:
    """Enabled via the env var, not the flag singleton.

    ``test_integration.py`` calls ``importlib.reload(app.flags)``, which
    replaces the singleton — so a ``flags.set()`` here lands on a
    different object from the one ``service.py`` bound at import time,
    and the flag reads False. The env var is read on every call and is
    immune to that.
    """

    def test_the_service_routes_on_the_classified_topic(self, tmp_db, monkeypatch):
        from app.service import ChatModel

        monkeypatch.setenv("FLAG_TICKET_QUEUE", "true")
        model = ChatModel.__new__(ChatModel)
        ticket_id = ChatModel._maybe_create_ticket(
            model,
            reason="I want to dispute my assessment",
            user_query="I want to dispute my assessment",
            bot_reply="A human will follow up.",
            session_id="s1",
            conversation_id="c1",
            handoff={"topic": "objection_or_dispute", "priority": "high"},
        )
        assert ticket_id
        assert tmp_db.get_ticket(ticket_id)["team"] == "disputes"

    def test_an_unclassified_handoff_still_gets_a_team(self, tmp_db, monkeypatch):
        from app.service import ChatModel

        monkeypatch.setenv("FLAG_TICKET_QUEUE", "true")
        model = ChatModel.__new__(ChatModel)
        ticket_id = ChatModel._maybe_create_ticket(
            model,
            reason="something else",
            user_query="q",
            bot_reply="a",
            session_id="s2",
            conversation_id="c2",
            handoff={"priority": "normal"},
        )
        assert tmp_db.get_ticket(ticket_id)["team"] == FALLBACK_TEAM

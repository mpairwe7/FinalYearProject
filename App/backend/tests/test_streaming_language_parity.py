"""The streaming core (SSE and WebSocket) treats Luganda and Swahili like REST.

``generate_retrieval_only`` is what the web and WebSocket clients use. These
pin the parity it lacked:

* the answer language comes from ``app.language_state`` (a default ``en`` from
  the client is not a choice);
* the English-pattern routers — workflows, topic tracking, the FAQ
  authorization gate, the input guard — read the English form of the turn;
* the supervisor's ESCALATE route is reachable again (it sat behind a
  ``return`` from 2026-09-21);
* an officer's reply is delivered before the bot answers, as in REST.
"""

from __future__ import annotations

import unittest
import unittest.mock as mock

LG_TIN = "Nnyinza ntya okwewandiisa okufuna TIN?"
EN_TIN = "How can I register to get a TIN?"


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from app import database as db

        db.init_db()
        from app import service

        cls.service = service
        cls.model = service.ChatModel()

    def _patches(self, **extra: object) -> list[mock._patch]:  # type: ignore[name-defined]
        service = self.service
        patches = [
            mock.patch.object(service, "english_retrieval_query", side_effect=self._translate),
            mock.patch.object(service, "_simple_search", return_value=[]),
            mock.patch.object(service, "needs_clarification", return_value=""),
            mock.patch.object(service.ChatModel, "_priority_faq_hits", return_value=[]),
            mock.patch.object(self.model._cache, "get", return_value=None),
            mock.patch.object(self.model._cache, "put"),
        ]
        for name, value in extra.items():
            patches.append(mock.patch.object(service.ChatModel, name, value))
        return patches

    @staticmethod
    def _translate(text: str, locale: str | None) -> str:
        return EN_TIN if text == LG_TIN else text

    def _run(self, patches: list[mock._patch], **kwargs: object) -> dict:  # type: ignore[name-defined]
        for p in patches:
            p.start()
        self.addCleanup(mock.patch.stopall)
        return self.model.generate_retrieval_only(**kwargs)


class LanguageResolutionTest(_Base):
    def test_default_english_from_the_client_still_answers_luganda(self) -> None:
        language: dict[str, str] = {}
        out = self._run(
            self._patches(_maybe_handle_workflow=mock.Mock(return_value=None)),
            message=LG_TIN,
            locale="en",
            locale_explicit=False,
            language_out=language,
        )
        self.assertEqual(out["locale"], "lg")
        self.assertEqual(language["source"], "detected")

    def test_picked_english_is_honoured(self) -> None:
        language: dict[str, str] = {}
        out = self._run(
            self._patches(_maybe_handle_workflow=mock.Mock(return_value=None)),
            message=LG_TIN,
            locale="en",
            locale_explicit=True,
            language_out=language,
        )
        self.assertEqual(out["locale"], "en")
        self.assertEqual(language["source"], "client_explicit")


class EnglishFormRoutingTest(_Base):
    def test_workflow_router_reads_the_english_form(self) -> None:
        workflow = mock.Mock(return_value=None)
        self._run(self._patches(_maybe_handle_workflow=workflow), message=LG_TIN, locale="en", locale_explicit=False)
        self.assertEqual(workflow.call_args.kwargs["message"], EN_TIN)

    def test_topic_tracking_reads_the_english_form(self) -> None:
        bind = mock.Mock(side_effect=lambda **kw: (kw["retrieval_query"], kw["binding_query"], kw["personalization"]))
        self._run(
            self._patches(
                _maybe_handle_workflow=mock.Mock(return_value=None),
                _bind_conversation_topic=bind,
            ),
            message=LG_TIN,
            locale="en",
            locale_explicit=False,
        )
        self.assertEqual(bind.call_args.kwargs["message"], EN_TIN)
        self.assertEqual(bind.call_args.kwargs["binding_query"], EN_TIN)

    def test_input_guard_also_reads_the_english_form(self) -> None:
        from app.guardrails import GuardResult

        calls: list[str] = []

        def check(text: str) -> GuardResult:
            calls.append(text)
            if text == EN_TIN:
                return GuardResult(allowed=False, reason="blocked-in-english")
            return GuardResult(allowed=True)

        with mock.patch.object(self.model._input_guard, "check", side_effect=check):
            out = self._run(
                self._patches(_maybe_handle_workflow=mock.Mock(return_value=None)),
                message=LG_TIN,
                locale="en",
                locale_explicit=False,
            )
        self.assertEqual(calls, [LG_TIN, EN_TIN])
        self.assertEqual(out["retrieval_mode"], "blocked")


class StreamingRouteParityTest(_Base):
    def test_supervisor_escalation_route_is_reachable(self) -> None:
        from app.agents import AgentRoute

        decision = mock.Mock(route=AgentRoute.ESCALATE, reason="taxpayer asked for an officer")
        decision.clarification_question = ""
        decision.suggested_tools = []
        with mock.patch.object(self.service.supervisor, "classify", return_value=decision), \
             mock.patch.object(self.service.flags, "is_enabled", side_effect=lambda name: name in {"agentic_mode", "query_rewrite"}), \
             mock.patch.object(self.service.ChatModel, "_maybe_create_ticket", return_value="tic-1"):
            out = self._run(
                self._patches(_maybe_handle_workflow=mock.Mock(return_value=None)),
                message="I want to dispute my assessment with an officer",
                locale="en",
                locale_explicit=False,
            )
        self.assertEqual(out["retrieval_mode"], "escalated")
        self.assertTrue(out["escalation_required"])

    def test_officer_reply_is_delivered_before_the_bot_answers(self) -> None:
        out = self._run(
            self._patches(
                _maybe_handle_workflow=mock.Mock(return_value=None),
                _deliver_officer_reply=mock.Mock(return_value="Officer Nakato: your TIN is ready."),
            ),
            message="Any update on my case?",
            locale="en",
            locale_explicit=False,
        )
        self.assertEqual(out["retrieval_mode"], "officer_reply")
        self.assertTrue(out["_short_circuit"])
        self.assertIn("Officer Nakato", out["reply"])


if __name__ == "__main__":
    unittest.main()

"""Long-term memory lifecycle (G121).

* One episode per conversation, derived from the whole conversation so far:
  its topics in the order they were named and an honest turn count. It used
  to be rewritten by every turn from that turn alone (``turn_count`` stuck at 2).
* Topic tags match whole words ("private" is not VAT, "city" is not CIT).
* Facts are read from the English form of a Luganda or Swahili turn too, with
  provenance, and from common Swahili/Luganda trade words.
* A newer value of a single-valued fact supersedes the old one, which keeps the
  time it stopped being true (``invalidated_at``).
* Working memory records the topic, not the role that answered it.
* Streamed turns are written to memory once final (they never were), and a call
  turn heard below the ASR confidence floor is not written at all.
"""

from __future__ import annotations

import sqlite3
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app import database as db
from app.memory.extractor import FactExtractor
from app.memory.semantic import SemanticMemory, UserFact
from app.memory.service import MemoryService, _guess_topic_tag, _ordered_topic_tags, topic_label


class TopicTagTest(unittest.TestCase):
    def test_whole_words_only(self) -> None:
        for text in ("I run a private company", "Is the city council allowed to charge me?", "Any tips for getting started?"):
            with self.subTest(text=text):
                self.assertEqual(_guess_topic_tag(text), "general")

    def test_named_topics(self) -> None:
        cases = {
            "What is the VAT rate?": "vat",
            "How do I register for a TIN?": "registration",
            "Kodi ya zuio ni nini?": "withholding",
            "How is PAYE calculated?": "paye",
        }
        for text, tag in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_guess_topic_tag(text), tag)

    def test_order_of_first_mention_and_general_only_alone(self) -> None:
        self.assertEqual(
            _ordered_topic_tags(["Omusolo gwange", "What is VAT?", "And PAYE?", "VAT again"]),
            ["vat", "paye"],
        )
        self.assertEqual(_ordered_topic_tags(["Omusolo gwange"]), ["general_tax"])
        self.assertEqual(topic_label("How is PAYE calculated?"), "PAYE")
        self.assertEqual(topic_label("Hello there"), "")


class EpisodePerConversationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        db.init_db()

    def setUp(self) -> None:
        self.user = f"mem-user-{uuid.uuid4().hex[:8]}"
        self.conversation = uuid.uuid4().hex
        self.memory = MemoryService()
        patcher = patch.object(MemoryService, "_has_consent", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _log(self, user_message: str, user_message_en: str = "") -> None:
        db.log_conversation(
            session_id=None,
            conversation_id=self.conversation,
            user_message=user_message,
            bot_reply="...",
            user_id=self.user,
            user_message_en=user_message_en,
        )

    def _episode(self) -> dict:
        rows = self.memory.episodic.list_for_user(self.user)
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_the_episode_grows_with_the_conversation(self) -> None:
        self.memory.absorb_conversation(self.user, self.conversation, [{"role": "user", "content": "What is the VAT rate?"}])
        self._log("What is the VAT rate?")
        self.memory.absorb_conversation(self.user, self.conversation, [{"role": "user", "content": "How is PAYE calculated?"}])
        self._log("How is PAYE calculated?")
        self.memory.absorb_conversation(self.user, self.conversation, [{"role": "user", "content": "ok thanks"}])

        episode = self._episode()
        self.assertEqual(episode["topic_tag"], "vat")
        self.assertEqual(episode["summary"], "Discussed VAT and PAYE.")
        self.assertEqual(episode["turn_count"], 3)

    def test_luganda_turns_are_tagged_from_their_english_form(self) -> None:
        self._log("Omusolo ogukwatibwa nga tennasasulwa kye ki?", "What is withholding tax?")
        self.memory.absorb_conversation(
            self.user,
            self.conversation,
            [{"role": "user", "content": "Nnyinza ntya okwewandiisa okufuna TIN?", "content_en": "How can I register to get a TIN?"}],
        )
        episode = self._episode()
        self.assertEqual(episode["summary"], "Discussed withholding tax and TIN registration.")
        self.assertEqual(episode["turn_count"], 2)

    def test_long_conversation_preserves_first_topic_beyond_25_turns(self) -> None:
        # First turn establishes VAT topic
        self._log("What is VAT?", "What is VAT?")
        # Next 26 turns are general tax queries
        for i in range(26):
            self._log(f"Question {i}", f"Question {i}")
        # Latest turn introduces PAYE
        self.memory.absorb_conversation(
            self.user,
            self.conversation,
            [{"role": "user", "content": "How do I calculate PAYE?"}],
        )
        episode = self._episode()
        # VAT must still be in the summary because all >25 turns are looked up
        self.assertIn("VAT", episode["summary"])
        self.assertEqual(episode["turn_count"], 28)


class FactsFromTheEnglishFormTest(unittest.TestCase):
    def test_english_form_facts_carry_provenance_and_less_confidence(self) -> None:
        facts = {
            (c.category, c.object_value): c
            for c in FactExtractor().extract(
                [{"role": "user", "content": "Nze ndi musuubuzi", "content_en": "I am a sole trader in Kampala"}]
            )
        }
        sole = facts[("taxpayer_type", "sole_trader")]
        self.assertTrue(sole.rule_id.endswith("+en"))
        self.assertAlmostEqual(sole.confidence, 0.85 * 0.95, places=4)

    def test_swahili_and_luganda_trade_words(self) -> None:
        for text in ("Nina duka la rejareja.", "Nnina edduuka."):
            with self.subTest(text=text):
                categories = {(c.category, c.object_value) for c in FactExtractor().extract([{"role": "user", "content": text}])}
                self.assertIn(("industry", "retail"), categories)

    def test_absorbed_english_form_fact_records_its_provenance(self) -> None:
        db.init_db()
        memory = MemoryService()
        user = f"mem-prov-{uuid.uuid4().hex[:8]}"
        with patch.object(MemoryService, "_has_consent", return_value=True):
            memory.absorb_conversation(
                user,
                uuid.uuid4().hex,
                [{"role": "user", "content": "Nze ndi musuubuzi", "content_en": "I am a sole trader"}],
            )
            facts = memory.semantic.read(user_id=user)
        self.assertEqual([(f.object_value, f.extractor_model) for f in facts], [("sole_trader", "rules-v1+en")])


class RegistrationsAreStatedNotAskedTest(unittest.TestCase):
    """Live 2026-10-06: "Do I need to pay PAYE for my employees?" was stored as
    "registered for PAYE"."""

    CASES = {
        "Do I need to pay PAYE for my employees?": set(),
        "Am I registered for VAT?": set(),
        "What is corporation tax?": set(),
        "Je, nimesajiliwa kwa VAT?": set(),
        "We deduct PAYE for our staff every month.": {("registered_tax", "paye")},
        "I am registered for VAT, what do I file next?": {("registered_vat", "vat")},
        "I'm registered for withholding tax": {("registered_tax", "wht")},
        "Nimesajiliwa kwa VAT.": {("registered_vat", "vat")},
    }

    def test_only_statements_register(self) -> None:
        for text, expected in self.CASES.items():
            with self.subTest(text=text):
                found = {
                    (c.category, c.object_value)
                    for c in FactExtractor().extract([{"role": "user", "content": text}])
                    if c.category.startswith("registered")
                }
                self.assertEqual(found, expected)


class FactValidityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        db.init_db()

    def _fact(self, user: str, value: str) -> UserFact:
        return UserFact(
            fact_id="",
            user_id=user,
            tenant_id="default",
            category="taxpayer_type",
            subject="user",
            predicate="is_a",
            object_value=value,
            confidence=0.85,
            extracted_at=0,
        )

    def test_a_new_value_supersedes_and_the_old_keeps_when_it_stopped(self) -> None:
        memory = SemanticMemory()
        user = f"mem-valid-{uuid.uuid4().hex[:8]}"
        memory.write(self._fact(user, "sole_trader"))
        memory.write(self._fact(user, "company"))
        self.assertEqual([f.object_value for f in memory.read(user_id=user)], ["company"])
        rows = {r["object_value"]: r for r in db.query_all("SELECT * FROM user_facts WHERE user_id = ?", (user,))}
        self.assertIsNotNone(rows["sole_trader"]["superseded_by"])
        self.assertIsNotNone(rows["sole_trader"]["invalidated_at"])
        self.assertIsNone(rows["company"]["invalidated_at"])

        # Said again: the old value is current again.
        memory.write(self._fact(user, "sole_trader"))
        self.assertEqual([f.object_value for f in memory.read(user_id=user)], ["sole_trader"])
        rows = {r["object_value"]: r for r in db.query_all("SELECT * FROM user_facts WHERE user_id = ?", (user,))}
        self.assertIsNone(rows["sole_trader"]["invalidated_at"])
        self.assertIsNotNone(rows["company"]["invalidated_at"])


class PersistTurnTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        db.init_db()
        from app import service

        cls.service = service
        cls.model = service.ChatModel()

    def test_working_memory_records_the_topic_not_the_role(self) -> None:
        memsvc = MagicMock()
        with patch.object(self.service, "get_memory_service", return_value=memsvc):
            self.model._persist_personalization_turn(
                user_id="u1",
                tenant_id="default",
                conversation_id="c1",
                message="Omusolo gwa PAYE gubalibwa gutya?",
                message_en="How is PAYE calculated?",
                reply="...",
                agent_role="rag_answerer",
                personalization={"consent_granted": True},
            )
        self.assertEqual(memsvc.update_working.call_args.kwargs["last_topic"], "PAYE")
        turns = memsvc.absorb_conversation.call_args.args[2]
        self.assertEqual(turns[0]["content_en"], "How is PAYE calculated?")

    def test_a_turn_not_allowed_is_not_written(self) -> None:
        memsvc = MagicMock()
        with patch.object(self.service, "get_memory_service", return_value=memsvc):
            self.model._persist_personalization_turn(
                user_id="u1",
                tenant_id="default",
                conversation_id="c1",
                message="How is PAYE calculated?",
                reply="...",
                agent_role="rag_answerer",
                personalization={"consent_granted": True},
                allowed=False,
            )
        memsvc.absorb_conversation.assert_not_called()
        memsvc.update_working.assert_not_called()


class MemoryStartsForANewConsentedUserTest(unittest.TestCase):
    """Live 2026-10-06: a consented user with no profile never got a memory write."""

    def test_consent_state_is_returned_with_nothing_to_inject(self) -> None:
        from app import service
        from app.memory.service import MemoryReadResult

        db.init_db()
        model = service.ChatModel()
        empty = MemoryReadResult(facts=[], episodic=[], working=None, consent_granted=True)
        with patch.object(service.flags, "is_enabled", side_effect=lambda name, *a, **k: name == "memory_enabled"), \
             patch.object(MemoryService, "read_all", return_value=empty), \
             patch.object(service.db, "get_user_profile", return_value=None):
            state = model._load_personalization_state(f"new-{uuid.uuid4().hex[:8]}")
        self.assertIsNotNone(state)
        self.assertTrue(state["consent_granted"])
        self.assertEqual(state["prompt_context"], "")

    def test_no_consent_still_means_no_state(self) -> None:
        from app import service
        from app.memory.service import MemoryReadResult

        db.init_db()
        model = service.ChatModel()
        refused = MemoryReadResult(facts=[], episodic=[], working=None, consent_granted=False)
        with patch.object(service.flags, "is_enabled", side_effect=lambda name, *a, **k: name == "memory_enabled"), \
             patch.object(MemoryService, "read_all", return_value=refused):
            self.assertIsNone(model._load_personalization_state("someone"))


class StreamedTurnMemoryTest(unittest.TestCase):
    def test_a_consented_streamed_turn_is_written_once_final(self) -> None:
        from app import service

        model = MagicMock()
        payload = {
            "result": {"_memory_consent": True, "conversation_id": "c9", "_english_message": "What is VAT?", "agent_role": "rag_answerer"},
            "full_reply": "VAT ni 18%.",
            "elapsed_ms": 10.0,
        }
        service._finish_streamed_turn(
            model, payload, channel="sse", failed=False, message="VAT kye ki?", session_id="s", user_id="u9", tenant_id="default"
        )
        kwargs = model._persist_personalization_turn.call_args.kwargs
        self.assertEqual((kwargs["message"], kwargs["message_en"], kwargs["reply"]), ("VAT kye ki?", "What is VAT?", "VAT ni 18%."))

    def test_not_written_twice_or_without_consent(self) -> None:
        from app import service

        for result in ({"_memory_consent": True, "_memory_absorbed": True}, {}):
            model = MagicMock()
            service._finish_streamed_turn(
                model,
                {"result": result, "full_reply": "x", "elapsed_ms": 1.0},
                channel="sse",
                failed=False,
                message="m",
                session_id="s",
                user_id="u",
                tenant_id="default",
            )
            model._persist_personalization_turn.assert_not_called()


class CallTurnConfidenceGateTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app.receptionist.brain import UraReceptionistBrain
        from app.receptionist.state import CallRoom, CallState
        from app.receptionist.store import init_receptionist_schema

        db.init_db()
        init_receptionist_schema()
        call_id = f"call_mem_{uuid.uuid4().hex[:8]}"
        state = CallState(call_id=call_id, conversation_id=f"conv_{call_id}", user_id="taxpayer_1", mode="ai")
        self.chat_model = MagicMock()
        self.chat_model.generate.return_value = {
            "reply": "You can register for a TIN using the URA portal.",
            "sources": ["URA Portal Guide"],
            "faithfulness_score": 0.95,
        }
        self.brain = UraReceptionistBrain(room=CallRoom(call_id=call_id, state=state), chat_model=self.chat_model)
        self.brain.push_frame = AsyncMock()

    async def test_low_confidence_turn_is_answered_but_not_remembered(self) -> None:
        await self.brain._generate_and_speak("How do I register for a TIN?", 0.3)
        self.assertIs(self.chat_model.generate.call_args.kwargs["memory_write"], False)

    async def test_confident_turn_is_remembered(self) -> None:
        await self.brain._generate_and_speak("How do I register for a TIN?", 0.9)
        self.assertIs(self.chat_model.generate.call_args.kwargs["memory_write"], True)


class SchemaMigrationErrorHandlingTest(unittest.TestCase):
    def test_re_raises_non_duplicate_migration_errors(self) -> None:
        sem = SemanticMemory.__new__(SemanticMemory)

        with patch.object(db, "execute", side_effect=sqlite3.OperationalError("database is locked")), \
             pytest.raises(sqlite3.OperationalError):
            sem._init_schema()

    def test_suppresses_duplicate_column_error(self) -> None:
        sem = SemanticMemory.__new__(SemanticMemory)

        with patch.object(db, "execute_script", return_value=None), \
             patch.object(db, "execute", side_effect=[
                 sqlite3.OperationalError("duplicate column name: invalidated_at"),  # ALTER TABLE
                 None,  # CREATE UNIQUE INDEX
             ]), \
             patch.object(db, "query_all", return_value=[]):
            # Should not raise
            sem._init_schema()


if __name__ == "__main__":
    unittest.main()

"""Model-facing history in English for Luganda and Swahili turns (G119, G120).

A lg/sw turn is stored with its English form beside what the taxpayer typed
and was shown. The generator answers in English and the rewriter, entity
extraction and summary match English patterns, so they read the English;
transcripts, exports and the audit trail keep the original. The rolling
summary covers every turn the prompt does not replay verbatim.
"""

from __future__ import annotations

import unittest
import unittest.mock as mock
import uuid

from app import database as db
from app.context_manager import (
    PROMPT_VERBATIM_TURNS,
    RollingContextManager,
    english_view,
    normalize_history_turns,
)

LG_TIN = "Nnyinza ntya okwewandiisa okufuna TIN?"
EN_TIN = "How can I register to get a TIN?"
LG_REPLY = "Osobola okwewandiisa ku mukutu gwa URA."
EN_REPLY = "You can register on the URA portal."


class StoredEnglishFormsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        db.init_db()

    def test_log_and_read_back_the_english_forms(self) -> None:
        conversation = uuid.uuid4().hex
        session = f"pivot-{uuid.uuid4().hex[:8]}"
        db.log_conversation(
            session_id=session,
            conversation_id=conversation,
            user_message=LG_TIN,
            bot_reply=LG_REPLY,
            locale="lg",
            user_message_en=EN_TIN,
            bot_reply_en=EN_REPLY,
        )
        db.log_conversation(
            session_id=session,
            conversation_id=conversation,
            user_message="What is VAT?",
            bot_reply="VAT is 18%.",
            locale="en",
        )
        turns = db.get_recent_turns(session_id=session, conversation_id=conversation, limit=5)
        self.assertEqual(turns[0]["user_message"], LG_TIN)
        self.assertEqual(turns[0]["user_message_en"], EN_TIN)
        self.assertEqual(turns[0]["bot_reply_en"], EN_REPLY)
        self.assertEqual(turns[1]["user_message_en"], "")

        context = db.get_conversation_context(session_id=session, conversation_id=conversation)
        self.assertEqual(context["recent_turns"][0]["user_message"], EN_TIN)
        self.assertEqual(context["recent_turns"][0]["bot_reply"], EN_REPLY)
        self.assertEqual(context["recent_turns"][0]["locale"], "lg")
        self.assertEqual(context["recent_turns"][1]["user_message"], "What is VAT?")


class EnglishViewTest(unittest.TestCase):
    def test_english_form_wins_and_locale_is_kept(self) -> None:
        view = english_view(
            normalize_history_turns(
                [{"user_message": LG_TIN, "bot_reply": LG_REPLY, "locale": "lg", "user_message_en": EN_TIN, "bot_reply_en": EN_REPLY}]
            )
        )
        self.assertEqual(view, [{"user_message": EN_TIN, "bot_reply": EN_REPLY, "locale": "lg"}])

    def test_websocket_messages_carry_their_english_form(self) -> None:
        turns = normalize_history_turns(
            [
                {"role": "user", "content": LG_TIN, "content_en": EN_TIN},
                {"role": "assistant", "content": LG_REPLY, "content_en": EN_REPLY, "locale": "lg"},
            ]
        )
        self.assertEqual(turns, [{"user_message": EN_TIN, "bot_reply": EN_REPLY, "locale": "lg"}])

    def test_english_turns_are_unchanged(self) -> None:
        turns = [{"user_message": "What is VAT?", "bot_reply": "18%."}]
        self.assertEqual(english_view(normalize_history_turns(turns)), turns)


class NoContextHoleTest(unittest.TestCase):
    def test_summary_covers_every_turn_the_prompt_does_not_replay(self) -> None:
        history = [{"user_message": "What is the VAT rate?", "bot_reply": "18%."}]
        history += [{"user_message": f"Question {i} about my return", "bot_reply": "..."} for i in range(4)]
        context = RollingContextManager().build_context(history)
        # Five turns: the prompt replays the last three, so the first two are summarised.
        self.assertEqual(PROMPT_VERBATIM_TURNS, 3)
        self.assertIn("Value Added Tax", context.context_summary)

    def test_no_summary_when_everything_is_replayed(self) -> None:
        history = [{"user_message": "What is the VAT rate?", "bot_reply": "18%."}] * PROMPT_VERBATIM_TURNS
        self.assertEqual(RollingContextManager().build_context(history).context_summary, "")

    def test_summary_reads_the_english_form(self) -> None:
        history = [
            {"user_message": "Omusolo ogukwatibwa nga tennasasulwa kye ki?", "user_message_en": "What is withholding tax?", "bot_reply": "...", "locale": "lg"}
        ]
        history += [{"user_message": "ok", "bot_reply": "..."}] * PROMPT_VERBATIM_TURNS
        summary = RollingContextManager().build_context(history).context_summary
        self.assertIn("Withholding Tax", summary)


class EnglishFormsForLoggingTest(unittest.TestCase):
    def test_forms_from_a_result_and_a_streamed_reply(self) -> None:
        from app.service import ChatModel

        forms = ChatModel.english_forms(
            {"_english_message": EN_TIN}, served_reply=LG_REPLY, english_reply=EN_REPLY
        )
        self.assertEqual(forms, {"user_message_en": EN_TIN, "bot_reply_en": EN_REPLY})

    def test_an_untranslated_reply_is_not_stored_twice(self) -> None:
        from app.service import ChatModel

        forms = ChatModel.english_forms({}, served_reply=EN_REPLY, english_reply=EN_REPLY)
        self.assertEqual(forms, {"user_message_en": "", "bot_reply_en": ""})


class RestPathCarriesEnglishFormsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        db.init_db()
        from app import service

        cls.service = service
        cls.model = service.ChatModel()

    def test_luganda_turn_result_carries_both_english_forms(self) -> None:
        service = self.service
        with mock.patch.object(service, "english_retrieval_query", side_effect=lambda t, _l: EN_TIN if t == LG_TIN else t), \
             mock.patch.object(service, "localize_reply", side_effect=lambda r, loc: f"[{loc}] {r}"), \
             mock.patch.object(service.ChatModel, "_maybe_handle_workflow", return_value=None), \
             mock.patch.object(self.model._cache, "get", return_value=None), \
             mock.patch.object(self.model._cache, "put"):
            out = self.model.generate(message=LG_TIN, locale="en", locale_explicit=False)
        self.assertEqual(out["locale"], "lg")
        self.assertEqual(out["_english_message"], EN_TIN)
        self.assertTrue(out["reply"].startswith("[lg] "))
        self.assertEqual(f"[lg] {out['_english_reply']}", out["reply"])


class AlreadyEnglishSearchTextTest(unittest.TestCase):
    def test_english_search_text_is_not_translated_again(self) -> None:
        from app import query

        with mock.patch.object(query, "translate_query_for_retrieval") as mt:
            out = query.english_retrieval_query("What documents do I need for TIN registration?", "lg")
        self.assertEqual(out, "What documents do I need for TIN registration?")
        mt.assert_not_called()

    def test_luganda_and_code_switched_text_is_still_translated(self) -> None:
        from app import query

        for text in (LG_TIN, "Withholding tax eri ebitundu bimeka for my company?"):
            with self.subTest(text=text), mock.patch.object(
                query, "translate_query_for_retrieval", return_value="translated"
            ) as mt:
                self.assertEqual(query.english_retrieval_query(text, "lg"), "translated")
                mt.assert_called_once()


class EnglishDecidedReplySafetyNetTest(unittest.TestCase):
    """Live 2026-10-06: decided English, answered in Luganda (G119)."""

    LG_ANSWER = (
        "Okusobola okwewandiisa okufuna TIN, olina okuba n'ebimu ku biwandiiko bino mu ofiisi ya URA nga kati."
        "\n\nEndagamuntu yo ne ssimu yo ku mukutu gwa URA."
    )

    def test_an_english_reply_is_left_alone(self) -> None:
        from app import service

        with mock.patch.object(service, "translate_query_for_retrieval") as mt:
            self.assertEqual(service._answer_in_english(EN_REPLY), EN_REPLY)
        mt.assert_not_called()

    def test_a_luganda_reply_is_translated_paragraph_by_paragraph(self) -> None:
        from app import service

        with mock.patch.object(
            service, "translate_query_for_retrieval", side_effect=["To register for a TIN, bring these documents to a URA office now.", "Your national ID and phone number on the URA portal."]
        ) as mt:
            out = service._answer_in_english(self.LG_ANSWER)
        self.assertEqual(mt.call_count, 2)
        self.assertEqual(out, "To register for a TIN, bring these documents to a URA office now.\n\nYour national ID and phone number on the URA portal.")

    def test_a_failed_translation_serves_the_reply_as_it_is(self) -> None:
        from app import service

        with mock.patch.object(service, "translate_query_for_retrieval", return_value=None):
            self.assertEqual(service._answer_in_english(self.LG_ANSWER), self.LG_ANSWER)


class PromptReadsEnglishHistoryTest(unittest.TestCase):
    def test_build_messages_replays_the_english_form(self) -> None:
        from app import llm

        messages = llm._build_messages(
            "Kiki ekyetaagisa?",
            [],
            [{"user_message": LG_TIN, "bot_reply": LG_REPLY, "user_message_en": EN_TIN, "bot_reply_en": EN_REPLY, "locale": "lg"}],
            "lg",
            tokenizer=None,
        )
        replayed = [m["content"] for m in messages[1:-1]]
        self.assertEqual(replayed, [EN_TIN, EN_REPLY])


if __name__ == "__main__":
    unittest.main()

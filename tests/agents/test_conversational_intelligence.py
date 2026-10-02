"""Tests for Multilingual Natural Conversational Intelligence & Civic Dialogue."""

from __future__ import annotations

import pytest

from app.conversational import handle_conversational_turn
from app.text_signals import (
    _resolve_courtesy_locale,
    get_farewell_reply,
    get_gratitude_reply,
    get_greeting_next_actions,
    get_greeting_reply,
    is_courtesy_sentence,
)


class TestMultilingualGreetingsAndCourtesy:
    @pytest.mark.parametrize(
        "phrase,expected_loc",
        [
            ("habari", "sw"),
            ("jambo", "sw"),
            ("hujambo", "sw"),
            ("mambo", "sw"),
            ("shikamoo", "sw"),
            ("habari yako", "sw"),
            ("oli otya", "lg"),
            ("wasuze otya", "lg"),
            ("gyebale", "lg"),
            ("gyebaleko", "lg"),
            ("ki kati", "lg"),
            ("hello", "en"),
            ("hi there", "en"),
        ],
    )
    def test_greeting_locale_resolution(self, phrase: str, expected_loc: str):
        assert _resolve_courtesy_locale(phrase) == expected_loc

    def test_greeting_reply_localization(self):
        sw_reply = get_greeting_reply("sw")
        assert "Msaidizi wa Kidijitali wa URA" in sw_reply
        assert "Habari" in sw_reply

        lg_reply = get_greeting_reply("lg")
        assert "Muyambi wa Digito owa URA" in lg_reply
        assert "Nkulamusizza" in lg_reply

        en_reply = get_greeting_reply("en")
        assert "URA Digital Assistant" in en_reply
        assert "Hello" in en_reply

    def test_gratitude_and_farewell_localization(self):
        assert "Karibu sana" in get_gratitude_reply("sw")
        assert "Tukwanirizza nnyo" in get_gratitude_reply("lg")
        assert "You're welcome" in get_gratitude_reply("en")

        assert "kwaheri" in get_farewell_reply("sw").lower()
        assert "weraba" in get_farewell_reply("lg").lower()
        assert "goodbye" in get_farewell_reply("en").lower()

    def test_is_courtesy_sentence_matches_vernaculars(self):
        assert is_courtesy_sentence("Habari ya leo") is True
        assert is_courtesy_sentence("Asante sana") is True
        assert is_courtesy_sentence("Oli otya nno") is True
        assert is_courtesy_sentence("Weebale nnyo") is True
        assert is_courtesy_sentence("Hello there") is True


class TestConversationalIntelligenceEngine:
    @pytest.mark.parametrize(
        "query,locale,expected_intent",
        [
            ("Why do we pay taxes in Uganda?", "en", "civic_philosophy"),
            ("Lwaki tusasula omusolo?", "lg", "civic_philosophy"),
            ("Kwa nini tunalipa kodi?", "sw", "civic_philosophy"),
            ("Who made you?", "en", "identity_capability"),
            ("Ggwe ani era osobola okwogera Oluganda?", "lg", "identity_capability"),
            ("Wewe ni nani na unaweza kunisaidia nini?", "sw", "identity_capability"),
            ("I am scared of starting a business because of taxes", "en", "business_empathy"),
            ("Ntya okutandika bizinensi olw'emisolo", "lg", "business_empathy"),
            ("Nina hofu ya kuanza biashara", "sw", "business_empathy"),
            ("How are you doing today?", "en", "status_smalltalk"),
            ("Oli otya leero?", "lg", "status_smalltalk"),
            ("Habari yako ya leo?", "sw", "status_smalltalk"),
            ("Write a poem about taxes in Uganda", "en", "creative_poem"),
            ("Wandiika ekitontome ku musolo", "lg", "creative_poem"),
            ("Who won the football world cup?", "en", "out_of_scope_divert"),
        ],
    )
    def test_conversational_intents(self, query: str, locale: str, expected_intent: str):
        res = handle_conversational_turn(query, locale)
        assert res is not None
        assert res.intent_type == expected_intent
        assert len(res.reply) > 50
        assert len(res.next_actions) >= 2

    def test_factual_tax_queries_pass_through_to_retrieval(self):
        """Factual tax calculations and rules must NOT be intercepted by conversational dialog."""
        assert handle_conversational_turn("What is the standard VAT rate in Uganda?") is None
        assert handle_conversational_turn("Calculate PAYE for salary 2,500,000") is None
        assert handle_conversational_turn("How do I register for a TIN?") is None
        assert handle_conversational_turn("Customs import duty on solar batteries") is None

    @pytest.mark.parametrize(
        "query,locale,expected_intent",
        [
            ("where a u from?", "en", "identity_capability"),
            ("where r u from?", "en", "identity_capability"),
            ("who r u?", "en", "identity_capability"),
            ("u there?", "en", "presence_check"),
            ("hw r u?", "en", "status_smalltalk"),
            ("r u a human?", "en", "identity_capability"),
            ("wat can u do?", "en", "identity_capability"),
            ("gwe ani?", "lg", "identity_capability"),
            ("ova wa?", "lg", "identity_capability"),
            ("unatoka wapi?", "sw", "identity_capability"),
            ("uko wapi?", "sw", "identity_capability"),
            ("upo?", "sw", "presence_check"),
        ],
    )
    def test_social_media_slang_conversational_queries(
        self, query: str, locale: str, expected_intent: str
    ):
        from app.text_signals import strip_conversational_prefix
        core = strip_conversational_prefix(query)
        res = handle_conversational_turn(core, locale)
        assert res is not None
        assert res.intent_type == expected_intent

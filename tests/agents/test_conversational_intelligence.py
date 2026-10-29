"""Tests for Multilingual Natural Conversational Intelligence & Civic Dialogue."""

from __future__ import annotations

import pytest
from app.conversational import handle_conversational_turn
from app.text_signals import (
    _resolve_courtesy_locale,
    get_farewell_reply,
    get_gratitude_reply,
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
            ("Ahabw'enki nitushashura omushoro?", "nyn", "civic_philosophy"),
            ("Pingo waculo mucoro?", "ach", "civic_philosophy"),
            ("Who made you?", "en", "identity_capability"),
            ("Ggwe ani era osobola okwogera Oluganda?", "lg", "identity_capability"),
            ("Wewe ni nani na unaweza kunisaidia nini?", "sw", "identity_capability"),
            ("Iwe ori oha kandi noobaasa kugamba Orunyankore?", "nyn", "identity_capability"),
            ("In ibedo nga dok itwero loko Leb Acoli?", "ach", "identity_capability"),
            ("I am scared of starting a business because of taxes", "en", "business_empathy"),
            ("Ntya okutandika bizinensi olw'emisolo", "lg", "business_empathy"),
            ("Nina hofu ya kuanza biashara", "sw", "business_empathy"),
            ("Nintina kutandika obushuubuzi", "nyn", "business_empathy"),
            ("Alworo cako biacara", "ach", "business_empathy"),
            ("How are you doing today?", "en", "status_smalltalk"),
            ("Oli otya leero?", "lg", "status_smalltalk"),
            ("Habari yako ya leo?", "sw", "status_smalltalk"),
            ("Agandi erizooba?", "nyn", "status_smalltalk"),
            ("Kopango tin?", "ach", "status_smalltalk"),
            ("Write a poem about taxes in Uganda", "en", "creative_poem"),
            ("Wandiika ekitontome ku musolo", "lg", "creative_poem"),
            ("Handiika ekikwate aha mushoro", "nyn", "creative_poem"),
            ("Coo wer me mucoro", "ach", "creative_poem"),
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

    def test_october_2026_qa_audit_conversational_eq_structuring_and_mcp_gaps(self, monkeypatch):
        """Verify all 5 phases of the October 2026 QA & Audit remediation."""
        from app.guardrails import OutputGuard
        from app.mcp import get_client, reset_client
        from app.mcp.validation import (
            coerce_arguments_to_schema,
            inspect_descriptor_integrity,
            result_matches_schema,
        )
        from app.number_words import decimal_words, en_words, sw_words
        from app.text_signals import detect_hopelessness, needs_wellbeing_checkin
        from app.tools import ToolRegistry

        # Phase 1: Conversational FY2026-27 VAT threshold (300M) + intensifier/particle resilience
        emp_en = handle_conversational_turn("I am afraid to start a business", "en")
        assert emp_en is not None and emp_en.intent_type == "business_empathy"
        assert "300,000,000" in emp_en.reply

        emp_sw = handle_conversational_turn("Nina hofu kubwa ya kuanza biashara kwa sababu ya kodi za URA.")
        assert emp_sw is not None and emp_sw.locale == "sw" and emp_sw.intent_type == "business_empathy"
        assert "300" in emp_sw.reply
        assert "yanayotozwa VAT" in emp_sw.reply

        en_speak_nyn = handle_conversational_turn("Can you speak Runyankole?", "en")
        assert en_speak_nyn is not None and en_speak_nyn.locale == "en" and en_speak_nyn.intent_type == "identity_capability"

        id_lg = handle_conversational_turn("Osobola okwogera n'okutegeera Oluganda olutuufu?")
        assert id_lg is not None and id_lg.locale == "lg" and id_lg.intent_type == "identity_capability"
        assert "nsobola" in id_lg.reply.lower()

        id_sw = handle_conversational_turn("Je, una uwezo wa kuelewa na kuongea lugha ya Kiswahili fasaha?")
        assert id_sw is not None and id_sw.locale == "sw" and id_sw.intent_type == "identity_capability"

        st_lg = handle_conversational_turn("Oli otya leero mwattu?")
        assert st_lg is not None and st_lg.locale == "lg" and st_lg.intent_type == "status_smalltalk"

        st_sw = handle_conversational_turn("Uko salama leo na kazi zinaendeleaje?")
        assert st_sw is not None and st_sw.locale == "sw" and st_sw.intent_type == "status_smalltalk"

        # Phase 2: 5-locale hopelessness & EmotionalToneTool schema/crisis parity
        assert detect_hopelessness("Mpeddemu essuubi era sisobola kugumiikiriza") is True
        assert detect_hopelessness("Tinyine matsiko kandi tindikubaasa kugumisiriza") is True
        assert detect_hopelessness("Geno peke dok pe atwero kanyo") is True
        assert needs_wellbeing_checkin(
            "Mpeddemu essuubi",
            history=["Sirina ssente era bizinesi yange egudde"],
        ) is True

        eq_tool = ToolRegistry.get("assess_emotional_tone")
        assert eq_tool is not None
        eq_crisis = eq_tool.execute(message="I want to end my life because of my tax debt")
        assert eq_crisis["ok"] is True and eq_crisis["crisis"] is True
        assert eq_crisis["tone_register"] == "empathetic_reassuring"
        assert result_matches_schema(eq_tool.schema.output_schema, eq_crisis) == []

        monkeypatch.setenv("FLAG_WELLBEING_CHECKIN", "false")
        eq_wb_off = eq_tool.execute(
            message="Mpeddemu essuubi",
            history=["Sirina ssente era edduuka lyange baligadde"],
            locale="lg",
        )
        assert eq_wb_off["ok"] is True and eq_wb_off["wellbeing_checkin"] is False

        monkeypatch.setenv("FLAG_WELLBEING_CHECKIN", "true")
        eq_wb = eq_tool.execute(
            message="Mpeddemu essuubi",
            history=["Sirina ssente era edduuka lyange baligadde"],
            locale="lg",
        )
        assert eq_wb["ok"] is True and eq_wb["wellbeing_checkin"] is True
        assert eq_wb["priority_boost"] == "urgent"
        assert result_matches_schema(eq_tool.schema.output_schema, eq_wb) == []

        # Phase 3: Structuring, paragraphing & numbering hygiene
        sec_text = "Filing is mandatory under Sec. 5. The taxpayer must submit a return under s. 12. Every company pays 30%."
        norm_sec = OutputGuard.normalize_structure(sec_text)
        assert "Sec.\n\n5." not in norm_sec
        assert "s.\n\n12." not in norm_sec
        assert "Sec. 5" in norm_sec
        assert "s. 12" in norm_sec

        sub_bullet_list = "1. Log in to the portal:\n   - Enter your TIN\n   - Enter your password\n1. Select e-Returns"
        renumbered = OutputGuard.normalize_structure(sub_bullet_list)
        assert "1. Log in to the portal:" in renumbered
        assert "2. Select e-Returns" in renumbered

        md_table = "| Band | Rate |\n| --- | --- |\n| Band A. First sentence. | Zero rate. Second sentence. |\n| Band B. Third sentence. | Twenty percent. Fourth sentence. |"
        assert OutputGuard.normalize_structure(md_table).count("\n\n") == 0

        mixed_prose_table = (
            "Register for a TIN before you trade. "
            "File the return by the fifteenth. "
            "Pay the assessed tax through the portal. "
            "Keep the acknowledgement for five years.\n"
            "| Band | Rate |\n"
            "| --- | --- |\n"
            "| Standard | 18% |"
        )
        norm_mixed = OutputGuard.normalize_structure(mixed_prose_table)
        assert "Keep the acknowledgement for five years.\n| Band | Rate |" in norm_mixed
        assert "\n\nKeep the acknowledgement" in norm_mixed

        # Phase 4: Number words negative & decimal edge cases
        assert en_words(-15) == "minus fifteen"
        assert sw_words(-18) == "hasi kumi na nane"
        assert decimal_words("1,250.5", "en") == "one thousand two hundred and fifty point five"
        assert decimal_words(".5", "en") == "zero point five"

        # Phase 5: MCP SEP-2106 external $ref guard, shorthand coercion & portal navigator MCP coverage
        bad_desc = {
            "name": "evil_tool",
            "description": "Safe description",
            "inputSchema": {"type": "object", "$ref": "https://attacker.example/schema.json"},
        }
        assert any("external_schema_ref" in v for v in inspect_descriptor_integrity(bad_desc))

        coerced = coerce_arguments_to_schema(
            {
                "type": "object",
                "properties": {
                    "amount": {"type": "number"},
                    "prn": {"type": "string"},
                    "nin": {"type": "string"},
                },
            },
            {"amount": "UGX 2.5m/=", "prn": "2260-0012-3456-7", "nin": "cm90-0123-4567-89"},
        )
        assert coerced["amount"] == 2_500_000.0
        assert coerced["prn"] == "2260001234567"
        assert coerced["nin"] == "CM900123456789"

        reset_client()
        client = get_client()
        nav_call = client.call_tool(
            "navigate_external_portal",
            {"extracted_text": "Upstream field services and deemed VAT", "portal_name": "oil_and_gas"},
            user_role="public",
        )
        assert nav_call.ok is True
        assert "- **Screen State**:" in nav_call.result["explanation"]

        # Phase 6: 72-tool MCP descriptor integrity and multimodal affect parity
        from app.tools.empathy import fuse_multimodal_affect

        all_tools = ToolRegistry.all()
        assert len(all_tools) >= 72
        for t_obj in all_tools:
            mcp_desc = t_obj.to_mcp_tool()
            assert inspect_descriptor_integrity(mcp_desc) == [], f"Integrity failure on {t_obj.schema.name}"

        fused_wb = fuse_multimodal_affect(
            "Tinyine matsiko",
            history=["Eduuka ryangye baariigara kandi tihaine sente"],
            locale="nyn",
            record_metric=False,
        )
        assert fused_wb.wellbeing_checkin is True
        assert fused_wb.priority_boost == "urgent"

        # Second-pass audit checks:
        # 1. nyn / ach conversational locale resolution on variants (ntiina, noobaasa kuhurira, itwero niang)
        c_nyn_var = handle_conversational_turn("Ntiina munonga kutandika obushuubuzi")
        assert c_nyn_var is not None and c_nyn_var.locale == "nyn" and c_nyn_var.intent_type == "business_empathy"
        c_ach_var = handle_conversational_turn("Itwero niang Acholi?")
        assert c_ach_var is not None and c_ach_var.locale == "ach" and c_ach_var.intent_type == "identity_capability"

        # 2. Wellbeing check-in guidance & governance_v3 scorecard calibration on crisis/safety replies
        assert "crisis support lines" in eq_wb["avoid"]
        assert "check in on wellbeing" in eq_wb["explanation"]
        from app.audit.governance_v3 import evaluate_interaction_scorecard
        from app.text_signals import wellbeing_checkin_reply

        sc_safety = evaluate_interaction_scorecard(
            session_id="sess-audit-wb",
            locale="lg",
            reply_text=wellbeing_checkin_reply(),
            distress_detected=True,
            relief_cited=False,
            deterministic_basis=True,
        )
        assert sc_safety.language_and_empathy_score == 1.0
        assert "MISSING_STATUTORY_RELIEF_ON_HARDSHIP" not in sc_safety.anomaly_flags

        # 3. Separate top-level bullet list after blank line resets ordered list numbering,
        #    while glued vernacular currency ("Milioni10. Biashara") is not split into a step
        two_lists = "1. Step A\n2. Step B\n\n- Separate top-level bullet\n\n1. First item of second list\n1. Second item of second list"
        norm_two_lists = OutputGuard.normalize_structure(two_lists)
        assert "1. First item of second list" in norm_two_lists
        assert "2. Second item of second list" in norm_two_lists
        assert "Milioni.\n\n10." not in OutputGuard.normalize_structure("Milioni10. Biashara ndogo zote.")

        # 4. Negative zero-whole decimals (-0.5) preserve minus/hasi sign
        assert decimal_words("-0.5", "en") == "minus zero point five"
        assert decimal_words("-0.5", "sw") == "hasi sifuri nukta tano"

        # 5. Bantu scale prefix coercion ("obukadde 15", "milioni 10") & percentage rate string ("18%")
        coerced_bantu = coerce_arguments_to_schema(
            {
                "type": "object",
                "properties": {
                    "annual_turnover": {"type": "number"},
                    "rate": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
            {"annual_turnover": "obukadde 15", "rate": "18%"},
        )
        assert coerced_bantu["annual_turnover"] == 15_000_000.0
        assert coerced_bantu["rate"] == 0.18

        # 6. TIN/PRN/BRN identifier spans excluded from UGX amount extraction & ChatResponse.connectors_used
        from app.calculator_router import extract_amounts
        from app.connector_intent import detect_connector_intent, missing_arguments
        from app.models import ChatResponse
        from app.speech_normalization import _format_ugx_amount
        from app.text_signals import detect_crisis, detect_high_stakes

        assert extract_amounts("What does EFRIS hold for TIN 1000000001?") == []
        assert extract_amounts("What does EFRIS hold for TIN 1000-000-001?") == []
        assert [value for value, _, _ in extract_amounts("Generate a PRN for TIN 1000000001 20000000")] == [20_000_000.0]
        assert extract_amounts("Check payment status for PRN 220000000001") == []
        assert extract_amounts("Verify BRN: 80020000012345") == []
        resp_model = ChatResponse(
            reply="Kakira Sugar Limited",
            connectors_used=[{"namespace": "efris", "label": "EFRIS", "mode": "simulation", "outcome": "found"}],
        )
        assert resp_model.connectors_used[0]["namespace"] == "efris"
        assert resp_model.connectors_used[0]["outcome"] == "found"

        # 7. English capability question naming a vernacular language stays in English ("en")
        cap_nyn_in_en = handle_conversational_turn("Can you speak Runyankole?", "nyn")
        assert cap_nyn_in_en is not None and cap_nyn_in_en.locale == "en"
        st_ssebo = handle_conversational_turn("Oli otya leero ssebo?", "lg")
        assert st_ssebo is not None and st_ssebo.locale == "lg" and st_ssebo.intent_type == "status_smalltalk"

        # 8. 5-locale high-stakes (enforcement, deadline_today, Bantu large_amount) & phrasal crisis detection
        assert "enforcement" in detect_high_stakes("Eduuka ryangye baariigara")
        assert "enforcement" in detect_high_stakes("Duka mera gicego")
        assert "enforcement" not in detect_high_stakes("Sirina ssente, bizinesi yange egudde")
        assert "deadline_today" in detect_high_stakes("Erizooba n'ekiro ky'aha muheru")
        assert "deadline_today" in detect_high_stakes("Tin aye nino me agikki")
        assert "large_amount" in detect_high_stakes("Bampadde ekibonerezo kya obukadde 20")
        assert "large_amount" in detect_high_stakes("Nimepewa adhabu ya milioni 50")
        assert "large_amount" not in detect_high_stakes("Nimepewa adhabu ya milioni 50 TZS")
        assert detect_crisis("Njagala kufa olw'ebbanja ly'omusolo") is True
        assert detect_crisis("Nataka kufa kwa sababu ya deni") is True
        assert detect_crisis("Ninyenda kufa") is True
        assert detect_crisis("Amito to") is True
        assert detect_crisis("Pe amito to") is False
        assert detect_crisis("I do not want to die") is False
        assert detect_crisis("Sinjagala kufa") is False
        assert detect_crisis("Sitaki kufa") is False
        from app.calculator_router import plan_rate_lookup

        paye_rate_plan = plan_rate_lookup("How is PAYE tax calculated?")
        assert paye_rate_plan is not None and paye_rate_plan.summary == "paye"

        # 9. Structuring ordinal word-boundary & comma handling + nested parenthesized step indentation + zero-cent TTS
        assert "5. **Tano**:" not in OutputGuard.normalize_structure("Tanoa kisu. Pilika nyingi.")
        norm_comma = OutputGuard.normalize_structure("Kwanza, fungua akaunti. Pili, weka TIN.")
        assert ": ," not in norm_comma and "1. **Kwanza**: fungua akaunti." in norm_comma
        assert "1. **Kwanza**: fungua" in OutputGuard.normalize_structure("Kwanza:fungua akaunti.")
        assert "1. File\n  2. Pay" in OutputGuard.normalize_structure("(1)File\n  (2)Pay")
        nested_paren = OutputGuard.normalize_structure(
            "1. Main step one:\n   (1) Sub-step A\n   (2) Sub-step B\n1. Main step two"
        )
        assert "2. Main step two" in nested_paren
        assert "sifuri" not in _format_ugx_amount("1,000,000.00", "sw")
        assert "zero" not in _format_ugx_amount("3,500,000.00", "lg")
        from app.speech_normalization import _spoken_number

        assert _spoken_number("2026.00", "lg") == "two thousand and twenty-six"

        # 10. Connector intent hyphenated TIN & integer identifier grounding/coercion
        efris_desc = {
            "name": "efris_taxpayer_status",
            "description": "Look up EFRIS registration status by TIN.",
            "inputSchema": {"type": "object", "properties": {"tin": {"type": "string"}}, "required": ["tin"]},
        }
        ci_hyph = detect_connector_intent("Check EFRIS status for TIN 1000-000-001", [("efris", efris_desc)])
        assert ci_hyph is not None and ci_hyph.operations == ("efris_taxpayer_status",)
        assert missing_arguments({"tin": 9999999999}, efris_desc["inputSchema"], "Check EFRIS status") == ["tin"]
        assert missing_arguments(
            {"tin": 220000000001},
            efris_desc["inputSchema"],
            "Check EFRIS status for 220000000001",
        ) == ["tin"]
        coerced_int_id = coerce_arguments_to_schema(efris_desc["inputSchema"], {"tin": 1000000001})
        assert coerced_int_id["tin"] == "1000000001"
        assert coerce_arguments_to_schema(efris_desc["inputSchema"], {"tin": 220000000001})["tin"] == 220000000001

        # 11. Runyankole ("eigana" / "igana" / "kikumi") & 4-locale TTS/ASR/entailment/MT/case_facts parity
        from app import mt
        from app.case_facts import _normalize_vernacular_amount_words
        from app.entailment import percentages
        from app.receptionist.lexicon import repair_asr_entities
        from app.speech_normalization import clean_text_for_speech

        assert clean_text_for_speech("Omushoro gwa VAT guri ebicweka 18% aha eigana.", locale="nyn") == (
            "Omushoro gwa VAT guri ebicweka ikumi na munaana aha eigana."
        )
        assert clean_text_for_speech("Omushoro gwa VAT guri ebicweka 18% aha kikumi.", locale="nyn") == (
            "Omushoro gwa VAT guri ebicweka ikumi na munaana aha kikumi."
        )
        assert "18" in percentages("Omushoro gwa VAT guri ebicweka ikumi na munaana aha eigana.")
        assert "18" in percentages("Omushoro gwa VAT guri ebicweka 18 aha igana.")
        assert mt.figures_survived(
            "The standard VAT rate is 18%.",
            "Omushoro gwa VAT guri ebicweka 18 aha eigana.",
            locale="nyn",
        )
        repaired_nyn = repair_asr_entities("Omushoro gwa VAT omuri Uganda guri aha bicweka ikumi na munaana aha kikuru.")
        assert "aha kikumi" in repaired_nyn and "kikuru" not in repaired_nyn
        assert "ni asilimia" in repair_asr_entities("Kiwango cha VAT ni silimia kumi na nane.")
        assert "dul apar wiye aboro i kom miya" in repair_asr_entities("Wel mucoro tye idulu apar wiye aboro i kom miyo.")
        assert "obukaikuru 100" in _normalize_vernacular_amount_words("obukaikuru eigana")
        assert "obukaikuru 18" in _normalize_vernacular_amount_words("obukaikuru ikumi na munaana")
        assert "milioni 25" in _normalize_vernacular_amount_words("milioni pyeraryo wiye abic")

        # 12. Expanded vernacular number-words, compound hundreds/tens/halves & money extraction across lg, sw, nyn, ach
        from app.entailment import canonical_amounts

        assert [v for v, _, _ in extract_amounts("shilingi obukadde amakumi asatu mu bitaano")] == [35_000_000.0]
        assert [v for v, _, _ in extract_amounts("shiringi obukaikuru makumi ashatu na bitaano")] == [35_000_000.0]
        assert [v for v, _, _ in extract_amounts("shilingi milioni mia tatu")] == [300_000_000.0]
        assert [v for v, _, _ in extract_amounts("shilingi obukadde bikumi bisatu")] == [300_000_000.0]
        assert [v for v, _, _ in extract_amounts("shiringi obukaikuru magana ashatu")] == [300_000_000.0]
        assert [v for v, _, _ in extract_amounts("cilingi milioni miya adek")] == [300_000_000.0]
        assert [v for v, _, _ in extract_amounts("shilingi akakadde kamu n'ekitundu")] == [1_500_000.0]
        assert [v for v, _, _ in extract_amounts("shiringi akakaikuru kamwe n'ekicweka")] == [1_500_000.0]
        assert [v for v, _, _ in extract_amounts("shilingi milioni moja na nusu")] == [1_500_000.0]
        assert [v for v, _, _ in extract_amounts("cilingi milioni acel ki nucu")] == [1_500_000.0]
        assert [v for v, _, _ in extract_amounts("elfu mia tatu thelathini na tano")] == [335_000.0]
        assert 300_000_000.0 in canonical_amounts("shiringi obukaikuru magana ashatu")
        assert 300_000_000.0 in canonical_amounts("cilingi milioni miya adek")
        assert "large_amount" in detect_high_stakes("Bampadde ekibonerezo kya obukadde amakumi abiri")
        assert "large_amount" in detect_high_stakes("Nimepewa adhabu ya milioni hamsini")
        assert "large_amount" in detect_high_stakes("Nyine ebanja rya obukaikuru makumi ashatu")
        assert "large_amount" in detect_high_stakes("Banya mera romo milioni pyeradek")
        coerced_words = coerce_arguments_to_schema(
            {"type": "object", "properties": {"annual_turnover": {"type": "number"}}},
            {"annual_turnover": "obukaikuru makumi ashatu na bitaano"},
        )
        assert coerced_words["annual_turnover"] == 35_000_000.0
        assert [v for v, _, _ in extract_amounts("obukadde ekkumi n'omunaana")] == [18_000_000.0]
        assert [v for v, _, _ in extract_amounts("obukadde kikumi n'ebitaano")] == [105_000_000.0]
        assert [v for v, _, _ in extract_amounts("milioni tatu na elfu mia tano")] == [3_500_000.0]
        assert [v for v, _, _ in extract_amounts("milioni 3 na elfu 500")] == [3_500_000.0]
        assert [v for v, _, _ in extract_amounts("obukadde busatu n'emitwalo ataano")] == [3_500_000.0]
        assert [v for v, _, _ in extract_amounts("obukadde 3 n'emitwalo 50")] == [3_500_000.0]
        assert [v for v, _, _ in extract_amounts("obukaikuru bushatu n'emitwaro ataano")] == [3_500_000.0]
        assert extract_amounts("prelim 2 and slim 5") == []
        assert mt.figures("shilingi milioni tatu na elfu mia tano", locale="sw") == {3_500_000.0}
        assert 10_000.0 in canonical_amounts("tutemia apar")
        assert 10_000_000.0 in canonical_amounts("miliyoni kumi")
        assert 20_000.0 in canonical_amounts("elufu makumi abiri")
        assert "large_amount" in detect_high_stakes("Bampadde ekibonerezo kya emitwalo rukumi")
        assert detect_crisis("I do not want to kill myself") is False
        assert detect_crisis("I don't want to end my life") is False
        assert detect_crisis("I am not suicidal") is False
        assert detect_crisis("I am not not suicidal") is True
        assert detect_crisis("Pe amito neko koma") is False
        assert detect_crisis("Sitaki kujiua") is False
        assert detect_crisis("Sinjagala okwetta") is False
        assert detect_crisis("Tindikwenda okweyita") is False
        assert detect_crisis("I can't pay my taxes and I want to die") is True
        assert extract_amounts("USD milioni tatu") == []
        assert [v for v, _, _ in extract_amounts("milioni 1,500")] == [1_500_000_000.0]
        assert [v for v, _, _ in extract_amounts("akakadde n'ekitundu")] == [1_500_000.0]
        assert _normalize_vernacular_amount_words("milioni 9 na elfu 999 na 999") == "milioni 9.999999"
        assert plan_rate_lookup("How is PAYE tax calculated for my second job?") is None
        assert clean_text_for_speech("Wel mucoro tye dul 18% i kom miya acel.", locale="ach") == (
            "Wel mucoro tye dul apar wiye aboro i kom miya."
        )

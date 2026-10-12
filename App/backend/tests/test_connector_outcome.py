"""The words a taxpayer gets after a service was asked — see ``app.connector_outcome``.

Every case here is a sentence the real model wrote, live, about a result that
did not say it: "likely counterfeit" for a stamp the fixtures simply did not
hold, "unable to access the URA database" for a consignment that was not
found, "ask the taxpayer for their TIN" for a lookup that lacked one, and a
practice-system record stated as fact.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.connector_outcome import (
    DEMO_NOTICE,
    FOUND,
    NEEDS_INPUT,
    NO_RECORD,
    REPORTED_SEPARATELY,
    UNAVAILABLE,
    argument_label,
    classify,
    classify_all,
    compose,
    deterministic,
)

POLICY_OK = {"allowed": True, "reasons": [], "risk": "low"}
LABELS = {
    "efris": "EFRIS",
    "ursb": "URSB",
    "payment_system": "Payments",
    "digital_tax_stamps": "Digital Tax Stamps",
}


def outcomes(*calls: dict[str, Any]):
    """The turn's outcomes, labelled the way the catalog labels them."""
    return classify_all(calls, label_for=LABELS.get)


def call(name: str, result: Any, *, connector: str = "efris", mode: str = "simulation") -> dict[str, Any]:
    return {
        "name": name,
        "arguments": {},
        "result": result,
        "connector": connector,
        "connector_mode": mode,
    }


EFRIS_FOUND = call(
    "efris_taxpayer_status",
    {
        "ok": True,
        "tin": "1000000002",
        "registered": True,
        "profile": {"business_name": "Nile Breweries Limited", "is_vat_registered": True, "efris_status": "ACTIVE"},
        "mode": "simulation",
        "live": False,
        "simulation_notice": "Local simulator result only.",
        "policy": POLICY_OK,
    },
)
STAMP_UNKNOWN = call(
    "dts_verify_stamp",
    {
        "ok": False,
        "stamp_code": "DTS-0001-SAMPLE",
        "is_authentic": None,
        "status": "UNKNOWN",
        "message": "",
        "error": (
            "Sample stamp code 'DTS-0001-SAMPLE' is not in the local simulator fixtures. This result does not "
            "establish whether a physical product is genuine or counterfeit. Verify it through https://ura.go.ug."
        ),
        "mode": "simulation",
        "policy": POLICY_OK,
    },
    connector="digital_tax_stamps",
)
PRN_NOT_FOUND = call(
    "payment_view_status",
    {
        "ok": False,
        "status": "NOT_FOUND",
        "message": "PRN '220000000001' was not found in the local payment simulator.",
        "error": "PRN not found",
        "mode": "simulation",
        "policy": POLICY_OK,
    },
    connector="payment_system",
)
NO_BUSINESS = call(
    "ursb_verify_business",
    {"ok": True, "found": False, "entity": None, "message": "No registered entity found matching query 'X'.", "error": None},
    connector="ursb",
)


class TestClassify:
    def test_a_record_is_found_and_keeps_a_few_facts(self):
        outcome = classify(EFRIS_FOUND)

        assert outcome is not None
        assert (outcome.kind, outcome.connector, outcome.mode) == (FOUND, "efris", "simulation")
        facts = dict(outcome.facts)
        assert facts["Business name"] == "Nile Breweries Limited"
        assert facts["VAT registered"] == "yes"
        # What describes the call is not a fact about the taxpayer, and the
        # identifier is the one the taxpayer typed.
        assert not {"Mode", "Live", "Policy", "Simulation notice", "Ok", "TIN"} & set(facts)

    def test_found_false_is_no_record_in_the_services_words(self):
        outcome = classify(NO_BUSINESS)

        assert outcome is not None
        assert outcome.kind == NO_RECORD
        assert outcome.message == "No registered entity found matching query 'X'."

    def test_a_message_is_preferred_to_a_terse_error(self):
        outcome = classify(PRN_NOT_FOUND)

        assert outcome is not None
        assert outcome.kind == NO_RECORD
        assert outcome.message == "PRN '220000000001' was not found in the local payment simulator."

    def test_an_unknown_stamp_keeps_the_service_s_caution_about_what_it_does_not_establish(self):
        outcome = classify(STAMP_UNKNOWN)

        assert outcome is not None
        assert outcome.kind == NO_RECORD
        assert "does not establish whether a physical product is genuine or counterfeit" in outcome.message

    def test_a_lookup_without_its_identifier_needs_input(self):
        outcome = classify(
            call(
                "efris_taxpayer_status",
                {"ok": False, "error": "argument_not_provided", "arguments": ["tin"], "argument_labels": ["TIN"]},
            )
        )

        assert outcome is not None
        assert (outcome.kind, outcome.missing) == (NEEDS_INPUT, ("TIN",))

    def test_the_clients_own_schema_refusal_is_a_question_not_prose(self):
        # Live: "Invalid arguments for efris_taxpayer_status: (root): 'tin' is a
        # required property." was shown to a taxpayer, with a demo-data notice.
        refused = call(
            "efris_taxpayer_status",
            {"ok": False, "error": "Invalid arguments for efris_taxpayer_status: (root): 'tin' is a required property"},
        )

        outcome = classify(refused)
        reply = compose(outcomes(refused), "")

        assert outcome is not None
        assert (outcome.kind, outcome.missing) == (NEEDS_INPUT, ("TIN",))
        assert reply == "To look that up in EFRIS I need the TIN. Please send it and I'll check."
        assert "Invalid arguments" not in reply

    def test_any_other_invalid_arguments_is_an_unusable_call_not_a_missing_record(self):
        outcome = classify(
            call("efris_taxpayer_status", {"ok": False, "error": "Invalid arguments for x: (root): 'tin' is not of type 'string'"})
        )

        assert outcome is not None
        assert outcome.kind == UNAVAILABLE

    @pytest.mark.parametrize(
        "result",
        [
            {"ok": False, "error": "efris tools are temporarily unavailable (circuit open)", "retryable": True},
            {"ok": False, "error": "tool result did not match its output schema", "validation_errors": ["x"]},
            {"ok": False, "error": "TransportError: connection refused"},
            {"ok": False, "error": "something", "policy": {"allowed": False, "reasons": ["role"]}},
            {"ok": False, "error": "This operation was not selected or available for this chat."},
            {"ok": False, "error": "The connector response was too large to review safely."},
            {"ok": False},
            "not a mapping",
        ],
    )
    def test_a_service_that_could_not_be_asked_is_not_a_missing_record(self, result):
        outcome = classify(call("efris_taxpayer_status", result))

        assert outcome is not None
        assert outcome.kind == UNAVAILABLE

    def test_a_local_tool_is_not_a_connector_call(self):
        assert classify({"name": "calculate_vat", "arguments": {}, "result": {"ok": True}}) is None

    def test_the_label_is_the_name_the_taxpayer_knows(self):
        outcome = classify(EFRIS_FOUND, label_for=LABELS.get)

        assert outcome is not None
        assert outcome.label == "EFRIS"
        # With no resolver the namespace is made readable rather than shown raw.
        assert classify(PRN_NOT_FOUND).label == "Payment System"  # type: ignore[union-attr]

    def test_argument_labels_prefer_the_schema_s_own_description(self):
        schema = {"properties": {"query": {"type": "string", "description": "Business Name, Registration Number. More."}}}

        assert argument_label(schema, "query") == "Business Name, Registration Number"
        assert argument_label(None, "prn") == "Payment Registration Number (PRN)"
        assert argument_label({}, "some_field") == "some field"


class TestCompose:
    def test_a_record_is_summarised_by_the_model_and_marked_as_demo_data(self):
        reply = compose(outcomes(EFRIS_FOUND), "Yes — this taxpayer is registered for VAT.")

        assert reply.startswith("Yes — this taxpayer is registered for VAT.")
        assert reply.endswith(DEMO_NOTICE)

    def test_the_demo_notice_is_not_repeated_when_the_model_already_said_it(self):
        reply = compose(outcomes(EFRIS_FOUND), "Registered (this is demo data).")

        assert reply == "Registered (this is demo data)."

    def test_a_live_service_is_not_called_a_practice_system(self):
        live = {**EFRIS_FOUND, "connector_mode": "live"}

        reply = compose(outcomes(live), "Registered.")

        assert reply == "Registered."

    def test_a_missing_record_is_never_paraphrased_by_the_model(self):
        model_text = "The stamp is invalid and is likely counterfeit. Report it immediately."

        reply = compose(outcomes(STAMP_UNKNOWN), model_text)

        assert "likely counterfeit" not in reply
        assert "invalid" not in reply
        assert "does not establish whether a physical product is genuine or counterfeit" in reply
        assert reply.endswith(DEMO_NOTICE)

    def test_a_service_that_answered_is_not_described_as_unreachable(self):
        reply = compose(outcomes(PRN_NOT_FOUND), "I am unable to access the URA database.")

        assert "unable to access" not in reply
        assert "was not found in the local payment simulator" in reply

    def test_missing_input_is_asked_for_in_the_taxpayer_s_words(self):
        needs = call(
            "efris_taxpayer_status",
            {"ok": False, "error": "argument_not_provided", "arguments": ["tin"], "argument_labels": ["TIN"]},
        )

        reply = compose(outcomes(needs), "Please ask the taxpayer for their TIN.")

        assert reply == "To look that up in EFRIS I need the TIN. Please send it and I'll check."
        assert "ask the taxpayer" not in reply

    def test_nothing_is_called_demo_data_when_nothing_was_returned(self):
        needs = call("efris_taxpayer_status", {"ok": False, "error": "argument_not_provided", "arguments": ["tin"]})
        down = call("efris_taxpayer_status", {"ok": False, "error": "x", "retryable": True})

        assert DEMO_NOTICE not in compose(outcomes(needs), "")
        assert DEMO_NOTICE not in compose(outcomes(down), "")

    def test_an_unreachable_service_says_so_and_gives_the_contact_channels(self):
        down = call("efris_taxpayer_status", {"ok": False, "error": "efris tools are temporarily unavailable", "retryable": True})

        reply = compose(outcomes(down), "")

        assert "couldn't get an answer from EFRIS just now" in reply
        assert "won't guess" in reply
        assert "0800 117 000" in reply

    def test_a_mixed_turn_is_worded_entirely_from_the_services_answers(self):
        both = outcomes(EFRIS_FOUND, NO_BUSINESS)
        # Live, a model given this mix wrote that the business "is registered
        # with URSB" next to URSB's own "no registered entity".
        reply = compose(both, "The business Sample Traders Limited is registered with URSB.")

        assert "Sample Traders Limited is registered" not in reply
        assert reply.split("\n\n") == [
            "EFRIS returned this record:",
            "- Registered: yes\n- Business name: Nile Breweries Limited\n"
            "- VAT registered: yes\n- EFRIS status: ACTIVE",
            "URSB: No registered entity found matching query 'X'.",
            DEMO_NOTICE,
        ]

    def test_a_turn_that_also_used_other_tools_keeps_the_models_account_of_them(self):
        reply = compose(outcomes(NO_BUSINESS), "VAT on 1,000,000 is 180,000.", other_tools=True)

        first, second, *rest = reply.split("\n\n")
        assert first == "VAT on 1,000,000 is 180,000."
        assert second == "No registered entity found matching query 'X'."
        assert rest == [DEMO_NOTICE]

    def test_services_are_named_only_when_there_is_more_than_one(self):
        single = compose(outcomes(NO_BUSINESS), "")
        several = compose(outcomes(NO_BUSINESS, PRN_NOT_FOUND), "")

        assert single.startswith("No registered entity")
        assert several.startswith("URSB: No registered entity")
        assert "Payments: PRN" in several

    def test_a_record_the_model_wrote_nothing_about_is_still_shown(self):
        reply = compose(outcomes(EFRIS_FOUND), "")

        assert "Business name: Nile Breweries Limited" in reply
        assert "VAT registered: yes" in reply
        # The identifier is the one the taxpayer typed; it is not echoed back.
        assert "1000000002" not in reply

    def test_no_connector_call_means_no_opinion(self):
        assert compose([], "anything") == ""

    def test_the_same_complaint_is_not_repeated(self):
        reply = compose(outcomes(NO_BUSINESS, NO_BUSINESS), "")

        assert reply.count("No registered entity") == 1


class TestDeterministic:
    def test_no_lookup_returning_a_record_is_worded_by_the_services(self):
        assert deterministic(outcomes(STAMP_UNKNOWN, NO_BUSINESS))

    def test_one_lookup_without_a_record_is_enough(self):
        assert deterministic(outcomes(EFRIS_FOUND, NO_BUSINESS))

    def test_only_records_are_left_to_the_model_to_summarise(self):
        assert not deterministic(outcomes(EFRIS_FOUND))

    def test_no_connector_call_has_nothing_to_say(self):
        assert not deterministic([])

    def test_keys_read_as_a_person_would_write_them(self):
        facts = dict(outcomes(EFRIS_FOUND)[0].facts)

        assert {"Business name", "VAT registered", "EFRIS status"} <= set(facts)

    def test_the_placeholder_the_model_sees_is_valid_json_and_tells_it_to_stay_silent(self):
        import json

        assert "Do not mention it" in json.loads(REPORTED_SEPARATELY)["note"]

"""When does a chat turn count as "look this up in the service I chose"?

The contract is conservative on purpose. A missed lookup leaves the turn as it
was; a hijacked procedural question replaces a correct answer with a lookup
nobody asked for.
"""

from __future__ import annotations

import unittest

from app.connector_intent import (
    MAX_FORCED_OPERATIONS,
    detect_connector_intent,
    missing_arguments,
    ungrounded_arguments,
)


def _tool(namespace: str, name: str) -> tuple[str, dict]:
    return namespace, {"name": name, "description": f"{name} reads a sample record."}


ALL = [
    _tool("bwims", "bwims_consignment_status"),
    _tool("bwims", "bwims_warehouse_inventory"),
    _tool("digital_tax_stamps", "dts_taxpayer_status"),
    _tool("digital_tax_stamps", "dts_verify_stamp"),
    _tool("efris", "efris_taxpayer_status"),
    _tool("payment_system", "payment_verify_advance_tax"),
    _tool("payment_system", "payment_view_status"),
    _tool("tin_registration", "tin_search_verify"),
    _tool("ursb", "ursb_compliance_status"),
    _tool("ursb", "ursb_verify_business"),
]


def _ops(message: str, tools=ALL) -> tuple[str, ...]:
    intent = detect_connector_intent(message, tools)
    return intent.operations if intent else ()


class LookupRequestsAreDetectedTests(unittest.TestCase):
    def test_a_code_and_the_service_name_is_a_lookup(self) -> None:
        self.assertEqual(
            _ops("Look up Taxpayer Identification Number (TIN) 1000000001 in Electronic Fiscal "
                 "Receipting and Invoicing System (EFRIS) and tell me whether this taxpayer is registered."),
            ("efris_taxpayer_status",),
        )

    def test_each_service_resolves_to_its_own_operation(self) -> None:
        cases = {
            "Check the status of payment reference 220000000001": "payment_view_status",
            "Verify digital tax stamp code DTS-0001-SAMPLE": "dts_verify_stamp",
            'Is there a registered business called "Sample Traders Limited" at URSB?': "ursb_verify_business",
            "What is the status of bonded warehouse consignment entry IM7-0001?": "bwims_consignment_status",
            "Search the TIN registry for a taxpayer named John Okello": "tin_search_verify",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(_ops(message)[:1], (expected,))

    def test_a_request_with_no_code_still_counts_when_it_asks_for_a_record(self) -> None:
        # The missing TIN is dealt with by the argument guard, not by silence.
        self.assertEqual(_ops("Is my taxpayer registered on EFRIS?"), ("efris_taxpayer_status",))

    def test_two_services_in_one_question_yield_one_operation_each(self) -> None:
        self.assertEqual(
            set(_ops("Check TIN 1000000001 on EFRIS and the business Sample Traders Limited on URSB.")),
            {"efris_taxpayer_status", "ursb_verify_business"},
        )

    def test_at_most_a_few_operations_are_forced(self) -> None:
        wide = "Check EFRIS, URSB, BWIMS, payment and stamp records for TIN 1000000001"
        self.assertLessEqual(len(_ops(wide)), MAX_FORCED_OPERATIONS)


def _typed(namespace: str, name: str, *required: str, description: str = "") -> tuple[str, dict]:
    """An operation that declares what it requires, as the bundled services do."""
    return namespace, {
        "name": name,
        "description": description or f"{name} reads a sample record.",
        "inputSchema": {
            "type": "object",
            "properties": {argument: {"type": "string"} for argument in required},
            "required": list(required),
        },
    }


URSB = [
    _typed("ursb", "ursb_compliance_status", "registration_number"),
    _typed("ursb", "ursb_verify_business", "query"),
]
BWIMS = [
    _typed(
        "bwims",
        "bwims_consignment_status",
        "entry_number",
        description="Read sample consignment fixtures. It does not check a real customs entry.",
    ),
    _typed(
        "bwims",
        "bwims_warehouse_inventory",
        "warehouse_code",
        description="Query all bonded consignments and stock balances stored inside a licensed warehouse.",
    ),
]


class AnOperationIsOnlyForcedWhenItsInputsAreInTheMessageTests(unittest.TestCase):
    """Live, the model was made to call ``ursb_compliance_status`` for a company
    *name* and invented the registration number ``URSB-CO-10001``."""

    def test_a_name_goes_to_the_search_not_to_the_operation_that_needs_a_number(self) -> None:
        for message in (
            "Is the business Sample Traders Limited registered at URSB?",
            "Is TIN 1000000002 registered on EFRIS, and is Sample Traders Limited registered at URSB?",
        ):
            with self.subTest(message=message):
                self.assertEqual(_ops(message, URSB), ("ursb_verify_business",))

    def test_a_registration_reference_selects_the_operation_that_takes_one(self) -> None:
        ops = _ops("What is the compliance status of URSB-CO-10001 at URSB?", URSB)
        self.assertEqual(ops, ("ursb_compliance_status",))

    def test_a_bare_run_of_digits_is_a_tin_and_not_a_reference(self) -> None:
        self.assertEqual(_ops("Check URSB for 1000000001", URSB), ("ursb_verify_business",))

    def test_entry_numbers_and_warehouse_codes_are_told_apart(self) -> None:
        self.assertEqual(
            _ops("What is the status of bonded warehouse consignment entry IM7-0001?", BWIMS),
            ("bwims_consignment_status",),
        )
        self.assertEqual(
            _ops("How much stock is held in warehouse WH-KLA-01 under BWIMS?", BWIMS),
            ("bwims_warehouse_inventory",),
        )

    def test_when_nothing_is_satisfiable_the_best_match_stands_for_the_argument_guard_to_question(self) -> None:
        # No code in either message: the best match is still the call, and the
        # argument guard then asks the taxpayer for the code.
        self.assertEqual(
            _ops("Check the consignment status at BWIMS please", BWIMS), ("bwims_consignment_status",)
        )
        self.assertEqual(
            _ops("Look up the bonded warehouse consignment status in BWIMS", BWIMS), ("bwims_consignment_status",)
        )

    def test_operations_that_declare_nothing_are_unaffected(self) -> None:
        self.assertEqual(_ops("Verify business registration for Sample Traders Ltd at URSB"), ("ursb_verify_business",))


class ProceduralAndGeneralQuestionsAreLeftAloneTests(unittest.TestCase):
    def test_how_questions_are_not_lookups(self) -> None:
        for message in (
            "How do I register for a TIN?",
            "What are the requirements to register for EFRIS?",
            "Explain how URSB business registration works",
            "How can I pay my PAYE through the payment system?",
            # A code in a how-to does not turn it into a lookup.
            "How do I pay PRN 220000000001 in the payment system?",
            "Explain how EFRIS handles invoice 1000000001",
        ):
            with self.subTest(message=message):
                self.assertEqual(_ops(message), ())

    def test_a_word_every_tool_shares_does_not_select_one(self) -> None:
        # "status" and "tax" are in many tool names and many questions.
        for message in (
            "What is the status of my return?",
            "What is the VAT tax rate?",
            "Check the status of my application",
        ):
            with self.subTest(message=message):
                self.assertEqual(_ops(message), ())

    def test_a_service_that_was_not_chosen_is_never_called(self) -> None:
        only_efris = [_tool("efris", "efris_taxpayer_status")]
        self.assertEqual(_ops("Verify business registration for Sample Traders Ltd at URSB", only_efris), ())

    def test_empty_or_blank_messages(self) -> None:
        self.assertEqual(_ops(""), ())
        self.assertEqual(_ops("   "), ())
        self.assertIsNone(detect_connector_intent("hello", []))

    def test_a_procedural_question_that_carries_a_code_is_still_a_lookup(self) -> None:
        self.assertEqual(
            _ops("How do I check PRN 220000000001 in the payment system?")[:1], ("payment_view_status",)
        )


class ArgumentGroundingTests(unittest.TestCase):
    SCHEMA = {
        "type": "object",
        "properties": {
            "tin": {"type": "string"},
            "mode": {"type": "string", "enum": ["summary", "full"]},
            "limit": {"type": "integer"},
        },
    }

    def test_a_value_the_taxpayer_wrote_is_grounded(self) -> None:
        self.assertEqual(
            ungrounded_arguments({"tin": "1000000001"}, self.SCHEMA, "Is TIN 1000000001 registered?"), []
        )

    def test_retyping_is_tolerated(self) -> None:
        for written in ("tin 1000 000 001", "TIN: 1000-000-001", "tin 1000000001."):
            with self.subTest(written=written):
                self.assertEqual(ungrounded_arguments({"tin": "1000000001"}, self.SCHEMA, written), [])

    def test_an_invented_identifier_is_caught(self) -> None:
        self.assertEqual(
            ungrounded_arguments({"tin": "1234567890"}, self.SCHEMA, "Is my taxpayer registered on EFRIS?"), ["tin"]
        )

    def test_enums_numbers_and_empty_values_are_not_checked(self) -> None:
        self.assertEqual(
            ungrounded_arguments({"mode": "full", "limit": 5, "tin": ""}, self.SCHEMA, "anything"), []
        )

    def test_a_value_from_an_earlier_turn_counts_when_it_is_in_the_supplied_text(self) -> None:
        history = "My TIN is 1000000002. Is it registered?\nIs the same taxpayer on EFRIS?"
        self.assertEqual(ungrounded_arguments({"tin": "1000000002"}, self.SCHEMA, history), [])


if __name__ == "__main__":
    unittest.main()


class MissingArgumentsTests(unittest.TestCase):
    """A forced call that leaves a required argument out is asked about, not sent."""

    SCHEMA = {
        "properties": {"tin": {"type": "string"}, "period": {"type": "string", "enum": ["monthly", "annual"]}},
        "required": ["tin"],
    }

    def test_an_absent_required_argument_is_missing(self) -> None:
        self.assertEqual(missing_arguments({}, self.SCHEMA, "Is my taxpayer registered on EFRIS?"), ["tin"])

    def test_a_blank_required_argument_is_missing(self) -> None:
        for blank in ("", "   ", None):
            with self.subTest(blank=blank):
                self.assertEqual(missing_arguments({"tin": blank}, self.SCHEMA, "x"), ["tin"])

    def test_an_invented_value_is_missing_too_and_listed_once(self) -> None:
        self.assertEqual(missing_arguments({"tin": "1000000009"}, self.SCHEMA, "Is my TIN registered?"), ["tin"])

    def test_a_value_the_taxpayer_wrote_is_not_missing(self) -> None:
        self.assertEqual(missing_arguments({"tin": "1000000002"}, self.SCHEMA, "Is 1000000002 registered?"), [])

    def test_an_optional_argument_may_be_left_out(self) -> None:
        self.assertEqual(missing_arguments({"tin": "1000000002"}, self.SCHEMA, "Check 1000000002"), [])

    def test_no_schema_means_nothing_is_required(self) -> None:
        self.assertEqual(missing_arguments({}, None, "anything"), [])

    def test_a_reference_the_taxpayer_wrote_is_not_a_tin(self) -> None:
        # The PRN was in the conversation, so it is grounded — and still not a TIN.
        history = "Check the status of payment reference 220000000001.\nIs my taxpayer registered on EFRIS?"
        self.assertEqual(missing_arguments({"tin": "220000000001"}, self.SCHEMA, history), ["tin"])

    def test_a_tin_retyped_with_spaces_is_still_a_tin(self) -> None:
        self.assertEqual(missing_arguments({"tin": "1000 000 002"}, self.SCHEMA, "TIN 1000000002"), [])

    def test_a_prn_argument_takes_a_prn_and_not_a_tin(self) -> None:
        schema = {"properties": {"prn": {"type": "string"}}, "required": ["prn"]}
        self.assertEqual(missing_arguments({"prn": "1000000002"}, schema, "TIN 1000000002"), ["prn"])
        self.assertEqual(missing_arguments({"prn": "220000000001"}, schema, "PRN 220000000001"), [])

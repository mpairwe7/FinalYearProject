"""Per-specialist prompts — Phase 19.

The supervisor has routed to `tax_specialist` and `customs_specialist`
since Phase 15, and the model received identical instructions either
way. Routing changed the tool whitelist and the `agent_role` string in
the response, and nothing else — a "customs specialist" that has never
been told it is one is a label, not a specialist.

Two properties have to hold. The specialisation must actually differ per
role, and it must never displace the base prompt: a longer, more
specific persona must not be a way around the grounding and abstention
rules.
"""

from __future__ import annotations

import pytest

from app.agents.prompts import specialist_prompt, specialist_roles
from app.llm import SYSTEM_PROMPT, _build_tool_messages


def _system_for(role: str) -> str:
    return _build_tool_messages("q", None, None, "en", agent_role=role)[0]["content"]


class TestSpecialistSelection:
    def test_the_routed_specialists_all_have_instructions(self):
        # These are the roles service.py actually routes to.
        for role in ("tax_specialist", "customs_specialist", "tool_specialist"):
            assert specialist_prompt(role), f"{role} has no instructions"

    def test_non_specialist_roles_get_nothing_extra(self):
        # A greeting does not need a domain persona, and appending one
        # would spend tokens to make the reply worse.
        for role in ("greeting_agent", "clarification_agent", "rag_answerer", ""):
            assert specialist_prompt(role) == ""

    def test_an_unknown_role_is_not_an_error(self):
        assert specialist_prompt("some_future_role") == ""

    def test_case_and_padding_do_not_matter(self):
        assert specialist_prompt("  TAX_SPECIALIST  ") == specialist_prompt("tax_specialist")

    def test_roles_are_introspectable(self):
        assert set(specialist_roles()) == {
            "tax_specialist",
            "customs_specialist",
            "tool_specialist",
        }


class TestPromptComposition:
    def test_the_base_prompt_always_survives(self):
        # The safety rules live in SYSTEM_PROMPT. A specialist must not
        # be able to talk its way out of them by being more specific.
        for role in [*specialist_roles(), "greeting_agent", ""]:
            assert SYSTEM_PROMPT in _system_for(role)

    def test_the_specialist_block_comes_after_the_base(self):
        content = _system_for("customs_specialist")
        assert content.index(SYSTEM_PROMPT) < content.index("Your speciality")

    def test_each_specialist_gets_a_different_prompt(self):
        prompts = {role: _system_for(role) for role in specialist_roles()}
        assert len(set(prompts.values())) == len(prompts)

    def test_a_non_specialist_prompt_is_unchanged(self):
        assert _system_for("greeting_agent") == _system_for("")

    @pytest.mark.parametrize(
        ("role", "marker"),
        [
            ("customs_specialist", "CIF"),
            ("tax_specialist", "fiscal year"),
            ("tool_specialist", "tools"),
        ],
    )
    def test_the_domain_guidance_is_present(self, role, marker):
        assert marker.lower() in _system_for(role).lower()

    def test_customs_guidance_does_not_leak_into_the_tax_specialist(self):
        assert "CIF" not in _system_for("tax_specialist")

    def test_the_fragments_stay_short(self):
        # They cost tokens on every turn of the loop; a long persona
        # preamble crowds out the passages the answer depends on.
        for role in specialist_roles():
            assert len(specialist_prompt(role)) < 1200, f"{role} prompt is too long"


class TestPlumbing:
    def test_generate_with_tools_accepts_the_role(self):
        import inspect

        from app.llm import generate_with_tools

        assert "agent_role" in inspect.signature(generate_with_tools).parameters

    def test_the_service_passes_the_role_through(self):
        import inspect

        from app.service import _call_llm_agentic

        assert "agent_role" in inspect.signature(_call_llm_agentic).parameters

    def test_the_supervisor_roles_and_prompt_roles_agree(self):
        # Every role the supervisor routes to as a specialist should
        # have instructions; a mismatch means routing that does nothing.
        from app.agents.state import AgentRoute

        routed = {
            AgentRoute.TAX_SPECIALIST: "tax_specialist",
            AgentRoute.CUSTOMS_SPECIALIST: "customs_specialist",
            AgentRoute.TOOLS: "tool_specialist",
        }
        for role_name in routed.values():
            assert specialist_prompt(role_name), (
                f"supervisor routes to {role_name} but it has no instructions"
            )

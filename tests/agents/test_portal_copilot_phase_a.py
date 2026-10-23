"""Phase A unit tests for URA Portal Task Execution & Inbuilt DOM Co-Pilot (`portal_copilot.py`)."""

from __future__ import annotations

from pathlib import Path

import pytest
from app import database as db
from app.flags import _REGISTRY, flags
from app.portal_copilot import (
    PORTAL_TASK_CATALOG,
    PortalTaskOwnershipError,
    PortalTaskTier,
    PortalTaskValidationError,
    analyze_portal_context,
    build_portal_task_card,
    classify_portal_task,
    clear_portal_task_cache,
    detect_portal_task_intent,
    handle_portal_task_decision,
    mask_sensitive_value,
)
from app.tools.portal_navigator_data import (
    EXTERNAL_URA_PORTALS,
    export_companion_manifest,
    get_portal_dom_recipe,
    portal_requires_auth,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_portal_copilot_flag_registered_in_all_three_places() -> None:
    """`portal_copilot` must land in `flags.py`, `docs/RAG_ARCHITECTURE.md`, and `.env.example`."""
    assert "portal_copilot" in _REGISTRY
    spec = _REGISTRY["portal_copilot"]
    assert spec.default is True
    assert spec.effective_category == "kill_switch"
    assert spec.owner == "agentic_runtime"
    assert flags.is_enabled("portal_copilot") is True

    rag_arch = (REPO_ROOT / "docs" / "RAG_ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "`portal_copilot` (`FLAG_PORTAL_COPILOT`)" in rag_arch
    assert f"**{len(_REGISTRY)}** in total" in rag_arch

    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "FLAG_PORTAL_COPILOT=true" in env_example


def test_portal_requires_auth_and_external_portal_metadata() -> None:
    """Public vs authenticated portals must be deterministically classified."""
    for public_key in (
        "tin_registration",
        "prn_payments",
        "document_authentication",
        "track_status",
        "whistle_blow",
        "elearning",
        "laws_and_acts",
        "research_publications",
    ):
        assert portal_requires_auth(public_key) is False
        assert EXTERNAL_URA_PORTALS[public_key]["requires_auth"] is False

    for auth_key in (
        "e_services",
        "efris",
        "tax_clearance",
        "objection_appeals",
        "voluntary_disclosure",
        "get_refund",
        "tax_incentives",
        "choose_tax_agent",
        "asycuda",
        "bwims",
        "motor_vehicle",
        "dts",
    ):
        assert portal_requires_auth(auth_key) is True
        assert EXTERNAL_URA_PORTALS[auth_key]["requires_auth"] is True


@pytest.mark.parametrize(
    "portal_key",
    [
        "prn_payments",
        "efris",
        "tin_registration",
        "return_filing",
        "e_services",
        "tax_clearance",
        "objection_appeals",
        "motor_vehicle",
        "asycuda",
        "dts",
        "get_refund",
    ],
)
def test_get_portal_dom_recipe_covers_all_core_portals(portal_key: str) -> None:
    """Every core portal key must produce a deterministic DOM recipe ending with a confirmation gate."""
    recipe = get_portal_dom_recipe(
        portal_key,
        entities={
            "tin": "1000023456",
            "amount_ugx": "2500000",
            "tax_head": "VAT_STANDARD",
            "nin": "CM92018100123A",
            "quantity": "5000",
        },
    )
    assert len(recipe) >= 3
    for idx, step in enumerate(recipe, start=1):
        assert step["step_number"] == idx
        assert step["selector"]
        assert step["action_type"] in ("fill", "select", "click", "spotlight")
        assert step["security_gate"] in ("safe", "requires_confirmation")
    assert recipe[-1]["security_gate"] == "requires_confirmation"


def test_companion_manifest_prohibits_password_otp_pin_cvv_and_secret_selectors() -> None:
    manifest = export_companion_manifest()
    prohibited = manifest["security_policy"]["prohibited_selectors"]
    for token in ("password", "otp", "pin", "cvv", "secret", "cc-csc"):
        assert any(token in sel for sel in prohibited), f"Missing prohibited selector for {token}"


def test_four_tier_portal_task_catalog_completeness() -> None:
    """All 4 tiers in `PORTAL_TASK_CATALOG` must have tasks with valid specs."""
    tiers_present = {spec.tier for spec in PORTAL_TASK_CATALOG.values()}
    assert tiers_present == {
        PortalTaskTier.AUTONOMOUS_VERIFY,
        PortalTaskTier.DELEGATED_CONFIRM,
        PortalTaskTier.AUTHENTICATED_ACCOUNT_READ,
        PortalTaskTier.STATUTORY_COPILOT_ONLY,
    }

    assert classify_portal_task("tin_search_verify").tier == PortalTaskTier.AUTONOMOUS_VERIFY
    assert classify_portal_task("payment_generate_prn").tier == PortalTaskTier.DELEGATED_CONFIRM
    assert classify_portal_task("tin_tax_obligations").tier == PortalTaskTier.AUTHENTICATED_ACCOUNT_READ
    assert classify_portal_task("return_filing_submit").tier == PortalTaskTier.STATUTORY_COPILOT_ONLY


def test_analyze_portal_context_scrubs_secrets_and_spotlights_errors() -> None:
    """Passwords, OTPs, PINs, and CVVs in `visible_fields` must never survive `analyze_portal_context`."""
    raw_ctx = {
        "portal_id": "prn_payments",
        "current_route": "/payment/generate-prn",
        "active_step": "Step 2: Tax Head & Amount",
        "form_id": "URA-PAY-PRN-201",
        "visible_fields": {
            "tin": "1000023456",
            "password": "SuperSecretPassword123!",  # pragma: allowlist secret
            "user_otp_code": "998877",
            "momo_pin": "4321",
            "card_cvv": "999",
        },
        "empty_required_fields": ["amount_ugx", "user_password"],
        "validation_errors": [
            {
                "field": "tax_head",
                "message": "Please select a valid Tax Head code before generating a PRN.",
                "selector": "select#ddlTaxHead",
            }
        ],
        "error_banner": "Validation failed: mandatory fields missing.",
        "stalled_duration_ms": 18000,
    }

    result = analyze_portal_context(raw_ctx, user_id=None, conversation_id="conv-test-1")
    sanitized_fields = result["sanitized_context"]["visible_fields"]
    assert sanitized_fields == {"tin": "1000023456"}
    for forbidden in ("SuperSecretPassword123!", "998877", "4321", "999"):
        assert forbidden not in str(result)

    assert result["card_type"] == "stuck_portal_assistance"
    first_action = result["dom_actions"][0]
    assert first_action["action_type"] == "spotlight"
    assert first_action["scroll_to"] is True
    assert "ddlTaxHead" in first_action["selector"]


def test_policy_gates_inline_form_confirmation_step_up_and_execution() -> None:
    """Verify Gate 1 (Step-Up Login), Gate 2 (Inline Form), Gate 4 (Claude-style Confirmation), and Execution."""
    clear_portal_task_cache()
    # 1. Missing inputs -> Inline Form
    intent = detect_portal_task_intent("Generate a VAT PRN for me")
    assert intent is not None
    assert intent.task_id == "payment_generate_prn"
    card_form = build_portal_task_card(
        intent.task_id,
        extracted_fields=intent.extracted_fields,
        conversation_id="conv-a",
        session_id="sess-a",
    )
    assert card_form["card_type"] == "inline_form"
    assert "tin" in card_form["missing_fields"]
    assert "amount_ugx" in card_form["missing_fields"]

    # 2. All required inputs -> Claude-style Confirmation Card
    intent_full = detect_portal_task_intent(
        "Generate a VAT PRN for UGX 2,500,000 for TIN 1000023456 via Stanbic Bank"
    )
    assert intent_full is not None
    card_confirm = build_portal_task_card(
        intent_full.task_id,
        extracted_fields=intent_full.extracted_fields,
        conversation_id="conv-a",
        session_id="sess-a",
    )
    assert card_confirm["card_type"] == "confirmation_required"
    assert "verify" in card_confirm["verification_prompt"].lower()
    assert card_confirm["allow_label"] == "Allow & Execute"
    assert card_confirm["disallow_label"] == "Disallow"

    # 3. Execute confirmed task & verify idempotency and cross-session protection
    owner_key = db.conversation_state_key("conv-a", session_id="sess-a", user_id=None)
    wrong_owner = db.conversation_state_key("conv-a", session_id="sess-attacker", user_id=None)

    with pytest.raises(PortalTaskOwnershipError):
        handle_portal_task_decision(
            "allow",
            card_confirm["request_state"],
            card_confirm["idempotency_key"],
            owner_key=wrong_owner,
            conversation_id="conv-a",
        )

    exec_out = handle_portal_task_decision(
        "allow",
        card_confirm["request_state"],
        card_confirm["idempotency_key"],
        owner_key=owner_key,
        conversation_id="conv-a",
    )
    assert exec_out["ok"] is True
    assert exec_out["status"] == "executed"
    assert exec_out["live"] is False
    assert exec_out["mode"] == "simulation"
    assert str(exec_out["result"].get("prn", "")).startswith("2")

    # Tampered state must fail validation
    tampered = dict(card_confirm["request_state"])
    tampered["payload"] = dict(tampered["payload"])
    tampered["payload"]["idempotency_key"] = "ura_task_tampered"
    with pytest.raises(PortalTaskValidationError):
        handle_portal_task_decision(
            "allow",
            tampered,
            card_confirm["idempotency_key"],
            owner_key=owner_key,
            conversation_id="conv-a",
        )

    # 4. Masking helper
    assert mask_sensitive_value("nin", "CM92018100123A") == "CM92*******23A"

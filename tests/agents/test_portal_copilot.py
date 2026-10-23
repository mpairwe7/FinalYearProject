"""Comprehensive backend integration, security & privacy test suite for Phase 42 (`portal_copilot.py`).

Exercises all 8 verification gates from `docs/plans/ura-portal-task-execution-and-inbuilt-copilot-plan.md`:
1. Tier 1 Autonomous Read & Inline Form Elicitation (`/v1/chat` + `portal_task_submission`)
2. Tier 2 Claude-Style Allow / Disallow Confirmation Card
3. `POST /v1/portal/tasks/execute` Allow vs. Disallow & Idempotency
4. Security: Tampered `request_state` (400) & Cross-Session Replay Refusal (403, `ADR-0013`)
5. Tier 3 Official Secrecy (TPCA Cap. 343 s. 75) & Step-Up Login with `returnTo`
6. Tier 4 Statutory Declaration Boundary (TPCA s. 16 `statutory_copilot_handoff`)
7. Screenshot-to-Co-Pilot Portal Handoff, Auth-Gated Deep Links & Completeness Guard
8. Live DOM Telemetry (`portal_context`) Diagnosis & Password/OTP/PIN/CVV Scrubbing
"""

from __future__ import annotations

from typing import Any

import pytest
from app.auth.jwt_auth import make_dev_token
from app.documents import diagnose_portal_screenshot
from app.flags import flags
from app.main import app
from app.service import ChatModel
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MCP_REQUEST_STATE_SECRET", "test-secret-for-portal-copilot-hmac-2026-ura")
    flags.set("portal_copilot", True)
    prev_model = getattr(app.state, "model", None)
    prev_overrides = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    app.state.model = ChatModel()
    yield TestClient(app)
    app.state.model = prev_model
    app.dependency_overrides.clear()
    app.dependency_overrides.update(prev_overrides)
    flags.clear("portal_copilot")


def _auth_headers(sub: str = "1000023456", session_id: str = "sess-taxpayer-1") -> dict[str, str]:
    token = make_dev_token(
        user_id=sub,
        role="verified_taxpayer",
        tenant_id="default",
        granted_purposes=["ura_account_access", "ura_actions"],
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Session-ID": session_id,
    }


def test_1_tier1_autonomous_verify_and_inline_form_elicitation(client: TestClient) -> None:
    """Asking 'Verify TIN' without a TIN returns `inline_form`; submitting '1000023456' executes `tin_search_verify`."""
    headers = {"X-Session-ID": "sess-tier1"}
    r1 = client.post(
        "/v1/chat",
        json={"message": "Verify TIN", "conversation_id": "conv-tier1"},
        headers=headers,
    )
    assert r1.status_code == 200
    body1 = r1.json()
    copilot1 = body1["portal_copilot"]
    assert copilot1 is not None
    assert copilot1["card_type"] == "inline_form"
    assert copilot1["task_id"] == "tin_search_verify"
    assert "tin" in copilot1["missing_fields"]
    assert any(f["name"] == "tin" for f in copilot1["fields"])

    # Submit the inline form with TIN 1000023456
    r2 = client.post(
        "/v1/chat",
        json={
            "message": "Submitted TIN verification form",
            "conversation_id": "conv-tier1",
            "portal_task_submission": {
                "task_id": "tin_search_verify",
                "fields": {"tin": "1000023456"},
            },
        },
        headers=headers,
    )
    assert r2.status_code == 200
    copilot2 = r2.json()["portal_copilot"]
    assert copilot2 is not None
    assert copilot2["card_type"] == "executed_result"
    assert copilot2["task_id"] == "tin_search_verify"
    assert copilot2["live"] is False
    assert copilot2["mode"] == "simulation"
    assert copilot2["result"]["ok"] is True


def test_2_tier2_claude_style_confirmation_card(client: TestClient) -> None:
    """Asking to generate a VAT PRN with TIN, amount, and bank returns `confirmation_required` before executing."""
    headers = {"X-Session-ID": "sess-tier2"}
    r = client.post(
        "/v1/chat",
        json={
            "message": "Generate a VAT PRN for UGX 2,500,000 for TIN 1000023456 via Stanbic Bank",
            "conversation_id": "conv-tier2",
        },
        headers=headers,
    )
    assert r.status_code == 200
    data = r.json()
    copilot = data["portal_copilot"]
    assert copilot is not None
    assert copilot["card_type"] == "confirmation_required"
    assert copilot["task_id"] == "payment_generate_prn"
    assert "verify" in copilot["verification_prompt"].lower()
    assert copilot["allow_label"] == "Allow & Execute"
    assert copilot["disallow_label"] == "Disallow"
    assert copilot["edit_label"] == "Edit Details"
    assert copilot["request_state"]["alg"] == "hmac-sha256"
    assert copilot["request_state"]["sig"]
    assert "verify" in data["reply"].lower()


def test_3_execute_endpoint_allow_disallow_and_idempotency(client: TestClient) -> None:
    """`POST /v1/portal/tasks/execute` handles disallow with decline-lock and allow with idempotency."""
    headers = {"X-Session-ID": "sess-exec-3"}
    chat_res_decline = client.post(
        "/v1/chat",
        json={
            "message": "Generate a VAT PRN for UGX 1,800,000 for TIN 1000023456 via Stanbic Bank",
            "conversation_id": "conv-exec-decline",
        },
        headers=headers,
    )
    copilot_dec = chat_res_decline.json()["portal_copilot"]
    req_state_dec = copilot_dec["request_state"]
    idem_key_dec = copilot_dec["idempotency_key"]

    # 1. Disallow cancels without generating a PRN and locks that idempotency_key against replay
    r_disallow = client.post(
        "/v1/portal/tasks/execute",
        json={
            "decision": "disallow",
            "request_state": req_state_dec,
            "idempotency_key": idem_key_dec,
            "conversation_id": "conv-exec-decline",
        },
        headers=headers,
    )
    assert r_disallow.status_code == 200
    d_body = r_disallow.json()
    assert d_body["ok"] is True
    assert d_body["status"] == "declined"
    assert "prn" not in d_body["result"]

    # Attempting to replay "allow" on a declined proposal must be rejected (400)
    r_replay_after_decline = client.post(
        "/v1/portal/tasks/execute",
        json={
            "decision": "allow",
            "request_state": req_state_dec,
            "idempotency_key": idem_key_dec,
            "conversation_id": "conv-exec-decline",
        },
        headers=headers,
    )
    assert r_replay_after_decline.status_code == 400

    # 2. Fresh proposal -> Allow executes `payment_generate_prn` once and returns simulated PRN
    chat_res = client.post(
        "/v1/chat",
        json={
            "message": "Generate a VAT PRN for UGX 2,500,000 for TIN 1000023456 via Stanbic Bank",
            "conversation_id": "conv-exec-3",
        },
        headers=headers,
    )
    copilot = chat_res.json()["portal_copilot"]
    req_state = copilot["request_state"]
    idem_key = copilot["idempotency_key"]

    r_allow = client.post(
        "/v1/portal/tasks/execute",
        json={
            "decision": "allow",
            "request_state": req_state,
            "idempotency_key": idem_key,
            "conversation_id": "conv-exec-3",
        },
        headers=headers,
    )
    assert r_allow.status_code == 200
    a_body = r_allow.json()
    assert a_body["ok"] is True
    assert a_body["status"] == "executed"
    assert a_body["live"] is False
    assert a_body["mode"] == "simulation"
    first_prn = a_body["result"]["prn"]
    assert first_prn.startswith(("226", "2"))

    # 3. Repeating the same `idempotency_key` returns the exact same PRN without duplicate creation
    r_replay = client.post(
        "/v1/portal/tasks/execute",
        json={
            "decision": "allow",
            "request_state": req_state,
            "idempotency_key": idem_key,
            "conversation_id": "conv-exec-3",
        },
        headers=headers,
    )
    assert r_replay.status_code == 200
    assert r_replay.json()["result"]["prn"] == first_prn


def test_4_security_tampered_request_state_and_cross_session_replay_refused(client: TestClient) -> None:
    """Tampering with `request_state` returns 400; replaying from another session/user returns 403 (ADR-0013)."""
    headers_alice = {"X-Session-ID": "sess-alice-4"}
    chat_res = client.post(
        "/v1/chat",
        json={
            "message": "Generate a VAT PRN for UGX 2,500,000 for TIN 1000023456 via Stanbic Bank",
            "conversation_id": "conv-sec-4",
        },
        headers=headers_alice,
    )
    copilot = chat_res.json()["portal_copilot"]
    req_state = copilot["request_state"]
    idem_key = copilot["idempotency_key"]

    # 1. Tamper with `amount_ugx` inside `request_state.payload.params` -> 400 Bad Request
    tampered_state: dict[str, Any] = {
        "alg": req_state["alg"],
        "sig": req_state["sig"],
        "payload": {
            **req_state["payload"],
            "params": {**req_state["payload"]["params"], "amount_ugx": "99999999"},
        },
    }
    r_tampered = client.post(
        "/v1/portal/tasks/execute",
        json={
            "decision": "allow",
            "request_state": tampered_state,
            "idempotency_key": idem_key,
            "conversation_id": "conv-sec-4",
        },
        headers=headers_alice,
    )
    assert r_tampered.status_code == 400

    # 2. Cross-session replay from a different `X-Session-ID` -> 403 Forbidden
    headers_mallory = {"X-Session-ID": "sess-mallory-attacker"}
    r_cross_sess = client.post(
        "/v1/portal/tasks/execute",
        json={
            "decision": "allow",
            "request_state": req_state,
            "idempotency_key": idem_key,
            "conversation_id": "conv-sec-4",
        },
        headers=headers_mallory,
    )
    assert r_cross_sess.status_code == 403

    # 3. Same-session post-login continuation (Alice signs in on `sess-alice-4`) -> 200 OK
    authed_alice = _auth_headers(sub="1000023456", session_id="sess-alice-4")
    r_same_sess_authed = client.post(
        "/v1/portal/tasks/execute",
        json={
            "decision": "allow",
            "request_state": req_state,
            "idempotency_key": idem_key,
            "conversation_id": "conv-sec-4",
        },
        headers=authed_alice,
    )
    assert r_same_sess_authed.status_code == 200
    assert r_same_sess_authed.json()["status"] == "executed"


def test_5_tier3_official_secrecy_tpca_s75_and_step_up_login(client: TestClient) -> None:
    """Unauthenticated Tier 3 request emits `step_up_login`; authenticated cross-TIN query is refused under TPCA s. 75."""
    # 1. Anonymous user asks for tax obligations -> `step_up_login` with `returnTo`
    r_anon = client.post(
        "/v1/chat",
        json={
            "message": "Check my tax obligations for TIN 1000023456",
            "conversation_id": "conv-tier3",
        },
        headers={"X-Session-ID": "sess-tier3"},
    )
    assert r_anon.status_code == 200
    c_anon = r_anon.json()["portal_copilot"]
    assert c_anon["card_type"] == "step_up_login"
    assert c_anon["task_id"] == "tin_tax_obligations"
    assert "/signin?continue=signin&returnTo=" in c_anon["signin_chat_href"]
    assert "auth_resumed%3D1" in c_anon["signin_chat_href"]
    assert c_anon["preserved_fields"]["tin"] == "1000023456"

    # 2. Authenticated user with TIN 1000023456 attempting to query a DIFFERENT TIN 1000099999 -> refused (TPCA s. 75)
    r_cross_tin = client.post(
        "/v1/chat",
        json={
            "message": "Check my tax obligations for TIN 1000099999",
            "conversation_id": "conv-tier3",
        },
        headers=_auth_headers(sub="1000023456", session_id="sess-tier3"),
    )
    assert r_cross_tin.status_code == 200
    c_denied = r_cross_tin.json()["portal_copilot"]
    assert c_denied["card_type"] == "access_denied"
    assert "Section 75" in c_denied["error"]
    assert "1000099999" in c_denied["error"]

    # 3. Authenticated user querying their OWN TIN 1000023456 -> proceeds to `confirmation_required`
    r_own_tin = client.post(
        "/v1/chat",
        json={
            "message": "Check my tax obligations for TIN 1000023456",
            "conversation_id": "conv-tier3",
        },
        headers=_auth_headers(sub="1000023456", session_id="sess-tier3"),
    )
    assert r_own_tin.status_code == 200
    c_own = r_own_tin.json()["portal_copilot"]
    assert c_own["card_type"] == "confirmation_required"
    assert c_own["task_id"] == "tin_tax_obligations"


def test_6_tier4_statutory_boundary_tpca_s16(client: TestClient) -> None:
    """Asking the AI to submit a final VAT return or lodge a formal Section 24 objection stages `statutory_copilot_handoff`."""
    headers = _auth_headers(sub="1000023456", session_id="sess-tier4")

    r_ret = client.post(
        "/v1/chat",
        json={
            "message": "Submit my VAT return for me for TIN 1000023456 with amount UGX 1,500,000",
            "conversation_id": "conv-tier4",
        },
        headers=headers,
    )
    assert r_ret.status_code == 200
    c_ret = r_ret.json()["portal_copilot"]
    assert c_ret["card_type"] == "statutory_copilot_handoff"
    assert c_ret["task_id"] == "return_filing_submit"
    assert "/portal/return_filing" in c_ret["copilot_portal_url"]
    assert "autofill=1" in c_ret["copilot_portal_url"]
    assert c_ret["dom_actions"][-1]["security_gate"] == "requires_confirmation"

    r_obj = client.post(
        "/v1/chat",
        json={
            "message": "Lodge my formal objection under Section 24",
            "conversation_id": "conv-tier4",
        },
        headers=headers,
    )
    assert r_obj.status_code == 200
    c_obj = r_obj.json()["portal_copilot"]
    assert c_obj["card_type"] == "statutory_copilot_handoff"
    assert c_obj["task_id"] == "objection_appeals_lodge"
    assert "/portal/objection_appeals" in c_obj["copilot_portal_url"]


def test_7_screenshot_to_copilot_handoff_and_completeness_guard() -> None:
    """Verify public vs auth-gated screenshot handoff links and cropped screenshot completeness checks."""
    # 1. Authenticated portal screenshot (Tax Clearance / Return Filing)
    auth_shot = diagnose_portal_screenshot(
        text="Uganda Revenue Authority e-Services Tax Clearance Certificate (TCC) Application TIN: 1000023456 Unfiled Return Error",
        filename="tcc_portal_screenshot.png",
        fields={"tins": ["1000023456"]},
        meta={},
        doc_type="portal_screenshot",
        kind="image",
    )
    assert auth_shot["is_screenshot"] is True
    assert auth_shot["portal_key"] == "tax_clearance"
    assert auth_shot["requires_auth"] is True
    assert auth_shot["completeness_check"]["is_complete"] is True
    handoff_auth = auth_shot["copilot_handoff"]
    assert handoff_auth["requires_login_first"] is True
    assert handoff_auth["inbuilt_portal_url"].startswith("/portal/tax_clearance")
    assert handoff_auth["signin_then_portal_url"].startswith("/signin?continue=signin&returnTo=")
    assert "signing in first" in handoff_auth["user_message"]

    # 2. Public portal screenshot (PRN Payment)
    pub_shot = diagnose_portal_screenshot(
        text="Uganda Revenue Authority e-Services Payment Registration PRN Generation Amount: UGX 2,500,000 Select Payment Mode",
        filename="prn_screen.png",
        fields={"tins": ["1000023456"], "amounts": ["UGX 2,500,000"]},
        meta={},
        doc_type="portal_screenshot",
        kind="image",
    )
    assert pub_shot["portal_key"] == "prn_payments"
    assert pub_shot["requires_auth"] is False
    handoff_pub = pub_shot["copilot_handoff"]
    assert handoff_pub["requires_login_first"] is False
    assert handoff_pub["prefill_state"]["tin"] == "1000023456"
    assert handoff_pub["prefill_state"]["amount_ugx"] == "2500000"

    # 3. Cropped / incomplete screenshot missing header signal
    cropped_shot = diagnose_portal_screenshot(
        text="mandatory field error...",
        filename="cropped_error_snip.png",
        fields={},
        meta={},
        doc_type="portal_screenshot",
        kind="image",
    )
    comp = cropped_shot["completeness_check"]
    assert comp["is_complete"] is False
    assert len(comp["missing_context_hints"]) >= 1
    assert len(comp["clarifying_options"]) >= 3
    assert cropped_shot["copilot_handoff"]["inbuilt_portal_url"].startswith("/portal/")


def test_8_live_dom_telemetry_portal_context_diagnosis_and_secret_scrubbing(client: TestClient) -> None:
    """Passing `portal_context` to `/v1/chat` scrubs `password` and `otp` and spotlights the failing field with `scroll_to=True`."""
    r = client.post(
        "/v1/chat",
        json={
            "message": "I am stuck on this portal screen, help me fix the error",
            "conversation_id": "conv-dom-8",
            "portal_context": {
                "portal_id": "prn_payments",
                "current_route": "/portal/prn_payments",
                "active_step": "Payment Registration Form",
                "form_id": "URA-PAY-PRN-201",
                "visible_fields": {
                    "tin": "1000023456",
                    "password": "secret123",  # pragma: allowlist secret
                    "otp": "999111",
                },
                "empty_required_fields": ["amount_ugx"],
                "validation_errors": [
                    {
                        "field": "tax_head",
                        "message": "Tax Head selection is mandatory",
                        "selector": "select#ddlTaxHead",
                    }
                ],
                "error_banner": "Please resolve the highlighted validation errors.",
                "stalled_duration_ms": 20000,
            },
        },
        headers={"X-Session-ID": "sess-dom-8"},
    )
    assert r.status_code == 200
    body = r.json()
    copilot = body["portal_copilot"]
    assert copilot is not None
    assert copilot["card_type"] == "stuck_portal_assistance"
    assert "secret123" not in str(body)
    assert "999111" not in str(body)
    assert copilot["sanitized_context"]["visible_fields"] == {"tin": "1000023456"}
    assert copilot["dom_actions"][0]["action_type"] == "spotlight"
    assert copilot["dom_actions"][0]["scroll_to"] is True
    assert "ddlTaxHead" in copilot["dom_actions"][0]["selector"]


def test_9_qa_hardening_temporal_ttl_intent_capsule_validators_and_multiturn_memory(
    client: TestClient,
) -> None:
    """Verify OWASP ASI01/ASI07 temporal TTL anti-replay, strict NIRA NIN/phone validators, and multi-turn memory."""
    import time

    from app import database as db
    from app.mcp import mrtr
    from app.portal_copilot import (
        PortalTaskValidationError,
        classify_portal_task,
        evaluate_turn,
        handle_portal_task_decision,
        validate_task_fields,
    )

    # 1. Expired `request_state` (issued 30 minutes ago > 15m TTL) must be rejected
    owner_key = db.conversation_state_key("conv-ttl-9", session_id="sess-ttl-9", user_id=None)
    expired_payload = {
        "task_id": "payment_generate_prn",
        "portal_key": "prn_payments",
        "tier": "DELEGATED_CONFIRM",
        "params": {
            "tin": "1000023456",
            "tax_head": "VAT_STANDARD",
            "amount_ugx": "2500000",
            "payment_mode": "COMMERCIAL_BANK",
        },
        "owner_key": owner_key,
        "user_id": "",
        "issued_at": int(time.time()) - 1800,
        "idempotency_key": "ura_task_expired_999",
    }
    signed_expired = mrtr.wrap_request_state(expired_payload)
    with pytest.raises(PortalTaskValidationError, match="expired"):
        handle_portal_task_decision(
            "allow",
            signed_expired,
            "ura_task_expired_999",
            owner_key=owner_key,
            conversation_id="conv-ttl-9",
        )

    # 2. Strict NIRA NIN (CM/CF prefix) and Uganda mobile number validation
    ind_spec = classify_portal_task("tin_apply_individual")
    assert ind_spec is not None
    _, _, errs = validate_task_fields(
        ind_spec,
        {
            "nin": "XX92018100123A",
            "surname": "Mukasa",
            "given_name": "Sarah",
            "date_of_birth": "1992-05-14",
            "mobile_number": "12345",
            "email": "not-an-email",
        },
    )
    assert "nin" in errs
    assert "mobile_number" in errs
    assert "email" in errs

    # 3. Multi-turn context remembering across conversation_history
    outcome = evaluate_turn(
        message="Generate a VAT PRN for me now via Stanbic Bank",
        conversation_history=[
            {
                "user_message": "My company TIN is 1000023456 and my net VAT payable is UGX 4,200,000.",
                "bot_reply": "You will need to generate a PRN to settle UGX 4,200,000.",
            }
        ],
        session_id="sess-mem-9",
        conversation_id="conv-mem-9",
    )
    assert outcome is not None
    assert outcome.card_type == "confirmation_required"
    params = outcome.payload["request_state"]["payload"]["params"]
    assert params["tin"] == "1000023456"
    assert params["amount_ugx"] == "4200000"
    # Verify no emojis in deterministic reply
    for emoji_char in ("⚠️", "🔒", "⛔", "📝", "✅", "🛡️", "🧭"):
        assert emoji_char not in outcome.deterministic_reply
